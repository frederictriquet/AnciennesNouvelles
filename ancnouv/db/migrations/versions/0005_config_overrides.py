"""add config_overrides table for dashboard [DASH-10.1]

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "config_overrides",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_type", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("key"),
        sa.CheckConstraint(
            "value_type IN ('str', 'int', 'float', 'bool', 'list', 'dict')",
            name="ck_config_overrides_value_type",
        ),
    )


def downgrade() -> None:
    op.drop_table("config_overrides")
