# Jobs APScheduler — Phase 6 [docs/SCHEDULER.md, SPEC-3.5]
# [SPEC-3.5, SC-C6] Toutes les dépendances injectées via le contexte partagé (get_config,
# get_bot_app, get_engine) — jamais via args=/kwargs= APScheduler.
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
    from telegram import Bot

    from ancnouv.config import Config

logger = logging.getLogger(__name__)


# ─── Compteur journalier Instagram ─────────────────────────────────────────────

async def check_and_increment_daily_count(engine: "AsyncEngine", max_daily_posts: int) -> bool:
    """Vérifie et incrémente le compteur journalier avec verrou exclusif SQLite.

    Retourne True si la publication est autorisée (compteur incrémenté), False sinon.
    [SCHEDULER.md — Compteur journalier, SC-1, SC-2]

    Pattern BEGIN EXCLUSIVE requis pour l'atomicité : évite que JOB-3 et handle_approve
    lisent simultanément count=24 et publient tous les deux [SCHEDULER.md].

    execution_options(isolation_level="AUTOCOMMIT") empêche SQLAlchemy d'émettre
    son propre BEGIN implicite avant BEGIN EXCLUSIVE [SCHEDULER.md].
    Requiert aiosqlite >= 0.20.0 [SCHEDULER.md].
    """
    today_str = datetime.now(timezone.utc).date().isoformat()

    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("BEGIN EXCLUSIVE"))

        # 1. Vérification publications_suspended en premier — à l'intérieur du verrou [SC-2]
        result = await conn.execute(
            text("SELECT value FROM scheduler_state WHERE key = 'publications_suspended'")
        )
        row = result.fetchone()
        if row and row[0] == "true":
            await conn.execute(text("COMMIT"))
            return False

        # 2. Lecture date du compteur courant
        result = await conn.execute(
            text("SELECT value FROM scheduler_state WHERE key = 'daily_post_count_date'")
        )
        row = result.fetchone()
        count_date = row[0] if row else None

        # 3. Lecture compteur
        result = await conn.execute(
            text("SELECT value FROM scheduler_state WHERE key = 'daily_post_count'")
        )
        row = result.fetchone()
        count = int(row[0]) if row else 0

        if count_date == today_str:
            # Même jour — vérifier la limite [SC-2]
            if count >= max_daily_posts:
                await conn.execute(text("COMMIT"))
                return False
            new_count = count + 1
        else:
            # Nouveau jour — reset : premier post du jour toujours autorisé [SCHEDULER.md]
            new_count = 1

        # 5. Mise à jour atomique via INSERT ... ON CONFLICT DO UPDATE [SCHEDULER.md]
        _upsert = (
            "INSERT INTO scheduler_state (key, value, updated_at) "
            "VALUES (:key, :val, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP"
        )
        await conn.execute(text(_upsert), {"key": "daily_post_count", "val": str(new_count)})
        await conn.execute(text(_upsert), {"key": "daily_post_count_date", "val": today_str})
        await conn.execute(text("COMMIT"))
        return True


# ─── Pause / reprise ────────────────────────────────────────────────────────────

async def pause_scheduler(session: "AsyncSession") -> None:
    """Écrit scheduler_state.paused='true'. [SPEC-3.5.4, SCHEDULER.md]

    Seul JOB-3 vérifie ce flag — les autres jobs continuent de s'exécuter. [SCHEDULER.md]
    """
    from ancnouv.db.utils import set_scheduler_state
    await set_scheduler_state(session, "paused", "true")


async def resume_scheduler(session: "AsyncSession") -> None:
    """Écrit scheduler_state.paused='false'. [SPEC-3.5.4, SCHEDULER.md]"""
    from ancnouv.db.utils import set_scheduler_state
    await set_scheduler_state(session, "paused", "false")


async def is_paused(session: "AsyncSession") -> bool:
    """Retourne True si scheduler_state.paused='true'. [SPEC-3.5.4]"""
    from ancnouv.db.utils import get_scheduler_state
    return await get_scheduler_state(session, "paused") == "true"


# ─── Récupération après crash ───────────────────────────────────────────────────

