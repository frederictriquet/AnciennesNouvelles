# Tests unitaires — db/session.py [docs/DATABASE.md]
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_configure_sqlite_pragmas_executes_pragmas():
    """_configure_sqlite_pragmas exécute les 3 PRAGMAs sur la connexion. [DATABASE.md]"""
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    from ancnouv.db.session import _configure_sqlite_pragmas
    _configure_sqlite_pragmas(mock_conn, None)

    assert mock_cursor.execute.call_count == 3
    mock_cursor.close.assert_called_once()


def test_create_engine_returns_async_engine():
    """create_engine(:memory:) retourne un AsyncEngine sans erreur. [DATABASE.md]"""
    from sqlalchemy.ext.asyncio import AsyncEngine
    from ancnouv.db.session import create_engine

    engine = create_engine(":memory:")
    assert isinstance(engine, AsyncEngine)


def test_init_db_sets_session_factory():
    """init_db(:memory:) initialise le moteur et la session factory. [DATABASE.md]"""
    from sqlalchemy.ext.asyncio import AsyncEngine
    import ancnouv.db.session as sess
    from ancnouv.db.session import init_db

    engine = init_db(":memory:")
    assert isinstance(engine, AsyncEngine)
    assert sess._session_factory is not None


async def test_get_session_raises_when_factory_none(monkeypatch):
    """get_session() lève RuntimeError si session factory non initialisée. [DATABASE.md]"""
    import ancnouv.db.session as sess
    monkeypatch.setattr(sess, "_session_factory", None)

    with pytest.raises(RuntimeError, match="Session factory non initialisée"):
        async with sess.get_session():
            pass
