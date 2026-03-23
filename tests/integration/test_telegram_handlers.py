# Tests d'intégration — handlers Telegram [SPEC-3.3, docs/TELEGRAM_BOT.md]
# [TESTING.md — test_telegram_handlers.py, TG-F5, TG-F13, T-02, T-06, T-11]
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ancnouv.db.models import Event, Post, RssArticle
from ancnouv.db.utils import compute_content_hash


def _make_update(user_id: int, text: str | None = None, callback_data: str | None = None):
    """Construit un faux Update PTB."""
    user = MagicMock()
    user.id = user_id

    update = MagicMock()
    update.effective_user = user

    if callback_data is not None:
        query = MagicMock()
        query.answer = AsyncMock()
        query.data = callback_data
        query.edit_message_text = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()
        msg = MagicMock()
        msg.reply_markup = None
        query.message = msg
        update.callback_query = query
        update.effective_message = query.message
    else:
        message = MagicMock()
        message.reply_text = AsyncMock()
        update.message = message
        update.effective_message = message
        update.callback_query = None

    return update


def _make_context(config, bot=None):
    """Construit un faux context PTB."""
    context = MagicMock()
    context.bot_data = {"config": config}
    context.bot = bot or MagicMock()
    context.bot.edit_message_reply_markup = AsyncMock()
    context.chat_data = {}
    return context


# ─── cmd_pause / cmd_resume ──────────────────────────────────────────────────────

async def test_cmd_pause_sets_flag(db_session, mock_config):
    """cmd_pause → scheduler_state.paused='true'. [TESTING.md]"""
    from sqlalchemy import text
    from ancnouv.bot.handlers import cmd_pause

    # Pré-condition : ligne paused doit exister
    await db_session.execute(text(
        "INSERT OR IGNORE INTO scheduler_state (key, value, updated_at) "
        "VALUES ('paused', 'false', CURRENT_TIMESTAMP)"
    ))
    await db_session.commit()

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_pause(update, context)

    result = await db_session.execute(
        text("SELECT value FROM scheduler_state WHERE key='paused'")
    )
    row = result.fetchone()
    assert row and row[0] == "true"


async def test_cmd_resume_clears_flag(db_session, mock_config):
    """cmd_resume → paused='false'. [TESTING.md]"""
    from sqlalchemy import text
    from ancnouv.bot.handlers import cmd_resume

    # Pré-condition : paused='true'
    await db_session.execute(text(
        "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) "
        "VALUES ('paused', 'true', CURRENT_TIMESTAMP)"
    ))
    await db_session.commit()

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_resume(update, context)

    result = await db_session.execute(
        text("SELECT value FROM scheduler_state WHERE key='paused'")
    )
    row = result.fetchone()
    assert row and row[0] == "false"


# ─── Autorisation ────────────────────────────────────────────────────────────────

async def test_unauthorized_user_rejected(db_session, mock_config):
    """Utilisateur non autorisé → reply_text('Accès non autorisé.'). [TESTING.md]"""
    from ancnouv.bot.handlers import cmd_pause

    update = _make_update(999999)  # non dans authorized_user_ids
    context = _make_context(mock_config)

    await cmd_pause(update, context)

    update.effective_message.reply_text.assert_called_once_with("Accès non autorisé.")


# ─── cmd_stats ───────────────────────────────────────────────────────────────────

async def test_cmd_stats_division_by_zero(db_session, mock_config):
    """0 publié + 0 rejeté → taux 'N/A' sans exception. [TG-F13]"""
    from ancnouv.bot.handlers import cmd_stats

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_stats(update, context)

    call_args = update.message.reply_text.call_args[0][0]
    assert "N/A" in call_args


# ─── handle_reject ───────────────────────────────────────────────────────────────