async def recover_pending_posts(
    session: "AsyncSession",
    bot: "Bot",
    config: "Config",
) -> None:
    """Récupère les posts dans des états intermédiaires après crash/redémarrage.

    [TRANSVERSAL-3, SCHEDULER.md — Récupération après crash]

    session : utilisée uniquement pour SELECT/UPDATE initiaux. Les opérations réseau
    (Meta, Telegram) créent leurs propres sessions internes. [SC-m7]

    Séquence :
    1. publishing → approved (crash mid-publish)
    2. approved + image_public_url → republier via publish_to_all_platforms [SC-4]
    3. approved sans image_public_url → notifier /retry manuel
    4. pending_approval + telegram_message_ids == {} → re-envoyer [SC-10]
    """
    from sqlalchemy import select

    from ancnouv.db.models import Post

    # --- 1. publishing → approved (crash pendant publication) ---
    publishing_posts = (
        await session.execute(select(Post).where(Post.status == "publishing"))
    ).scalars().all()

    for post in publishing_posts:
        post.status = "approved"
        logger.info("recover: post %d publishing → approved (crash détecté)", post.id)

    if publishing_posts:
        await session.commit()

    # --- 2 & 3. Posts approved : republier ou notifier retry ---
    approved_posts = (
        await session.execute(select(Post).where(Post.status == "approved"))
    ).scalars().all()

    approved_with_url = [p for p in approved_posts if p.image_public_url]
    approved_without_url = [p for p in approved_posts if not p.image_public_url]

    # Posts sans URL → notifier /retry manuel
    if approved_without_url:
        lines = [f"Post #{p.id} : \"{(p.caption or '')[:50]}\"" for p in approved_without_url]
        msg = (
            "⚠️ Posts approuvés sans URL d'image après redémarrage.\n"
            "Lancer /retry pour republier :\n"
            + "\n".join(lines)
        )
        try:
            from ancnouv.bot.notifications import notify_all
            await notify_all(bot, config, msg)
        except Exception as exc:
            logger.error("recover: notification retry manuel échouée : %s", exc)

    # Posts avec URL → tenter republication [SC-4]
    for post in approved_with_url:
        ig_needs_publish = post.instagram_post_id is None
        fb_needs_publish = post.facebook_post_id is None

        if not ig_needs_publish and not fb_needs_publish:
            # Les deux plateformes ont déjà publié — marquer published directement
            post.status = "published"
            await session.commit()
            continue

        try:
            from ancnouv.db.models import Post as _Post
            from ancnouv.db.session import get_session as _get_session
            from ancnouv.publisher import publish_to_all_platforms
            from ancnouv.publisher.facebook import FacebookPublisher
            from ancnouv.publisher.instagram import InstagramPublisher
            from ancnouv.publisher.token_manager import TokenManager

            token_mgr = TokenManager(
                meta_app_id=config.meta_app_id,
                meta_app_secret=config.meta_app_secret,
                api_version=config.instagram.api_version,
            )
            ig_pub = (
                InstagramPublisher(config.instagram.user_id, token_mgr, config.instagram.api_version)
                if config.instagram.enabled and ig_needs_publish else None
            )
            fb_pub = (
                FacebookPublisher(config.facebook.page_id, token_mgr, config.instagram.api_version)
                if config.facebook.enabled and fb_needs_publish else None
            )
            threads_pub = None
            if config.threads.enabled and post.threads_post_id is None:
                from ancnouv.publisher.threads import ThreadsPublisher
                threads_pub = ThreadsPublisher(
                    config.threads.user_id, token_mgr, config.threads.api_version
                )

            async with _get_session() as pub_session:
                pub_post = await pub_session.get(_Post, post.id)
                if pub_post is None:
                    continue
                await publish_to_all_platforms(
                    post=pub_post,
                    image_url=post.image_public_url,
                    ig_publisher=ig_pub,
                    fb_publisher=fb_pub,
                    session=pub_session,
                    caption=pub_post.caption,
                    max_daily_posts=config.instagram.max_daily_posts,
                    threads_publisher=threads_pub,
                )
        except Exception as exc:
            logger.error("recover: republication post %d échouée : %s", post.id, exc)

    # --- 4. pending_approval + telegram_message_ids == {} → re-envoyer [SC-10] ---
    pending_posts = (
        await session.execute(select(Post).where(Post.status == "pending_approval"))
    ).scalars().all()

    for post in pending_posts:
        try:
            msg_ids = json.loads(post.telegram_message_ids) if post.telegram_message_ids else {}
        except (json.JSONDecodeError, TypeError):
            msg_ids = {}

        if msg_ids:
            # Partiellement envoyé — ne pas renvoyer [SC-10]
            continue

        try:
            from ancnouv.bot.notifications import send_approval_request
            from ancnouv.db.models import Post as _Post
            from ancnouv.db.session import get_session as _get_session

            async with _get_session() as resend_session:
                resend_post = await resend_session.get(_Post, post.id)
                if resend_post is None:
                    continue
                await send_approval_request(
                    post=resend_post,
                    bot=bot,
                    session=resend_session,
                    config=config,
                )
            logger.info("recover: post %d renvoyé en approbation Telegram", post.id)
        except Exception as exc:
            logger.error("recover: renvoi approbation post %d échoué : %s", post.id, exc)


