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

from telegram import Update
from telegram.ext import ContextTypes

from adso.bot_utils import _get_existing_items, _is_awaiting_text_input
from adso.config import Settings
from adso.constants import (
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
    resolve_item_token,
)
from adso.reporters import health_report, ideas_report, reading_queue, scope_report
from adso.security import authorized

logger = logging.getLogger(__name__)


# Paso 1 de cada tipo de reporte con scope: pregunta, callbacks de "mostrar
# proyectos" / "mostrar áreas" y la opción extra (Inbox / todo).
_CATEGORY_STEP: dict[str, tuple[str, str, str, str, str]] = {
    CB_REPORT_SCOPE: (
        "¿Reporte de un proyecto, un área o el inbox?",
        CB_REPORT_SCOPE_SHOW_P, CB_REPORT_SCOPE_SHOW_A,
        f"{CB_REPORT_SCOPE_PREFIX}inbox", "Inbox",
    ),
    CB_REPORT_IDEAS: (
        "¿Filtrar ideas por proyecto, área o ver todas?",
        CB_REPORT_IDEAS_SHOW_P, CB_REPORT_IDEAS_SHOW_A,
        f"{CB_REPORT_IDEAS_PREFIX}all", "Todas",
    ),
    CB_REPORT_READING: (
        "¿Filtrar la cola de lectura por proyecto, área o ver toda?",
        CB_REPORT_READING_SHOW_P, CB_REPORT_READING_SHOW_A,
        f"{CB_REPORT_READING_PREFIX}all", "Toda la cola",
    ),
}

# Paso 2: qué lista mostrar (proyectos o áreas), con qué prefijo final y a
# dónde vuelve [← Volver].
_ITEMS_STEP: dict[str, tuple[bool, str, str, str]] = {
    CB_REPORT_SCOPE_SHOW_P: (True, CB_REPORT_SCOPE_PREFIX, CB_REPORT_SCOPE, "¿Qué proyecto?"),
    CB_REPORT_SCOPE_SHOW_A: (False, CB_REPORT_SCOPE_PREFIX, CB_REPORT_SCOPE, "¿Qué área?"),
    CB_REPORT_IDEAS_SHOW_P: (True, CB_REPORT_IDEAS_PREFIX, CB_REPORT_IDEAS, "¿Ideas de qué proyecto?"),
    CB_REPORT_IDEAS_SHOW_A: (False, CB_REPORT_IDEAS_PREFIX, CB_REPORT_IDEAS, "¿Ideas de qué área?"),
    CB_REPORT_READING_SHOW_P: (
        True, CB_REPORT_READING_PREFIX, CB_REPORT_READING, "¿Cola de lectura de qué proyecto?"
    ),
    CB_REPORT_READING_SHOW_A: (
        False, CB_REPORT_READING_PREFIX, CB_REPORT_READING, "¿Cola de lectura de qué área?"
    ),
}


async def _start_report_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, full: bool
) -> None:
    """Muestra el menú de tipos de reporte y bloquea texto mientras esté activo.

    `pending_report=True` bloquea texto entrante; `report_full` lo leen los
    callbacks para pasarlo a los reporters.
    """
    if _is_awaiting_text_input(context):
        await update.message.reply_text("Hay una corrección pendiente. Escribir el texto primero.")
        return
    context.user_data["pending_report"] = True
    context.user_data["report_full"] = full
    prompt = "¿Qué reporte generar?"
    if full:
        prompt += " (modo completo — incluye contenido de cada nota)"
    await update.message.reply_text(prompt, reply_markup=build_report_type_keyboard())


