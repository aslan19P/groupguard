from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from aiogram.enums import MessageEntityType
from aiogram.types import MessageEntity

from groupguard.domain import (
    build_fingerprint,
    extract_phones,
    format_local_datetime,
    local_calendar_date,
    normalize_phone,
    normalize_text,
)
from groupguard.services.moderation import entity_phone_values


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("+998901234567", "998901234567"),
        ("+998 90 123-45-67", "998901234567"),
        ("998 (90) 123 45 67", "998901234567"),
        ("(90) 123 45 67", "998901234567"),
        ("90-123-45-67", "998901234567"),
        ("901234567", "998901234567"),
        ("+7 (999) 123-45-67", "79991234567"),
    ],
)
def test_phone_formats(text: str, expected: str) -> None:
    assert [match.normalized for match in extract_phones(text)] == [expected]


def test_two_numbers_are_extracted_separately() -> None:
    matches = extract_phones("+998 90 123 45 67 или +998-91-765-43-21")
    assert [item.normalized for item in matches] == ["998901234567", "998917654321"]


@pytest.mark.parametrize(
    "text",
    [
        "цена 150000 сум, год 2020",
        "выезд в 12:30, 2 пассажира",
        "заказ №12345678",
        "дата 02.09.2026",
    ],
)
def test_common_numbers_are_not_phones(text: str) -> None:
    assert extract_phones(text) == []


def test_normalization_ignores_case_punctuation_emoji_and_phone_format() -> None:
    left = normalize_text("ТАКСИ 🚕 Шовот — Ургенч! +998 90 123-45-67")
    right = normalize_text("такси шовот, ургенч (90) 123 45 67")
    assert left == right == "такси шовот ургенч phone"


def test_phone_fingerprint_is_hmac_and_does_not_contain_raw_number() -> None:
    fingerprint = build_fingerprint("Такси +998 90 123 45 67", "secret-value")
    assert (
        fingerprint.phone_hashes
        == build_fingerprint("Такси 90-123-45-67", "secret-value").phone_hashes
    )
    assert "998901234567" not in fingerprint.phone_hashes[0]
    assert fingerprint.phone_masks == ("998 ***** 4567",)


def test_entities_add_hidden_tel_number() -> None:
    text = "Позвонить"
    entity = MessageEntity(
        type=MessageEntityType.TEXT_LINK,
        offset=0,
        length=len(text),
        url="tel:+998901234567",
    )
    values = entity_phone_values(text, [entity])
    assert values == ["+998901234567"]
    assert build_fingerprint(text, "secret-value", values).phone_hashes


def test_phone_entity_uses_entity_text() -> None:
    text = "+998 90 123 45 67"
    entity = MessageEntity(
        type=MessageEntityType.PHONE_NUMBER,
        offset=0,
        length=len(text),
    )
    assert entity_phone_values(text, [entity]) == [text]


def test_tashkent_calendar_date_changes_at_local_midnight() -> None:
    timezone = ZoneInfo("Asia/Tashkent")
    before = datetime(2026, 9, 1, 18, 59, 59, tzinfo=UTC)
    after = datetime(2026, 9, 1, 19, 0, 0, tzinfo=UTC)
    assert local_calendar_date(before, timezone).isoformat() == "2026-09-01"
    assert local_calendar_date(after, timezone).isoformat() == "2026-09-02"


def test_datetime_is_formatted_in_configured_timezone() -> None:
    timestamp = datetime(2026, 9, 3, 5, 39, tzinfo=UTC)

    assert format_local_datetime(timestamp, ZoneInfo("Asia/Tashkent")) == "03.09.2026 10:39"
    assert format_local_datetime(timestamp, ZoneInfo("Europe/Moscow")) == "03.09.2026 08:39"


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("0123456789", "998123456789"), ("+1 202 555 0100", "12025550100")],
)
def test_normalize_phone(raw: str, normalized: str) -> None:
    assert normalize_phone(raw) == normalized
