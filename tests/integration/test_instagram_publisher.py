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
