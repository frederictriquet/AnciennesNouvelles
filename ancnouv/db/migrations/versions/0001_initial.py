"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tables ORM (events, rss_articles, posts, meta_tokens) générées par autogenerate.
    # scheduler_state n'est pas un modèle ORM — créée explicitement ici. [DATABASE.md]
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="wikipedia"),
        sa.Column("source_lang", sa.String(), nullable=False, server_default="fr"),
        sa.Column("event_type", sa.String(), nullable=False, server_default="event"),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("wikipedia_url", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="available"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("published_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('available', 'blocked')"),
        sa.UniqueConstraint("source", "source_lang", "month", "day", "year", "content_hash"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_events_date", "events", ["month", "day"])
    op.create_index("idx_events_status", "events", ["status"])
    op.create_index("idx_events_year", "events", ["year"])
    op.create_index("idx_events_date_status", "events", ["month", "day", "status", "published_count"])

    op.create_table(
        "rss_articles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("feed_url", sa.Text(), nullable=False),
        sa.Column("feed_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("article_url", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("status", sa.String(), nullable=False, server_default="available"),
        sa.Column("published_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('available', 'blocked')"),
        sa.UniqueConstraint("article_url"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rss_published_at", "rss_articles", ["published_at"])
    op.create_index("idx_rss_status", "rss_articles", ["status"])
    op.create_index("idx_rss_status_fetched_at", "rss_articles", ["status", "fetched_at"])
    op.create_index("idx_rss_feed_url", "rss_articles", ["feed_url"])

    op.create_table(
        "meta_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token_kind", sa.String(), nullable=False),
        sa.Column("ig_user_id", sa.Text(), nullable=True),
        sa.Column("ig_username", sa.Text(), nullable=True),
        sa.Column("fb_page_id", sa.Text(), nullable=True),
        sa.Column("fb_page_name", sa.Text(), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(), nullable=True),
        sa.Column("refresh_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_alert_days_threshold", sa.Integer(), nullable=True),
        sa.Column("last_alert_sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("token_kind IN ('user_long', 'page')"),
        sa.UniqueConstraint("token_kind"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("rss_articles.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=True),
        sa.Column("image_public_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending_approval"),
        sa.Column("telegram_message_ids", sa.Text(), nullable=False, server_default="'{}'"),
        sa.Column("ig_container_id", sa.Text(), nullable=True),
        sa.Column("instagram_post_id", sa.Text(), nullable=True),
        sa.Column("instagram_error", sa.Text(), nullable=True),
        sa.Column("facebook_post_id", sa.Text(), nullable=True),
        sa.Column("facebook_error", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_approval', 'approved', 'queued', 'publishing', "
            "'published', 'rejected', 'skipped', 'error', 'expired')"
        ),
        sa.CheckConstraint(
            "(event_id IS NOT NULL AND article_id IS NULL) OR "
            "(event_id IS NULL AND article_id IS NOT NULL)"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_posts_status", "posts", ["status"])
    op.create_index("idx_posts_created_at", "posts", ["created_at"])
    op.create_index("idx_posts_status_created_at", "posts", ["status", "created_at"])
    op.create_index("idx_posts_event_id", "posts", ["event_id"])
    op.create_index("idx_posts_article_id", "posts", ["article_id"])
    op.create_index("idx_posts_event_id_status", "posts", ["event_id", "status"])
    op.create_index("idx_posts_article_id_status", "posts", ["article_id", "status"])

    # [DATABASE.md] scheduler_state hors ORM — créée explicitement ici
    op.execute(
        "CREATE TABLE IF NOT EXISTS scheduler_state ("
        "key TEXT PRIMARY KEY, "
        "value TEXT NOT NULL DEFAULT '', "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_table("posts")
    op.drop_table("meta_tokens")
    op.drop_table("rss_articles")
    op.drop_table("events")
    op.execute("DROP TABLE IF EXISTS scheduler_state")
