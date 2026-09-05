"""Store the first message matched by a moderation case.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "moderation_cases",
        sa.Column("reference_message_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "moderation_cases",
        sa.Column("reference_message_link", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("moderation_cases", "reference_message_link")
    op.drop_column("moderation_cases", "reference_message_id")
