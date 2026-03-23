# Gestionnaire de tokens Meta [docs/INSTAGRAM_API.md — IG-2.4, IG-F1]
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ancnouv.db.models import MetaToken
from ancnouv.exceptions import PublisherError, TokenExpiredError

logger = logging.getLogger(__name__)

# Seuils d'alerte progressifs en jours [IG-2.4, SCHEDULER.md JOB-5]
ALERT_THRESHOLDS = [30, 14, 7, 3, 1]

# Seuil de refresh anticipé : tenter si remaining <= 7 [IG-2.3, IG-2.4]
_REFRESH_THRESHOLD_DAYS = 7


def days_until_expiry(expires_at: datetime) -> int:
    """Retourne (expires_at - utcnow()).days — négatif si expiré.

    expires_at est UTC naïf (sans tzinfo). Comparaison avec datetime.utcnow()
    pour éviter les erreurs de timezone système [IG-2.4].
    """
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    return (expires_at - now_utc).days


def get_alert_threshold(remaining: int) -> int | None:
    """Retourne le seuil d'alerte si une notification doit être envoyée ce jour.

    - remaining <= 0 → 0 (token expiré — distinct de 1 pour le message Telegram)
    - remaining dans ALERT_THRESHOLDS → remaining
    - sinon → None (pas de notification ce jour — anti-spam)
    """
    if remaining <= 0:
        return 0
    if remaining in ALERT_THRESHOLDS:
        return remaining
    return None


class TokenManager:
    """Gestion centralisée des tokens Meta (user_long + page).

    Singleton partagé entre InstagramPublisher et FacebookPublisher.
    Le asyncio.Lock interne empêche les double-refreshes lors d'appels
    concurrents (ex : asyncio.gather Instagram + Facebook).
    """

    def __init__(
        self,
        meta_app_id: str,
        meta_app_secret: str,
        api_version: str = "v21.0",
        notify_fn: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._app_id = meta_app_id
        self._app_secret = meta_app_secret
        self._api_version = api_version
        self._notify_fn = notify_fn
        self._lock = asyncio.Lock()

    async def get_valid_token(
        self, session: AsyncSession, token_kind: str = "user_long"
    ) -> str:
        """Retourne un token valide.

        - token_kind='page' : permanent, retourné directement (pas d'expiration).
        - token_kind='user_long' : vérifie l'expiration, tente le refresh si
          remaining <= 7. Lève TokenExpiredError uniquement si remaining <= 0.
          Un échec de refresh entre J-7 et J-1 est absorbé silencieusement —
          le token encore valide est retourné [IG-2.4].
        """
        async with self._lock:
            token = await self._load_token(session, token_kind)
            if token is None:
                raise PublisherError(
                    f"Token {token_kind!r} introuvable en DB — relancer `auth meta`."
                )

            if token_kind == "page":
                return token.access_token

            # --- token_kind == 'user_long' ---
            if token.expires_at is None:
                raise TokenExpiredError(
                    "Token utilisateur sans date d'expiration — relancer `auth meta`."
                )

            remaining = days_until_expiry(token.expires_at)

            if remaining <= 0:
                raise TokenExpiredError(
                    f"Token Meta expiré depuis {-remaining} jour(s). "
                    "Relancer `python -m ancnouv auth meta`."
                )

            if remaining <= _REFRESH_THRESHOLD_DAYS:
                try:
                    new_access_token, expires_in = await self._refresh_token(
                        token.access_token
                    )
                    # Vérification que expires_at a progressé [IG-F8]
                    new_expires_at = (
                        datetime.now(timezone.utc).replace(tzinfo=None)
                        + timedelta(seconds=expires_in)
                    )
                    if token.expires_at and new_expires_at <= token.expires_at:
                        logger.warning(
                            "Refresh token : expires_at inchangé après refresh "
                            "(remaining=%d j) — Meta a peut-être refusé le renouvellement.",
                            remaining,
                        )
                    await self._save_token(session, new_access_token, expires_in)
                    logger.info(
                        "Token Meta renouvelé avec succès (remaining=%d j).", remaining
                    )
                    return new_access_token
                except (PublisherError, TokenExpiredError):
                    raise
                except Exception as exc:
                    # Échec absorbé si token encore valide [IG-2.4]
                    logger.warning(
                        "Échec refresh token Meta (remaining=%d j) : %s. "
                        "Le token actuel est encore valide.",
                        remaining,
                        exc,
                    )

            return token.access_token

    async def _load_token(
        self, session: AsyncSession, token_kind: str
    ) -> MetaToken | None:
        """Charge le token depuis la DB."""
        from sqlalchemy import select

        result = await session.execute(
            select(MetaToken).where(MetaToken.token_kind == token_kind)
        )
        return result.scalar_one_or_none()

    async def _refresh_token(
        self, current_access_token: str
    ) -> tuple[str, int]:
        """Renouvelle le token long via Meta Graph API [IG-2.3].

        Lit le corps JSON avant raise_for_status() — les erreurs Meta sont dans le corps.
        expires_in peut être absent → 5 184 000 s (60 jours) par défaut [IG-2.3].
        """
        url = f"https://graph.facebook.com/{self._api_version}/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self._app_id,
            "client_secret": self._app_secret,
            "fb_exchange_token": current_access_token,
        }
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=30)

        data = response.json()
        if "error" in data:
            err = data["error"]
            raise Exception(
                f"Meta refresh error {err.get('code')}: {err.get('message', err)}"
            )

        response.raise_for_status()

        access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 5_184_000))  # 60 jours par défaut
        return access_token, expires_in

    async def _save_token(
        self,
        session: AsyncSession,
        new_access_token: str,
        expires_in_seconds: int,
    ) -> None:
        """Met à jour le token user_long en DB. Commit immédiat.

        Ne touche jamais le token 'page' — seul `auth meta` peut le régénérer.
        [IG-2.4] Contrainte intentionnelle : _save_token concerne uniquement user_long.
        """
        new_expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(seconds=expires_in_seconds)
        )
        await session.execute(
            text(
                "UPDATE meta_tokens SET "
                "  access_token = :token, "
                "  expires_at = :expires_at, "
                "  last_refreshed_at = CURRENT_TIMESTAMP, "
                "  updated_at = CURRENT_TIMESTAMP "
                "WHERE token_kind = 'user_long'"
            ),
            {"token": new_access_token, "expires_at": new_expires_at},
        )
        await session.commit()