# ─── JOB-1 : Collecte Wikipedia ────────────────────────────────────────────────

async def job_fetch_wiki() -> None:
    """Collecte Wikipedia pour les prefetch_days prochains jours. [SCHEDULER.md JOB-1]

    [DS-1.4c, TRANSVERSAL-1] get_effective_query_params appelé avant instanciation fetcher.
    [SC-M1] OperationalError → retry x3 avec backoff (1s, 2s, 4s).
    [SC-M1] Concurrence avec JOB-3 à 2h possible — recommander décalage via
    scheduler.generation_cron (ex: "30 */4 * * *") ou connect_args timeout=15.
    """
    from ancnouv.db.session import get_session
    from ancnouv.fetchers.wikipedia import WikipediaFetcher
    from ancnouv.generator.selector import (
        get_effective_query_params,
        increment_escalation_level,
        needs_escalation,
    )
    from ancnouv.config_loader import get_effective_config

    config = await get_effective_config()
    today = date.today()

    # [TRANSVERSAL-1] Paramètres effectifs avant instanciation (niveau d'escalade courant)
    async with get_session() as session:
        effective_params = await get_effective_query_params(session, config)

    fetcher = WikipediaFetcher(config, effective_params)

    for i in range(config.content.prefetch_days):
        target = today + timedelta(days=i)
        # [SC-M1] Retry x3 sur OperationalError (contention SQLite à 2h)
        for attempt in range(3):
            try:
                items = await fetcher.fetch(target)
                if items:
                    async with get_session() as session:
                        await fetcher.store(items, session)
                break
            except OperationalError as exc:
                delay = 2 ** attempt  # 1s, 2s, 4s
                if attempt < 2:
                    logger.warning(
                        "job_fetch_wiki : OperationalError %s (tentative %d/3) — retry %ds : %s",
                        target, attempt + 1, delay, exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "job_fetch_wiki : OperationalError %s après 3 tentatives : %s",
                        target, exc,
                    )
            except Exception as exc:
                logger.warning("job_fetch_wiki : erreur pour %s : %s", target, exc)
                break

    # Vérification stock 7j → escalation si bas [DS-1.4c]
    try:
        async with get_session() as session:
            if await needs_escalation(session, config):
                new_level = await increment_escalation_level(session)
                logger.warning("job_fetch_wiki : stock bas — escalade niveau %d", new_level)
    except Exception as exc:
        logger.error("job_fetch_wiki : erreur vérification escalade : %s", exc)


# ─── JOB-2 : Collecte RSS ──────────────────────────────────────────────────────

async def job_fetch_rss() -> None:
    """Collecte les articles RSS configurés. [SCHEDULER.md JOB-2]

    Séquentiel (pas asyncio.gather) pour éviter saturation du thread pool [JOB-2].
    Enregistré dans create_scheduler uniquement si rss.enabled=true.
    [SC-C1] Déclenchement immédiat au redémarrage si délai > 6h intentionnel (idempotent).
    """
    from ancnouv.db.session import get_session
    from ancnouv.fetchers.rss import RssFetcher
    from ancnouv.config_loader import get_effective_config

    config = await get_effective_config()
    try:
        fetcher = RssFetcher()
        articles = await fetcher.fetch_all(config)
        if articles:
            async with get_session() as session:
                await fetcher.store(articles, session)
        logger.info("job_fetch_rss : %d article(s) collecté(s)", len(articles))
    except Exception as exc:
        logger.error("job_fetch_rss : %s", exc, exc_info=True)


# ─── JOB-3 : Génération et soumission ──────────────────────────────────────────

