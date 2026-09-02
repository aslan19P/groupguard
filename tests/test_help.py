from __future__ import annotations

from groupguard.handlers.private import GUEST_HELP_TEXT, OWNER_HELP_TEXT
from groupguard.keyboards import dashboard_keyboard, help_keyboard


def test_owner_help_fits_telegram_message_and_covers_common_tasks() -> None:
    assert len(OWNER_HELP_TEXT) < 4096
    assert "белый список" in OWNER_HELP_TEXT.lower()
    assert "group privacy" in OWNER_HELP_TEXT.lower()
    assert "семь" not in OWNER_HELP_TEXT.lower()
    assert "7 дней" in OWNER_HELP_TEXT
    assert "/menu" in OWNER_HELP_TEXT


def test_guest_help_explains_how_to_get_access() -> None:
    assert "одноразовую ссылку-приглашение" in GUEST_HELP_TEXT


def test_dashboard_contains_help_button() -> None:
    keyboard = dashboard_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    help_button = next(button for button in buttons if button.callback_data == "panel:help")
    assert help_button.text == "ℹ️ Помощь"


def test_help_keyboard_returns_to_dashboard() -> None:
    button = help_keyboard().inline_keyboard[0][0]
    assert button.callback_data == "panel:home"
