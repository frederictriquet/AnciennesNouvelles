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


async def test_rss_skips_entry_without_published_parsed(db_session, mock_config):
    """Entry sans published_parsed → ignorée. [DS-2.3]"""
    from ancnouv.fetchers.rss import RssFetcher
    import time

    recent = time.strptime("2026-01-01", "%Y-%m-%d")
    entries = [
        {"title": "Sans date", "link": "https://example.com/nodate", "summary": "", "published_parsed": None},
        {"title": "Avec date", "link": "https://example.com/withdate", "summary": "", "published_parsed": recent},
    ]
    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://example.com/feed.rss", "name": "Test"})()
    ]
    mock_config.content.rss.enabled = True

    with patch("feedparser.parse", return_value={"entries": entries, "bozo": False}):
        articles = await RssFetcher().fetch_all(mock_config)

    assert len(articles) == 1
    assert articles[0].article_url == "https://example.com/withdate"


async def test_rss_skips_entry_with_short_title(db_session, mock_config):
    """Entry avec titre < 5 chars → ignorée. [DS-2.4]"""
    from ancnouv.fetchers.rss import RssFetcher
    import time

    recent = time.strptime("2026-01-01", "%Y-%m-%d")
    entries = [
        {"title": "AB", "link": "https://example.com/short", "summary": "", "published_parsed": recent},
        {"title": "Titre valide", "link": "https://example.com/valid", "summary": "", "published_parsed": recent},
    ]
    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://example.com/feed.rss", "name": "Test"})()
    ]
    mock_config.content.rss.enabled = True

    with patch("feedparser.parse", return_value={"entries": entries, "bozo": False}):
        articles = await RssFetcher().fetch_all(mock_config)

    assert len(articles) == 1
    assert articles[0].article_url == "https://example.com/valid"


async def test_rss_skips_entry_without_url(db_session, mock_config):
    """Entry sans URL → ignorée. [DS-2.4]"""
    from ancnouv.fetchers.rss import RssFetcher
    import time

    recent = time.strptime("2026-01-01", "%Y-%m-%d")
    entries = [
        {"title": "Pas d'URL", "link": "", "summary": "", "published_parsed": recent},
        {"title": "Avec URL", "link": "https://example.com/ok", "summary": "", "published_parsed": recent},
    ]
    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://example.com/feed.rss", "name": "Test"})()
    ]
    mock_config.content.rss.enabled = True

    with patch("feedparser.parse", return_value={"entries": entries, "bozo": False}):
        articles = await RssFetcher().fetch_all(mock_config)

    assert len(articles) == 1


async def test_rss_extract_image_from_media_thumbnail(db_session, mock_config):
    """Image extraite depuis media_thumbnail. [DS-2.3]"""
    from ancnouv.fetchers.rss import RssFetcher
    import time

    recent = time.strptime("2026-01-01", "%Y-%m-%d")
    entries = [{
        "title": "Article avec thumbnail",
        "link": "https://example.com/thumb",
        "summary": "",
        "published_parsed": recent,
        "media_thumbnail": [{"url": "https://example.com/img.jpg"}],
    }]
    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://example.com/feed.rss", "name": "Test"})()
    ]
    mock_config.content.rss.enabled = True

    with patch("feedparser.parse", return_value={"entries": entries, "bozo": False}):
        articles = await RssFetcher().fetch_all(mock_config)

    assert len(articles) == 1
    assert articles[0].image_url == "https://example.com/img.jpg"


async def test_rss_extract_image_from_enclosures(db_session, mock_config):
    """Image extraite depuis enclosures. [DS-2.3]"""
    from ancnouv.fetchers.rss import RssFetcher
    import time

    recent = time.strptime("2026-01-01", "%Y-%m-%d")
    entries = [{
        "title": "Article avec enclosure",
        "link": "https://example.com/enc",
        "summary": "",
        "published_parsed": recent,
        "enclosures": [{"type": "image/jpeg", "href": "https://example.com/enc.jpg"}],
    }]
    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://example.com/feed.rss", "name": "Test"})()
    ]
    mock_config.content.rss.enabled = True

    with patch("feedparser.parse", return_value={"entries": entries, "bozo": False}):
        articles = await RssFetcher().fetch_all(mock_config)

    assert len(articles) == 1
    assert articles[0].image_url == "https://example.com/enc.jpg"


async def test_rss_skips_bozo_connection_error(db_session, mock_config):
    """Flux avec bozo + ConnectionError → ignoré. [DS-2.3]"""
    from ancnouv.fetchers.rss import RssFetcher

    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://example.com/broken.rss", "name": "Broken"})()
    ]
    mock_config.content.rss.enabled = True

    with patch("feedparser.parse", return_value={
        "entries": [], "bozo": True, "bozo_exception": ConnectionError("refused")
    }):
        articles = await RssFetcher().fetch_all(mock_config)

    assert articles == []


async def test_rss_network_exception_continues(db_session, mock_config):
    """Exception réseau sur asyncio.to_thread → log warning + continue. [DS-2.3]"""
    from ancnouv.fetchers.rss import RssFetcher

    mock_config.content.rss.feeds = [
        type("Feed", (), {"url": "https://example.com/feed.rss", "name": "Test"})()
    ]
    mock_config.content.rss.enabled = True

    with patch("asyncio.to_thread", side_effect=Exception("timeout réseau")):
        articles = await RssFetcher().fetch_all(mock_config)

    assert articles == []
