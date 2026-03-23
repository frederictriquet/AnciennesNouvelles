# Commandes test telegram / test instagram [docs/CLI.md]
from __future__ import annotations

import sys

from ancnouv.config import Config


async def run_test(config: Config, target: str) -> int:
    """Exécute un test de publication (telegram ou instagram)."""
    if target == "telegram":
        return await _test_telegram(config)
    elif target == "instagram":
        return await _test_instagram(config)
    print(f"Cible inconnue : {target}", file=sys.stderr)
    return 2


async def _test_telegram(config: Config) -> int:
    """Envoie un message de test à tous les authorized_user_ids."""
    import httpx

    message = "✅ Test Anciennes Nouvelles — le bot Telegram fonctionne correctement."
    errors = 0

    async with httpx.AsyncClient(timeout=10) as client:
        for user_id in config.telegram.authorized_user_ids:
            resp = await client.post(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                json={"chat_id": user_id, "text": message},
            )
            if resp.status_code == 200:
                print(f"✓ Message envoyé à {user_id}")
            else:
                print(f"✗ Échec pour {user_id} : {resp.text}", file=sys.stderr)
                errors += 1

    return 1 if errors else 0


async def _test_instagram(config: Config) -> int:
    """Publie un post de test sur Instagram/Facebook (publication réelle).

    [CLI.md] Prérequis : token Meta valide, instagram.enabled=true, DB initialisée.
    """
    import os
    from pathlib import Path

    db_path = (
        os.environ.get("ANCNOUV_DB_PATH", "")
        or f"{config.data_dir}/{config.database.filename}"
    )

    print("⚠️  Publication réelle — compte de test recommandé.")

    try:
        from ancnouv.db.session import get_session, init_db
        from ancnouv.cli.generate import generate_test_image
        from ancnouv.publisher import publish_to_all_platforms

        init_db(db_path)

        # Génération de l'image de test
        ret = generate_test_image(config)
        if ret != 0:
            return ret

        test_image = Path(config.data_dir) / "test_output.jpg"
        print(f"Image : {test_image}")
        print("Fonctionnalité complète disponible en Phase 4.")
        return 0

    except Exception as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
