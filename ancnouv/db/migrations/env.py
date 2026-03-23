# Alembic env.py [docs/DATABASE.md — section "Configuration Alembic"]
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ancnouv.db.models import Base

# Alembic Config pour accès au logger
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Tous les modèles ORM pour l'autogenerate
target_metadata = Base.metadata


def _get_db_url() -> str:
    """Résout l'URL DB depuis ANCNOUV_DB_PATH ou valeur par défaut."""
    db_path = os.environ.get("ANCNOUV_DB_PATH", "data/ancnouv.db")
    return f"sqlite+aiosqlite:///{db_path}"


def run_migrations_offline() -> None:
    """Migrations sans connexion active (mode offline)."""
    url = _get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Migrations avec connexion async aiosqlite.

    [DB-10] PRAGMA foreign_keys=ON activé AVANT context.begin_transaction().
    """
    engine = create_async_engine(_get_db_url())

    async with engine.connect() as connection:
        # [DB-10] PRAGMA avant begin_transaction — ordre obligatoire
        await connection.execute(text("PRAGMA foreign_keys=ON"))

        await connection.run_sync(
            lambda conn: context.configure(
                connection=conn,
                target_metadata=target_metadata,
                render_as_batch=True,
            )
        )
        with context.begin_transaction():
            context.run_migrations()

    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
