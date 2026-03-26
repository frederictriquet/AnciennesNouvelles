"""add gallica_articles table and gallica_id to posts [SPEC-9.3]

Recreates posts table for CheckConstraint 3-way exclusivity update.

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Créer la table gallica_articles
    op.create_table(
        "gallica_articles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ark_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("date_published", sa.Date(), nullable=False),
        sa.Column("gallica_url", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="available"),
        sa.Column("published_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("ark_id"),
        sa.CheckConstraint(
            "status IN ('available', 'blocked')",
            name="ck_gallica_articles_status",
        ),
    )
    op.create_index("idx_gallica_date_published", "gallica_articles", ["date_published"])
    op.create_index("idx_gallica_status", "gallica_articles", ["status"])
    op.create_index(
        "idx_gallica_status_created",
        "gallica_articles",
        ["status", "created_at"],
    )

    # 2. Recréer posts avec gallica_id + contrainte d'exclusivité 3-sources mise à jour
    # SQLite ne permet pas de modifier les CHECK constraints en place [ARCH-M7]
    op.execute("ALTER TABLE posts RENAME TO posts_old")

    op.execute(
        """
        CREATE TABLE posts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id   INTEGER,
            article_id INTEGER,
            gallica_id INTEGER,
            caption    TEXT    NOT NULL,
            image_path TEXT,
            image_public_url TEXT,
            status     VARCHAR NOT NULL DEFAULT 'pending_approval',
            telegram_message_ids TEXT NOT NULL DEFAULT '{}',
            ig_container_id TEXT,
            story_image_path TEXT,
            story_post_id    TEXT,
            instagram_post_id TEXT,
            instagram_error   TEXT,
            facebook_post_id  TEXT,
            facebook_error    TEXT,
            threads_post_id   TEXT,
            threads_error     TEXT,
            error_message TEXT,
            retry_count   INTEGER NOT NULL DEFAULT 0,
            created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            approved_at  DATETIME,
            published_at DATETIME,
            scheduled_for DATETIME,
            CHECK (status IN (
                'pending_approval', 'approved', 'queued', 'publishing',
                'published', 'rejected', 'skipped', 'error', 'expired'
            )),
            CHECK (
                (event_id IS NOT NULL AND article_id IS NULL     AND gallica_id IS NULL    ) OR
                (event_id IS NULL     AND article_id IS NOT NULL AND gallica_id IS NULL    ) OR
                (event_id IS NULL     AND article_id IS NULL     AND gallica_id IS NOT NULL)
            )
        )
        """
    )

    op.execute(
        """
        INSERT INTO posts (
            id, event_id, article_id, gallica_id,
            caption, image_path, image_public_url,
            status, telegram_message_ids, ig_container_id,
            story_image_path, story_post_id,
            instagram_post_id, instagram_error,
            facebook_post_id, facebook_error,
            threads_post_id, threads_error,
            error_message, retry_count,
            created_at, updated_at, approved_at, published_at, scheduled_for
        )
        SELECT
            id, event_id, article_id, NULL,
            caption, image_path, image_public_url,
            status, telegram_message_ids, ig_container_id,
            story_image_path, story_post_id,
            instagram_post_id, instagram_error,
            facebook_post_id, facebook_error,
            threads_post_id, threads_error,
            error_message, retry_count,
            created_at, updated_at, approved_at, published_at, scheduled_for
        FROM posts_old
        """
    )

    op.execute("DROP TABLE posts_old")

    # Recréer les index sur la nouvelle table posts
    op.create_index("idx_posts_status",            "posts", ["status"])
    op.create_index("idx_posts_created_at",        "posts", ["created_at"])
    op.create_index("idx_posts_status_created_at", "posts", ["status", "created_at"])
    op.create_index("idx_posts_event_id",          "posts", ["event_id"])
    op.create_index("idx_posts_article_id",        "posts", ["article_id"])
    op.create_index("idx_posts_event_id_status",   "posts", ["event_id", "status"])
    op.create_index("idx_posts_article_id_status", "posts", ["article_id", "status"])
    op.create_index("idx_posts_gallica_id",        "posts", ["gallica_id"])


def downgrade() -> None:
    # Supprimer l'index gallica
    op.drop_index("idx_posts_gallica_id", table_name="posts")

    # Recréer posts sans gallica_id + contrainte 2-sources
    op.execute("ALTER TABLE posts RENAME TO posts_old")

    op.execute(
        """
        CREATE TABLE posts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id   INTEGER,
            article_id INTEGER,
            caption    TEXT    NOT NULL,
            image_path TEXT,
            image_public_url TEXT,
            status     VARCHAR NOT NULL DEFAULT 'pending_approval',
            telegram_message_ids TEXT NOT NULL DEFAULT '{}',
            ig_container_id TEXT,
            story_image_path TEXT,
            story_post_id    TEXT,
            instagram_post_id TEXT,
            instagram_error   TEXT,
            facebook_post_id  TEXT,
            facebook_error    TEXT,
            threads_post_id   TEXT,
            threads_error     TEXT,
            error_message TEXT,
            retry_count   INTEGER NOT NULL DEFAULT 0,
            created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            approved_at  DATETIME,
            published_at DATETIME,
            scheduled_for DATETIME,
            CHECK (status IN (
                'pending_approval', 'approved', 'queued', 'publishing',
                'published', 'rejected', 'skipped', 'error', 'expired'
            )),
            CHECK (
                (event_id IS NOT NULL AND article_id IS NULL) OR
                (event_id IS NULL     AND article_id IS NOT NULL)
            )
        )
        """
    )

    op.execute(
        """
        INSERT INTO posts (
            id, event_id, article_id,
            caption, image_path, image_public_url,
            status, telegram_message_ids, ig_container_id,
            story_image_path, story_post_id,
            instagram_post_id, instagram_error,
            facebook_post_id, facebook_error,
            threads_post_id, threads_error,
            error_message, retry_count,
            created_at, updated_at, approved_at, published_at, scheduled_for
        )
        SELECT
            id, event_id, article_id,
            caption, image_path, image_public_url,
            status, telegram_message_ids, ig_container_id,
            story_image_path, story_post_id,
            instagram_post_id, instagram_error,
            facebook_post_id, facebook_error,
            threads_post_id, threads_error,
            error_message, retry_count,
            created_at, updated_at, approved_at, published_at, scheduled_for
        FROM posts_old
        WHERE gallica_id IS NULL
        """
    )

    op.execute("DROP TABLE posts_old")

    # Recréer les index sans gallica
    op.create_index("idx_posts_status",            "posts", ["status"])
    op.create_index("idx_posts_created_at",        "posts", ["created_at"])
    op.create_index("idx_posts_status_created_at", "posts", ["status", "created_at"])
    op.create_index("idx_posts_event_id",          "posts", ["event_id"])
    op.create_index("idx_posts_article_id",        "posts", ["article_id"])
    op.create_index("idx_posts_event_id_status",   "posts", ["event_id", "status"])
    op.create_index("idx_posts_article_id_status", "posts", ["article_id", "status"])

    # Supprimer gallica_articles
    op.drop_index("idx_gallica_status_created", table_name="gallica_articles")
    op.drop_index("idx_gallica_status", table_name="gallica_articles")
    op.drop_index("idx_gallica_date_published", table_name="gallica_articles")
    op.drop_table("gallica_articles")
