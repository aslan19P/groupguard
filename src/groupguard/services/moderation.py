from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import weakref
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import imagehash
from aiogram import Bot
from aiogram.enums import MessageEntityType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatPermissions, Message, MessageEntity
from PIL import Image, UnidentifiedImageError
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from groupguard.config import Settings
from groupguard.domain import (
    build_fingerprint,
    hashes_overlap,
    local_calendar_date,
    message_link,
    safe_excerpt,
    text_similarity,
)
from groupguard.error_reporting import summarize_exception
from groupguard.models import MessageFingerprint, ModerationCase, Sanction
from groupguard.repositories import (
    add_audit,
    create_case_if_absent,
    get_managed_chat,
    get_recent_fingerprints,
    is_immune,
    mark_violation,
    upsert_fingerprint,
    upsert_user,
)
from groupguard.services.notifications import NotificationService

logger = logging.getLogger(__name__)


def phash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _calculate_phash(data: bytes) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            return str(imagehash.phash(image.convert("RGB")))
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def entity_phone_values(text: str, entities: list[MessageEntity] | None) -> list[str]:
    values: list[str] = []
    for entity in entities or []:
        if entity.type == MessageEntityType.PHONE_NUMBER:
            values.append(entity.extract_from(text))
        elif entity.type == MessageEntityType.TEXT_LINK and entity.url:
            url = entity.url.strip()
            if url.casefold().startswith("tel:"):
                values.append(url[4:])
    return values


def advisory_lock_key(chat_id: int, user_id: int) -> int:
    raw = f"{chat_id}:{user_id}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)


@dataclass(frozen=True, slots=True)
class MatchResult:
    exact: bool = False
    suspicion_reason: str | None = None


def classify_candidate(
    *,
    user_id: int,
    local_date: date,
    text_hash: str,
    normalized_text: str,
    phone_hashes: tuple[str, ...],
    media_file_unique_id: str | None,
    media_phash: str | None,
    candidate: MessageFingerprint,
    similarity_threshold: int,
) -> MatchResult:
    if candidate.local_date != local_date:
        return MatchResult()
    phone_overlap = hashes_overlap(phone_hashes, candidate.phone_hashes)
    if (
        phone_hashes
        and candidate.user_id == user_id
        and candidate.local_date == local_date
        and candidate.text_hash == text_hash
        and phone_overlap
    ):
        return MatchResult(exact=True)
    if phone_hashes and phone_overlap:
        if candidate.user_id != user_id:
            return MatchResult(suspicion_reason="phone_other_user")
        if text_similarity(normalized_text, candidate.normalized_text) >= similarity_threshold:
            return MatchResult(suspicion_reason="similar_text")
    if media_file_unique_id and candidate.media_file_unique_id == media_file_unique_id:
        return MatchResult(suspicion_reason="repeated_media")
    if (
        media_phash
        and candidate.media_phash
        and phash_distance(media_phash, candidate.media_phash) <= 8
    ):
        return MatchResult(suspicion_reason="similar_media")
    return MatchResult()


