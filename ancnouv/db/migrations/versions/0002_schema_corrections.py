"""add updated_at to events/rss_articles and event_type check constraint

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-22

[DS-14] CheckConstraint event_type IN ('event','birth','death','holiday','selected')
[ROADMAP l.65] updated_at sur events
[ROADMAP l.67] updated_at sur rss_articles
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ajout updated_at + CheckConstraint event_type sur events.
    # recreate='always' requis : SQLite ne supporte pas ALTER TABLE ADD CONSTRAINT.
    with op.batch_alter_table("events", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch_op.create_check_constraint(
            "ck_events_event_type",
            "event_type IN ('event', 'birth', 'death', 'holiday', 'selected')",
        )

    # Ajout updated_at sur rss_articles (pas de nouvelle contrainte — add_column suffit)
    with op.batch_alter_table("rss_articles") as batch_op:
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("rss_articles") as batch_op:
        batch_op.drop_column("updated_at")

    with op.batch_alter_table("events", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_events_event_type", type_="check")
        batch_op.drop_column("updated_at")
