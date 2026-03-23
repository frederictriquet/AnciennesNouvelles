# Notifications Telegram — Phase 5 [SPEC-3.3, docs/TELEGRAM_BOT.md]
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ancnouv.config import Config
    from ancnouv.db.models import Post

logger = logging.getLogger(__name__)

# Délais de retry (secondes) : 1, 2, 4, 8, 16 [TELEGRAM_BOT.md — TG-F15]
_RETRY_DELAYS = [1, 2, 4, 8, 16]


async def send_with_retry(
    coro_factory: Callable[[], Awaitable[Message]],
    max_attempts: int = 5,
) -> Message | None:
    """Envoie un message Telegram avec retry x5 et backoff exponentiel. [TG-F15]

    coro_factory est appelé à chaque tentative — permet de ré-ouvrir
    les fichiers ou reconstruire les coroutines sans état partagé.
    Retourne None si toutes les tentatives échouent.
    """
    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except Exception as exc:
            if attempt < max_attempts - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "Envoi Telegram échoué (tentative %d/%d) : %s — retry dans %ds",
                    attempt + 1,
                    max_attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "Envoi Telegram définitivement échoué après %d tentatives : %s",
                    max_attempts,
                    exc,
                )
    return None


async def notify_all(bot: Bot, config: "Config", message: str) -> None:
    """Envoie une notification texte à tous les admins autorisés. [TELEGRAM_BOT.md]

    Applique un délai notification_debounce entre chaque envoi.
    Les erreurs par admin sont loggées sans interrompre les autres envois.
    """
    users = config.telegram.authorized_user_ids
    for i, user_id in enumerate(users):
        try:
            await send_with_retry(
                lambda uid=user_id: bot.send_message(chat_id=uid, text=message)  # type: ignore[misc]
            )
        except Exception as exc:
            logger.error(
                "Notification impossible pour user_id=%s : %s", user_id, exc
            )
        if i < len(users) - 1 and config.telegram.notification_debounce > 0:
            await asyncio.sleep(config.telegram.notification_debounce)


def _build_approval_keyboard(post_id: int) -> InlineKeyboardMarkup:
    """Construit le clavier inline d'approbation. [SPEC-3.3.2]"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Publier", callback_data=f"approve:{post_id}"),
            InlineKeyboardButton("Rejeter", callback_data=f"reject:{post_id}"),
        ],
        [InlineKeyboardButton("Autre événement", callback_data=f"skip:{post_id}")],
        [InlineKeyboardButton("Modifier la légende", callback_data=f"edit:{post_id}")],
    ])


def _build_approval_text(post: "Post") -> str:
    """Construit le texte du message de validation (Mode A ou B). [TELEGRAM_BOT.md]"""
    now_str = datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M")
    header = (
        "PROPOSITION DE POST (Actualité RSS)"
        if post.article_id is not None
        else "PROPOSITION DE POST"
    )
    return f"{header}\n\n{post.caption}\n─────────────────────────\nGénéré le {now_str}"


async def send_approval_request(
    post: "Post",
    bot: Bot,
    session: "AsyncSession",
    config: "Config",
) -> None:
    """Envoie le post en approbation à tous les admins autorisés. [SPEC-3.3.2]

    Peuple post.telegram_message_ids avec {str(user_id): message_id} et commit.
    Guard TG-F9 : liste vide → erreur loggée, post marqué error.
    Guard TG-F11 : légende tronquée à 1024 pour send_photo uniquement.
    """
    # Guard TG-F9 : authorized_user_ids vide
    if not config.telegram.authorized_user_ids:
        logger.error(
            "authorized_user_ids vide — impossible d'envoyer le post en approbation"
        )
        post.status = "error"
        post.error_message = "No authorized users configured"
        await session.commit()
        return

    text = _build_approval_text(post)
    # TG-F11 : tronquer à 1024 chars pour send_photo uniquement
    caption_telegram = text[:1024]
    keyboard = _build_approval_keyboard(post.id)

    image_path = Path(post.image_path) if post.image_path else None
    has_image = image_path is not None and image_path.exists()

    # Lire les bytes une fois pour les retries [TG-F15]
    image_bytes: bytes | None = None
    if has_image:
        try:
            assert image_path is not None  # Garanti par has_image check
            image_bytes = image_path.read_bytes()
        except OSError as exc:
            logger.warning("Impossible de lire l'image %s : %s — envoi sans photo", image_path, exc)
            has_image = False

    telegram_message_ids: dict[str, int] = {}

    for user_id in config.telegram.authorized_user_ids:
        try:
            if has_image and image_bytes is not None:
                msg = await send_with_retry(
                    lambda uid=user_id, data=image_bytes, cap=caption_telegram: bot.send_photo(  # type: ignore[misc]
                        chat_id=uid,
                        photo=data,
                        caption=cap,
                        reply_markup=keyboard,
                    )
                )
            else:
                msg = await send_with_retry(
                    lambda uid=user_id, t=text: bot.send_message(  # type: ignore[misc]
                        chat_id=uid,
                        text=t,
                        reply_markup=keyboard,
                    )
                )
            if msg is not None:
                telegram_message_ids[str(user_id)] = msg.message_id
        except Exception as exc:
            logger.error(
                "Échec envoi approbation pour user_id=%s : %s", user_id, exc
            )

    post.telegram_message_ids = json.dumps(telegram_message_ids)
    await session.commit()
