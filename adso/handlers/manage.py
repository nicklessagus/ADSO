"""Flujo de gestión: crear proyectos/áreas, archivar, etc."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from telegram import Update
from telegram.ext import ContextTypes

from adso.bot_utils import _extract_name_from_command, _get_existing_items
from adso.config import Settings
from adso.keyboards import _esc, build_manage_keyboard
from adso.llm_client import classify
from adso.vault_writer import create_note

logger = logging.getLogger(__name__)


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
        if not params.get("name") or params.get("name") in (None, "None", ""):
            missing.append("nombre")
        if not params.get("description") or params.get("description") in (None, "None", ""):
            missing.append("descripción")
    elif operation == "create_section":
        if not params.get("name") or params.get("name") in (None, "None", ""):
            missing.append("nombre de la sección")
        if not params.get("project") or params.get("project") in (None, "None", ""):
            missing.append("nombre del proyecto")

    if missing:
        context.user_data["pending_operation"] = result
        context.user_data["manage_missing_fields"] = missing
        op_label = "proyecto" if "project" in operation else "área"
        await update.message.reply_text(
            f"Para crear el {op_label} necesito: <b>{', '.join(missing)}</b>.\n"
            f"Mandame el nombre y la descripción (ej: <i>Docencia — gestión de clases y materiales</i>)",
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


async def _cb_manage_confirm(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    vault_path: Path,
) -> None:
    """Ejecuta operación de gestión confirmada."""
    pending = context.user_data.pop("pending_operation", None)
    if not pending:
        await query.edit_message_text("No hay operación pendiente.")
        return

    payload = pending["payload"]
    operation = payload["operation"]
    params = payload["params"]

    try:
        if operation == "create_project":
            project_dir = vault_path / "01-Projects" / params["name"]
            if project_dir.exists():
                await query.edit_message_text(
                    f"El proyecto '{params['name']}' ya existe."
                )
                return
            fm = {
                "title": params["name"].replace("-", " ").title(),
                "type": "project-index",
                "status": "active",
                "description": params["description"],
                "sections": [],
                "tags": ["system", params["name"]],
                "source": "system",
                "project": params["name"],
            }
            body = (
                f"# {fm['title']}\n\n"
                f"## Descripción\n{params['description']}\n\n"
                f"## Secciones\n\n## Estado\n- Creado: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            )
            await create_note(fm, body, vault_path)
            await query.edit_message_text(f"Proyecto '{params['name']}' creado.")

        elif operation == "create_area":
            area_dir = vault_path / "02-Areas" / params["name"]
            if area_dir.exists():
                await query.edit_message_text(
                    f"El área '{params['name']}' ya existe."
                )
                return
            fm = {
                "title": params["name"].replace("-", " ").title(),
                "type": "area-index",
                "description": params["description"],
                "tags": ["system", params["name"]],
                "source": "system",
                "area": params["name"],
            }
            body = (
                f"# {fm['title']}\n\n"
                f"## Descripción\n{params['description']}\n"
            )
            await create_note(fm, body, vault_path)
            await query.edit_message_text(f"Área '{params['name']}' creada.")

        elif operation == "create_section":
            section_dir = vault_path / "01-Projects" / params["project"] / params["name"]
            section_dir.mkdir(parents=True, exist_ok=True)
            await query.edit_message_text(
                f"Sección '{params['name']}' creada en proyecto '{params['project']}'."
            )

        else:
            await query.edit_message_text(
                f"Operación '{operation}' disponible en próxima versión."
            )

    except Exception as e:
        logger.error("Error en operación %s: %s", operation, e)
        await query.edit_message_text(f"Error: {e}")


def _pop_pending_content(context: ContextTypes.DEFAULT_TYPE) -> tuple[str | None, dict]:
    """Extrae texto pendiente y contexto de captura (media_type, preserve_body, resource_file).

    Soporta texto libre (pending_raw_content solo) y audio/otros medios
    que además guardan pending_capture_ctx.
    """
    text = context.user_data.pop("pending_raw_content", None)
    ctx = context.user_data.pop("pending_capture_ctx", {})
    return text, ctx


async def _cb_intent_save(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """El usuario eligió guardar como nota — clasifica con LLM y muestra preview."""
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
    )


async def _cb_intent_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """El usuario eligió guardar como tarea — el LLM infiere título/tags/destino, type=task fijo."""
    from adso.handlers.capture import _classify_and_preview

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
        forced_type="task",
        user_context="El usuario clasificó este contenido como tarea. Inferir prioridad, fecha límite y proyecto/área con ese foco.",
    )


async def _cb_intent_note(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """El usuario eligió guardar como nota — el LLM infiere tipo (reference/idea/draft) y resto."""
    from adso.handlers.capture import _classify_and_preview

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
