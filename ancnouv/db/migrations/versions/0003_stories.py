"""add story_image_path and story_post_id to posts

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-24

[SPEC-7, SPEC-7.4] Colonnes Story sur la table posts.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("posts") as batch_op:
        batch_op.add_column(sa.Column("story_image_path", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("story_post_id", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("posts") as batch_op:
        batch_op.drop_column("story_post_id")
        batch_op.drop_column("story_image_path")
