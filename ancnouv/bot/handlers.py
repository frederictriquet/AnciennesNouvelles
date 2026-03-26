# Handlers bot Telegram — Phase 5 [SPEC-3.3, docs/TELEGRAM_BOT.md]
from __future__ import annotations

import functools
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from ancnouv.bot.notifications import notify_all, send_approval_request
from ancnouv.db.models import Event, Post, RssArticle
from ancnouv.db.session import get_session
from ancnouv.db.utils import get_scheduler_state, set_scheduler_state

if TYPE_CHECKING:
    from ancnouv.config import Config

logger = logging.getLogger(__name__)

# État de la conversation d'édition de légende [TELEGRAM_BOT.md]
WAITING_CAPTION = 1

# Mapping token_alert_level → texte affiché [TELEGRAM_BOT.md — cmd_status]
_TOKEN_LEVEL_LABELS: dict[str | None, str] = {
    None: "normal",
    "normal": "normal",
    "30j": "attention — 30 jours",
    "14j": "avertissement — 14 jours",
    "7j": "alerte — 7 jours",
    "3j": "CRITIQUE — 3 jours",
    "1j": "EXPIRÉ DEMAIN",
    "expired": "EXPIRÉ — publications suspendues",
}


# ---------------------------------------------------------------------------
# Décorateur d'autorisation [TELEGRAM_BOT.md]
# ---------------------------------------------------------------------------

def authorized_only(handler):
    """Vérifie que l'utilisateur est dans authorized_user_ids. [TELEGRAM_BOT.md]

    @functools.wraps obligatoire pour préserver __name__ utilisé par le routage PTB.
    update.effective_message fonctionne pour les messages ET les callbacks.
    """
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        config: Config | None = context.bot_data.get("config")
        if config is None:
            return
        user = update.effective_user
        if user is None or user.id not in config.telegram.authorized_user_ids:
            if update.effective_message:
                await update.effective_message.reply_text("Accès non autorisé.")
            return
        return await handler(update, context)
    return wrapper


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

def _get_next_run_str(config: "Config") -> str:
    """Retourne l'heure du prochain déclenchement de job_generate. [TG-M3]

    Retourne "—" si le scheduler n'est pas initialisé ou le job introuvable.
    """
    try:
        from zoneinfo import ZoneInfo

        from ancnouv.scheduler.context import get_scheduler
        sched = get_scheduler()
        job = sched.get_job("job_generate")
        if job and job.next_run_time:
            tz = ZoneInfo(config.scheduler.timezone)
            return job.next_run_time.astimezone(tz).strftime("%Hh%M")
    except Exception:
        pass
    return "—"


