# Scheduler APScheduler — Phase 6 [docs/SCHEDULER.md, SPEC-3.5]
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ancnouv.config import Config

logger = logging.getLogger(__name__)


def create_scheduler(config: Config) -> "AsyncIOScheduler":
    """Crée et configure l'AsyncIOScheduler avec tous les jobs. [SCHEDULER.md — Configuration APScheduler]

    Pré-requis : init_context() doit avoir été appelé avant cette fonction [SC-C6].
    Les dépendances des jobs sont injectées via le contexte partagé — jamais via args=/kwargs=.

    Jobstores :
    - "default" : SQLAlchemyJobStore sync (sqlite:///scheduler.db) — JOB-1, JOB-2, JOB-3
    - "memory" : MemoryJobStore — JOB-4, JOB-5, JOB-6 (non-persistants)

    replace_existing=True obligatoire pour les jobs persistants — évite ConflictingIdError
    au redémarrage quand le job existe déjà dans scheduler.db [SCHEDULER.md].
    """
    from apscheduler.jobstores.memory import MemoryJobStore
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    from ancnouv.scheduler.context import set_scheduler
    from ancnouv.scheduler.jobs import (
        job_check_expired,
        job_check_token,
        job_cleanup,
        job_fetch_gallica,
        job_fetch_rss,
        job_fetch_wiki,
        job_generate,
        job_publish_queued,
    )

    # [ARCH-15] scheduler.db relatif à config.data_dir — URL sync (sans aiosqlite) [SCHEDULER.md]
    scheduler_db_path = str(Path(config.data_dir) / "scheduler.db")
    jobstores = {
        "default": SQLAlchemyJobStore(url=f"sqlite:///{scheduler_db_path}"),
        "memory": MemoryJobStore(),
    }

    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        timezone=config.scheduler.timezone,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": config.scheduler.misfire_grace_time,
        },
    )

    # JOB-1 — collecte Wikipedia — cron 0 2 * * *, persistant [ARCH-04]
    # [SC-M1] Concurrence possible avec JOB-3 si generation_cron inclut 2h —
    # recommander "30 */4 * * *" ou connect_args timeout=15 pour éviter la contention.
    scheduler.add_job(
        job_fetch_wiki,
        "cron",
        hour=2,
        minute=0,
        id="job_fetch_wiki",
        jobstore="default",
        replace_existing=True,
    )

    # JOB-8 — collecte Gallica — cron 0 3 * * *, persistant, enregistré si gallica.enabled [SPEC-9.3]
    if config.gallica.enabled:
        scheduler.add_job(
            job_fetch_gallica,
            "cron",
            hour=3,
            minute=30,
            id="job_fetch_gallica",
            jobstore="default",
            replace_existing=True,
        )

    # JOB-2 — collecte RSS — interval 6h, persistant, enregistré si rss.enabled [ARCH-05]
    # [SC-C1] Sans start_date : déclenchement immédiat si délai > misfire_grace_time (intentionnel).
    if config.content.rss.enabled:
        scheduler.add_job(
            job_fetch_rss,
            "interval",
            hours=6,
            id="job_fetch_rss",
            jobstore="default",
            replace_existing=True,
        )

    # JOB-3 — génération — cron depuis config, persistant, max_instances=1 [SC-C7]
    # [SC-C7] max_instances=1 : APScheduler skippe silencieusement si instance active.
    scheduler.add_job(
        job_generate,
        CronTrigger.from_crontab(
            config.scheduler.generation_cron,
            timezone=config.scheduler.timezone,
        ),
        id="job_generate",
        jobstore="default",
        replace_existing=True,
        max_instances=1,
    )

    # JOB-4 — expiration posts — interval 1h, MemoryJobStore [RF-3.3.3, SC-M2]
    scheduler.add_job(
        job_check_expired,
        "interval",
        hours=1,
        id="job_check_expired",
        jobstore="memory",
    )

    # JOB-5 — surveillance token — cron 0 9 * * *, MemoryJobStore [RF-3.4.5]
    # [SC-M4] job_check_token est aussi appelé directement dans main_async() au démarrage.
    scheduler.add_job(
        job_check_token,
        "cron",
        hour=9,
        minute=0,
        id="job_check_token",
        jobstore="memory",
    )

    # JOB-6 — nettoyage images — cron 0 3 * * *, MemoryJobStore [TRANSVERSAL-5]
    scheduler.add_job(
        job_cleanup,
        "cron",
        hour=3,
        minute=0,
        id="job_cleanup",
        jobstore="memory",
    )

    # JOB-7 — publication queued — même cron que JOB-3 [SPEC-7ter, RF-7ter.3]
    # MemoryJobStore : non-persistant (pas de replace_existing nécessaire)
    scheduler.add_job(
        job_publish_queued,
        CronTrigger.from_crontab(
            config.scheduler.generation_cron,
            timezone=config.scheduler.timezone,
        ),
        id="job_publish_queued",
        jobstore="memory",
    )

    set_scheduler(scheduler)
    return scheduler


