# Tests d'intégration — logique des jobs scheduler [docs/SCHEDULER.md]
# [TESTING.md — T-04, T-08]
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from ancnouv.db.models import Post


async def test_job_check_expired_marks_expired(db_session, db_event, mock_config):
    """Post créé il y a 49h → status='expired' après job_check_expired. [T-04, RF-3.3.3]"""
    from ancnouv.scheduler.context import set_config

    mock_config.scheduler.approval_timeout_hours = 48
    set_config(mock_config)

    created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=49)
    post = Post(
        event_id=db_event.id,
        caption="Post expiré",
        image_path="/tmp/expired.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
        created_at=created_at,
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()
    mock_bot_app.bot.edit_message_reply_markup = AsyncMock()

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", new=AsyncMock()),
    ):
        from ancnouv.scheduler.jobs import _job_check_expired_inner
        await _job_check_expired_inner()

    await db_session.refresh(post)
    assert post.status == "expired"


async def test_job_check_expired_does_not_expire_recent(db_session, db_event, mock_config):
    """Post créé il y a 1h → reste pending_approval. [T-04]"""
    from ancnouv.scheduler.context import set_config

    mock_config.scheduler.approval_timeout_hours = 48
    set_config(mock_config)

    created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    post = Post(
        event_id=db_event.id,
        caption="Post récent",
        image_path="/tmp/recent.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
        created_at=created_at,
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", new=AsyncMock()),
    ):
        from ancnouv.scheduler.jobs import _job_check_expired_inner
        await _job_check_expired_inner()

    await db_session.refresh(post)
    assert post.status == "pending_approval"


async def test_job_check_expired_event_stays_available(db_session, db_event, mock_config):
    """Expiration post → event.status reste 'available'. [T-04, RF-3.3.3]"""
    from ancnouv.scheduler.context import set_config

    mock_config.scheduler.approval_timeout_hours = 48
    set_config(mock_config)

    created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=49)
    post = Post(
        event_id=db_event.id,
        caption="Test",
        image_path="/tmp/test.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
        created_at=created_at,
    )
    db_session.add(post)
    await db_session.commit()

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()
    mock_bot_app.bot.edit_message_reply_markup = AsyncMock()

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", new=AsyncMock()),
    ):
        from ancnouv.scheduler.jobs import _job_check_expired_inner
        await _job_check_expired_inner()

    await db_session.refresh(db_event)
    assert db_event.status == "available"


async def test_job_check_token_sends_alert(db_session, mock_config):
    """Token avec 7j restants, last_alert=None → notify_all appelé. [TESTING.md]"""
    from ancnouv.db.models import MetaToken
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)

    token = MetaToken(
        token_kind="user_long",
        access_token="token_7j",
        expires_at=(
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(days=7, hours=1)
        ),
        last_alert_days_threshold=None,
    )
    db_session.add(token)
    await db_session.commit()

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()

    notified_messages = []

    async def capture_notify(bot, cfg, msg):
        notified_messages.append(msg)

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", side_effect=capture_notify),
        patch("ancnouv.publisher.token_manager.TokenManager.get_valid_token", new=AsyncMock(return_value="token")),
    ):
        from ancnouv.scheduler.jobs import _job_check_token_inner
        await _job_check_token_inner()

    assert any("7" in msg for msg in notified_messages)


async def test_job_check_token_no_duplicate_alert(db_session, mock_config):
    """last_alert_days_threshold=7 déjà renseigné → pas de notification. [TRANSVERSAL-4]"""
    from ancnouv.db.models import MetaToken
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)

    token = MetaToken(
        token_kind="user_long",
        access_token="token_dedup",
        expires_at=(
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(days=7, hours=1)
        ),
        last_alert_days_threshold=7,  # Déjà alerté à J-7
    )
    db_session.add(token)
    await db_session.commit()

    mock_bot_app = MagicMock()
    notified_count = [0]

    async def count_notify(bot, cfg, msg):
        notified_count[0] += 1

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", side_effect=count_notify),
    ):
        from ancnouv.scheduler.jobs import _job_check_token_inner
        await _job_check_token_inner()

    assert notified_count[0] == 0


