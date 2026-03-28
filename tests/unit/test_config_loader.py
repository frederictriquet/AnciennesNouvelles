# Tests unitaires — config_loader [DASH-10.2]
from __future__ import annotations

import pytest


def test_apply_dot_overrides_simple():
    """Override simple une clé → valeur mise à jour."""
    from ancnouv.config_loader import apply_dot_overrides

    base = {"scheduler": {"max_pending_posts": 1, "auto_publish": False}}
    result = apply_dot_overrides(base, {"scheduler.max_pending_posts": 5})
    assert result["scheduler"]["max_pending_posts"] == 5
    assert result["scheduler"]["auto_publish"] is False


def test_apply_dot_overrides_nested():
    """Override chemin imbriqué profond."""
    from ancnouv.config_loader import apply_dot_overrides

    base = {"content": {"rss": {"enabled": False, "min_delay_days": 90}}}
    result = apply_dot_overrides(base, {"content.rss.enabled": True})
    assert result["content"]["rss"]["enabled"] is True
    assert result["content"]["rss"]["min_delay_days"] == 90


def test_apply_dot_overrides_invalid_path_ignored(caplog):
    """Chemin introuvable → override ignoré, log warning."""
    import logging

    from ancnouv.config_loader import apply_dot_overrides

    base = {"scheduler": {"max_pending_posts": 1}}
    with caplog.at_level(logging.WARNING):
        result = apply_dot_overrides(base, {"nonexistent.key": 99})
    assert "nonexistent.key" in caplog.text
    assert result == {"scheduler": {"max_pending_posts": 1}}


def test_apply_dot_overrides_does_not_mutate_base():
    """apply_dot_overrides ne modifie pas le dict original."""
    from ancnouv.config_loader import apply_dot_overrides

    base = {"scheduler": {"max_pending_posts": 1}}
    base_copy = {"scheduler": {"max_pending_posts": 1}}
    apply_dot_overrides(base, {"scheduler.max_pending_posts": 5})
    assert base == base_copy


def test_apply_dot_overrides_multiple():
    """Plusieurs overrides appliqués en une seule passe."""
    from ancnouv.config_loader import apply_dot_overrides

    base = {"image": {"jpeg_quality": 95, "paper_texture": True}}
    result = apply_dot_overrides(base, {
        "image.jpeg_quality": 75,
        "image.paper_texture": False,
    })
    assert result["image"]["jpeg_quality"] == 75
    assert result["image"]["paper_texture"] is False


def test_invalidate_config_cache():
    """invalidate_config_cache remet le timestamp à 0."""
    import ancnouv.config_loader as cl

    cl._cache_timestamp = 999.0
    cl.invalidate_config_cache()
    assert cl._cache_timestamp == 0.0


# ─── apply_dot_overrides_config (Config instance) ────────────────────────────────
# Modèles minimaux BaseSettings pour tester le mécanisme model_copy
# sans dépendre du Config réel (qui a des validateurs prod complexes).

from pydantic_settings import BaseSettings as _BaseSettings

class _Inner(_BaseSettings):
    event_types: list[str] = ['events']
    enabled: bool = False

class _Outer(_BaseSettings):
    content: _Inner = _Inner()
    max_posts: int = 1


def test_apply_dot_overrides_config_list():
    """Override list sur un BaseSettings imbriqué — ne doit PAS revenir au défaut. [DASH-A4]

    Régression : model_validate() sur un BaseSettings imbriqué réinitialise
    les champs depuis ses propres sources (env, défaut), ignorant le dict passé.
    apply_dot_overrides_config() utilise model_copy() pour éviter ce problème.
    """
    from ancnouv.config_loader import apply_dot_overrides_config

    config = _Outer()
    result = apply_dot_overrides_config(config, {'content.event_types': ['births', 'deaths']})
    assert result.content.event_types == ['births', 'deaths']


def test_apply_dot_overrides_config_nested():
    """Override profond (content.enabled) sur BaseSettings imbriqué."""
    from ancnouv.config_loader import apply_dot_overrides_config

    config = _Outer()
    assert config.content.enabled is False
    result = apply_dot_overrides_config(config, {'content.enabled': True})
    assert result.content.enabled is True


def test_apply_dot_overrides_config_invalid_path_ignored(caplog):
    """Chemin invalide ignoré avec log warning, config inchangée."""
    import logging
    from ancnouv.config_loader import apply_dot_overrides_config

    config = _Outer()
    with caplog.at_level(logging.WARNING):
        result = apply_dot_overrides_config(config, {'nonexistent.field': 99})
    assert 'nonexistent.field' in caplog.text
    assert result.content.event_types == config.content.event_types


def test_apply_dot_overrides_config_does_not_mutate_original():
    """apply_dot_overrides_config ne modifie pas l'instance originale."""
    from ancnouv.config_loader import apply_dot_overrides_config

    config = _Outer()
    original_types = list(config.content.event_types)
    apply_dot_overrides_config(config, {'content.event_types': ['births']})
    assert config.content.event_types == original_types
