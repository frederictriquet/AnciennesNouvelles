# Tests unitaires — formatage de légendes [SPEC-2.3, IMG-7]
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from freezegun import freeze_time

from ancnouv.generator.caption import format_caption, format_caption_rss, truncate_caption
from ancnouv.utils.date_helpers import compute_time_ago, format_historical_date


# ─── compute_time_ago ────────────────────────────────────────────────────────────

@freeze_time("2026-03-21")
def test_compute_time_ago_less_than_one_month():
    """(2026, 3, 10) → "Il y a moins d'un mois". [SPEC-2.2]"""
    from datetime import date
    result = compute_time_ago(date(2026, 3, 10))
    assert result == "Il y a moins d'un mois"


@freeze_time("2026-03-21")
def test_compute_time_ago_three_months():
    """(2025, 12, 21) → "Il y a 3 mois". [SPEC-2.2]"""
    from datetime import date
    result = compute_time_ago(date(2025, 12, 21))
    assert result == "Il y a 3 mois"


@freeze_time("2026-03-21")
def test_compute_time_ago_one_year():
    """(2025, 3, 21) → "Il y a 1 an". [SPEC-2.2]"""
    from datetime import date
    result = compute_time_ago(date(2025, 3, 21))
    assert result == "Il y a 1 an"


@freeze_time("2026-03-21")
def test_compute_time_ago_two_years():
    """(2024, 3, 21) → "Il y a 2 ans" (pluriel). [SPEC-2.2]"""
    from datetime import date
    result = compute_time_ago(date(2024, 3, 21))
    assert result == "Il y a 2 ans"


@freeze_time("2026-03-21")
def test_compute_time_ago_ten_years():
    """(2016, 3, 21) → "Il y a 10 ans". [SPEC-2.2]"""
    from datetime import date
    result = compute_time_ago(date(2016, 3, 21))
    assert result == "Il y a 10 ans"


@freeze_time("2026-03-21")
def test_compute_time_ago_day_adjustment():
    """today.day (21) < event_date.day (25) → total_months décrémenté. [SPEC-2.2]"""
    from datetime import date
    # years_diff=1, months_diff=0, total_months=12, mais 21 < 25 → total_months=11
    result = compute_time_ago(date(2025, 3, 25))
    assert result == "Il y a 11 mois"


# ─── format_historical_date ──────────────────────────────────────────────────────

def test_format_historical_date_positive():
    """Année positive → "21 mars 2016". [SPEC-2.2]"""
    from datetime import date
    result = format_historical_date(date(2016, 3, 21))
    assert result == "21 mars 2016"


def test_format_historical_date_early():
    """Année positive ancienne → format correct. [SPEC-2.2]"""
    from datetime import date
    result = format_historical_date(date(100, 7, 4))
    assert "4" in result
    assert "juillet" in result
    assert "100" in result


# ─── truncate_caption ────────────────────────────────────────────────────────────

def test_truncate_caption_long():
    """Texte > 300 chars → résultat <= 300 chars avec "..." en fin."""
    text = "a " * 200  # 400 chars
    result = truncate_caption(text)
    assert len(result) <= 300
    assert result.endswith("...")


def test_truncate_caption_exact_300():
    """Texte de exactement 300 chars → retourné sans modification."""
    text = "x" * 300
    result = truncate_caption(text)
    assert result == text
    assert not result.endswith("...")


def test_truncate_caption_short():
    """Texte court → retourné intact."""
    text = "Court texte."
    assert truncate_caption(text) == text


# ─── format_caption ──────────────────────────────────────────────────────────────

@pytest.fixture
def _mock_event():
    ev = MagicMock()
    ev.year = 1871
    ev.month = 3
    ev.day = 18
    ev.description = "La Commune de Paris est proclamée."
    ev.source_lang = "fr"
    ev.wikipedia_url = None
    return ev


