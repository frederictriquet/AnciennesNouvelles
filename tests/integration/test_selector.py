# Tests d'intégration — select_event, select_article, get_effective_query_params
# [docs/DATA_SOURCES.md, SPEC-3.2.1, DS-1.4b, DB-16, T-09]
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from ancnouv.db.models import Event, RssArticle
from ancnouv.db.utils import compute_content_hash, set_scheduler_state
from ancnouv.fetchers.base import EffectiveQueryParams
from ancnouv.generator.selector import (
    get_effective_query_params,
    select_article,
    select_event,
)


async def _make_event(session, month, day, year, description, source_lang="fr",
                      published_count=0, last_used_at=None, status="available"):
    ev = Event(
        source="wikipedia",
        source_lang=source_lang,
        event_type="event",
        month=month,
        day=day,
        year=year,
        description=description,
        content_hash=compute_content_hash(description),
        status=status,
        published_count=published_count,
        last_used_at=last_used_at,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(ev)
    await session.commit()
    await session.refresh(ev)
    return ev


async def _make_article(session, fetched_days_ago=91, status="available", published_count=0):
    article = RssArticle(
        feed_url="https://test.com/feed",
        feed_name="Test",
        title=f"Article test {fetched_days_ago}j",
        article_url=f"https://test.com/article-{fetched_days_ago}",
        summary="Résumé.",
        published_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365),
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=fetched_days_ago),
        status=status,
        published_count=published_count,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)
    return article


# ─── select_event ────────────────────────────────────────────────────────────────

async def test_select_event_returns_candidate(db_session, mock_config):
    """2 events pour (3, 21) → select_event retourne l'un des deux. [TESTING.md]"""
    params = EffectiveQueryParams(event_types=["events"], use_fallback_en=False, dedup_policy="never", dedup_window=365)
    await _make_event(db_session, 3, 21, 1871, "Événement A")
    await _make_event(db_session, 3, 21, 1920, "Événement B")

    result = await select_event(db_session, date(2026, 3, 21), params)
    assert result is not None
    assert result.month == 3
    assert result.day == 21


async def test_select_event_respects_dedup_never(db_session, mock_config):
    """Event published_count=1 non retourné (politique 'never'). [SPEC-3.2.1]"""
    params = EffectiveQueryParams(event_types=["events"], use_fallback_en=False, dedup_policy="never", dedup_window=365)
    await _make_event(db_session, 3, 21, 1871, "Publié une fois", published_count=1)

    result = await select_event(db_session, date(2026, 3, 21), params)
    assert result is None


async def test_select_event_respects_dedup_window(db_session, mock_config):
    """Event publié il y a 300j (window=365) → non retourné. [DB-1]"""
    last_used = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=300)
    params = EffectiveQueryParams(event_types=["events"], use_fallback_en=False, dedup_policy="window", dedup_window=365)
    await _make_event(db_session, 3, 21, 1871, "Dans la fenêtre",
                      published_count=1, last_used_at=last_used)

    result = await select_event(db_session, date(2026, 3, 21), params)
    assert result is None


async def test_select_event_window_outside_returns(db_session, mock_config):
    """Event publié il y a 400j (window=365) → retourné. [DB-1]"""
    last_used = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
    params = EffectiveQueryParams(event_types=["events"], use_fallback_en=False, dedup_policy="window", dedup_window=365)
    await _make_event(db_session, 3, 21, 1871, "Hors fenêtre",
                      published_count=1, last_used_at=last_used)

    result = await select_event(db_session, date(2026, 3, 21), params)
    assert result is not None


# ─── select_article ──────────────────────────────────────────────────────────────

async def test_select_article_respects_delay(db_session, mock_config):
    """fetched_at = now-80j (< min_delay_days=90) → non retourné. [DS-2.5]"""
    params = EffectiveQueryParams(event_types=["events"], use_fallback_en=False, dedup_policy="never", dedup_window=365)
    await _make_article(db_session, fetched_days_ago=80)

    result = await select_article(db_session, mock_config, params)
    assert result is None


async def test_select_article_eligible(db_session, mock_config):
    """fetched_at = now-91j → retourné. [DS-2.5]"""
    params = EffectiveQueryParams(event_types=["events"], use_fallback_en=False, dedup_policy="never", dedup_window=365)
    await _make_article(db_session, fetched_days_ago=91)

    result = await select_article(db_session, mock_config, params)
    assert result is not None


# ─── get_effective_query_params ──────────────────────────────────────────────────

async def test_get_effective_params_level_0(db_session, mock_config):
    """Niveau 0 → event_types=["events"], use_fallback_en=False. [DS-1.4b]"""
    await set_scheduler_state(db_session, "escalation_level", "0")
    params = await get_effective_query_params(db_session, mock_config)
    assert params.event_types == ["events"]
    assert params.use_fallback_en is False


async def test_get_effective_params_level_1(db_session, mock_config):
    """Niveau 1 → event_types contient births et deaths. [DS-1.4b]"""
    await set_scheduler_state(db_session, "escalation_level", "1")
    params = await get_effective_query_params(db_session, mock_config)
    assert "births" in params.event_types
    assert "deaths" in params.event_types
    assert params.use_fallback_en is False


async def test_get_effective_params_level_2(db_session, mock_config):
    """Niveau 2 → use_fallback_en=True. [DS-1.4b]"""
    await set_scheduler_state(db_session, "escalation_level", "2")
    params = await get_effective_query_params(db_session, mock_config)
    assert params.use_fallback_en is True


async def test_get_effective_params_level_3_forces_window(db_session, mock_config):
    """Niveau 3 → dedup_policy='window' si config='never'. [DS-1.4b]"""
    mock_config.content.deduplication_policy = "never"
    await set_scheduler_state(db_session, "escalation_level", "3")
    params = await get_effective_query_params(db_session, mock_config)
    assert params.dedup_policy == "window"
    assert params.use_fallback_en is True


async def test_get_effective_params_level_4(db_session, mock_config):
    """Niveau 4 → dedup_window ≤ 180j. [DS-1.4b]"""
    mock_config.content.deduplication_policy = "never"
    await set_scheduler_state(db_session, "escalation_level", "4")
    params = await get_effective_query_params(db_session, mock_config)
    assert params.dedup_policy == "window"
    assert params.dedup_window <= 180
