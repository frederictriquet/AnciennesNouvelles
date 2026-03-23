# Session SQLAlchemy async [docs/DATABASE.md — section "Configuration du moteur"]
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Fabrique de sessions — initialisée par init_db(), utilisée par get_session()
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _configure_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Active les PRAGMAs SQLite nécessaires sur chaque nouvelle connexion.

    Exécuté via event listener synchrone car aiosqlite utilise un thread interne —
    les PRAGMAs doivent être configurés au niveau de la connexion SQLite sous-jacente.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA busy_timeout = 10000")
    cursor.close()


def create_engine(db_path: str) -> AsyncEngine:
    """Crée le moteur async SQLAlchemy pour le fichier SQLite donné."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"timeout": 15},
        echo=False,
    )
    # [DATABASE.md] listener synchrone sur sync_engine pour les PRAGMAs
    event.listen(engine.sync_engine, "connect", _configure_sqlite_pragmas)
    return engine


def init_db(db_path: str) -> AsyncEngine:
    """Initialise le moteur et la fabrique de sessions.

    Retourne l'AsyncEngine — nécessaire pour init_context(config, bot_app, engine).
    La DB est créée via Alembic (db init/migrate), pas par create_all() ici.
    """
    global _session_factory
    engine = create_engine(db_path)
    _session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine


def set_session_factory(engine: AsyncEngine) -> None:
    """Rebind la session factory vers un engine donné (tests, context.py)."""
    global _session_factory
    _session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager de session — utiliser exclusivement avec async with.

    Chaque coroutine obtient sa propre session.
    Ne jamais partager une session entre coroutines concurrentes.
    """
    if _session_factory is None:
        raise RuntimeError(
            "Session factory non initialisée — appeler init_db() d'abord"
        )
    async with _session_factory() as session:
        yield session
