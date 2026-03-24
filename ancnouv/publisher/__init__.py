# Publication Meta (Instagram + Facebook) — Phase 4 [SPEC-3.4, docs/INSTAGRAM_API.md]
# [D-02] Pas d'imports top-level InstagramPublisher/FacebookPublisher.
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ancnouv.db.models import Post
    from ancnouv.publisher.facebook import FacebookPublisher
    from ancnouv.publisher.instagram import InstagramPublisher

logger = logging.getLogger(__name__)


async def _check_and_increment_daily_count(
    session: AsyncSession,
    max_daily_posts: int,
) -> None:
    """Vérifie et incrémente le compteur journalier de publications Instagram.

    Réinitialise automatiquement si la date a changé.
    Lève PublisherError si la limite est atteinte [RF-3.4.7].
    N'est appelé que si Instagram est activé (ig_publisher is not None) [IG-F10].
    """
    from ancnouv.db.utils import get_scheduler_state, set_scheduler_state
    from ancnouv.exceptions import PublisherError

    today_str = datetime.now(timezone.utc).date().isoformat()
    count_date = await get_scheduler_state(session, "daily_post_count_date")

    if count_date != today_str:
        # Nouveau jour — réinitialiser le compteur
        await set_scheduler_state(session, "daily_post_count", "0")
        await set_scheduler_state(session, "daily_post_count_date", today_str)
        count = 0
    else:
        count_str = await get_scheduler_state(session, "daily_post_count") or "0"
        count = int(count_str)

    if count >= max_daily_posts:
        raise PublisherError(
            f"Limite journalière Instagram atteinte ({count}/{max_daily_posts}). "
            "Le post sera traité lors de la prochaine fenêtre. "
            "Action manuelle : UPDATE posts SET status='approved' WHERE status='queued'."
        )

    await set_scheduler_state(session, "daily_post_count", str(count + 1))