@authorized
async def handle_reporte_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /reporte — muestra el menú de tipos de reporte."""
    await _start_report_menu(update, context, full=False)


@authorized
async def handle_reporte_full_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /reporte_full — igual a /reporte pero con el cuerpo completo de cada nota."""
    await _start_report_menu(update, context, full=True)


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
            "¿Qué reporte generar?",
            reply_markup=build_report_type_keyboard(),
        )
        return

    # --- Paso 1: elegir categoría (proyectos / áreas / extra) ---
    if data in _CATEGORY_STEP:
        prompt, show_p_cb, show_a_cb, extra_cb, extra_label = _CATEGORY_STEP[data]
        await query.edit_message_text(
            prompt,
            reply_markup=build_report_category_keyboard(
                show_p_cb=show_p_cb, show_a_cb=show_a_cb,
                extra_cb=extra_cb, extra_label=extra_label,
            ),
        )
        return

    # --- Paso 2: lista de proyectos o áreas ---
    if data in _ITEMS_STEP:
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

    # --- Scope final: generar el reporte con scope (proyecto/área/inbox/todo) ---
    for prefix, progress, stem, reporter in (
        (CB_REPORT_SCOPE_PREFIX, "Generando reporte...", "scope", scope_report),
        (CB_REPORT_IDEAS_PREFIX, "Generando reporte de ideas...", "ideas", ideas_report),
        (CB_REPORT_READING_PREFIX, "Generando cola de lectura...", "lectura", reading_queue),
    ):
        if not data.startswith(prefix):
            continue
        suffix = data[len(prefix):]
        project, area, inbox, missing = await _parse_scope_suffix(suffix, vault_path)
        if missing:
            await _aviso_scope_borrado(query, context, suffix)
            return
        kwargs: dict = {"project": project, "area": area, "full": full}
        if reporter is scope_report:
            kwargs["inbox"] = inbox
        await query.edit_message_text(progress)
        await _send_report(
            query, context,
            report_bytes_coro=reporter(vault_path, **kwargs),
            filename=f"{stem}-{suffix.replace(':', '-')}-{date.today()}.md",
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
    is_project, prefix, back_cb, label = _ITEMS_STEP[data]
    items = projects if is_project else areas

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


async def _parse_scope_suffix(
    suffix: str, vault_path: Path
) -> tuple[Optional[str], Optional[str], bool, bool]:
    """Parsea el sufijo del callback_data de scope.

    Formatos:
    - "p:token"   → project="nombre", area=None, inbox=False
    - "a:token"   → project=None, area="nombre", inbox=False
    - "inbox"     → project=None, area=None, inbox=True
    - "all"       → project=None, area=None, inbox=False

    Args:
        suffix: Parte del callback_data después del prefijo.

    Returns:
        Tupla (project, area, inbox, missing). `missing=True` significa que el
        token tenía forma válida pero el proyecto/área ya no existe (se borró
        entre que se dibujó el teclado y el usuario apretó el botón).
    """
    if suffix == "inbox":
        return None, None, True, False
    if suffix == "all":
        return None, None, False, False
    # El sufijo lleva un token, no el nombre: antes viajaba el nombre truncado
    # a 32 chars y `scope_report` armaba `01-Projects/{truncado}`, un path
    # inexistente → "No se encontraron notas", reporte vacío y sin error.
    # F3 de docs/audit-2026-07-31.md.
    #
    # El `None` de `resolve_item_token` significa "ese proyecto/área ya no
    # existe" y hay que propagarlo como tal: devolverlo como `project=None`
    # hacía que `scope_report` cayera al scope "Vault completo" y el usuario
    # recibiera un reporte de TODO el vault como si fuera el que pidió (R2).
    if suffix.startswith("p:"):
        project = await resolve_item_token(suffix[2:], vault_path, True)
        return project, None, False, project is None
    if suffix.startswith("a:"):
        area = await resolve_item_token(suffix[2:], vault_path, False)
        return None, area, False, area is None
    return None, None, False, False


async def _aviso_scope_borrado(query, context: ContextTypes.DEFAULT_TYPE, suffix: str) -> None:
    """Avisa que el proyecto/área elegido ya no existe y repone el menú.

    Mismo trato que `callbacks.py` le da al `None` de `resolve_item_token` en el
    flujo de captura. El menú queda activo (`pending_report`) para que el
    usuario elija otro reporte sin tener que repetir el comando.
    """
    tipo = "Ese proyecto" if suffix.startswith("p:") else "Esa área"
    context.user_data["pending_report"] = True
    try:
        await query.edit_message_text(
            f"{tipo} ya no existe. Elegir otro reporte.",
            reply_markup=build_report_type_keyboard(),
        )
    except Exception:
        logger.warning("No se pudo avisar que el scope del reporte ya no existe.")


async def _send_report(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    report_bytes_coro,
    filename: str,
) -> None:
    """Genera un reporte y lo envía como documento .md. Limpia pending_report al terminar.

    Si el reporte no tiene ningún ítem (`ReportBytes.item_count == 0`), notifica
    en el chat en vez de enviar un archivo con secciones vacías.

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
            # `_esc` + parse_mode van juntos: escapar sin declararlo hacía que
            # el usuario leyera `&lt;...&gt;` literal en el aviso de error (E8).
            await query.edit_message_text(
                f"Error al generar el reporte: {_esc(str(e))}", parse_mode="HTML"
            )
        except Exception:
            pass
        return

    # Reporte vacío: ningún ítem en el scope. El conteo lo trae el reporter
    # (`ReportBytes.item_count`): medirlo en bytes no funcionaba — el header
    # solo ya pesa ~650 bytes, así que el umbral de 400 nunca se alcanzaba y
    # esta rama era código muerto. El usuario recibía igual un .md que solo
    # decía "_Sin referencias activas._" (R1).
    if getattr(report_bytes, "item_count", None) == 0:
        context.user_data.pop("pending_report", None)
        try:
            await query.edit_message_text("No se encontraron notas para este scope.")
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
            await query.edit_message_text(
                f"Error al enviar el reporte: {_esc(str(e))}", parse_mode="HTML"
            )
        except Exception:
            pass
