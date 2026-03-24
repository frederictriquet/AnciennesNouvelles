# Publication Facebook [SPEC-3.4.3, docs/INSTAGRAM_API.md — FB-1]
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ancnouv.exceptions import PublisherError, TokenExpiredError

if TYPE_CHECKING:
    from ancnouv.db.models import Post
    from ancnouv.publisher.token_manager import TokenManager

logger = logging.getLogger(__name__)


class FacebookPublisher:
    """Publication sur la Page Facebook via l'API Graph Meta.

    Utilise le Page Access Token permanent (token_kind='page').
    L'api_version est partagé depuis InstagramConfig [CONF-11].
    """

    def __init__(
        self,
        page_id: str,
        token_manager: "TokenManager",
        api_version: str = "v21.0",
    ) -> None:
        self.page_id = page_id
        self.token_manager = token_manager
        self.api_version = api_version

    async def publish(
        self,
        post: "Post",
        image_url: str,
        caption: str,
        session: AsyncSession,
    ) -> str:
        """Publie une photo sur la Page Facebook [FB-1.2]. Retourne facebook_post_id.

        Utilise data.get("post_id") or data.get("id") — post_id peut être absent
        si la photo est publiée sans légende [FB-1.2].
        """
        access_token = await self.token_manager.get_valid_token(
            session, token_kind="page"
        )

        url = f"https://graph.facebook.com/{self.api_version}/{self.page_id}/photos"
        payload = {
            "url": image_url,
            "caption": caption,
            "access_token": access_token,
        }

        logger.debug("Publication Facebook du post %d.", post.id)
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30)

        data = response.json()

        if "error" in data:
            err = data["error"]
            code = err.get("code", 0)
            message = err.get("message", str(err))
            if code == 190:
                raise TokenExpiredError(
                    f"Token Meta invalide ou révoqué (code 190) : {message}"
                )
            raise PublisherError(f"Meta Facebook API error {code}: {message}")

        response.raise_for_status()

        # post_id peut être absent si pas de légende [FB-1.2]
        facebook_post_id = data.get("post_id") or data.get("id")
        if not facebook_post_id:
            raise PublisherError(
                "Réponse Facebook invalide : ni post_id ni id dans la réponse."
            )

        return facebook_post_id

    async def publish_story(
        self,
        image_url: str,
        session: AsyncSession,
    ) -> str:
        """Publie une Story sur la Page Facebook. Retourne story_post_id. [SPEC-7, SPEC-7.3.4]

        Endpoint : POST /{page-id}/photo_stories [SPEC-7.2, IG-F5].
        """
        access_token = await self.token_manager.get_valid_token(
            session, token_kind="page"
        )

        url = f"https://graph.facebook.com/{self.api_version}/{self.page_id}/photo_stories"
        payload = {
            "url": image_url,
            "access_token": access_token,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30)

        data = response.json()

        if "error" in data:
            err = data["error"]
            code = err.get("code", 0)
            message = err.get("message", str(err))
            if code == 190:
                raise PublisherError(
                    f"Token Meta invalide ou révoqué (code 190) : {message}"
                )
            raise PublisherError(f"Meta Facebook Story API error {code}: {message}")

        response.raise_for_status()

        story_id = data.get("post_id") or data.get("id")
        if not story_id:
            raise PublisherError(
                "Réponse Facebook Story invalide : ni post_id ni id dans la réponse."
            )
        return story_id
