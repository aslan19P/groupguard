from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import cast

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from groupguard.domain import normalize_username
from groupguard.models import (
    AllowlistEntry,
    AuditEvent,
    ManagedChat,
    MessageFingerprint,
    ModerationCase,
    Owner,
    PendingOwnerInvite,
    Sanction,
    UsernameAlias,
    UserProfile,
)


async def upsert_user(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    display_name: str,
) -> UserProfile:
    normalized = normalize_username(username) if username else None
    now = datetime.now(UTC)
    profile_statement = (
        insert(UserProfile)
        .values(
            user_id=user_id,
            current_username=normalized,
            display_name=display_name,
        )
        .on_conflict_do_update(
            index_elements=[UserProfile.user_id],
            set_={
                "current_username": normalized,
                "display_name": display_name,
                "updated_at": now,
            },
        )
        .returning(UserProfile)
    )
    profile = (
        await session.scalars(
            profile_statement,
            execution_options={"populate_existing": True},
        )
    ).one()

    if normalized:
        alias_statement = (
            insert(UsernameAlias)
            .values(user_id=user_id, username=normalized, last_seen_at=now)
            .on_conflict_do_update(
                index_elements=[UsernameAlias.user_id, UsernameAlias.username],
                set_={"last_seen_at": now},
            )
        )
        await session.execute(alias_statement)
    return profile


async def find_user(session: AsyncSession, query: str) -> UserProfile | None:
    clean = query.strip()
    if clean.isdigit():
        return await session.get(UserProfile, int(clean))
    username = normalize_username(clean)
    return cast(
        UserProfile | None,
        await session.scalar(
            select(UserProfile)
            .outerjoin(UsernameAlias, UsernameAlias.user_id == UserProfile.user_id)
            .where(
                or_(
                    UserProfile.current_username == username,
                    UsernameAlias.username == username,
                )
            )
            .limit(1)
        ),
    )


async def is_owner(session: AsyncSession, user_id: int) -> bool:
    return await session.get(Owner, user_id) is not None


async def is_immune(session: AsyncSession, user_id: int) -> bool:
    return (
        await session.get(Owner, user_id) is not None
        or await session.get(AllowlistEntry, user_id) is not None
    )


async def list_owners(session: AsyncSession, notifications_only: bool = False) -> list[Owner]:
    statement = select(Owner).order_by(Owner.created_at)
    if notifications_only:
        statement = statement.where(Owner.notifications_enabled.is_(True))
    return list((await session.scalars(statement)).all())


async def owner_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(Owner)) or 0)


async def get_managed_chat(session: AsyncSession) -> ManagedChat | None:
    return cast(ManagedChat | None, await session.scalar(select(ManagedChat).limit(1)))


async def get_recent_fingerprints(
    session: AsyncSession,
    chat_id: int,
    *,
    exclude_message_id: int,
    local_date: date,
    since: datetime,
) -> list[MessageFingerprint]:
    return list(
        (
            await session.scalars(
                select(MessageFingerprint).where(
                    MessageFingerprint.chat_id == chat_id,
                    MessageFingerprint.message_id != exclude_message_id,
                    MessageFingerprint.local_date == local_date,
                    MessageFingerprint.source_created_at >= since,
                )
            )
        ).all()
    )


async def upsert_fingerprint(
    session: AsyncSession, fingerprint: MessageFingerprint
) -> MessageFingerprint:
    existing = await session.scalar(
        select(MessageFingerprint).where(
            MessageFingerprint.chat_id == fingerprint.chat_id,
            MessageFingerprint.message_id == fingerprint.message_id,
        )
    )
    if existing is None:
        session.add(fingerprint)
        return fingerprint
    for attribute in (
        "user_id",
        "local_date",
        "normalized_text",
        "text_hash",
        "phone_hashes",
        "phone_masks",
        "excerpt",
        "media_file_id",
        "media_file_unique_id",
        "media_phash",
        "source_created_at",
        "expires_at",
    ):
        setattr(existing, attribute, getattr(fingerprint, attribute))
    return existing


async def create_case_if_absent(
    session: AsyncSession, case: ModerationCase
) -> tuple[ModerationCase, bool]:
    existing = await session.scalar(
        select(ModerationCase).where(
            ModerationCase.chat_id == case.chat_id,
            ModerationCase.source_message_id == case.source_message_id,
        )
    )
    if existing is not None:
        return existing, False
    session.add(case)
    await session.flush()
    return case, True


async def add_audit(
    session: AsyncSession,
    action: str,
    *,
    actor_user_id: int | None = None,
    target_user_id: int | None = None,
    case_id: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            action=action,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            case_id=case_id,
            details=details or {},
        )
    )


async def mark_violation(
    session: AsyncSession,
    user_id: int,
    *,
    deleted: bool = False,
    muted: bool = False,
) -> None:
    values: dict[str, object] = {
        "violation_count": UserProfile.violation_count + 1,
        "last_violation_at": datetime.now(UTC),
    }
    if deleted:
        values["deletion_count"] = UserProfile.deletion_count + 1
    if muted:
        values["mute_count"] = UserProfile.mute_count + 1
    await session.execute(
        update(UserProfile).where(UserProfile.user_id == user_id).values(**values)
    )


async def cleanup_expired(session: AsyncSession) -> dict[str, int]:
    now = datetime.now(UTC)
    fingerprints = await session.execute(
        delete(MessageFingerprint).where(MessageFingerprint.expires_at < now)
    )
    expired_cases = await session.execute(
        update(ModerationCase)
        .where(
            ModerationCase.status == "open",
            ModerationCase.created_at < now - timedelta(days=14),
        )
        .values(status="expired", excerpt=None, phone_masks=[], media_file_id=None)
    )
    scrubbed_cases = await session.execute(
        update(ModerationCase)
        .where(
            ModerationCase.status != "open",
            ModerationCase.created_at < now - timedelta(days=30),
            or_(ModerationCase.excerpt.is_not(None), ModerationCase.media_file_id.is_not(None)),
        )
        .values(excerpt=None, phone_masks=[], media_file_id=None)
    )
    audits = await session.execute(
        delete(AuditEvent).where(AuditEvent.created_at < now - timedelta(days=30))
    )
    sanctions = await session.execute(
        update(Sanction)
        .where(Sanction.active.is_(True), Sanction.until_at <= now)
        .values(active=False)
    )
    old_sanctions = await session.execute(
        delete(Sanction).where(
            Sanction.active.is_(False),
            Sanction.updated_at < now - timedelta(days=30),
        )
    )
    invites = await session.execute(
        delete(PendingOwnerInvite).where(
            or_(
                PendingOwnerInvite.expires_at < now - timedelta(days=1),
                PendingOwnerInvite.consumed_at < now - timedelta(days=30),
            )
        )
    )
    return {
        "fingerprints": fingerprints.rowcount or 0,
        "expired_cases": expired_cases.rowcount or 0,
        "scrubbed_cases": scrubbed_cases.rowcount or 0,
        "audits": audits.rowcount or 0,
        "sanctions": sanctions.rowcount or 0,
        "old_sanctions": old_sanctions.rowcount or 0,
        "invites": invites.rowcount or 0,
    }


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
