# Tests d'intégration — cycle de vie des posts et génération [SPEC-3.2, SPEC-3.4]
# [TESTING.md — test_post_lifecycle.py, T-10]
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ancnouv.db.models import Event, Post, RssArticle
from ancnouv.db.utils import compute_content_hash, set_scheduler_state


# ─── Machine à états ────────────────────────────────────────────────────────────

async def test_post_approved_then_published(db_session, db_event):
    """pending_approval → approved → published. [TESTING.md]"""
    post = Post(
        event_id=db_event.id,
        caption="Test",
        image_path="/tmp/test.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    post.status = "approved"
    await db_session.commit()

    post.status = "published"
    post.instagram_post_id = "post_123"
    await db_session.commit()

    await db_session.refresh(post)
    assert post.status == "published"
    assert post.instagram_post_id == "post_123"


async def test_publish_both_platforms_success(db_session, db_event, mock_config, httpx_mock):
    """IG + FB réussis → status='published', deux post_ids stockés. [TESTING.md]"""
    from tests.conftest import seed_page_token, seed_token
    from ancnouv.publisher import publish_to_all_platforms
    from ancnouv.publisher.facebook import FacebookPublisher
    from ancnouv.publisher.instagram import InstagramPublisher
    from ancnouv.publisher.token_manager import TokenManager

    await seed_token(db_session)
    await seed_page_token(db_session)

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media",
        json={"id": "container_1"},
    )
    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media_publish",
        json={"id": "ig_post_1"},
    )
    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.facebook.page_id}/photos",
        json={"post_id": "fb_post_1"},
    )

    post = Post(
        event_id=db_event.id,
        caption="Caption test",
        image_path="/tmp/test.jpg",
        status="approved",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    ig_pub = InstagramPublisher(mock_config.instagram.user_id, token_mgr)
    fb_pub = FacebookPublisher(mock_config.facebook.page_id, token_mgr)

    with patch.object(ig_pub, "_get_container_status", new=AsyncMock(return_value="FINISHED")):
        await publish_to_all_platforms(
            post, "https://example.com/img.jpg", ig_pub, fb_pub, db_session
        )

    await db_session.refresh(post)
    assert post.status == "published"
    assert post.instagram_post_id == "ig_post_1"
    assert post.facebook_post_id == "fb_post_1"
    assert post.error_message is None


async def test_publish_instagram_ok_facebook_fails(db_session, db_event, mock_config, httpx_mock):
    """IG OK + FB erreur → status='published', facebook_error renseigné. [TESTING.md]"""
    from tests.conftest import seed_page_token, seed_token
    from ancnouv.publisher import publish_to_all_platforms
    from ancnouv.publisher.facebook import FacebookPublisher
    from ancnouv.publisher.instagram import InstagramPublisher
    from ancnouv.publisher.token_manager import TokenManager

    await seed_token(db_session)
    await seed_page_token(db_session)

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media",
        json={"id": "container_2"},
    )
    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media_publish",
        json={"id": "ig_post_2"},
    )
    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.facebook.page_id}/photos",
        json={"error": {"code": 2, "message": "FB error"}},
    )

    post = Post(
        event_id=db_event.id,
        caption="Test",
        image_path="/tmp/test.jpg",
        status="approved",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    ig_pub = InstagramPublisher(mock_config.instagram.user_id, token_mgr)
    fb_pub = FacebookPublisher(mock_config.facebook.page_id, token_mgr)

    with patch.object(ig_pub, "_get_container_status", new=AsyncMock(return_value="FINISHED")):
        await publish_to_all_platforms(
            post, "https://example.com/img.jpg", ig_pub, fb_pub, db_session
        )

    await db_session.refresh(post)
    assert post.status == "published"
    assert post.instagram_post_id == "ig_post_2"
    assert post.facebook_error is not None


async def test_publish_both_platforms_fail(db_session, db_event, mock_config, httpx_mock):
    """IG + FB en erreur → status='error'. [TESTING.md]"""
    from tests.conftest import seed_page_token, seed_token
    from ancnouv.publisher import publish_to_all_platforms
    from ancnouv.publisher.facebook import FacebookPublisher
    from ancnouv.publisher.instagram import InstagramPublisher
    from ancnouv.publisher.token_manager import TokenManager

    await seed_token(db_session)
    await seed_page_token(db_session)

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media",
        json={"error": {"code": 2, "message": "IG error"}},
    )
    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.facebook.page_id}/photos",
        json={"error": {"code": 2, "message": "FB error"}},
    )

    post = Post(
        event_id=db_event.id,
        caption="Test",
        image_path="/tmp/test.jpg",
        status="approved",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    ig_pub = InstagramPublisher(mock_config.instagram.user_id, token_mgr)
    fb_pub = FacebookPublisher(mock_config.facebook.page_id, token_mgr)

    await publish_to_all_platforms(
        post, "https://example.com/img.jpg", ig_pub, fb_pub, db_session
    )

    await db_session.refresh(post)
    assert post.status == "error"
    assert post.error_message is not None


# ─── generate_post ───────────────────────────────────────────────────────────────

async def test_generate_post_creates_post(db_engine, db_session, mock_config):
    """generate_post retourne Post pending_approval. [TESTING.md]"""
    from datetime import date
    from ancnouv.db.utils import compute_content_hash
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)

    # Événement avec la date du jour pour que select_event le trouve
    today = date.today()
    ev = Event(
        source="wikipedia",
        source_lang="fr",
        event_type="event",
        month=today.month,
        day=today.day,
        year=1871,
        description="Événement du jour.",
        content_hash=compute_content_hash("Événement du jour."),
        status="available",
        published_count=0,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(ev)
    await db_session.commit()
    await db_session.refresh(ev)

    with (
        patch("ancnouv.generator.image.generate_image"),
        patch("ancnouv.generator.image.fetch_thumbnail", new=AsyncMock(return_value=None)),
    ):
        from ancnouv.generator import generate_post
        post = await generate_post(db_session, mock_config)

    assert post is not None
    assert post.status == "pending_approval"
    assert post.event_id == ev.id
    assert post.caption
    assert post.image_path


async def test_generate_post_no_stock(db_engine, db_session, mock_config):
    """select_event retourne None → generate_post retourne None. [TESTING.md]"""
    from sqlalchemy import select, func
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)

    from ancnouv.generator import generate_post
    post = await generate_post(db_session, mock_config)

    assert post is None
    result = await db_session.execute(select(func.count(Post.id)))
    assert result.scalar() == 0


