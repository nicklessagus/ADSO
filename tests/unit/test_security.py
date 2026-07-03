"""Tests para adso.security — middleware de autenticación."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, User

from adso.security import authorized, is_authorized


def _make_update(user_id: int | None = None) -> Update:
    """Construye un Update de Telegram con un user_id dado."""
    update = MagicMock(spec=Update)
    if user_id is not None:
        user = MagicMock(spec=User)
        user.id = user_id
        update.effective_user = user
    else:
        update.effective_user = None
    return update


class TestAuthorized:

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    @pytest.mark.asyncio
    async def test_authorized_user_passes(self) -> None:
        """Usuario autorizado → handler se ejecuta."""
        handler = AsyncMock(return_value="ok")
        wrapped = authorized(handler)
        update = _make_update(user_id=42)
        context = MagicMock()

        result = await wrapped(update, context)

        handler.assert_awaited_once_with(update, context)
        assert result == "ok"

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    @pytest.mark.asyncio
    async def test_unauthorized_user_silent(self) -> None:
        """Usuario no autorizado → silencio total."""
        handler = AsyncMock()
        wrapped = authorized(handler)
        update = _make_update(user_id=999)
        context = MagicMock()

        result = await wrapped(update, context)

        handler.assert_not_awaited()
        assert result is None

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    @pytest.mark.asyncio
    async def test_no_user_silent(self) -> None:
        """Update sin effective_user → silencio."""
        handler = AsyncMock()
        wrapped = authorized(handler)
        update = _make_update(user_id=None)
        context = MagicMock()

        result = await wrapped(update, context)

        handler.assert_not_awaited()
        assert result is None

    @patch("adso.security.ALLOWED_USER_IDS", {42, 100})
    @pytest.mark.asyncio
    async def test_multiple_allowed_ids(self) -> None:
        """Múltiples IDs autorizados → ambos pasan."""
        handler = AsyncMock(return_value="ok")
        wrapped = authorized(handler)
        context = MagicMock()

        result1 = await wrapped(_make_update(42), context)
        result2 = await wrapped(_make_update(100), context)

        assert result1 == "ok"
        assert result2 == "ok"
        assert handler.await_count == 2

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    @pytest.mark.asyncio
    async def test_decorator_preserves_function_name(self) -> None:
        """El decorador preserva el nombre de la función original."""
        async def my_handler(update: Update, context: object) -> str:
            return "ok"

        wrapped = authorized(my_handler)
        assert wrapped.__name__ == "my_handler"

    @patch("adso.security.ALLOWED_USER_IDS", set())
    @pytest.mark.asyncio
    async def test_empty_allowed_rejects_all(self) -> None:
        """Sin IDs configurados → todos rechazados."""
        handler = AsyncMock()
        wrapped = authorized(handler)
        update = _make_update(user_id=42)
        context = MagicMock()

        result = await wrapped(update, context)

        handler.assert_not_awaited()
        assert result is None


class TestIsAuthorized:

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    def test_authorized_true(self) -> None:
        assert is_authorized(_make_update(42)) is True

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    def test_unauthorized_false(self) -> None:
        assert is_authorized(_make_update(999)) is False

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    def test_no_user_false(self) -> None:
        assert is_authorized(_make_update(None)) is False


class TestGlobalAuthGate:
    """Gate global de bot.py: corta updates no autorizados con ApplicationHandlerStop."""

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    @pytest.mark.asyncio
    async def test_unauthorized_raises_stop(self) -> None:
        from telegram.ext import ApplicationHandlerStop
        from adso.bot import _global_auth_gate

        with pytest.raises(ApplicationHandlerStop):
            await _global_auth_gate(_make_update(999), MagicMock())

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    @pytest.mark.asyncio
    async def test_authorized_passes_through(self) -> None:
        from adso.bot import _global_auth_gate

        # No debe lanzar: el update sigue al group 0
        assert await _global_auth_gate(_make_update(42), MagicMock()) is None
