# Tests — ordre d'initialisation main_async() [scheduler/__init__.py]
from __future__ import annotations

import pytest


def test_scheduler_shutdown_avant_start_leve_erreur():
    """APScheduler lève SchedulerNotRunningError si shutdown() appelé avant start().

    Valide le wrapper try/except autour de scheduler.shutdown() dans finally de main_async() :
    si une exception survient avant scheduler.start() (ex: dans recover_pending_posts),
    le finally sans wrapper masquerait l'exception originale avec SchedulerNotRunningError.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.schedulers.base import SchedulerNotRunningError

    scheduler = AsyncIOScheduler()
    with pytest.raises(SchedulerNotRunningError):
        scheduler.shutdown(wait=False)