async def test_job_generate_skips_when_max_pending(db_session, db_event, mock_config):
    """max_pending_posts posts en attente → generate_post non appelé. [T-08]"""
    from ancnouv.scheduler.context import set_config

    mock_config.scheduler.max_pending_posts = 1
    set_config(mock_config)

    pending = Post(
        event_id=db_event.id,
        caption="En attente",
        image_path="/tmp/x.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(pending)
    await db_session.commit()

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.scheduler.context.get_engine"),
        patch("ancnouv.generator.generate_post", new=AsyncMock(return_value=None)) as mock_gen,
        patch("ancnouv.scheduler.jobs.is_paused", new=AsyncMock(return_value=False)),
    ):
        from ancnouv.scheduler.jobs import _job_generate_inner
        await _job_generate_inner()

    # generate_post ne doit pas être appelé (guard pending >= max)
    mock_gen.assert_not_called()


# ─── pause_scheduler / resume_scheduler / is_paused ─────────────────────────────

async def test_pause_scheduler(db_session, mock_config):
    """pause_scheduler → scheduler_state.paused='true'. [SPEC-3.5.4]"""
    from sqlalchemy import text

    from ancnouv.scheduler.jobs import pause_scheduler

    await pause_scheduler(db_session)
    await db_session.commit()

    result = await db_session.execute(text("SELECT value FROM scheduler_state WHERE key='paused'"))
    row = result.fetchone()
    assert row and row[0] == "true"


async def test_resume_scheduler(db_session, mock_config):
    """resume_scheduler → scheduler_state.paused='false'. [SPEC-3.5.4]"""
    from sqlalchemy import text

    from ancnouv.scheduler.jobs import pause_scheduler, resume_scheduler

    await pause_scheduler(db_session)
    await resume_scheduler(db_session)
    await db_session.commit()

    result = await db_session.execute(text("SELECT value FROM scheduler_state WHERE key='paused'"))
    row = result.fetchone()
    assert row and row[0] == "false"


async def test_is_paused_returns_true(db_session, mock_config):
    """is_paused → True quand paused='true'. [SPEC-3.5.4]"""
    from sqlalchemy import text

    from ancnouv.scheduler.jobs import is_paused

    await db_session.execute(text(
        "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) "
        "VALUES ('paused', 'true', CURRENT_TIMESTAMP)"
    ))
    await db_session.commit()

    assert await is_paused(db_session) is True


async def test_is_paused_returns_false(db_session, mock_config):
    """is_paused → False quand paused='false'. [SPEC-3.5.4]"""
    from sqlalchemy import text

    from ancnouv.scheduler.jobs import is_paused

    await db_session.execute(text(
        "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) "
        "VALUES ('paused', 'false', CURRENT_TIMESTAMP)"
    ))
    await db_session.commit()

    assert await is_paused(db_session) is False


# ─── job_fetch_rss ────────────────────────────────────────────────────────────────

async def test_job_fetch_rss_stores_articles(db_session, mock_config):
    """job_fetch_rss avec 1 article valide → stocké en DB sans exception. [SCHEDULER.md JOB-2]"""
    import time
    from unittest.mock import patch

    from ancnouv.scheduler.context import set_config

    mock_config.content.rss.enabled = True
    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://example.com/feed.rss", "name": "Test"})()
    ]
    mock_config.content.rss.max_age_days = 180
    mock_config.content.rss.min_delay_days = 0
    set_config(mock_config)

    recent = time.strptime("2026-01-01", "%Y-%m-%d")
    entries = [{
        "title": "Article RSS",
        "link": "https://example.com/rss/job-test/1",
        "summary": "Résumé RSS",
        "published_parsed": recent,
    }]

    with patch("feedparser.parse", return_value={"entries": entries, "bozo": False}):
        from ancnouv.scheduler.jobs import job_fetch_rss
        await job_fetch_rss()


# ─── _job_generate_inner (chemins supplémentaires) ────────────────────────────────

