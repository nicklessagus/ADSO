"""Orquestador principal del bot de Telegram.

Maneja mensajes de texto, inline keyboards, flujo de confirmación,
desambiguación y gestión de proyectos/áreas.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from adso.config import Settings, load_settings
from adso.llm_client import classify, check_injection_risk, LLMResponseError
from adso.security import authorized
from adso.vault_writer import (
    GitBackup,
    NoteData,
    create_note,
    ensure_vault_structure,
    read_note,
    seed_vault,
)
from adso.embeddings import EmbeddingsClient
from adso.vault_search import find_by_property, get_note_index

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de callback data
# ---------------------------------------------------------------------------

CB_CONFIRM = "confirm"
CB_CANCEL = "cancel"
CB_CORRECT = "correct"
CB_DEST_INBOX = "dest:inbox"
CB_DEST_RESOURCES = "dest:resources"
CB_DISAMBIG_CAPTURE = "disambig:capture"
CB_DISAMBIG_QUERY = "disambig:query"
CB_MANAGE_CONFIRM = "manage:confirm"
CB_MANAGE_CANCEL = "manage:cancel"

# Prefijos
CB_DEST_AREA_PREFIX = "dest:area:"
CB_DEST_PROJECT_PREFIX = "dest:project:"
CB_CHOOSE_AREA = "choose:area"
CB_CHOOSE_PROJECT = "choose:project"


# ---------------------------------------------------------------------------
# Preview y teclados
# ---------------------------------------------------------------------------


def build_preview(
    frontmatter: dict,
    body: str,
    suggested_links: list[str],
) -> str:
    """Construye texto de preview para mostrar al usuario.

    Args:
        frontmatter: Dict del frontmatter propuesto.
        body: Cuerpo de la nota.
        suggested_links: Wikilinks sugeridos.

    Returns:
        Texto formateado para Telegram (HTML).
    """
    lines = ["<b>Preview de nota</b>\n"]

    fm = frontmatter
    lines.append(f"<b>Título:</b> {_esc(fm.get('title', ''))}")
    lines.append(f"<b>Tipo:</b> {fm.get('type', '?')}")

    if fm.get("project"):
        dest = f"01-Projects/{fm['project']}"
        if fm.get("section"):
            dest += f"/{fm['section']}"
        lines.append(f"<b>Destino:</b> {dest}")
    elif fm.get("area"):
        lines.append(f"<b>Destino:</b> 02-Areas/{fm['area']}")
    else:
        lines.append("<b>Destino:</b> por definir")

    if fm.get("status"):
        lines.append(f"<b>Status:</b> {fm['status']}")
    if fm.get("priority"):
        lines.append(f"<b>Prioridad:</b> {fm['priority']}")
    if fm.get("tags"):
        lines.append(f"<b>Tags:</b> {', '.join(fm['tags'])}")
    if fm.get("due_date"):
        lines.append(f"<b>Fecha límite:</b> {fm['due_date']}")

    if suggested_links:
        lines.append(f"\n<b>Links sugeridos:</b> {', '.join(suggested_links)}")

    # Snippet del body (primeras 200 chars)
    snippet = body[:200].strip()
    if len(body) > 200:
        snippet += "..."
    lines.append(f"\n<i>{_esc(snippet)}</i>")

    return "\n".join(lines)


def _esc(text: str) -> str:
    """Escapa caracteres HTML para Telegram."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_capture_keyboard(
    frontmatter: dict,
    has_destination: bool,
) -> InlineKeyboardMarkup:
    """Construye inline keyboard para captura.

    Args:
        frontmatter: Dict del frontmatter propuesto.
        has_destination: Si True, el LLM propuso un destino claro.

    Returns:
        InlineKeyboardMarkup.
    """
    if has_destination:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Confirmar", callback_data=CB_CONFIRM),
                InlineKeyboardButton("Corregir", callback_data=CB_CORRECT),
                InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
            ]
        ])
    else:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Resources", callback_data=CB_DEST_RESOURCES),
                InlineKeyboardButton("Inbox", callback_data=CB_DEST_INBOX),
            ],
            [
                InlineKeyboardButton("Elegir área", callback_data=CB_CHOOSE_AREA),
                InlineKeyboardButton("Elegir proyecto", callback_data=CB_CHOOSE_PROJECT),
            ],
            [
                InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
            ],
        ])


