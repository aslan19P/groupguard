from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


def uuid_str() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AppState(Base):
    __tablename__ = "app_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    bootstrap_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManagedChat(TimestampMixin, Base):
    __tablename__ = "managed_chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    connected_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class UserProfile(TimestampMixin, Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    current_username: Mapped[str | None] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    violation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deletion_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mute_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_violation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsernameAlias(Base):
    __tablename__ = "username_aliases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_profiles.user_id", ondelete="CASCADE"), index=True
    )
    username: Mapped[str] = mapped_column(String(64), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("uq_alias_user_username", "user_id", "username", unique=True),)


class Owner(TimestampMixin, Base):
    __tablename__ = "owners"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    private_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    added_by_user_id: Mapped[int | None] = mapped_column(BigInteger)


class PendingOwnerInvite(TimestampMixin, Base):
    __tablename__ = "pending_owner_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_by_user_id: Mapped[int | None] = mapped_column(BigInteger)


class AllowlistEntry(TimestampMixin, Base):
    __tablename__ = "allowlist_entries"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    added_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))


class MessageFingerprint(TimestampMixin, Base):
    __tablename__ = "message_fingerprints"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    local_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    phone_hashes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    phone_masks: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    media_file_id: Mapped[str | None] = mapped_column(Text)
    media_file_unique_id: Mapped[str | None] = mapped_column(String(255), index=True)
    media_phash: Mapped[str | None] = mapped_column(String(32), index=True)
    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )

    __table_args__ = (
        Index("uq_fingerprint_message", "chat_id", "message_id", unique=True),
        Index("ix_fingerprint_duplicate", "chat_id", "user_id", "local_date", "text_hash"),
    )


class ModerationCase(TimestampMixin, Base):
    __tablename__ = "moderation_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    phone_masks: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    media_file_id: Mapped[str | None] = mapped_column(Text)
    message_link: Mapped[str | None] = mapped_column(Text)
    reference_message_id: Mapped[int | None] = mapped_column(BigInteger)
    reference_message_link: Mapped[str | None] = mapped_column(Text)
    delete_available_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[int | None] = mapped_column(BigInteger)
    resolution: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (Index("uq_case_source", "chat_id", "source_message_id", unique=True),)


class CaseDelivery(Base):
    __tablename__ = "case_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("moderation_cases.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    private_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivery_type: Mapped[str] = mapped_column(String(10), nullable=False)

    __table_args__ = (Index("uq_case_owner", "case_id", "owner_user_id", unique=True),)


class Sanction(TimestampMixin, Base):
    __tablename__ = "sanctions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="mute", nullable=False)
    until_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    case_id: Mapped[str | None] = mapped_column(String(36))
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