async def test_job_generate_inner_paused(db_session, mock_config):
    """is_paused=True → generate_post non appelé. [SPEC-3.5.4]"""
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.scheduler.context.get_engine"),
        patch("ancnouv.scheduler.jobs.is_paused", new=AsyncMock(return_value=True)),
        patch("ancnouv.generator.generate_post", new=AsyncMock(return_value=None)) as mock_gen,
    ):
        from ancnouv.scheduler.jobs import _job_generate_inner
        await _job_generate_inner()

    mock_gen.assert_not_called()


async def test_job_generate_inner_no_event(db_session, mock_config):
    """generate_post retourne None → notify_all appelé avec 'Aucun'. [SCHEDULER.md JOB-3]"""
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)
    mock_config.scheduler.auto_publish = False

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()

    notified = []

    async def capture_notify(bot, cfg, msg):
        notified.append(msg)

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.scheduler.context.get_engine"),
        patch("ancnouv.scheduler.jobs.is_paused", new=AsyncMock(return_value=False)),
        patch("ancnouv.generator.generate_post", new=AsyncMock(return_value=None)),
        patch("ancnouv.bot.notifications.notify_all", side_effect=capture_notify),
    ):
        from ancnouv.scheduler.jobs import _job_generate_inner
        await _job_generate_inner()

    assert any("Aucun" in m for m in notified)


async def test_job_generate_inner_sends_approval(db_session, mock_config):
    """generate_post retourne post → send_approval_request appelé. [SCHEDULER.md JOB-3]"""
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)
    mock_config.scheduler.auto_publish = False

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()

    mock_post = MagicMock()
    mock_post.id = 1

    send_calls = []

    async def mock_send(*args, **kwargs):
        send_calls.append(True)

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.scheduler.context.get_engine"),
        patch("ancnouv.scheduler.jobs.is_paused", new=AsyncMock(return_value=False)),
        patch("ancnouv.generator.generate_post", new=AsyncMock(return_value=mock_post)),
        patch("ancnouv.bot.notifications.send_approval_request", side_effect=mock_send),
    ):
        from ancnouv.scheduler.jobs import _job_generate_inner
        await _job_generate_inner()

    assert len(send_calls) == 1


# ─── Wrappers externes job_* (gestion des exceptions) ────────────────────────────

async def test_job_generate_catches_exception(mock_config):
    """job_generate attrape les exceptions de _job_generate_inner. [SCHEDULER.md JOB-3]"""
    with patch("ancnouv.scheduler.jobs._job_generate_inner", new=AsyncMock(side_effect=ValueError("test"))):
        from ancnouv.scheduler.jobs import job_generate
        await job_generate()  # Ne doit pas lever d'exception


async def test_job_check_expired_catches_exception(mock_config):
    """job_check_expired attrape les exceptions de _job_check_expired_inner. [SCHEDULER.md JOB-4]"""
    with patch("ancnouv.scheduler.jobs._job_check_expired_inner", new=AsyncMock(side_effect=ValueError("test"))):
        from ancnouv.scheduler.jobs import job_check_expired
        await job_check_expired()


async def test_job_check_token_catches_exception(mock_config):
    """job_check_token attrape les exceptions de _job_check_token_inner. [SCHEDULER.md JOB-5]"""
    with patch("ancnouv.scheduler.jobs._job_check_token_inner", new=AsyncMock(side_effect=ValueError("test"))):
        from ancnouv.scheduler.jobs import job_check_token
        await job_check_token()


async def test_job_cleanup_catches_exception(mock_config):
    """job_cleanup attrape les exceptions de _job_cleanup_inner. [SCHEDULER.md JOB-6]"""
    with patch("ancnouv.scheduler.jobs._job_cleanup_inner", new=AsyncMock(side_effect=ValueError("test"))):
        from ancnouv.scheduler.jobs import job_cleanup
        await job_cleanup()


# ─── job_cleanup ──────────────────────────────────────────────────────────────────

