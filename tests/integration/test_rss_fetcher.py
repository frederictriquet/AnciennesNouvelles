# Tests d'intégration — RssFetcher [docs/DATA_SOURCES.md — DS-2]
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


def _make_entries(entries: list) -> list:
    """Convertit une liste de dicts simplifiés en format feedparser."""
    return entries


async def test_rss_fetch_parses_articles(db_session, mock_config):
    """2 items valides → fetch_all retourne 2 articles. [TESTING.md]"""
    from ancnouv.fetchers.rss import RssFetcher

    recent = time.strptime("2026-01-01", "%Y-%m-%d")
    entries = [
        {
            "title": "Article 1",
            "link": "https://example.com/1",
            "summary": "Résumé 1",
            "published_parsed": recent,
        },
        {
            "title": "Article 2",
            "link": "https://example.com/2",
            "summary": "Résumé 2",
            "published_parsed": recent,
        },
    ]

    mock_config.content.rss.enabled = True
    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://example.com/feed.rss", "name": "Test"})()
    ]

    fetcher = RssFetcher()
    with patch("feedparser.parse", return_value={"entries": entries, "bozo": False}):
        articles = await fetcher.fetch_all(mock_config)

    assert len(articles) == 2
    assert articles[0].title == "Article 1"
    assert articles[1].article_url == "https://example.com/2"


async def test_rss_store_deduplicates_by_url(db_session, mock_config):
    """Deux store() successifs → premier retourne 2, deuxième retourne 0. [TESTING.md]"""
    from ancnouv.fetchers.rss import RssFetcher

    recent = time.strptime("2026-01-01", "%Y-%m-%d")
    entries = [
        {
            "title": "Art 1",
            "link": "https://dedup.com/1",
            "summary": "Résumé",
            "published_parsed": recent,
        },
        {
            "title": "Art 2",
            "link": "https://dedup.com/2",
            "summary": "Résumé",
            "published_parsed": recent,
        },
    ]

    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://dedup.com/feed.rss", "name": "Dedup"})()
    ]
    mock_config.content.rss.enabled = True

    fetcher = RssFetcher()
    with patch("feedparser.parse", return_value={"entries": entries, "bozo": False}):
        articles = await fetcher.fetch_all(mock_config)

    first = await fetcher.store(articles, db_session)
    assert first == 2

    second = await fetcher.store(articles, db_session)
    assert second == 0


async def test_rss_max_age_filters_old_articles(db_session, mock_config):
    """Article > max_age_days filtré, article récent retourné. [DS-2.4]"""
    from ancnouv.fetchers.rss import RssFetcher

    # Article trop vieux : 365j ago
    old_date = datetime.now(timezone.utc) - timedelta(days=365)
    old_struct = time.struct_time(old_date.timetuple())

    # Article récent : 5j ago
    recent_date = datetime.now(timezone.utc) - timedelta(days=5)
    recent_struct = time.struct_time(recent_date.timetuple())

    entries = [
        {
            "title": "Vieux article",
            "link": "https://example.com/old",
            "summary": "Vieux",
            "published_parsed": old_struct,
        },
        {
            "title": "Article récent",
            "link": "https://example.com/recent",
            "summary": "Récent",
            "published_parsed": recent_struct,
        },
    ]

    mock_config.content.rss.max_age_days = 180
    mock_config.content.rss.min_delay_days = 0
    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://example.com/feed.rss", "name": "Test"})()
    ]
    mock_config.content.rss.enabled = True

    fetcher = RssFetcher()
    with patch("feedparser.parse", return_value={"entries": entries, "bozo": False}):
        articles = await fetcher.fetch_all(mock_config)

    urls = [a.article_url for a in articles]
    assert "https://example.com/recent" in urls
    assert "https://example.com/old" not in urls
