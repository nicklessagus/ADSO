"""Handlers para reportes a pedido del vault.

/reporte dispara un menú de tipos. El usuario elige el tipo, luego navega
por una botonera de dos pasos para seleccionar el scope (categoría → item),
y el bot genera un archivo .md que envía como documento de Telegram.

/reporte_full es idéntico pero genera reportes con el cuerpo completo de cada nota
(usa _note_block en vez de _note_line en los reporters). El flag report_full se guarda
en user_data y se lee en handle_report_callback para pasarlo a los reporters.

Mientras el menú está activo, pending_report=True bloquea texto entrante.
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
    CB_REPORT_IDEAS_SHOW_A,
    CB_REPORT_IDEAS_SHOW_P,
    CB_REPORT_MENU,
    CB_REPORT_READING,
    CB_REPORT_READING_PREFIX,
    CB_REPORT_READING_SHOW_A,
    CB_REPORT_READING_SHOW_P,
    CB_REPORT_SCOPE,
    CB_REPORT_SCOPE_PREFIX,
    CB_REPORT_SCOPE_SHOW_A,
    CB_REPORT_SCOPE_SHOW_P,
)
from adso.keyboards import (
    _esc,
    build_report_category_keyboard,
    build_report_items_keyboard,
    build_report_type_keyboard,
)
from adso.reporters import health_report, ideas_report, reading_queue, scope_report
from adso.security import authorized

logger = logging.getLogger(__name__)


@authorized
async def handle_reporte_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /reporte — muestra el menú de tipos de reporte.

    Setea pending_report=True para bloquear texto mientras el menú está activo.

    Args:
        update: Telegram update.
        context: Bot context.
    """
    context.user_data["pending_report"] = True
    context.user_data["report_full"] = False
    await update.message.reply_text(
        "¿Qué reporte querés generar?",
        reply_markup=build_report_type_keyboard(),
    )


