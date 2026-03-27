# Génération de posts — Phase 3 [SPEC-3.2, docs/IMAGE_GENERATION.md]
from __future__ import annotations

import logging
import random
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ancnouv.db.models import Event, GallicaArticle, Post, RssArticle

logger = logging.getLogger(__name__)


def get_image_path(config) -> tuple[Path, Path]:
    """Construit les chemins feed + story pour la prochaine image générée. [IMAGE_GENERATION.md, SPEC-7.3.7]

    Retourne (feed_path, story_path) basés sur le même UUID.
    story_path = {uuid}_story.jpg [RF-7.3.7].
    """
    images_dir = Path(config.data_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    uid = uuid4()
    return images_dir / f"{uid}.jpg", images_dir / f"{uid}_story.jpg"


async def generate_post(session: AsyncSession) -> Post | None:
    """Génère un post (sélection, image, légende) et l'insère en DB. [SPEC-3.2, DS-3]

    Config obtenue via get_config() (singleton scheduler/context.py). [IMAGE_GENERATION.md]
    Sélection hybride A+B selon mix_ratio. Fallback si la source tirée est vide. [DS-3]
    Guard max_pending_posts : retourne None si déjà atteint. [RF-3.2.7, T-08]
    """
    from ancnouv.generator.caption import format_caption, format_caption_rss
    from ancnouv.generator.image import fetch_thumbnail, generate_image, generate_story_image
    from ancnouv.generator.selector import get_effective_query_params, select_article, select_event, select_gallica_article
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

    # [DS-3] Sélection hybride A+B+C selon mix_ratio
    source = None
    today = date.today()

    # Tirage aléatoire pour Mode C (Gallica)
    if config.gallica.enabled and random.random() < config.gallica.mix_ratio:
        source = await select_gallica_article(session, config, {})

    # Tirage aléatoire pour Mode B (RSS) si Mode C non sélectionné ou vide
    if source is None and config.content.rss.enabled and random.random() < config.content.mix_ratio:
        source = await select_article(session, config, effective_params)

    # Mode A (Wikipedia) par défaut
    if source is None:
        source = await select_event(session, today, effective_params)

    # Fallback : B si A vide, C si A+B vides [DS-3]
    if source is None and config.content.rss.enabled:
        source = await select_article(session, config, effective_params)
    if source is None and config.gallica.enabled:
        source = await select_gallica_article(session, config, {})

    if source is None:
        logger.info("Aucune source disponible pour generate_post — retourne None")
        return None

    source_type = "rss" if isinstance(source, RssArticle) else "gallica" if isinstance(source, GallicaArticle) else "event"
    logger.info("Source sélectionnée : type=%s id=%s", source_type, source.id)

    # Téléchargement thumbnail (async)
    image_url = getattr(source, "image_url", None)
    logger.info("Thumbnail : %s", image_url[:80] if image_url else "aucune")
    thumbnail = await fetch_thumbnail(image_url)
    logger.info("Thumbnail : %s", "OK" if thumbnail else "absent (génération sans photo)")

    # Génération image feed (synchrone — ~100ms acceptable sur RPi4) [IMAGE_GENERATION.md]
    output_path, story_path = get_image_path(config)
    logger.info("Génération image feed…")
    generate_image(source, config, output_path, thumbnail=thumbnail)
    logger.info("Image feed générée : %s", output_path)

    # Génération image Story si activé [SPEC-7, RF-7.3.1]
    story_image_path: str | None = None
    if config.stories.enabled:
        logger.info("Génération Story…")
        try:
            generate_story_image(source, config, story_path, thumbnail=thumbnail)
            story_image_path = str(story_path)
            logger.info("Story générée : %s", story_path)
        except Exception as exc:
            logger.warning("Génération Story échouée (non-bloquant) : %s", exc)

    # Génération vidéo Reel si activé [SPEC-8, RF-8.3]
    reel_video_path: str | None = None
    if config.reels.enabled:
        logger.info("Génération Reel…")
        try:
            from ancnouv.generator.video import generate_reel_video, get_reel_output_path, get_shell_output_path
            from ancnouv.generator.image import generate_shell_image
            reel_out = get_reel_output_path(output_path)
            audio: Path | None = None
            if config.reels.audio_file:
                audio = Path(config.reels.audio_file)
                if not audio.exists():
                    logger.warning("Fichier audio Reel introuvable : %s — sans audio", audio)
                    audio = None
            else:
                # Sélection aléatoire parmi les fichiers CC0 de assets/audio/ [SPEC-8.3]
                audio_dir = Path("assets") / "audio"
                candidates = list(audio_dir.glob("*.mp3")) if audio_dir.exists() else []
                if candidates:
                    audio = random.choice(candidates)
                    logger.info("Audio Reel sélectionné : %s", audio.name)
                else:
                    logger.info("Audio Reel : aucun fichier dans %s — sans audio", audio_dir)
            # Image shell (fond+chrome sans texte) pour l'animation fade+reveal
            # La miniature est incluse dans le shell : révélée en phase 1, texte en phase 2
            shell_path = get_shell_output_path(output_path)
            shell_image_path: Path | None = None
            try:
                generate_shell_image(source, config, shell_path, thumbnail=thumbnail)
                shell_image_path = shell_path
                logger.info("Shell image générée : %s", shell_path)
            except Exception as shell_exc:
                logger.warning("Génération shell image échouée (fallback sans shell) : %s", shell_exc)
            logger.info("Encodage ffmpeg Reel…")
            await generate_reel_video(
                output_path,
                reel_out,
                shell_image_path=shell_image_path,
                audio_path=audio,
                duration_seconds=config.reels.duration_seconds,
                fps=config.reels.fps,
            )
            reel_video_path = str(reel_out)
            logger.info("Reel générée : %s", reel_out)
        except Exception as exc:
            logger.error("Génération Reel échouée : %s", exc, exc_info=True)

    # Formatage légende
    if isinstance(source, RssArticle):
        caption = format_caption_rss(source, config)
    elif isinstance(source, GallicaArticle):
        from ancnouv.generator.caption import format_caption_gallica
        caption = format_caption_gallica(source, config)
    else:
        caption = format_caption(source, config)

    # [SPEC-3.2.6] Sauvegarde post dans une transaction — rollback si exception
    now = datetime.now(timezone.utc)
    post = Post(
        event_id=source.id if isinstance(source, Event) else None,
        article_id=source.id if isinstance(source, RssArticle) else None,
        gallica_id=source.id if isinstance(source, GallicaArticle) else None,
        caption=caption,
        image_path=str(output_path),
        story_image_path=story_image_path,
        reel_video_path=reel_video_path,
        status="pending_approval",
    )
    session.add(post)

    # [IMAGE_GENERATION.md TC-5] last_used_at mis à jour à la génération, pas à la publication
    source.last_used_at = now

    await session.commit()
    logger.info(
        "Post généré : id=%s source=%s source_id=%s",
        post.id,
        "rss" if isinstance(source, RssArticle) else "gallica" if isinstance(source, GallicaArticle) else "event",
        source.id,
    )
    return post
