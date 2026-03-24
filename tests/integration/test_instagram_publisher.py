# Tests d'intégration — InstagramPublisher [docs/INSTAGRAM_API.md — IG-3]
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import seed_token


async def test_publish_success(db_session, mock_config, httpx_mock):
    """Flow complet container → publish réussi. [TESTING.md]"""
    from ancnouv.db.models import Post
    from ancnouv.publisher.instagram import InstagramPublisher
    from ancnouv.publisher.token_manager import TokenManager

    await seed_token(db_session)

    # Mock création container
    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media",
        json={"id": "container_123"},
    )
    # Mock publication
    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media_publish",
        json={"id": "post_456"},
    )

    post = Post(
        event_id=None,
        article_id=1,
        caption="Test caption",
        image_path="/tmp/test.jpg",
        status="approved",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramPublisher(
        ig_user_id=mock_config.instagram.user_id,
        token_manager=token_mgr,
        api_version="v21.0",
    )

    with patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="FINISHED")):
        result = await publisher.publish(post, "https://example.com/img.jpg", "Caption", db_session)

    assert result == "post_456"


async def test_publish_container_creation_error(db_session, mock_config, httpx_mock):
    """Erreur création container (code 190) → PublisherError. [TESTING.md]"""
    from ancnouv.db.models import Post
    from ancnouv.exceptions import TokenExpiredError
    from ancnouv.publisher.instagram import InstagramPublisher
    from ancnouv.publisher.token_manager import TokenManager

    await seed_token(db_session)

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media",
        json={"error": {"code": 190, "message": "Invalid OAuth access token."}},
    )

    post = Post(
        event_id=None,
        article_id=1,
        caption="Test",
        image_path="/tmp/test.jpg",
        status="approved",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramPublisher(
        ig_user_id=mock_config.instagram.user_id,
        token_manager=token_mgr,
        api_version="v21.0",
    )

    with pytest.raises(TokenExpiredError):
        await publisher.publish(post, "https://example.com/img.jpg", "Caption", db_session)


async def test_publish_container_processing_then_finished(db_session, mock_config, httpx_mock):
    """PROCESSING puis FINISHED → réussi après 2 appels. [IG-F9, TESTING.md]"""
    from ancnouv.db.models import Post
    from ancnouv.publisher.instagram import InstagramPublisher
    from ancnouv.publisher.token_manager import TokenManager

    await seed_token(db_session)

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media",
        json={"id": "container_789"},
    )
    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media_publish",
        json={"id": "post_999"},
    )

    post = Post(
        event_id=None,
        article_id=1,
        caption="Test",
        image_path="/tmp/test.jpg",
        status="approved",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramPublisher(
        ig_user_id=mock_config.instagram.user_id,
        token_manager=token_mgr,
        api_version="v21.0",
    )

    mock_status = AsyncMock(side_effect=["PROCESSING", "FINISHED"])
    with patch.object(publisher, "_get_container_status", new=mock_status):
        result = await publisher.publish(post, "https://example.com/img.jpg", "Caption", db_session)

    assert result == "post_999"
    assert mock_status.call_count == 2


async def test_instagram_publish_story_success(db_session, mock_config, httpx_mock):
    """publish_story → retourne story_post_id. [SPEC-7, SPEC-7.3.4, IG-F5]"""
    from ancnouv.publisher.instagram import InstagramPublisher
    from ancnouv.publisher.token_manager import TokenManager

    await seed_token(db_session)

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media",
        json={"id": "story_container_xyz"},
    )
    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media_publish",
        json={"id": "story_ig_999"},
    )

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramPublisher(
        ig_user_id=mock_config.instagram.user_id,
        token_manager=token_mgr,
        api_version="v21.0",
    )

    with patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="FINISHED")):
        result = await publisher.publish_story("https://example.com/story.jpg", db_session)

    assert result == "story_ig_999"


async def test_instagram_publish_story_error(db_session, mock_config, httpx_mock):
    """publish_story erreur container → PublisherError. [SPEC-7, IG-F5]"""
    from ancnouv.exceptions import PublisherError
    from ancnouv.publisher.instagram import InstagramPublisher
    from ancnouv.publisher.token_manager import TokenManager

    await seed_token(db_session)

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.instagram.user_id}/media",
        json={"error": {"code": 500, "message": "Server error."}},
    )

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramPublisher(
        ig_user_id=mock_config.instagram.user_id,
        token_manager=token_mgr,
        api_version="v21.0",
    )

    with pytest.raises(PublisherError):
        await publisher.publish_story("https://example.com/story.jpg", db_session)


async def test_publish_to_all_platforms_suspended(db_session):
    """publish_to_all_platforms lève PublisherError si publications suspendues. [SC-2]"""
    from ancnouv.db.utils import set_scheduler_state
    from ancnouv.exceptions import PublisherError
    from ancnouv.publisher import publish_to_all_platforms
    from ancnouv.db.models import Post

    await set_scheduler_state(db_session, "publications_suspended", "true")

    post = Post(
        event_id=None,
        article_id=1,
        caption="Caption",
        image_path="/tmp/test.jpg",
        status="approved",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    with pytest.raises(PublisherError, match="suspendues"):
        await publish_to_all_platforms(post, "https://example.com/img.jpg", None, None, db_session)


async def test_publisher_daily_count_new_day(db_session):
    """_check_and_increment_daily_count réinitialise sur nouveau jour. [publisher/__init__.py]"""
    from ancnouv.publisher import _check_and_increment_daily_count

    # Appel sur DB vierge → nouveau jour → count=0 → incrément sans erreur
    await _check_and_increment_daily_count(db_session, max_daily_posts=25)


async def test_publisher_daily_count_limit_exceeded(db_session):
    """_check_and_increment_daily_count lève PublisherError si limite atteinte. [publisher/__init__.py]"""
    from ancnouv.db.utils import set_scheduler_state
    from ancnouv.exceptions import PublisherError
    from ancnouv.publisher import _check_and_increment_daily_count

    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    await set_scheduler_state(db_session, "daily_post_count_date", today)
    await set_scheduler_state(db_session, "daily_post_count", "5")

    with pytest.raises(PublisherError):
        await _check_and_increment_daily_count(db_session, max_daily_posts=5)


async def test_publish_stories_ig_fb_parallel(db_session, mock_config):
    """_publish_stories publie IG + FB en parallèle, story_post_id persisté. [SPEC-7, IG-F5C]"""
    from unittest.mock import MagicMock, patch as mpatch

    from ancnouv.db.models import Post
    from ancnouv.publisher import _publish_stories

    post = Post(
        event_id=None,
        article_id=1,
        caption="Caption",
        image_path="/tmp/test.jpg",
        status="published",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    ig_mock = MagicMock()
    ig_mock.publish_story = AsyncMock(return_value="story_ig_111")
    fb_mock = MagicMock()
    fb_mock.publish_story = AsyncMock(return_value="story_fb_111")

    fake_cm = MagicMock()
    fake_cm.__aenter__ = AsyncMock(return_value=db_session)
    fake_cm.__aexit__ = AsyncMock(return_value=None)

    with mpatch("ancnouv.db.session.get_session", new=MagicMock(return_value=fake_cm)):
        await _publish_stories(post, "https://example.com/story.jpg", ig_mock, fb_mock, db_session)

    assert post.story_post_id in ("story_ig_111", "story_fb_111")
