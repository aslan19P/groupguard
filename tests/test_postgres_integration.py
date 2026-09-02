from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage
from aiogram.types import Chat, Message, User
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from groupguard.config import Settings
from groupguard.models import (
    AllowlistEntry,
    AuditEvent,
    ManagedChat,
    MessageFingerprint,
    ModerationCase,
    Owner,
    Sanction,
    UserProfile,
)
from groupguard.repositories import cleanup_expired, is_immune
from groupguard.services.cases import CaseActionError, CaseActionService
from groupguard.services.moderation import ModerationService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
async def session_factory() -> async_sessionmaker[AsyncSession]:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        table_names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        assert "alembic_version" in table_names
        await connection.execute(
            text(
                "TRUNCATE audit_events, sanctions, case_deliveries, moderation_cases, "
                "message_fingerprints, allowlist_entries, pending_owner_invites, owners, "
                "username_aliases, user_profiles, managed_chats RESTART IDENTITY CASCADE"
            )
        )
        await connection.execute(
            text("UPDATE app_state SET bootstrap_consumed_at = NULL WHERE id = 1")
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_owner_and_allowlist_are_immune(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                UserProfile(user_id=1, display_name="Owner"),
                UserProfile(user_id=2, display_name="Allowed"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Owner(user_id=1, private_chat_id=1),
                AllowlistEntry(user_id=2, added_by_user_id=1),
            ]
        )
        await session.commit()
        assert await is_immune(session, 1)
        assert await is_immune(session, 2)
        assert not await is_immune(session, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("immune_kind", ["owner", "allowlist"])
async def test_moderation_never_records_or_sanctions_immune_user(
    session_factory: async_sessionmaker[AsyncSession],
    immune_kind: str,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(UserProfile(user_id=8, display_name="Protected"))
        await session.flush()
        session.add(
            Owner(user_id=8, private_chat_id=8)
            if immune_kind == "owner"
            else AllowlistEntry(user_id=8, added_by_user_id=1)
        )
        session.add(
            ManagedChat(
                chat_id=-1001,
                title="Test",
                connected_by_user_id=1,
            )
        )
        await session.commit()
        message = Message(
            message_id=1,
            date=now,
            chat=Chat(id=-1001, type=ChatType.SUPERGROUP, title="Test"),
            from_user=User(id=8, is_bot=False, first_name="Protected"),
            text="Такси +998 90 123 45 67",
        )
        bot = AsyncMock()
        settings = Settings(
            bot_token="123456789:test-token",
            bootstrap_token="bootstrap-secret-value",
            database_url="postgresql+psycopg://unused",
            phone_hmac_secret="phone-hmac-secret-value",
        )
        service = ModerationService(bot, settings, FakeNotifier())  # type: ignore[arg-type]

        await service.process(session, message)
        await session.commit()

        assert await session.scalar(select(MessageFingerprint)) is None
        assert await session.scalar(select(ModerationCase)) is None
        bot.restrict_chat_member.assert_not_awaited()
        bot.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_retention_boundaries(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(UserProfile(user_id=5, display_name="Driver", violation_count=7))
        session.add_all(
            [
                MessageFingerprint(
                    chat_id=-1001,
                    message_id=1,
                    user_id=5,
                    local_date=date.today(),
                    normalized_text="old",
                    text_hash="a" * 64,
                    source_created_at=now - timedelta(days=3),
                    expires_at=now - timedelta(seconds=1),
                ),
                MessageFingerprint(
                    chat_id=-1001,
                    message_id=2,
                    user_id=5,
                    local_date=date.today(),
                    normalized_text="new",
                    text_hash="b" * 64,
                    source_created_at=now,
                    expires_at=now + timedelta(hours=48),
                ),
                ModerationCase(
                    chat_id=-1001,
                    source_message_id=3,
                    target_user_id=5,
                    reason="similar_text",
                    excerpt="temporary text",
                    phone_masks=["998 ***** 4567"],
                    delete_available_until=now - timedelta(days=14),
                    created_at=now - timedelta(days=15),
                ),
                AuditEvent(
                    action="old_event",
                    target_user_id=5,
                    created_at=now - timedelta(days=31),
                ),
                Sanction(
                    chat_id=-1001,
                    user_id=5,
                    until_at=now - timedelta(seconds=1),
                ),
            ]
        )
        await session.commit()
        counts = await cleanup_expired(session)
        await session.commit()

        assert counts["fingerprints"] == 1
        assert counts["expired_cases"] == 1
        assert counts["audits"] == 1
        assert counts["sanctions"] == 1
        assert len(list((await session.scalars(select(MessageFingerprint))).all())) == 1
        case = await session.scalar(select(ModerationCase))
        assert case is not None
        assert case.status == "expired"
        assert case.excerpt is None
        profile = await session.get(UserProfile, 5)
        assert profile is not None and profile.violation_count == 7


class FakeNotifier:
    def __init__(self) -> None:
        self.sync_count = 0
        self.delivery_count = 0
        self.critical_count = 0

    async def sync_case(self, session: AsyncSession, case: ModerationCase) -> None:
        self.sync_count += 1

    async def critical(self, session: AsyncSession, text: str) -> None:
        self.critical_count += 1

    async def deliver_case(
        self,
        session: AsyncSession,
        case: ModerationCase,
        **kwargs: object,
    ) -> None:
        self.delivery_count += 1


@pytest.mark.asyncio
async def test_concurrent_case_decision_first_commit_wins(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    case = ModerationCase(
        chat_id=-1001,
        source_message_id=10,
        target_user_id=5,
        reason="similar_text",
        delete_available_until=now + timedelta(hours=1),
    )
    async with session_factory() as session:
        session.add(UserProfile(user_id=5, display_name="Driver"))
        session.add(case)
        await session.commit()
        case_id = case.id

    notifier = FakeNotifier()
    service = CaseActionService(Any, notifier)  # type: ignore[arg-type]

    async def decide(actor_id: int) -> str:
        async with session_factory() as session:
            try:
                await service.act(session, case_id, actor_id, "keep")
                return "won"
            except CaseActionError:
                return "lost"

    results = await asyncio.gather(decide(1), decide(2))
    assert sorted(results) == ["lost", "won"]
    assert notifier.sync_count == 1


@pytest.mark.asyncio
async def test_auto_sanction_success_is_persisted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    case = ModerationCase(
        chat_id=-1001,
        source_message_id=20,
        target_user_id=5,
        reason="exact_duplicate",
        delete_available_until=now + timedelta(hours=48),
    )
    async with session_factory() as session:
        session.add(UserProfile(user_id=5, display_name="Driver"))
        session.add(case)
        await session.commit()
        bot = AsyncMock()
        notifier = FakeNotifier()
        service = ModerationService(bot, Any, notifier)  # type: ignore[arg-type]

        await service._apply_auto_sanction(session, case)

        assert case.resolution == "auto_muted"
        assert bot.restrict_chat_member.await_count == 1
        assert bot.delete_message.await_count == 1
        sanction = await session.scalar(select(Sanction).where(Sanction.case_id == case.id))
        assert sanction is not None and sanction.active
        profile = await session.get(UserProfile, 5)
        assert profile is not None
        await session.refresh(profile)
        assert (profile.violation_count, profile.deletion_count, profile.mute_count) == (1, 1, 1)
        assert notifier.delivery_count == 1
        assert notifier.critical_count == 0


@pytest.mark.asyncio
async def test_partial_auto_sanction_notifies_all_owners(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    case = ModerationCase(
        chat_id=-1001,
        source_message_id=21,
        target_user_id=6,
        reason="exact_duplicate",
        delete_available_until=now + timedelta(hours=48),
    )
    async with session_factory() as session:
        session.add(UserProfile(user_id=6, display_name="Driver"))
        session.add(case)
        await session.commit()
        bot = AsyncMock()
        bot.delete_message.side_effect = TelegramBadRequest(
            method=DeleteMessage(chat_id=-1001, message_id=21),
            message="not enough rights",
        )
        notifier = FakeNotifier()
        service = ModerationService(bot, Any, notifier)  # type: ignore[arg-type]

        await service._apply_auto_sanction(session, case)

        assert case.resolution == "auto_partial"
        assert notifier.delivery_count == 1
        assert notifier.critical_count == 1
        profile = await session.get(UserProfile, 6)
        assert profile is not None
        await session.refresh(profile)
        assert (profile.deletion_count, profile.mute_count) == (0, 1)


@pytest.mark.asyncio
async def test_manual_delete_rejects_expired_telegram_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = ModerationCase(
        chat_id=-1001,
        source_message_id=22,
        target_user_id=7,
        reason="similar_text",
        delete_available_until=datetime.now(UTC) - timedelta(seconds=1),
    )
    async with session_factory() as session:
        session.add(UserProfile(user_id=7, display_name="Driver"))
        session.add(case)
        await session.commit()
        bot = AsyncMock()
        service = CaseActionService(bot, FakeNotifier())  # type: ignore[arg-type]

        with pytest.raises(CaseActionError, match="48 часов"):
            await service.act(session, case.id, 1, "delete")
        bot.delete_message.assert_not_awaited()
