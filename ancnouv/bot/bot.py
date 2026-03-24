# Configuration bot Telegram — Phase 5 [SPEC-3.3, docs/TELEGRAM_BOT.md]
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from telegram.warnings import PTBUserWarning

# [TELEGRAM_BOT.md] ConversationHandler avec CallbackQueryHandler en entry_point
# et per_message=False : warning inoffensif pour ce cas d'usage (max_pending_posts=1).
warnings.filterwarnings(
    "ignore",
    message=".*per_message=False.*CallbackQueryHandler.*",
    category=PTBUserWarning,
)

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
)

if TYPE_CHECKING:
    pass


def create_application(token: str) -> Application:
    """Construit l'Application PTB et enregistre tous les handlers. [TELEGRAM_BOT.md]"""
    app = Application.builder().token(token).build()
    setup_handlers(app)
    return app


def setup_handlers(app: Application) -> None:
    """Enregistre les handlers dans l'ordre requis par PTB. [TELEGRAM_BOT.md]

    Ordre obligatoire (PTB route vers le premier handler correspondant) :
    1. edit_conv_handler (ConversationHandler) — en premier pour capturer edit:*
       avant tout CallbackQueryHandler générique.
    2. CallbackQueryHandlers pour approve, reject, skip.
    3. CommandHandlers.
    """
    from ancnouv.bot.handlers import (
        cmd_force,
        cmd_help,
        cmd_pause,
        cmd_pending,
        cmd_queue,
        cmd_resume,
        cmd_retry,
        cmd_retry_fb,
        cmd_retry_ig,
        cmd_start,
        cmd_stats,
        cmd_status,
        edit_conv_handler,
        handle_approve,
        handle_queue_it,
        handle_reject,
        handle_skip,
    )

    # 1. ConversationHandler en premier [TELEGRAM_BOT.md — pourquoi edit_conv_handler en premier]
    app.add_handler(edit_conv_handler)

    # 2. Callbacks d'approbation
    app.add_handler(CallbackQueryHandler(handle_approve, pattern=r"^approve:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_reject, pattern=r"^reject:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_skip, pattern=r"^skip:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_queue_it, pattern=r"^queue:\d+$"))

    # 3. Commandes [TELEGRAM_BOT.md — Noms commandes TC-12 : underscores, pas tirets]
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("force", cmd_force))
    app.add_handler(CommandHandler("retry", cmd_retry))
    app.add_handler(CommandHandler("retry_ig", cmd_retry_ig))
    app.add_handler(CommandHandler("retry_fb", cmd_retry_fb))
