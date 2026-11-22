"""user password login

Revision ID: b7d4e9c2a1f3
Revises: 9f2c1b7a4d6e
Create Date: 2026-11-21 11:05:00

Adds an optional local password so people can register without an OAuth
tenant. OAuth-only identities keep ``NULL`` and continue to work.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7d4e9c2a1f3"
down_revision: Union[str, None] = "9f2c1b7a4d6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("password_hash")