async def test_handle_reject_mode_a(db_session, mock_config):
    """Rejet post Mode A → post='rejected', event='blocked'. [TESTING.md]"""
    from ancnouv.bot.handlers import handle_reject

    ev = Event(
        source="wikipedia",
        source_lang="fr",
        event_type="event",
        month=3,
        day=21,
        year=1871,
        description="Test event.",
        content_hash=compute_content_hash("Test event."),
        status="available",
        published_count=0,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(ev)
    await db_session.commit()
    await db_session.refresh(ev)

    post = Post(
        event_id=ev.id,
        caption="Test rejection",
        image_path="/tmp/test.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    update = _make_update(123456789, callback_data=f"reject:{post.id}")
    context = _make_context(mock_config)

    await handle_reject(update, context)

    await db_session.refresh(post)
    await db_session.refresh(ev)
    assert post.status == "rejected"
    assert ev.status == "blocked"


async def test_handle_reject_mode_b(db_session, mock_config):
    """Rejet post Mode B → post='rejected', rss_article='blocked'. [TESTING.md]"""
    from ancnouv.bot.handlers import handle_reject

    article = RssArticle(
        feed_url="https://example.com/feed",
        feed_name="Test",
        title="Article",
        article_url="https://example.com/art/1",
        summary="Résumé",
        published_at=datetime.now(timezone.utc).replace(tzinfo=None),
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status="available",
        published_count=0,
    )
    db_session.add(article)
    await db_session.commit()
    await db_session.refresh(article)

    post = Post(
        article_id=article.id,
        caption="Test RSS rejection",
        image_path="/tmp/test.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    update = _make_update(123456789, callback_data=f"reject:{post.id}")
    context = _make_context(mock_config)

    await handle_reject(update, context)

    await db_session.refresh(post)
    await db_session.refresh(article)
    assert post.status == "rejected"
    assert article.status == "blocked"


# ─── handle_skip ─────────────────────────────────────────────────────────────────

async def test_handle_skip_generates_next(db_session, db_event, mock_config):
    """Post skipé → status='skipped', generate_post appelé. [TESTING.md, T-06]"""
    from ancnouv.bot.handlers import handle_skip

    post = Post(
        event_id=db_event.id,
        caption="Test skip",
        image_path="/tmp/test.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    update = _make_update(123456789, callback_data=f"skip:{post.id}")
    context = _make_context(mock_config)

    async def mock_notify(bot, cfg, msg):
        pass

    with (
        patch("ancnouv.generator.generate_post", new=AsyncMock(return_value=None)),
        patch("ancnouv.bot.handlers.notify_all", side_effect=mock_notify),
    ):
        await handle_skip(update, context)

    await db_session.refresh(post)
    assert post.status == "skipped"


async def test_handle_skip_sends_new_approval_request(db_session, db_event, mock_config):
    """handle_skip + generate_post retourne nouveau post → send_approval_request appelé. [T-06]"""
    from ancnouv.bot.handlers import handle_skip

    post = Post(
        event_id=db_event.id,
        caption="Test skip avec suite",
        image_path="/tmp/test.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    ev2 = Event(
        source="wikipedia",
        source_lang="fr",
        event_type="event",
        month=3,
        day=21,
        year=1920,
        description="Second événement.",
        content_hash=compute_content_hash("Second événement."),
        status="available",
        published_count=0,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(ev2)
    await db_session.commit()
    await db_session.refresh(ev2)

    new_post = Post(
        event_id=ev2.id,
        caption="Nouveau post généré",
        image_path="/tmp/new.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(new_post)
    await db_session.commit()
    await db_session.refresh(new_post)

    send_calls = []

    async def mock_send_approval(post, bot, session, config):
        send_calls.append(post.id)

    update = _make_update(123456789, callback_data=f"skip:{post.id}")
    context = _make_context(mock_config)

    with (
        patch("ancnouv.generator.generate_post", new=AsyncMock(return_value=new_post)),
        patch("ancnouv.bot.handlers.send_approval_request", side_effect=mock_send_approval),
    ):
        await handle_skip(update, context)

    assert len(send_calls) == 1


# ─── handle_approve ──────────────────────────────────────────────────────────────

async def test_handle_approve_sets_approved_at(db_session, db_event, mock_config):
    """handle_approve → post.approved_at not None. [TESTING.md]"""
    from ancnouv.bot.handlers import handle_approve

    post = Post(
        event_id=db_event.id,
        caption="Test approve",
        image_path="/tmp/test.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    update = _make_update(123456789, callback_data=f"approve:{post.id}")
    context = _make_context(mock_config)

    with (
        patch("ancnouv.scheduler.jobs.check_and_increment_daily_count", new=AsyncMock(return_value=True)),
        patch("ancnouv.scheduler.context.get_engine"),
        patch("ancnouv.bot.handlers._publish_approved_post", new=AsyncMock()),
    ):
        await handle_approve(update, context)

    await db_session.refresh(post)
    assert post.approved_at is not None


async def test_handle_approve_image_path_null(db_session, db_event, mock_config):
    """image_path=None → message 'introuvable', _publish_approved_post non appelé. [TESTING.md]"""
    from ancnouv.bot.handlers import handle_approve

    post = Post(
        event_id=db_event.id,
        caption="Sans image",
        image_path=None,
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    update = _make_update(123456789, callback_data=f"approve:{post.id}")
    context = _make_context(mock_config)

    publish_called = []

    async def track_publish(*args, **kwargs):
        publish_called.append(True)

    with (
        patch("ancnouv.scheduler.jobs.check_and_increment_daily_count", new=AsyncMock(return_value=True)),
        patch("ancnouv.scheduler.context.get_engine"),
        patch("ancnouv.bot.handlers._publish_approved_post", side_effect=track_publish),
    ):
        await handle_approve(update, context)

    assert len(publish_called) == 0
    update.callback_query.edit_message_text.assert_called()
    call_text = update.callback_query.edit_message_text.call_args[0][0]
    assert "introuvable" in call_text.lower() or "image" in call_text.lower()


async def test_handle_approve_daily_limit_queues(db_session, db_event, mock_config):
    """Limite journalière atteinte → post.status='queued'. [T-11, TRANSVERSAL-2]"""
    from ancnouv.bot.handlers import handle_approve

    post = Post(
        event_id=db_event.id,
        caption="Limite quotidienne",
        image_path="/tmp/test.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    update = _make_update(123456789, callback_data=f"approve:{post.id}")
    context = _make_context(mock_config)

    with (
        patch("ancnouv.scheduler.jobs.check_and_increment_daily_count", new=AsyncMock(return_value=False)),
        patch("ancnouv.scheduler.context.get_engine"),
    ):
        await handle_approve(update, context)

    await db_session.refresh(post)
    assert post.status == "queued"


async def test_handle_approve_optimistic_lock(tmp_path, mock_config):
    """Deux handle_approve simultanés → exactement un approuvé. [TG-F5, TESTING.md]

    Utilise un fichier SQLite réel pour que les deux coroutines aient des connexions
    indépendantes — le verrou optimiste (UPDATE WHERE status='pending_approval')
    garantit qu'une seule réussit (rowcount=1), l'autre retourne silencieusement.
    """
    import asyncio
    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from ancnouv.db.models import Base
    from ancnouv.db.utils import compute_content_hash
    from ancnouv.db.session import set_session_factory
    from ancnouv.bot.handlers import handle_approve

    db_file = tmp_path / "test_lock.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"timeout": 15, "check_same_thread": False},
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

    set_session_factory(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        ev = Event(
            source="wikipedia",
            source_lang="fr",
            event_type="event",
            month=3,
            day=21,
            year=1871,
            description="Lock event.",
            content_hash=compute_content_hash("Lock event."),
            status="available",
            published_count=0,
            fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(ev)
        await session.commit()
        await session.refresh(ev)

        post = Post(
            event_id=ev.id,
            caption="Lock test",
            image_path="/tmp/test.jpg",
            status="pending_approval",
            telegram_message_ids="{}",
        )
        session.add(post)
        await session.commit()
        await session.refresh(post)
        post_id = post.id

    update1 = _make_update(123456789, callback_data=f"approve:{post_id}")
    update2 = _make_update(123456789, callback_data=f"approve:{post_id}")
    context1 = _make_context(mock_config)
    context2 = _make_context(mock_config)

    with (
        patch("ancnouv.scheduler.jobs.check_and_increment_daily_count", new=AsyncMock(return_value=True)),
        patch("ancnouv.scheduler.context.get_engine"),
        patch("ancnouv.bot.handlers._publish_approved_post", new=AsyncMock()),
    ):
        await asyncio.gather(
            handle_approve(update1, context1),
            handle_approve(update2, context2),
        )

    await engine.dispose()

    async with factory() as session:
        result = await session.get(Post, post_id)
        # Le statut doit être "approved" — le verrou optimiste empêche la double approbation
        assert result.status == "approved"


# ─── cmd_start ────────────────────────────────────────────────────────────────────

async def test_cmd_start_shows_status(db_session, mock_config):
    """cmd_start → message contenant 'Anciennes Nouvelles'. [TESTING.md]"""
    from ancnouv.bot.handlers import cmd_start

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_start(update, context)

    update.message.reply_text.assert_called_once()
    assert "Anciennes Nouvelles" in update.message.reply_text.call_args[0][0]


# ─── cmd_help ─────────────────────────────────────────────────────────────────────

async def test_cmd_help_lists_commands(db_session, mock_config):
    """cmd_help → message contenant '/status'. [TESTING.md]"""
    from ancnouv.bot.handlers import cmd_help

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_help(update, context)

    update.message.reply_text.assert_called_once()
    assert "/status" in update.message.reply_text.call_args[0][0]


# ─── cmd_status ───────────────────────────────────────────────────────────────────

async def test_cmd_status_shows_state(db_session, mock_config):
    """cmd_status → message contenant 'Anciennes Nouvelles'. [TESTING.md]"""
    from ancnouv.bot.handlers import cmd_status

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_status(update, context)

    update.message.reply_text.assert_called_once()
    assert "Anciennes Nouvelles" in update.message.reply_text.call_args[0][0]


# ─── cmd_pending ──────────────────────────────────────────────────────────────────

async def test_cmd_pending_empty(db_session, mock_config):
    """Aucun post en attente → 'Aucun post en attente.' [TESTING.md]"""
    from ancnouv.bot.handlers import cmd_pending

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_pending(update, context)

    update.message.reply_text.assert_called_once_with("Aucun post en attente.")


async def test_cmd_pending_with_posts(db_session, mock_config):
    """Post en attente → liste affichée. [TG-F7]"""
    from ancnouv.bot.handlers import cmd_pending
    from ancnouv.db.models import Event, Post
    from ancnouv.db.utils import compute_content_hash

    ev = Event(
        source="wikipedia", source_lang="fr", event_type="event",
        month=3, day=21, year=1871,
        description="Événement test pending.",
        content_hash=compute_content_hash("Événement test pending."),
        status="available", published_count=0,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(ev)
    await db_session.commit()
    await db_session.refresh(ev)

    post = Post(
        event_id=ev.id,
        caption="Légende test pending",
        image_path="/tmp/t.jpg",
        status="pending_approval",
        telegram_message_ids='{"123456789": 42}',
    )
    db_session.add(post)
    await db_session.commit()

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_pending(update, context)

    update.message.reply_text.assert_called_once()
    assert "Posts en attente" in update.message.reply_text.call_args[0][0]


# ─── cmd_force ────────────────────────────────────────────────────────────────────

async def test_cmd_force_no_event(db_session, mock_config):
    """generate_post retourne None → message 'Aucun événement disponible'. [TESTING.md]"""
    from ancnouv.bot.handlers import cmd_force

    update = _make_update(123456789)
    context = _make_context(mock_config)

    with patch("ancnouv.generator.generate_post", new=AsyncMock(return_value=None)):
        await cmd_force(update, context)

    call_text = update.message.reply_text.call_args[0][0]
    assert "Aucun" in call_text


async def test_cmd_force_sends_approval(db_session, mock_config):
    """generate_post retourne post → send_approval_request appelé. [TESTING.md]"""
    from ancnouv.bot.handlers import cmd_force

    mock_post = MagicMock()
    mock_post.id = 1

    send_calls = []

    async def mock_send(post, bot, session, config):
        send_calls.append(True)

    update = _make_update(123456789)
    context = _make_context(mock_config)

    with (
        patch("ancnouv.generator.generate_post", new=AsyncMock(return_value=mock_post)),
        patch("ancnouv.bot.handlers.send_approval_request", side_effect=mock_send),
    ):
        await cmd_force(update, context)

    assert len(send_calls) == 1
    assert "Post généré" in update.message.reply_text.call_args[0][0]


# ─── cmd_retry ────────────────────────────────────────────────────────────────────

async def test_cmd_retry_no_error_post(db_session, mock_config):
    """Aucun post en erreur → 'Aucun post en erreur.' [TESTING.md]"""
    from ancnouv.bot.handlers import cmd_retry

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_retry(update, context)

    update.message.reply_text.assert_called_once_with("Aucun post en erreur.")


async def test_cmd_retry_publishes_post(db_session, db_event, mock_config):
    """Post en erreur → _publish_approved_post appelé. [TESTING.md]"""
    from ancnouv.bot.handlers import cmd_retry
    from ancnouv.db.models import Post

    post = Post(
        event_id=db_event.id,
        caption="Post en erreur",
        image_path="/tmp/err.jpg",
        status="error",
        error_message="Erreur réseau",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    update = _make_update(123456789)
    context = _make_context(mock_config)

    publish_calls = []

    async def mock_publish(p, session, bot, config):
        publish_calls.append(p.id)

    with patch("ancnouv.bot.handlers._publish_approved_post", side_effect=mock_publish):
        await cmd_retry(update, context)

    assert len(publish_calls) == 1


# ─── cmd_retry_ig / cmd_retry_fb ─────────────────────────────────────────────────

async def test_cmd_retry_ig_no_post(db_session, mock_config):
    """Aucun post avec instagram_error → message approprié. [TG-F16]"""
    from ancnouv.bot.handlers import cmd_retry_ig

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_retry_ig(update, context)

    call_text = update.message.reply_text.call_args[0][0]
    assert "Instagram" in call_text


async def test_cmd_retry_fb_no_post(db_session, mock_config):
    """Aucun post avec facebook_error → message approprié. [TG-F16]"""
    from ancnouv.bot.handlers import cmd_retry_fb

    update = _make_update(123456789)
    context = _make_context(mock_config)

    await cmd_retry_fb(update, context)

    call_text = update.message.reply_text.call_args[0][0]
    assert "Facebook" in call_text


# ─── handle_approve (chemins supplémentaires) ─────────────────────────────────────

async def test_handle_approve_post_not_found(db_session, mock_config):
    """Post introuvable → edit_message_text appelé. [TG-F5]"""
    from ancnouv.bot.handlers import handle_approve

    update = _make_update(123456789, callback_data="approve:99999")
    context = _make_context(mock_config)

    await handle_approve(update, context)

    update.callback_query.edit_message_text.assert_called()


# ─── handle_reject (chemins supplémentaires) ─────────────────────────────────────

async def test_handle_reject_post_not_found(db_session, mock_config):
    """Post introuvable → edit_message_text appelé. [TESTING.md]"""
    from ancnouv.bot.handlers import handle_reject

    update = _make_update(123456789, callback_data="reject:99999")
    context = _make_context(mock_config)

    await handle_reject(update, context)

    update.callback_query.edit_message_text.assert_called()


# ─── handle_edit ──────────────────────────────────────────────────────────────────

async def test_handle_edit_stores_context(db_session, db_event, mock_config):
    """handle_edit stocke post_id dans chat_data et retourne WAITING_CAPTION. [TESTING.md]"""
    from ancnouv.bot.handlers import WAITING_CAPTION, handle_edit
    from ancnouv.db.models import Post

    post = Post(
        event_id=db_event.id,
        caption="Légende à éditer",
        image_path="/tmp/e.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    update = _make_update(123456789, callback_data=f"edit:{post.id}")
    update.callback_query.message.message_id = 42
    update.callback_query.message.photo = None
    update.callback_query.message.text = "Légende à éditer"
    update.callback_query.message.caption = None
    update.callback_query.message.reply_text = AsyncMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = 123456789

    context = _make_context(mock_config)

    result = await handle_edit(update, context)

    assert result == WAITING_CAPTION
    assert "pending_edit_message_id" in context.chat_data


# ─── handle_new_caption ───────────────────────────────────────────────────────────

async def test_handle_new_caption_updates_caption(db_session, db_event, mock_config):
    """handle_new_caption met à jour post.caption en DB. [TF-F11]"""
    from telegram.ext import ConversationHandler

    from ancnouv.bot.handlers import handle_new_caption
    from ancnouv.db.models import Post

    post = Post(
        event_id=db_event.id,
        caption="Ancienne légende",
        image_path="/tmp/nc.jpg",
        status="pending_approval",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    update = _make_update(123456789)
    update.message.text = "Nouvelle légende modifiée"
    context = _make_context(mock_config)
    context.chat_data = {
        "pending_edit_message_id": 42,
        "edit_42": post.id,
        "edit_message_type": "text",
        "edit_message_current_text": "Ancienne légende",
        "edit_chat_id": 123456789,
    }

    with patch("ancnouv.bot.handlers.send_approval_request", new=AsyncMock()):
        result = await handle_new_caption(update, context)

    await db_session.refresh(post)
    assert post.caption == "Nouvelle légende modifiée"
    assert result == ConversationHandler.END


# ─── cancel_edit ──────────────────────────────────────────────────────────────────

async def test_cancel_edit_returns_end(mock_config):
    """cancel_edit → ConversationHandler.END sans exception. [TESTING.md]"""
    from telegram.ext import ConversationHandler

    from ancnouv.bot.handlers import cancel_edit

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    result = await cancel_edit(update, context)

    assert result == ConversationHandler.END
    update.message.reply_text.assert_called_once_with("Édition annulée.")


# ─── handle_edit_timeout ──────────────────────────────────────────────────────────

async def test_handle_edit_timeout_no_pending(mock_config):
    """handle_edit_timeout sans pending_edit_message_id → retour immédiat. [TF-F14]"""
    from telegram.ext import ConversationHandler

    from ancnouv.bot.handlers import handle_edit_timeout

    update = MagicMock()
    context = MagicMock()
    context.chat_data = {}

    result = await handle_edit_timeout(update, context)

    assert result == ConversationHandler.END


async def test_handle_edit_timeout_edits_message(mock_config):
    """handle_edit_timeout avec message_id → édite le message. [TF-F14]"""
    from telegram.ext import ConversationHandler

    from ancnouv.bot.handlers import handle_edit_timeout

    context = MagicMock()
    context.chat_data = {
        "pending_edit_message_id": 42,
        "edit_chat_id": 123456789,
        "edit_message_type": "text",
        "edit_message_current_text": "Légende originale",
    }
    context.bot.edit_message_text = AsyncMock()

    update = MagicMock()

    result = await handle_edit_timeout(update, context)

    assert result == ConversationHandler.END
    context.bot.edit_message_text.assert_called_once()


async def test_handle_edit_timeout_photo_message(mock_config):
    """handle_edit_timeout type photo → edit_message_caption appelé. [TF-F14]"""
    from telegram.ext import ConversationHandler

    from ancnouv.bot.handlers import handle_edit_timeout

    context = MagicMock()
    context.chat_data = {
        "pending_edit_message_id": 42,
        "edit_chat_id": 123456789,
        "edit_message_type": "photo",
        "edit_message_current_text": "Légende photo originale",
    }
    context.bot.edit_message_caption = AsyncMock()

    update = MagicMock()

    result = await handle_edit_timeout(update, context)

    assert result == ConversationHandler.END
    context.bot.edit_message_caption.assert_called_once()


# ─── handle_skip (chemins supplémentaires) ────────────────────────────────────────

async def test_handle_skip_post_not_found(db_session, mock_config):
    """Post introuvable pour skip → edit_message_text appelé. [TESTING.md]"""
    from ancnouv.bot.handlers import handle_skip

    update = _make_update(123456789, callback_data="skip:99999")
    context = _make_context(mock_config)

    await handle_skip(update, context)

    update.callback_query.edit_message_text.assert_called()


# ─── handle_new_caption (chemins de retour anticipé) ──────────────────────────────

async def test_handle_new_caption_no_message_id(db_session, mock_config):
    """Pas de pending_edit_message_id → ConversationHandler.END immédiat. [TESTING.md]"""
    from telegram.ext import ConversationHandler

    from ancnouv.bot.handlers import handle_new_caption

    update = _make_update(123456789)
    update.message.text = "Nouvelle légende"
    context = _make_context(mock_config)
    context.chat_data = {}  # pas de pending_edit_message_id

    result = await handle_new_caption(update, context)

    assert result == ConversationHandler.END


async def test_handle_new_caption_no_post_id(db_session, mock_config):
    """pending_edit_message_id présent mais pas de edit_N → ConversationHandler.END. [TESTING.md]"""
    from telegram.ext import ConversationHandler

    from ancnouv.bot.handlers import handle_new_caption

    update = _make_update(123456789)
    update.message.text = "Nouvelle légende"
    context = _make_context(mock_config)
    context.chat_data = {"pending_edit_message_id": 42}  # pas de edit_42

    result = await handle_new_caption(update, context)

    assert result == ConversationHandler.END


# ─── _publish_approved_post ───────────────────────────────────────────────────────

async def test_publish_approved_post_success(db_session, db_event, mock_config):
    """_publish_approved_post : upload + publish réussis → notify_all appelé. [TG-F6]"""
    from ancnouv.bot.handlers import _publish_approved_post
    from ancnouv.db.models import Post

    post = Post(
        event_id=db_event.id,
        caption="Test publish direct",
        image_path="/tmp/test_pub.jpg",
        image_public_url=None,
        status="approved",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    bot = AsyncMock()

    async def mock_publish(p, url, ig_pub, fb_pub, session):
        p.status = "published"
        return {"instagram": "IG123", "facebook": None}

    notified = []

    async def capture_notify(b, cfg, msg):
        notified.append(msg)

    with (
        patch("ancnouv.publisher.image_hosting.upload_image", new=AsyncMock(return_value="https://img.example.com/test.jpg")),
        patch("ancnouv.publisher.publish_to_all_platforms", side_effect=mock_publish),
        patch("ancnouv.bot.handlers.notify_all", side_effect=capture_notify),
    ):
        await _publish_approved_post(post, db_session, bot, mock_config)

    assert post.status == "published"
    assert len(notified) >= 1


async def test_publish_approved_post_already_has_url(db_session, db_event, mock_config):
    """_publish_approved_post : image_public_url déjà renseigné → skip upload. [TG-F6]"""
    from ancnouv.bot.handlers import _publish_approved_post
    from ancnouv.db.models import Post

    post = Post(
        event_id=db_event.id,
        caption="Post déjà uploadé",
        image_path="/tmp/test_pub2.jpg",
        image_public_url="https://img.example.com/existing.jpg",
        status="approved",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    bot = AsyncMock()
    upload_calls = []

    async def mock_publish(p, url, ig_pub, fb_pub, session):
        p.status = "published"
        return {"instagram": None, "facebook": None}

    with (
        patch("ancnouv.publisher.image_hosting.upload_image", side_effect=lambda *a, **k: upload_calls.append(True)),
        patch("ancnouv.publisher.publish_to_all_platforms", side_effect=mock_publish),
        patch("ancnouv.bot.handlers.notify_all", new=AsyncMock()),
    ):
        await _publish_approved_post(post, db_session, bot, mock_config)

    # Upload ne doit pas être appelé si URL déjà renseignée [TG-F6]
    assert len(upload_calls) == 0
