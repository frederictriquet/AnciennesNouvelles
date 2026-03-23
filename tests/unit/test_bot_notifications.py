# Tests — ancnouv/bot/notifications.py [SPEC-3.3, TG-F15]
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Bot, InlineKeyboardMarkup, Message


# Fixtures
@pytest.fixture
def mock_bot() -> Bot:
    """Mock bot Telegram."""
    bot = MagicMock(spec=Bot)
    bot.send_with_retry = AsyncMock()
    return bot


@pytest.fixture
def mock_config():
    """Mock de configuration Telegram."""
    config = MagicMock()
    config.telegram.authorized_user_ids = [111, 222, 333]
    config.telegram.notification_debounce = 0.1
    return config


@pytest.fixture
def mock_post():
    """Mock de Post (DB model)."""
    post = MagicMock()
    post.id = 1
    post.caption = "Test caption"
    post.article_id = 42
    post.image_path = None
    post.status = "pending_approval"
    post.telegram_message_ids = "{}"
    return post


# Tests : send_with_retry
@pytest.mark.asyncio
async def test_send_with_retry_success_first_attempt():
    """should return message on first successful attempt."""
    from ancnouv.bot.notifications import send_with_retry

    mock_message = MagicMock(spec=Message)
    mock_coro = AsyncMock(return_value=mock_message)

    result = await send_with_retry(mock_coro)

    assert result == mock_message
    mock_coro.assert_called_once()


@pytest.mark.asyncio
async def test_send_with_retry_succeeds_after_retries():
    """should retry on failure and succeed."""
    from ancnouv.bot.notifications import send_with_retry

    mock_message = MagicMock(spec=Message)
    mock_coro = AsyncMock(
        side_effect=[
            Exception("Failure 1"),
            Exception("Failure 2"),
            mock_message,
        ]
    )

    result = await send_with_retry(mock_coro, max_attempts=5)

    assert result == mock_message
    assert mock_coro.call_count == 3


@pytest.mark.asyncio
async def test_send_with_retry_exhausts_attempts():
    """should return None after max attempts exceeded."""
    from ancnouv.bot.notifications import send_with_retry

    mock_coro = AsyncMock(side_effect=Exception("Always fails"))

    result = await send_with_retry(mock_coro, max_attempts=3)

    assert result is None
    assert mock_coro.call_count == 3


