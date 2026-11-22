"""conversation read state

Revision ID: 9f2c1b7a4d6e
Revises: 3cd4ac9f2b8d
Create Date: 2026-11-08 17:40:00
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import backend.app.db

revision: str = "9f2c1b7a4d6e"
down_revision: Union[str, None] = "3cd4ac9f2b8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_reads",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("platform_chat_id", sa.String(length=300), nullable=False),
        sa.Column("last_read_at", backend.app.db.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "platform", "platform_chat_id", name="uq_conversation_read"
        ),
    )
    op.create_index(
        "ix_conversation_reads_owner_id", "conversation_reads", ["owner_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_reads_owner_id", table_name="conversation_reads")
    op.drop_table("conversation_reads")