@authorized
async def handle_reporte_full_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /reporte_full — igual a /reporte pero con detalle completo de cada nota.

    Setea report_full=True para que los reporters incluyan el cuerpo de cada nota.

    Args:
        update: Telegram update.
        context: Bot context.
    """
    context.user_data["pending_report"] = True
    context.user_data["report_full"] = True
    await update.message.reply_text(
        "¿Qué reporte querés generar? (modo completo — incluye contenido de cada nota)",
        reply_markup=build_report_type_keyboard(),
    )


async def handle_report_callback(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    """Maneja todos los callbacks rpt:*.

    Flujo de dos pasos para scope/ideas/lectura:
    1. Tipo → categoría (Proyectos / Áreas / extra)
    2. Categoría → lista de items → genera reporte

    Args:
        query: CallbackQuery de Telegram.
        context: Bot context.
        data: callback_data recibido.
    """
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    # --- Menú inicial ---
    if data == CB_REPORT_MENU:
        context.user_data["pending_report"] = True
        await query.edit_message_text(
            "¿Qué reporte querés generar?",
            reply_markup=build_report_type_keyboard(),
        )
        return

    # --- Tipo: Proyecto/Área → paso 1: elegir categoría ---
    if data == CB_REPORT_SCOPE:
        await query.edit_message_text(
            "¿Querés el reporte de un proyecto, un área o el inbox?",
            reply_markup=build_report_category_keyboard(
                show_p_cb=CB_REPORT_SCOPE_SHOW_P,
                show_a_cb=CB_REPORT_SCOPE_SHOW_A,
                extra_cb=f"{CB_REPORT_SCOPE_PREFIX}inbox",
                extra_label="Inbox",
            ),
        )
        return

    # --- Tipo: Ideas → paso 1: elegir categoría ---
    if data == CB_REPORT_IDEAS:
        await query.edit_message_text(
            "¿Filtrar ideas por proyecto, área o ver todas?",
            reply_markup=build_report_category_keyboard(
                show_p_cb=CB_REPORT_IDEAS_SHOW_P,
                show_a_cb=CB_REPORT_IDEAS_SHOW_A,
                extra_cb=f"{CB_REPORT_IDEAS_PREFIX}all",
                extra_label="Todas",
            ),
        )
        return

    # --- Tipo: Cola de lectura → paso 1: elegir categoría ---
    if data == CB_REPORT_READING:
        await query.edit_message_text(
            "¿Filtrar la cola de lectura por proyecto, área o ver toda?",
            reply_markup=build_report_category_keyboard(
                show_p_cb=CB_REPORT_READING_SHOW_P,
                show_a_cb=CB_REPORT_READING_SHOW_A,
                extra_cb=f"{CB_REPORT_READING_PREFIX}all",
                extra_label="Toda la cola",
            ),
        )
        return

    # --- Paso 2: lista de proyectos o áreas ---
    if data in (CB_REPORT_SCOPE_SHOW_P, CB_REPORT_SCOPE_SHOW_A,
                CB_REPORT_IDEAS_SHOW_P, CB_REPORT_IDEAS_SHOW_A,
                CB_REPORT_READING_SHOW_P, CB_REPORT_READING_SHOW_A):
        await _show_items_keyboard(query, context, vault_path, data)
        return

    full: bool = context.user_data.get("report_full", False)

    # --- Tipo: Salud del vault → generar directo ---
    if data == CB_REPORT_HEALTH:
        await query.edit_message_text("Generando reporte de salud del vault...")
        await _send_report(
            query, context,
            report_bytes_coro=health_report(vault_path, full=full),
            filename=f"salud-vault-{date.today()}.md",
        )
        return

    # --- Scope final: generar reporte de proyecto/área/inbox ---
    if data.startswith(CB_REPORT_SCOPE_PREFIX):
        suffix = data[len(CB_REPORT_SCOPE_PREFIX):]
        project, area, inbox = _parse_scope_suffix(suffix)
        await query.edit_message_text("Generando reporte...")
        await _send_report(
            query, context,
            report_bytes_coro=scope_report(vault_path, project=project, area=area, inbox=inbox, full=full),
            filename=f"scope-{suffix.replace(':', '-')}-{date.today()}.md",
        )
        return

    # --- Scope final: generar reporte de ideas ---
    if data.startswith(CB_REPORT_IDEAS_PREFIX):
        suffix = data[len(CB_REPORT_IDEAS_PREFIX):]
        project, area, _ = _parse_scope_suffix(suffix)
        await query.edit_message_text("Generando reporte de ideas...")
        await _send_report(
            query, context,
            report_bytes_coro=ideas_report(vault_path, project=project, area=area, full=full),
            filename=f"ideas-{suffix.replace(':', '-')}-{date.today()}.md",
        )
        return

    # --- Scope final: generar cola de lectura ---
    if data.startswith(CB_REPORT_READING_PREFIX):
        suffix = data[len(CB_REPORT_READING_PREFIX):]
        project, area, _ = _parse_scope_suffix(suffix)
        await query.edit_message_text("Generando cola de lectura...")
        await _send_report(
            query, context,
            report_bytes_coro=reading_queue(vault_path, project=project, area=area, full=full),
            filename=f"lectura-{suffix.replace(':', '-')}-{date.today()}.md",
        )
        return


async def _show_items_keyboard(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    vault_path: Path,
    data: str,
) -> None:
    """Muestra la lista de proyectos o áreas según el callback recibido.

    Args:
        query: CallbackQuery.
        context: Bot context.
        vault_path: Path del vault.
        data: callback_data que indica tipo de reporte y categoría.
    """
    projects, areas = await _get_existing_items(vault_path)

    # Determinar si es proyectos o áreas, el prefijo final y el back_cb
    if data == CB_REPORT_SCOPE_SHOW_P:
        items, is_project, prefix, back_cb = projects, True, CB_REPORT_SCOPE_PREFIX, CB_REPORT_SCOPE
        label = "¿Qué proyecto?"
    elif data == CB_REPORT_SCOPE_SHOW_A:
        items, is_project, prefix, back_cb = areas, False, CB_REPORT_SCOPE_PREFIX, CB_REPORT_SCOPE
        label = "¿Qué área?"
    elif data == CB_REPORT_IDEAS_SHOW_P:
        items, is_project, prefix, back_cb = projects, True, CB_REPORT_IDEAS_PREFIX, CB_REPORT_IDEAS
        label = "¿Ideas de qué proyecto?"
    elif data == CB_REPORT_IDEAS_SHOW_A:
        items, is_project, prefix, back_cb = areas, False, CB_REPORT_IDEAS_PREFIX, CB_REPORT_IDEAS
        label = "¿Ideas de qué área?"
    elif data == CB_REPORT_READING_SHOW_P:
        items, is_project, prefix, back_cb = projects, True, CB_REPORT_READING_PREFIX, CB_REPORT_READING
        label = "¿Cola de lectura de qué proyecto?"
    else:  # CB_REPORT_READING_SHOW_A
        items, is_project, prefix, back_cb = areas, False, CB_REPORT_READING_PREFIX, CB_REPORT_READING
        label = "¿Cola de lectura de qué área?"

    if not items:
        tipo = "proyectos" if is_project else "áreas"
        await query.edit_message_text(
            f"No hay {tipo} en el vault todavía.",
            reply_markup=build_report_type_keyboard(),
        )
        return

    await query.edit_message_text(
        label,
        reply_markup=build_report_items_keyboard(items, is_project, prefix, back_cb),
    )


def _parse_scope_suffix(suffix: str) -> tuple[Optional[str], Optional[str], bool]:
    """Parsea el sufijo del callback_data de scope.

    Formatos:
    - "p:nombre"  → project="nombre", area=None, inbox=False
    - "a:nombre"  → project=None, area="nombre", inbox=False
    - "inbox"     → project=None, area=None, inbox=True
    - "all"       → project=None, area=None, inbox=False

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
    report_bytes_coro,
    filename: str,
) -> None:
    """Genera un reporte y lo envía como documento .md. Limpia pending_report al terminar.

    Si el reporte está vacío (menos de 400 bytes), notifica en el chat
    en vez de enviar un archivo vacío.

    Args:
        query: CallbackQuery para editar el mensaje de progreso.
        context: Bot context.
        report_bytes_coro: Coroutine que retorna bytes del reporte.
        filename: Nombre del archivo .md a enviar.
    """
    settings: Settings = context.bot_data["settings"]
    chat_id = settings.telegram_allowed_user_id

    try:
        report_bytes = await report_bytes_coro
    except Exception as e:
        logger.exception("Error generando reporte '%s': %s", filename, e)
        context.user_data.pop("pending_report", None)
        try:
            await query.edit_message_text(f"Error al generar el reporte: {_esc(str(e))}")
        except Exception:
            pass
        return

    # Reporte vacío: solo tiene el header
    if len(report_bytes) < 400:
        context.user_data.pop("pending_report", None)
        try:
            await query.edit_message_text("No encontré notas para este scope.")
        except Exception:
            pass
        return

    context.user_data.pop("pending_report", None)

    try:
        doc = io.BytesIO(report_bytes)
        doc.name = filename
        await context.bot.send_document(
            chat_id=chat_id,
            document=doc,
            filename=filename,
        )
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
