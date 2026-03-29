# Commande fetch [docs/CLI.md — section "fetch", RF-3.1.1, RF-3.1.5]
from __future__ import annotations

from datetime import date, timedelta

from ancnouv.config import Config


async def run_fetch(config: Config, prefetch: bool = False) -> int:
    """Collecte les événements Wikipedia pour aujourd'hui ou les N prochains jours.

    Utilise get_effective_config() pour lire les overrides dashboard (wikipedia_event_types, etc.)
    et get_effective_query_params() pour le niveau d'escalade courant — cohérent avec job_fetch_wiki.

    [C-02] Sur erreur réseau avec cache existant → retourne 0.
    Sans cache ET API inaccessible → retourne 1.
    """
    import os

    from ancnouv.db.session import get_session, init_db
    from ancnouv.fetchers.wikipedia import WikipediaFetcher
    from ancnouv.generator.selector import get_effective_query_params

    db_path = (
        os.environ.get("ANCNOUV_DB_PATH", "")
        or f"{config.data_dir}/{config.database.filename}"
    )
    init_db(db_path)

    # Lire config effective (config.yml + overrides dashboard + niveau d'escalade)
    from ancnouv.config_loader import get_effective_config

    effective_config = await get_effective_config()
    async with get_session() as session:
        effective_params = await get_effective_query_params(session, effective_config)

    print(f"  Types d'événements : {effective_params.event_types}")

    today = date.today()
    window = effective_config.content.date_window_days
    if prefetch:
        # Couvre today−window → today+prefetch_days pour alimenter la fenêtre symétrique
        dates = [today + timedelta(days=i) for i in range(-window, effective_config.content.prefetch_days)]
    else:
        dates = [today + timedelta(days=i) for i in range(-window, window + 1)]

    total_stored = 0
    errors = 0

    fetcher = WikipediaFetcher(effective_config, effective_params)
    for target_date in dates:
        try:
            items = await fetcher.fetch(target_date)
            async with get_session() as session:
                count = await fetcher.store(items, session)
                total_stored += count
        except Exception as exc:
            print(f"  ⚠ {target_date}: {exc}")
            errors += 1

    print(f"\n{total_stored} événement(s) stocké(s).")

    if errors and total_stored == 0:
        return 1
    return 0
