# Tests d'intégration — recover_pending_posts [SCHEDULER.md, TRANSVERSAL-3, T-10]
# [TESTING.md — test_recover_pending_posts.py]
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ancnouv.db.models import Event, Post
from ancnouv.db.utils import compute_content_hash
from ancnouv.scheduler.jobs import recover_pending_posts


@pytest.fixture
async def event_in_db(db_session):
    """Event disponible en DB pour les posts."""
    ev = Event(
        source="wikipedia",
        source_lang="fr",
        event_type="event",
        month=3,
        day=21,
        year=1871,
        description="Commune de Paris.",
        content_hash=compute_content_hash("Commune de Paris."),
        status="available",
        published_count=0,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(ev)
    await db_session.commit()
    await db_session.refresh(ev)
    return ev


async def test_recover_sends_pending_posts(db_session, event_in_db, mock_config, mock_bot):
    """2 posts pending + telegram_message_ids={} → send_approval_request appelé 2 fois."""
    for i in range(2):
        post = Post(
            event_id=event_in_db.id,
            caption=f"Caption {i}",
            image_path=f"/tmp/test{i}.jpg",
            status="pending_approval",
            telegram_message_ids="{}",
        )
        db_session.add(post)
    await db_session.commit()

    send_calls = []

    async def mock_send_approval(post, bot, session, config):
        send_calls.append(post.id)
        post.telegram_message_ids = json.dumps({"123456789": 101 + post.id})
        await session.commit()

    with patch("ancnouv.bot.notifications.send_approval_request", side_effect=mock_send_approval):
        await recover_pending_posts(db_session, mock_bot, mock_config)

    assert len(send_calls) == 2


async def test_recover_skips_already_sent(db_session, event_in_db, mock_config, mock_bot):
    """Post pending + telegram_message_ids renseigné → send_approval_request non appelé."""
    post = Post(
        event_id=event_in_db.id,
        caption="Déjà envoyé",
        image_path="/tmp/test.jpg",
        status="pending_approval",
        telegram_message_ids='{"123456789": 456}',
    )
    db_session.add(post)
    await db_session.commit()

    send_calls = []

    async def mock_send_approval(p, bot, session, config):
        send_calls.append(p.id)

    with patch("ancnouv.bot.notifications.send_approval_request", side_effect=mock_send_approval):
        await recover_pending_posts(db_session, mock_bot, mock_config)

    assert len(send_calls) == 0


async def test_recover_publishes_approved_with_url(db_session, event_in_db, mock_config, mock_bot):
    """Post approved + image_public_url → _publish_approved_post appelé. [SC-4]"""
    post = Post(
        event_id=event_in_db.id,
        caption="Approuvé avec URL",
        image_path="/tmp/test.jpg",
        status="approved",
        image_public_url="https://example.com/img.jpg",
        instagram_post_id=None,
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    publish_calls = []

    async def mock_publish(*args, **kwargs):
        publish_calls.append(True)

    with patch("ancnouv.publisher.publish_to_all_platforms", side_effect=mock_publish):
        await recover_pending_posts(db_session, mock_bot, mock_config)

    assert len(publish_calls) == 1


async def test_recover_approved_without_url_notifies(db_session, event_in_db, mock_config, mock_bot):
    """Post approved sans image_public_url → notify_all appelé, post.status='error'."""
    post = Post(
        event_id=event_in_db.id,
        caption="Approuvé sans URL",
        image_path="/tmp/test.jpg",
        status="approved",
        image_public_url=None,
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    notify_messages = []

    async def capture_notify(bot, cfg, msg):
        notify_messages.append(msg)

    with (
        patch("ancnouv.bot.notifications.notify_all", side_effect=capture_notify),
        patch("ancnouv.publisher.publish_to_all_platforms", new=AsyncMock()),
    ):
        await recover_pending_posts(db_session, mock_bot, mock_config)

    assert any("url" in m.lower() or "image" in m.lower() for m in notify_messages)


async def test_recover_publishing_posts_on_restart(db_session, event_in_db, mock_config, mock_bot):
    """Post status='publishing' → status='approved' après recover. [SC-4, T-10]"""
    post = Post(
        event_id=event_in_db.id,
        caption="Crash mid-publish",
        image_path="/tmp/test.jpg",
        status="publishing",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    with patch("ancnouv.bot.notifications.notify_all", new=AsyncMock()):
        await recover_pending_posts(db_session, mock_bot, mock_config)

    await db_session.refresh(post)
    assert post.status == "approved"
