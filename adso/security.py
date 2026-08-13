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

def _parse_allowed_ids(raw: str) -> set[int]:
    """Parsea `TELEGRAM_ALLOWED_USER_ID` (uno o varios IDs separados por comas).

    El filtro `isdigit()` descartaba los valores no numéricos **sin error**: con
    `"12a"` el set quedaba vacío y el bot arrancaba con lockout total en
    silencio — nadie podía usarlo y no había nada en los logs que lo explicara.
    G7 de docs/audit-2026-07-31.md.

    Args:
        raw: Valor crudo de la variable de entorno.

    Returns:
        Set de IDs numéricos.

    Raises:
        RuntimeError: Si está vacío o no queda ningún ID válido.
    """
    if not raw.strip():
        raise RuntimeError("TELEGRAM_ALLOWED_USER_ID is not set — bot refuses to start")

    ids: set[int] = set()
    invalidos: list[str] = []
    for parte in raw.split(","):
        parte = parte.strip()
        if not parte:
            continue
        if parte.isdigit():
            ids.add(int(parte))
        else:
            invalidos.append(parte)

    if not ids:
        raise RuntimeError(
            "TELEGRAM_ALLOWED_USER_ID no contiene ningún ID numérico válido "
            f"(recibido: {raw!r}) — el bot quedaría inaccesible para todos"
        )
    if invalidos:
        logger.warning(
            "TELEGRAM_ALLOWED_USER_ID: se ignoran valores no numéricos: %s",
            ", ".join(invalidos),
        )
    return ids


ALLOWED_USER_IDS: set[int] = _parse_allowed_ids(
    os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")
)


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
