from __future__ import annotations

from datetime import UTC, datetime

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText
from aiogram.types import Chat, ErrorEvent, Message, Update, User
from sqlalchemy.exc import IntegrityError

from groupguard.error_reporting import (
    error_stage,
    is_expired_callback_query_error,
    summarize_exception,
)
from groupguard.handlers.errors import render_error_notification


class UniqueViolation(Exception):
    pass


def _process_serialized() -> None:
    raise RuntimeError("failure")


def test_integrity_error_is_explained_without_raw_sql() -> None:
    error = IntegrityError(
        "INSERT INTO user_profiles VALUES (...) ",
        {"private": "value"},
        UniqueViolation("duplicate key contains private data"),
    )

    summary = summarize_exception(error)

    assert summary.title == "Конфликт записи в базе данных"
    assert "уже существует" in summary.detail
    assert "INSERT" not in summary.detail
    assert "private" not in summary.detail


def test_error_notification_contains_safe_update_context() -> None:
    update = Update(
        update_id=10,
        message=Message(
            message_id=20,
            date=datetime.now(UTC),
            chat=Chat(id=-1001, type=ChatType.SUPERGROUP, title="Test"),
            from_user=User(id=30, is_bot=False, first_name="Driver"),
            text="Секретный текст сообщения +998 90 123 45 67",
        ),
    )
    event = ErrorEvent(update=update, exception=ValueError("секрет внутри исключения"))

    text = render_error_notification("abcd1234", event)

    assert "ValueError" in text
    assert "message" in text
    assert "-1001" in text
    assert "20" in text
    assert "30" in text
    assert "abcd1234" in text
    assert "Секретный текст" not in text
    assert "+998" not in text
    assert "секрет внутри исключения" not in text


def test_error_stage_uses_only_safe_application_stage() -> None:
    try:
        _process_serialized()
    except RuntimeError as error:
        assert error_stage(error) == "проверка сообщения"


def test_privacy_restricted_button_error_has_clear_explanation() -> None:
    error = TelegramBadRequest(
        method=EditMessageText(text="test", chat_id=1, message_id=2),
        message="Bad Request: BUTTON_USER_PRIVACY_RESTRICTED",
    )

    summary = summarize_exception(error)

    assert summary.title == "Telegram отклонил запрос"
    assert "приватности" in summary.detail
    assert "прямую ссылку" in summary.detail


def test_expired_callback_query_error_is_recognized() -> None:
    error = TelegramBadRequest(
        method=AnswerCallbackQuery(callback_query_id="old"),
        message="query is too old and response timeout expired or query ID is invalid",
    )

    assert is_expired_callback_query_error(error)
