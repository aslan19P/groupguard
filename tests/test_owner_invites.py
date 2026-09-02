from __future__ import annotations

from datetime import UTC, datetime, timedelta

from groupguard.models import PendingOwnerInvite
from groupguard.services.owners import invite_validation_error


def invite(now: datetime) -> PendingOwnerInvite:
    return PendingOwnerInvite(
        username="new_owner",
        token_hash="hash",
        created_by_user_id=1,
        expires_at=now + timedelta(hours=24),
    )


def test_owner_invite_accepts_matching_username() -> None:
    now = datetime.now(UTC)
    assert invite_validation_error(invite(now), "@New_Owner", now) is None


def test_owner_invite_rejects_mismatch_and_missing_username() -> None:
    now = datetime.now(UTC)
    pending = invite(now)
    assert invite_validation_error(pending, "someone_else", now) == "username_mismatch"
    assert invite_validation_error(pending, None, now) == "username_missing"


def test_owner_invite_rejects_expired_and_consumed() -> None:
    now = datetime.now(UTC)
    expired = invite(now)
    expired.expires_at = now
    assert invite_validation_error(expired, "new_owner", now) == "expired"
    consumed = invite(now)
    consumed.consumed_at = now
    assert invite_validation_error(consumed, "new_owner", now) == "consumed"


def test_missing_invite_is_rejected() -> None:
    assert invite_validation_error(None, "new_owner", datetime.now(UTC)) == "missing"
