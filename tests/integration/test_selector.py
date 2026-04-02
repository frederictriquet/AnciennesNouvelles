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


async def test_select_event_date_window(db_session, mock_config):
    """Fenêtre de dates : event sur J-2 sélectionnable via liste de dates. [content.date_window_days]"""
    params = EffectiveQueryParams(event_types=["events"], use_fallback_en=False, dedup_policy="never", dedup_window=365)
    # Événement sur le 19 mars — 2 jours avant le 21
    await _make_event(db_session, 3, 19, 1900, "Événement J-2")

    # Sans fenêtre (date unique) → non trouvé
    result = await select_event(db_session, date(2026, 3, 21), params)
    assert result is None

    # Avec fenêtre ±2 jours → trouvé
    dates = [date(2026, 3, 21) + timedelta(days=i) for i in range(-2, 3)]
    result = await select_event(db_session, dates, params)
    assert result is not None
    assert result.day == 19


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


async def test_get_effective_params_age_range_from_config(db_session, mock_config):
    """min/max age_years propagés depuis la config dans EffectiveQueryParams. [content.event_min_age_years, content.event_max_age_years]"""
    mock_config.content.event_min_age_years = 10
    mock_config.content.event_max_age_years = 200
    params = await get_effective_query_params(db_session, mock_config)
    assert params.min_age_years == 10
    assert params.max_age_years == 200


# ─── select_gallica_article ───────────────────────────────────────────────────────

def _make_gallica_article(**kwargs):
    """Crée un GallicaArticle avec des valeurs par défaut."""
    from datetime import date
    from ancnouv.db.models import GallicaArticle
    defaults = dict(
        ark_id="ark:/12148/bpt6k_default",
        title="Le Temps",
        description=None,
        source_name=None,
        date_published=date(1900, 3, 15),
        gallica_url="https://gallica.bnf.fr/ark:/12148/bpt6k_default",
        content_hash="abc123",
        status="available",
        published_count=0,
    )
    defaults.update(kwargs)
    return GallicaArticle(**defaults)


async def test_select_gallica_article_returns_available(db_session, mock_config):
    """Article disponible en DB → retourné par select_gallica_article. [SPEC-9.3]"""
    from ancnouv.generator.selector import select_gallica_article

    article = _make_gallica_article(ark_id="ark:/12148/bpt6k_test", content_hash="abc123")
    db_session.add(article)
    await db_session.commit()

    result = await select_gallica_article(db_session, mock_config, EffectiveQueryParams())
    assert result is not None
    assert result.ark_id == "ark:/12148/bpt6k_test"


async def test_select_gallica_article_empty_db(db_session, mock_config):
    """Aucun article en DB → None."""
    from ancnouv.generator.selector import select_gallica_article

    result = await select_gallica_article(db_session, mock_config, EffectiveQueryParams())
    assert result is None


async def test_increment_escalation_level(db_session, mock_config):
    """increment_escalation_level incrémente la valeur dans scheduler_state. [DS-1.4b]"""
    from ancnouv.generator.selector import increment_escalation_level

    new_level = await increment_escalation_level(db_session)
    assert new_level == 1

    new_level = await increment_escalation_level(db_session)
    assert new_level == 2


async def test_increment_escalation_level_capped_at_5(db_session, mock_config):
    """increment_escalation_level ne dépasse pas 5. [DS-1.4b]"""
    from ancnouv.generator.selector import increment_escalation_level

    await set_scheduler_state(db_session, "escalation_level", "5")
    new_level = await increment_escalation_level(db_session)
    assert new_level == 5


async def test_needs_escalation_below_threshold(db_session, mock_config):
    """Stock < low_stock_threshold sur au moins 1 jour → True. [DS-1.4c]"""
    from ancnouv.generator.selector import needs_escalation

    mock_config.content.low_stock_threshold = 3
    # Aucun événement en DB → stock = 0 < 3
    result = await needs_escalation(db_session, mock_config)
    assert result is True


