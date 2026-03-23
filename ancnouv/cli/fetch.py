# Commande fetch [docs/CLI.md — section "fetch", RF-3.1.1, RF-3.1.5]
from __future__ import annotations

from datetime import date, timedelta

from ancnouv.config import Config


async def run_fetch(config: Config, prefetch: bool = False) -> int:
    """Collecte les événements Wikipedia pour aujourd'hui ou les N prochains jours.

    [C-02] Sur erreur réseau avec cache existant → retourne 0.
    Sans cache ET API inaccessible → retourne 1.
    """
    from ancnouv.db.session import get_session, init_db
    import os

    db_path = (
        os.environ.get("ANCNOUV_DB_PATH", "")
        or f"{config.data_dir}/{config.database.filename}"
    )
    init_db(db_path)

    today = date.today()
    if prefetch:
        dates = [today + timedelta(days=i) for i in range(config.content.prefetch_days)]
    else:
        dates = [today]

    from ancnouv.fetchers.wikipedia import WikipediaFetcher

    total_stored = 0
    errors = 0

    for target_date in dates:
        try:
            async with get_session() as session:
                # params résolus en Phase 2 via get_effective_query_params
                fetcher = WikipediaFetcher(config)
                items = await fetcher.fetch(target_date)
                count = await fetcher.store(items, session)
                total_stored += count
        except Exception as exc:
            print(f"  ⚠ {target_date}: {exc}")
            errors += 1

    print(f"\n{total_stored} événement(s) stocké(s).")

    if errors and total_stored == 0:
        return 1
    return 0