async def job_generate() -> None:
    """Génère et soumet un post pour approbation (ou publie en auto_publish). [SCHEDULER.md JOB-3]

    [SC-C7] max_instances=1 — APScheduler skippe silencieusement si déjà actif.
    Toutes les exceptions internes sont catchées pour ne pas interrompre APScheduler.
    """
    try:
        await _job_generate_inner()
    except Exception as exc:
        logger.error("JOB-3 erreur : %s", exc, exc_info=True)


async def _job_generate_inner() -> None:
    from ancnouv.bot.notifications import notify_all, send_approval_request
    from ancnouv.db.session import get_session
    from ancnouv.db.utils import get_scheduler_state
    from ancnouv.generator import generate_post
    from ancnouv.config_loader import get_effective_config
    from ancnouv.scheduler.context import get_bot_app, get_engine

    config = await get_effective_config()
    bot_app = get_bot_app()
    engine = get_engine()
    today_str = date.today().strftime("%d/%m/%Y")

    async with get_session() as session:
        # 1. Vérification pause [SPEC-3.5.4]
        if await is_paused(session):
            logger.debug("job_generate : scheduler en pause — ignoré")
            return

        # Niveau 5 : stock critique — notification bloquante [DS-1.4b]
        level_raw = await get_scheduler_state(session, "escalation_level")
        if int(level_raw or "0") >= 5:
            await notify_all(
                bot_app.bot,
                config,
                "🚨 Niveau d'escalade 5 atteint — stock critique.\n"
                "Publication automatique suspendue jusqu'à résolution.\n"
                "Lancer `fetch --prefetch` puis `escalation reset`.",
            )
            return

        # 2. Vérification pending_count [RF-3.2.7]
        result = await session.execute(
            text("SELECT COUNT(*) FROM posts WHERE status = 'pending_approval'")
        )
        pending_count = result.scalar() or 0
        if pending_count >= config.scheduler.max_pending_posts:
            logger.debug(
                "job_generate : %d post(s) en attente >= max_pending_posts (%d) — ignoré",
                pending_count, config.scheduler.max_pending_posts,
            )
            return

        # 3. Génération
        post = await generate_post(session)

    if post is None:
        await notify_all(
            bot_app.bot, config,
            f"⚠️ Aucun événement disponible pour le {today_str}.",
        )
        return

    if config.reels.enabled and post.reel_video_path is None:
        await notify_all(
            bot_app.bot, config,
            "⚠️ Reel non généré pour ce post — voir les logs (ffmpeg introuvable ou erreur d'encodage).",
        )

    if not config.scheduler.auto_publish:
        # Mode approbation (défaut) — envoyer pour validation Telegram [SCHEDULER.md JOB-3]
        async with get_session() as session:
            await send_approval_request(post=post, bot=bot_app.bot, session=session, config=config)
        return

    # Mode auto_publish — vérifier limite APRÈS génération [RF-3.2.8, SCHEDULER.md]
    if not await check_and_increment_daily_count(engine, config.instagram.max_daily_posts):
        logger.info("job_generate (auto_publish) : limite journalière atteinte — ignoré")
        return

    from ancnouv.db.models import Post
    from ancnouv.db.session import get_session as _get_session
    from ancnouv.publisher import publish_to_all_platforms
    from ancnouv.publisher.facebook import FacebookPublisher
    from ancnouv.publisher.image_hosting import upload_image
    from ancnouv.publisher.instagram import InstagramPublisher
    from ancnouv.publisher.token_manager import TokenManager

    token_mgr = TokenManager(
        meta_app_id=config.meta_app_id,
        meta_app_secret=config.meta_app_secret,
        api_version=config.instagram.api_version,
    )
    ig_pub = (
        InstagramPublisher(config.instagram.user_id, token_mgr, config.instagram.api_version)
        if config.instagram.enabled else None
    )
    fb_pub = (
        FacebookPublisher(config.facebook.page_id, token_mgr, config.instagram.api_version)
        if config.facebook.enabled else None
    )
    threads_pub = None
    if config.threads.enabled:
        from ancnouv.publisher.threads import ThreadsPublisher
        threads_pub = ThreadsPublisher(config.threads.user_id, token_mgr, config.threads.api_version)

    try:
        if not post.image_path:
            raise ValueError(f"Post {post.id} sans image_path")
        image_url = await upload_image(Path(post.image_path), config)

        # Upload vidéo Reel si disponible [SPEC-8, non-bloquant]
        reel_url: str | None = None
        reel_pub = None
        if config.reels.enabled and post.reel_video_path:
            try:
                reel_url = await upload_image(Path(post.reel_video_path), config)
                from ancnouv.publisher.reels import InstagramReelsPublisher
                reel_pub = InstagramReelsPublisher(
                    config.instagram.user_id, token_mgr, config.instagram.api_version
                )
            except Exception as reel_exc:
                logger.error("auto_publish : upload Reel échoué : %s", reel_exc, exc_info=True)
                await notify_all(bot_app.bot, config, f"⚠️ Upload Reel échoué : {reel_exc}")

        async with _get_session() as pub_session:
            pub_post = await pub_session.get(Post, post.id)
            if pub_post is None:
                return
            pub_post.image_public_url = image_url
            await pub_session.commit()
            await publish_to_all_platforms(
                post=pub_post,
                image_url=image_url,
                ig_publisher=ig_pub,
                fb_publisher=fb_pub,
                session=pub_session,
                caption=pub_post.caption,
                max_daily_posts=config.instagram.max_daily_posts,
                threads_publisher=threads_pub,
                reel_video_url=reel_url,
                reel_publisher=reel_pub,
            )
        await notify_all(bot_app.bot, config, f"✅ Post #{post.id} publié automatiquement.")
    except Exception as exc:
        logger.error("job_generate auto_publish : %s", exc, exc_info=True)
        await notify_all(
            bot_app.bot, config,
            f"❌ Erreur publication auto post #{post.id} : {exc}",
        )


