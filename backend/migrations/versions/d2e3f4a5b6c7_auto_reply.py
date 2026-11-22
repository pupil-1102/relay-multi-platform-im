"""auto reply rules

Revision ID: d2e3f4a5b6c7
Revises: c1f2a3b4d5e6
Create Date: 2026-11-21 11:40:00

Adds user-defined keyword/fallback auto-reply rules and tags generated replies
on ``messages`` so the UI can label them and the cooldown can count them.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import backend.app.db

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1f2a3b4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auto_reply_rules",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=True),
        sa.Column("match_type", sa.String(length=20), nullable=False),
        sa.Column("keywords", sa.Text(), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("last_hit_at", backend.app.db.UTCDateTime(), nullable=True),
        sa.Column("created_at", backend.app.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", backend.app.db.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auto_reply_rules_owner_id", "auto_reply_rules", ["owner_id"], unique=False)
    op.create_index(
        "ix_auto_reply_owner_enabled", "auto_reply_rules", ["owner_id", "enabled"], unique=False
    )
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("auto_reply_rule_id", sa.String(length=32), nullable=True))
        batch_op.create_index(
            "ix_messages_auto_reply_rule_id", ["auto_reply_rule_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_index("ix_messages_auto_reply_rule_id")
        batch_op.drop_column("auto_reply_rule_id")
    op.drop_index("ix_auto_reply_owner_enabled", table_name="auto_reply_rules")
    op.drop_index("ix_auto_reply_rules_owner_id", table_name="auto_reply_rules")
    op.drop_table("auto_reply_rules")
