from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatPermissions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from groupguard.models import AllowlistEntry, ModerationCase, Sanction
from groupguard.repositories import add_audit, get_managed_chat, mark_violation
from groupguard.services.notifications import NotificationService


class CaseActionError(RuntimeError):
    pass


class CaseActionService:
    def __init__(self, bot: Bot, notifier: NotificationService) -> None:
        self.bot = bot
        self.notifier = notifier

    async def act(
        self,
        session: AsyncSession,
        case_id: str,
        actor_user_id: int,
        action: str,
    ) -> ModerationCase:
        case = await session.scalar(
            select(ModerationCase).where(ModerationCase.id == case_id).with_for_update()
        )
        if case is None:
            raise CaseActionError("Случай не найден.")
        if case.status != "open":
            raise CaseActionError("Этот случай уже обработан другим владельцем.")

        now = datetime.now(UTC)
        if action == "keep":
            resolution = "kept"
        elif action == "delete":
            if case.delete_available_until <= now:
                raise CaseActionError("Прошло более 48 часов: Telegram уже не разрешает удаление.")
            await self._delete(case)
            await mark_violation(session, case.target_user_id, deleted=True)
            resolution = "deleted"
        elif action == "mute":
            if case.delete_available_until <= now:
                raise CaseActionError("Прошло более 48 часов: удалить сообщение уже нельзя.")
            resolution = await self._mute_and_delete(session, case, actor_user_id)
        elif action == "allow":
            entry = await session.get(AllowlistEntry, case.target_user_id)
            if entry is None:
                session.add(
                    AllowlistEntry(
                        user_id=case.target_user_id,
                        added_by_user_id=actor_user_id,
                        reason=f"case:{case.id}",
                    )
                )
            resolution = "allowlisted"
        else:
            raise CaseActionError("Неизвестное действие.")

        case.status = "resolved"
        case.resolved_at = now
        case.resolved_by_user_id = actor_user_id
        case.resolution = resolution
        await add_audit(
            session,
            f"case_{resolution}",
            actor_user_id=actor_user_id,
            target_user_id=case.target_user_id,
            case_id=case.id,
        )
        await session.commit()
        await self.notifier.sync_case(session, case)
        return case

    async def _delete(self, case: ModerationCase) -> None:
        try:
            await self.bot.delete_message(case.chat_id, case.source_message_id)
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            raise CaseActionError(f"Telegram не дал удалить сообщение: {error.message}") from error

    async def _mute_and_delete(
        self,
        session: AsyncSession,
        case: ModerationCase,
        actor_user_id: int,
    ) -> str:
        until_at = datetime.now(UTC) + timedelta(days=7)
        try:
            await self.bot.restrict_chat_member(
                case.chat_id,
                case.target_user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_at,
                use_independent_chat_permissions=True,
            )
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            raise CaseActionError(
                f"Telegram не дал ограничить пользователя: {error.message}"
            ) from error

        session.add(
            Sanction(
                chat_id=case.chat_id,
                user_id=case.target_user_id,
                until_at=until_at,
                case_id=case.id,
                created_by_user_id=actor_user_id,
            )
        )
        deleted = True
        try:
            await self.bot.delete_message(case.chat_id, case.source_message_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            deleted = False
            await self.notifier.critical(
                session,
                "Пользователь заглушён, но сообщение удалить не удалось. Случай: "
                f"<code>{case.id}</code>.",
            )
        await mark_violation(session, case.target_user_id, deleted=deleted, muted=True)
        return "muted" if deleted else "mute_partial"

    async def unmute_user(
        self,
        session: AsyncSession,
        user_id: int,
        actor_user_id: int,
        *,
        case_id: str | None = None,
    ) -> None:
        managed = await get_managed_chat(session)
        if managed is None:
            raise CaseActionError("Группа ещё не подключена.")
        chat = await self.bot.get_chat(managed.chat_id)
        permissions = chat.permissions or ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_invite_users=True,
        )
        try:
            await self.bot.restrict_chat_member(
                managed.chat_id,
                user_id,
                permissions=permissions,
                use_independent_chat_permissions=True,
            )
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            raise CaseActionError(f"Telegram не дал снять ограничение: {error.message}") from error
        sanctions = list(
            (
                await session.scalars(
                    select(Sanction).where(
                        Sanction.chat_id == managed.chat_id,
                        Sanction.user_id == user_id,
                        Sanction.active.is_(True),
                    )
                )
            ).all()
        )
        for sanction in sanctions:
            sanction.active = False
        await add_audit(
            session,
            "user_unmuted",
            actor_user_id=actor_user_id,
            target_user_id=user_id,
            case_id=case_id,
        )
        await session.commit()
