# Utilitaires texte [docs/ARCHITECTURE.md — utils/text_helpers.py]
from __future__ import annotations


def truncate(text: str, max_chars: int, suffix: str = "...") -> str:
    """Tronque un texte à max_chars caractères, en ajoutant suffix si nécessaire."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(suffix)] + suffix


def truncate_for_image(text: str, max_chars: int = 500) -> str:
    """Pré-filtre à 500 chars avant génération d'image. Tronque au dernier mot entier. [IMAGE_GENERATION.md]"""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(" ", 1)[0]
    return truncated + "…"


def clean_text(text: str) -> str:
    """Normalise les espaces et supprime les caractères de contrôle."""
    import unicodedata
    # Supprime les caractères de contrôle (sauf newlines)
    cleaned = "".join(
        c for c in text
        if not unicodedata.category(c).startswith("C") or c in "\n\t"
    )
    # Normalise les espaces multiples (pas les newlines)
    import re
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()
