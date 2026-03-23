# Tests unitaires — validation Config Pydantic [SPEC-3.6, docs/CONFIGURATION.md]
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class _TestConfigBase:
    """Config minimale sans fichier YAML ni .env pour les tests."""

    @staticmethod
    def _env():
        return {
            "TELEGRAM_BOT_TOKEN": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh1",
            "META_APP_ID": "123456789",
            "META_APP_SECRET": "abcdef123456",
            "IMAGE_HOSTING__PUBLIC_BASE_URL": "https://images.test-server.example.net",
            "TELEGRAM__AUTHORIZED_USER_IDS": "[123456789]",
        }

    @staticmethod
    def _make_config(**overrides):
        """Crée un Config en patchant les variables d'environnement et les sources fichier."""
        env = _TestConfigBase._env()
        env.update(overrides)
        with (
            patch.dict(os.environ, env, clear=True),
            patch("ancnouv.config.YamlConfigSettingsSource.__call__", return_value={}),
        ):
            from ancnouv.config import Config
            return Config()


def test_default_config_is_valid():
    """Config avec valeurs par défaut et variables minimales est valide."""
    config = _TestConfigBase._make_config()
    assert config.scheduler.max_pending_posts == 1
    assert config.content.image_retention_days == 7


def test_invalid_cron_raises():
    """Cron invalide (heure hors plage) lève ValueError. [CONF-16, T-07]"""
    env = _TestConfigBase._env()
    env["SCHEDULER__GENERATION_CRON"] = "0 */25 * * *"
    with (
        patch.dict(os.environ, env, clear=True),
        patch("ancnouv.config.YamlConfigSettingsSource.__call__", return_value={}),
        pytest.raises(Exception),
    ):
        from ancnouv.config import Config
        Config()


def test_no_platform_valid():
    """instagram.enabled=False + facebook.enabled=False est valide. [CONF-15, T-07]"""
    config = _TestConfigBase._make_config()
    assert not config.instagram.enabled
    assert not config.facebook.enabled


def test_rss_delay_constraint():
    """min_delay_days >= max_age_days lève ValueError. [CONF-08]"""
    env = _TestConfigBase._env()
    env["CONTENT__RSS__MIN_DELAY_DAYS"] = "180"
    env["CONTENT__RSS__MAX_AGE_DAYS"] = "90"
    with (
        patch.dict(os.environ, env, clear=True),
        patch("ancnouv.config.YamlConfigSettingsSource.__call__", return_value={}),
        pytest.raises(Exception),
    ):
        from ancnouv.config import Config
        Config()


def test_backup_keep_ge1():
    """backup_keep >= 1 est requis. [CONF-05]"""
    env = _TestConfigBase._env()
    env["DATABASE__BACKUP_KEEP"] = "0"
    with (
        patch.dict(os.environ, env, clear=True),
        patch("ancnouv.config.YamlConfigSettingsSource.__call__", return_value={}),
        pytest.raises(Exception),
    ):
        from ancnouv.config import Config
        Config()


def test_jpeg_quality_bounds():
    """jpeg_quality doit être entre 1 et 100. [CONF-01]"""
    env = _TestConfigBase._env()
    env["IMAGE__JPEG_QUALITY"] = "0"
    with (
        patch.dict(os.environ, env, clear=True),
        patch("ancnouv.config.YamlConfigSettingsSource.__call__", return_value={}),
        pytest.raises(Exception),
    ):
        from ancnouv.config import Config
        Config()


def test_approval_timeout_bounds():
    """approval_timeout_hours doit être entre 1 et 8760. [CONF-07]"""
    env = _TestConfigBase._env()
    env["SCHEDULER__APPROVAL_TIMEOUT_HOURS"] = "0"
    with (
        patch.dict(os.environ, env, clear=True),
        patch("ancnouv.config.YamlConfigSettingsSource.__call__", return_value={}),
        pytest.raises(Exception),
    ):
        from ancnouv.config import Config
        Config()
