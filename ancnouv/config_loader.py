# Config overlay : YAML baseline + overrides DB [DASH-10.2, DASHBOARD.md]
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ─── Cache ───────────────────────────────────────────────────────────────────────

_cache_config: Any = None          # instance Config ou None
_cache_timestamp: float = 0.0      # epoch seconds
_CACHE_TTL: float = 30.0           # secondes [DASH-A1]


def invalidate_config_cache() -> None:
    """Invalide le cache pour forcer un rechargement au prochain get_effective_config()."""
    global _cache_timestamp
    _cache_timestamp = 0.0


# ─── API publique ─────────────────────────────────────────────────────────────────

async def get_effective_config() -> Any:
    """Retourne la Config effective : YAML baseline + overrides DB, TTL 30s. [DASH-A1]

    Comportement dégradé [DASH-A2] :
    - DB inaccessible → retourne le dernier Config en cache (ou baseline seule)
    - Override invalide → ignoré avec log warning
    - `config_reload_requested` en DB → rechargement immédiat
    """
    global _cache_config, _cache_timestamp

    now = time.monotonic()
    if _cache_config is not None and (now - _cache_timestamp) < _CACHE_TTL:
        # Vérifie le flag config_reload_requested avant de servir le cache
        if not await _reload_requested():
            return _cache_config

    return await _reload()


# ─── Implémentation ───────────────────────────────────────────────────────────────

async def _reload_requested() -> bool:
    """Vérifie si config_reload_requested == 'true' dans scheduler_state et le remet à false."""
    try:
        from ancnouv.db.session import get_session
        from ancnouv.db.utils import get_scheduler_state, set_scheduler_state

        async with get_session() as session:
            flag = await get_scheduler_state(session, "config_reload_requested")
            if flag == "true":
                await set_scheduler_state(session, "config_reload_requested", "false")
                return True
        return False
    except Exception:
        return False


async def _reload() -> Any:
    """Charge la Config baseline puis applique les overrides DB. [DASHBOARD.md — Principe]"""
    global _cache_config, _cache_timestamp

    from ancnouv.scheduler.context import get_config

    baseline = get_config()

    try:
        from ancnouv.db.config_store import get_all_overrides
        from ancnouv.db.session import get_session

        async with get_session() as session:
            overrides_flat = await get_all_overrides(session)

        if overrides_flat:
            base_dict = baseline.model_dump()
            merged = apply_dot_overrides(base_dict, overrides_flat)
            effective = type(baseline).model_validate(merged)
        else:
            effective = baseline

    except Exception as exc:
        logger.warning("get_effective_config : erreur chargement overrides (%s) — cache conservé", exc)
        if _cache_config is not None:
            return _cache_config
        effective = baseline

    _cache_config = effective
    _cache_timestamp = time.monotonic()
    return effective


# ─── apply_dot_overrides ─────────────────────────────────────────────────────────

def apply_dot_overrides(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge des overrides dot-path dans le dict base (deep). [DASHBOARD.md — Contrat]

    Règles :
    - "a.b.c" → base["a"]["b"]["c"] = valeur
    - Si un segment intermédiaire n'existe pas → KeyError → override ignoré + log warning [DASH-A3]
    - Ne supprime jamais les clés absentes des overrides
    """
    import copy
    result = copy.deepcopy(base)

    for dot_key, value in overrides.items():
        segments = dot_key.split(".")
        try:
            target = result
            for seg in segments[:-1]:
                if not isinstance(target, dict) or seg not in target:
                    raise KeyError(seg)
                target = target[seg]
            if not isinstance(target, dict):
                raise KeyError(segments[-2])
            target[segments[-1]] = value
        except (KeyError, TypeError):
            logger.warning(
                "apply_dot_overrides : chemin %r introuvable dans la config baseline — override ignoré",
                dot_key,
            )

    return result
