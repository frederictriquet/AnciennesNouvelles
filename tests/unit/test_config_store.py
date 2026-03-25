# Tests unitaires — config_store [DASH-10.1]
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def store_session():
    """Session SQLite :memory: avec table config_overrides."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE config_overrides ("
            "key TEXT PRIMARY KEY NOT NULL, "
            "value TEXT NOT NULL, "
            "value_type TEXT NOT NULL, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "CHECK (value_type IN ('str','int','float','bool','list','dict'))"
            ")"
        ))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_set_and_get_override_str(store_session):
    """set_override puis get_override → valeur str correcte."""
    from ancnouv.db.config_store import get_override, set_override

    await set_override(store_session, "image.masthead_text", "TEST", "str")
    result = await get_override(store_session, "image.masthead_text")
    assert result == "TEST"


async def test_set_and_get_override_int(store_session):
    """set_override int → get_override retourne int."""
    from ancnouv.db.config_store import get_override, set_override

    await set_override(store_session, "scheduler.max_pending_posts", 3, "int")
    result = await get_override(store_session, "scheduler.max_pending_posts")
    assert result == 3
    assert isinstance(result, int)


async def test_set_and_get_override_bool(store_session):
    """set_override bool → get_override retourne bool."""
    from ancnouv.db.config_store import get_override, set_override

    await set_override(store_session, "content.rss.enabled", True, "bool")
    result = await get_override(store_session, "content.rss.enabled")
    assert result is True


async def test_set_and_get_override_list(store_session):
    """set_override list → get_override retourne list."""
    from ancnouv.db.config_store import get_override, set_override

    await set_override(store_session, "caption.hashtags", ["#foo", "#bar"], "list")
    result = await get_override(store_session, "caption.hashtags")
    assert result == ["#foo", "#bar"]


async def test_upsert_updates_existing(store_session):
    """Deuxième set_override sur même clé → valeur mise à jour."""
    from ancnouv.db.config_store import get_override, set_override

    await set_override(store_session, "scheduler.max_pending_posts", 1, "int")
    await set_override(store_session, "scheduler.max_pending_posts", 5, "int")
    result = await get_override(store_session, "scheduler.max_pending_posts")
    assert result == 5


async def test_get_override_absent(store_session):
    """get_override clé absente → None."""
    from ancnouv.db.config_store import get_override

    result = await get_override(store_session, "nonexistent.key")
    assert result is None


async def test_get_all_overrides(store_session):
    """get_all_overrides retourne toutes les clés désérialisées."""
    from ancnouv.db.config_store import get_all_overrides, set_override

    await set_override(store_session, "image.jpeg_quality", 85, "int")
    await set_override(store_session, "stories.enabled", True, "bool")
    result = await get_all_overrides(store_session)
    assert result == {"image.jpeg_quality": 85, "stories.enabled": True}


async def test_delete_override_existing(store_session):
    """delete_override clé existante → True, clé absente après."""
    from ancnouv.db.config_store import delete_override, get_override, set_override

    await set_override(store_session, "content.mix_ratio", 0.3, "float")
    deleted = await delete_override(store_session, "content.mix_ratio")
    assert deleted is True
    assert await get_override(store_session, "content.mix_ratio") is None


async def test_delete_override_absent(store_session):
    """delete_override clé absente → False."""
    from ancnouv.db.config_store import delete_override

    deleted = await delete_override(store_session, "nonexistent.key")
    assert deleted is False


async def test_set_override_invalid_value_type(store_session):
    """set_override avec value_type invalide → ValueError."""
    from ancnouv.db.config_store import set_override

    with pytest.raises(ValueError, match="value_type invalide"):
        await set_override(store_session, "some.key", "val", "invalid_type")
