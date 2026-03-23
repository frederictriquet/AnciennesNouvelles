# Tests d'intégration — FacebookPublisher [SPEC-3.4.3]
from __future__ import annotations

import pytest

from tests.conftest import seed_page_token


async def test_facebook_publish_success(db_session, mock_config, httpx_mock):
    """Flow /photos réussi → retourne facebook_post_id. [TESTING.md]"""
    from ancnouv.db.models import Post
    from ancnouv.publisher.facebook import FacebookPublisher
    from ancnouv.publisher.token_manager import TokenManager

    await seed_page_token(db_session)

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.facebook.page_id}/photos",
        json={"post_id": "page_123_post_789"},
    )

    post = Post(
        event_id=None,
        article_id=1,
        caption="Test FB",
        image_path="/tmp/test.jpg",
        status="approved",
        telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = FacebookPublisher(
        page_id=mock_config.facebook.page_id,
        token_manager=token_mgr,
        api_version="v21.0",
    )

    result = await publisher.publish(post, "https://example.com/img.jpg", "Caption", db_session)
    assert result == "page_123_post_789"


async def test_facebook_publish_error(db_session, mock_config, httpx_mock):
    """Erreur Meta (code 190) → TokenExpiredError. [TESTING.md]"""
    from ancnouv.db.models import Post
    from ancnouv.exceptions import TokenExpiredError
    from ancnouv.publisher.facebook import FacebookPublisher
    from ancnouv.publisher.token_manager import TokenManager

    await seed_page_token(db_session)

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/v21.0/{mock_config.facebook.page_id}/photos",
        json={"error": {"code": 190, "message": "Invalid token."}},
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
    publisher = FacebookPublisher(
        page_id=mock_config.facebook.page_id,
        token_manager=token_mgr,
        api_version="v21.0",
    )

    with pytest.raises(TokenExpiredError):
        await publisher.publish(post, "https://example.com/img.jpg", "Caption", db_session)


async def test_facebook_no_page_token_raises(db_session, mock_config):
    """Aucun token page en DB → TokenExpiredError (ou PublisherError). [TESTING.md]"""
    from ancnouv.db.models import Post
    from ancnouv.exceptions import PublisherError, TokenExpiredError
    from ancnouv.publisher.facebook import FacebookPublisher
    from ancnouv.publisher.token_manager import TokenManager

    # Pas de token inséré

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
    publisher = FacebookPublisher(
        page_id=mock_config.facebook.page_id,
        token_manager=token_mgr,
        api_version="v21.0",
    )

    with pytest.raises((TokenExpiredError, PublisherError)):
        await publisher.publish(post, "https://example.com/img.jpg", "Caption", db_session)
