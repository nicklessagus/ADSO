"""Middleware de autenticación por Telegram user_id.

Ignora silenciosamente mensajes de usuarios no autorizados.
No responde, no loguea contenido — solo descarta.
"""

from __future__ import annotations

import logging
import os
from functools import wraps
from typing import Any, Callable, Coroutine

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_raw = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "0")
ALLOWED_USER_IDS: set[int] = {int(uid.strip()) for uid in _raw.split(",") if uid.strip()}


def authorized(
    handler: Callable[..., Coroutine[Any, Any, Any]],
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Decorador que restringe un handler a usuarios autorizados.

    Si el usuario no está en ALLOWED_USER_IDS, el handler no se ejecuta
    y no se envía ninguna respuesta (silencio total).

    Args:
        handler: Handler async de python-telegram-bot.

    Returns:
        Handler wrapeado con check de autenticación.
    """

    @wraps(handler)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if update.effective_user is None:
            return None
        if update.effective_user.id not in ALLOWED_USER_IDS:
            return None
        return await handler(update, context, *args, **kwargs)

    return wrapper