@pytest.fixture
def _mock_config():
    config = MagicMock()
    config.caption.hashtags = ["#histoire", "#onthisday"]
    config.caption.hashtags_separator = "\n\n"
    config.caption.source_template_fr = "Source : Wikipédia"
    config.caption.source_template_en = "Source : Wikipedia (EN)"
    config.caption.include_wikipedia_url = False
    return config


@freeze_time("2026-03-21")
def test_format_caption_fr(_mock_event, _mock_config):
    """Caption FR → contient 'Wikipédia' et des '#'. [SPEC-2.3]"""
    result = format_caption(_mock_event, _mock_config)
    assert "Wikipédia" in result
    assert "#" in result


@freeze_time("2026-03-21")
def test_format_caption_en(_mock_event, _mock_config):
    """Caption EN → contient 'Wikipedia (EN)'. [SPEC-2.3]"""
    _mock_event.source_lang = "en"
    result = format_caption(_mock_event, _mock_config)
    assert "Wikipedia (EN)" in result


@freeze_time("2026-03-21")
def test_format_caption_contains_time_ago(_mock_event, _mock_config):
    """Caption contient la formule temporelle. [SPEC-2.3]"""
    result = format_caption(_mock_event, _mock_config)
    assert "Il y a" in result


# ─── format_caption_rss ──────────────────────────────────────────────────────────

@pytest.fixture
def _mock_article():
    article = MagicMock()
    article.title = "Titre de l'article RSS"
    article.summary = "Résumé de l'article."
    article.feed_name = "Le Monde"
    article.published_at = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    return article


@freeze_time("2026-03-21")
def test_format_caption_rss_contains_feed_name(_mock_article, _mock_config):
    """Caption RSS contient le feed_name. [IMG-7, SPEC-2.3]"""
    result = format_caption_rss(_mock_article, _mock_config)
    assert "Le Monde" in result
    assert "Source :" in result


@freeze_time("2026-03-21")
def test_format_caption_rss_contains_title(_mock_article, _mock_config):
    """Caption RSS contient le titre de l'article. [SPEC-2.3]"""
    result = format_caption_rss(_mock_article, _mock_config)
    assert "Titre de l'article RSS" in result


@freeze_time("2026-03-21")
def test_format_caption_rss_contains_hashtags(_mock_article, _mock_config):
    """Caption RSS contient des hashtags. [SPEC-2.3]"""
    result = format_caption_rss(_mock_article, _mock_config)
    assert "#" in result


@freeze_time("2026-03-21")
def test_format_caption_rss_long_summary(_mock_article, _mock_config):
    """Résumé > 300 chars → tronqué, total < 2200 chars. [IMG-7]"""
    _mock_article.summary = "X " * 200  # 400 chars
    result = format_caption_rss(_mock_article, _mock_config)
    assert len(result) <= 2200


@freeze_time("2026-03-21")
def test_format_caption_rss_contains_time_ago(_mock_article, _mock_config):
    """Caption RSS contient la formule temporelle (obligatoire). [IMG-7]"""
    result = format_caption_rss(_mock_article, _mock_config)
    assert "Il y a" in result


# ─── format_caption (cas limites) ────────────────────────────────────────────────

@freeze_time("2026-03-21")
def test_format_caption_bc_date(_mock_config):
    """Événement av. J.-C. (year <= 0) → formule 'Il y a X ans'. [IMAGE_GENERATION.md]"""
    from ancnouv.generator.caption import format_caption
    ev = MagicMock()
    ev.year = -44   # 44 av. J.-C. (mort de César)
    ev.month = 3
    ev.day = 15
    ev.description = "César est assassiné."
    ev.source_lang = "fr"
    ev.wikipedia_url = None

    result = format_caption(ev, _mock_config)
    assert "Il y a" in result
    assert "av. J.-C." in result


@freeze_time("2026-03-21")
def test_format_caption_includes_wikipedia_url(_mock_event, _mock_config):
    """include_wikipedia_url=True → URL présente dans la légende. [SPEC-2.3]"""
    _mock_event.wikipedia_url = "https://fr.wikipedia.org/wiki/Commune_de_Paris"
    _mock_config.caption.include_wikipedia_url = True

    result = format_caption(_mock_event, _mock_config)
    assert "https://fr.wikipedia.org" in result


