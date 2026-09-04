from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User

from groupguard.handlers.private import edit_panel
from groupguard.keyboards import dashboard_keyboard


def callback() -> CallbackQuery:
    return CallbackQuery(
        id="callback-id",
        from_user=User(id=1, is_bot=False, first_name="Owner"),
        chat_instance="chat-instance",
        message=Message(
            message_id=2,
            date=datetime.now(UTC),
            chat=Chat(id=1, type=ChatType.PRIVATE),
            text="Old panel",
        ),
    )


def bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(
        method=EditMessageText(text="Panel", chat_id=1, message_id=2),
        message=message,
    )


@pytest.mark.asyncio
async def test_invalid_markup_is_not_retried_as_a_new_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edit = AsyncMock(side_effect=bad_request("BUTTON_USER_PRIVACY_RESTRICTED"))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit)
    monkeypatch.setattr(Message, "answer", answer)

    with pytest.raises(TelegramBadRequest, match="BUTTON_USER_PRIVACY_RESTRICTED"):
        await edit_panel(callback(), "Panel", dashboard_keyboard())

    edit.assert_awaited_once()
    answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_uneditable_old_panel_is_sent_as_new_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edit = AsyncMock(side_effect=bad_request("message can't be edited"))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit)
    monkeypatch.setattr(Message, "answer", answer)
    markup: InlineKeyboardMarkup = dashboard_keyboard()

    await edit_panel(callback(), "Panel", markup)

    answer.assert_awaited_once_with("Panel", reply_markup=markup)


@pytest.mark.asyncio
async def test_unchanged_panel_does_not_create_duplicate_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edit = AsyncMock(side_effect=bad_request("message is not modified"))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit)
    monkeypatch.setattr(Message, "answer", answer)

    await edit_panel(callback(), "Panel", dashboard_keyboard())

    answer.assert_not_awaited()