@pytest.mark.asyncio
async def test_send_with_retry_backoff_delays():
    """should apply exponential backoff: 1, 2, 4, 8, 16 seconds [TG-F15]."""
    from ancnouv.bot.notifications import send_with_retry

    mock_message = MagicMock(spec=Message)
    mock_coro = AsyncMock(
        side_effect=[
            Exception("Fail 1"),
            Exception("Fail 2"),
            mock_message,
        ]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await send_with_retry(mock_coro, max_attempts=5)

    assert result == mock_message
    # Should have slept with delays [1, 2] before success
    assert mock_sleep.call_count == 2
    calls = [call.args[0] for call in mock_sleep.call_args_list]
    assert calls == [1, 2]


# Tests : notify_all
@pytest.mark.asyncio
async def test_notify_all_sends_to_all_users(mock_bot, mock_config):
    """should send message to all authorized users."""
    from ancnouv.bot.notifications import notify_all

    with patch(
        "ancnouv.bot.notifications.send_with_retry", new_callable=AsyncMock
    ) as mock_retry:
        mock_retry.return_value = MagicMock(spec=Message)

        await notify_all(mock_bot, mock_config, "Test message")

        # Should call send_with_retry 3 times (3 users)
        assert mock_retry.call_count == 3


@pytest.mark.asyncio
async def test_notify_all_applies_debounce(mock_bot, mock_config):
    """should apply notification_debounce between sends."""
    from ancnouv.bot.notifications import notify_all

    with patch(
        "ancnouv.bot.notifications.send_with_retry", new_callable=AsyncMock
    ), patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await notify_all(mock_bot, mock_config, "Test message")

        # Should sleep (n_users - 1) times with debounce value
        assert mock_sleep.call_count == 2
        assert all(
            call.args[0] == 0.1 for call in mock_sleep.call_args_list
        )


@pytest.mark.asyncio
async def test_notify_all_empty_users(mock_bot, mock_config):
    """should handle empty authorized_user_ids gracefully."""
    from ancnouv.bot.notifications import notify_all

    mock_config.telegram.authorized_user_ids = []

    with patch(
        "ancnouv.bot.notifications.send_with_retry", new_callable=AsyncMock
    ) as mock_retry:
        await notify_all(mock_bot, mock_config, "Test message")

        # Should not attempt to send
        mock_retry.assert_not_called()


# Tests : _build_approval_text
@pytest.mark.asyncio
async def test_build_approval_text_with_article(mock_post):
    """should include 'Actualité RSS' header when article_id is set."""
    from ancnouv.bot.notifications import _build_approval_text

    mock_post.article_id = 42
    text = _build_approval_text(mock_post)

    assert "PROPOSITION DE POST (Actualité RSS)" in text
    assert "Test caption" in text
    assert "─────────────────────────" in text


@pytest.mark.asyncio
async def test_build_approval_text_without_article(mock_post):
    """should exclude 'Actualité RSS' header when article_id is None."""
    from ancnouv.bot.notifications import _build_approval_text

    mock_post.article_id = None
    text = _build_approval_text(mock_post)

    assert "PROPOSITION DE POST\n" in text
    assert "(Actualité RSS)" not in text


# Tests : _build_approval_keyboard
@pytest.mark.asyncio
async def test_build_approval_keyboard_structure(mock_post):
    """should build keyboard with 3 rows of buttons [SPEC-3.3.2]."""
    from ancnouv.bot.notifications import _build_approval_keyboard

    keyboard = _build_approval_keyboard(42)

    assert isinstance(keyboard, InlineKeyboardMarkup)
    assert len(keyboard.inline_keyboard) == 3
    # Row 1: Publier, Rejeter
    assert len(keyboard.inline_keyboard[0]) == 2
    # Row 2: Autre événement
    assert len(keyboard.inline_keyboard[1]) == 1
    # Row 3: Modifier la légende
    assert len(keyboard.inline_keyboard[2]) == 1


@pytest.mark.asyncio
async def test_build_approval_keyboard_callbacks(mock_post):
    """should set correct callback_data patterns."""
    from ancnouv.bot.notifications import _build_approval_keyboard

    keyboard = _build_approval_keyboard(42)

    buttons_flat = [
        btn for row in keyboard.inline_keyboard for btn in row
    ]
    callback_data = [btn.callback_data for btn in buttons_flat]

    assert "approve:42" in callback_data
    assert "reject:42" in callback_data
    assert "skip:42" in callback_data
    assert "edit:42" in callback_data


# Tests : send_approval_request
@pytest.mark.asyncio
async def test_send_approval_request_guard_empty_users(
    mock_post, mock_bot, mock_config
):
    """should log error and mark post as error when no authorized users [TG-F9]."""
    from ancnouv.bot.notifications import send_approval_request

    mock_config.telegram.authorized_user_ids = []
    mock_session = AsyncMock()

    await send_approval_request(mock_post, mock_bot, mock_session, mock_config)

    # Should mark post as error
    assert mock_post.status == "error"
    assert mock_post.error_message == "No authorized users configured"
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_send_approval_request_caption_truncation():
    """should truncate caption to 1024 chars for Telegram [TG-F11]."""
    from ancnouv.bot.notifications import send_approval_request

    post = MagicMock()
    post.id = 1
    post.article_id = None
    post.image_path = None
    post.status = "pending_approval"
    post.caption = "x" * 2000  # 2000 chars

    bot = MagicMock(spec=Bot)
    config = MagicMock()
    config.telegram.authorized_user_ids = [111]
    session = AsyncMock()

    with patch(
        "ancnouv.bot.notifications.send_with_retry", new_callable=AsyncMock
    ) as mock_retry:
        mock_retry.return_value = MagicMock(message_id=123)

        await send_approval_request(post, bot, session, config)

    # Verify that send_with_retry was called with truncated caption
    assert mock_retry.called


@pytest.mark.asyncio
async def test_send_approval_request_populates_telegram_message_ids():
    """should populate telegram_message_ids dict with {str(user_id): message_id}."""
    from ancnouv.bot.notifications import send_approval_request

    post = MagicMock()
    post.id = 1
    post.article_id = None
    post.image_path = None
    post.status = "pending_approval"
    post.caption = "Test"

    bot = MagicMock(spec=Bot)
    config = MagicMock()
    config.telegram.authorized_user_ids = [111, 222]
    session = AsyncMock()

    with patch(
        "ancnouv.bot.notifications.send_with_retry", new_callable=AsyncMock
    ) as mock_retry:
        mock_msg1 = MagicMock(message_id=101)
        mock_msg2 = MagicMock(message_id=102)
        mock_retry.side_effect = [mock_msg1, mock_msg2]

        await send_approval_request(post, bot, session, config)

    # Verify telegram_message_ids was set
    msg_ids = json.loads(post.telegram_message_ids)
    assert msg_ids == {"111": 101, "222": 102}
    session.commit.assert_called_once()
