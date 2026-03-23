# Utilitaires DB [docs/DATABASE.md — section "db/utils.py"]
from __future__ import annotations

import hashlib
import unicodedata

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def compute_content_hash(description: str) -> str:
    """SHA-256 de NFKC(description).strip().lower() encodée UTF-8.

    [DS-1.7b] Vecteur canonique de test :
    compute_content_hash("  Événement  ") == compute_content_hash("evenement")
    → False (NFKC ne normalise pas les accents, strip seul)
    Vecteur réel :
    compute_content_hash("Napoléon") == compute_content_hash("Napoléon ")
    → True (strip supprime les espaces de fin)
    """
    normalized = unicodedata.normalize("NFKC", description)
    normalized = normalized.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def get_scheduler_state(session: AsyncSession, key: str) -> str | None:
    """Lit une valeur dans scheduler_state. Retourne None si la clé est absente."""
    result = await session.execute(
        text("SELECT value FROM scheduler_state WHERE key = :key"),
        {"key": key},
    )
    row = result.fetchone()
    return row[0] if row else None


async def set_scheduler_state(session: AsyncSession, key: str, value: str) -> None:
    """Écrit ou met à jour une valeur dans scheduler_state.

    Inclut updated_at=CURRENT_TIMESTAMP explicitement car scheduler_state
    n'est pas un modèle ORM (pas de onupdate automatique).
    """
    await session.execute(
        text(
            "INSERT INTO scheduler_state (key, value, updated_at) "
            "VALUES (:key, :value, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated_at = CURRENT_TIMESTAMP"
        ),
        {"key": key, "value": value},
    )
    await session.commit()