# ─── JOB-7 : Publication des posts en file d'attente ───────────────────────────

async def job_publish_queued() -> None:
    """Publie les posts `queued` éligibles selon leur ordre de priorité. [SCHEDULER.md JOB-7, SPEC-7ter]

    Activé en v2 uniquement. [TRANSVERSAL-2]
    """
    try:
        await _job_publish_queued_inner()
    except Exception as exc:
        logger.error("JOB-7 erreur : %s", exc, exc_info=True)


async def _job_publish_queued_inner() -> None:
    from pathlib import Path

    from ancnouv.bot.notifications import notify_all
    from ancnouv.db.models import Post
    from ancnouv.db.session import get_session
    from ancnouv.exceptions import ImageHostingError
    from ancnouv.publisher import publish_to_all_platforms
    from ancnouv.publisher.facebook import FacebookPublisher
    from ancnouv.publisher.image_hosting import upload_image
    from ancnouv.publisher.instagram import InstagramPublisher
    from ancnouv.publisher.token_manager import TokenManager
    from ancnouv.config_loader import get_effective_config
    from ancnouv.scheduler.context import get_bot_app, get_engine

    config = await get_effective_config()
    bot_app = get_bot_app()
    engine = get_engine()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # Récupérer les post_ids éligibles [RF-7ter.6 : scheduled_for <= now() ou NULL]
    async with get_session() as session:
        result = await session.execute(
            text(
                "SELECT id FROM posts "
                "WHERE status = 'queued' "
                "AND (scheduled_for IS NULL OR scheduled_for <= :now) "
                "ORDER BY scheduled_for ASC NULLS LAST, approved_at ASC"
            ),
            {"now": now_utc},
        )
        post_ids = [row[0] for row in result.fetchall()]

    if not post_ids:
        return

    async def _notify(msg: str) -> None:
        await notify_all(bot_app.bot, config, msg)

    token_manager = TokenManager(
        config.meta_app_id,
        config.meta_app_secret,
        api_version=config.instagram.api_version,
        notify_fn=_notify,
    )

    ig_publisher = None
    fb_publisher = None
    threads_publisher = None
    if config.instagram.enabled:
        ig_publisher = InstagramPublisher(
            config.instagram.user_id, token_manager, config.instagram.api_version
        )
    if config.facebook.enabled:
        fb_publisher = FacebookPublisher(
            config.facebook.page_id, token_manager, config.instagram.api_version
        )
    if config.threads.enabled:
        from ancnouv.publisher.threads import ThreadsPublisher
        threads_publisher = ThreadsPublisher(
            config.threads.user_id, token_manager, config.threads.api_version
        )

    for post_id in post_ids:
        # Vérification limite journalière avant chaque post [RF-7ter.3, SC-1]
        if not await check_and_increment_daily_count(engine, config.instagram.max_daily_posts):
            await notify_all(
                bot_app.bot, config,
                "File d'attente : limite journalière atteinte — publication reprise demain."
            )
            return

        async with get_session() as session:
            # Verrou optimiste : transition queued → publishing [JOB-7]
            upd = await session.execute(
                text(
                    "UPDATE posts SET status = 'publishing', updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :id AND status = 'queued'"
                ),
                {"id": post_id},
            )
            if upd.rowcount == 0:
                continue

            post = await session.get(Post, post_id)
            if post is None:
                continue
            await session.refresh(post)
            await session.commit()

            try:
                # Upload feed si pas encore fait [TG-F6]
                if not post.image_public_url:
                    if not post.image_path:
                        raise ValueError(f"Post {post_id} sans image_path")
                    url = await upload_image(Path(post.image_path), config)
                    post.image_public_url = url
                    await session.commit()

                # Upload story si activé [SPEC-7, RF-7.3.3, non-bloquant RF-7.3.6]
                story_image_url: str | None = None
                if config.stories.enabled and post.story_image_path:
                    try:
                        story_image_url = await upload_image(Path(post.story_image_path), config)
                    except ImageHostingError as exc:
                        logger.warning("JOB-7 upload Story échoué (non-bloquant) : %s", exc)

                # Upload vidéo Reel si disponible [SPEC-8, non-bloquant]
                reel_video_url: str | None = None
                reel_pub = None
                if config.reels.enabled and post.reel_video_path:
                    try:
                        reel_video_url = await upload_image(Path(post.reel_video_path), config)
                        from ancnouv.publisher.reels import InstagramReelsPublisher
                        reel_pub = InstagramReelsPublisher(
                            config.instagram.user_id, token_manager, config.instagram.api_version
                        )
                    except ImageHostingError as exc:
                        logger.warning("JOB-7 upload Reel vidéo échoué (non-bloquant) : %s", exc)

                await publish_to_all_platforms(
                    post, post.image_public_url, ig_publisher, fb_publisher, session,
                    story_image_url=story_image_url,
                    threads_publisher=threads_publisher,
                    reel_video_url=reel_video_url,
                    reel_publisher=reel_pub,
                )
                await notify_all(bot_app.bot, config, f"File d'attente : post #{post_id} publié.")
            except Exception as exc:
                logger.error("JOB-7 publication post %s : %s", post_id, exc, exc_info=True)
                post.status = "error"
                post.error_message = str(exc)
                post.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await session.commit()
                await notify_all(
                    bot_app.bot, config,
                    f"File d'attente : échec post #{post_id} : {exc}."
                )


