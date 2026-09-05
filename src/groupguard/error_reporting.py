from __future__ import annotations

import traceback
from dataclasses import dataclass

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError


@dataclass(frozen=True, slots=True)
class ErrorSummary:
    title: str
    detail: str


def is_expired_callback_query_error(error: BaseException) -> bool:
    if not isinstance(error, TelegramBadRequest):
        return False
    message = error.message.casefold()
    return (
        "query is too old" in message
        or "response timeout expired" in message
        or "query id is invalid" in message
    )


_ERROR_STAGES = {
    "upsert_user": "сохранение профиля пользователя",
    "_process_serialized": "проверка сообщения",
    "process": "проверка сообщения",
    "_apply_auto_sanction": "автоматическое ограничение и удаление",
    "act": "выполнение решения владельца",
    "deliver_case": "доставка карточки владельцам",
    "sync_case": "синхронизация карточки владельцам",
    "edit_panel": "обновление личной панели",
}


def error_stage(error: BaseException) -> str | None:
    for frame in reversed(traceback.extract_tb(error.__traceback__)):
        stage = _ERROR_STAGES.get(frame.name)
        if stage is not None:
            return stage
    return None


def _telegram_bad_request_detail(error: TelegramBadRequest) -> str:
    message = error.message.casefold()
    if "user_privacy_restricted" in message:
        return "Настройки приватности пользователя запрещают прямую ссылку на его профиль."
    if "not enough rights" in message or "administrator rights" in message:
        return "Боту не хватает необходимых прав администратора."
    if "message to delete not found" in message or "message can't be deleted" in message:
        return "Сообщение уже удалено или больше недоступно для удаления."
    if "user is an administrator" in message:
        return "Telegram не разрешает ограничить администратора группы."
    if "chat not found" in message:
        return "Группа недоступна боту или была отключена."
    return "Telegram отклонил запрос; точный ответ сохранён в журнале контейнера."


def summarize_exception(error: BaseException) -> ErrorSummary:
    if isinstance(error, IntegrityError):
        original_name = type(error.orig).__name__
        details = {
            "UniqueViolation": "В базе уже существует запись с таким уникальным ключом.",
            "ForeignKeyViolation": "Связанная запись в базе отсутствует или была удалена.",
            "NotNullViolation": "В обязательное поле базы не было передано значение.",
            "CheckViolation": "Данные не прошли проверку ограничения базы.",
        }
        return ErrorSummary(
            "Конфликт записи в базе данных",
            details.get(original_name, "Операция нарушила ограничение целостности базы."),
        )
    if isinstance(error, OperationalError):
        return ErrorSummary(
            "База данных временно недоступна",
            "Не удалось установить соединение или выполнить операцию с PostgreSQL.",
        )
    if isinstance(error, TelegramRetryAfter):
        return ErrorSummary(
            "Telegram временно ограничил частоту запросов",
            f"Повторный запрос разрешён примерно через {error.retry_after} сек.",
        )
    if isinstance(error, TelegramForbiddenError):
        return ErrorSummary(
            "Telegram запретил действие",
            "Проверьте доступ бота к группе и его права администратора.",
        )
    if isinstance(error, TelegramBadRequest):
        return ErrorSummary("Telegram отклонил запрос", _telegram_bad_request_detail(error))
    if isinstance(error, TelegramNetworkError | TelegramServerError | TimeoutError):
        return ErrorSummary(
            "Временная ошибка связи с Telegram",
            "Telegram или сеть не ответили корректно; подробности сохранены в журнале.",
        )
    if isinstance(error, SQLAlchemyError):
        return ErrorSummary(
            "Ошибка базы данных",
            "PostgreSQL не смог выполнить операцию; подробности сохранены в журнале.",
        )
    return ErrorSummary(
        "Внутренняя ошибка обработки",
        f"Тип ошибки: {type(error).__name__}. Полный traceback сохранён в журнале.",
    )
