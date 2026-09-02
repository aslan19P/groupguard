from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from groupguard.repositories import cleanup_expired

logger = logging.getLogger(__name__)


async def retention_loop(
    session_factory: async_sessionmaker[AsyncSession],
    interval_seconds: int,
) -> None:
    """Periodically delete transient moderation data while preserving aggregates."""
    while True:
        try:
            async with session_factory() as session:
                counts = await cleanup_expired(session)
                await session.commit()
            if any(counts.values()):
                logger.info("Retention cleanup completed counts=%s", counts)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Retention cleanup failed")
        await asyncio.sleep(interval_seconds)
