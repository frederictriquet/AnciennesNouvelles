# Commandes CLI db [docs/CLI.md — section db, docs/DATABASE.md]
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_db_path() -> Path:
    """Résout le chemin DB depuis ANCNOUV_DB_PATH ou config.yml (défaut data/ancnouv.db)."""
    env_path = os.environ.get("ANCNOUV_DB_PATH", "")
    if env_path:
        return Path(env_path)
    # Lecture partielle de la config pour backup_keep — DatabaseConfig seul
    try:
        from pydantic_settings import BaseSettings, SettingsConfigDict, YamlConfigSettingsSource
        from pydantic import BaseModel

        class _DBOnly(BaseSettings):
            model_config = SettingsConfigDict(yaml_file="config.yml", extra="ignore")
            data_dir: str = "data"

            class database(BaseModel):
                filename: str = "ancnouv.db"

            @classmethod
            def settings_customise_sources(cls, settings_cls, **kwargs):
                return (kwargs["env_settings"], YamlConfigSettingsSource(settings_cls), kwargs["init_settings"])

        cfg = _DBOnly()
        return Path(cfg.data_dir) / cfg.database.filename  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("_get_db_path : lecture config.yml impossible (%s) — fallback data/ancnouv.db", exc)
        return Path("data") / "ancnouv.db"


def _get_backup_keep() -> int:
    """Lit backup_keep depuis la config (défaut 7 si config absente)."""
    try:
        from pydantic_settings import BaseSettings, SettingsConfigDict, YamlConfigSettingsSource
        from pydantic import BaseModel, Field

        class _DBOnly(BaseSettings):
            model_config = SettingsConfigDict(yaml_file="config.yml", extra="ignore")

            class database(BaseModel):
                backup_keep: int = Field(default=7, ge=1)

            @classmethod
            def settings_customise_sources(cls, settings_cls, **kwargs):
                return (kwargs["env_settings"], YamlConfigSettingsSource(settings_cls), kwargs["init_settings"])

        cfg = _DBOnly()
        return cfg.database.backup_keep  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("_get_backup_keep : lecture config.yml impossible (%s) — fallback 7", exc)
        return 7


def cmd_db_init() -> int:
    """Crée data/ancnouv.db et applique toutes les migrations Alembic.

    [RF-3.6.3] Peut s'exécuter sans config complète (pas de validate_meta).
    """
    import asyncio
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # [ARCH-16] Répertoires runtime créés au démarrage
    Path("data/images").mkdir(parents=True, exist_ok=True)
    Path("logs").mkdir(parents=True, exist_ok=True)

    return _run_alembic(["upgrade", "head"], db_path)


def cmd_db_migrate() -> int:
    """Applique les migrations en attente (alembic upgrade head)."""
    return _run_alembic(["upgrade", "head"], _get_db_path())


def cmd_db_status() -> int:
    """Affiche l'état des migrations (alembic current). [CLI-M7]

    Retourne 1 si la DB est inaccessible.
    """
    db_path = _get_db_path()
    if not db_path.exists():
        print(f"Base de données introuvable : {db_path}", file=sys.stderr)
        return 1
    return _run_alembic(["current"], db_path)


def cmd_db_backup() -> int:
    """Sauvegarde data/ancnouv.db via VACUUM INTO.

    [DB-14] VACUUM INTO est sûr pendant que start tourne (mode WAL).
    Rotation alphabétique décroissante — conserve les N dernières sauvegardes.
    """
    db_path = _get_db_path()
    if not db_path.exists():
        print(f"Base de données introuvable : {db_path}", file=sys.stderr)
        return 1

    backup_keep = _get_backup_keep()
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"ancnouv_{timestamp}.db"

    # VACUUM INTO crée une copie propre — sûr en lecture concurrente (WAL)
    return asyncio.run(_vacuum_into(str(db_path), str(backup_path), backup_dir, backup_keep))


async def _vacuum_into(db_path: str, backup_path: str, backup_dir: Path, keep: int) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"VACUUM INTO '{backup_path}'"))
    except Exception as exc:
        print(f"Erreur lors de la sauvegarde : {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    print(f"Sauvegarde créée : {backup_path}")

    # Rotation alphabétique décroissante — supprimer les plus anciennes
    backups = sorted(backup_dir.glob("ancnouv_*.db"), reverse=True)
    for old in backups[keep:]:
        old.unlink()
        print(f"Ancienne sauvegarde supprimée : {old.name}")

    return 0


def cmd_db_reset() -> int:
    """DANGER : supprime et recrée la DB (dev uniquement).

    Demande confirmation interactive. Retourne 3 si annulé.
    """
    db_path = _get_db_path()
    print(f"⚠️  DANGER : cette commande supprime {db_path} et toutes ses données.")
    confirm = input("Taper 'RESET' pour confirmer : ").strip()
    if confirm != "RESET":
        print("Annulé.")
        return 3

    if db_path.exists():
        db_path.unlink()
        print(f"Base de données supprimée : {db_path}")

    return cmd_db_init()


def _run_alembic(args: list[str], db_path: Path) -> int:
    """Exécute une commande Alembic avec la DB path donnée."""
    import subprocess
    env = os.environ.copy()
    env["ANCNOUV_DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env=env,
    )
    return result.returncode


def run_db_command(subcommand: str) -> int:
    """Dispatcher des commandes db (appelé depuis __main__.py)."""
    commands = {
        "init": cmd_db_init,
        "migrate": cmd_db_migrate,
        "status": cmd_db_status,
        "backup": cmd_db_backup,
        "reset": cmd_db_reset,
    }
    if subcommand not in commands:
        print(f"Commande db inconnue : {subcommand}", file=sys.stderr)
        return 2
    return commands[subcommand]()