async def test_generate_post_max_pending_guard(db_engine, db_session, db_event, mock_config):
    """max_pending_posts atteint → generate_post retourne None. [RF-3.2.7, T-08]"""
    from ancnouv.scheduler.context import set_config

    mock_config.scheduler.max_pending_posts = 1
    set_config(mock_config)

    # Insérer un post pending_approval
    pending = Post(
        event_id=db_event.id,
        caption="Existant",
        image_path="/tmp/x.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(pending)
    await db_session.commit()

    from ancnouv.generator import generate_post
    result = await generate_post(db_session, mock_config)
    assert result is None


async def test_generate_post_mode_b_selects_rss(db_engine, db_session, db_article, mock_config):
    """mix_ratio élevé + article dispo → post avec article_id. [TESTING.md, DS-3]"""
    from ancnouv.scheduler.context import set_config

    mock_config.content.rss.enabled = True
    mock_config.content.mix_ratio = 0.99
    set_config(mock_config)

    with (
        patch("ancnouv.generator.image.generate_image"),
        patch("ancnouv.generator.image.fetch_thumbnail", new=AsyncMock(return_value=None)),
        patch("random.random", return_value=0.01),  # 0.01 < 0.99 → Mode B
    ):
        from ancnouv.generator import generate_post
        post = await generate_post(db_session, mock_config)

    assert post is not None
    assert post.article_id == db_article.id
    assert post.event_id is None


async def test_generate_post_hybrid_fallback(db_engine, db_session, mock_config):
    """Mode B décidé mais aucun article → fallback Mode A. [DS-3]"""
    from datetime import date
    from ancnouv.db.utils import compute_content_hash
    from ancnouv.scheduler.context import set_config

    mock_config.content.rss.enabled = True
    mock_config.content.mix_ratio = 0.99
    set_config(mock_config)

    # Événement du jour pour que le fallback Mode A le trouve
    today = date.today()
    ev = Event(
        source="wikipedia",
        source_lang="fr",
        event_type="event",
        month=today.month,
        day=today.day,
        year=1900,
        description="Fallback event.",
        content_hash=compute_content_hash("Fallback event."),
        status="available",
        published_count=0,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(ev)
    await db_session.commit()
    await db_session.refresh(ev)

    with (
        patch("ancnouv.generator.image.generate_image"),
        patch("ancnouv.generator.image.fetch_thumbnail", new=AsyncMock(return_value=None)),
        patch("random.random", return_value=0.01),
    ):
        from ancnouv.generator import generate_post
        post = await generate_post(db_session, mock_config)

    assert post is not None
    assert post.event_id == ev.id


async def test_generate_post_date_window(db_engine, db_session, mock_config):
    """date_window_days=2 → événement J-2 sélectionnable. [content.date_window_days]"""
    from datetime import date, timedelta
    from ancnouv.db.utils import compute_content_hash
    from ancnouv.scheduler.context import set_config

    mock_config.content.date_window_days = 2
    set_config(mock_config)

    # Événement situé 2 jours avant aujourd'hui — hors de la fenêtre par défaut (0)
    j_minus_2 = date.today() - timedelta(days=2)
    ev = Event(
        source="wikipedia",
        source_lang="fr",
        event_type="event",
        month=j_minus_2.month,
        day=j_minus_2.day,
        year=1900,
        description="Événement J-2.",
        content_hash=compute_content_hash("Événement J-2."),
        status="available",
        published_count=0,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(ev)
    await db_session.commit()
    await db_session.refresh(ev)

    with (
        patch("ancnouv.generator.image.generate_image"),
        patch("ancnouv.generator.image.fetch_thumbnail", new=AsyncMock(return_value=None)),
    ):
        from ancnouv.generator import generate_post
        post = await generate_post(db_session, mock_config)

    assert post is not None
    assert post.event_id == ev.id


# ─── Compteur journalier — race condition ────────────────────────────────────────

async def test_daily_counter_race_condition(tmp_path):
    """BEGIN EXCLUSIVE garantit l'atomicité : exactement [False, True]. [TESTING.md]

    Utilise un fichier SQLite réel (pas StaticPool) pour que les deux connexions
    concurrentes soient indépendantes — BEGIN EXCLUSIVE bloque la seconde jusqu'au
    COMMIT de la première, ce qui est impossible avec une connexion partagée StaticPool.
    """
    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from ancnouv.db.models import Base
    from ancnouv.scheduler.jobs import check_and_increment_daily_count

    db_file = tmp_path / "test_race.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"timeout": 15, "check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_pragmas(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode = WAL")
        dbapi_conn.execute("PRAGMA busy_timeout = 10000")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS scheduler_state "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))

    today_str = datetime.now(timezone.utc).date().isoformat()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Seed : compteur à 24/25
    async with factory() as session:
        upsert = (
            "INSERT INTO scheduler_state (key, value, updated_at) "
            "VALUES (:key, :val, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP"
        )
        await session.execute(text(upsert), {"key": "daily_post_count", "val": "24"})
        await session.execute(text(upsert), {"key": "daily_post_count_date", "val": today_str})
        await session.commit()

    results = await asyncio.gather(
        check_and_increment_daily_count(engine, 25),
        check_and_increment_daily_count(engine, 25),
    )
    await engine.dispose()
    assert sorted(results) == [False, True]