# ─── JOB-4 : Expiration des posts ──────────────────────────────────────────────

async def job_check_expired() -> None:
    """Expire les posts pending_approval dépassant approval_timeout_hours. [SCHEDULER.md JOB-4]

    [SCHEDULER.md] Désactive les boutons via telegram_message_ids.items() + guard is not None.
    Ne jamais itérer sur authorized_user_ids avec lookup (KeyError si envoi initial échoué).
    [SC-M2] Non-persistant (MemoryJobStore) — redémarre à chaque démarrage.
    """
    try:
        await _job_check_expired_inner()
    except Exception as exc:
        logger.error("JOB-4 erreur : %s", exc, exc_info=True)


async def _job_check_expired_inner() -> None:
    from sqlalchemy import select

    from ancnouv.bot.notifications import notify_all
    from ancnouv.db.models import Post
    from ancnouv.db.session import get_session
    from ancnouv.config_loader import get_effective_config
    from ancnouv.scheduler.context import get_bot_app

    config = await get_effective_config()
    bot_app = get_bot_app()

    # SQLite stocke CURRENT_TIMESTAMP en UTC naïf — comparaison avec datetime naïf [SCHEDULER.md]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.scheduler.approval_timeout_hours)
    cutoff_naive = cutoff.replace(tzinfo=None)

    async with get_session() as session:
        expired_posts = (
            await session.execute(
                select(Post).where(
                    Post.status == "pending_approval",
                    Post.created_at < cutoff_naive,
                )
            )
        ).scalars().all()

        for post in expired_posts:
            post.status = "expired"

        if expired_posts:
            await session.commit()

    # Opérations Telegram hors session DB [SC-m7 pattern]
    for post in expired_posts:
        # Désactiver les boutons [SCHEDULER.md — itérer sur .items() avec guard]
        try:
            msg_ids = json.loads(post.telegram_message_ids) if post.telegram_message_ids else {}
        except (json.JSONDecodeError, TypeError):
            msg_ids = {}

        for user_id_str, message_id in msg_ids.items():
            if message_id is None:
                continue
            try:
                await bot_app.bot.edit_message_reply_markup(
                    chat_id=int(user_id_str),
                    message_id=message_id,
                    reply_markup=None,
                )
            except Exception as exc:
                logger.warning(
                    "job_check_expired : désactivation boutons post %d user %s : %s",
                    post.id, user_id_str, exc,
                )

        # Notification avec extrait de légende [TG-F12]
        excerpt = (post.caption or "")[:50]
        await notify_all(
            bot_app.bot, config,
            f"⚠️ Post expiré sans validation : \"{excerpt}\"",
        )


