# Fixtures partagées pour tous les tests [docs/TESTING.md]
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from ancnouv.db.models import Base, Event, MetaToken, RssArticle
from ancnouv.db.session import set_session_factory
from ancnouv.db.utils import compute_content_hash
from ancnouv.scheduler.context import set_engine


# ─── Helpers de seed (pas des fixtures — appelés manuellement dans les tests) ──

async def seed_token(session: AsyncSession) -> MetaToken:
    """Insère un token user_long valide (expire dans 30j)."""
    token = MetaToken(
        token_kind="user_long",
        access_token="test_user_token",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return token


async def seed_page_token(session: AsyncSession) -> MetaToken:
    """Insère un token page permanent (sans expiration)."""
    token = MetaToken(
        token_kind="page",
        access_token="test_page_token",
        expires_at=None,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return token


# ─── Fixtures engine ────────────────────────────────────────────────────────────

@pytest.fixture
async def db_engine():
    """Engine SQLite :memory: isolé par test. [TESTING.md — db_engine]"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"timeout": 15},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_pragmas(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys = ON")
        dbapi_conn.execute("PRAGMA journal_mode = WAL")
        dbapi_conn.execute("PRAGMA busy_timeout = 10000")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS scheduler_state "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))

    set_engine(engine)
    set_session_factory(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_engine_static():
    """Engine StaticPool — requis pour les tests de concurrence (BEGIN EXCLUSIVE).

    [TESTING.md — db_engine_static] StaticPool force toutes les connexions async
    à partager le même objet Connection. Sans StaticPool, BEGIN EXCLUSIVE entre
    deux coroutines deadlockerait sur des DB :memory: distinctes.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_pragmas(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys = ON")
        dbapi_conn.execute("PRAGMA journal_mode = WAL")
        dbapi_conn.execute("PRAGMA busy_timeout = 10000")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS scheduler_state "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))

    set_engine(engine)
    set_session_factory(engine)
    yield engine
    await engine.dispose()


# ─── Fixture session ─────────────────────────────────────────────────────────────

@pytest.fixture
async def db_session(db_engine):
    """Session async isolée par test. expire_on_commit=False. [TESTING.md]"""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


# ─── Fixtures de données ─────────────────────────────────────────────────────────

@pytest.fixture
async def db_event(db_session):
    """Événement Wikipedia disponible inséré en DB. [TESTING.md — db_event]"""
    ev = Event(
        source="wikipedia",
        source_lang="fr",
        event_type="event",
        month=3,
        day=21,
        year=1871,
        description="La Commune de Paris est proclamée.",
        content_hash=compute_content_hash("La Commune de Paris est proclamée."),
        status="available",
        published_count=0,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(ev)
    await db_session.commit()
    await db_session.refresh(ev)
    return ev


@pytest.fixture
async def db_article(db_session, mock_config):
    """Article RSS éligible : fetched_at antérieur au cutoff. [TESTING.md — T-05]"""
    delay = mock_config.content.rss.min_delay_days + 1
    fetched_at = datetime.now(timezone.utc) - timedelta(days=delay)
    article = RssArticle(
        feed_url="https://example.com/feed.rss",
        feed_name="Test Feed",
        title="Article de test RSS",
        article_url="https://example.com/article/test-1",
        summary="Résumé de l'article de test.",
        published_at=datetime.now(timezone.utc) - timedelta(days=365),
        fetched_at=fetched_at.replace(tzinfo=None),
        status="available",
        published_count=0,
    )
    db_session.add(article)
    await db_session.commit()
    await db_session.refresh(article)
    return article


# ─── Fixture config ──────────────────────────────────────────────────────────────

@pytest.fixture
def mock_config():
    """Config minimale sans fichiers YAML ni .env. [TESTING.md]"""
    config = MagicMock()
    config.scheduler.max_pending_posts = 1
    config.scheduler.approval_timeout_hours = 48
    config.scheduler.auto_publish = False
    config.scheduler.timezone = "Europe/Paris"
    config.scheduler.max_queue_size = 10
    config.content.rss.enabled = False
    config.content.rss.min_delay_days = 90
    config.content.rss.max_age_days = 180
    config.content.rss.feeds = []
    config.content.mix_ratio = 0.2
    config.content.event_min_age_years = 0
    config.content.event_max_age_years = 0
    config.content.deduplication_policy = "never"
    config.content.deduplication_window_days = 365
    config.content.low_stock_threshold = 3
    config.content.wikipedia_event_types = ["events"]
    config.content.wikipedia_min_events = 3
    config.content.date_window_days = 0
    config.content.prefetch_days = 30
    config.content.image_retention_days = 7
    config.instagram.enabled = False
    config.instagram.user_id = "test_ig_user"
    config.instagram.api_version = "v21.0"
    config.instagram.max_daily_posts = 25
    config.facebook.enabled = False
    config.facebook.page_id = "test_fb_page"
    config.telegram.authorized_user_ids = [123456789]
    config.telegram.notification_debounce = 0
    config.caption.hashtags = ["#histoire", "#onthisday"]
    config.caption.hashtags_separator = "\n\n"
    config.caption.source_template_fr = "Source : Wikipédia"
    config.caption.source_template_en = "Source : Wikipedia (EN)"
    config.caption.include_wikipedia_url = False
    config.image.width = 1080
    config.image.height = 1350
    config.image.jpeg_quality = 85
    config.image.paper_texture = False
    config.image.paper_texture_intensity = 8
    config.image.masthead_text = "TEST JOURNAL"
    config.image_hosting.backend = "local"
    config.image_hosting.public_base_url = "http://localhost:8765"
    config.image_hosting.local_port = 8765
    config.data_dir = "data"
    config.meta_app_id = "test_meta_app_id"
    config.meta_app_secret = "test_meta_app_secret"
    config.gallica.enabled = False
    config.gallica.mix_ratio = 0.1
    config.reels.enabled = False
    config.threads.enabled = False
    config.stories.enabled = False
    return config


@pytest.fixture
def mock_bot():
    """Mock bot Telegram. [TESTING.md]"""
    bot = MagicMock()
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=998))
    bot.edit_message_reply_markup = AsyncMock()
    bot.edit_message_text = AsyncMock()
    return bot


@pytest.fixture
def mock_wikipedia_response():
    """Réponse API Wikipedia type. [TESTING.md]"""
    return {
        "events": [
            {
                "year": 1871,
                "text": "La Commune de Paris est proclamée.",
                "pages": [],
            }
        ]
    }