async def publish_to_all_platforms(
    post: "Post",
    image_url: str,
    ig_publisher: "InstagramPublisher | None",
    fb_publisher: "FacebookPublisher | None",
    session: AsyncSession,
    caption: str | None = None,
    max_daily_posts: int = 25,
    story_image_url: str | None = None,
) -> dict:
    """Publie sur Instagram et/ou Facebook en parallèle [IG-4, SPEC-3.4.4].

    session : utilisée UNIQUEMENT pour les opérations sur post (status, erreurs,
              published_count). Les publishers créent leurs propres sessions [IG-4].

    Retourne {"instagram": post_id | None, "facebook": post_id | None}.
    None = plateforme désactivée ou échec.

    Si les deux plateformes sont désactivées : post marqué 'published' intentionnellement
    (approbation Telegram valide le contenu) [IG-4].
    """
    from ancnouv.db.session import get_session
    from ancnouv.db.utils import get_scheduler_state
    from ancnouv.exceptions import PublisherError

    # Vérification publications_suspended hors race condition [SC-2]
    suspended = await get_scheduler_state(session, "publications_suspended")
    if suspended == "true":
        raise PublisherError(
            "Publications suspendues (token expiré ou suspendu). "
            "Relancer `auth meta` pour lever la suspension."
        )

    # [Phase 5] Limite journalière vérifiée par l'appelant (handle_approve, JOB-3)
    # avant d'appeler publish_to_all_platforms — ne pas double-incrémenter [TELEGRAM_BOT.md].

    caption_to_use = caption or post.caption

    # post.status = 'publishing' avant les appels Meta — crash-safe [IG-4, SPEC-3.4.4]
    post.status = "publishing"
    await session.commit()

    # Tâches parallèles — chaque publisher avec sa propre session [IG-4]
    async def ig_task() -> str:
        async with get_session() as ig_session:
            return await ig_publisher.publish(post, image_url, caption_to_use, ig_session)  # type: ignore[union-attr]

    async def fb_task() -> str:
        async with get_session() as fb_session:
            return await fb_publisher.publish(post, image_url, caption_to_use, fb_session)  # type: ignore[union-attr]

    tasks = []
    task_labels = []
    if ig_publisher is not None:
        tasks.append(ig_task())
        task_labels.append("instagram")
    if fb_publisher is not None:
        tasks.append(fb_task())
        task_labels.append("facebook")

    logger.info("Publication post %d sur : %s", post.id, ", ".join(task_labels) or "aucune plateforme")

    results: list = []
    if tasks:
        results = list(
            await asyncio.gather(*tasks, return_exceptions=True)
        )

    # Mapper les résultats [IG-4, SPEC-3.4.6]
    ig_result: str | None = None
    fb_result: str | None = None
    ig_exc: Exception | None = None
    fb_exc: Exception | None = None

    for label, result in zip(task_labels, results):
        if label == "instagram":
            if isinstance(result, Exception):
                ig_exc = result
                logger.error("Échec publication Instagram (post %d) : %s", post.id, result)
            else:
                ig_result = result
                logger.info("Instagram OK (post %d) : %s", post.id, result)
        elif label == "facebook":
            if isinstance(result, Exception):
                fb_exc = result
                logger.error("Échec publication Facebook (post %d) : %s", post.id, result)
            else:
                fb_result = result
                logger.info("Facebook OK (post %d) : %s", post.id, result)

    # Mise à jour atomique : status + erreurs + published_count dans un seul commit [DB-2]
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    if ig_result is not None:
        post.instagram_post_id = ig_result
        post.instagram_error = None
    elif ig_exc is not None:
        post.instagram_error = str(ig_exc)

    if fb_result is not None:
        post.facebook_post_id = fb_result
        post.facebook_error = None
    elif fb_exc is not None:
        post.facebook_error = str(fb_exc)

    success = ig_result is not None or fb_result is not None
    # Si les deux plateformes sont désactivées : succès intentionnel [IG-4]
    both_disabled = ig_publisher is None and fb_publisher is None

    if success or both_disabled:
        post.status = "published"
        post.published_at = now_utc

        # Incrémenter published_count sur la source (event ou article)
        if post.event_id is not None:
            await session.execute(
                text(
                    "UPDATE events SET "
                    "  published_count = published_count + 1, "
                    "  updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :id"
                ),
                {"id": post.event_id},
            )
        elif post.article_id is not None:
            await session.execute(
                text(
                    "UPDATE rss_articles SET "
                    "  published_count = published_count + 1, "
                    "  updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :id"
                ),
                {"id": post.article_id},
            )
    else:
        post.status = "error"
        errors = []
        if ig_exc:
            errors.append(f"Instagram: {ig_exc}")
        if fb_exc:
            errors.append(f"Facebook: {fb_exc}")
        post.error_message = " | ".join(errors)

    post.updated_at = now_utc
    await session.commit()

    # Publication des Stories si image disponible [SPEC-7, RF-7.3.3, RF-7.3.6]
    # Non-bloquante : un échec Story ne change pas le statut du post feed.
    if story_image_url and (ig_publisher is not None or fb_publisher is not None):
        await _publish_stories(post, story_image_url, ig_publisher, fb_publisher, session)

    return {"instagram": ig_result, "facebook": fb_result}


async def _publish_stories(
    post: "Post",
    story_image_url: str,
    ig_publisher: "InstagramPublisher | None",
    fb_publisher: "FacebookPublisher | None",
    session: AsyncSession,
) -> None:
    """Publie les Stories IG + FB en parallèle après le post feed. [SPEC-7, RF-7.3.3, RF-7.3.6]

    Erreur non-bloquante : loggée mais ne modifie pas post.status [RF-7.3.6].
    story_post_id persisté sur succès.
    """
    from ancnouv.db.session import get_session

    async def ig_story_task() -> str:
        async with get_session() as s:
            return await ig_publisher.publish_story(story_image_url, s)  # type: ignore[union-attr]

    async def fb_story_task() -> str:
        async with get_session() as s:
            return await fb_publisher.publish_story(story_image_url, s)  # type: ignore[union-attr]

    story_tasks = []
    story_labels = []
    if ig_publisher is not None:
        story_tasks.append(ig_story_task())
        story_labels.append("ig")
    if fb_publisher is not None:
        story_tasks.append(fb_story_task())
        story_labels.append("fb")

    results = list(await asyncio.gather(*story_tasks, return_exceptions=True))

    story_ids = []
    for label, result in zip(story_labels, results):
        if isinstance(result, Exception):
            logger.error("Échec Story %s (post %d) : %s", label, post.id, result)
        else:
            story_ids.append(result)
            logger.info("Story %s OK (post %d) : %s", label, post.id, result)

    if story_ids:
        # Stocker le premier story_post_id disponible (IG prioritaire)
        post.story_post_id = story_ids[0]
        await session.commit()
