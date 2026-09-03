from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from rapidfuzz.fuzz import ratio

# Uzbekistan local numbers and explicit international numbers. The explicit '+' branch
# stops at the next plus sign, so two adjacent numbers are not merged.
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:"
    r"\+?\s*998(?:[\s()\-]*\d){9}"
    r"|\+\s*\d(?:[\s()\-]*\d){9,14}"
    r"|(?:\d[\s()\-]*){8}\d"
    r")(?!\d)"
)


@dataclass(frozen=True, slots=True)
class PhoneMatch:
    raw: str
    normalized: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class ContentFingerprint:
    normalized_text: str
    text_hash: str
    phone_hashes: tuple[str, ...]
    phone_masks: tuple[str, ...]


def normalize_username(username: str) -> str:
    return username.strip().removeprefix("@").casefold()


def normalize_phone(raw: str) -> str | None:
    digits = "".join(character for character in raw if character.isdigit())
    if len(digits) == 9:
        return f"998{digits}"
    if len(digits) == 10 and digits.startswith("0"):
        return f"998{digits[1:]}"
    if 10 <= len(digits) <= 15:
        return digits
    return None


def extract_phones(text: str) -> list[PhoneMatch]:
    result: list[PhoneMatch] = []
    seen: set[tuple[int, int]] = set()
    for match in PHONE_PATTERN.finditer(text):
        normalized = normalize_phone(match.group())
        if normalized is None or (match.start(), match.end()) in seen:
            continue
        seen.add((match.start(), match.end()))
        result.append(
            PhoneMatch(
                raw=match.group(),
                normalized=normalized,
                start=match.start(),
                end=match.end(),
            )
        )
    return result


def mask_phone(phone: str) -> str:
    if len(phone) <= 6:
        return "*" * len(phone)
    return f"{phone[:3]} {'*' * max(3, len(phone) - 7)} {phone[-4:]}"


def hmac_digest(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def normalize_text(text: str, phones: list[PhoneMatch] | None = None) -> str:
    matches = phones if phones is not None else extract_phones(text)
    parts: list[str] = []
    cursor = 0
    for phone in matches:
        parts.append(text[cursor : phone.start])
        parts.append(" phone ")
        cursor = phone.end
    parts.append(text[cursor:])
    phone_replaced = "".join(parts).casefold()

    characters: list[str] = []
    for character in unicodedata.normalize("NFKC", phone_replaced):
        category = unicodedata.category(character)
        if character.isalnum() or character.isspace() or category.startswith("M"):
            characters.append(character)
        else:
            characters.append(" ")
    return " ".join("".join(characters).split())


def build_fingerprint(
    text: str,
    hmac_secret: str,
    additional_phones: Iterable[str] = (),
) -> ContentFingerprint:
    phones = extract_phones(text)
    normalized_text = normalize_text(text, phones)
    normalized_phones = {phone.normalized for phone in phones}
    normalized_phones.update(
        normalized for raw in additional_phones if (normalized := normalize_phone(raw)) is not None
    )
    return ContentFingerprint(
        normalized_text=normalized_text,
        text_hash=hashlib.sha256(normalized_text.encode()).hexdigest(),
        phone_hashes=tuple(hmac_digest(phone, hmac_secret) for phone in sorted(normalized_phones)),
        phone_masks=tuple(mask_phone(phone) for phone in sorted(normalized_phones)),
    )


def hashes_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    return bool(set(left).intersection(right))


def text_similarity(left: str, right: str) -> int:
    if not left and not right:
        return 100
    return round(ratio(left, right))


def local_calendar_date(timestamp: datetime, timezone: ZoneInfo) -> date:
    return timestamp.astimezone(timezone).date()


def format_local_datetime(
    timestamp: datetime,
    timezone: ZoneInfo,
    date_format: str = "%d.%m.%Y %H:%M",
) -> str:
    return timestamp.astimezone(timezone).strftime(date_format)


def safe_excerpt(text: str, limit: int = 300) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1]}…"


def message_link(chat_username: str | None, chat_id: int, message_id: int) -> str:
    if chat_username:
        return f"https://t.me/{chat_username}/{message_id}"
    internal_id = str(abs(chat_id)).removeprefix("100")
    return f"https://t.me/c/{internal_id}/{message_id}"
