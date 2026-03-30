# Utilitaires de date [SPEC-2.2, docs/ARCHITECTURE.md — utils/date_helpers.py]
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


def compute_time_ago(event_date: date, today: date | None = None) -> str:
    """Calcule la formule temporelle "Il y a X ans/mois" selon SPEC-2.2.

    [SPEC-2.2] Tableau des formulations :
    - < 1 mois → "Il y a moins d'un mois"
    - 1–11 mois → "Il y a N mois"
    - 1 an exactement (même MM/JJ) → "Il y a 1 an"
    - 2 ans et plus → "Il y a N ans"

    Mode A (même MM/JJ) : toujours multiple de 12 mois → N ans.
    Mode B : calcul en mois glissants.
    """
    if today is None:
        today = date.today()

    # Calcul de l'écart en mois
    years_diff = today.year - event_date.year
    months_diff = today.month - event_date.month

    total_months = years_diff * 12 + months_diff

    # Ajustement si le jour du mois n'est pas encore atteint
    if today.day < event_date.day:
        total_months -= 1

    if total_months < 1:
        return "Il y a moins d'un mois"

    if total_months < 12:
        return f"Il y a {total_months} mois"

    years = total_months // 12
    if years == 1:
        return "Il y a 1 an"
    return f"Il y a {years} ans"


def time_ago_from_ymd(year: int, month: int, day: int) -> str:
    """Formule temporelle depuis des entiers year/month/day. Gère les années négatives."""
    today = date.today()
    if year <= 0:
        # Av. J.-C. : abs(year) + today.year [IMAGE_GENERATION.md]
        return f"Il y a {abs(year) + today.year} ans"
    if 1 <= year <= 9999:
        try:
            return compute_time_ago(date(year, month, day), today)
        except (ValueError, OverflowError) as exc:
            logger.debug("time_ago_from_ymd : compute_time_ago(%d) — fallback (%s)", year, exc)
    # Fallback (année hors plage date Python)
    delta = today.year - year
    if delta <= 0:
        return "Il y a moins d'un an"
    return "Il y a 1 an" if delta == 1 else f"Il y a {delta} ans"


def format_historical_date(event_date: date) -> str:
    """Formate une date historique en français.

    Ex : date(1871, 3, 21) → "21 mars 1871"
    Gère les années négatives (avant J.-C.) : -44 → "44 av. J.-C."
    """
    _MOIS = [
        "", "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ]
    month_name = _MOIS[event_date.month]
    year = event_date.year

    if year < 0:
        return f"{event_date.day} {month_name} {abs(year)} av. J.-C."
    return f"{event_date.day} {month_name} {year}"
