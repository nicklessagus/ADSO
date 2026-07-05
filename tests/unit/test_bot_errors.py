"""Tests del error handler global de PTB (_global_error_handler en bot.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.error import BadRequest, TimedOut

from adso.bot import _global_error_handler


def _make_context(error: Exception) -> MagicMock:
    context = MagicMock()
    context.error = error
    context.bot.send_message = AsyncMock()
    return context


def _make_update(chat_id: int = 12345) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.id = chat_id
    return update


class TestGlobalErrorHandler:
    @pytest.mark.asyncio
    async def test_benign_badrequest_is_silent(self) -> None:
        """'Message is not modified' no notifica al usuario ni loguea como error."""
        context = _make_context(
            BadRequest("Message is not modified: specified new message content...")
        )
        await _global_error_handler(_make_update(), context)
        context.bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_query_badrequest_is_silent(self) -> None:
        context = _make_context(BadRequest("Query is too old and response timeout expired"))
        await _global_error_handler(_make_update(), context)
        context.bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_network_error_does_not_notify(self) -> None:
        """Con la red a Telegram caída, no se intenta notificar por esa misma red."""
        context = _make_context(TimedOut())
        await _global_error_handler(_make_update(), context)
        context.bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unexpected_error_notifies_user(self) -> None:
        context = _make_context(RuntimeError("boom"))
        await _global_error_handler(_make_update(chat_id=999), context)
        context.bot.send_message.assert_awaited_once()
        assert context.bot.send_message.await_args.kwargs["chat_id"] == 999

    @pytest.mark.asyncio
    async def test_unexpected_error_without_update_does_not_notify(self) -> None:
        """update puede ser None u otro objeto (errores de jobs): solo se loguea."""
        context = _make_context(RuntimeError("boom"))
        await _global_error_handler(None, context)
        context.bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notification_failure_is_swallowed(self) -> None:
        """Si el aviso al usuario también falla, no propaga (evita loop de errores)."""
        context = _make_context(RuntimeError("boom"))
        context.bot.send_message = AsyncMock(side_effect=TimedOut())
        await _global_error_handler(_make_update(), context)
