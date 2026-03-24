"""add scheduled_for to posts for queue feature

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-24

[SPEC-7ter] Colonne scheduled_for pour la publication planifiée.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("posts") as batch_op:
        batch_op.add_column(sa.Column("scheduled_for", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("posts") as batch_op:
        batch_op.drop_column("scheduled_for")