@freeze_time("2026-03-21")
def test_format_caption_truncates_at_2200(_mock_event, _mock_config):
    """Caption > 2200 chars → tronquée à 2200. [IMG-14]"""
    _mock_event.description = "x " * 2000  # très long
    result = format_caption(_mock_event, _mock_config)
    assert len(result) <= 2200


@freeze_time("2026-03-21")
def test_format_caption_rss_truncates_at_2200(_mock_article, _mock_config):
    """Caption RSS > 2200 chars → tronquée à 2200. [IMG-14]"""
    _mock_article.summary = "y " * 2000  # très long
    result = format_caption_rss(_mock_article, _mock_config)
    assert len(result) <= 2200


# ─── time_ago_from_ymd — chemins de fallback ─────────────────────────────────

def test_time_ago_int_invalid_date_triggers_fallback():
    """Mois invalide (13) → ValueError attrapé, fallback calcul. [IMAGE_GENERATION.md]"""
    from ancnouv.utils.date_helpers import time_ago_from_ymd
    # month=13 → date() lève ValueError → fallback
    result = time_ago_from_ymd(2000, 13, 1)
    assert "Il y a" in result


def test_time_ago_int_year_above_9999():
    """Année > 9999 → branche fallback directe. [IMAGE_GENERATION.md]"""
    from ancnouv.utils.date_helpers import time_ago_from_ymd
    result = time_ago_from_ymd(10000, 1, 1)
    assert "Il y a" in result


@freeze_time("2026-03-21")
def test_time_ago_int_fallback_delta_zero():
    """Année actuelle (delta=0) → 'Il y a moins d'un an'. [IMAGE_GENERATION.md]"""
    from ancnouv.utils.date_helpers import time_ago_from_ymd
    # Mois invalide + année courante → delta = 0
    result = time_ago_from_ymd(2026, 13, 1)
    assert "Il y a moins d'un an" in result


@freeze_time("2026-03-21")
def test_time_ago_int_fallback_one_year():
    """Année invalide (delta=1) → 'Il y a 1 an'. [IMAGE_GENERATION.md]"""
    from ancnouv.utils.date_helpers import time_ago_from_ymd
    result = time_ago_from_ymd(2025, 13, 1)
    assert "Il y a 1 an" in result


# ─── _time_ago_from_datetime — chemin d'exception ────────────────────────────

@freeze_time("2026-03-21")
def test_time_ago_from_datetime_fallback_exception():
    """Quand compute_time_ago lève, le fallback calcul est utilisé. [IMAGE_GENERATION.md]"""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from ancnouv.generator.caption import _time_ago_from_datetime

    dt = datetime(2025, 3, 21, tzinfo=timezone.utc)
    with patch("ancnouv.utils.date_helpers.compute_time_ago", side_effect=RuntimeError("mock")):
        result = _time_ago_from_datetime(dt)
    assert "Il y a" in result


@freeze_time("2026-03-21")
def test_time_ago_from_datetime_fallback_recent():
    """Fallback < 30 jours → 'Il y a moins d'un mois'. [IMAGE_GENERATION.md]"""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from ancnouv.generator.caption import _time_ago_from_datetime

    dt = datetime(2026, 3, 10, tzinfo=timezone.utc)
    with patch("ancnouv.utils.date_helpers.compute_time_ago", side_effect=RuntimeError("mock")):
        result = _time_ago_from_datetime(dt)
    assert result == "Il y a moins d'un mois"


# ─── format_caption_gallica ───────────────────────────────────────────────────────