async def main_async(config: Config) -> None:
    """Séquence canonique de démarrage du scheduler + bot Telegram. [SCHEDULER.md SC-C6]

    Ordre obligatoire :
    1. init_db           — moteur SQLAlchemy, migrations
    2. create_application — Application PTB, handlers enregistrés
    3. bot_data["config"] — config injectée avant init_context
    4. init_context       — singletons initialisés, session factory rebindée
    5. create_scheduler   — jobs ajoutés, pas encore démarrés
    6. start_local_image_server (si backend=local) — URLs accessibles avant recover
    7. bot_app.initialize() — client HTTP PTB prêt avant tout appel Telegram
    8. recover_pending_posts — états intermédiaires après crash
    9. scheduler.start()  — les jobs peuvent appeler get_config()/get_engine()
    10. job_check_token()  — vérification token immédiate [SC-M4]
    11. run_polling        — boucle principale PTB (bloquante)
    12. shutdown           — arrêt propre à la sortie

    Inverser 4 et 9 → RuntimeError dès le premier job déclenché.
    Inverser 6 et 8 → recover_pending_posts tente des URLs inaccessibles [SC-C6].
    Inverser 7 et 8 → notify_all() dans recover lève RuntimeError (client HTTP non initialisé).
    """
    from ancnouv.bot.bot import create_application
    from ancnouv.db.session import get_session, init_db
    from ancnouv.scheduler.context import init_context
    from ancnouv.scheduler.jobs import job_check_token, recover_pending_posts

    # 1. Initialisation DB
    db_path = str(Path(config.data_dir) / config.database.filename)
    engine = init_db(db_path)

    # 2. Application PTB
    bot_app = create_application(config.telegram_bot_token)

    # 3. Config dans bot_data
    bot_app.bot_data["config"] = config

    # 4. Singletons partagés + session factory
    init_context(config, bot_app, engine)

    # 5. Scheduler (jobs ajoutés, pas encore démarrés)
    scheduler = create_scheduler(config)

    # 6. Serveur d'images local (avant recover — URLs doivent être accessibles)
    image_runner = None
    if config.image_hosting.backend == "local":
        from ancnouv.publisher.image_hosting import start_local_image_server
        images_dir = Path(config.data_dir) / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        image_runner = await start_local_image_server(images_dir, config.image_hosting.local_port)

    # 10. Boucle principale PTB — pattern async sans run_polling() [PTB v20]
    # initialize() avant les steps 7-9 : requis pour les appels Telegram (notify_all, etc.)
    stop_event = asyncio.Event()

    # Handler SIGTERM : Docker envoie SIGTERM pour stopper le container.
    # Sans handler, Python est tué par l'OS immédiatement (finally ne s'exécute pas,
    # updater.stop() n'est jamais appelé, Telegram conserve la session getUpdates active
    # brièvement → Conflict au redémarrage). [SC-M5]
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)

    try:
        await bot_app.initialize()

        # 7. Récupération des posts en état intermédiaire
        async with get_session() as session:
            await recover_pending_posts(session, bot_app.bot, config)

        # 8. Démarrage scheduler
        scheduler.start()
        logger.info("Scheduler APScheduler démarré.")

        # 9. Vérification token au démarrage [SC-M4]
        await job_check_token()

        logger.info("Démarrage du bot Telegram (polling)...")

        await bot_app.start()
        if bot_app.updater is not None:
            await bot_app.updater.start_polling(drop_pending_updates=True)
        await stop_event.wait()  # bloque jusqu'à CancelledError (KeyboardInterrupt/SIGTERM)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        # [SC-M5] Conflict Telegram : instance précédente encore active.
        # Attente 60s avant shutdown pour laisser Telegram invalider la session —
        # évite le crash loop où chaque redémarrage Docker génère un nouveau Conflict.
        from telegram.error import Conflict as TelegramConflict
        if isinstance(exc, TelegramConflict):
            logger.warning(
                "Conflit Telegram — instance précédente encore active. "
                "Attente 60s avant redémarrage."
            )
            await asyncio.sleep(60)
        else:
            raise
    finally:
        # 11. Arrêt propre
        logger.info("Arrêt en cours...")
        try:
            if bot_app.updater is not None:
                await bot_app.updater.stop()
        except Exception as exc:
            logger.warning("Erreur arrêt updater : %s", exc)
        try:
            await bot_app.stop()
            await bot_app.shutdown()
        except Exception as exc:
            logger.warning("Erreur arrêt bot_app : %s", exc)
        try:
            scheduler.shutdown(wait=False)
        except Exception as exc:
            logger.warning("Erreur arrêt scheduler : %s", exc)
        if image_runner is not None:
            try:
                await image_runner.cleanup()
            except Exception as exc:
                logger.warning("Erreur arrêt serveur images : %s", exc)
        logger.info("Scheduler et bot Telegram arrêtés.")


def run(config: Config) -> int:
    """Point d'entrée synchrone depuis __main__.py. [CLI.md, SCHEDULER.md]

    Unique appel asyncio.run() de l'application [CLI.md — asyncio.run() unique].
    KeyboardInterrupt → sortie propre (code 0).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # Réduire le bruit des bibliothèques tierces
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    try:
        asyncio.run(main_async(config))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.error("Erreur fatale du scheduler : %s", exc, exc_info=True)
        return 1
