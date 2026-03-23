# Contexte partagé (singletons) — Phase 6 [docs/SCHEDULER.md]
# Tous les getters lèvent RuntimeError si non initialisés (pas assert) [SCHEDULER.md].
# init_context() initialise config + bot_app + engine + session factory en une seule passe [SC-C6].
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.ext.asyncio import AsyncEngine
    from telegram.ext import Application

    from ancnouv.config import Config

_config: "Config | None" = None
_engine: "AsyncEngine | None" = None
_scheduler: "AsyncIOScheduler | None" = None
_bot_app: "Application | None" = None


def get_config() -> "Config":
    """Retourne la config applicative. Lève RuntimeError si non initialisée. [SCHEDULER.md]"""
    if _config is None:
        raise RuntimeError(
            "Config non initialisée — appeler init_context() avant get_config()"
        )
    return _config


def set_config(config: "Config") -> None:
    """Initialise la config singleton."""
    global _config
    _config = config


def get_engine() -> "AsyncEngine":
    """Retourne l'engine SQLAlchemy. Lève RuntimeError si non initialisé. [SCHEDULER.md]"""
    if _engine is None:
        raise RuntimeError(
            "Engine non initialisé — appeler init_context() avant get_engine()"
        )
    return _engine


def set_engine(engine: "AsyncEngine") -> None:
    """Initialise l'engine singleton."""
    global _engine
    _engine = engine


def get_scheduler() -> "AsyncIOScheduler":
    """Retourne l'instance APScheduler. Lève RuntimeError si non initialisé. [SCHEDULER.md]"""
    if _scheduler is None:
        raise RuntimeError(
            "Scheduler non initialisé — appeler set_scheduler() avant get_scheduler()"
        )
    return _scheduler


def set_scheduler(scheduler: "AsyncIOScheduler") -> None:
    """Initialise le scheduler singleton. Appelé depuis create_scheduler()."""
    global _scheduler
    _scheduler = scheduler


def get_bot_app() -> "Application":
    """Retourne l'Application PTB. Lève RuntimeError si non initialisée. [SCHEDULER.md]"""
    if _bot_app is None:
        raise RuntimeError(
            "Application PTB non initialisée — appeler init_context() avant get_bot_app()"
        )
    return _bot_app


def set_bot_app(bot_app: "Application") -> None:
    """Initialise le singleton Application PTB."""
    global _bot_app
    _bot_app = bot_app


def init_context(
    config: "Config",
    bot_app: "Application",
    engine: "AsyncEngine",
) -> None:
    """Initialise tous les singletons partagés et rebind la session factory.

    Séquence canonique [SC-C6] : appeler après create_application() + bot_data["config"],
    avant create_scheduler(). Les jobs récupèrent leurs dépendances via les getters —
    jamais via args= ou kwargs= APScheduler [SCHEDULER.md — Configuration APScheduler].

    La session factory est rebindée vers engine via set_session_factory() — les appels
    à get_session() après init_context() utilisent le bon engine [SCHEDULER.md].
    """
    set_config(config)
    set_bot_app(bot_app)
    set_engine(engine)
    from ancnouv.db.session import set_session_factory
    set_session_factory(engine)
