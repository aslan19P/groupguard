from __future__ import annotations

from datetime import datetime

from groupguard.domain import normalize_username
from groupguard.models import PendingOwnerInvite


def invite_validation_error(
    invite: PendingOwnerInvite | None,
    username: str | None,
    now: datetime,
) -> str | None:
    if invite is None:
        return "missing"
    if invite.consumed_at is not None:
        return "consumed"
    if invite.expires_at <= now:
        return "expired"
    if username is None:
        return "username_missing"
    if normalize_username(username) != invite.username:
        return "username_mismatch"
    return None
