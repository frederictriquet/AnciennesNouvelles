# Tests unitaires — scheduler/context.py [SCHEDULER.md]
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_get_config_raises_when_not_initialized(monkeypatch):
    """get_config() lève RuntimeError si _config est None. [SCHEDULER.md]"""
    import ancnouv.scheduler.context as ctx
    monkeypatch.setattr(ctx, "_config", None)
    with pytest.raises(RuntimeError, match="Config non initialisée"):
        ctx.get_config()


def test_get_engine_raises_when_not_initialized(monkeypatch):
    """get_engine() lève RuntimeError si _engine est None. [SCHEDULER.md]"""
    import ancnouv.scheduler.context as ctx
    monkeypatch.setattr(ctx, "_engine", None)
    with pytest.raises(RuntimeError, match="Engine non initialisé"):
        ctx.get_engine()


def test_get_bot_app_raises_when_not_initialized(monkeypatch):
    """get_bot_app() lève RuntimeError si _bot_app est None. [SCHEDULER.md]"""
    import ancnouv.scheduler.context as ctx
    monkeypatch.setattr(ctx, "_bot_app", None)
    with pytest.raises(RuntimeError, match="Application PTB non initialisée"):
        ctx.get_bot_app()


def test_set_get_scheduler():
    """set_scheduler + get_scheduler roundtrip. [SCHEDULER.md]"""
    import ancnouv.scheduler.context as ctx
    mock_sched = MagicMock()
    ctx.set_scheduler(mock_sched)
    assert ctx.get_scheduler() is mock_sched


def test_set_get_bot_app():
    """set_bot_app + get_bot_app roundtrip. [SCHEDULER.md]"""
    import ancnouv.scheduler.context as ctx
    mock_app = MagicMock()
    ctx.set_bot_app(mock_app)
    assert ctx.get_bot_app() is mock_app


def test_init_context_sets_all_singletons():
    """init_context() initialise config + bot_app + engine en une passe. [SC-C6]"""
    import ancnouv.scheduler.context as ctx

    mock_config = MagicMock()
    mock_app = MagicMock()
    mock_engine = MagicMock()

    with patch("ancnouv.db.session.set_session_factory"):
        ctx.init_context(mock_config, mock_app, mock_engine)

    assert ctx._config is mock_config
    assert ctx._bot_app is mock_app
    assert ctx._engine is mock_engine