# ─── JOB-5 : Surveillance token Meta ───────────────────────────────────────────

async def job_check_token() -> None:
    """Vérifie l'expiration du token Meta et envoie les alertes progressives.

    [SCHEDULER.md JOB-5, TRANSVERSAL-4]
    Anti-doublon : MetaToken.last_alert_days_threshold + scheduler_state.token_alert_level.
    [SC-M4] Appelé aussi directement dans main_async() avant scheduler.start().
    """
    try:
        await _job_check_token_inner()
    except Exception as exc:
        logger.error("JOB-5 erreur : %s", exc, exc_info=True)


async def _job_check_token_inner() -> None:
    from sqlalchemy import select

    from ancnouv.bot.notifications import notify_all
    from ancnouv.db.models import MetaToken
    from ancnouv.db.session import get_session
    from ancnouv.db.utils import get_scheduler_state, set_scheduler_state
    from ancnouv.publisher.token_manager import (
        TokenManager,
        days_until_expiry,
        get_alert_threshold,
    )
    from ancnouv.config_loader import get_effective_config
    from ancnouv.scheduler.context import get_bot_app

    config = await get_effective_config()
    bot_app = get_bot_app()

    # Mapping seuil numérique → niveau lisible [SCHEDULER.md — table scheduler_state]
    _LEVEL_MAP: dict[int, str] = {30: "30j", 14: "14j", 7: "7j", 3: "3j", 1: "1j", 0: "expired"}

    async with get_session() as session:
        token = (
            await session.execute(
                select(MetaToken).where(MetaToken.token_kind == "user_long")
            )
        ).scalar_one_or_none()

        if token is None:
            logger.debug("job_check_token : aucun token user_long en DB — ignoré")
            return

        if token.expires_at is None:
            logger.warning("job_check_token : token user_long sans expires_at")
            return

        remaining = days_until_expiry(token.expires_at)
        threshold = get_alert_threshold(remaining)

        # Pas d'alerte ce jour
        if threshold is None:
            return

        # Anti-doublon par comparaison de seuil [TRANSVERSAL-4]
        if threshold == token.last_alert_days_threshold:
            return

        level_str = _LEVEL_MAP.get(threshold, str(threshold))

        # Seuils 7j et 3j : tenter refresh automatique
        refresh_ok = False
        if threshold in (7, 3):
            try:
                token_mgr = TokenManager(
                    meta_app_id=config.meta_app_id,
                    meta_app_secret=config.meta_app_secret,
                    api_version=config.instagram.api_version,
                )
                await token_mgr.get_valid_token(session)
                refresh_ok = True
                logger.info("job_check_token : refresh automatique réussi (J-%d)", threshold)
            except Exception as exc:
                logger.warning(
                    "job_check_token : refresh automatique échoué (J-%d) : %s", threshold, exc
                )

        # Seuil 1j ou expiré + refresh échoue → suspendre les publications
        if threshold <= 1 and not refresh_ok:
            await set_scheduler_state(session, "publications_suspended", "true")
            logger.error(
                "job_check_token : token expiré ou J-1 sans refresh — publications suspendues"
            )

        # Mise à jour anti-doublon [TRANSVERSAL-4]
        token.last_alert_days_threshold = threshold
        token.last_alert_sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await set_scheduler_state(session, "token_alert_level", level_str)
        await session.commit()

    # Notification Telegram (hors session)
    if threshold == 0:
        msg = (
            "🚨 Token Meta EXPIRÉ — publications suspendues.\n"
            "Relancer `python -m ancnouv auth meta` immédiatement."
        )
    elif threshold == 1:
        suspend = " — publications SUSPENDUES" if not refresh_ok else ""
        msg = (
            f"🚨 Token Meta expire demain (J-1){suspend}.\n"
            "Relancer `python -m ancnouv auth meta`."
        )
    elif threshold in (7, 3):
        refresh_note = " Refresh automatique réussi." if refresh_ok else " ⚠️ Refresh échoué."
        msg = f"⚠️ Token Meta : {threshold} jours restants.{refresh_note}"
    else:
        msg = f"ℹ️ Token Meta : {threshold} jours restants."

    await notify_all(bot_app.bot, config, msg)