def test_format_caption_gallica_full(mock_config):
    """Légende Mode C avec titre, description et source_name. [SPEC-9.3]"""
    from datetime import date
    from unittest.mock import MagicMock
    from ancnouv.generator.caption import format_caption_gallica

    article = MagicMock()
    article.title = "Le Figaro"
    article.description = "Compte rendu des séances parlementaires."
    article.date_published = date(1900, 3, 15)
    article.source_name = "Imprimerie nationale"

    mock_config.caption.hashtags = ["#histoire"]
    mock_config.caption.hashtags_separator = "\n\n"

    result = format_caption_gallica(article, mock_config)

    assert "Le Figaro" in result
    assert "Compte rendu" in result
    assert "15 mars 1900" in result
    assert "BnF Gallica — Imprimerie nationale" in result
    assert "#histoire" in result


def test_format_caption_gallica_no_description(mock_config):
    """Légende Mode C sans description → titre + date + source. [SPEC-9.3]"""
    from datetime import date
    from unittest.mock import MagicMock
    from ancnouv.generator.caption import format_caption_gallica

    article = MagicMock()
    article.title = "Journal officiel"
    article.description = None
    article.date_published = date(1914, 8, 1)
    article.source_name = None

    mock_config.caption.hashtags = []
    mock_config.caption.hashtags_separator = "\n\n"

    result = format_caption_gallica(article, mock_config)

    assert "Journal officiel" in result
    assert "1 août 1914" in result
    assert "Source : BnF Gallica\n" in result or result.endswith("Source : BnF Gallica")


def test_format_caption_gallica_date_fallback(mock_config):
    """date_published sans méthode strftime → str() utilisée. [SPEC-9.3]"""
    from unittest.mock import MagicMock
    from ancnouv.generator.caption import format_caption_gallica

    article = MagicMock()
    article.title = "Gazette"
    article.description = None
    article.source_name = None
    # date_published sans strftime → déclenche le else de hasattr
    del article.date_published.strftime
    article.date_published.__str__ = lambda self: "1900-03-15"

    mock_config.caption.hashtags = []
    mock_config.caption.hashtags_separator = "\n\n"

    result = format_caption_gallica(article, mock_config)
    assert "Gazette" in result


@freeze_time("2026-03-21")
def test_time_ago_from_datetime_fallback_months():
    """Fallback 1-11 mois → 'Il y a N mois'. [IMAGE_GENERATION.md]"""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from ancnouv.generator.caption import _time_ago_from_datetime

    dt = datetime(2025, 12, 21, tzinfo=timezone.utc)  # ~3 mois
    with patch("ancnouv.utils.date_helpers.compute_time_ago", side_effect=RuntimeError("mock")):
        result = _time_ago_from_datetime(dt)
    assert "Il y a" in result and "mois" in result


@freeze_time("2026-03-21")
def test_time_ago_from_datetime_fallback_years():
    """Fallback >= 12 mois → 'Il y a X an(s)'. [IMAGE_GENERATION.md]"""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from ancnouv.generator.caption import _time_ago_from_datetime

    dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with patch("ancnouv.utils.date_helpers.compute_time_ago", side_effect=RuntimeError("mock")):
        result = _time_ago_from_datetime(dt)
    assert "Il y a" in result and "an" in result


# ─── Troncature globale à 2200 chars — via hashtags longs ────────────────────

@freeze_time("2026-03-21")
def test_format_caption_global_truncation_triggered(_mock_event, _mock_config):
    """Caption > 2200 chars (via hashtags longs) → tronquée à exactement 2200. [IMG-14]"""
    _mock_config.caption.hashtags = ["#" + "x" * 20] * 200
    result = format_caption(_mock_event, _mock_config)
    assert len(result) == 2200


@freeze_time("2026-03-21")
def test_format_caption_rss_global_truncation_triggered(_mock_article, _mock_config):
    """Caption RSS > 2200 chars (via hashtags longs) → tronquée à exactement 2200. [IMG-14]"""
    _mock_config.caption.hashtags = ["#" + "x" * 20] * 200
    result = format_caption_rss(_mock_article, _mock_config)
    assert len(result) == 2200
