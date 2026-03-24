# Modèles ORM SQLAlchemy 2.x [docs/DATABASE.md, SPEC-3.2]
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Event(Base):
    """Événement historique Wikipedia (Mode A). [DATABASE.md — Table events]"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identification de la source
    source: Mapped[str] = mapped_column(String, nullable=False, default="wikipedia")
    source_lang: Mapped[str] = mapped_column(String, nullable=False, default="fr")
    event_type: Mapped[str] = mapped_column(String, nullable=False, default="event")

    # Date de l'événement historique
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    day: Mapped[int] = mapped_column(Integer, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)

    # Contenu
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    wikipedia_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Hash de déduplication (SHA-256 de NFKC(description).strip().lower())
    content_hash: Mapped[str] = mapped_column(String, nullable=False)

    # Gestion de l'utilisation
    status: Mapped[str] = mapped_column(String, nullable=False, default="available")
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    published_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Métadonnées
    fetched_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("source", "source_lang", "month", "day", "year", "content_hash"),
        CheckConstraint("status IN ('available', 'blocked')"),
        CheckConstraint(  # [DS-14]
            "event_type IN ('event', 'birth', 'death', 'holiday', 'selected')"
        ),
        # [DB-11] index composite couvrant exactement le pattern de select_event
        Index("idx_events_date", "month", "day"),
        Index("idx_events_status", "status"),
        Index("idx_events_year", "year"),
        Index("idx_events_date_status", "month", "day", "status", "published_count"),
    )


class RssArticle(Base):
    """Article RSS (Mode B — optionnel). [DATABASE.md — Table rss_articles]

    Ne pas confondre avec RssFeedItem (ancnouv.fetchers.base) qui est la dataclass
    de transport — différence documentée dans ARCHITECTURE.md (lexique des types).
    """

    __tablename__ = "rss_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Source
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    feed_name: Mapped[str] = mapped_column(Text, nullable=False)

    # Contenu
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    article_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Dates
    published_at: Mapped[datetime] = mapped_column(nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    # Gestion
    status: Mapped[str] = mapped_column(String, nullable=False, default="available")
    published_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("article_url"),
        CheckConstraint("status IN ('available', 'blocked')"),
        Index("idx_rss_published_at", "published_at"),
        Index("idx_rss_status", "status"),
        Index("idx_rss_status_fetched_at", "status", "fetched_at"),
        Index("idx_rss_feed_url", "feed_url"),
    )


class Post(Base):
    """Post en cours de cycle de vie (approval → publication). [DATABASE.md — Table posts]"""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Source du contenu (exclusivité enforced par CheckConstraint)
    event_id: Mapped[int | None] = mapped_column(nullable=True)
    article_id: Mapped[int | None] = mapped_column(nullable=True)

    # Contenu généré
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_public_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Cycle de vie (machine à états — voir DATABASE.md)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending_approval"
    )

    # Telegram : dict JSON {user_id: message_id} par admin
    # [DB-13] server_default syntaxe exacte : guillemets doubles Python, simples SQL
    telegram_message_ids: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'{}'")
    )

    # Container Instagram (protection crash entre étape 1 et 2)
    ig_container_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Story — image et résultats [SPEC-7, SPEC-7.4]
    story_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_post_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Résultats par plateforme
    instagram_post_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    instagram_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    facebook_post_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    facebook_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timestamps
    # [DB-7] onupdate=func.now() ignoré par les UPDATE SQL directs — inclure
    # updated_at=CURRENT_TIMESTAMP explicitement dans ces requêtes
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.now(),
    )
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_approval', 'approved', 'queued', 'publishing', "
            "'published', 'rejected', 'skipped', 'error', 'expired')"
        ),
        # Exclusivité source : exactement un des deux doit être renseigné
        CheckConstraint(
            "(event_id IS NOT NULL AND article_id IS NULL) OR "
            "(event_id IS NULL AND article_id IS NOT NULL)"
        ),
        Index("idx_posts_status", "status"),
        Index("idx_posts_created_at", "created_at"),
        Index("idx_posts_status_created_at", "status", "created_at"),
        Index("idx_posts_event_id", "event_id"),
        Index("idx_posts_article_id", "article_id"),
        Index("idx_posts_event_id_status", "event_id", "status"),
        Index("idx_posts_article_id_status", "article_id", "status"),
    )


class MetaToken(Base):
    """Tokens Meta (utilisateur long + page). [DATABASE.md — Table meta_tokens]

    Deux enregistrements : token_kind='user_long' (expire 60j) + token_kind='page' (permanent).
    Source de vérité unique des tokens — aucun token dans .env.
    """

    __tablename__ = "meta_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    token_kind: Mapped[str] = mapped_column(String, nullable=False)

    ig_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    ig_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    fb_page_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    fb_page_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    last_refreshed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    refresh_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # [TRANSVERSAL-4] Seuil entier (30, 14, 7, 3, 1) de la dernière alerte envoyée.
    # Complémentaire (non redondant) avec scheduler_state.token_alert_level :
    # last_alert_days_threshold = entier anti-doublon ; token_alert_level = chaîne d'affichage.
    last_alert_days_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_alert_sent_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("token_kind"),
        CheckConstraint("token_kind IN ('user_long', 'page')"),
    )
