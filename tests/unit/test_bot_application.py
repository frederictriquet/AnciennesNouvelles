# Tests — ancnouv/bot/bot.py [SPEC-3.3]
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# Tests : create_application
def test_create_application_calls_setup_handlers():
    """should call setup_handlers to register all handlers."""
    from ancnouv.bot.bot import create_application

    with patch("ancnouv.bot.bot.Application") as MockApplication, patch(
        "ancnouv.bot.bot.setup_handlers"
    ) as mock_setup:
        mock_builder = MagicMock()
        MockApplication.builder.return_value = mock_builder
        mock_app = MagicMock()
        mock_builder.token.return_value = mock_builder
        mock_builder.build.return_value = mock_app

        result = create_application(token="test_token_12345")

        # Should have called setup_handlers with the app
        mock_setup.assert_called_once()
        assert result == mock_app


def test_create_application_uses_builder_pattern():
    """should use Application.builder().token(token).build() [SPEC-3.3]."""
    from ancnouv.bot.bot import create_application

    with patch("ancnouv.bot.bot.Application") as MockApplication, patch(
        "ancnouv.bot.bot.setup_handlers"
    ):
        mock_builder = MagicMock()
        MockApplication.builder.return_value = mock_builder
        mock_builder.token.return_value = mock_builder
        mock_app = MagicMock()
        mock_builder.build.return_value = mock_app

        create_application(token="test_token_12345")

        # Verify the builder pattern was used
        MockApplication.builder.assert_called_once()
        mock_builder.token.assert_called_once_with("test_token_12345")
        mock_builder.build.assert_called_once()


# Tests : setup_handlers
def test_setup_handlers_adds_handlers_to_app():
    """should add handlers to the application."""
    from ancnouv.bot.bot import setup_handlers

    app = MagicMock()
    setup_handlers(app)

    # Should have called add_handler at least 17 times
    # (1 ConversationHandler + 4 CallbackQuery + 12 Command)
    assert app.add_handler.call_count >= 17


def test_setup_handlers_registers_handlers_in_order():
    """should register handlers in required order [TELEGRAM_BOT.md]."""
    from ancnouv.bot.bot import setup_handlers

    app = MagicMock()
    handlers_in_order = []

    def track_handler(handler):
        handlers_in_order.append(handler)

    app.add_handler = track_handler

    setup_handlers(app)

    # First handler should be a ConversationHandler (edit_conv_handler)
    first_handler = handlers_in_order[0]
    assert hasattr(first_handler, "states")  # ConversationHandler has states


def test_setup_handlers_registers_all_command_handlers():
    """should register all 12 command handlers [SPEC-3.3.6, SPEC-7ter]."""
    from ancnouv.bot.bot import setup_handlers
    from telegram.ext import CommandHandler

    app = MagicMock()
    command_handlers = []

    def track_handler(handler):
        if isinstance(handler, CommandHandler):
            command_handlers.append(handler)

    app.add_handler = track_handler

    setup_handlers(app)

    # Should have 12 command handlers
    assert len(command_handlers) == 12


def test_setup_handlers_uses_underscore_in_command_names():
    """should use underscores not hyphens in command names [TELEGRAM_BOT.md — TC-12]."""
    from ancnouv.bot.bot import setup_handlers
    from telegram.ext import CommandHandler

    app = MagicMock()
    commands = []

    def track_handler(handler):
        if isinstance(handler, CommandHandler):
            if hasattr(handler, "commands"):
                commands.extend(handler.commands)

    app.add_handler = track_handler

    setup_handlers(app)

    # Should have retry_ig and retry_fb, not retry-ig/retry-fb
    assert "retry_ig" in commands
    assert "retry_fb" in commands
    assert "retry-ig" not in commands
    assert "retry-fb" not in commands