async def test_job_cleanup_sets_image_path_null(db_session, db_event, mock_config, tmp_path):
    """Image ancienne → fichier supprimé et image_path=NULL. [SCHEDULER.md JOB-6]"""
    from datetime import timedelta

    from ancnouv.db.models import Post
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)
    mock_config.content.image_retention_days = 7

    img_file = tmp_path / "old_post.jpg"
    img_file.write_bytes(b"fake jpeg data")

    old_date = datetime.now(timezone.utc) - timedelta(days=10)
    post = Post(
        event_id=db_event.id,
        caption="Old post",
        image_path=str(img_file),
        status="published",
        telegram_message_ids="{}",
        created_at=old_date.replace(tzinfo=None),
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    with patch("ancnouv.scheduler.context.get_config", return_value=mock_config):
        from ancnouv.scheduler.jobs import _job_cleanup_inner
        await _job_cleanup_inner()

    await db_session.refresh(post)
    assert post.image_path is None
    assert not img_file.exists()


async def test_job_cleanup_file_not_found(db_session, db_event, mock_config):
    """Fichier déjà absent → image_path=NULL sans exception. [SCHEDULER.md JOB-6]"""
    from datetime import timedelta

    from ancnouv.db.models import Post
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)
    mock_config.content.image_retention_days = 7

    old_date = datetime.now(timezone.utc) - timedelta(days=10)
    post = Post(
        event_id=db_event.id,
        caption="Post sans fichier",
        image_path="/nonexistent/path/image_cleanup.jpg",
        status="published",
        telegram_message_ids="{}",
        created_at=old_date.replace(tzinfo=None),
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    with patch("ancnouv.scheduler.context.get_config", return_value=mock_config):
        from ancnouv.scheduler.jobs import _job_cleanup_inner
        await _job_cleanup_inner()

    await db_session.refresh(post)
    assert post.image_path is None


# ─── check_and_increment_daily_count (cas supplémentaires) ───────────────────────

async def test_check_and_increment_suspended(db_engine, db_session):
    """publications_suspended='true' → retourne False. [SC-2]"""
    from sqlalchemy import text

    await db_session.execute(text(
        "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) "
        "VALUES ('publications_suspended', 'true', CURRENT_TIMESTAMP)"
    ))
    await db_session.commit()

    from ancnouv.scheduler.jobs import check_and_increment_daily_count
    result = await check_and_increment_daily_count(db_engine, max_daily_posts=25)
    assert result is False


async def test_check_and_increment_new_day(db_engine, db_session):
    """Nouveau jour → compteur réinitialisé à 1, retourne True. [SCHEDULER.md SC-2]"""
    from sqlalchemy import text

    await db_session.execute(text(
        "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) "
        "VALUES ('daily_post_count_date', '2020-01-01', CURRENT_TIMESTAMP)"
    ))
    await db_session.execute(text(
        "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) "
        "VALUES ('daily_post_count', '25', CURRENT_TIMESTAMP)"
    ))
    await db_session.commit()

    from ancnouv.scheduler.jobs import check_and_increment_daily_count
    result = await check_and_increment_daily_count(db_engine, max_daily_posts=25)
    assert result is True  # Nouveau jour → premier post toujours autorisé


# ─── Concurrence — fichier SQLite requis [TESTING.md — T-15] ─────────────────────

async def _make_file_engine(db_file):
    """Crée un engine SQLite fichier + scheduler_state. [TESTING.md — race_condition]"""
    from sqlalchemy import event as sa_event, text
    from sqlalchemy.ext.asyncio import create_async_engine
    from ancnouv.db.models import Base

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"timeout": 15, "check_same_thread": False},
    )

    @sa_event.listens_for(engine.sync_engine, "connect")
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
    return engine


async def test_daily_counter_race_condition(tmp_path):
    """Deux appels simultanés, max=1 → exactement un True, un False (BEGIN EXCLUSIVE). [TESTING.md]

    Utilise un fichier SQLite (pas :memory:) pour que les deux engine.connect()
    obtiennent des connexions indépendantes à la même base — necessary pour que
    BEGIN EXCLUSIVE fonctionne comme verrou inter-connexions. [SC-1, SCHEDULER.md]
    """
    import asyncio
    from ancnouv.scheduler.jobs import check_and_increment_daily_count

    engine = await _make_file_engine(tmp_path / "race.db")
    try:
        results = await asyncio.gather(
            check_and_increment_daily_count(engine, 1),
            check_and_increment_daily_count(engine, 1),
        )
        assert sorted(results) == [False, True]
    finally:
        await engine.dispose()


