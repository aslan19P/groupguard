from __future__ import annotations

from groupguard.config import Settings
from groupguard.handlers.private import GUEST_HELP_TEXT, owner_help_text
from groupguard.keyboards import dashboard_keyboard, help_keyboard


def test_owner_help_fits_telegram_message_and_covers_common_tasks() -> None:
    settings = Settings(
        bot_token="123456789:test-token",
        bootstrap_token="bootstrap-secret-value",
        database_url="postgresql+psycopg://example",
        phone_hmac_secret="phone-secret-value",
        timezone="Europe/Moscow",
    )
    text = owner_help_text(settings)

    assert len(text) < 4096
    assert "белый список" in text.lower()
    assert "group privacy" in text.lower()
    assert "7 дней" in text
    assert "/menu" in text
    assert "Europe/Moscow" in text


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