class ModerationService:
    def __init__(self, bot: Bot, settings: Settings, notifier: NotificationService) -> None:
        self.bot = bot
        self.settings = settings
        self.notifier = notifier
        self._locks_guard = asyncio.Lock()
        self._user_locks: weakref.WeakValueDictionary[tuple[int, int], asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    async def _user_lock(self, chat_id: int, user_id: int) -> asyncio.Lock:
        key = (chat_id, user_id)
        async with self._locks_guard:
            lock = self._user_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._user_locks[key] = lock
            return lock

    async def _photo_phash(self, message: Message) -> str | None:
        if not message.photo:
            return None
        destination = io.BytesIO()
        try:
            await self.bot.download(message.photo[-1], destination=destination)
        except (TelegramBadRequest, TelegramForbiddenError, OSError) as error:
            logger.info("Photo hash download failed message_id=%s: %s", message.message_id, error)
            return None
        return await asyncio.to_thread(_calculate_phash, destination.getvalue())

    async def process(
        self, session: AsyncSession, message: Message, *, edited: bool = False
    ) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        lock = await self._user_lock(message.chat.id, message.from_user.id)
        async with lock:
            await self._process_serialized(session, message, edited=edited)

    async def _process_serialized(
        self, session: AsyncSession, message: Message, *, edited: bool
    ) -> None:
        if message.from_user is None:
            return
        managed = await get_managed_chat(session)
        if managed is None or message.chat.id != managed.chat_id:
            return

        user = message.from_user
        await session.execute(
            sql_text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": advisory_lock_key(managed.chat_id, user.id)},
        )
        await upsert_user(session, user.id, user.username, user.full_name)
        if await is_immune(session, user.id):
            return

        text = message.text or message.caption or ""
        photo = message.photo[-1] if message.photo else None
        generic_media = photo or message.document or message.video or message.animation
        contact_phone = message.contact.phone_number if message.contact else None
        if not text and generic_media is None and contact_phone is None:
            return

        entities = message.entities if message.text else message.caption_entities
        additional_phones = entity_phone_values(text, entities)
        if contact_phone:
            additional_phones.append(contact_phone)
        fingerprint = build_fingerprint(
            text,
            self.settings.phone_hmac_secret.get_secret_value(),
            additional_phones,
        )
        media_phash = await self._photo_phash(message) if photo else None
        source_time = message.date.astimezone(UTC)
        local_date = local_calendar_date(source_time, self.settings.zoneinfo)
        now = datetime.now(UTC)
        recent = await get_recent_fingerprints(
            session,
            managed.chat_id,
            exclude_message_id=message.message_id,
            local_date=local_date,
            since=now - timedelta(days=7),
        )

        exact: MessageFingerprint | None = None
        suspicion_reason: str | None = None
        for candidate in recent:
            match = classify_candidate(
                user_id=user.id,
                local_date=local_date,
                text_hash=fingerprint.text_hash,
                normalized_text=fingerprint.normalized_text,
                phone_hashes=fingerprint.phone_hashes,
                media_file_unique_id=generic_media.file_unique_id if generic_media else None,
                media_phash=media_phash,
                candidate=candidate,
                similarity_threshold=self.settings.similarity_threshold,
            )
            if match.exact:
                exact = candidate
                break
            suspicion_reason = suspicion_reason or match.suspicion_reason

        stored = MessageFingerprint(
            chat_id=managed.chat_id,
            message_id=message.message_id,
            user_id=user.id,
            local_date=local_date,
            normalized_text=fingerprint.normalized_text,
            text_hash=fingerprint.text_hash,
            phone_hashes=list(fingerprint.phone_hashes),
            phone_masks=list(fingerprint.phone_masks),
            excerpt=safe_excerpt(text) if text else None,
            media_file_id=photo.file_id if photo else None,
            media_file_unique_id=generic_media.file_unique_id if generic_media else None,
            media_phash=media_phash,
            source_created_at=source_time,
            expires_at=now + timedelta(hours=48),
        )
        await upsert_fingerprint(session, stored)

        reason = "exact_duplicate" if exact is not None else suspicion_reason
        if edited and reason is not None and reason != "exact_duplicate":
            reason = "edited_suspicious"
        if reason is None:
            return

        case = ModerationCase(
            chat_id=managed.chat_id,
            source_message_id=message.message_id,
            target_user_id=user.id,
            reason=reason,
            excerpt=safe_excerpt(text) if text else "Медиа или контакт без подписи",
            phone_masks=list(fingerprint.phone_masks),
            media_file_id=photo.file_id if photo else None,
            message_link=message_link(managed.username, managed.chat_id, message.message_id),
            delete_available_until=source_time + timedelta(hours=48),
        )
        case, created = await create_case_if_absent(session, case)
        if not created:
            return
        await session.commit()

        if exact is not None:
            await self._apply_auto_sanction(session, case)
        else:
            await self.notifier.deliver_case(session, case)
            await add_audit(
                session,
                "case_created",
                target_user_id=user.id,
                case_id=case.id,
                details={"reason": reason},
            )
            await session.commit()

    async def _apply_auto_sanction(self, session: AsyncSession, case: ModerationCase) -> None:
        until_at = datetime.now(UTC) + timedelta(days=7)
        muted = False
        deleted = False
        errors: list[str] = []
        try:
            await self.bot.restrict_chat_member(
                case.chat_id,
                case.target_user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_at,
                use_independent_chat_permissions=True,
            )
            muted = True
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            summary = summarize_exception(error)
            errors.append(f"ограничение пользователя — {summary.detail}")
        try:
            await self.bot.delete_message(case.chat_id, case.source_message_id)
            deleted = True
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            summary = summarize_exception(error)
            errors.append(f"удаление сообщения — {summary.detail}")

        case.status = "resolved"
        case.resolved_at = datetime.now(UTC)
        case.resolution = "auto_muted" if muted and deleted else "auto_partial"
        if muted:
            session.add(
                Sanction(
                    chat_id=case.chat_id,
                    user_id=case.target_user_id,
                    until_at=until_at,
                    case_id=case.id,
                )
            )
        await mark_violation(
            session,
            case.target_user_id,
            deleted=deleted,
            muted=muted,
        )
        await add_audit(
            session,
            "auto_duplicate_sanction",
            target_user_id=case.target_user_id,
            case_id=case.id,
            details={"muted": muted, "deleted": deleted, "errors": errors},
        )
        await session.commit()
        await self.notifier.deliver_case(session, case)
        if errors:
            details = "; ".join(errors)
            await self.notifier.critical(
                session,
                "Автоматическая санкция выполнена частично. Откройте карточку случая "
                f"<code>{case.id}</code>. Не выполнено: {details}.",
            )
        await session.commit()
