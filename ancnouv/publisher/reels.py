# Publication Reels Instagram [SPEC-8]
# API: même API Graph Meta qu'Instagram (graph.facebook.com)
# Endpoint: POST /{ig_user_id}/media avec media_type=REELS, video_url=..., caption=...
# Polling: max_wait=150 itérations × 2s = 5 minutes (les vidéos prennent plus de temps)
# Persistance: reel_container_id sauvegardé immédiatement après création (résistance aux crashs)
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


def _extract_meta_error(data: dict) -> tuple[int, str] | None:
    """Extrait le code et le message d'erreur Meta Graph API si présents."""
    if "error" not in data:
        return None
    err = data["error"]
    return err.get("code", 0), err.get("message", str(err))


def _raise_meta_error(code: int, message: str) -> None:
    """Lève l'exception appropriée selon le code d'erreur Meta [SPEC-8]."""
    if code == 190:
        raise TokenExpiredError(f"Token Meta invalide ou révoqué (code 190) : {message}")
    raise PublisherError(f"Meta API error {code}: {message}")


class InstagramReelsPublisher:
    """Publication de Reels sur Instagram via l'API Graph Meta [SPEC-8].

    Résistant aux crashs : reel_container_id est persisté immédiatement après
    création du container, avant la publication [SPEC-8].
    Polling étendu à 150 itérations (5 min) — l'encodage vidéo est lent.
    """

    def __init__(
        self,
        ig_user_id: str,
        token_manager: "TokenManager",
        api_version: str = "v21.0",
    ) -> None:
        self.ig_user_id = ig_user_id
        self.token_manager = token_manager
        self.api_version = api_version

    async def publish(
        self,
        post: "Post",
        video_url: str,
        caption: str,
        session: AsyncSession,
    ) -> str:
        """Publie un Reel sur Instagram. Retourne reel_post_id [SPEC-8].

        Flux : création container → persistance reel_container_id → polling
        FINISHED → publication via media_publish.
        """
        access_token = await self.token_manager.get_valid_token(session)

        # Créer le container Reel
        logger.debug("Création container Reel Instagram pour le post %d.", post.id)
        container_id = await self._create_reel_container(video_url, caption, access_token)

        # Persistance immédiate pour résistance aux crashs [SPEC-8]
        post.reel_container_id = container_id
        await session.commit()

        # Attendre FINISHED — encodage vidéo : jusqu'à 5 minutes
        await self._wait_for_container_ready(container_id, access_token)

        reel_post_id = await self._publish_container(container_id, access_token)

        # Persister l'ID du post publié
        post.reel_post_id = reel_post_id
        await session.commit()

        permalink = await self._get_permalink(reel_post_id, access_token)
        return permalink or reel_post_id

    async def _create_reel_container(
        self, video_url: str, caption: str, access_token: str
    ) -> str:
        """Crée le container Reel Instagram (media_type=REELS) [SPEC-8]. Retourne container_id."""
        url = f"https://graph.facebook.com/{self.api_version}/{self.ig_user_id}/media"
        payload = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
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
        self, container_id: str, access_token: str, max_wait: int = 150
    ) -> None:
        """Attend que le container Reel soit FINISHED (polling toutes les 2s).

        max_wait : 150 itérations → attente max = 5 minutes [SPEC-8].
        L'encodage vidéo est nettement plus lent que les images.
        Lève PublisherError si ERROR, EXPIRED, ou timeout.
        """
        for _ in range(max_wait):
            status = await self._get_container_status(container_id, access_token)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise PublisherError(
                    f"Container Reel Meta inutilisable — statut {status}. "
                    "Lancer /retry pour re-publier."
                )
            await asyncio.sleep(2)

        raise PublisherError(
            "Container Reel Meta bloqué — statut IN_PROGRESS après 5 minutes. "
            "Vérifier l'URL de la vidéo."
        )

    async def _get_container_status(self, container_id: str, access_token: str) -> str:
        """Interroge le statut du container Reel [SPEC-8]. Retourne status_code."""
        url = f"https://graph.facebook.com/{self.api_version}/{container_id}"
        params = {"fields": "status_code", "access_token": access_token}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=15)

        data = response.json()
        err = _extract_meta_error(data)
        if err:
            _raise_meta_error(*err)
        response.raise_for_status()
        return data.get("status_code", "IN_PROGRESS")

    async def _get_permalink(self, media_id: str, access_token: str) -> str | None:
        """Récupère l'URL publique du Reel depuis l'API Graph [SPEC-8].

        Retourne None en cas d'erreur — l'appelant utilise alors l'ID numérique.
        """
        url = f"https://graph.facebook.com/{self.api_version}/{media_id}"
        params = {"fields": "permalink", "access_token": access_token}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10)
            data = response.json()
            return data.get("permalink")
        except Exception as exc:
            logger.error("Impossible de récupérer le permalink Reel %s : %s", media_id, exc)
            return None

    async def _publish_container(self, container_id: str, access_token: str) -> str:
        """Publie le container Reel FINISHED via media_publish [SPEC-8]. Retourne reel_post_id."""
        url = f"https://graph.facebook.com/{self.api_version}/{self.ig_user_id}/media_publish"
        payload = {"creation_id": container_id, "access_token": access_token}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30)

        data = response.json()
        err = _extract_meta_error(data)
        if err:
            _raise_meta_error(*err)
        response.raise_for_status()
        return data["id"]
