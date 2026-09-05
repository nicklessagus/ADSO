"""Flujo de gestión: crear proyectos/áreas, archivar, etc."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from telegram import Update
from telegram.ext import ContextTypes

from adso.bot_utils import (
    _extract_name_from_command,
    _get_existing_items,
    mark_bot_written,
)
from adso.config import Settings
from adso.keyboards import _esc, build_manage_keyboard
from adso.llm_client import classify
from adso.vault_writer import _safe_component, build_index_note, create_note

logger = logging.getLogger(__name__)


_OPERATION_LABELS = {
    "create_project": "proyecto",
    "create_area": "área",
    "create_section": "sección",
}


def _is_blank(value: Any) -> bool:
    """True si un param de gestión no trae nada usable.

    Cubre ``None``, ``""`` y el string ``"None"``: un modelo chico serializa el
    null como texto y llegaba al índice como nombre o descripción literal.
    """
    return value is None or str(value).strip() in ("", "None")


def _operation_label(operation: str) -> str:
    """Etiqueta en español de una operación de gestión.

    Antes era `"proyecto" if "project" in operation else "área"`: como
    "project" no está en "create_section", el prompt decía "Para crear el
    **área** hacen falta: nombre de la sección…". G11 de
    docs/audit-2026-07-31.md.

    Args:
        operation: Nombre de la operación (`create_project`, etc.).

    Returns:
        Etiqueta legible; "elemento" si la operación no se reconoce.
    """
    return _OPERATION_LABELS.get(operation, "elemento")


def pop_manage_state(context: ContextTypes.DEFAULT_TYPE) -> Optional[dict]:
    """Saca el estado del flujo de gestión de ``user_data`` y devuelve la operación.

    Popea ``pending_operation`` y ``manage_missing_fields`` juntas: ambas están
    en ``_PENDING_FLOW_KEYS``, así que dejar la segunda colgada hace que
    ``reclassify_inbox`` posponga cada pasada indefinidamente (el inbox nunca se
    drena) hasta un ``/reset`` que el usuario no tiene motivo de ejecutar.

    Args:
        context: Bot context con ``user_data``.

    Returns:
        El dict de la operación pendiente, o None si no había ninguna.
    """
    context.user_data.pop("manage_missing_fields", None)
    return context.user_data.pop("pending_operation", None)


async def _handle_manage_missing_fields(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    """Rellena campos faltantes de una operación manage con el texto del usuario.

    Parsea 'Nombre — Descripción' o 'Nombre: descripción' o texto libre.
    """
    pending = context.user_data["pending_operation"]
    params = pending["payload"]["params"]
    missing = context.user_data.get("manage_missing_fields") or []

    # Si lo único que falta es la descripción (típico del camino por botón, que
    # resuelve el nombre por regex), el texto entero ES la descripción. Sin este
    # caso, el `else` de abajo lo tomaba como nombre y pisaba el que ya estaba.
    if missing == ["descripción"] and params.get("name"):
        name = ""
        description = text.strip()
    else:
        _SEP = re.compile(r"\s+[—–\-]\s+")
        m = _SEP.search(text)
        if m:
            name = text[:m.start()].strip()
            description = text[m.end():].strip()
        elif ": " in text and not params.get("name"):
            parts = text.split(": ", 1)
            name, description = parts[0].strip(), parts[1].strip()
        else:
            name = text.strip()
            description = ""

    if name:
        params["name"] = name
    if description:
        params["description"] = description

    context.user_data.pop("manage_missing_fields", None)
    await _handle_manage(update, context, pending)


async def _handle_manage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: dict,
) -> None:
    """Procesa modo manage: muestra operación + confirmación.

    Si faltan datos obligatorios (name, description), pregunta al usuario.
    """
    payload = result["payload"]
    operation = payload.get("operation", "?")
    params = payload.get("params", {})

    missing = []
    if operation in ("create_project", "create_area"):
        if _is_blank(params.get("name")):
            missing.append("nombre")
        if _is_blank(params.get("description")):
            missing.append("descripción")
    elif operation == "create_section":
        if _is_blank(params.get("name")):
            missing.append("nombre de la sección")
        if _is_blank(params.get("project")):
            missing.append("nombre del proyecto")

    if missing:
        context.user_data["pending_operation"] = result
        context.user_data["manage_missing_fields"] = missing
        op_label = _operation_label(operation)
        await update.message.reply_text(
            f"Para crear el {op_label} hacen falta: <b>{', '.join(missing)}</b>.\n"
            f"Enviar el nombre y la descripción (ej: <i>Docencia — gestión de clases y materiales</i>)",
            parse_mode="HTML",
        )
        return

    context.user_data["pending_operation"] = result

    op_desc = {
        "create_project": f"Crear proyecto '{params.get('name')}'\nDescripción: {params.get('description')}",
        "create_area": f"Crear área '{params.get('name')}'\nDescripción: {params.get('description')}",
        "create_section": f"Crear sección '{params.get('name')}' en proyecto '{params.get('project')}'",
        "archive_project": f"Archivar proyecto '{params.get('name')}'",
        "delete_project": f"Eliminar proyecto '{params.get('name')}'",
        "delete_area": f"Eliminar área '{params.get('name')}'",
    }

    desc = op_desc.get(operation, f"Operación: {operation}\nParams: {params}")

    await update.message.reply_text(
        f"<b>Gestión</b>\n\n{_esc(desc)}",
        reply_markup=build_manage_keyboard(),
        parse_mode="HTML",
    )


async def _notify_index_written(
    context: ContextTypes.DEFAULT_TYPE, path: Path, title: str
) -> None:
    """Registra un `_index.md` recién escrito y lo encola para el backup.

    Mismo patrón que `_cb_confirm` y `reclassify_inbox`. Sin esto, el commit de
    backup y el no-doble-embed dependían de que el `VaultWatcher` tratara la
    escritura propia del bot como un cambio externo: notificación espuria en
    modo debug, y nada en absoluto si el watcher está caído.
    G12 de docs/audit-2026-07-31.md.

    Args:
        context: Bot context (para `bot_data`).
        path: Path del `_index.md` escrito.
        title: Título de la nota, para el mensaje de commit.
    """
    mark_bot_written(context.bot_data, path)
    git_backup = context.bot_data.get("git_backup")
    if git_backup:
        await git_backup.notify(title)


async def _cb_manage_confirm(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    vault_path: Path,
) -> None:
    """Ejecuta operación de gestión confirmada."""
    pending = pop_manage_state(context)
    if not pending:
        await query.edit_message_text("No hay operación pendiente.")
        return

    payload = pending["payload"]
    operation = payload["operation"]
    params = payload["params"]

    # Sanitizar nombres contra path traversal antes de construir cualquier path.
    # El nombre puede venir del LLM o de texto libre del usuario.
    safe_name = _safe_component(params.get("name"))
    if operation in ("create_project", "create_area", "create_section") and not safe_name:
        await query.edit_message_text(
            f"Nombre inválido: {params.get('name')!r}. No se creó nada."
        )
        return
    if operation == "create_section":
        safe_project = _safe_component(params.get("project"))
        if not safe_project:
            await query.edit_message_text(
                f"Proyecto inválido: {params.get('project')!r}. No se creó nada."
            )
            return
        params["project"] = safe_project
    if safe_name:
        params["name"] = safe_name

    # `description` obligatoria: el flujo por botón dejaba `description=""` y
    # ofrecía confirmar directo, así que se creaba el índice con descripción
    # vacía — viola la regla "el bot la pide y no permite omitirla" (CLAUDE.md).
    # G10 de docs/audit-2026-07-31.md.
    if operation in ("create_project", "create_area"):
        descripcion = (params.get("description") or "").strip()
        if _is_blank(descripcion):
            # `pop_manage_state` ya popeó el estado: hay que reponerlo para que
            # `_handle_manage_missing_fields` pueda retomar con el texto que
            # escriba el usuario.
            context.user_data["pending_operation"] = pending
            context.user_data["manage_missing_fields"] = ["descripción"]
            await query.edit_message_text(
                f"Falta la descripción para crear el {_operation_label(operation)} "
                f"'{params.get('name')}'. Escribirla a continuación:"
            )
            return
        params["description"] = descripcion

    try:
        if operation in ("create_project", "create_area"):
            kind = "project" if operation == "create_project" else "area"
            folder = "01-Projects" if kind == "project" else "02-Areas"
            label, done = ("proyecto", "creado") if kind == "project" else ("área", "creada")
            if (vault_path / folder / params["name"]).exists():
                await query.edit_message_text(f"El {label} '{params['name']}' ya existe.")
                return
            fm, body = build_index_note(kind, params["name"], params["description"])
            index_path = await create_note(fm, body, vault_path)
            await _notify_index_written(context, index_path, fm["title"])
            await query.edit_message_text(
                f"{label.capitalize()} '{params['name']}' {done}."
            )

        elif operation == "create_section":
            section_dir = vault_path / "01-Projects" / params["project"] / params["name"]
            section_dir.mkdir(parents=True, exist_ok=True)
            await query.edit_message_text(
                f"Sección '{params['name']}' creada en proyecto '{params['project']}'."
            )

        else:
            await query.edit_message_text(
                f"Operación '{operation}' todavía no está disponible."
            )

    except Exception:
        # Mensaje genérico al chat y traceback al log: el `f"Error: {e}"` volcaba
        # la excepción cruda (paths internos incluidos) al usuario.
        # G13 de docs/audit-2026-07-31.md.
        logger.exception("Error en operación de gestión %s", operation)
        await query.edit_message_text(
            "No se pudo completar la operación. Ver los logs para el detalle."
        )


def _pop_pending_content(context: ContextTypes.DEFAULT_TYPE) -> tuple[str | None, dict]:
    """Extrae texto pendiente y contexto de captura (media_type, preserve_body, resource_file).

    Soporta texto libre (pending_raw_content solo) y audio/otros medios
    que además guardan pending_capture_ctx.
    """
    text = context.user_data.pop("pending_raw_content", None)
    ctx = context.user_data.pop("pending_capture_ctx", {})
    return text, ctx


async def _run_intent(
    update: Update, context: ContextTypes.DEFAULT_TYPE, **classify_kwargs: Any
) -> None:
    """Consume el contenido pendiente y lo manda al flujo de captura.

    Camino común de `[Tarea]` y `[Nota]`: popea ``pending_raw_content`` (y el
    contexto de captura que lo acompaña en audio) y clasifica con
    ``force_capture=True`` — el usuario ya eligió guardar, así que un
    ``mode=manage`` del LLM no puede descartar el texto.
    """
    from adso.handlers.capture import _classify_and_preview  # evitar circular en módulo-nivel

    text, ctx = _pop_pending_content(context)
    query = update.callback_query
    if not text:
        await query.edit_message_text("No hay contenido pendiente.")
        return
    await query.edit_message_text("Clasificando...")
    await _classify_and_preview(
        update, context, text, force_capture=True,
        media_type=ctx.get("media_type", "text"),
        preserve_body=ctx.get("preserve_body", False),
        resource_file=ctx.get("resource_file"),
        **classify_kwargs,
    )


async def _cb_intent_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """El usuario eligió guardar como tarea — el LLM infiere título/tags/destino, type=task fijo."""
    await _run_intent(
        update, context,
        forced_type="task",
        user_context="El usuario clasificó este contenido como tarea. Inferir prioridad, fecha límite y proyecto/área con ese foco.",
    )


async def _cb_intent_note(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """El usuario eligió guardar como nota — el LLM infiere tipo (reference/idea) y resto."""
    await _run_intent(
        update, context,
        user_context="El usuario clasificó este contenido como nota (no es una tarea).",
        prevent_task=True,
    )


async def _cb_intent_create(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    operation: str,
) -> None:
    """El usuario eligió crear proyecto o área.

    Llama al LLM para inferir nombre y descripción del texto original,
    muestra preview con opción de corregir por texto libre.

    Args:
        operation: 'create_project' o 'create_area'.
    """
    text = context.user_data.pop("pending_raw_content", None)
    query = update.callback_query
    type_label = "proyecto" if operation == "create_project" else "área"

    if not text:
        await query.edit_message_text("No hay contenido pendiente.")
        return

    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    name = _extract_name_from_command(text, operation)
    description = ""

    if not name:
        await query.edit_message_text(f"Infiriendo nombre del {type_label}...")
        projects, areas = await _get_existing_items(vault_path)
        result = await classify(
            content=text,
            media_type="text",
            existing_projects=projects,
            existing_areas=areas,
            disambiguation_threshold=0.5,
        )
        if result.get("mode") == "manage":
            params = result.get("payload", {}).get("params", {})
            name = (params.get("name") or "").strip()
            description = (params.get("description") or "").strip()

    if not name:
        name = text[:60].strip()

    pending_op = {
        "mode": "manage",
        "payload": {
            "operation": operation,
            "params": {"name": name, "description": description},
        },
    }
    context.user_data["pending_operation"] = pending_op
    context.user_data["manage_missing_fields"] = ["correction"]

    desc_line = f"\n<b>Descripción:</b> {_esc(description)}" if description else ""
    await query.edit_message_text(
        f"<b>Crear {type_label}:</b> {_esc(name)}{desc_line}\n\n"
        f"Corregir por texto libre (<i>Nombre — Descripción</i>) o confirmar.",
        reply_markup=build_manage_keyboard(),
        parse_mode="HTML",
    )
