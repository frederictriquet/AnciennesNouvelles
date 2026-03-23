# Commande escalation reset [docs/CLI.md — section "escalation reset", CLI-M6]
from __future__ import annotations

import os
import sys

from ancnouv.config import Config


async def reset_escalation(config: Config) -> int:
    """Remet escalation_level à 0 et envoie une notification Telegram.

    [CLI-M6] Si le bot est inaccessible, notification ignorée silencieusement —
    le reset reste effectif (retourne 0 quand même).
    """
    db_path = (
        os.environ.get("ANCNOUV_DB_PATH", "")
        or f"{config.data_dir}/{config.database.filename}"
    )

    try:
        from ancnouv.db.session import get_session, init_db
        from ancnouv.db.utils import set_scheduler_state

        init_db(db_path)
        async with get_session() as session:
            await set_scheduler_state(session, "escalation_level", "0")

        print("Niveau d'escalade réinitialisé à 0.")
    except Exception as exc:
        print(f"Erreur DB : {exc}", file=sys.stderr)
        return 1

    # Notification Telegram — silencieuse si bot inaccessible [CLI-M6]
    try:
        import httpx
        message = "✅ Niveau d'escalade Wikipedia réinitialisé à 0 (commande manuelle)."
        async with httpx.AsyncClient(timeout=5) as client:
            for user_id in config.telegram.authorized_user_ids:
                await client.post(
                    f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                    json={"chat_id": user_id, "text": message},
                )
    except Exception as exc:
        import logging
        logging.warning("Notification Telegram ignorée : %s", exc)

    return 0
