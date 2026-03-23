# Commande health [docs/CLI.md — section "health", CLI-M2, CLI-M3]
from __future__ import annotations

import socket
import sys
from datetime import datetime, timezone

from ancnouv.config import Config


async def run_health(config: Config) -> int:
    """Vérifie l'état de tous les composants et affiche un rapport.

    Composants critiques (exit 1 si KO) : DB, Token Meta. [CLI-M2]
    Composants non-critiques (warning) : Wikipedia API, polices, Telegram Bot, serveur images.
    """
    critical_failure = False
    lines: list[str] = []

    # ── Base de données ─────────────────────────────────────────────────────
    import os
    from pathlib import Path

    db_path = (
        os.environ.get("ANCNOUV_DB_PATH", "")
        or f"{config.data_dir}/{config.database.filename}"
    )

    try:
        from ancnouv.db.session import get_session, init_db
        from sqlalchemy import text

        init_db(db_path)
        async with get_session() as session:
            r_events = await session.execute(text("SELECT COUNT(*) FROM events"))
            r_posts = await session.execute(text("SELECT COUNT(*) FROM posts"))
            n_events = r_events.scalar()
            n_posts = r_posts.scalar()

            # Lecture de l'état du scheduler
            r_paused = await session.execute(
                text("SELECT value FROM scheduler_state WHERE key='paused'")
            )
            paused_row = r_paused.fetchone()
            paused = paused_row and paused_row[0] == "true"

            r_susp = await session.execute(
                text("SELECT value FROM scheduler_state WHERE key='publications_suspended'")
            )
            susp_row = r_susp.fetchone()
            publications_suspended = susp_row and susp_row[0] == "true"

            r_esc = await session.execute(
                text("SELECT value FROM scheduler_state WHERE key='escalation_level'")
            )
            esc_row = r_esc.fetchone()
            escalation_level = int(esc_row[0]) if esc_row else 0

        lines.append(f"Base de données : OK ({n_events} événements, {n_posts} posts)")
        db_ok = True
    except Exception as exc:
        lines.append(f"Base de données : KO ({exc})")
        critical_failure = True
        db_ok = False
        paused = False
        publications_suspended = False
        escalation_level = 0

    # ── Token Meta ──────────────────────────────────────────────────────────
    token_ok = False
    if db_ok:
        try:
            from sqlalchemy import text
            async with get_session() as session:
                r = await session.execute(
                    text("SELECT expires_at FROM meta_tokens WHERE token_kind='user_long'")
                )
                row = r.fetchone()
            if not row:
                lines.append("Token Meta     : ABSENT (lancer : python -m ancnouv auth meta)")
                critical_failure = True
            else:
                expires_at_str = row[0]
                if expires_at_str:
                    try:
                        from datetime import datetime
                        exp = datetime.fromisoformat(str(expires_at_str).replace(" ", "T"))
                        if exp.tzinfo is None:
                            exp = exp.replace(tzinfo=timezone.utc)
                        days_left = (exp - datetime.now(timezone.utc)).days
                        if days_left <= 0:
                            lines.append("Token Meta     : EXPIRÉ (lancer : python -m ancnouv auth meta)")
                            critical_failure = True
                        else:
                            lines.append(f"Token Meta     : OK (expire dans {days_left} jours)")
                            token_ok = True
                    except Exception:
                        lines.append("Token Meta     : OK (expiration illisible)")
                        token_ok = True
                else:
                    lines.append("Token Meta     : OK (token permanent)")
                    token_ok = True
        except Exception as exc:
            lines.append(f"Token Meta     : KO ({exc})")
            critical_failure = True

    # ── Wikipedia API ───────────────────────────────────────────────────────
    try:
        import httpx
        _ua = "AnciennesNouvelles/1.0 (https://github.com/anciennesnouv)"
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.head(
                "https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/01/01",
                headers={"User-Agent": _ua},
            )
            if resp.status_code == 405:
                resp = await client.get(
                    "https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/01/01",
                    headers={"User-Agent": _ua},
                )
        if resp.status_code in (200, 404):
            lines.append("Wikipedia API  : OK")
        else:
            lines.append(f"Wikipedia API  : ⚠ HTTP {resp.status_code}")
    except Exception as exc:
        lines.append(f"Wikipedia API  : ⚠ inaccessible ({exc})")

    # ── Polices ─────────────────────────────────────────────────────────────
    from pathlib import Path
    fonts_dir = Path("assets") / "fonts"
    font_files = [
        "PlayfairDisplay-Bold.ttf",
        "LibreBaskerville-Regular.ttf",
        "LibreBaskerville-Italic.ttf",
        "IMFellEnglish-Regular.ttf",
    ]
    present = sum(1 for f in font_files if (fonts_dir / f).exists())
    if present == len(font_files):
        lines.append(f"Polices        : {present}/{len(font_files)} présentes")
    else:
        lines.append(
            f"Polices        : ⚠ {present}/{len(font_files)} présentes "
            f"(lancer : python -m ancnouv setup fonts)"
        )

    # ── Bot Telegram ────────────────────────────────────────────────────────
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/getMe"
            )
        if resp.status_code == 200:
            username = resp.json().get("result", {}).get("username", "")
            lines.append(f"Telegram Bot   : OK (@{username})")
        else:
            lines.append(f"Telegram Bot   : ⚠ HTTP {resp.status_code}")
    except Exception as exc:
        lines.append(f"Telegram Bot   : ⚠ inaccessible ({exc})")

    # ── Scheduler ───────────────────────────────────────────────────────────
    if db_ok:
        try:
            from apscheduler.triggers.cron import CronTrigger
            from datetime import datetime, timezone as dt_timezone
            trigger = CronTrigger.from_crontab(
                config.scheduler.generation_cron,
                timezone=config.scheduler.timezone,
            )
            next_fire = trigger.get_next_fire_time(None, datetime.now(dt_timezone.utc))
            next_str = next_fire.strftime("%d/%m/%Y %H:%M") if next_fire else "inconnu"
            state_str = "PAUSE" if paused else "ACTIF"
            lines.append(f"Scheduler      : {state_str} (prochain post : {next_str})")
        except Exception as exc:
            lines.append(f"Scheduler      : ⚠ calcul impossible ({exc})")

    # ── Serveur images ──────────────────────────────────────────────────────
    if config.image_hosting.backend == "local":
        try:
            with socket.create_connection(("localhost", config.image_hosting.local_port), timeout=2):
                lines.append(f"Serveur images : ACTIF (port {config.image_hosting.local_port})")
        except Exception:
            lines.append(
                f"Serveur images : ARRÊTÉ (port {config.image_hosting.local_port} "
                "— normal si app non démarrée)"
            )
    else:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.head(config.image_hosting.remote_upload_url)
            lines.append("Serveur images : OK (remote)")
        except Exception as exc:
            lines.append(f"Serveur images : ⚠ inaccessible ({exc})")

    # ── Affichage ────────────────────────────────────────────────────────────
    for line in lines:
        print(line)

    # [CLI-M3] Avertissements d'état spéciaux
    if publications_suspended:
        print("⚠️  Publications suspendues (token expiré — lancer : python -m ancnouv auth meta)")
    if escalation_level > 0:
        print(f"ℹ️  Escalade niveau {escalation_level}/5")

    return 1 if critical_failure else 0
