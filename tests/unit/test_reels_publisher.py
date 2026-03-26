# Tests unitaires — InstagramReelsPublisher [SPEC-8]
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ancnouv.exceptions import PublisherError, TokenExpiredError


# ─── Fonctions pures ─────────────────────────────────────────────────────────────

def test_extract_meta_error_no_error():
    """Pas de clé 'error' → None."""
    from ancnouv.publisher.reels import _extract_meta_error
    assert _extract_meta_error({"id": "123"}) is None


def test_extract_meta_error_with_error():
    """Clé 'error' présente → (code, message)."""
    from ancnouv.publisher.reels import _extract_meta_error
    data = {"error": {"code": 190, "message": "Invalid token"}}
    assert _extract_meta_error(data) == (190, "Invalid token")


def test_raise_meta_error_190():
    """Code 190 → TokenExpiredError. [SPEC-8]"""
    from ancnouv.publisher.reels import _raise_meta_error
    with pytest.raises(TokenExpiredError, match="190"):
        _raise_meta_error(190, "Token expiré")


def test_raise_meta_error_other():
    """Code != 190 → PublisherError."""
    from ancnouv.publisher.reels import _raise_meta_error
    with pytest.raises(PublisherError, match="400"):
        _raise_meta_error(400, "Bad request")


# ─── Tests publish ────────────────────────────────────────────────────────────────

async def test_publish_success(db_session, mock_config, httpx_mock):
    """Flow complet : container créé → persisté → FINISHED → publié. [SPEC-8]"""
    from ancnouv.db.models import Post
    from ancnouv.publisher.reels import InstagramReelsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    ig_user_id = mock_config.instagram.user_id
    api_version = "v21.0"

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/{api_version}/{ig_user_id}/media",
        json={"id": "reel_container_1"},
    )
    httpx_mock.add_response(
        url=f"https://graph.facebook.com/{api_version}/{ig_user_id}/media_publish",
        json={"id": "reel_post_xyz"},
    )

    post = Post(
        event_id=None, article_id=1, caption="Test",
        image_path="/tmp/img.jpg", status="approved", telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramReelsPublisher(
        ig_user_id=ig_user_id, token_manager=token_mgr, api_version=api_version
    )

    with patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="FINISHED")):
        result = await publisher.publish(post, "https://example.com/video.mp4", "Caption", db_session)

    assert result == "reel_post_xyz"
    # reel_container_id persisté immédiatement après création [SPEC-8]
    assert post.reel_container_id == "reel_container_1"
    assert post.reel_post_id == "reel_post_xyz"


async def test_publish_token_expired(db_session, mock_config, httpx_mock):
    """Erreur 190 à la création container → TokenExpiredError. [SPEC-8]"""
    from ancnouv.db.models import Post
    from ancnouv.publisher.reels import InstagramReelsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    ig_user_id = mock_config.instagram.user_id
    api_version = "v21.0"

    httpx_mock.add_response(
        url=f"https://graph.facebook.com/{api_version}/{ig_user_id}/media",
        json={"error": {"code": 190, "message": "Invalid OAuth access token."}},
    )

    post = Post(
        event_id=None, article_id=1, caption="Test",
        image_path="/tmp/img.jpg", status="approved", telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramReelsPublisher(
        ig_user_id=ig_user_id, token_manager=token_mgr, api_version=api_version
    )

    with pytest.raises(TokenExpiredError):
        await publisher.publish(post, "https://example.com/video.mp4", "Caption", db_session)


async def test_wait_for_container_error_status(db_session, mock_config):
    """Status ERROR → PublisherError immédiat."""
    from ancnouv.publisher.reels import InstagramReelsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramReelsPublisher(ig_user_id="u", token_manager=token_mgr)

    with patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="ERROR")):
        with pytest.raises(PublisherError, match="ERROR"):
            await publisher._wait_for_container_ready("cid", "token")


async def test_wait_for_container_expired_status(db_session, mock_config):
    """Status EXPIRED → PublisherError immédiat."""
    from ancnouv.publisher.reels import InstagramReelsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramReelsPublisher(ig_user_id="u", token_manager=token_mgr)

    with patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="EXPIRED")):
        with pytest.raises(PublisherError, match="EXPIRED"):
            await publisher._wait_for_container_ready("cid", "token")


async def test_wait_for_container_timeout(db_session, mock_config):
    """Timeout après max_wait itérations → PublisherError. [SPEC-8]"""
    from ancnouv.publisher.reels import InstagramReelsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramReelsPublisher(ig_user_id="u", token_manager=token_mgr)

    with patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="IN_PROGRESS")):
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(PublisherError, match="5 minutes"):
                await publisher._wait_for_container_ready("cid", "token", max_wait=2)


async def test_get_container_status(db_session, mock_config, httpx_mock):
    """_get_container_status retourne la valeur du champ 'status_code'."""
    import re
    from ancnouv.publisher.reels import InstagramReelsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    container_id = "cid_reel"
    api_version = "v21.0"

    httpx_mock.add_response(
        url=re.compile(rf"https://graph\.facebook\.com/{api_version}/{container_id}"),
        json={"status_code": "FINISHED", "id": container_id},
    )

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = InstagramReelsPublisher(ig_user_id="u", token_manager=token_mgr, api_version=api_version)
    status = await publisher._get_container_status(container_id, "token")
    assert status == "FINISHED"
