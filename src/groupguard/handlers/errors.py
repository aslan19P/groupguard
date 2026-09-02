from __future__ import annotations

import logging
import secrets

from aiogram import Router
from aiogram.types import ErrorEvent
from sqlalchemy.ext.asyncio import AsyncSession

from groupguard.services.notifications import NotificationService

router = Router(name="errors")
logger = logging.getLogger(__name__)


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
            "Неожиданная ошибка обработки. Код инцидента: "
            f"<code>{incident_id}</code>. Проверьте журналы контейнера.",
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
