from __future__ import annotations

import asyncio
from typing import Any

import pytest

from groupguard.services.moderation import ModerationService


@pytest.mark.asyncio
async def test_messages_from_same_user_are_processed_sequentially() -> None:
    service = ModerationService(Any, Any, Any)  # type: ignore[arg-type]
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def first() -> None:
        lock = await service._user_lock(-1001, 42)
        async with lock:
            order.append("first")
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        await first_entered.wait()
        lock = await service._user_lock(-1001, 42)
        async with lock:
            order.append("second")

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_entered.wait()
    await asyncio.sleep(0)

    assert order == ["first"]
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert order == ["first", "second"]
