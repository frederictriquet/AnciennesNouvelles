# Fetchers Wikipedia + RSS — Phase 2 [docs/DATA_SOURCES.md, SPEC-3.1]
from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ancnouv.config import Config
from ancnouv.fetchers.wikipedia import WikipediaFetcher

logger = logging.getLogger(__name__)

_USER_AGENT = "AnciennesNouvelles/1.0 (contact@exemple.com)"
_WIKI_HEALTH_URL = (
    "https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/01/01"
)


async def prefetch_wikipedia(config: Config, session: AsyncSession) -> None:
    """Collecte Wikipedia pour les prefetch_days prochains jours. [DS-4]

    Utilise les paramètres de config de base (niveau d'escalade 0 — bootstrap).
    Le niveau d'escalade courant est ignoré intentionnellement [DS-4].
    """
    fetcher = WikipediaFetcher(config)  # effective_params=None → niveau 0
    today = date.today()

    for i in range(config.content.prefetch_days):
        target = today + timedelta(days=i)
        try:
            items = await fetcher.fetch(target)
            if items:
                await fetcher.store(items, session)
        except Exception as exc:
            logger.warning("prefetch_wikipedia : erreur pour %s : %s", target, exc)


async def check_sources_health(config: Config) -> dict[str, bool]:
    """Vérifie la connectivité Wikipedia et des flux RSS (HEAD, timeout 5s). [DS-4, DS-12]

    Retourne {"wikipedia_fr": bool, "rss_0": bool, "rss_1": bool, ...}.
    HEAD 405 sur Wikimedia → fallback GET pour confirmer [DS-12].
    """
    headers = {"User-Agent": _USER_AGENT}
    results: dict[str, bool] = {}

    async with httpx.AsyncClient() as client:
        # Wikipedia
        try:
            r = await client.head(_WIKI_HEALTH_URL, headers=headers, timeout=5.0)
            if r.status_code == 405:
                # [DS-12] Certaines configs Wikimedia refusent HEAD — fallback GET
                r = await client.get(_WIKI_HEALTH_URL, headers=headers, timeout=5.0)
            results["wikipedia_fr"] = r.is_success
        except Exception as exc:
            logger.warning("health check Wikipedia KO : %s", exc)
            results["wikipedia_fr"] = False

        # Flux RSS configurés
        for i, feed in enumerate(config.content.rss.feeds):
            try:
                r = await client.head(feed.url, timeout=5.0)
                results[f"rss_{i}"] = r.is_success
            except Exception as exc:
                logger.warning("health check RSS %s KO : %s", feed.url, exc)
                results[f"rss_{i}"] = False

    return results
