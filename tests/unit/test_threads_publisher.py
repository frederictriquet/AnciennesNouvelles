# Tests unitaires — ThreadsPublisher [SPEC-9.1]
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ancnouv.exceptions import PublisherError, TokenExpiredError


# ─── Fonctions pures ─────────────────────────────────────────────────────────────

def test_truncate_caption_short():
    """Légende courte → inchangée."""
    from ancnouv.publisher.threads import _truncate_caption
    assert _truncate_caption("Courte légende") == "Courte légende"


def test_truncate_caption_exact_limit():
    """Légende exactement à la limite → inchangée."""
    from ancnouv.publisher.threads import _truncate_caption, _CAPTION_MAX_LEN
    text = "a" * _CAPTION_MAX_LEN
    assert _truncate_caption(text) == text


def test_truncate_caption_long():
    """Légende > 500 chars → tronquée avec '…' ≤ 500 chars. [SPEC-9.1]"""
    from ancnouv.publisher.threads import _truncate_caption, _CAPTION_MAX_LEN
    text = " ".join(["mot"] * 200)  # bien au-delà de 500 chars
    result = _truncate_caption(text)
    assert len(result) <= _CAPTION_MAX_LEN
    assert result.endswith("…")


def test_extract_meta_error_no_error():
    """Pas de clé 'error' → None."""
    from ancnouv.publisher.threads import _extract_meta_error
    assert _extract_meta_error({"id": "123"}) is None


def test_extract_meta_error_with_error():
    """Clé 'error' présente → (code, message)."""
    from ancnouv.publisher.threads import _extract_meta_error
    data = {"error": {"code": 190, "message": "Invalid token"}}
    assert _extract_meta_error(data) == (190, "Invalid token")


def test_raise_meta_error_190_raises_token_expired():
    """Code 190 → TokenExpiredError. [SPEC-9.1]"""
    from ancnouv.publisher.threads import _raise_meta_error
    with pytest.raises(TokenExpiredError, match="190"):
        _raise_meta_error(190, "Token invalide")


def test_raise_meta_error_other_raises_publisher_error():
    """Code != 190 → PublisherError."""
    from ancnouv.publisher.threads import _raise_meta_error
    with pytest.raises(PublisherError, match="400"):
        _raise_meta_error(400, "Bad request")


# ─── Tests publish ────────────────────────────────────────────────────────────────

async def test_publish_success(db_session, mock_config, httpx_mock):
    """Flow complet : container → FINISHED → publication → retourne post_id. [SPEC-9.1]"""
    from ancnouv.db.models import Post
    from ancnouv.publisher.threads import ThreadsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    user_id = "threads_user_1"
    api_version = "v1.0"

    httpx_mock.add_response(
        url=f"https://graph.threads.net/{api_version}/{user_id}/threads",
        json={"id": "container_t1"},
    )
    httpx_mock.add_response(
        url=f"https://graph.threads.net/{api_version}/{user_id}/threads_publish",
        json={"id": "threads_post_abc"},
    )

    post = Post(
        event_id=None, article_id=1, caption="Test",
        image_path="/tmp/img.jpg", status="approved", telegram_message_ids="{}",
    )
    db_session.add(post)
    await db_session.commit()
    await db_session.refresh(post)

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = ThreadsPublisher(user_id=user_id, token_manager=token_mgr, api_version=api_version)

    with patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="FINISHED")):
        result = await publisher.publish(post, "https://example.com/img.jpg", "Caption", db_session)

    assert result == "threads_post_abc"


async def test_publish_token_expired(db_session, mock_config, httpx_mock):
    """Erreur 190 à la création container → TokenExpiredError. [SPEC-9.1]"""
    from ancnouv.db.models import Post
    from ancnouv.publisher.threads import ThreadsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    user_id = "threads_user_2"
    api_version = "v1.0"

    httpx_mock.add_response(
        url=f"https://graph.threads.net/{api_version}/{user_id}/threads",
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
    publisher = ThreadsPublisher(user_id=user_id, token_manager=token_mgr, api_version=api_version)

    with pytest.raises(TokenExpiredError):
        await publisher.publish(post, "https://example.com/img.jpg", "Caption", db_session)


async def test_wait_for_container_error_status(db_session, mock_config):
    """Status ERROR → PublisherError immédiat."""
    from ancnouv.publisher.threads import ThreadsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = ThreadsPublisher(user_id="u", token_manager=token_mgr)

    with patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="ERROR")):
        with pytest.raises(PublisherError, match="ERROR"):
            await publisher._wait_for_container_ready("cid", "token")


async def test_wait_for_container_expired_status(db_session, mock_config):
    """Status EXPIRED → PublisherError immédiat."""
    from ancnouv.publisher.threads import ThreadsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = ThreadsPublisher(user_id="u", token_manager=token_mgr)

    with patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="EXPIRED")):
        with pytest.raises(PublisherError, match="EXPIRED"):
            await publisher._wait_for_container_ready("cid", "token")


async def test_wait_for_container_timeout(db_session, mock_config):
    """Timeout (IN_PROGRESS pendant max_wait itérations) → PublisherError. [SPEC-9.1]"""
    from ancnouv.publisher.threads import ThreadsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = ThreadsPublisher(user_id="u", token_manager=token_mgr)

    with patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="IN_PROGRESS")):
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(PublisherError, match="60s"):
                await publisher._wait_for_container_ready("cid", "token", max_wait=2)


async def test_get_container_status(db_session, mock_config, httpx_mock):
    """_get_container_status retourne la valeur du champ 'status'."""
    import re
    from ancnouv.publisher.threads import ThreadsPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from tests.conftest import seed_token

    await seed_token(db_session)
    container_id = "cid_test"
    api_version = "v1.0"

    httpx_mock.add_response(
        url=re.compile(rf"https://graph\.threads\.net/{api_version}/{container_id}"),
        json={"status": "FINISHED", "id": container_id},
    )

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = ThreadsPublisher(user_id="u", token_manager=token_mgr, api_version=api_version)
    status = await publisher._get_container_status(container_id, "token")
    assert status == "FINISHED"


async def test_get_permalink_threads(db_session, mock_config, httpx_mock):
    """_get_permalink retourne l'URL publique du post Threads."""
    import re
    from ancnouv.publisher.threads import ThreadsPublisher
    from ancnouv.publisher.token_manager import TokenManager

    post_id = "threads_post_456"
    api_version = "v1.0"

    httpx_mock.add_response(
        url=re.compile(rf"https://graph\.threads\.net/{api_version}/{post_id}"),
        json={"permalink": "https://www.threads.net/@user/post/ABC456", "id": post_id},
    )

    token_mgr = TokenManager(mock_config.meta_app_id, mock_config.meta_app_secret)
    publisher = ThreadsPublisher(user_id="u", token_manager=token_mgr, api_version=api_version)
    permalink = await publisher._get_permalink(post_id, "token")
    assert permalink == "https://www.threads.net/@user/post/ABC456"
