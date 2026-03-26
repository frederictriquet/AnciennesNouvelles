"""add threads_post_id and threads_error to posts [SPEC-9.1]

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("threads_post_id", sa.Text(), nullable=True))
    op.add_column("posts", sa.Column("threads_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("posts", recreate="always") as batch_op:
        batch_op.drop_column("threads_error")
        batch_op.drop_column("threads_post_id")
