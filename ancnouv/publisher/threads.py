# Publication Threads [SPEC-9.1]
# API base: https://graph.threads.net
# Endpoints:
#   POST /v1.0/{user_id}/threads          → crée le container (retourne {id})
#   GET  /v1.0/{id}?fields=status         → poll statut (FINISHED/IN_PROGRESS/ERROR/EXPIRED)
#   POST /v1.0/{user_id}/threads_publish  → publie (creation_id=...) → retourne {id}
# Token: user_long via token_manager.get_valid_token(session)
# Caption: tronquée à 500 chars max [SPEC-9.1]
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ancnouv.exceptions import PublisherError, TokenExpiredError

if TYPE_CHECKING:
    from ancnouv.db.models import Post
    from ancnouv.publisher.token_manager import TokenManager

logger = logging.getLogger(__name__)

_API_BASE = "https://graph.threads.net"
_CAPTION_MAX_LEN = 500


def _extract_meta_error(data: dict) -> tuple[int, str] | None:
    """Extrait le code et le message d'erreur Threads API si présents."""
    if "error" not in data:
        return None
    err = data["error"]
    return err.get("code", 0), err.get("message", str(err))


def _raise_meta_error(code: int, message: str) -> None:
    """Lève l'exception appropriée selon le code d'erreur [SPEC-9.1]."""
    if code == 190:
        raise TokenExpiredError(f"Token Meta invalide ou révoqué (code 190) : {message}")
    raise PublisherError(f"Threads API error {code}: {message}")


def _truncate_caption(caption: str) -> str:
    """Tronque la légende à 500 chars max — coupe au dernier espace [SPEC-9.1]."""
    if len(caption) <= _CAPTION_MAX_LEN:
        return caption
    # Couper à _CAPTION_MAX_LEN - 1 pour laisser la place à "…"
    truncated = caption[:_CAPTION_MAX_LEN - 1].rsplit(" ", 1)[0]
    return truncated + "…"


class ThreadsPublisher:
    """Publication sur Threads via l'API graph.threads.net [SPEC-9.1].

    Pas de persistance du container_id : les containers Threads expirent
    rapidement — pas de résistance aux crashs contrairement à Instagram.
    """

    def __init__(
        self,
        user_id: str,
        token_manager: "TokenManager",
        api_version: str = "v1.0",
    ) -> None:
        self.user_id = user_id
        self.token_manager = token_manager
        self.api_version = api_version

    async def publish(
        self,
        post: "Post",
        image_url: str,
        caption: str,
        session: AsyncSession,
    ) -> str:
        """Publie une image sur Threads. Retourne threads_post_id [SPEC-9.1].

        Flux en 3 étapes : création container → polling FINISHED → publication.
        Caption tronquée à 500 chars si nécessaire.
        """
        access_token = await self.token_manager.get_valid_token(session)
        safe_caption = _truncate_caption(caption)

        logger.debug("Création container Threads pour le post %d.", post.id)
        container_id = await self._create_container(image_url, safe_caption, access_token)

        # Attendre FINISHED — même pattern que Instagram (30 × 2s = 60s max)
        await self._wait_for_container_ready(container_id, access_token)

        threads_post_id = await self._publish_container(container_id, access_token)
        logger.info("Post Threads publié : %s (post %d).", threads_post_id, post.id)
        return threads_post_id

    async def _create_container(
        self, image_url: str, caption: str, access_token: str
    ) -> str:
        """Crée le container Threads (media_type=IMAGE) [SPEC-9.1]. Retourne container_id."""
        url = f"{_API_BASE}/{self.api_version}/{self.user_id}/threads"
        payload = {
            "media_type": "IMAGE",
            "image_url": image_url,
            "text": caption,
            "access_token": access_token,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30)

        data = response.json()
        err = _extract_meta_error(data)
        if err:
            _raise_meta_error(*err)
        response.raise_for_status()
        return data["id"]

    async def _wait_for_container_ready(
        self, container_id: str, access_token: str, max_wait: int = 30
    ) -> None:
        """Attend que le container soit FINISHED (polling toutes les 2s).

        max_wait : nombre maximum d'itérations → attente max = 60s.
        Lève PublisherError si ERROR, EXPIRED, ou timeout.
        """
        for _ in range(max_wait):
            status = await self._get_container_status(container_id, access_token)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise PublisherError(
                    f"Container Threads inutilisable — statut {status}. "
                    "Lancer /retry pour re-publier."
                )
            await asyncio.sleep(2)

        raise PublisherError(
            "Container Threads bloqué — statut IN_PROGRESS après 60s. "
            "Vérifier que l'URL de l'image est accessible publiquement."
        )

    async def _get_container_status(self, container_id: str, access_token: str) -> str:
        """Interroge le statut du container Threads [SPEC-9.1]. Retourne le statut."""
        url = f"{_API_BASE}/{self.api_version}/{container_id}"
        params = {"fields": "status", "access_token": access_token}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=15)

        data = response.json()
        err = _extract_meta_error(data)
        if err:
            _raise_meta_error(*err)
        response.raise_for_status()
        return data.get("status", "IN_PROGRESS")

    async def _publish_container(self, container_id: str, access_token: str) -> str:
        """Publie le container FINISHED via threads_publish [SPEC-9.1]. Retourne post_id."""
        url = f"{_API_BASE}/{self.api_version}/{self.user_id}/threads_publish"
        payload = {"creation_id": container_id, "access_token": access_token}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30)

        data = response.json()
        err = _extract_meta_error(data)
        if err:
            _raise_meta_error(*err)
        response.raise_for_status()
        return data["id"]
