#!/usr/bin/env python3
"""Re-fetche tous les événements Wikipedia pour les 366 jours de l'année.

À utiliser après avoir vidé la table events :
    sqlite3 data/ancnouv.db "DELETE FROM events;"
    python scripts/refetch_all_events.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Permet d'exécuter depuis la racine du projet sans install
sys.path.insert(0, str(Path(__file__).parent.parent))


async def main() -> int:
    from ancnouv.config import Config
    from ancnouv.db.session import get_session, init_db
    from ancnouv.fetchers.wikipedia import WikipediaFetcher

    config = Config()
    db_path = (
        os.environ.get("ANCNOUV_DB_PATH", "")
        or f"{config.data_dir}/{config.database.filename}"
    )
    init_db(db_path)

    fetcher = WikipediaFetcher(config)

    # Année bissextile pour couvrir les 366 jours (including 29/02)
    base = date(2024, 1, 1)
    total = 0
    errors = 0

    for i in range(366):
        d = base + timedelta(days=i)
        try:
            async with get_session() as session:
                items = await fetcher.fetch(d)
                n = await fetcher.store(items, session)
                total += n
                print(f"{d.strftime('%d/%m')} : {n} nouveaux ({len(items)} reçus)")
        except Exception as exc:
            print(f"{d.strftime('%d/%m')} erreur : {exc}", file=sys.stderr)
            errors += 1

    print(f"\nTerminé : {total} événements insérés, {errors} erreurs.")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
