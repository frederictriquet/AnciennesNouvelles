# Sélecteur de contenu et stratégie d'escalade — Phase 2/3 [DS-1.4b, DS-1.4c, TRANSVERSAL-1]
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ancnouv.config import Config
from ancnouv.db.models import Event, RssArticle
from ancnouv.db.utils import get_scheduler_state, set_scheduler_state
from ancnouv.fetchers.base import EffectiveQueryParams

logger = logging.getLogger(__name__)


async def increment_escalation_level(session: AsyncSession) -> int:
    """Incrémente escalation_level dans scheduler_state (max 5). [DS-1.4b, DS-1.4c]

    Cliquet à sens unique — ne redescend jamais automatiquement.
    Réinitialisation uniquement via `python -m ancnouv escalation reset`.
    """
    current_raw = await get_scheduler_state(session, "escalation_level")
    current = int(current_raw or "0")
    new_level = min(current + 1, 5)
    await set_scheduler_state(session, "escalation_level", str(new_level))
    logger.warning("Niveau d'escalade : %d → %d", current, new_level)
    return new_level


async def get_effective_query_params(
    session: AsyncSession, config: Config
) -> EffectiveQueryParams:
    """Paramètres effectifs selon le niveau d'escalade actuel. [DS-1.4b, DS-1.4c, ARCH-09]

    L'escalade ne peut qu'assouplir la config de base, jamais la durcir [DS-1.4c].
    Les niveaux sont cumulatifs (niveau 4 inclut les effets du niveau 3, etc.).
    """
    level_raw = await get_scheduler_state(session, "escalation_level")
    level = int(level_raw or "0")

    # Base depuis config
    event_types = list(config.content.wikipedia_event_types)
    use_fallback_en = False
    dedup_policy = config.content.deduplication_policy
    dedup_window = config.content.deduplication_window_days

    if level >= 1:
        # [DS-1.4b] Niveau 1 : ajouter births et deaths (noms API)
        for api_type in ("births", "deaths"):
            if api_type not in event_types:
                event_types.append(api_type)

    if level >= 2:
        # [DS-1.4b] Niveau 2 : EN inconditionnel
        use_fallback_en = True

    if level >= 3:
        # [DS-1.4b] Niveau 3 : déduplication window 365j
        # N'applique que si la config est plus restrictive [DS-1.4c]
        if dedup_policy == "never":
            dedup_policy = "window"
            dedup_window = 365
        elif dedup_policy == "window" and dedup_window > 365:
            dedup_window = 365
        # "always" reste "always" — déjà plus permissif que window

    if level >= 4:
        # [DS-1.4b] Niveau 4 : déduplication window 180j (plus permissif que 365j)
        # Fenêtre plus courte = plus d'événements éligibles [DS-1.4b aparté]
        if dedup_policy == "never":
            dedup_policy = "window"
            dedup_window = 180
        elif dedup_policy == "window" and dedup_window > 180:
            dedup_window = 180

    return EffectiveQueryParams(
        event_types=event_types,
        use_fallback_en=use_fallback_en,
        dedup_policy=dedup_policy,
        dedup_window=dedup_window,
    )


async def select_event(
    session: AsyncSession,
    target_date: date,
    effective_params: EffectiveQueryParams,
) -> Event | None:
    """Sélection aléatoire d'un événement disponible pour target_date. [TRANSVERSAL-1, SPEC-3.2.1]

    Politique de déduplication appliquée depuis effective_params.
    - never : published_count = 0
    - window : last_used_at IS NULL OU < cutoff
    - always : sans filtre dédup
    """
    dedup_policy = effective_params.dedup_policy
    params: dict = {"month": target_date.month, "day": target_date.day}

    if dedup_policy == "never":
        dedup_clause = "AND published_count = 0"
    elif dedup_policy == "window":
        cutoff = datetime.now(timezone.utc) - timedelta(days=effective_params.dedup_window)
        dedup_clause = "AND (last_used_at IS NULL OR last_used_at < :cutoff)"
        params["cutoff"] = cutoff
    else:  # "always"
        dedup_clause = ""

    result = await session.execute(
        text(
            "SELECT id FROM events "
            "WHERE month = :month AND day = :day AND status = 'available' "
            f"{dedup_clause} "
            "ORDER BY RANDOM() LIMIT 1"
        ),
        params,
    )
    row = result.fetchone()
    if row is None:
        return None
    return await session.get(Event, row[0])


async def select_article(
    session: AsyncSession,
    config: Config,
    effective_params: EffectiveQueryParams,
) -> RssArticle | None:
    """Sélection aléatoire d'un article RSS éligible. [DB-16, DB-17, DS-2.5]

    Contrainte temporelle : fetched_at <= today - min_delay_days (date de collecte).
    Politique de déduplication depuis effective_params. [DB-18]
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.content.rss.min_delay_days)
    dedup_policy = effective_params.dedup_policy
    params: dict = {"cutoff": cutoff}

    if dedup_policy == "never":
        dedup_clause = "AND published_count = 0"
    elif dedup_policy == "window":
        window_cutoff = now - timedelta(days=effective_params.dedup_window)
        dedup_clause = "AND (last_used_at IS NULL OR last_used_at < :window_cutoff)"
        params["window_cutoff"] = window_cutoff
    else:  # "always"
        dedup_clause = ""

    result = await session.execute(
        text(
            "SELECT id FROM rss_articles "
            "WHERE status = 'available' AND fetched_at <= :cutoff "
            f"{dedup_clause} "
            "ORDER BY RANDOM() LIMIT 1"
        ),
        params,
    )
    row = result.fetchone()
    if row is None:
        return None
    return await session.get(RssArticle, row[0])


async def needs_escalation(session: AsyncSession, config: Config) -> bool:
    """Retourne True si au moins une des 7 prochaines dates a un stock < low_stock_threshold.

    [DS-1.4c] Comptage par date individuelle (pas total 7j).
    Requête fixe published_count = 0 — mesure conservatrice intentionnelle [DS-1.4b note].
    Déclenché depuis job_fetch_wiki après chaque collecte [DS-1.4c].
    """
    today = date.today()
    threshold = config.content.low_stock_threshold

    for i in range(7):
        target = today + timedelta(days=i)
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM events "
                "WHERE month = :month AND day = :day "
                "AND status = 'available' AND published_count = 0"
            ),
            {"month": target.month, "day": target.day},
        )
        count = result.scalar() or 0
        if count < threshold:
            return True

    return False
