"""auto reply attachments

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-11-21 12:05:00

Lets a rule carry one file (price list, onboarding PDF, ...) that is sent with
the generated reply. The file lives in ``files`` with a NULL message_id.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("auto_reply_rules") as batch_op:
        batch_op.add_column(sa.Column("attachment_file_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("auto_reply_rules") as batch_op:
        batch_op.drop_column("attachment_file_id")