def build_destination_keyboard() -> InlineKeyboardMarkup:
    """Teclado para corregir destino."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Resources", callback_data=CB_DEST_RESOURCES),
            InlineKeyboardButton("Inbox", callback_data=CB_DEST_INBOX),
        ],
        [
            InlineKeyboardButton("Elegir área", callback_data=CB_CHOOSE_AREA),
            InlineKeyboardButton("Elegir proyecto", callback_data=CB_CHOOSE_PROJECT),
        ],
    ])


def build_disambiguation_keyboard() -> InlineKeyboardMarkup:
    """Teclado para desambiguación de intención."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Guardar como nota", callback_data=CB_DISAMBIG_CAPTURE),
            InlineKeyboardButton("Buscar en vault", callback_data=CB_DISAMBIG_QUERY),
        ]
    ])


def build_manage_keyboard() -> InlineKeyboardMarkup:
    """Teclado de confirmación para operaciones de gestión."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirmar", callback_data=CB_MANAGE_CONFIRM),
            InlineKeyboardButton("Cancelar", callback_data=CB_MANAGE_CANCEL),
        ]
    ])


async def build_area_selector(vault_path: Path) -> InlineKeyboardMarkup:
    """Construye teclado con áreas existentes."""
    areas = await find_by_property("type", "area-index", vault_path)
    buttons = []
    for area in areas:
        name = area.path.parent.name
        buttons.append(
            InlineKeyboardButton(name, callback_data=f"{CB_DEST_AREA_PREFIX}{name}")
        )
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL)])
    return InlineKeyboardMarkup(rows)


async def build_project_selector(vault_path: Path) -> InlineKeyboardMarkup:
    """Construye teclado con proyectos existentes."""
    projects = await find_by_property("type", "project-index", vault_path)
    buttons = []
    for proj in projects:
        name = proj.path.parent.name
        buttons.append(
            InlineKeyboardButton(name, callback_data=f"{CB_DEST_PROJECT_PREFIX}{name}")
        )
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL)])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_destination(fm: dict) -> bool:
    """Determina si el frontmatter tiene un destino claro."""
    if fm.get("type") in ("inbox", "task", "idea"):
        return True  # inbox va a inbox, task/idea van a su área
    if fm.get("project") or fm.get("area"):
        return True
    return False


async def _get_existing_items(vault_path: Path) -> tuple[list[dict], list[dict]]:
    """Obtiene proyectos y áreas existentes para el system prompt del LLM."""
    projects = []
    areas = []

    proj_refs = await find_by_property("type", "project-index", vault_path)
    for ref in proj_refs:
        try:
            note = await read_note(ref.path)
            projects.append({
                "name": note.frontmatter.get("project", ref.path.parent.name),
                "description": note.frontmatter.get("description", ""),
            })
        except Exception:
            pass

    area_refs = await find_by_property("type", "area-index", vault_path)
    for ref in area_refs:
        try:
            note = await read_note(ref.path)
            areas.append({
                "name": note.frontmatter.get("area", ref.path.parent.name),
                "description": note.frontmatter.get("description", ""),
            })
        except Exception:
            pass

    return projects, areas


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@authorized
async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler principal de mensajes de texto."""
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path
    text = update.message.text

    # Si hay un preview pendiente, tratar como corrección por texto libre
    pending = context.user_data.get("pending_note")
    if pending:
        await _handle_text_correction(update, context, text, pending)
        return

    # Clasificar con LLM
    projects, areas = await _get_existing_items(vault_path)

    # Check de inyección
    if check_injection_risk(text):
        logger.warning("Patrón de inyección detectado en mensaje")
        await update.message.reply_text(
            "Detecté un patrón sospechoso en el contenido. "
            "¿Querés procesarlo de todas formas?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Sí, procesar", callback_data=CB_DISAMBIG_CAPTURE),
                    InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
                ]
            ]),
        )
        context.user_data["pending_raw_content"] = text
        return

    async def on_retry(attempt: int, max_attempts: int) -> None:
        await update.message.reply_text(
            f"Servicio caído, reintento {attempt}/{max_attempts}..."
        )

    result = await classify(
        content=text,
        media_type="text",
        existing_projects=projects,
        existing_areas=areas,
        disambiguation_threshold=settings.llm.disambiguation_threshold,
        on_retry=on_retry,
    )

    mode = result.get("mode", "")

    # Modo degradado
    if mode == "degraded":
        await _handle_degraded(update, context, text, result)
        return

    # Desambiguación
    if result.get("needs_disambiguation"):
        context.user_data["pending_note"] = result
        context.user_data["original_content"] = text
        await update.message.reply_text(
            "No estoy seguro si querés guardar esto o buscar en el vault.",
            reply_markup=build_disambiguation_keyboard(),
        )
        return

    if mode == "capture":
        await _handle_capture(update, context, result)
    elif mode == "manage":
        await _handle_manage(update, context, result)
    elif mode in ("query", "edit"):
        await update.message.reply_text(
            f"Modo {mode} disponible en próxima versión."
        )
    else:
        await update.message.reply_text("No pude interpretar el mensaje.")


