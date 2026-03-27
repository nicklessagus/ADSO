"""Handlers para reportes a pedido del vault.

/reporte o keywords "reporte"/"resumen"/"informe" disparan un menú de tipos.
El usuario elige el tipo, luego el scope (si aplica), y el bot genera un
archivo .md que envía como documento de Telegram.
"""

from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from adso.bot_utils import _get_existing_items
from adso.config import Settings
from adso.constants import (
    CB_CANCEL,
    CB_REPORT_HEALTH,
    CB_REPORT_IDEAS,
    CB_REPORT_IDEAS_PREFIX,
    CB_REPORT_MENU,
    CB_REPORT_READING,
    CB_REPORT_READING_PREFIX,
    CB_REPORT_SCOPE,
    CB_REPORT_SCOPE_PREFIX,
)
from adso.keyboards import (
    _esc,
    build_report_scope_keyboard,
    build_report_type_keyboard,
)
from adso.reporters import health_report, ideas_report, reading_queue, scope_report
from adso.security import authorized

logger = logging.getLogger(__name__)

# Keywords que disparan el menú de reportes en handle_text
REPORT_KEYWORDS = {"reporte", "resumen", "informe"}


@authorized
async def handle_reporte_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /reporte — muestra el menú de tipos de reporte.

    Args:
        update: Telegram update.
        context: Bot context.
    """
    await update.message.reply_text(
        "¿Qué reporte querés generar?",
        reply_markup=build_report_type_keyboard(),
    )


async def handle_report_callback(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    """Maneja todos los callbacks CB_REPORT_*.

    Routing principal:
    - rpt:menu        → muestra menú de tipos
    - rpt:scope       → pide scope (proyecto/área/inbox)
    - rpt:ideas       → pide scope (proyecto/área/todo)
    - rpt:health      → genera reporte directamente
    - rpt:reading     → pide scope (proyecto/área/todo)
    - rpt:s:*         → genera reporte de scope con el destino elegido
    - rpt:i:*         → genera reporte de ideas con el destino elegido
    - rpt:r:*         → genera reporte de cola de lectura con el destino elegido

    Args:
        query: CallbackQuery de Telegram.
        context: Bot context.
        data: callback_data recibido.
    """
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    # --- Menú inicial ---
    if data == CB_REPORT_MENU:
        await query.edit_message_text(
            "¿Qué reporte querés generar?",
            reply_markup=build_report_type_keyboard(),
        )
        return

    # --- Tipo: Proyecto/Área → pedir scope ---
    if data == CB_REPORT_SCOPE:
        projects, areas = await _get_existing_items(vault_path)
        keyboard = build_report_scope_keyboard(
            projects, areas,
            include_all=False,
            prefix=CB_REPORT_SCOPE_PREFIX,
            inbox_label="Inbox",
        )
        await query.edit_message_text(
            "¿Scope del reporte?",
            reply_markup=keyboard,
        )
        return

    # --- Tipo: Ideas → pedir scope ---
    if data == CB_REPORT_IDEAS:
        projects, areas = await _get_existing_items(vault_path)
        keyboard = build_report_scope_keyboard(
            projects, areas,
            include_all=True,
            prefix=CB_REPORT_IDEAS_PREFIX,
            inbox_label=None,
        )
        await query.edit_message_text(
            "¿Filtrar ideas por scope?",
            reply_markup=keyboard,
        )
        return

    # --- Tipo: Cola de lectura → pedir scope ---
    if data == CB_REPORT_READING:
        projects, areas = await _get_existing_items(vault_path)
        keyboard = build_report_scope_keyboard(
            projects, areas,
            include_all=True,
            prefix=CB_REPORT_READING_PREFIX,
            inbox_label=None,
        )
        await query.edit_message_text(
            "¿Filtrar cola de lectura por scope?",
            reply_markup=keyboard,
        )
        return

    # --- Tipo: Salud del vault → generar directo ---
    if data == CB_REPORT_HEALTH:
        await query.edit_message_text("Generando reporte de salud del vault...")
        await _send_report(
            query, context, vault_path,
            report_bytes_coro=health_report(vault_path),
            filename=f"salud-vault-{date.today()}.md",
        )
        return

    # --- Scope del reporte de proyecto/área/inbox ---
    if data.startswith(CB_REPORT_SCOPE_PREFIX):
        suffix = data[len(CB_REPORT_SCOPE_PREFIX):]
        project, area, inbox = _parse_scope_suffix(suffix)
        await query.edit_message_text("Generando reporte de scope...")
        await _send_report(
            query, context, vault_path,
            report_bytes_coro=scope_report(vault_path, project=project, area=area, inbox=inbox),
            filename=f"scope-{suffix.replace(':', '-')}-{date.today()}.md",
        )
        return

    # --- Scope del reporte de ideas ---
    if data.startswith(CB_REPORT_IDEAS_PREFIX):
        suffix = data[len(CB_REPORT_IDEAS_PREFIX):]
        project, area, _ = _parse_scope_suffix(suffix)
        await query.edit_message_text("Generando reporte de ideas...")
        await _send_report(
            query, context, vault_path,
            report_bytes_coro=ideas_report(vault_path, project=project, area=area),
            filename=f"ideas-{suffix.replace(':', '-')}-{date.today()}.md",
        )
        return

    # --- Scope del reporte de cola de lectura ---
    if data.startswith(CB_REPORT_READING_PREFIX):
        suffix = data[len(CB_REPORT_READING_PREFIX):]
        project, area, _ = _parse_scope_suffix(suffix)
        await query.edit_message_text("Generando cola de lectura...")
        await _send_report(
            query, context, vault_path,
            report_bytes_coro=reading_queue(vault_path, project=project, area=area),
            filename=f"lectura-{suffix.replace(':', '-')}-{date.today()}.md",
        )
        return


def _parse_scope_suffix(suffix: str) -> tuple[Optional[str], Optional[str], bool]:
    """Parsea el sufijo del callback_data de scope.

    Formatos:
    - "p:nombre"   → project="nombre", area=None, inbox=False
    - "a:nombre"   → project=None, area="nombre", inbox=False
    - "inbox"      → project=None, area=None, inbox=True
    - "all"        → project=None, area=None, inbox=False

    Args:
        suffix: Parte del callback_data después del prefijo.

    Returns:
        Tupla (project, area, inbox).
    """
    if suffix == "inbox":
        return None, None, True
    if suffix == "all":
        return None, None, False
    if suffix.startswith("p:"):
        return suffix[2:], None, False
    if suffix.startswith("a:"):
        return None, suffix[2:], False
    return None, None, False


async def _send_report(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    vault_path: Path,
    report_bytes_coro,
    filename: str,
) -> None:
    """Genera un reporte y lo envía como documento .md.

    Si el reporte está vacío (menos de 200 bytes de contenido real), notifica
    en el chat en vez de enviar un archivo vacío.

    Args:
        query: CallbackQuery para editar el mensaje de progreso.
        context: Bot context.
        vault_path: Path del vault (usado para obtener chat_id).
        report_bytes_coro: Coroutine que retorna bytes del reporte.
        filename: Nombre del archivo .md a enviar.
    """
    settings: Settings = context.bot_data["settings"]
    chat_id = settings.telegram_allowed_user_id

    try:
        report_bytes = await report_bytes_coro
    except Exception as e:
        logger.exception("Error generando reporte '%s': %s", filename, e)
        try:
            await query.edit_message_text(f"Error al generar el reporte: {_esc(str(e))}")
        except Exception:
            pass
        return

    # Detectar reportes vacíos: menos de 400 bytes sugiere solo el header
    if len(report_bytes) < 400:
        try:
            await query.edit_message_text("No encontré notas para este scope.")
        except Exception:
            pass
        return

    try:
        doc = io.BytesIO(report_bytes)
        doc.name = filename
        await context.bot.send_document(
            chat_id=chat_id,
            document=doc,
            filename=filename,
        )
        # Limpiar el mensaje de "generando..."
        try:
            await query.delete_message()
        except Exception:
            pass
    except Exception as e:
        logger.exception("Error enviando reporte '%s': %s", filename, e)
        try:
            await query.edit_message_text(f"Error al enviar el reporte: {_esc(str(e))}")
        except Exception:
            pass
