from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from aiogram import Router
from aiogram.types import CallbackQuery, ErrorEvent, Message, Update
from sqlalchemy.ext.asyncio import AsyncSession

from groupguard.error_reporting import error_stage, summarize_exception
from groupguard.services.notifications import NotificationService

router = Router(name="errors")
logger = logging.getLogger(__name__)

_UPDATE_LABELS = {
    "message": "новое сообщение",
    "edited_message": "редактирование сообщения",
    "callback_query": "нажатие кнопки",
    "my_chat_member": "изменение доступа бота",
    "chat_member": "изменение участника группы",
    "chat_join_request": "заявка на вступление",
}


@dataclass(frozen=True, slots=True)
class UpdateContext:
    kind: str
    chat_id: int | None = None
    message_id: int | None = None
    user_id: int | None = None


def update_context(update: Update) -> UpdateContext:
    try:
        kind = update.event_type
        event = update.event
    except LookupError:
        return UpdateContext(kind="unknown")
    if isinstance(event, Message):
        return UpdateContext(
            kind=kind,
            chat_id=event.chat.id,
            message_id=event.message_id,
            user_id=event.from_user.id if event.from_user else None,
        )
    if isinstance(event, CallbackQuery):
        message = event.message
        return UpdateContext(
            kind=kind,
            chat_id=message.chat.id if message else None,
            message_id=message.message_id if message else None,
            user_id=event.from_user.id,
        )
    return UpdateContext(kind=kind)


def render_error_notification(incident_id: str, event: ErrorEvent) -> str:
    summary = summarize_exception(event.exception)
    context = update_context(event.update)
    event_label = _UPDATE_LABELS.get(context.kind, "другое обновление")
    locations = [f"Событие: {event_label} (<code>{context.kind}</code>)"]
    stage = error_stage(event.exception)
    if stage is not None:
        locations.append(f"Этап: {stage}")
    if context.chat_id is not None:
        locations.append(f"Чат: <code>{context.chat_id}</code>")
    if context.message_id is not None:
        locations.append(f"Сообщение: <code>{context.message_id}</code>")
    if context.user_id is not None:
        locations.append(f"Пользователь: <code>{context.user_id}</code>")
    location_text = " · ".join(locations)
    return (
        f"<b>⚠️ {summary.title}</b>\n"
        f"{summary.detail}\n"
        f"{location_text}\n"
        f"Код инцидента: <code>{incident_id}</code>. "
        "Это обновление могло быть пропущено; бот продолжает работу."
    )


@router.error()
async def unexpected_error(
    event: ErrorEvent,
    session: AsyncSession,
    notifier: NotificationService,
) -> bool:
    incident_id = secrets.token_hex(4)
    logger.error(
        "Unhandled update error incident_id=%s error_type=%s",
        incident_id,
        type(event.exception).__name__,
        exc_info=event.exception,
    )
    await session.rollback()
    try:
        await notifier.critical(
            session,
            render_error_notification(incident_id, event),
        )
        await session.commit()
    except Exception:
        logger.error(
            "Critical notification failed incident_id=%s",
            incident_id,
            exc_info=True,
        )
        await session.rollback()
    return True