async def _handle_capture(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: dict,
) -> None:
    """Procesa modo capture: muestra preview + teclado."""
    payload = result["payload"]
    fm = payload["frontmatter"]
    body = payload.get("body", "")
    suggested_links = payload.get("suggested_links", [])

    # Setear campos automáticos
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    fm["date_created"] = now
    fm["date_modified"] = now
    fm["source"] = "telegram"
    fm["media_type"] = "text"

    # Buscar links sugeridos via embeddings (si disponible)
    embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")
    if embeddings and body:
        try:
            settings: Settings = context.bot_data["settings"]
            similar = await embeddings.query_similar(
                query_text=body,
                n_results=settings.links.max_suggestions,
                threshold=settings.links.similarity_threshold,
            )
            if similar:
                suggested_links = [s.note_id for s in similar]
        except Exception as e:
            logger.warning("Error buscando links similares: %s", e)

    # Guardar en contexto del usuario
    context.user_data["pending_note"] = result
    # Guardar links sugeridos en el resultado para que se muestren en preview
    result["payload"]["suggested_links"] = suggested_links
    context.user_data["original_content"] = update.message.text

    has_dest = _has_destination(fm)
    preview = build_preview(fm, body, suggested_links)
    keyboard = build_capture_keyboard(fm, has_dest)

    await update.message.reply_text(
        preview,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def _handle_manage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: dict,
) -> None:
    """Procesa modo manage: muestra operación + confirmación."""
    payload = result["payload"]
    operation = payload.get("operation", "?")
    params = payload.get("params", {})

    context.user_data["pending_operation"] = result

    op_desc = {
        "create_project": f"Crear proyecto '{params.get('name', '?')}'\nDescripción: {params.get('description', '?')}",
        "create_area": f"Crear área '{params.get('name', '?')}'\nDescripción: {params.get('description', '?')}",
        "create_section": f"Crear sección '{params.get('name', '?')}' en proyecto '{params.get('project', '?')}'",
        "archive_project": f"Archivar proyecto '{params.get('name', '?')}'",
        "delete_project": f"Eliminar proyecto '{params.get('name', '?')}'",
        "delete_area": f"Eliminar área '{params.get('name', '?')}'",
    }

    desc = op_desc.get(operation, f"Operación: {operation}\nParams: {params}")

    await update.message.reply_text(
        f"<b>Gestión</b>\n\n{_esc(desc)}",
        reply_markup=build_manage_keyboard(),
        parse_mode="HTML",
    )


async def _handle_degraded(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    original_text: str,
    result: dict,
) -> None:
    """Modo degradado: guarda en Inbox con pending-classification."""
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    payload = result["payload"]
    fm = payload["frontmatter"]
    fm["source"] = "telegram"
    fm["media_type"] = "text"

    path = await create_note(fm, payload["body"], vault_path)

    # Git backup
    git_backup: Optional[GitBackup] = context.bot_data.get("git_backup")
    if git_backup:
        await git_backup.notify(fm.get("title", "Sin título"))

    await update.message.reply_text(
        "No pude clasificar — guardado en Inbox. "
        "Se reintenta automáticamente."
    )


