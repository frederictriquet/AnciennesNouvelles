# Tests d'intégration — WikipediaFetcher [docs/DATA_SOURCES.md — DS-1, DS-2]
from __future__ import annotations

from datetime import date

import pytest


async def test_fetch_and_store_events(db_session, mock_config, httpx_mock):
    """Mock API → 1 item retourné, store retourne 1. [TESTING.md]"""
    from ancnouv.fetchers.wikipedia import WikipediaFetcher

    httpx_mock.add_response(
        url="https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/03/21",
        json={"events": [{"year": 1871, "text": "La Commune de Paris est proclamée.", "pages": []}]},
    )

    # 1 item FR suffisant → pas de fallback EN
    mock_config.content.wikipedia_min_events = 1
    fetcher = WikipediaFetcher(config=mock_config)
    items = await fetcher.fetch(date(2026, 3, 21))
    assert len(items) == 1
    stored = await fetcher.store(items, db_session)
    assert stored == 1


async def test_fetch_deduplication(db_session, mock_config, httpx_mock):
    """Deux store() successifs avec les mêmes items → deuxième retourne 0. [TESTING.md]"""
    from ancnouv.fetchers.wikipedia import WikipediaFetcher

    httpx_mock.add_response(
        url="https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/03/21",
        json={"events": [{"year": 1871, "text": "La Commune de Paris.", "pages": []}]},
    )

    mock_config.content.wikipedia_min_events = 1
    fetcher = WikipediaFetcher(config=mock_config)
    items = await fetcher.fetch(date(2026, 3, 21))
    first = await fetcher.store(items, db_session)
    assert first == 1

    second = await fetcher.store(items, db_session)
    assert second == 0


async def test_fetch_fallback_en(db_session, mock_config, httpx_mock):
    """FR vide + EN avec 2 événements → fallback déclenché, source_lang='en'. [RF-3.1.4]"""
    from ancnouv.fetchers.base import EffectiveQueryParams
    from ancnouv.fetchers.wikipedia import WikipediaFetcher

    # FR sans événements
    httpx_mock.add_response(
        url="https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/03/21",
        json={"events": []},
    )
    # EN avec 2 événements
    httpx_mock.add_response(
        url="https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/03/21",
        json={
            "events": [
                {"year": 1871, "text": "Paris Commune was proclaimed.", "pages": []},
                {"year": 1945, "text": "World War II ends in Europe.", "pages": []},
            ]
        },
    )

    params = EffectiveQueryParams(
        event_types=["events"],
        use_fallback_en=False,
        dedup_policy="never",
        dedup_window=365,
    )
    fetcher = WikipediaFetcher(config=mock_config, effective_params=params)
    items = await fetcher.fetch(date(2026, 3, 21))

    # Fallback EN car FR < wikipedia_min_events (3 par défaut)
    assert len(items) >= 1
    assert all(item.source_lang == "en" for item in items)


async def test_fetch_404_returns_empty(db_session, mock_config, httpx_mock):
    """HTTP 404 → liste vide, pas d'exception. [DS-1.2]"""
    from ancnouv.fetchers.wikipedia import WikipediaFetcher

    httpx_mock.add_response(
        url="https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/03/21",
        status_code=404,
    )

    # 0 items FR → seuil 0 évite le fallback EN dans ce test
    mock_config.content.wikipedia_min_events = 0
    fetcher = WikipediaFetcher(config=mock_config)
    items = await fetcher.fetch(date(2026, 3, 21))
    assert items == []


async def test_fetch_both_apis_fail(db_session, mock_config, httpx_mock):
    """FR HTTP 500 → FetcherError levée ; store([]) retourne 0. [TESTING.md]

    _call_api lève FetcherError sur 5xx (pas de retour silencieux).
    Le job job_fetch_wiki capture cette exception — testé ici via pytest.raises.
    """
    from ancnouv.exceptions import FetcherError
    from ancnouv.fetchers.wikipedia import WikipediaFetcher

    httpx_mock.add_response(
        url="https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/03/21",
        status_code=500,
    )

    mock_config.content.wikipedia_min_events = 0  # pas de fallback EN dans ce test
    fetcher = WikipediaFetcher(config=mock_config)
    with pytest.raises(FetcherError):
        await fetcher.fetch(date(2026, 3, 21))
    stored = await fetcher.store([], db_session)
    assert stored == 0
