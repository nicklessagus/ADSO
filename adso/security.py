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

_raw = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")
if not _raw.strip():
    raise RuntimeError("TELEGRAM_ALLOWED_USER_ID is not set — bot refuses to start")
ALLOWED_USER_IDS: set[int] = {int(uid.strip()) for uid in _raw.split(",") if uid.strip().isdigit()}


def is_authorized(update: Update) -> bool:
    """True si el update proviene de un usuario en la allow-list.

    Helper reutilizable: lo usan tanto el decorador `authorized` (por handler)
    como el gate global de `bot.py` (defensa en profundidad).
    """
    user = update.effective_user
    return user is not None and user.id in ALLOWED_USER_IDS


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
        if not is_authorized(update):
            return None
        return await handler(update, context, *args, **kwargs)

    return wrapper
