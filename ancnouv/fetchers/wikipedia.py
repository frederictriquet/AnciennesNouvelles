# Fetcher Wikipedia "On This Day" — Phase 2 [docs/DATA_SOURCES.md — DS-1, RF-3.1]
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ancnouv.config import Config
from ancnouv.db.utils import compute_content_hash
from ancnouv.exceptions import FetcherError
from ancnouv.fetchers.base import BaseFetcher, EffectiveQueryParams, RawContentItem

logger = logging.getLogger(__name__)

# [DS-1.8] Mapping param API → (clé JSON réponse, type DB)
# Clé = nom API (events, births…) — cohérent avec EffectiveQueryParams.event_types
TYPE_TO_KEY: dict[str, tuple[str, str]] = {
    "events":   ("events",   "event"),
    "births":   ("births",   "birth"),
    "deaths":   ("deaths",   "death"),
    "holidays": ("holidays", "holiday"),
    "selected": ("selected", "selected"),
}

_USER_AGENT = "AnciennesNouvelles/1.0 (contact@exemple.com)"
_API_BASE = "https://api.wikimedia.org/feed/v1/wikipedia"


class WikipediaFetcher(BaseFetcher):
    """Collecte les événements Wikipedia "On This Day". [DS-1, DS-1.9]"""

    def __init__(
        self, config: Config, effective_params: EffectiveQueryParams | None = None
    ) -> None:
        self.config = config
        self.effective_params = effective_params or EffectiveQueryParams(
            event_types=list(config.content.wikipedia_event_types),
            use_fallback_en=False,
            dedup_policy=config.content.deduplication_policy,
            dedup_window=config.content.deduplication_window_days,
        )

    async def _call_api(self, lang: str, event_type: str, target_date: date) -> dict:
        """GET Wikipedia onthisday API. [DS-1.2, DS-1.5, DS-1.9]

        Retourne {} sur HTTP 404 (date sans événements — normal).
        Retry sur 429/503 avec Retry-After, max 3 tentatives, cap 60s.
        Lève FetcherError sur tout autre code non-200 ou erreur réseau.
        """
        url = (
            f"{_API_BASE}/{lang}/onthisday/{event_type}"
            f"/{target_date.month:02d}/{target_date.day:02d}"
        )
        headers = {"User-Agent": _USER_AGENT}

        async with httpx.AsyncClient() as client:
            for attempt in range(3):
                try:
                    r = await client.get(url, headers=headers, timeout=10.0)
                except httpx.RequestError as exc:
                    raise FetcherError(
                        f"Erreur réseau Wikipedia {url} : {exc}"
                    ) from exc

                if r.status_code == 404:
                    return {}

                if r.status_code in (429, 503):
                    if attempt < 2:
                        # [DS-1.9] Retry-After : entier OU date RFC 7231
                        retry_after_raw = r.headers.get("Retry-After", "5")
                        try:
                            wait = int(retry_after_raw)
                        except ValueError:
                            from email.utils import parsedate_to_datetime
                            try:
                                dt = parsedate_to_datetime(retry_after_raw)
                                wait = max(
                                    0,
                                    int(
                                        (dt - datetime.now(timezone.utc)).total_seconds()
                                    ),
                                )
                            except Exception:
                                wait = 5
                        wait = min(wait, 60)
                        logger.warning(
                            "HTTP %s Wikipedia %s — attente %ss (tentative %d/3)",
                            r.status_code,
                            url,
                            wait,
                            attempt + 1,
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise FetcherError(
                        f"HTTP {r.status_code} après 3 tentatives : {url}"
                    )

                if not r.is_success:
                    raise FetcherError(f"HTTP {r.status_code} inattendu : {url}")

                return r.json()

        return {}  # Inaccessible — satisfait mypy

    def _parse_entries(
        self,
        raw: dict,
        json_key: str,
        db_event_type: str,
        lang: str,
        target_date: date,
    ) -> list[RawContentItem]:
        """Filtre et mappe les entrées brutes vers RawContentItem. [DS-1.6, DS-1.7]"""
        current_year = date.today().year
        items: list[RawContentItem] = []

        for entry in raw.get(json_key, []):
            text_raw = entry.get("text") or ""
            year = entry.get("year")

            # [DS-1.6] Filtrage qualité
            if year is None:
                continue
            if year > current_year:
                continue
            if year < -9999:
                continue
            if len(text_raw) < 20:
                continue
            if len(text_raw) > 1200:
                text_raw = text_raw[:1200].rsplit(" ", 1)[0] + "..."

            # [DS-1.7] Extraction URL Wikipedia et thumbnail
            pages = entry.get("pages") or []
            wikipedia_url: str | None = None
            image_url: str | None = None
            if pages:
                page = pages[0]
                wikipedia_url = (
                    page.get("content_urls", {}).get("desktop", {}).get("page")
                )
                thumbnail = page.get("thumbnail")
                if thumbnail:
                    image_url = thumbnail.get("source")

            items.append(
                RawContentItem(
                    source="wikipedia",
                    source_lang=lang,
                    event_type=db_event_type,
                    month=target_date.month,
                    day=target_date.day,
                    year=year,
                    description=text_raw,
                    title=None,  # v1 — l'endpoint "On This Day" n'a pas de title [DS-1.7]
                    wikipedia_url=wikipedia_url,
                    image_url=image_url,
                )
            )

        return items

    async def fetch(self, target_date: date) -> list[RawContentItem]:
        """Collecte et filtre les événements pour target_date. [DS-1.4, DS-1.9]

        Niveaux 0–1 : FR d'abord, fallback EN si FR < wikipedia_min_events après filtrage.
        Niveau ≥ 2 (use_fallback_en=True) : EN inconditionnel + fusion FR+EN. [DS-3 avertissement doublons]
        """
        params = self.effective_params
        fr_items: list[RawContentItem] = []

        for api_type in params.event_types:
            if api_type not in TYPE_TO_KEY:
                logger.warning("Type d'événement inconnu ignoré : %r", api_type)
                continue
            json_key, db_type = TYPE_TO_KEY[api_type]
            raw = await self._call_api("fr", api_type, target_date)
            fr_items.extend(
                self._parse_entries(raw, json_key, db_type, "fr", target_date)
            )

        if params.use_fallback_en:
            # [DS-1.4b] Niveau ≥ 2 : EN inconditionnel, fusion FR+EN
            en_items: list[RawContentItem] = []
            for api_type in params.event_types:
                if api_type not in TYPE_TO_KEY:
                    continue
                json_key, db_type = TYPE_TO_KEY[api_type]
                raw = await self._call_api("en", api_type, target_date)
                en_items.extend(
                    self._parse_entries(raw, json_key, db_type, "en", target_date)
                )
            return fr_items + en_items

        # [DS-1.4] Niveau 0 : fallback EN si FR < wikipedia_min_events après filtrage
        if len(fr_items) < self.config.content.wikipedia_min_events:
            en_items = []
            for api_type in params.event_types:
                if api_type not in TYPE_TO_KEY:
                    continue
                json_key, db_type = TYPE_TO_KEY[api_type]
                raw = await self._call_api("en", api_type, target_date)
                en_items.extend(
                    self._parse_entries(raw, json_key, db_type, "en", target_date)
                )
            if len(en_items) < self.config.content.wikipedia_min_events:
                logger.warning(
                    "API Wikipedia EN insuffisante pour %s : %d événements après filtrage",
                    target_date,
                    len(en_items),
                )
            return fr_items + en_items

        return fr_items

    async def store(self, items: list[RawContentItem], session: AsyncSession) -> int:
        """INSERT OR IGNORE en DB. Retourne le nombre de nouveaux événements insérés. [DS-1.9]

        Comportement transactionnel : rollback total si exception non catchée.
        """
        inserted = 0
        now = datetime.now(timezone.utc)

        for item in items:
            content_hash = compute_content_hash(item.description)
            result = await session.execute(
                text(
                    "INSERT OR IGNORE INTO events "
                    "(source, source_lang, event_type, month, day, year, description, "
                    "title, wikipedia_url, image_url, content_hash, status, "
                    "published_count, fetched_at) "
                    "VALUES (:source, :source_lang, :event_type, :month, :day, :year, "
                    ":description, :title, :wikipedia_url, :image_url, :content_hash, "
                    "'available', 0, :fetched_at)"
                ),
                {
                    "source": item.source,
                    "source_lang": item.source_lang,
                    "event_type": item.event_type,
                    "month": item.month,
                    "day": item.day,
                    "year": item.year,
                    "description": item.description,
                    "title": item.title,
                    "wikipedia_url": item.wikipedia_url,
                    "image_url": item.image_url,
                    "content_hash": content_hash,
                    "fetched_at": now,
                },
            )
            inserted += result.rowcount

        await session.commit()
        return inserted
