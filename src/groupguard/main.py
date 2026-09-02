from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from groupguard.config import get_settings
from groupguard.db import DatabaseSessionMiddleware, create_engine, create_session_factory
from groupguard.handlers import errors, group, private
from groupguard.services.cases import CaseActionService
from groupguard.services.moderation import ModerationService
from groupguard.services.notifications import NotificationService
from groupguard.services.retention import retention_loop


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger(__name__)

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    bot = Bot(
        settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notifier = NotificationService(bot)
    moderation = ModerationService(bot, settings, notifier)
    actions = CaseActionService(bot, notifier)

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(DatabaseSessionMiddleware(session_factory))
    errors.router.include_routers(private.router, group.router)
    dispatcher.include_router(errors.router)

    cleanup_task = asyncio.create_task(
        retention_loop(session_factory, settings.retention_interval_seconds),
        name="retention-cleanup",
    )
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Открыть панель"),
                BotCommand(command="menu", description="Показать меню"),
            ],
            scope=BotCommandScopeAllPrivateChats(),
        )
        logger.info("GroupGuard polling started")
        await dispatcher.start_polling(
            bot,
            settings=settings,
            notifier=notifier,
            moderation=moderation,
            actions=actions,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        await dispatcher.storage.close()
        await bot.session.close()
        await engine.dispose()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
