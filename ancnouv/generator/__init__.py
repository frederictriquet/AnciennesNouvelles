# Génération de posts — Phase 3 [SPEC-3.2, docs/IMAGE_GENERATION.md]
from __future__ import annotations

import logging
import random
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ancnouv.db.models import Event, Post, RssArticle

logger = logging.getLogger(__name__)


def get_image_path(config) -> Path:
    """Construit le chemin pour la prochaine image générée. [IMAGE_GENERATION.md]"""
    images_dir = Path(config.data_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir / f"{uuid4()}.jpg"


async def generate_post(session: AsyncSession) -> Post | None:
    """Génère un post (sélection, image, légende) et l'insère en DB. [SPEC-3.2, DS-3]

    Config obtenue via get_config() (singleton scheduler/context.py). [IMAGE_GENERATION.md]
    Sélection hybride A+B selon mix_ratio. Fallback si la source tirée est vide. [DS-3]
    Guard max_pending_posts : retourne None si déjà atteint. [RF-3.2.7, T-08]
    """
    from ancnouv.generator.caption import format_caption, format_caption_rss
    from ancnouv.generator.image import fetch_thumbnail, generate_image
    from ancnouv.generator.selector import get_effective_query_params, select_article, select_event
    from ancnouv.scheduler.context import get_config

    config = get_config()

    # [RF-3.2.7, T-08] Guard max_pending_posts
    result = await session.execute(
        text("SELECT COUNT(*) FROM posts WHERE status = 'pending_approval'")
    )
    pending_count = result.scalar() or 0
    if pending_count >= config.scheduler.max_pending_posts:
        logger.debug(
            "max_pending_posts atteint (%d) — génération ignorée", config.scheduler.max_pending_posts
        )
        return None

    effective_params = await get_effective_query_params(session, config)

    # [DS-3] Sélection hybride A+B selon mix_ratio
    source = None
    today = date.today()

    if config.content.rss.enabled and random.random() < config.content.mix_ratio:
        # Mode B d'abord, fallback A
        source = await select_article(session, config, effective_params)
        if source is None:
            source = await select_event(session, today, effective_params)
    else:
        # Mode A d'abord, fallback B si RSS activé
        source = await select_event(session, today, effective_params)
        if source is None and config.content.rss.enabled:
            source = await select_article(session, config, effective_params)

    if source is None:
        logger.info("Aucune source disponible pour generate_post — retourne None")
        return None

    # Téléchargement thumbnail (async)
    thumbnail = await fetch_thumbnail(getattr(source, "image_url", None))

    # Génération image (synchrone — ~100ms acceptable sur RPi4) [IMAGE_GENERATION.md]
    output_path = get_image_path(config)
    generate_image(source, config, output_path, thumbnail=thumbnail)

    # Formatage légende
    if isinstance(source, RssArticle):
        caption = format_caption_rss(source, config)
    else:
        caption = format_caption(source, config)

    # [SPEC-3.2.6] Sauvegarde post dans une transaction — rollback si exception
    now = datetime.now(timezone.utc)
    post = Post(
        event_id=source.id if isinstance(source, Event) else None,
        article_id=source.id if isinstance(source, RssArticle) else None,
        caption=caption,
        image_path=str(output_path),
        status="pending_approval",
    )
    session.add(post)

    # [IMAGE_GENERATION.md TC-5] last_used_at mis à jour à la génération, pas à la publication
    source.last_used_at = now

    await session.commit()
    logger.info(
        "Post généré : id=%s source=%s source_id=%s",
        post.id,
        "rss" if isinstance(source, RssArticle) else "event",
        source.id,
    )
    return post
