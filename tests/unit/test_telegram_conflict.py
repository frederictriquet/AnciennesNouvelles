# Tests — gestion TelegramConflict dans main_async [scheduler/__init__.py, SC-M5]
#
# Deux scénarios distincts :
# A) start_polling() lève TelegramConflict (ex: bootstrap conflictuel)
#    → except TelegramConflict est atteint → sleep 60s → exit propre   ✓ COUVERT
# B) Conflict pendant le polling continu (background task PTB)
#    → _network_loop_retry le catch, log et retry silencieusement
#    → except TelegramConflict jamais atteint                           ✗ NON COUVERT
from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import Conflict as TelegramConflict

from ancnouv.scheduler import main_async


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _make_bot_app(start_polling_side_effect=None):
    """Mock complet d'Application PTB."""
    app = MagicMock()
    app.bot_data = {}
    app.bot = MagicMock()
    app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()

    updater = MagicMock()
    updater.stop = AsyncMock()
    updater.start_polling = AsyncMock(side_effect=start_polling_side_effect)
    app.updater = updater
    return app


def _make_config(backend: str = "remote"):
    cfg = MagicMock()
    cfg.data_dir = "/tmp/ancnouv_test"
    cfg.database.filename = "test.db"
    cfg.telegram_bot_token = "fake_token"
    cfg.image_hosting.backend = backend
    return cfg


@asynccontextmanager
async def _fake_session():
    yield MagicMock()


def _enter_base_patches(stack: ExitStack, bot_app, scheduler=None):
    """Applique les patches communs via un ExitStack."""
    stack.enter_context(patch("ancnouv.db.session.init_db", return_value=MagicMock()))
    stack.enter_context(patch("ancnouv.bot.bot.create_application", return_value=bot_app))
    stack.enter_context(patch("ancnouv.scheduler.context.init_context"))
    stack.enter_context(patch("ancnouv.scheduler.create_scheduler", return_value=scheduler or MagicMock()))
    stack.enter_context(patch("ancnouv.scheduler.jobs.recover_pending_posts", AsyncMock()))
    stack.enter_context(patch("ancnouv.scheduler.jobs.job_check_token", AsyncMock()))
    stack.enter_context(patch("ancnouv.db.session.get_session", _fake_session))


# ─── Tests scénario A : Conflict levé par start_polling() ───────────────────────

async def test_conflict_depuis_start_polling_sleep_60s():
    """main_async dort 60s sur TelegramConflict levé par start_polling() [SC-M5]."""
    bot_app = _make_bot_app(
        start_polling_side_effect=TelegramConflict(
            "Conflict: terminated by other getUpdates request"
        )
    )
    sleep_calls = []

    async def capture_sleep(duration):
        sleep_calls.append(duration)

    with ExitStack() as stack:
        _enter_base_patches(stack, bot_app)
        stack.enter_context(patch("asyncio.sleep", capture_sleep))
        await main_async(_make_config())

    assert 60 in sleep_calls, f"Sleep 60s attendu, reçu : {sleep_calls}"


async def test_conflict_depuis_start_polling_shutdown_propre():
    """main_async appelle stop/shutdown proprement après TelegramConflict [SC-M5]."""
    bot_app = _make_bot_app(
        start_polling_side_effect=TelegramConflict("terminated by other getUpdates request")
    )
    mock_scheduler = MagicMock()

    with ExitStack() as stack:
        _enter_base_patches(stack, bot_app, scheduler=mock_scheduler)
        stack.enter_context(patch("asyncio.sleep", AsyncMock()))
        await main_async(_make_config())

    bot_app.stop.assert_awaited_once()
    bot_app.shutdown.assert_awaited_once()
    mock_scheduler.shutdown.assert_called_once_with(wait=False)


async def test_conflict_depuis_start_polling_exit_propre():
    """main_async ne lève pas d'exception sur TelegramConflict — exit code 0 [SC-M5]."""
    bot_app = _make_bot_app(
        start_polling_side_effect=TelegramConflict("terminated by other getUpdates request")
    )

    with ExitStack() as stack:
        _enter_base_patches(stack, bot_app)
        stack.enter_context(patch("asyncio.sleep", AsyncMock()))
        await main_async(_make_config())  # ne doit pas lever


async def test_autre_exception_propage():
    """main_async propage les exceptions non-Conflict (comportement normal)."""
    bot_app = _make_bot_app(
        start_polling_side_effect=RuntimeError("Erreur inattendue")
    )

    with ExitStack() as stack:
        _enter_base_patches(stack, bot_app)
        stack.enter_context(patch("asyncio.sleep", AsyncMock()))
        with pytest.raises(RuntimeError, match="Erreur inattendue"):
            await main_async(_make_config())


# ─── Tests scénario B : Conflict pendant polling continu ────────────────────────

async def test_conflict_polling_continu_gere_par_ptb():
    """PTB gère TelegramConflict en interne via _network_loop_retry — ne crashe pas.

    Vérifie que _network_loop_retry attrape TelegramError (dont Conflict) et appelle
    error_callback sans propager l'exception. Le container reste actif — le Conflict
    est visible en logs mais n'est PAS remonté à main_async.
    NOTE : le except TelegramConflict de main_async n'est pas atteint dans ce cas.
    """
    from telegram.error import TelegramError
    from telegram.ext import Updater

    call_count = 0

    async def action_cb():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TelegramConflict("terminated by other getUpdates request")
        return False  # stoppe la boucle au 2ème appel

    errors_received = []

    def error_cb(exc: TelegramError) -> None:
        errors_received.append(exc)

    updater = MagicMock(spec=Updater)
    updater.running = True

    # Appelle directement la méthode interne PTB pour confirmer le comportement
    await Updater._network_loop_retry(
        updater,
        action_cb=action_cb,
        on_err_cb=error_cb,
        description="test polling",
        interval=0,
    )

    assert len(errors_received) == 1
    assert isinstance(errors_received[0], TelegramConflict)
    assert call_count == 2  # retry après le Conflict