async def _publish_approved_post(
    post: Post,
    session,
    bot,
    config: "Config",
) -> None:
    """Délègue upload + publication + notifications système. [TELEGRAM_BOT.md]

    Guard image_path NULL : géré par handle_approve (fait foi) — non vérifié ici.
    TG-F6 : idempotence sur image_public_url (ne re-uploade pas si déjà renseigné).
    En cas d'ImageHostingError : notify_all + retour sans changer le statut.
    """
    from pathlib import Path

    from ancnouv.exceptions import ImageHostingError
    from ancnouv.publisher import publish_to_all_platforms
    from ancnouv.publisher.image_hosting import upload_image
    from ancnouv.publisher.token_manager import TokenManager

    # Étape 1 : upload feed si non encore fait [TG-F6]
    if not post.image_public_url:
        try:
            assert post.image_path is not None  # Garanti par handle_approve [TG-F6]
            url = await upload_image(Path(post.image_path), config)
            post.image_public_url = url
            await session.commit()
        except ImageHostingError as exc:
            logger.error("Upload image échoué : %s", exc)
            await notify_all(
                bot, config,
                "Upload image échoué après 3 tentatives. Vérifier le service d'hébergement."
            )
            return

    # Upload story si activé et image disponible [SPEC-7, RF-7.3.3]
    story_image_url: str | None = None
    if config.stories.enabled and post.story_image_path:
        try:
            story_image_url = await upload_image(Path(post.story_image_path), config)
        except ImageHostingError as exc:
            # Non-bloquant [RF-7.3.6] : on continue sans Story
            logger.warning("Upload Story échoué (non-bloquant) : %s", exc)

    # Upload vidéo Reel si disponible [SPEC-8]
    reel_video_url: str | None = None
    if config.reels.enabled and post.reel_video_path:
        try:
            reel_video_url = await upload_image(Path(post.reel_video_path), config)
        except ImageHostingError as exc:
            logger.warning("Upload Reel vidéo échoué (non-bloquant) : %s", exc)

    # Étape 2 : instancier les publishers
    async def _notify(msg: str) -> None:
        await notify_all(bot, config, msg)

    token_manager = TokenManager(
        config.meta_app_id,
        config.meta_app_secret,
        api_version=config.instagram.api_version,
        notify_fn=_notify,
    )

    ig_publisher = None
    fb_publisher = None
    threads_publisher = None
    reel_publisher = None

    if config.instagram.enabled:
        from ancnouv.publisher.instagram import InstagramPublisher
        ig_publisher = InstagramPublisher(
            config.instagram.user_id,
            token_manager,
            config.instagram.api_version,
        )

    if config.facebook.enabled:
        from ancnouv.publisher.facebook import FacebookPublisher
        fb_publisher = FacebookPublisher(
            config.facebook.page_id,
            token_manager,
            config.instagram.api_version,
        )

    if config.threads.enabled:
        from ancnouv.publisher.threads import ThreadsPublisher
        threads_publisher = ThreadsPublisher(
            config.threads.user_id,
            token_manager,
            config.threads.api_version,
        )

    if config.reels.enabled and reel_video_url:
        from ancnouv.publisher.reels import InstagramReelsPublisher
        reel_publisher = InstagramReelsPublisher(
            config.instagram.user_id,
            token_manager,
            config.instagram.api_version,
        )

    # Étape 3 : publication feed + stories + threads + reels [SPEC-7, SPEC-8, SPEC-9.1]
    results = await publish_to_all_platforms(
        post, post.image_public_url, ig_publisher, fb_publisher, session,
        story_image_url=story_image_url,
        threads_publisher=threads_publisher,
        reel_video_url=reel_video_url,
        reel_publisher=reel_publisher,
    )

    # Étape 4 : notification résultat [TELEGRAM_BOT.md — tableau notifications]
    ig_ok = results.get("instagram") is not None

    if post.status == "published":
        if post.instagram_error and post.facebook_error:
            await notify_all(
                bot, config,
                f"Échec publication Instagram + Facebook : {post.error_message}. "
                "Post conservé pour retry."
            )
        elif post.instagram_error:
            await notify_all(
                bot, config,
                f"Facebook publié, Instagram échoué : {post.instagram_error}. "
                "Utiliser /retry_ig pour retenter."
            )
        elif post.facebook_error:
            await notify_all(
                bot, config,
                f"Instagram publié, Facebook échoué : {post.facebook_error}. "
                "Utiliser /retry_fb pour retenter."
            )
        else:
            parts = []
            if results.get("instagram"):
                parts.append(f"Instagram : {results['instagram']}")
            if results.get("facebook"):
                fb_id = results["facebook"]
                # post_id format : {page_id}_{object_id}
                if "_" in fb_id:
                    page_id, obj_id = fb_id.split("_", 1)
                    fb_url = f"https://www.facebook.com/{page_id}/posts/{obj_id}"
                else:
                    fb_url = f"https://www.facebook.com/{fb_id}"
                parts.append(f"Facebook : {fb_url}")
            if post.story_post_id:
                story_id = post.story_post_id
                if "_" in story_id:
                    pg, obj = story_id.split("_", 1)
                    parts.append(f"Story FB : https://www.facebook.com/{pg}/posts/{obj}")
                else:
                    parts.append(f"Story IG : {story_id}")
            msg = "Publié — " + " | ".join(parts) if parts else "✓ Publié avec succès."
            await notify_all(bot, config, msg)
    else:
        # status = "error"
        await notify_all(
            bot, config,
            f"Échec publication Instagram + Facebook : {post.error_message}. "
            "Post conservé pour retry."
        )


async def _retry_single_platform(
    post: Post,
    platform: str,
    session,
    bot,
    config: "Config",
) -> None:
    """Retente la publication sur une seule plateforme. [TELEGRAM_BOT.md — TG-F16]

    platform : "instagram" ou "facebook".
    post.status reste 'published' dans tous les cas.
    """
    from ancnouv.publisher.token_manager import TokenManager

    async def _notify(msg: str) -> None:
        await notify_all(bot, config, msg)

    token_manager = TokenManager(
        config.meta_app_id,
        config.meta_app_secret,
        api_version=config.instagram.api_version,
        notify_fn=_notify,
    )

    try:
        from ancnouv.publisher.instagram import InstagramPublisher
        from ancnouv.publisher.facebook import FacebookPublisher

        publisher: InstagramPublisher | FacebookPublisher
        if platform == "instagram":
            publisher = InstagramPublisher(
                config.instagram.user_id,
                token_manager,
                config.instagram.api_version,
            )
        else:
            publisher = FacebookPublisher(
                config.facebook.page_id,
                token_manager,
                config.instagram.api_version,
            )

        async with get_session() as pub_session:
            post_id_result = await publisher.publish(
                post, post.image_public_url, post.caption, pub_session
            )

        # Succès : mettre à jour le post_id et effacer l'erreur
        if platform == "instagram":
            post.instagram_post_id = post_id_result
            post.instagram_error = None
        else:
            post.facebook_post_id = post_id_result
            post.facebook_error = None
        post.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()

        await notify_all(
            bot, config,
            f"Retry {platform} réussi. Post publié avec succès."
        )

    except Exception as exc:
        # Échec : enregistrer l'erreur, status reste 'published'
        if platform == "instagram":
            post.instagram_error = str(exc)
        else:
            post.facebook_error = str(exc)
        post.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
        logger.error("Retry %s échoué pour post %s : %s", platform, post.id, exc)


