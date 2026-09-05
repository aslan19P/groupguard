from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from groupguard.domain import build_fingerprint
from groupguard.models import MessageFingerprint
from groupguard.services.moderation import classify_candidate, phash_distance

SECRET = "test-hmac-secret"


def candidate(
    text: str,
    *,
    user_id: int = 10,
    local_date: date = date(2026, 9, 2),
    message_id: int = 1,
    media_unique_id: str | None = None,
    media_phash: str | None = None,
) -> MessageFingerprint:
    fingerprint = build_fingerprint(text, SECRET)
    now = datetime.now(UTC)
    return MessageFingerprint(
        chat_id=-1001,
        message_id=message_id,
        user_id=user_id,
        local_date=local_date,
        normalized_text=fingerprint.normalized_text,
        text_hash=fingerprint.text_hash,
        phone_hashes=list(fingerprint.phone_hashes),
        phone_masks=list(fingerprint.phone_masks),
        media_file_unique_id=media_unique_id,
        media_phash=media_phash,
        source_created_at=now,
        expires_at=now + timedelta(hours=48),
    )


def classify(
    current_text: str,
    previous: MessageFingerprint,
    *,
    user_id: int = 10,
    local_date: date = date(2026, 9, 2),
    media_unique_id: str | None = None,
    media_phash: str | None = None,
):
    current = build_fingerprint(current_text, SECRET)
    return classify_candidate(
        user_id=user_id,
        local_date=local_date,
        text_hash=current.text_hash,
        normalized_text=current.normalized_text,
        phone_hashes=current.phone_hashes,
        media_file_unique_id=media_unique_id,
        media_phash=media_phash,
        candidate=previous,
        similarity_threshold=85,
    )


def test_exact_duplicate_same_user_day_text_and_phone() -> None:
    previous = candidate("Такси Шовот Ургенч +998 90 123 45 67")
    result = classify("ТАКСИ, Шовот — Ургенч 90-123-45-67", previous)
    assert result.exact


def test_one_overlapping_number_out_of_two_is_enough() -> None:
    previous = candidate("Такси +998901234567 +998917654321")
    result = classify("Такси 90 123 45 67 +998 93 555 44 33", previous)
    assert result.exact


@pytest.mark.parametrize(
    ("user_id", "day"),
    [(11, date(2026, 9, 2)), (10, date(2026, 9, 3))],
)
def test_other_author_or_day_is_not_exact(user_id: int, day: date) -> None:
    previous = candidate("Такси +998901234567")
    assert not classify("Такси 90 123 45 67", previous, user_id=user_id, local_date=day).exact


def test_same_phone_other_author_goes_to_manual_queue() -> None:
    previous = candidate("Такси Шовот +998901234567", user_id=11)
    assert classify("Везу в Ургенч 90 123 45 67", previous).suspicion_reason == "phone_other_user"


def test_similar_text_same_phone_goes_to_manual_queue() -> None:
    previous = candidate("Такси Шовот Ургенч +998901234567")
    result = classify("Такси Шовот в Ургенч 90 123 45 67", previous)
    assert not result.exact
    assert result.suspicion_reason == "similar_text"


def test_new_calendar_day_does_not_create_any_case() -> None:
    previous = candidate("Такси Шовот Ургенч +998901234567")
    result = classify(
        "Такси Шовот в Ургенч 90 123 45 67",
        previous,
        local_date=date(2026, 9, 3),
    )
    assert not result.exact
    assert result.suspicion_reason is None


def test_new_calendar_day_ignores_repeated_media_too() -> None:
    previous = candidate("", media_unique_id="telegram-file")
    result = classify(
        "",
        previous,
        local_date=date(2026, 9, 3),
        media_unique_id="telegram-file",
    )
    assert result.suspicion_reason is None


def test_repeated_and_similar_media_go_to_manual_queue() -> None:
    same_file = candidate("", media_unique_id="telegram-file")
    assert classify("", same_file, media_unique_id="telegram-file").suspicion_reason == (
        "repeated_media"
    )
    similar = candidate("", media_phash="0000000000000000")
    assert classify("", similar, media_phash="0000000000000003").suspicion_reason == (
        "similar_media"
    )


@pytest.mark.parametrize(
    ("left", "right", "distance"),
    [("0", "0", 0), ("0", "f", 4), ("ffff", "0000", 16)],
)
def test_phash_hamming_distance(left: str, right: str, distance: int) -> None:
    assert phash_distance(left, right) == distance
