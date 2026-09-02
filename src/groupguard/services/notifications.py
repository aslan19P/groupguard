from __future__ import annotations

import html
import logging
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from groupguard.keyboards import case_keyboard
from groupguard.models import CaseDelivery, ModerationCase, UserProfile
from groupguard.repositories import list_owners

logger = logging.getLogger(__name__)


REASON_LABELS = {
    "similar_text": "похожий текст с тем же номером",
    "phone_other_user": "тот же номер у другого аккаунта",
    "repeated_media": "повтор той же фотографии",
    "similar_media": "визуально похожая фотография",
    "edited_suspicious": "подозрительное редактирование",
    "exact_duplicate": "точный повтор объявления",
}

RESOLUTION_LABELS = {
    "kept": "✅ оставлено",
    "deleted": "🗑 удалено",
    "muted": "🔇 удалено, мут на 7 дней",
    "allowlisted": "🛡 добавлено в белый список",
    "auto_muted": "🤖 автоматически удалено, мут на 7 дней",
    "auto_partial": "⚠️ автоматическая санкция выполнена частично",
    "mute_partial": "⚠️ мут назначен, сообщение удалить не удалось",
    "expired": "⌛ срок проверки истёк",
}


def render_case(case: ModerationCase, profile: UserProfile | None) -> str:
    username = f"@{profile.current_username}" if profile and profile.current_username else "нет"
    display_name = profile.display_name if profile else "неизвестно"
    stats = (
        f"Нарушений: {profile.violation_count}, мутов: {profile.mute_count}"
        if profile
        else "Статистика недоступна"
    )
    deletion_remaining = case.delete_available_until - datetime.now(UTC)
    if deletion_remaining.total_seconds() > 0:
        hours = max(1, int(deletion_remaining.total_seconds() // 3600))
        deletion = f"Удаление доступно ещё примерно {hours} ч."
    else:
        deletion = "Удаление сообщения уже недоступно."
    status = (
        "Ожидает решения"
        if case.status == "open"
        else RESOLUTION_LABELS.get(case.resolution or case.status, case.resolution or case.status)
    )
    phones = ", ".join(case.phone_masks) if case.phone_masks else "не указаны"
    excerpt = html.escape(case.excerpt or "нет сохранённого текста")
    link = (
        f'<a href="{html.escape(case.message_link)}">Открыть оригинал</a>'
        if case.message_link
        else ""
    )
    return (
        f"<b>⚠️ Случай модерации</b>\n\n"
        f"Автор: {html.escape(display_name)} ({html.escape(username)})\n"
        f"ID: <code>{case.target_user_id}</code>\n"
        f"Причина: {html.escape(REASON_LABELS.get(case.reason, case.reason))}\n"
        f"Телефоны: {html.escape(phones)}\n"
        f"{html.escape(stats)}\n"
        f"{html.escape(deletion)}\n"
        f"Статус: {html.escape(status)}\n\n"
        f"<blockquote>{excerpt}</blockquote>\n"
        f"{link}"
    )


class NotificationService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def deliver_case(
        self,
        session: AsyncSession,
        case: ModerationCase,
        *,
        force_all: bool = False,
        only_owner_id: int | None = None,
    ) -> None:
        profile = await session.get(UserProfile, case.target_user_id)
        owners = await list_owners(session, notifications_only=not force_all)
        if only_owner_id is not None:
            owners = [
                owner for owner in await list_owners(session) if owner.user_id == only_owner_id
            ]
        text = render_case(case, profile)
        for owner in owners:
            existing = await session.scalar(
                select(CaseDelivery).where(
                    CaseDelivery.case_id == case.id,
                    CaseDelivery.owner_user_id == owner.user_id,
                )
            )
            if existing is not None:
                continue
            try:
                if case.media_file_id:
                    sent = await self.bot.send_photo(
                        owner.private_chat_id,
                        photo=case.media_file_id,
                        caption=text,
                        reply_markup=case_keyboard(case),
                    )
                    delivery_type = "photo"
                else:
                    sent = await self.bot.send_message(
                        owner.private_chat_id,
                        text,
                        reply_markup=case_keyboard(case),
                        disable_web_page_preview=True,
                    )
                    delivery_type = "text"
                session.add(
                    CaseDelivery(
                        case_id=case.id,
                        owner_user_id=owner.user_id,
                        private_chat_id=owner.private_chat_id,
                        message_id=sent.message_id,
                        delivery_type=delivery_type,
                    )
                )
            except (TelegramBadRequest, TelegramForbiddenError) as error:
                logger.warning(
                    "Failed to deliver moderation case owner_id=%s: %s", owner.user_id, error
                )

    async def sync_case(self, session: AsyncSession, case: ModerationCase) -> None:
        profile = await session.get(UserProfile, case.target_user_id)
        text = render_case(case, profile)
        deliveries = list(
            (
                await session.scalars(select(CaseDelivery).where(CaseDelivery.case_id == case.id))
            ).all()
        )
        for delivery in deliveries:
            try:
                markup: InlineKeyboardMarkup | None = case_keyboard(case)
                if delivery.delivery_type == "photo":
                    await self.bot.edit_message_caption(
                        chat_id=delivery.private_chat_id,
                        message_id=delivery.message_id,
                        caption=text,
                        reply_markup=markup,
                    )
                else:
                    await self.bot.edit_message_text(
                        text,
                        chat_id=delivery.private_chat_id,
                        message_id=delivery.message_id,
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )
            except (TelegramBadRequest, TelegramForbiddenError) as error:
                logger.info("Could not update delivered case case_id=%s: %s", case.id, error)

    async def critical(self, session: AsyncSession, text: str) -> None:
        for owner in await list_owners(session):
            try:
                await self.bot.send_message(owner.private_chat_id, f"<b>⚠️ GroupGuard</b>\n{text}")
            except (TelegramBadRequest, TelegramForbiddenError) as error:
                logger.warning("Critical notification failed owner_id=%s: %s", owner.user_id, error)