# ---------------------------------------------------------------------------
# Handlers de commandes [SPEC-3.3.6]
# ---------------------------------------------------------------------------

@authorized_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Message de bienvenue + état courant. [TELEGRAM_BOT.md — cmd_start, RF-3.3.5]"""
    config: Config = context.bot_data["config"]

    async with get_session() as session:
        paused = await get_scheduler_state(session, "paused")
        result = await session.execute(
            text("SELECT COUNT(*) FROM posts WHERE status = 'pending_approval'")
        )
        pending = result.scalar() or 0

    scheduler_status = "en pause" if paused == "true" else "actif"
    next_run = _get_next_run_str(config)

    await update.effective_message.reply_text(
        f"Anciennes Nouvelles démarré ✓\n"
        f"Scheduler : {scheduler_status} | Prochain post : {next_run}\n"
        f"Posts en attente : {pending}\n"
        f"Tapez /help pour la liste des commandes."
    )


@authorized_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Liste statique des commandes. [TELEGRAM_BOT.md — cmd_help]"""
    await update.effective_message.reply_text(
        "/status — État détaillé du système\n"
        "/force  — Générer un post immédiatement\n"
        "/pause  — Suspendre la génération automatique\n"
        "/resume — Reprendre la génération automatique\n"
        "/pending — Posts en attente d'approbation\n"
        "/stats  — Statistiques\n"
        "/retry  — Retenter le dernier post en erreur\n"
        "/retry_ig | /retry_fb — Retry plateforme seule"
    )


@authorized_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """État détaillé du système. [TELEGRAM_BOT.md — cmd_status, TG-M9]"""
    config: Config = context.bot_data["config"]

    async with get_session() as session:
        paused = await get_scheduler_state(session, "paused")
        daily_count_str = await get_scheduler_state(session, "daily_post_count") or "0"
        token_level = await get_scheduler_state(session, "token_alert_level")

        pending_result = await session.execute(
            text("SELECT COUNT(*) FROM posts WHERE status = 'pending_approval'")
        )
        pending = pending_result.scalar() or 0

        last_result = await session.execute(
            text(
                "SELECT published_at FROM posts "
                "WHERE status = 'published' AND published_at IS NOT NULL "
                "ORDER BY published_at DESC LIMIT 1"
            )
        )
        last_row = last_result.fetchone()

    scheduler_status = "en pause" if paused == "true" else "actif"
    next_run = _get_next_run_str(config)

    if last_row and last_row[0]:
        try:
            last_dt = datetime.fromisoformat(str(last_row[0]))
        except ValueError:
            last_dt = None
        last_published = last_dt.strftime("%d/%m/%Y à %H:%M") if last_dt else "—"
    else:
        last_published = "—"

    token_label = _TOKEN_LEVEL_LABELS.get(token_level, token_level or "normal")

    await update.effective_message.reply_text(
        f"Anciennes Nouvelles — État\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Scheduler : {scheduler_status} | Prochain post : {next_run}\n"
        f"Posts aujourd'hui : {daily_count_str} / {config.instagram.max_daily_posts}\n"
        f"Posts en attente : {pending}\n"
        f"Dernier publié : {last_published}\n"
        f"Token Meta : {token_label}"
    )


