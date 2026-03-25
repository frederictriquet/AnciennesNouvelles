# Accès CRUD à la table config_overrides [DASH-10.1]
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Types supportés pour value_type [DASHBOARD.md — Sémantique des colonnes]
VALID_VALUE_TYPES = frozenset({"str", "int", "float", "bool", "list", "dict"})


async def get_all_overrides(session: AsyncSession) -> dict[str, Any]:
    """Retourne tous les overrides désérialisés sous forme {dot.key: valeur_Python}."""
    result = await session.execute(
        text("SELECT key, value, value_type FROM config_overrides ORDER BY key")
    )
    rows = result.fetchall()
    overrides: dict[str, Any] = {}
    for key, value, value_type in rows:
        try:
            overrides[key] = _deserialize(value, value_type)
        except Exception:
            logger.warning("config_overrides : override invalide ignoré (key=%s)", key)
    return overrides


async def get_override(session: AsyncSession, key: str) -> Any | None:
    """Retourne la valeur désérialisée d'un override, ou None si absent."""
    result = await session.execute(
        text("SELECT value, value_type FROM config_overrides WHERE key = :key"),
        {"key": key},
    )
    row = result.fetchone()
    if row is None:
        return None
    try:
        return _deserialize(row.value, row.value_type)
    except Exception:
        logger.warning("config_overrides : override invalide ignoré (key=%s)", key)
        return None


async def set_override(
    session: AsyncSession, key: str, value: Any, value_type: str
) -> None:
    """Insère ou met à jour un override (UPSERT). [DASHBOARD.md — POST /config/set]"""
    if value_type not in VALID_VALUE_TYPES:
        raise ValueError(f"value_type invalide : {value_type!r}")
    serialized = _serialize(value, value_type)
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    await session.execute(
        text(
            "INSERT INTO config_overrides (key, value, value_type, updated_at) "
            "VALUES (:key, :value, :value_type, :updated_at) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, "
            "value_type = excluded.value_type, "
            "updated_at = excluded.updated_at"
        ),
        {"key": key, "value": serialized, "value_type": value_type, "updated_at": now},
    )
    await session.commit()


async def delete_override(session: AsyncSession, key: str) -> bool:
    """Supprime un override. Retourne True si une ligne a été supprimée."""
    result = await session.execute(
        text("DELETE FROM config_overrides WHERE key = :key"), {"key": key}
    )
    await session.commit()
    return result.rowcount > 0


# ─── Sérialisation / désérialisation ────────────────────────────────────────────

def _serialize(value: Any, value_type: str) -> str:
    """Encode une valeur Python en chaîne JSON selon value_type. [DASHBOARD.md — Conventions d'encodage]"""
    return json.dumps(value)


def _deserialize(raw: str, value_type: str) -> Any:
    """Décode une chaîne JSON en valeur Python selon value_type."""
    parsed = json.loads(raw)
    if value_type == "str" and not isinstance(parsed, str):
        raise TypeError(f"Attendu str, obtenu {type(parsed).__name__}")
    if value_type == "int" and not isinstance(parsed, int):
        raise TypeError(f"Attendu int, obtenu {type(parsed).__name__}")
    if value_type == "float" and not isinstance(parsed, (int, float)):
        raise TypeError(f"Attendu float, obtenu {type(parsed).__name__}")
    if value_type == "bool" and not isinstance(parsed, bool):
        raise TypeError(f"Attendu bool, obtenu {type(parsed).__name__}")
    if value_type == "list" and not isinstance(parsed, list):
        raise TypeError(f"Attendu list, obtenu {type(parsed).__name__}")
    if value_type == "dict" and not isinstance(parsed, dict):
        raise TypeError(f"Attendu dict, obtenu {type(parsed).__name__}")
    return parsed
