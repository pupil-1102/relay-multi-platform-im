"""platform configuration overrides

Revision ID: c1f2a3b4d5e6
Revises: b7d4e9c2a1f3
Create Date: 2026-11-21 11:20:00

Stores per-platform credentials entered from the UI. Values override the
environment settings at startup and are applied to the running app when saved.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import backend.app.db

revision: str = "c1f2a3b4d5e6"
down_revision: Union[str, None] = "b7d4e9c2a1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_configs",
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_enc", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", backend.app.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", backend.app.db.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("platform"),
    )


def downgrade() -> None:
    op.drop_table("platform_configs")
