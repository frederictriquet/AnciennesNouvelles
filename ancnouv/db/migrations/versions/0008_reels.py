"""add reel columns to posts [SPEC-8]

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("reel_video_path", sa.Text(), nullable=True))
    op.add_column("posts", sa.Column("reel_container_id", sa.Text(), nullable=True))
    op.add_column("posts", sa.Column("reel_post_id", sa.Text(), nullable=True))
    op.add_column("posts", sa.Column("reel_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("posts", recreate="always") as batch_op:
        batch_op.drop_column("reel_error")
        batch_op.drop_column("reel_post_id")
        batch_op.drop_column("reel_container_id")
        batch_op.drop_column("reel_video_path")