@authorized_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Statistiques de publication. [TELEGRAM_BOT.md — cmd_stats, TG-F13]"""
    seven_days_ago = (
        datetime.now(timezone.utc) - timedelta(days=7)
    ).replace(tzinfo=None)

    async with get_session() as session:
        pub_r = await session.execute(
            text("SELECT COUNT(*) FROM posts WHERE status = 'published'")
        )
        published = pub_r.scalar() or 0

        rej_r = await session.execute(
            text("SELECT COUNT(*) FROM posts WHERE status = 'rejected'")
        )
        rejected = rej_r.scalar() or 0

        week_r = await session.execute(
            text(
                "SELECT COUNT(*) FROM posts "
                "WHERE status = 'published' AND published_at >= :since"
            ),
            {"since": seven_days_ago},
        )
        week_count = week_r.scalar() or 0

    # Guard division par zéro [TG-F13]
    total = published + rejected
    approval_rate = f"{published / total * 100:.0f}%" if total > 0 else "N/A"

    await update.effective_message.reply_text(
        f"Statistiques Anciennes Nouvelles\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Total publié : {published}\n"
        f"Total rejeté : {rejected}\n"
        f"Posts (7 derniers jours) : {week_count}\n"
        f"Taux d'approbation : {approval_rate}"
    )


@authorized_only
async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Liste les posts en attente d'approbation. [TELEGRAM_BOT.md — cmd_pending, TG-F7]"""
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    async with get_session() as session:
        result = await session.execute(
            text(
                "SELECT id, caption, created_at, telegram_message_ids "
                "FROM posts WHERE status = 'pending_approval' "
                "ORDER BY created_at ASC"
            )
        )
        rows = result.fetchall()

    if not rows:
        await update.effective_message.reply_text("Aucun post en attente.")
        return

    lines = [f"Posts en attente ({len(rows)}) :"]
    for row in rows:
        post_id, caption, created_at_raw, msg_ids_raw = row

        # Calcul âge via total_seconds [TELEGRAM_BOT.md — ne pas utiliser .seconds]
        try:
            if isinstance(created_at_raw, str):
                created_at = datetime.fromisoformat(created_at_raw)
            else:
                created_at = created_at_raw
            delta = now_utc - created_at
            hours = int(delta.total_seconds()) // 3600
        except Exception:
            hours = 0

        extract = (caption or "")[:50]

        # Lien Telegram direct [TG-F7]
        try:
            msg_ids: dict = json.loads(msg_ids_raw or "{}")
        except (json.JSONDecodeError, TypeError):
            msg_ids = {}

        if msg_ids:
            uid, mid = next(iter(msg_ids.items()))
            link = f"t.me/c/{abs(int(uid))}/{mid}"
        else:
            link = "(message non retrouvé)"

        lines.append(f'• #{post_id} — il y a {hours}h — "{extract}..." — {link}')

    await update.effective_message.reply_text("\n".join(lines))


@authorized_only
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Met le scheduler en pause. [TELEGRAM_BOT.md — cmd_pause, SPEC-3.5.4]"""
    async with get_session() as session:
        await set_scheduler_state(session, "paused", "true")
    await update.effective_message.reply_text("Scheduler mis en pause.")


@authorized_only
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reprend le scheduler. [TELEGRAM_BOT.md — cmd_resume, SPEC-3.5.4]"""
    async with get_session() as session:
        await set_scheduler_state(session, "paused", "false")
    await update.effective_message.reply_text("Scheduler repris.")