async def test_daily_counter_exclusivity(tmp_path):
    """Deux appels simultanés, max=10 → compteur exactement à 2 (pas de doublon). [TESTING.md]"""
    import asyncio
    from sqlalchemy import text
    from ancnouv.scheduler.jobs import check_and_increment_daily_count

    engine = await _make_file_engine(tmp_path / "exclusivity.db")
    try:
        results = await asyncio.gather(
            check_and_increment_daily_count(engine, 10),
            check_and_increment_daily_count(engine, 10),
        )
        assert all(results)  # Les deux réussissent avec max=10

        # Compteur exactement à 2 — pas de perte ni de doublon [SC-1]
        async with engine.connect() as conn:
            row = await conn.execute(
                text("SELECT value FROM scheduler_state WHERE key = 'daily_post_count'")
            )
            count = int(row.scalar())
        assert count == 2
    finally:
        await engine.dispose()


# ─── _job_check_token_inner (chemins supplémentaires) ────────────────────────────

async def test_job_check_token_no_token_in_db(db_session, mock_config):
    """Aucun token en DB → retour silencieux, notify_all non appelé. [SCHEDULER.md JOB-5]"""
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)
    mock_bot_app = MagicMock()

    notified = [False]

    async def check_notify(*args):
        notified[0] = True

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", side_effect=check_notify),
    ):
        from ancnouv.scheduler.jobs import _job_check_token_inner
        await _job_check_token_inner()

    assert notified[0] is False


async def test_job_check_token_threshold_none(db_session, mock_config):
    """Token avec 35j restants → threshold=None, notify_all non appelé. [SCHEDULER.md JOB-5]"""
    from ancnouv.db.models import MetaToken
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)

    token = MetaToken(
        token_kind="user_long",
        access_token="token_35j",
        expires_at=(
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(days=35)
        ),
        last_alert_days_threshold=None,
    )
    db_session.add(token)
    await db_session.commit()

    mock_bot_app = MagicMock()
    notified = [False]

    async def check_notify(*args):
        notified[0] = True

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", side_effect=check_notify),
    ):
        from ancnouv.scheduler.jobs import _job_check_token_inner
        await _job_check_token_inner()

    assert notified[0] is False


async def test_job_check_token_expires_at_none(db_session, mock_config):
    """Token sans expires_at → retour silencieux. [SCHEDULER.md JOB-5]"""
    from ancnouv.db.models import MetaToken
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)

    token = MetaToken(
        token_kind="user_long",
        access_token="tok_no_exp",
        expires_at=None,
    )
    db_session.add(token)
    await db_session.commit()

    mock_bot_app = MagicMock()
    notified = [False]

    async def check_notify(*args):
        notified[0] = True

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", side_effect=check_notify),
    ):
        from ancnouv.scheduler.jobs import _job_check_token_inner
        await _job_check_token_inner()

    assert notified[0] is False


async def test_job_check_token_refresh_fails(db_session, mock_config):
    """Token 7j + refresh échoue → notify avec '⚠️ Refresh échoué'. [SCHEDULER.md JOB-5]"""
    from ancnouv.db.models import MetaToken
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)

    token = MetaToken(
        token_kind="user_long",
        access_token="token_7j_fail",
        expires_at=(
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(days=7, hours=1)
        ),
        last_alert_days_threshold=None,
    )
    db_session.add(token)
    await db_session.commit()

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()

    notified_messages = []

    async def capture_notify(bot, cfg, msg):
        notified_messages.append(msg)

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", side_effect=capture_notify),
        patch("ancnouv.publisher.token_manager.TokenManager.get_valid_token",
              new=AsyncMock(side_effect=Exception("Token refresh failed"))),
    ):
        from ancnouv.scheduler.jobs import _job_check_token_inner
        await _job_check_token_inner()

    assert any("Refresh échoué" in m for m in notified_messages)


