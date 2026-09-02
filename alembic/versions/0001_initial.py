"""Initial schema.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "app_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bootstrap_consumed_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        sa.table("app_state", sa.column("id", sa.Integer())),
        [{"id": 1}],
    )
    op.create_table(
        "managed_chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("username", sa.String(64)),
        sa.Column("connected_by_user_id", sa.BigInteger(), nullable=False),
        *timestamps(),
    )
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("current_username", sa.String(64)),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("violation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deletion_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mute_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_violation_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_user_profiles_current_username", "user_profiles", ["current_username"])
    op.create_table(
        "username_aliases",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("user_profiles.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_username_aliases_user_id", "username_aliases", ["user_id"])
    op.create_index("ix_username_aliases_username", "username_aliases", ["username"])
    op.create_index(
        "uq_alias_user_username", "username_aliases", ["user_id", "username"], unique=True
    )
    op.create_table(
        "owners",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("user_profiles.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("private_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("added_by_user_id", sa.BigInteger()),
        *timestamps(),
    )
    op.create_table(
        "pending_owner_invites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_by_user_id", sa.BigInteger()),
        *timestamps(),
    )
    op.create_index("ix_pending_owner_invites_username", "pending_owner_invites", ["username"])
    op.create_table(
        "allowlist_entries",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("user_profiles.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("added_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(255)),
        *timestamps(),
    )
    op.create_table(
        "message_fingerprints",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("phone_hashes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("phone_masks", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("excerpt", sa.Text()),
        sa.Column("media_file_id", sa.Text()),
        sa.Column("media_file_unique_id", sa.String(255)),
        sa.Column("media_phash", sa.String(32)),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
    )
    op.create_index(
        "uq_fingerprint_message", "message_fingerprints", ["chat_id", "message_id"], unique=True
    )
    op.create_index(
        "ix_fingerprint_duplicate",
        "message_fingerprints",
        ["chat_id", "user_id", "local_date", "text_hash"],
    )
    for name, columns in {
        "ix_message_fingerprints_chat_id": ["chat_id"],
        "ix_message_fingerprints_user_id": ["user_id"],
        "ix_message_fingerprints_local_date": ["local_date"],
        "ix_message_fingerprints_text_hash": ["text_hash"],
        "ix_message_fingerprints_media_file_unique_id": ["media_file_unique_id"],
        "ix_message_fingerprints_media_phash": ["media_phash"],
        "ix_message_fingerprints_expires_at": ["expires_at"],
    }.items():
        op.create_index(name, "message_fingerprints", columns)
    op.create_table(
        "moderation_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("excerpt", sa.Text()),
        sa.Column("phone_masks", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("media_file_id", sa.Text()),
        sa.Column("message_link", sa.Text()),
        sa.Column("delete_available_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_user_id", sa.BigInteger()),
        sa.Column("resolution", sa.String(40)),
        *timestamps(),
    )
    op.create_index(
        "uq_case_source", "moderation_cases", ["chat_id", "source_message_id"], unique=True
    )
    op.create_index("ix_moderation_cases_chat_id", "moderation_cases", ["chat_id"])
    op.create_index("ix_moderation_cases_target_user_id", "moderation_cases", ["target_user_id"])
    op.create_index("ix_moderation_cases_status", "moderation_cases", ["status"])
    op.create_table(
        "case_deliveries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "case_id",
            sa.String(36),
            sa.ForeignKey("moderation_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("private_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("delivery_type", sa.String(10), nullable=False),
    )
    op.create_index("ix_case_deliveries_case_id", "case_deliveries", ["case_id"])
    op.create_index("ix_case_deliveries_owner_user_id", "case_deliveries", ["owner_user_id"])
    op.create_index("uq_case_owner", "case_deliveries", ["case_id", "owner_user_id"], unique=True)
    op.create_table(
        "sanctions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="mute"),
        sa.Column("until_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("case_id", sa.String(36)),
        sa.Column("created_by_user_id", sa.BigInteger()),
        *timestamps(),
    )
    for name, columns in {
        "ix_sanctions_chat_id": ["chat_id"],
        "ix_sanctions_user_id": ["user_id"],
        "ix_sanctions_until_at": ["until_at"],
        "ix_sanctions_active": ["active"],
    }.items():
        op.create_index(name, "sanctions", columns)
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("actor_user_id", sa.BigInteger()),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_user_id", sa.BigInteger()),
        sa.Column("case_id", sa.String(36)),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    for name, columns in {
        "ix_audit_events_created_at": ["created_at"],
        "ix_audit_events_actor_user_id": ["actor_user_id"],
        "ix_audit_events_action": ["action"],
        "ix_audit_events_target_user_id": ["target_user_id"],
        "ix_audit_events_case_id": ["case_id"],
    }.items():
        op.create_index(name, "audit_events", columns)


def downgrade() -> None:
    for table in (
        "audit_events",
        "sanctions",
        "case_deliveries",
        "moderation_cases",
        "message_fingerprints",
        "allowlist_entries",
        "pending_owner_invites",
        "owners",
        "username_aliases",
        "user_profiles",
        "managed_chats",
        "app_state",
    ):
        op.drop_table(table)
