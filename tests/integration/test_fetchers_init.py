# Tests d'intégration — fetchers/__init__.py : prefetch_wikipedia, check_sources_health [DS-4, DS-12]
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def test_prefetch_wikipedia_fetches_n_days(db_session, mock_config):
    """prefetch_wikipedia appelle WikipediaFetcher.fetch pour chaque jour. [DS-4]"""
    from ancnouv.fetchers import prefetch_wikipedia

    mock_config.content.prefetch_days = 3
    mock_fetcher = MagicMock()
    mock_fetcher.fetch = AsyncMock(return_value=[MagicMock()])
    mock_fetcher.store = AsyncMock()

    with patch("ancnouv.fetchers.WikipediaFetcher", return_value=mock_fetcher):
        await prefetch_wikipedia(mock_config, db_session)

    assert mock_fetcher.fetch.call_count == 3
    assert mock_fetcher.store.call_count == 3


async def test_prefetch_wikipedia_skips_empty_result(db_session, mock_config):
    """Fetch retourne [] → store non appelé pour ce jour. [DS-4]"""
    from ancnouv.fetchers import prefetch_wikipedia

    mock_config.content.prefetch_days = 2
    mock_fetcher = MagicMock()
    mock_fetcher.fetch = AsyncMock(side_effect=[[], [MagicMock()]])
    mock_fetcher.store = AsyncMock()

    with patch("ancnouv.fetchers.WikipediaFetcher", return_value=mock_fetcher):
        await prefetch_wikipedia(mock_config, db_session)

    # store appelé seulement pour le 2e jour (résultat non vide)
    assert mock_fetcher.store.call_count == 1


async def test_prefetch_wikipedia_continues_on_error(db_session, mock_config):
    """Exception sur un jour → log warning + continue. [DS-4]"""
    from ancnouv.fetchers import prefetch_wikipedia

    mock_config.content.prefetch_days = 2
    mock_fetcher = MagicMock()
    mock_fetcher.fetch = AsyncMock(side_effect=[Exception("réseau KO"), []])
    mock_fetcher.store = AsyncMock()

    with patch("ancnouv.fetchers.WikipediaFetcher", return_value=mock_fetcher):
        # Ne doit pas lever d'exception
        await prefetch_wikipedia(mock_config, db_session)

    assert mock_fetcher.fetch.call_count == 2


async def test_check_sources_health_wikipedia_ok(mock_config, httpx_mock):
    """Wikipedia répond 200 → wikipedia_fr: True. [DS-12]"""
    from ancnouv.fetchers import check_sources_health, _WIKI_HEALTH_URL

    httpx_mock.add_response(url=_WIKI_HEALTH_URL, status_code=200)
    mock_config.content.rss.feeds = []

    result = await check_sources_health(mock_config)
    assert result["wikipedia_fr"] is True


async def test_check_sources_health_wikipedia_405_fallback_get(mock_config, httpx_mock):
    """Wikipedia HEAD 405 → fallback GET [DS-12]."""
    from ancnouv.fetchers import check_sources_health, _WIKI_HEALTH_URL

    # HEAD retourne 405, GET retourne 200
    httpx_mock.add_response(url=_WIKI_HEALTH_URL, method="HEAD", status_code=405)
    httpx_mock.add_response(url=_WIKI_HEALTH_URL, method="GET", status_code=200)
    mock_config.content.rss.feeds = []

    result = await check_sources_health(mock_config)
    assert result["wikipedia_fr"] is True


async def test_check_sources_health_wikipedia_error(mock_config, httpx_mock):
    """Exception réseau Wikipedia → wikipedia_fr: False. [DS-12]"""
    from ancnouv.fetchers import check_sources_health, _WIKI_HEALTH_URL

    httpx_mock.add_exception(Exception("timeout"), url=_WIKI_HEALTH_URL)
    mock_config.content.rss.feeds = []

    result = await check_sources_health(mock_config)
    assert result["wikipedia_fr"] is False


async def test_check_sources_health_rss_feeds(mock_config, httpx_mock):
    """Flux RSS configurés → clés rss_0, rss_1 dans le résultat. [DS-12]"""
    from ancnouv.fetchers import check_sources_health, _WIKI_HEALTH_URL

    feed1 = MagicMock()
    feed1.url = "https://example.com/feed1.rss"
    feed2 = MagicMock()
    feed2.url = "https://example.com/feed2.rss"

    httpx_mock.add_response(url=_WIKI_HEALTH_URL, status_code=200)
    httpx_mock.add_response(url=feed1.url, status_code=200)
    httpx_mock.add_exception(Exception("timeout"), url=feed2.url)

    mock_config.content.rss.feeds = [feed1, feed2]

    result = await check_sources_health(mock_config)
    assert result["rss_0"] is True
    assert result["rss_1"] is False
