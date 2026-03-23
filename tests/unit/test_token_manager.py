# Tests unitaires — TokenManager [docs/INSTAGRAM_API.md — IG-2.4, T-03]
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ancnouv.publisher.token_manager import days_until_expiry, get_alert_threshold


# ─── days_until_expiry ──────────────────────────────────────────────────────────

def test_days_until_expiry_future():
    """now + 15j + 1h → 15 (robuste aux exécutions à minuit). [T-03]"""
    expires = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(days=15, hours=1)
    )
    assert days_until_expiry(expires) == 15


def test_days_until_expiry_past():
    """Date passée → valeur négative."""
    expires = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)
    assert days_until_expiry(expires) < 0


def test_days_until_expiry_today():
    """Expire aujourd'hui (dans quelques heures) → 0."""
    expires = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=2)
    )
    assert days_until_expiry(expires) == 0


# ─── get_alert_threshold ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("remaining,expected", [
    (35, None),    # au-delà des seuils
    (30, 30),      # seuil J-30
    (25, None),    # entre seuils — anti-spam
    (14, 14),      # seuil J-14
    (7, 7),        # seuil J-7 + refresh déclenché
    (5, None),     # entre J-7 et J-3
    (3, 3),        # seuil J-3 + refresh déclenché
    (2, None),     # entre J-3 et J-1
    (1, 1),        # seuil J-1 — alerte bloquante
    (0, 0),        # expiré aujourd'hui
    (-5, 0),       # expiré depuis plusieurs jours
])
def test_get_alert_threshold(remaining, expected):
    """Tous les seuils d'alerte [T-03, SCHEDULER.md JOB-5]."""
    assert get_alert_threshold(remaining) == expected


# ─── get_valid_token (avec DB en mémoire) ───────────────────────────────────────

async def test_get_valid_token_no_refresh_needed(db_session):
    """Token avec 35j restants → retourné sans appel réseau. [RF-3.4.5]"""
    from ancnouv.db.models import MetaToken
    from ancnouv.publisher.token_manager import TokenManager

    token = MetaToken(
        token_kind="user_long",
        access_token="valid_token_35j",
        expires_at=(
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(days=35, hours=1)
        ),
    )
    db_session.add(token)
    await db_session.commit()

    mgr = TokenManager("app_id", "app_secret")
    result = await mgr.get_valid_token(db_session, "user_long")
    assert result == "valid_token_35j"


async def test_get_valid_token_raises_when_expired(db_session):
    """Token expiré → TokenExpiredError."""
    from ancnouv.db.models import MetaToken
    from ancnouv.exceptions import TokenExpiredError
    from ancnouv.publisher.token_manager import TokenManager

    token = MetaToken(
        token_kind="user_long",
        access_token="expired_token",
        expires_at=(
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(days=2)
        ),
    )
    db_session.add(token)
    await db_session.commit()

    mgr = TokenManager("app_id", "app_secret")
    with pytest.raises(TokenExpiredError):
        await mgr.get_valid_token(db_session, "user_long")


async def test_get_valid_token_page_returns_directly(db_session):
    """Token page (permanent) → retourné directement sans check expiration. [IG-2.4]"""
    from ancnouv.db.models import MetaToken
    from ancnouv.publisher.token_manager import TokenManager

    token = MetaToken(
        token_kind="page",
        access_token="page_token_permanent",
        expires_at=None,
    )
    db_session.add(token)
    await db_session.commit()

    mgr = TokenManager("app_id", "app_secret")
    result = await mgr.get_valid_token(db_session, "page")
    assert result == "page_token_permanent"


async def test_get_valid_token_triggers_refresh(db_session, httpx_mock):
    """Token avec 5j restants → refresh automatique déclenché. [RF-3.4.5]"""
    from ancnouv.db.models import MetaToken
    from ancnouv.publisher.token_manager import TokenManager

    token = MetaToken(
        token_kind="user_long",
        access_token="old_token",
        expires_at=(
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(days=5, hours=1)
        ),
    )
    db_session.add(token)
    await db_session.commit()

    httpx_mock.add_response(
        json={"access_token": "new_refreshed_token", "expires_in": 5_184_000},
    )

    mgr = TokenManager("app_id", "app_secret")
    result = await mgr.get_valid_token(db_session, "user_long")
    assert result == "new_refreshed_token"