async def _handle_text_correction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    pending: dict,
) -> None:
    """Interpreta texto libre como corrección del preview pendiente."""
    settings: Settings = context.bot_data["settings"]
    payload = pending["payload"]
    fm = payload["frontmatter"]

    text_lower = text.lower().strip()

    # Correcciones comunes
    if text_lower.startswith("titulo ") or text_lower.startswith("título "):
        new_title = text.split(" ", 1)[1].strip()
        fm["title"] = new_title
    elif text_lower.startswith("prioridad "):
        prio = text_lower.split(" ", 1)[1].strip()
        if prio in ("alta", "high"):
            fm["priority"] = "high"
        elif prio in ("media", "medium"):
            fm["priority"] = "medium"
        elif prio in ("baja", "low"):
            fm["priority"] = "low"
    elif text_lower.startswith("tag ") or text_lower.startswith("agregar tag "):
        tag = text_lower.split("tag ", 1)[1].strip().replace(" ", "-")
        if "tags" not in fm or fm["tags"] is None:
            fm["tags"] = []
        fm["tags"].append(tag)
    elif text_lower.startswith("tipo ") or text_lower.startswith("type "):
        new_type = text_lower.split(" ", 1)[1].strip()
        if new_type in ("note", "nota"):
            fm["type"] = "note"
        elif new_type in ("task", "tarea"):
            fm["type"] = "task"
        elif new_type in ("idea",):
            fm["type"] = "idea"
    else:
        # Usar el texto como nuevo título por default
        fm["title"] = text.strip()

    fm["date_modified"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    # Regenerar preview
    body = payload.get("body", "")
    suggested_links = payload.get("suggested_links", [])
    has_dest = _has_destination(fm)
    preview = build_preview(fm, body, suggested_links)
    keyboard = build_capture_keyboard(fm, has_dest)

    await update.message.reply_text(
        preview,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------


@authorized
async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    if data == CB_CONFIRM:
        await _cb_confirm(query, context, vault_path)
    elif data == CB_CANCEL:
        await _cb_cancel(query, context)
    elif data == CB_CORRECT:
        await _cb_correct(query, context, vault_path)
    elif data == CB_DEST_INBOX:
        await _cb_dest(query, context, dest_type="inbox")
    elif data == CB_DEST_RESOURCES:
        await _cb_dest(query, context, dest_type="resources")
    elif data.startswith(CB_DEST_AREA_PREFIX):
        area = data[len(CB_DEST_AREA_PREFIX):]
        await _cb_dest(query, context, dest_type="area", dest_name=area)
    elif data.startswith(CB_DEST_PROJECT_PREFIX):
        project = data[len(CB_DEST_PROJECT_PREFIX):]
        await _cb_dest(query, context, dest_type="project", dest_name=project)
    elif data == CB_CHOOSE_AREA:
        keyboard = await build_area_selector(vault_path)
        await query.edit_message_reply_markup(reply_markup=keyboard)
    elif data == CB_CHOOSE_PROJECT:
        keyboard = await build_project_selector(vault_path)
        await query.edit_message_reply_markup(reply_markup=keyboard)
    elif data == CB_DISAMBIG_CAPTURE:
        # Tratar como captura
        pending = context.user_data.get("pending_note")
        if pending:
            pending["needs_disambiguation"] = False
            await _handle_capture_from_callback(query, context, pending)
    elif data == CB_DISAMBIG_QUERY:
        await query.edit_message_text("Modo consulta disponible en próxima versión.")
        context.user_data.pop("pending_note", None)
    elif data == CB_MANAGE_CONFIRM:
        await _cb_manage_confirm(query, context, vault_path)
    elif data == CB_MANAGE_CANCEL:
        await query.edit_message_text("Operación cancelada.")
        context.user_data.pop("pending_operation", None)


async def _cb_confirm(query: Any, context: ContextTypes.DEFAULT_TYPE, vault_path: Path) -> None:
    """Confirma y escribe la nota al vault."""
    pending = context.user_data.pop("pending_note", None)
    if not pending:
        await query.edit_message_text("No hay nota pendiente.")
        return

    payload = pending["payload"]
    fm = payload["frontmatter"]
    body = payload.get("body", "")

    path = await create_note(fm, body, vault_path)

    # Git backup
    git_backup: Optional[GitBackup] = context.bot_data.get("git_backup")
    if git_backup:
        await git_backup.notify(fm.get("title", "Sin título"))

    # Fire-and-forget: indexar embedding
    embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")
    if embeddings and body:
        asyncio.create_task(
            _index_note_safe(embeddings, path, body, fm, vault_path)
        )

    rel_path = path.relative_to(vault_path)
    await query.edit_message_text(f"Nota guardada en {rel_path}")
    context.user_data.pop("original_content", None)


async def _index_note_safe(
    embeddings: EmbeddingsClient,
    note_path: Path,
    body: str,
    fm: dict,
    vault_path: Path,
) -> None:
    """Indexa embedding de forma segura (no propaga errores)."""
    try:
        note_id = note_path.stem
        rel_path = str(note_path.relative_to(vault_path))
        metadata = {
            "path": rel_path,
            "type": fm.get("type", ""),
            "status": fm.get("status", ""),
            "project": fm.get("project", ""),
            "area": fm.get("area", ""),
            "tags": fm.get("tags", []),
            "media_type": fm.get("media_type", ""),
            "title": fm.get("title", ""),
        }
        await embeddings.index_note(note_id, body, metadata)
    except Exception as e:
        logger.warning("Error indexando embedding para %s: %s", note_path, e)


async def _cb_cancel(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancela la operación pendiente."""
    context.user_data.pop("pending_note", None)
    context.user_data.pop("pending_operation", None)
    context.user_data.pop("original_content", None)
    context.user_data.pop("pending_raw_content", None)
    await query.edit_message_text("Cancelado.")


async def _cb_correct(query: Any, context: ContextTypes.DEFAULT_TYPE, vault_path: Path) -> None:
    """Muestra selector de destino."""
    keyboard = build_destination_keyboard()
    await query.edit_message_reply_markup(reply_markup=keyboard)


async def _cb_dest(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    dest_type: str,
    dest_name: Optional[str] = None,
) -> None:
    """Actualiza destino de la nota pendiente."""
    pending = context.user_data.get("pending_note")
    if not pending:
        await query.edit_message_text("No hay nota pendiente.")
        return

    fm = pending["payload"]["frontmatter"]

    if dest_type == "inbox":
        fm["type"] = "inbox"
        fm["project"] = None
        fm["section"] = None
        fm["area"] = None
        fm["status"] = "pending-classification"
    elif dest_type == "resources":
        fm["project"] = None
        fm["section"] = None
        fm["area"] = None
        # Note va a 03-Resources — manejado por routing especial
        fm["_dest_resources"] = True
    elif dest_type == "area":
        fm["project"] = None
        fm["section"] = None
        fm["area"] = dest_name
    elif dest_type == "project":
        fm["project"] = dest_name
        fm["section"] = None
        fm["area"] = None

    # Regenerar preview
    body = pending["payload"].get("body", "")
    suggested_links = pending["payload"].get("suggested_links", [])
    preview = build_preview(fm, body, suggested_links)

    await query.edit_message_text(
        preview + "\n\n¿Confirmar?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Confirmar", callback_data=CB_CONFIRM),
                InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
            ]
        ]),
        parse_mode="HTML",
    )


async def _handle_capture_from_callback(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    result: dict,
) -> None:
    """Procesa captura desde un callback (ej: desambiguación)."""
    payload = result["payload"]
    fm = payload["frontmatter"]
    body = payload.get("body", "")
    suggested_links = payload.get("suggested_links", [])

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    fm.setdefault("date_created", now)
    fm.setdefault("date_modified", now)
    fm.setdefault("source", "telegram")
    fm.setdefault("media_type", "text")

    has_dest = _has_destination(fm)
    preview = build_preview(fm, body, suggested_links)
    keyboard = build_capture_keyboard(fm, has_dest)

    await query.edit_message_text(
        preview,
        reply_markup=keyboard,
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
                "tags": [params["name"]],
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


# ---------------------------------------------------------------------------
# Comando /start
# ---------------------------------------------------------------------------


@authorized
async def handle_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /start."""
    await update.message.reply_text(
        "ADSO activo. Mandame texto y lo clasifico para tu vault."
    )


# ---------------------------------------------------------------------------
# Reclasificación de inbox
# ---------------------------------------------------------------------------


async def reclassify_inbox(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job periódico: reintenta clasificar notas de Inbox pendientes."""
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    inbox_notes = await find_by_property(
        "status", "pending-classification", vault_path,
        scope="00-Inbox",
    )

    if not inbox_notes:
        return

    projects, areas = await _get_existing_items(vault_path)

    for ref in inbox_notes:
        try:
            note = await read_note(ref.path)
            result = await classify(
                content=note.body,
                media_type=note.frontmatter.get("media_type", "text"),
                existing_projects=projects,
                existing_areas=areas,
                disambiguation_threshold=settings.llm.disambiguation_threshold,
            )

            if result.get("mode") != "degraded":
                # Reclasificación exitosa — crear nueva nota y borrar la vieja
                payload = result["payload"]
                fm = payload["frontmatter"]
                fm["date_created"] = note.frontmatter.get("date_created", "")
                fm["source"] = "telegram"
                fm["media_type"] = note.frontmatter.get("media_type", "text")

                new_path = await create_note(fm, payload.get("body", note.body), vault_path)
                ref.path.unlink()  # Borrar la nota vieja del inbox
                logger.info("Reclasificada: %s → %s", ref.path, new_path)

        except Exception as e:
            logger.warning("Error reclasificando %s: %s", ref.path, e)


# ---------------------------------------------------------------------------
# Creación de la aplicación
# ---------------------------------------------------------------------------


def create_application(settings: Optional[Settings] = None) -> Application:
    """Crea y configura la Application de python-telegram-bot.

    Args:
        settings: Settings cargados. Si None, los carga de config.yaml.

    Returns:
        Application configurada lista para run_polling().
    """
    if settings is None:
        settings = load_settings()

    app = Application.builder().token(settings.telegram_token).build()

    # Bot data compartida
    app.bot_data["settings"] = settings
    app.bot_data["git_backup"] = GitBackup(
        settings.vault_path, settings.backup.debounce_seconds
    )

    # Inicializar embeddings client
    app.bot_data["embeddings"] = EmbeddingsClient(
        chroma_data_dir=settings.chroma_data_dir,
        gemini_api_key=settings.gemini_api_key,
    )

    # Handlers
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Jobs periódicos
    if settings.llm.degraded_retry_minutes > 0:
        app.job_queue.run_repeating(
            reclassify_inbox,
            interval=settings.llm.degraded_retry_minutes * 60,
            first=60,  # Esperar 1 min después del startup
        )

    # Reindex nocturno de embeddings
    if settings.reindex.enabled:
        reindex_time = datetime.strptime(settings.reindex.time, "%H:%M").time()
        app.job_queue.run_daily(
            reindex_job,
            time=reindex_time,
        )

    return app


async def reindex_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job nocturno: reindexar vault completo en ChromaDB."""
    settings: Settings = context.bot_data["settings"]
    embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")

    if not embeddings:
        return

    logger.info("Reindex nocturno iniciando...")
    try:
        stats = await embeddings.reindex_vault(
            vault_path=settings.vault_path,
            exclude_dirs=settings.vault.exclude_dirs,
        )
        logger.info("Reindex completo: %s", stats)
    except Exception as e:
        logger.error("Error en reindex nocturno: %s", e)


async def run_bot() -> None:
    """Punto de entrada async: inicializa vault y arranca el bot."""
    settings = load_settings()

    # Inicializar vault
    await ensure_vault_structure(settings.vault_path)
    await seed_vault(settings.vault_path, settings.vault_seed)

    logger.info("ADSO iniciando — vault en %s", settings.vault_path)

    app = create_application(settings)
    await app.run_polling()
