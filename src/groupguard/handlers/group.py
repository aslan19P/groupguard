from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from groupguard.services.moderation import ModerationService

router = Router(name="group")


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def observe_message(
    message: Message,
    session: AsyncSession,
    moderation: ModerationService,
) -> None:
    await moderation.process(session, message)


@router.edited_message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def observe_edited_message(
    message: Message,
    session: AsyncSession,
    moderation: ModerationService,
) -> None:
    await moderation.process(session, message, edited=True)