@authorized_only
async def cmd_force(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Génère un post immédiatement hors cycle. [TELEGRAM_BOT.md — cmd_force, SC-8]

    Bypass max_pending_posts (pas de vérification avant generate_post).
    Ne bypass pas la limite journalière (vérifiée lors de l'approbation).
    Imports inline pour éviter les imports circulaires. [TELEGRAM_BOT.md]
    """
    # Import inline [TELEGRAM_BOT.md]
    from ancnouv.generator import generate_post

    config: Config = context.bot_data["config"]

    # Session unique pour generate_post + send_approval_request (post attaché à la session)
    async with get_session() as session:
        post = await generate_post(session)

        if post is None:
            await update.effective_message.reply_text(
                "Aucun événement disponible — base de données épuisée "
                "ou tous les candidats exclus par les filtres."
            )
            return

        await send_approval_request(post, context.bot, session, config)

    await update.effective_message.reply_text("Post généré et envoyé en approbation.")


@authorized_only
async def cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retente la publication du dernier post en statut error. [TELEGRAM_BOT.md — cmd_retry]

    Verrou optimiste : UPDATE ... WHERE status='error'.
    Si 0 lignes → retry parallèle, retour silencieux. [TELEGRAM_BOT.md]
    """
    config: Config = context.bot_data["config"]

    async with get_session() as session:
        # Sélection du dernier post en erreur
        result = await session.execute(
            text(
                "SELECT id FROM posts WHERE status = 'error' "
                "ORDER BY updated_at DESC LIMIT 1"
            )
        )
        row = result.fetchone()
        if not row:
            await update.effective_message.reply_text("Aucun post en erreur.")
            return
        post_id = row[0]

        post = await session.get(Post, post_id)
        if post is None:
            await update.effective_message.reply_text("Aucun post en erreur.")
            return

        # Verrou optimiste [TELEGRAM_BOT.md]
        upd = await session.execute(
            text(
                "UPDATE posts SET "
                "  status = 'publishing', "
                "  retry_count = retry_count + 1, "
                "  updated_at = CURRENT_TIMESTAMP "
                "WHERE id = :id AND status = 'error'"
            ),
            {"id": post_id},
        )
        if upd.rowcount == 0:
            # Retry parallèle a déjà pris le verrou — retour silencieux
            return

        # Synchroniser l'ORM après le UPDATE SQL brut [TELEGRAM_BOT.md]
        await session.refresh(post)
        post.error_message = None
        await session.commit()

        await _publish_approved_post(post, session, context.bot, config)

    result_status = post.status
    if result_status == "published":
        await update.effective_message.reply_text("Post republié avec succès.")
    else:
        await update.effective_message.reply_text(
            f"Échec du retry : {post.error_message}. Post en statut {result_status}."
        )


@authorized_only
async def cmd_retry_ig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retente Instagram uniquement sur le dernier post published avec instagram_error. [TG-F16]"""
    config: Config = context.bot_data["config"]

    async with get_session() as session:
        result = await session.execute(
            text(
                "SELECT id FROM posts "
                "WHERE status = 'published' AND instagram_error IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 1"
            )
        )
        row = result.fetchone()
        if not row:
            await update.effective_message.reply_text(
                "Aucun post avec erreur Instagram en attente de retry."
            )
            return

        post = await session.get(Post, row[0])
        if post is None:
            return

        post.retry_count += 1
        post.instagram_error = None
        await session.commit()

        await _retry_single_platform(post, "instagram", session, context.bot, config)

    if post.instagram_error is None:
        await update.effective_message.reply_text("Retry Instagram réussi.")
    else:
        await update.effective_message.reply_text(
            f"Retry Instagram échoué : {post.instagram_error}"
        )


@authorized_only
async def cmd_retry_fb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retente Facebook uniquement sur le dernier post published avec facebook_error. [TG-F16]"""
    config: Config = context.bot_data["config"]

    async with get_session() as session:
        result = await session.execute(
            text(
                "SELECT id FROM posts "
                "WHERE status = 'published' AND facebook_error IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 1"
            )
        )
        row = result.fetchone()
        if not row:
            await update.effective_message.reply_text(
                "Aucun post avec erreur Facebook en attente de retry."
            )
            return

        post = await session.get(Post, row[0])
        if post is None:
            return

        post.retry_count += 1
        post.facebook_error = None
        await session.commit()

        await _retry_single_platform(post, "facebook", session, context.bot, config)

    if post.facebook_error is None:
        await update.effective_message.reply_text("Retry Facebook réussi.")
    else:
        await update.effective_message.reply_text(
            f"Retry Facebook échoué : {post.facebook_error}"
        )


# ---------------------------------------------------------------------------
# Workflow d'approbation [SPEC-3.3.2]
# ---------------------------------------------------------------------------

@authorized_only
async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approuve et publie un post. [TELEGRAM_BOT.md — handle_approve, TG-F5, TG-F10]

    Verrou optimiste contre double-clic / deux admins simultanés.
    Imports inline check_and_increment_daily_count / get_engine pour éviter
    les imports circulaires. [TELEGRAM_BOT.md]
    """
    query = update.callback_query
    await query.answer()

    post_id = int(query.data.split(":")[1])
    config: Config = context.bot_data["config"]

    async with get_session() as session:
        post = await session.get(Post, post_id)
        if post is None or post.status != "pending_approval":
            try:
                await query.edit_message_text("Ce post n'est plus disponible.")
            except Exception:
                pass
            return

        # Verrou optimiste [TG-F5] : un seul admin peut approuver
        upd = await session.execute(
            text(
                "UPDATE posts SET "
                "  status = 'approved', "
                "  updated_at = CURRENT_TIMESTAMP "
                "WHERE id = :id AND status = 'pending_approval'"
            ),
            {"id": post_id},
        )
        if upd.rowcount == 0:
            # Autre admin a déjà approuvé — retour silencieux [TG-F5]
            return

        # Synchroniser l'ORM [TELEGRAM_BOT.md — handle_approve étape 3]
        await session.refresh(post)
        post.approved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()

        # Guard image_path NULL [TELEGRAM_BOT.md — handle_approve étape 5]
        if post.image_path is None:
            try:
                await query.edit_message_text(
                    "Image introuvable — post non publiable."
                )
            except Exception:
                pass
            return

        # Vérification limite journalière [TELEGRAM_BOT.md — handle_approve étape 6]
        # Imports inline pour éviter les imports circulaires [TELEGRAM_BOT.md]
        try:
            from ancnouv.scheduler.context import get_engine
            from ancnouv.scheduler.jobs import check_and_increment_daily_count

            can_publish = await check_and_increment_daily_count(
                get_engine(), config.instagram.max_daily_posts
            )
        except RuntimeError:
            # Engine non initialisé (Phase 6 non démarrée) — skip du check journalier
            can_publish = True

        if not can_publish:
            post.status = "queued"
            await session.commit()
            try:
                await query.edit_message_text(
                    "⚠️ Limite journalière atteinte "
                    f"({config.instagram.max_daily_posts} posts). "
                    "Post mis en file d'attente. "
                    "En v1, déblocage manuel requis : lancer /retry demain ou exécuter "
                    "directement UPDATE posts SET status='approved' WHERE status='queued' "
                    "puis /retry."
                )
            except Exception:
                pass
            return

        # Publication [TELEGRAM_BOT.md — handle_approve étape 7]
        await _publish_approved_post(post, session, context.bot, config)

        # Retrait boutons chez TOUS les admins [TG-F10]
        try:
            msg_ids: dict = json.loads(post.telegram_message_ids or "{}")
        except (json.JSONDecodeError, TypeError):
            msg_ids = {}

        for uid, mid in msg_ids.items():
            if mid is not None:
                try:
                    await context.bot.edit_message_reply_markup(
                        chat_id=int(uid),
                        message_id=mid,
                        reply_markup=None,
                    )
                except Exception:
                    pass

        # Résultat sur le message de l'admin courant [TG-F10]
        if post.status == "published":
            if post.instagram_error and post.facebook_error:
                result_text = (
                    f"Publié partiellement.\n"
                    f"Instagram : {post.instagram_error}\n"
                    f"Facebook : {post.facebook_error}"
                )
            elif post.instagram_error:
                result_text = (
                    f"Facebook publié, Instagram échoué : {post.instagram_error}. "
                    "Utiliser /retry_ig."
                )
            elif post.facebook_error:
                result_text = (
                    f"Instagram publié, Facebook échoué : {post.facebook_error}. "
                    "Utiliser /retry_fb."
                )
            else:
                result_text = "✓ Publié avec succès."
        elif post.status == "approved":
            # Upload échoué, post reste approved pour retry
            result_text = "Upload image échoué. Post conservé pour retry."
        elif post.status == "error":
            result_text = f"Échec publication : {post.error_message}. Utiliser /retry."
        else:
            result_text = f"Statut : {post.status}"

        try:
            await query.edit_message_text(result_text)
        except Exception:
            pass


@authorized_only
async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rejette un post et bloque la source. [TELEGRAM_BOT.md — handle_reject, SPEC-3.3.2]"""
    query = update.callback_query
    # Acquittement immédiat [TELEGRAM_BOT.md — délai max 10s]
    await query.answer()

    post_id = int(query.data.split(":")[1])

    async with get_session() as session:
        post = await session.get(Post, post_id)
        if post is None or post.status != "pending_approval":
            try:
                await query.edit_message_text("Ce post n'est plus disponible.")
            except Exception:
                pass
            return

        post.status = "rejected"

        # Bloquer la source [TELEGRAM_BOT.md — handle_reject, ARCH-I4]
        if post.article_id is not None:
            article = await session.get(RssArticle, post.article_id)
            if article:
                article.status = "blocked"
        elif post.event_id is not None:
            event = await session.get(Event, post.event_id)
            if event:
                event.status = "blocked"

        await session.commit()

    try:
        await query.edit_message_text("Post rejeté.")
    except Exception:
        pass


@authorized_only
async def handle_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ignore le post et génère le suivant. [TELEGRAM_BOT.md — handle_skip, SPEC-3.3.2, SPEC-B2]

    Nouvelle session pour generate_post après le commit du skip. [TELEGRAM_BOT.md]
    """
    query = update.callback_query
    await query.answer()

    post_id = int(query.data.split(":")[1])
    config: Config = context.bot_data["config"]

    async with get_session() as session:
        post = await session.get(Post, post_id)
        if post is None or post.status != "pending_approval":
            try:
                await query.edit_message_text("Ce post n'est plus disponible.")
            except Exception:
                pass
            return

        # Séquençage : commit du skip AVANT generate_post [SPEC-3.3.2]
        post.status = "skipped"
        await session.commit()

    try:
        await query.edit_message_text("Post ignoré.")
    except Exception:
        pass

    # Nouvelle session pour generate_post + send_approval_request [TELEGRAM_BOT.md]
    # Ne pas réutiliser la session du skip (anti-pattern session longue).
    # Session unique pour generate + send (post doit rester attaché à la même session).
    from ancnouv.generator import generate_post

    async with get_session() as new_session:
        new_post = await generate_post(new_session)

        if new_post is None:
            await notify_all(
                context.bot, config,
                "Aucun autre événement disponible."
            )
            return

        await send_approval_request(new_post, context.bot, new_session, config)


@authorized_only
async def handle_queue_it(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ajoute un post à la file d'attente. [SPEC-7ter, RF-7ter.1, RF-7ter.2, RF-7ter.5]"""
    query = update.callback_query
    await query.answer()

    post_id = int(query.data.split(":")[1])
    config: Config = context.bot_data["config"]

    async with get_session() as session:
        post = await session.get(Post, post_id)
        if post is None or post.status != "pending_approval":
            try:
                await query.edit_message_text("Ce post n'est plus disponible.")
            except Exception:
                pass
            return

        # Guard taille max de la file [RF-7ter.5]
        queue_count_result = await session.execute(
            text("SELECT COUNT(*) FROM posts WHERE status = 'queued'")
        )
        queue_count = queue_count_result.scalar() or 0
        if queue_count >= config.scheduler.max_queue_size:
            try:
                await query.edit_message_text(
                    f"File d'attente pleine ({queue_count}/{config.scheduler.max_queue_size}). "
                    "Attendez la publication d'un post avant d'en ajouter un nouveau."
                )
            except Exception:
                pass
            return

        # Verrou optimiste [TG-F5]
        upd = await session.execute(
            text(
                "UPDATE posts SET "
                "  status = 'queued', "
                "  approved_at = CURRENT_TIMESTAMP, "
                "  updated_at = CURRENT_TIMESTAMP "
                "WHERE id = :id AND status = 'pending_approval'"
            ),
            {"id": post_id},
        )
        if upd.rowcount == 0:
            return

        await session.refresh(post)
        await session.commit()

        # Position dans la file (parmi les posts queued triés par approved_at)
        pos_result = await session.execute(
            text(
                "SELECT COUNT(*) FROM posts WHERE status = 'queued' "
                "AND approved_at <= :approved_at"
            ),
            {"approved_at": post.approved_at},
        )
        position = pos_result.scalar() or 1

    # Retrait boutons chez tous les admins [TG-F10]
    try:
        msg_ids: dict = json.loads(post.telegram_message_ids or "{}")
    except (json.JSONDecodeError, TypeError):
        msg_ids = {}

    for uid, mid in msg_ids.items():
        if mid is not None:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=int(uid),
                    message_id=mid,
                    reply_markup=None,
                )
            except Exception:
                pass

    try:
        await query.edit_message_text(
            f"Ajouté à la file d'attente (position {position})."
        )
    except Exception:
        pass


@authorized_only
async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Affiche la file d'attente avec heures estimées. [SPEC-7ter, RF-7ter.4]"""
    from zoneinfo import ZoneInfo

    config: Config = context.bot_data["config"]

    async with get_session() as session:
        result = await session.execute(
            text(
                "SELECT id, caption, approved_at, scheduled_for "
                "FROM posts WHERE status = 'queued' "
                "ORDER BY scheduled_for ASC NULLS LAST, approved_at ASC"
            )
        )
        rows = result.fetchall()

    if not rows:
        await update.effective_message.reply_text("File d'attente vide.")
        return

    tz = ZoneInfo(config.scheduler.timezone)
    lines = [f"File d'attente ({len(rows)}/{config.scheduler.max_queue_size}) :"]
    for i, row in enumerate(rows, 1):
        post_id, caption, approved_at_raw, scheduled_for_raw = row
        extract = (caption or "")[:40]
        if scheduled_for_raw:
            try:
                sf = datetime.fromisoformat(str(scheduled_for_raw))
                sf_local = sf.replace(tzinfo=timezone.utc).astimezone(tz)
                time_str = f"planifié {sf_local.strftime('%d/%m à %Hh%M')}"
            except Exception:
                time_str = "planifié"
        else:
            time_str = f"position {i}"
        lines.append(f'• #{post_id} — {time_str} — "{extract}..."')

    await update.effective_message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Flux d'édition de légende (ConversationHandler) [TELEGRAM_BOT.md]
# ---------------------------------------------------------------------------

@authorized_only
async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Point d'entrée du ConversationHandler d'édition. [TELEGRAM_BOT.md — handle_edit]"""
    query = update.callback_query
    await query.answer()

    post_id = int(query.data.split(":")[1])
    msg = query.message
    message_id = msg.message_id

    # Stockage des données pour handle_new_caption et handle_edit_timeout [TG-F4]
    context.chat_data[f"edit_{message_id}"] = post_id
    context.chat_data["pending_edit_message_id"] = message_id
    context.chat_data["edit_message_type"] = "photo" if msg.photo else "text"
    context.chat_data["edit_message_current_text"] = msg.caption or msg.text or ""
    context.chat_data["edit_chat_id"] = update.effective_chat.id

    await msg.reply_text("Envoyez la nouvelle légende :")
    return WAITING_CAPTION


@authorized_only
async def handle_new_caption(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Reçoit la nouvelle légende et relance send_approval_request. [TELEGRAM_BOT.md]

    TF-F11 : légende complète en DB (pas tronquée). Troncature à 1024 uniquement
    dans send_approval_request pour l'appel Telegram.
    """
    new_caption = update.message.text
    message_id = context.chat_data.get("pending_edit_message_id")
    if message_id is None:
        return ConversationHandler.END

    post_id = context.chat_data.get(f"edit_{message_id}")
    if post_id is None:
        return ConversationHandler.END

    config: Config = context.bot_data["config"]

    async with get_session() as session:
        post = await session.get(Post, post_id)
        if post is None:
            await update.effective_message.reply_text("Post introuvable.")
            return ConversationHandler.END

        # Mise à jour légende complète en DB [TF-F11]
        post.caption = new_caption
        post.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # Retrait boutons chez tous les admins avant renvoi [TELEGRAM_BOT.md]
        try:
            msg_ids: dict = json.loads(post.telegram_message_ids or "{}")
        except (json.JSONDecodeError, TypeError):
            msg_ids = {}

        for uid, mid in msg_ids.items():
            if mid is not None:
                try:
                    await context.bot.edit_message_reply_markup(
                        chat_id=int(uid),
                        message_id=mid,
                        reply_markup=None,
                    )
                except Exception:
                    pass

        # Réinitialiser telegram_message_ids et re-envoyer [TELEGRAM_BOT.md]
        post.telegram_message_ids = json.dumps({})
        await session.commit()

        await send_approval_request(post, context.bot, session, config)

    # Nettoyage chat_data [TELEGRAM_BOT.md]
    context.chat_data.pop(f"edit_{message_id}", None)
    context.chat_data.pop("pending_edit_message_id", None)
    context.chat_data.pop("edit_message_type", None)
    context.chat_data.pop("edit_message_current_text", None)
    context.chat_data.pop("edit_chat_id", None)

    return ConversationHandler.END


async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Annule l'édition de légende. [TELEGRAM_BOT.md — cancel_edit]

    Ne modifie pas le post en DB. Les boutons d'approbation restent actifs.
    """
    await update.effective_message.reply_text("Édition annulée.")
    return ConversationHandler.END


async def handle_edit_timeout(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Gère le timeout de la conversation d'édition. [TELEGRAM_BOT.md — TF-F14, TF-F4]

    Retire les boutons ET ajoute "(délai d'édition expiré)" au message.
    Si message_id absent (timeout déclenché deux fois) : ignorer silencieusement.
    """
    message_id = context.chat_data.get("pending_edit_message_id")
    if message_id is None:
        return ConversationHandler.END

    chat_id = context.chat_data.get("edit_chat_id")
    msg_type = context.chat_data.get("edit_message_type", "photo")
    current_text = context.chat_data.get("edit_message_current_text", "")

    # Nettoyage chat_data avant les appels réseau
    context.chat_data.pop(f"edit_{message_id}", None)
    context.chat_data.pop("pending_edit_message_id", None)
    context.chat_data.pop("edit_message_type", None)
    context.chat_data.pop("edit_message_current_text", None)
    context.chat_data.pop("edit_chat_id", None)

    if chat_id is None:
        return ConversationHandler.END

    expired_text = current_text + "\n(délai d'édition expiré)"

    try:
        if msg_type == "photo":
            await context.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=expired_text[:1024],
            )
        else:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=expired_text,
            )
    except Exception as exc:
        logger.warning("Impossible d'éditer le message expiré : %s", exc)

    return ConversationHandler.END


# ---------------------------------------------------------------------------
# ConversationHandler (défini en module-level pour registration dans bot.py)
# [TELEGRAM_BOT.md — Configuration du ConversationHandler]
# ---------------------------------------------------------------------------

edit_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(handle_edit, pattern=r"^edit:\d+$")],
    states={
        WAITING_CAPTION: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_caption)
        ],
        # Timeout : TypeHandler(Update, ...) capture tout type d'update [TELEGRAM_BOT.md]
        ConversationHandler.TIMEOUT: [TypeHandler(Update, handle_edit_timeout)],
    },
    fallbacks=[CommandHandler("cancel", cancel_edit)],
    per_user=True,
    per_chat=False,
    conversation_timeout=300,
)