# ─── JOB-6 : Nettoyage des fichiers ────────────────────────────────────────────

async def job_cleanup() -> None:
    """Supprime les images des posts terminés selon image_retention_days. [SCHEDULER.md JOB-6]

    [TRANSVERSAL-5] Clé config : content.image_retention_days (défaut 7j, pas scheduler).
    FileNotFoundError → ignorer silencieusement, écrire image_path=NULL [SCHEDULER.md].
    [DB-8] Utilise created_at (impact /retry_ig si image nettoyée avant publication).
    """
    try:
        await _job_cleanup_inner()
    except Exception as exc:
        logger.error("JOB-6 erreur : %s", exc, exc_info=True)


async def _job_cleanup_inner() -> None:
    from sqlalchemy import select

    from ancnouv.db.models import Post
    from ancnouv.db.session import get_session
    from ancnouv.config_loader import get_effective_config

    config = await get_effective_config()
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.content.image_retention_days)
    cutoff_naive = cutoff.replace(tzinfo=None)

    _CLEANUP_STATUSES = ("published", "rejected", "expired", "skipped")

    async with get_session() as session:
        old_posts = (
            await session.execute(
                select(Post).where(
                    Post.status.in_(_CLEANUP_STATUSES),
                    Post.image_path.is_not(None),
                    Post.created_at < cutoff_naive,
                )
            )
        ).scalars().all()

        freed_bytes = 0
        cleaned = 0

        for post in old_posts:
            if not post.image_path:
                continue
            path = Path(post.image_path)
            try:
                size = path.stat().st_size
                path.unlink()
                freed_bytes += size
                cleaned += 1
            except FileNotFoundError:
                # Fichier déjà absent — cas normal après crash ou suppression manuelle [SCHEDULER.md]
                logger.debug("job_cleanup : fichier déjà absent %s — OK", path)
            except OSError as exc:
                logger.warning("job_cleanup : impossible de supprimer %s : %s", path, exc)
            finally:
                # image_path = NULL dans tous les cas [SCHEDULER.md]
                post.image_path = None

        if old_posts:
            await session.commit()

    if cleaned > 0:
        freed_mb = freed_bytes / (1024 * 1024)
        logger.info(
            "job_cleanup : %d fichier(s) supprimé(s), %.1f Mo libérés", cleaned, freed_mb
        )


# ─── JOB-8 : Collecte BnF Gallica ──────────────────────────────────────────────

async def job_fetch_gallica() -> None:
    """Collecte et stocke les fascicules BnF Gallica pour le jour courant. [SPEC-9.3]"""
    try:
        await _job_fetch_gallica_inner()
    except Exception as exc:
        logger.error("JOB-8 erreur : %s", exc, exc_info=True)


async def _job_fetch_gallica_inner() -> None:
    from datetime import date

    from ancnouv.db.session import get_session
    from ancnouv.fetchers.gallica import GallicaFetcher
    from ancnouv.scheduler.context import get_config

    config = get_config()

    if not config.gallica.enabled:
        return

    today = date.today()
    fetcher = GallicaFetcher(max_results=config.gallica.max_results_per_year * 5)

    items = await fetcher.fetch(today.month, today.day)
    if not items:
        logger.debug("JOB-8 : aucun fascicule Gallica trouvé pour le %02d-%02d.", today.month, today.day)
        return

    async with get_session() as session:
        count = await fetcher.store(items, session)

    logger.info("JOB-8 : %d fascicule(s) Gallica stocké(s) pour le %02d-%02d.", count, today.month, today.day)
