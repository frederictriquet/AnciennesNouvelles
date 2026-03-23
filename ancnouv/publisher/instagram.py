# Publication Instagram [SPEC-3.4.2, docs/INSTAGRAM_API.md — IG-3, IG-3.4]
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
    """Lève l'exception appropriée selon le code d'erreur Meta [IG-3.4]."""
    if code == 190:
        raise TokenExpiredError(f"Token Meta invalide ou révoqué (code 190) : {message}")
    raise PublisherError(f"Meta API error {code}: {message}")


class InstagramPublisher:
    """Publication sur Instagram via l'API Graph Meta.

    Résistant aux crashs : ig_container_id est persisté immédiatement après
    création du container, avant la publication [IG-3.4].
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
        image_url: str,
        caption: str,
        session: AsyncSession,
    ) -> str:
        """Publie une image sur Instagram. Retourne instagram_post_id.

        Guard image_path NULL [IG-F3] : levée de PublisherError si l'image
        a été nettoyée par job_cleanup.
        Le post est chargé depuis la session du publisher pour isolation [IG-4].
        """
        if post.image_path is None:
            raise PublisherError(
                "image_path NULL — image nettoyée par job_cleanup. "
                "Lancer /retry pour re-générer."
            )

        access_token = await self.token_manager.get_valid_token(session)

        # Chargement du post dans la session du publisher (isolation des sessions)
        from ancnouv.db.models import Post as PostModel

        db_post = await session.get(PostModel, post.id)
        if db_post is None:
            raise PublisherError(f"Post {post.id} introuvable en DB.")

        container_id = await self._get_or_create_container(
            db_post, image_url, caption, access_token, session
        )

        # Toujours attendre FINISHED avant publish — Meta est asynchrone [IG-3.4]
        await self._wait_for_container_ready(container_id, access_token)

        ig_post_id = await self._publish_container(
            self.ig_user_id, container_id, access_token
        )
        return ig_post_id

    async def _get_or_create_container(
        self,
        post: "Post",
        image_url: str,
        caption: str,
        access_token: str,
        session: AsyncSession,
    ) -> str:
        """Réutilise le container existant si valide, sinon en crée un nouveau.

        Si IN_PROGRESS : attend la fin. Si bloqué (timeout), recrée.
        Si ERROR/EXPIRED : recrée directement.
        Commit du creation_id immédiatement après création [IG-3.4].
        """
        if post.ig_container_id:
            status = await self._get_container_status(post.ig_container_id, access_token)

            if status == "FINISHED":
                return post.ig_container_id

            if status == "IN_PROGRESS":
                try:
                    await self._wait_for_container_ready(post.ig_container_id, access_token)
                    return post.ig_container_id
                except PublisherError:
                    # Container bloqué — effacer et recréer [IG-3.4]
                    logger.warning(
                        "Container %s bloqué en IN_PROGRESS — recréation.",
                        post.ig_container_id,
                    )
                    post.ig_container_id = None
                    await session.commit()
                    # fall through vers création

            # status == ERROR ou EXPIRED : recréer
            elif status in ("ERROR", "EXPIRED"):
                logger.info(
                    "Container %s inutilisable (statut %s) — recréation.",
                    post.ig_container_id,
                    status,
                )
                post.ig_container_id = None
                await session.commit()

        # Créer un nouveau container
        container_id = await self._create_media_container(
            self.ig_user_id, image_url, caption, access_token
        )

        # Persistance immédiate pour résistance aux crashs [IG-3.4]
        post.ig_container_id = container_id
        await session.commit()

        return container_id

    async def _wait_for_container_ready(
        self, creation_id: str, token: str, max_wait: int = 30
    ) -> None:
        """Attend que le container soit FINISHED (polling toutes les 2s).

        max_wait : nombre maximum d'itérations → attente max = 60s [IG-3.3, IG-3.4].
        Lève PublisherError si ERROR, EXPIRED, ou timeout.
        """
        for _ in range(max_wait):
            status = await self._get_container_status(creation_id, token)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise PublisherError(
                    f"Container Meta inutilisable — statut {status}. "
                    "Lancer /retry pour re-publier."
                )
            await asyncio.sleep(2)

        raise PublisherError(
            "Container Meta bloqué — statut IN_PROGRESS après 60s. "
            "Vérifier que l'URL de l'image est accessible publiquement."
        )

    async def _create_media_container(
        self,
        ig_user_id: str,
        image_url: str,
        caption: str,
        access_token: str,
    ) -> str:
        """Crée le container média Instagram [IG-3.1]. Retourne creation_id."""
        url = f"https://graph.facebook.com/{self.api_version}/{ig_user_id}/media"
        payload = {
            "image_url": image_url,
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

    async def _get_container_status(
        self, creation_id: str, access_token: str
    ) -> str:
        """Interroge le statut du container [IG-3.3]. Retourne status_code."""
        url = f"https://graph.facebook.com/{self.api_version}/{creation_id}"
        params = {"fields": "status_code", "access_token": access_token}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=15)

        data = response.json()
        err = _extract_meta_error(data)
        if err:
            _raise_meta_error(*err)
        response.raise_for_status()

        return data.get("status_code", "IN_PROGRESS")

    async def _publish_container(
        self,
        ig_user_id: str,
        creation_id: str,
        access_token: str,
    ) -> str:
        """Publie le container FINISHED [IG-3.2]. Retourne instagram_post_id.

        Si code 9007 reçu malgré le wait (race condition rare) : lever PublisherError
        sans retry — l'upload sera retenté au prochain /retry [IG-3.4].
        """
        url = f"https://graph.facebook.com/{self.api_version}/{ig_user_id}/media_publish"
        payload = {"creation_id": creation_id, "access_token": access_token}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30)

        data = response.json()
        err = _extract_meta_error(data)
        if err:
            code, message = err
            if code == 9007:
                raise PublisherError(
                    "Container pas encore finalisé (code 9007 — race condition rare). "
                    "Lancer /retry."
                )
            _raise_meta_error(code, message)
        response.raise_for_status()

        return data["id"]