async def test_needs_escalation_above_threshold(db_session, mock_config):
    """Stock ≥ low_stock_threshold pour tous les jours → False. [DS-1.4c]"""
    from datetime import date, timedelta, datetime, timezone
    from ancnouv.db.models import Event
    from ancnouv.db.utils import compute_content_hash
    from ancnouv.generator.selector import needs_escalation

    mock_config.content.low_stock_threshold = 1
    today = date.today()

    # Insérer suffisamment d'événements pour les 7 prochains jours
    for i in range(7):
        target = today + timedelta(days=i)
        ev = Event(
            source="wikipedia",
            source_lang="fr",
            event_type="event",
            month=target.month,
            day=target.day,
            year=1900 + i,
            description=f"Événement du {target}",
            content_hash=compute_content_hash(f"Événement du {target} {i}"),
            status="available",
            published_count=0,
            fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db_session.add(ev)
    await db_session.commit()

    result = await needs_escalation(db_session, mock_config)
    assert result is False


async def test_get_effective_params_level_3_window_already_permissive(db_session, mock_config):
    """Niveau 3 avec dedup_policy='always' → inchangé ('always' est déjà plus permissif). [DS-1.4c]"""
    mock_config.content.deduplication_policy = "always"
    await set_scheduler_state(db_session, "escalation_level", "3")
    params = await get_effective_query_params(db_session, mock_config)
    assert params.dedup_policy == "always"


async def test_get_effective_params_level_4_window_reduces_from_365(db_session, mock_config):
    """Niveau 4 avec dedup_policy='window' et window=365 → réduit à 180. [DS-1.4b]"""
    mock_config.content.deduplication_policy = "window"
    mock_config.content.deduplication_window_days = 365
    await set_scheduler_state(db_session, "escalation_level", "4")
    params = await get_effective_query_params(db_session, mock_config)
    assert params.dedup_window == 180


async def test_get_effective_params_level_3_window_reduces_from_above_365(db_session, mock_config):
    """Niveau 3 avec dedup_policy='window' et window=500 → réduit à 365. [DS-1.4c]"""
    mock_config.content.deduplication_policy = "window"
    mock_config.content.deduplication_window_days = 500
    await set_scheduler_state(db_session, "escalation_level", "3")
    params = await get_effective_query_params(db_session, mock_config)
    assert params.dedup_window == 365


async def test_select_event_dedup_always(db_session, mock_config, db_event):
    """select_event avec dedup_policy='always' → événement retourné même si publié. [SPEC-3.2.1]"""
    from ancnouv.db.models import Event
    from ancnouv.db.utils import compute_content_hash

    db_event.published_count = 5
    await db_session.commit()

    params = EffectiveQueryParams(
        event_types=["event"],
        use_fallback_en=False,
        dedup_policy="always",
        dedup_window=365,
    )
    result = await select_event(db_session, date(db_event.year, db_event.month, db_event.day), params)
    # Avec dedup_policy="always", l'événement est retourné même avec published_count > 0
    # Note : on utilise la date de l'événement db_event (3-21)
    from datetime import date as d
    result = await select_event(db_session, d(2000, db_event.month, db_event.day), params)
    assert result is not None


async def test_select_article_dedup_window(db_session, mock_config, db_article):
    """select_article avec dedup_policy='window' → article eligible retourné. [DB-16]"""
    params = EffectiveQueryParams(
        event_types=["event"],
        use_fallback_en=False,
        dedup_policy="window",
        dedup_window=365,
    )
    result = await select_article(db_session, mock_config, params)
    assert result is not None


async def test_select_article_dedup_always(db_session, mock_config, db_article):
    """select_article avec dedup_policy='always' → article retourné sans filtre dédup. [DB-16]"""
    db_article.published_count = 10
    await db_session.commit()

    params = EffectiveQueryParams(
        event_types=["event"],
        use_fallback_en=False,
        dedup_policy="always",
        dedup_window=365,
    )
    result = await select_article(db_session, mock_config, params)
    assert result is not None


async def test_select_gallica_article_skips_published(db_session, mock_config):
    """Article avec published_count > 0 → non sélectionné. [SPEC-9.3]"""
    from ancnouv.generator.selector import select_gallica_article

    article = _make_gallica_article(
        ark_id="ark:/12148/already_published",
        content_hash="def456",
        published_count=1,
    )
    db_session.add(article)
    await db_session.commit()

    result = await select_gallica_article(db_session, mock_config, EffectiveQueryParams())
    assert result is None


# ─── Filtre d'ancienneté — événements Wikipedia ───────────────────────────────

async def test_select_event_min_age_excludes_recent(db_session, mock_config):
    """min_age_years=50 → événement de l'année courante non sélectionné. [content.event_min_age_years]"""
    today = date.today()
    await _make_event(db_session, today.month, today.day, today.year, "Événement récent")

    params = EffectiveQueryParams(dedup_policy="always", min_age_years=50)
    result = await select_event(db_session, today, params)
    assert result is None


async def test_select_event_max_age_excludes_ancient(db_session, mock_config):
    """max_age_years=100 → événement de l'an 1 non sélectionné. [content.event_max_age_years]"""
    today = date.today()
    await _make_event(db_session, today.month, today.day, 1, "Événement très ancien")

    params = EffectiveQueryParams(dedup_policy="always", max_age_years=100)
    result = await select_event(db_session, today, params)
    assert result is None


async def test_select_event_age_range_selects_within(db_session, mock_config):
    """Événement dans la plage min/max → sélectionné. [content.event_min_age_years, content.event_max_age_years]"""
    today = date.today()
    year_in_range = today.year - 50
    await _make_event(db_session, today.month, today.day, year_in_range, "Événement dans la plage")

    params = EffectiveQueryParams(dedup_policy="always", min_age_years=10, max_age_years=100)
    result = await select_event(db_session, today, params)
    assert result is not None
    assert result.year == year_in_range


# ─── Filtre d'ancienneté — articles Gallica ───────────────────────────────────

async def test_select_gallica_min_age_excludes_recent(db_session, mock_config):
    """min_age_years=50 → article Gallica de l'année courante non sélectionné. [content.event_min_age_years]"""
    from ancnouv.generator.selector import select_gallica_article
    today = date.today()
    article = _make_gallica_article(
        ark_id="ark:/12148/recent",
        content_hash="recent01",
        date_published=date(today.year, 1, 1),
    )
    db_session.add(article)
    await db_session.commit()

    params = EffectiveQueryParams(min_age_years=50)
    result = await select_gallica_article(db_session, mock_config, params)
    assert result is None


async def test_select_gallica_max_age_excludes_ancient(db_session, mock_config):
    """max_age_years=100 → article Gallica de 1850 non sélectionné. [content.event_max_age_years]"""
    from ancnouv.generator.selector import select_gallica_article
    article = _make_gallica_article(
        ark_id="ark:/12148/ancient",
        content_hash="ancient01",
        date_published=date(1850, 6, 15),
    )
    db_session.add(article)
    await db_session.commit()

    params = EffectiveQueryParams(max_age_years=100)
    result = await select_gallica_article(db_session, mock_config, params)
    assert result is None


async def test_select_gallica_age_range_selects_within(db_session, mock_config):
    """Article Gallica dans la plage min/max → sélectionné. [content.event_min_age_years, content.event_max_age_years]"""
    from ancnouv.generator.selector import select_gallica_article
    today = date.today()
    year_in_range = today.year - 80
    article = _make_gallica_article(
        ark_id="ark:/12148/inrange",
        content_hash="inrange01",
        date_published=date(year_in_range, 6, 15),
    )
    db_session.add(article)
    await db_session.commit()

    params = EffectiveQueryParams(min_age_years=50, max_age_years=150)
    result = await select_gallica_article(db_session, mock_config, params)
    assert result is not None