async def test_job_check_token_14j_message(db_session, mock_config):
    """Token 14j → message ℹ️ sans refresh. [SCHEDULER.md JOB-5]"""
    from ancnouv.db.models import MetaToken
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)

    token = MetaToken(
        token_kind="user_long",
        access_token="token_14j",
        expires_at=(
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(days=14, hours=1)
        ),
        last_alert_days_threshold=None,
    )
    db_session.add(token)
    await db_session.commit()

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()

    notified_messages = []

    async def capture_notify(bot, cfg, msg):
        notified_messages.append(msg)

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", side_effect=capture_notify),
    ):
        from ancnouv.scheduler.jobs import _job_check_token_inner
        await _job_check_token_inner()

    assert any("14" in m for m in notified_messages)


# ─── job_check_expired avec telegram_message_ids non vides ───────────────────────

async def test_job_check_expired_disables_buttons(db_session, db_event, mock_config):
    """Post expiré avec message_ids → edit_message_reply_markup appelé. [SCHEDULER.md JOB-4]"""
    from ancnouv.db.models import Post
    from ancnouv.scheduler.context import set_config

    mock_config.scheduler.approval_timeout_hours = 48
    set_config(mock_config)

    created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=49)
    post = Post(
        event_id=db_event.id,
        caption="Post expiré avec boutons",
        image_path="/tmp/expired_btn.jpg",
        status="pending_approval",
        telegram_message_ids='{"123456789": 42}',
        created_at=created_at,
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()
    mock_bot_app.bot.edit_message_reply_markup = AsyncMock()

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.bot.notifications.notify_all", new=AsyncMock()),
    ):
        from ancnouv.scheduler.jobs import _job_check_expired_inner
        await _job_check_expired_inner()

    mock_bot_app.bot.edit_message_reply_markup.assert_called()


# ─── _job_generate_inner (escalade niveau 5) ─────────────────────────────────────

async def test_job_generate_inner_escalation_level_5(db_session, mock_config):
    """escalation_level=5 → notify_all + retour sans generate_post. [DS-1.4b]"""
    from sqlalchemy import text

    from ancnouv.scheduler.context import set_config

    set_config(mock_config)

    await db_session.execute(text(
        "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) "
        "VALUES ('escalation_level', '5', CURRENT_TIMESTAMP)"
    ))
    await db_session.commit()

    mock_bot_app = MagicMock()
    mock_bot_app.bot = AsyncMock()

    notified = []

    async def capture_notify(bot, cfg, msg):
        notified.append(msg)

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.scheduler.context.get_bot_app", return_value=mock_bot_app),
        patch("ancnouv.scheduler.context.get_engine"),
        patch("ancnouv.scheduler.jobs.is_paused", new=AsyncMock(return_value=False)),
        patch("ancnouv.bot.notifications.notify_all", side_effect=capture_notify),
        patch("ancnouv.generator.generate_post", new=AsyncMock(return_value=None)) as mock_gen,
    ):
        from ancnouv.scheduler.jobs import _job_generate_inner
        await _job_generate_inner()

    mock_gen.assert_not_called()
    assert any("escalade 5" in m or "critique" in m.lower() for m in notified)


# ─── job_fetch_wiki ───────────────────────────────────────────────────────────────

async def test_job_fetch_wiki_basic(db_session, mock_config):
    """job_fetch_wiki : 1 jour, fetch retourne vide, pas d'escalade. [SCHEDULER.md JOB-1]"""
    from ancnouv.scheduler.context import set_config

    set_config(mock_config)
    mock_config.content.prefetch_days = 1

    with (
        patch("ancnouv.scheduler.context.get_config", return_value=mock_config),
        patch("ancnouv.generator.selector.get_effective_query_params", new=AsyncMock(return_value=MagicMock())),
        patch("ancnouv.fetchers.wikipedia.WikipediaFetcher.fetch", new=AsyncMock(return_value=[])),
        patch("ancnouv.generator.selector.needs_escalation", new=AsyncMock(return_value=False)),
    ):
        from ancnouv.scheduler.jobs import job_fetch_wiki
        await job_fetch_wiki()
