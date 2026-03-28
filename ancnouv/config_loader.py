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
    """Charge la Config baseline puis applique les overrides DB. [DASHBOARD.md — Principe]

    La baseline est toujours Config() — ne dépend pas du scheduler, utilisable en CLI.
    """
    global _cache_config, _cache_timestamp

    # Utiliser le singleton scheduler si initialisé (app running), sinon Config() direct (CLI/tests)
    try:
        from ancnouv.scheduler.context import get_config
        baseline = get_config()
    except RuntimeError:
        from ancnouv.config import Config
        baseline = Config()

    try:
        from ancnouv.db.config_store import get_all_overrides
        from ancnouv.db.session import get_session

        async with get_session() as session:
            overrides_flat = await get_all_overrides(session)

        if overrides_flat:
            effective = apply_dot_overrides_config(baseline, overrides_flat)
        else:
            effective = baseline

    except Exception as exc:
        import sys
        print(f"  [config_loader] ERREUR chargement overrides : {exc!r}", file=sys.stderr)
        logger.warning("get_effective_config : erreur chargement overrides (%s) — cache conservé", exc)
        if _cache_config is not None:
            return _cache_config
        effective = baseline

    _cache_config = effective
    _cache_timestamp = time.monotonic()
    return effective


# ─── apply_dot_overrides (Config instance) ───────────────────────────────────────

def apply_dot_overrides_config(config: Any, overrides: dict[str, Any]) -> Any:
    """Applique les overrides dot-path sur une instance Config via model_copy. [DASHBOARD.md]

    Utilise model_copy() au lieu de model_validate() pour éviter la réinitialisation
    des BaseSettings imbriqués depuis leurs sources (env, defaults). [DASH-A4]
    """
    result = config
    for dot_key, value in overrides.items():
        try:
            result = _apply_single_override(result, dot_key.split('.'), value)
        except Exception:
            logger.warning(
                'apply_dot_overrides_config : chemin %r introuvable dans la config — override ignoré',
                dot_key,
            )
    return result


def _apply_single_override(config: Any, segments: list[str], value: Any) -> Any:
    """Applique récursivement un override via model_copy sans retrigger l'init BaseSettings."""
    if len(segments) == 1:
        return config.model_copy(update={segments[0]: value})
    nested = getattr(config, segments[0])
    updated_nested = _apply_single_override(nested, segments[1:], value)
    return config.model_copy(update={segments[0]: updated_nested})


# ─── apply_dot_overrides (dict) ──────────────────────────────────────────────────

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
