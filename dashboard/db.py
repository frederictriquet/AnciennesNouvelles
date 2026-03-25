# Couche DB du dashboard — accès SQLite direct, sans réimporter ancnouv [DASHBOARD.md]
from __future__ import annotations

import os

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DB_PATH = os.environ.get("DB_PATH", "data/ancnouv.db")

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            f"sqlite+aiosqlite:///{DB_PATH}",
            connect_args={"timeout": 15, "check_same_thread": False},
        )

        @event.listens_for(_engine.sync_engine, "connect")
        def set_pragmas(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys = ON")
            dbapi_conn.execute("PRAGMA journal_mode = WAL")
            dbapi_conn.execute("PRAGMA busy_timeout = 10000")

    return _engine


def get_session_factory() -> async_sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_db() -> AsyncSession:
    """Dépendance FastAPI — retourne une session async."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


# ─── Helpers DB directs ───────────────────────────────────────────────────────────

async def get_scheduler_state(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(
        text("SELECT value FROM scheduler_state WHERE key = :key"), {"key": key}
    )
    row = result.fetchone()
    return row[0] if row else None


async def set_scheduler_state(session: AsyncSession, key: str, value: str) -> None:
    await session.execute(
        text(
            "INSERT INTO scheduler_state (key, value, updated_at) VALUES (:key, :value, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
        ),
        {"key": key, "value": value},
    )
    await session.commit()


async def get_all_overrides(session: AsyncSession) -> list[dict]:
    result = await session.execute(
        text("SELECT key, value, value_type, updated_at FROM config_overrides ORDER BY key")
    )
    return [
        {"key": r.key, "value": r.value, "value_type": r.value_type, "updated_at": r.updated_at}
        for r in result.fetchall()
    ]


async def upsert_override(session: AsyncSession, key: str, value: str, value_type: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    await session.execute(
        text(
            "INSERT INTO config_overrides (key, value, value_type, updated_at) "
            "VALUES (:key, :value, :value_type, :updated_at) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, value_type=excluded.value_type, "
            "updated_at=excluded.updated_at"
        ),
        {"key": key, "value": value, "value_type": value_type, "updated_at": now},
    )
    await session.commit()


async def delete_override(session: AsyncSession, key: str) -> bool:
    result = await session.execute(
        text("DELETE FROM config_overrides WHERE key = :key"), {"key": key}
    )
    await session.commit()
    return result.rowcount > 0


async def get_recent_posts(session: AsyncSession, limit: int = 20, status: str | None = None) -> list[dict]:
    if status:
        result = await session.execute(
            text(
                "SELECT id, status, caption, created_at, approved_at, "
                "event_id, article_id, instagram_post_id, facebook_post_id, story_post_id, "
                "instagram_error, facebook_error "
                "FROM posts WHERE status = :status ORDER BY created_at DESC LIMIT :limit"
            ),
            {"status": status, "limit": limit},
        )
    else:
        result = await session.execute(
            text(
                "SELECT id, status, caption, created_at, approved_at, "
                "event_id, article_id, instagram_post_id, facebook_post_id, story_post_id, "
                "instagram_error, facebook_error "
                "FROM posts ORDER BY created_at DESC LIMIT :limit"
            ),
            {"limit": limit},
        )
    cols = result.keys()
    return [dict(zip(cols, row)) for row in result.fetchall()]


async def get_meta_token_expiry(session: AsyncSession) -> dict:
    """Retourne les informations d'expiration des tokens Meta."""
    result = await session.execute(
        text("SELECT token_kind, expires_at FROM meta_tokens ORDER BY token_kind")
    )
    rows = result.fetchall()
    return {r.token_kind: r.expires_at for r in rows}
