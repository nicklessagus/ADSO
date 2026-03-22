"""Orquestador principal del bot de Telegram.

Maneja mensajes de texto, inline keyboards, flujo de confirmación,
desambiguación y gestión de proyectos/áreas.
"""

from __future__ import annotations

import asyncio
import logging
import re
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
    save_resource,
    seed_vault,
)
from adso.embeddings import EmbeddingsClient
from adso.vault_search import find_by_property, get_note_index, get_all_tags
from adso.transcriber import transcribe_audio
from adso.document_extractor import (
    extract_pdf, extract_text_file, is_text_file, is_pdf,
    detect_paper, build_classify_content, extract_paper_sections,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de callback data
# ---------------------------------------------------------------------------

CB_CONFIRM = "confirm"
CB_CANCEL = "cancel"
CB_CORRECT = "correct"
CB_DEST_INBOX = "dest:inbox"
CB_DISAMBIG_CAPTURE = "disambig:capture"
CB_DISAMBIG_QUERY = "disambig:query"
CB_MANAGE_CONFIRM = "manage:confirm"
CB_MANAGE_CANCEL = "manage:cancel"
CB_INTENT_SAVE = "intent:save"
CB_INTENT_CREATE_PROJECT = "intent:project"
CB_INTENT_CREATE_AREA = "intent:area"

# Audio / documento
CB_TRANSCRIPT_OK = "transcript:ok"
CB_TRANSCRIPT_CANCEL = "transcript:cancel"
CB_READ_STATUS_READ = "read:read"
CB_READ_STATUS_UNREAD = "read:unread"
CB_EXTRACTION_OK = "extraction:ok"
CB_EXTRACTION_CANCEL = "extraction:cancel"
CB_DESCRIBE = "describe"

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


def build_transcript_keyboard() -> InlineKeyboardMarkup:
    """Teclado para confirmar/cancelar transcripción de audio."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirmar transcripción", callback_data=CB_TRANSCRIPT_OK),
            InlineKeyboardButton("Cancelar", callback_data=CB_TRANSCRIPT_CANCEL),
        ]
    ])


def build_read_status_keyboard() -> InlineKeyboardMarkup:
    """Teclado para marcar si ya se leyó un PDF/link."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Ya lo leí", callback_data=CB_READ_STATUS_READ),
            InlineKeyboardButton("Lo quiero leer", callback_data=CB_READ_STATUS_UNREAD),
        ]
    ])


def build_extraction_keyboard() -> InlineKeyboardMarkup:
    """Teclado para confirmar texto extraído de un documento."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirmar", callback_data=CB_EXTRACTION_OK),
            InlineKeyboardButton("Cancelar", callback_data=CB_EXTRACTION_CANCEL),
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


def _extract_name_from_command(text: str, operation: str) -> str:
    """Extrae el nombre de proyecto/área de un comando de creación.

    Maneja patrones como:
      - crear proyecto "Introducción a la ciencia de datos"
      - nuevo proyecto Tesis
      - crear área investigacion

    Args:
        text: Texto original del usuario.
        operation: 'create_project' o 'create_area'.

    Returns:
        Nombre extraído, o string vacío si no se pudo parsear.
    """
    keyword = r"proyecto" if operation == "create_project" else r"[aá]rea"
    # Con comillas simples o dobles
    m = re.search(
        rf'(?:crear?|nuevo?|agrega[r]?|add)\s+{keyword}\s+["\u201c]([^"\u201d]+)["\u201d]',
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    # Sin comillas: todo lo que viene después de la keyword
    m = re.search(
        rf'(?:crear?|nuevo?|agrega[r]?|add)\s+{keyword}\s+(.+)',
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return ""


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


async def _get_existing_tags(vault_path: Path, limit: int = 100) -> list[str]:
    """Retorna los tags confirmados del vault (sin Inbox), ordenados por frecuencia.

    Excluye 00-Inbox para que solo se propaguen tags de notas ya confirmadas por
    el usuario. Limita a `limit` tags para no inflar el system prompt.
    """
    exclude = ["05-Archive", ".obsidian", ".trash", "00-Inbox"]
    tag_counts = await get_all_tags(vault_path, exclude_dirs=exclude)
    return list(tag_counts.keys())[:limit]


# ---------------------------------------------------------------------------
# Detección local de intención (sin LLM)
# ---------------------------------------------------------------------------

_MANAGE_KEYWORDS: dict[str, set[str]] = {
    "project": {"proyecto", "project"},
    "area":    {"área", "area"},
    "archive": {"archivar", "archive"},
    "delete":  {"borrar", "eliminar", "delete"},
    "rename":  {"renombrar", "rename"},
}


def _detect_manage_keywords(text: str) -> list[str]:
    """Detecta intenciones de gestión en el texto por keywords.

    Args:
        text: Texto del usuario.

    Returns:
        Lista de intenciones detectadas: 'project', 'area', 'archive', 'delete', 'rename'.
    """
    lower = text.lower()
    return [intent for intent, kws in _MANAGE_KEYWORDS.items() if any(kw in lower for kw in kws)]


def build_intent_keyboard(intents: list[str]) -> InlineKeyboardMarkup:
    """Teclado de intención cuando se detectan keywords de gestión."""
    manage_buttons = []
    if "project" in intents:
        manage_buttons.append(InlineKeyboardButton("Crear proyecto", callback_data=CB_INTENT_CREATE_PROJECT))
    if "area" in intents:
        manage_buttons.append(InlineKeyboardButton("Crear área", callback_data=CB_INTENT_CREATE_AREA))

    rows = [manage_buttons[i:i+2] for i in range(0, len(manage_buttons), 2)] if manage_buttons else []
    rows.append([
        InlineKeyboardButton("Guardar como nota", callback_data=CB_INTENT_SAVE),
        InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def build_save_keyboard() -> InlineKeyboardMarkup:
    """Teclado mínimo: guardar como nota o cancelar."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Guardar como nota", callback_data=CB_INTENT_SAVE),
        InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
    ]])


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

    # Si hay transcripción pendiente, tratar como corrección del transcript
    if context.user_data.get("pending_transcript"):
        pt = context.user_data["pending_transcript"]
        pt["text"] = text
        await update.message.reply_text(
            f"<b>Transcripción corregida.</b>\n\n<i>{_esc(text[:500])}</i>",
            reply_markup=build_transcript_keyboard(),
            parse_mode="HTML",
        )
        return

    # Si hay extracción pendiente, tratar como corrección del texto extraído
    if context.user_data.get("pending_extraction"):
        pe = context.user_data["pending_extraction"]
        pe["text"] = text
        pe.pop("classify_content", None)  # usar el texto corregido directamente
        await update.message.reply_text(
            f"<b>Texto corregido.</b>\n\n<i>{_esc(text[:500])}</i>",
            reply_markup=build_extraction_keyboard(),
            parse_mode="HTML",
        )
        return

    # Si se espera descripción de un archivo binario
    if context.user_data.get("pending_description"):
        pd = context.user_data.pop("pending_description")
        resource_info = {
            "temp_path": pd["temp_path"],
            "filename": pd["original_filename"],
        }
        extra_fm = {}
        if pd.get("read_status"):
            extra_fm["read_status"] = pd["read_status"]
        await _classify_and_preview(
            update, context, text,
            media_type=pd["media_type"],
            resource_file=resource_info,
            extra_fm=extra_fm or None,
        )
        return

    # Si hay campos faltantes de una operación manage, rellenarlos con este texto
    if context.user_data.get("manage_missing_fields") and context.user_data.get("pending_operation"):
        await _handle_manage_missing_fields(update, context, text)
        return

    # Si hay un preview pendiente, tratar como corrección por texto libre
    pending = context.user_data.get("pending_note")
    if pending:
        await _handle_text_correction(update, context, text, pending)
        return

    # Guardar texto para procesarlo después (según la elección del usuario)
    context.user_data["pending_raw_content"] = text

    # Check de inyección antes de mostrar opciones
    if check_injection_risk(text):
        logger.warning("Patrón de inyección detectado en mensaje")
        await update.message.reply_text(
            "Detecté un patrón sospechoso en el contenido. "
            "¿Querés procesarlo de todas formas?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Sí, procesar", callback_data=CB_INTENT_SAVE),
                InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
            ]]),
        )
        return

    # Detección local de intención — sin LLM
    intents = _detect_manage_keywords(text)
    if intents:
        await update.message.reply_text(
            "¿Qué querés hacer?",
            reply_markup=build_intent_keyboard(intents),
        )
    else:
        await update.message.reply_text(
            "¿Guardás esto como nota?",
            reply_markup=build_save_keyboard(),
        )


@authorized
async def handle_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler para mensajes de audio y voz."""
    settings: Settings = context.bot_data["settings"]
    msg = update.message

    # Obtener archivo de audio (voice o audio)
    audio_file = msg.voice or msg.audio
    if not audio_file:
        await msg.reply_text("No se pudo procesar el audio.")
        return

    # Verificar tamaño
    max_bytes = settings.documents.max_size_mb * 1024 * 1024
    if audio_file.file_size and audio_file.file_size > max_bytes:
        await msg.reply_text(
            f"Audio demasiado grande (máx {settings.documents.max_size_mb}MB)."
        )
        return

    await msg.reply_text("Transcribiendo audio...")

    try:
        # Descargar a archivo temporal
        import tempfile
        tg_file = await audio_file.get_file()
        suffix = ".ogg" if msg.voice else Path(tg_file.file_path or "audio.ogg").suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        await tg_file.download_to_drive(str(tmp_path))

        # Transcribir
        text = await transcribe_audio(tmp_path, model=settings.whisper.model)

        if not text.strip():
            await msg.reply_text("No se pudo extraer texto del audio.")
            tmp_path.unlink(missing_ok=True)
            return

        # Guardar estado para confirmación
        context.user_data["pending_transcript"] = {
            "text": text,
            "temp_path": str(tmp_path),
            "media_type": "audio",
        }

        # Mostrar transcripción para confirmación
        snippet = text[:500]
        if len(text) > 500:
            snippet += "..."
        await msg.reply_text(
            f"<b>Transcripción:</b>\n\n<i>{_esc(snippet)}</i>\n\n"
            "Si es correcto, confirmá. Si no, mandá el texto corregido.",
            reply_markup=build_transcript_keyboard(),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error("Error transcribiendo audio: %s", e)
        await msg.reply_text(f"Error al transcribir: {e}")
        # Cleanup
        if "tmp_path" in dir():
            tmp_path.unlink(missing_ok=True)


@authorized
async def handle_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler para documentos (PDF, texto, binarios)."""
    settings: Settings = context.bot_data["settings"]
    msg = update.message
    doc = msg.document

    if not doc:
        await msg.reply_text("No se pudo procesar el documento.")
        return

    filename = doc.file_name or "documento"

    # Verificar tamaño
    max_bytes = settings.documents.max_size_mb * 1024 * 1024
    if doc.file_size and doc.file_size > max_bytes:
        await msg.reply_text(
            f"Archivo demasiado grande (máx {settings.documents.max_size_mb}MB)."
        )
        return

    # Descargar a archivo temporal
    import tempfile
    tg_file = await doc.get_file()
    suffix = Path(filename).suffix or ""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    await tg_file.download_to_drive(str(tmp_path))

    if is_pdf(filename):
        # PDF: preguntar read_status primero
        context.user_data["pending_read_status"] = {
            "temp_path": str(tmp_path),
            "original_filename": filename,
            "media_type": "document",
        }
        await msg.reply_text(
            f"PDF recibido: <b>{_esc(filename)}</b>",
            reply_markup=build_read_status_keyboard(),
            parse_mode="HTML",
        )

    elif is_text_file(filename):
        # Texto plano: leer directamente y clasificar
        try:
            text = await extract_text_file(tmp_path, max_chars=50000)
            if not text.strip():
                await msg.reply_text("El archivo está vacío.")
                tmp_path.unlink(missing_ok=True)
                return

            context.user_data["pending_extraction"] = {
                "text": text,
                "temp_path": str(tmp_path),
                "original_filename": filename,
                "media_type": "document",
                "metadata": {},
            }

            snippet = text[:500]
            if len(text) > 500:
                snippet += "..."
            await msg.reply_text(
                f"<b>Contenido de {_esc(filename)}:</b>\n\n"
                f"<i>{_esc(snippet)}</i>\n\n"
                "Confirmá para clasificar o mandá texto corregido.",
                reply_markup=build_extraction_keyboard(),
                parse_mode="HTML",
            )

        except Exception as e:
            logger.error("Error leyendo archivo de texto: %s", e)
            await msg.reply_text(f"Error leyendo archivo: {e}")
            tmp_path.unlink(missing_ok=True)

    else:
        # Binario / formato no reconocido: pedir descripción
        context.user_data["pending_description"] = {
            "temp_path": str(tmp_path),
            "original_filename": filename,
            "media_type": "document",
        }
        await msg.reply_text(
            f"Archivo recibido: <b>{_esc(filename)}</b>\n\n"
            "No puedo leer este formato. Describí el contenido para clasificarlo, "
            "o cancelá.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Cancelar", callback_data=CB_EXTRACTION_CANCEL)]
            ]),
            parse_mode="HTML",
        )


async def _process_pdf_after_read_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    read_status: str,
) -> None:
    """Procesa un PDF después de que el usuario seleccionó read_status."""
    pending = context.user_data.pop("pending_read_status", None)
    if not pending:
        return

    query = update.callback_query
    tmp_path = Path(pending["temp_path"])
    filename = pending["original_filename"]

    await query.edit_message_text("Extrayendo texto del PDF...")

    try:
        text, pdf_meta = await extract_pdf(tmp_path)

        if not text.strip():
            # PDF sin texto extraíble — pedir descripción
            context.user_data["pending_description"] = {
                "temp_path": str(tmp_path),
                "original_filename": filename,
                "media_type": "document",
                "read_status": read_status,
                "pdf_metadata": pdf_meta,
            }
            await query.edit_message_text(
                "No pude extraer texto del PDF (puede ser escaneado).\n"
                "Describí el contenido para clasificarlo.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Cancelar", callback_data=CB_EXTRACTION_CANCEL)]
                ]),
            )
            return

        is_paper = detect_paper(text, pdf_meta)
        classify_content = build_classify_content(text, pdf_meta, is_paper)

        # Para papers: capturar título exacto del PDF antes de que el LLM lo reescriba
        paper_title: Optional[str] = None
        if is_paper:
            sections = extract_paper_sections(text, pdf_meta)
            paper_title = sections["title"] or pdf_meta.get("title") or None

        context.user_data["pending_extraction"] = {
            "text": text,
            "classify_content": classify_content,
            "is_paper": is_paper,
            "paper_title": paper_title,
            "temp_path": str(tmp_path),
            "original_filename": filename,
            "media_type": "document",
            "read_status": read_status,
            "metadata": pdf_meta,
        }

        # Armar preview según tipo de documento
        if is_paper:
            preview_parts = []
            title = paper_title or ""
            if title:
                preview_parts.append(f"<b>{_esc(title)}</b>")
            if sections["abstract"]:
                abstract_snippet = sections["abstract"][:400]
                if len(sections["abstract"]) > 400:
                    abstract_snippet += "..."
                preview_parts.append(f"\n<i>{_esc(abstract_snippet)}</i>")
            preview_text = "\n".join(preview_parts) or "<i>(sin secciones detectadas)</i>"
        else:
            snippet = text[:500]
            if len(text) > 500:
                snippet += "..."
            pages = pdf_meta.get("pages", "?")
            preview_text = f"Páginas: {pages}\n\n<i>{_esc(snippet)}</i>"

        await query.edit_message_text(
            f"<b>PDF extraído:</b>\n\n{preview_text}\n\n"
            "Confirmá para clasificar o mandá texto corregido.",
            reply_markup=build_extraction_keyboard(),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error("Error extrayendo PDF: %s", e)
        await query.edit_message_text(f"Error extrayendo PDF: {e}")
        tmp_path.unlink(missing_ok=True)


async def _classify_and_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    media_type: str,
    resource_file: Optional[dict] = None,
    extra_fm: Optional[dict] = None,
) -> None:
    """Clasifica texto extraído y muestra preview.

    Flujo compartido por audio, PDF y documentos de texto.

    Args:
        update: Telegram update.
        context: Bot context.
        text: Texto a clasificar.
        media_type: 'audio' o 'document'.
        resource_file: Info del archivo para guardar en Resources {temp_path, filename}.
        extra_fm: Campos adicionales para el frontmatter (read_status, etc.).
    """
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    projects, areas = await _get_existing_items(vault_path)
    existing_tags = await _get_existing_tags(vault_path)

    async def on_retry(attempt: int, max_attempts: int) -> None:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                f"Servicio caído, reintento {attempt}/{max_attempts}..."
            )

    result = await classify(
        content=text,
        media_type=media_type,
        existing_projects=projects,
        existing_areas=areas,
        existing_tags=existing_tags,
        disambiguation_threshold=settings.llm.disambiguation_threshold,
        on_retry=on_retry,
    )

    mode = result.get("mode", "")

    # Guardar info de recurso para el confirm
    if resource_file:
        result["_resource_file"] = resource_file

    # Inyectar campos extra al frontmatter
    if extra_fm:
        payload = result.get("payload", {})
        fm = payload.get("frontmatter", {})
        fm.update(extra_fm)

    if mode == "degraded":
        # Modo degradado: mostrar preview de nota inbox para que el usuario confirme.
        # No se guarda nada sin confirmación explícita.
        payload = result["payload"]
        fm = payload["frontmatter"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        fm["date_created"] = now
        fm["date_modified"] = now
        fm["source"] = "telegram"
        fm["media_type"] = media_type
        if extra_fm:
            fm.update(extra_fm)

        context.user_data["pending_note"] = result
        result["payload"]["suggested_links"] = []

        preview = build_preview(fm, payload.get("body", text), [])
        keyboard = build_capture_keyboard(fm, False)

        reply_fn = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text
        await reply_fn(
            "⚠️ No pude clasificar bien — guardado en Inbox como borrador. "
            "Podés confirmar, corregir o cancelar.\n\n" + preview,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    # Captura normal
    payload = result["payload"]
    fm = payload["frontmatter"]
    suggested_links: list[str] = []

    # Para texto libre el body es siempre el texto original del usuario,
    # no la reformulación del LLM. Para PDFs/audio el LLM genera el body.
    if media_type == "text":
        body = text
        payload["body"] = text
    else:
        body = payload.get("body", "")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    fm["date_created"] = now
    fm["date_modified"] = now
    fm["source"] = "telegram"
    fm["media_type"] = media_type

    # Buscar links similares
    embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")
    if embeddings and body:
        try:
            similar = await embeddings.query_similar(
                query_text=body,
                n_results=settings.links.max_suggestions,
                threshold=settings.links.similarity_threshold,
            )
            if similar:
                suggested_links = [s.note_id for s in similar]
        except Exception as e:
            logger.warning("Error buscando links similares: %s", e)

    context.user_data["pending_note"] = result
    result["payload"]["suggested_links"] = suggested_links

    has_dest = _has_destination(fm)
    preview = build_preview(fm, body, suggested_links)
    keyboard = build_capture_keyboard(fm, has_dest)

    reply_fn = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text
    await reply_fn(
        preview,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def _handle_capture(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: dict,
) -> None:
    """Procesa modo capture: muestra preview + teclado."""
    payload = result["payload"]
    fm = payload["frontmatter"]
    body = payload.get("body", "")
    suggested_links: list[str] = []

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

    # Intentar parsear "nombre — descripción" o "nombre: descripción"
    if " — " in text:
        parts = text.split(" — ", 1)
        name, description = parts[0].strip(), parts[1].strip()
    elif ": " in text and not params.get("name"):
        parts = text.split(": ", 1)
        name, description = parts[0].strip(), parts[1].strip()
    else:
        name = text.strip()
        description = ""

    # Siempre actualizar — permite corrección tanto de campos vacíos como de valores inferidos
    if name:
        params["name"] = name
    if description:
        params["description"] = description
    elif not params.get("description"):
        params["description"] = name  # fallback: usar nombre como descripción

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

    # Detectar campos faltantes según operación
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
# Callbacks de intención (nuevo flujo de texto)
# ---------------------------------------------------------------------------


async def _cb_intent_save(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """El usuario eligió guardar como nota — clasifica con LLM y muestra preview."""
    text = context.user_data.pop("pending_raw_content", None)
    query = update.callback_query
    if not text:
        await query.edit_message_text("No hay contenido pendiente.")
        return
    await query.edit_message_text("Clasificando...")
    await _classify_and_preview(update, context, text, media_type="text")


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

    # Intentar extracción directa del texto antes de llamar al LLM
    name = _extract_name_from_command(text, operation)
    description = ""

    if not name:
        # Caso complejo (ej: "quiero un proyecto para mi tesis") → llamar al LLM
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

    # Último fallback
    if not name:
        name = text[:60].strip()

    # Construir pending_operation con los parámetros inferidos
    pending_op = {
        "mode": "manage",
        "payload": {
            "operation": operation,
            "params": {"name": name, "description": description},
        },
    }
    context.user_data["pending_operation"] = pending_op
    # Habilitar corrección por texto libre
    context.user_data["manage_missing_fields"] = ["correction"]

    desc_line = f"\n<b>Descripción:</b> {_esc(description)}" if description else ""
    await query.edit_message_text(
        f"<b>Crear {type_label}:</b> {_esc(name)}{desc_line}\n\n"
        f"Corregí por texto libre (<i>Nombre — Descripción</i>) o confirmá.",
        reply_markup=build_manage_keyboard(),
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
    elif data == CB_INTENT_SAVE:
        await _cb_intent_save(update, context)
    elif data == CB_INTENT_CREATE_PROJECT:
        await _cb_intent_create(update, context, "create_project")
    elif data == CB_INTENT_CREATE_AREA:
        await _cb_intent_create(update, context, "create_area")
    elif data == CB_DISAMBIG_CAPTURE:
        # Mantener para el flujo de inyección y desambiguación legacy
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
    # --- Audio / documento callbacks ---
    elif data == CB_TRANSCRIPT_OK:
        await _cb_transcript_ok(update, context)
    elif data == CB_TRANSCRIPT_CANCEL:
        _cleanup_pending(context, "pending_transcript")
        await query.edit_message_text("Transcripción cancelada.")
    elif data == CB_READ_STATUS_READ:
        await _process_pdf_after_read_status(update, context, "read")
    elif data == CB_READ_STATUS_UNREAD:
        await _process_pdf_after_read_status(update, context, "unread")
    elif data == CB_EXTRACTION_OK:
        await _cb_extraction_ok(update, context)
    elif data == CB_EXTRACTION_CANCEL:
        _cleanup_pending(context, "pending_extraction", "pending_description")
        await query.edit_message_text("Cancelado.")


async def _cb_confirm(query: Any, context: ContextTypes.DEFAULT_TYPE, vault_path: Path) -> None:
    """Confirma y escribe la nota al vault."""
    # Chequear si es una reclasificación de inbox (estado en bot_data, no user_data)
    msg_id = query.message.message_id
    reclassify_map: dict = context.bot_data.get("reclassify_pending", {})
    inbox_path_str: Optional[str] = None
    if msg_id in reclassify_map:
        entry = reclassify_map.pop(msg_id)
        pending = entry["result"]
        inbox_path_str = entry["inbox_path"]
    else:
        pending = context.user_data.pop("pending_note", None)

    if not pending:
        await query.edit_message_text("No hay nota pendiente.")
        return

    payload = pending["payload"]
    fm = payload["frontmatter"]
    body = payload.get("body", "")

    # Agregar wikilinks sugeridos al cuerpo
    suggested_links = payload.get("suggested_links", [])
    if suggested_links:
        wikilinks = " ".join(f"[[{link}]]" for link in suggested_links)
        body = body.rstrip() + f"\n\n## Ver también\n\n{wikilinks}"

    # Si hay archivo para guardar en Resources (PDF, texto, etc.)
    resource_file = pending.get("_resource_file")
    if resource_file:
        try:
            res_path = await save_resource(
                Path(resource_file["temp_path"]),
                resource_file["filename"],
                vault_path,
            )
            body += f"\n\n![[{res_path.name}]]"
            fm.setdefault("source_file", res_path.name)
            Path(resource_file["temp_path"]).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Error guardando recurso: %s", e)

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

    # Si es reclasificación de inbox, borrar la nota vieja
    if inbox_path_str:
        try:
            Path(inbox_path_str).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("No se pudo borrar nota de inbox: %s", e)

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
    _cleanup_pending(context)
    # Limpiar reclasificación pendiente si corresponde a este mensaje
    msg_id = query.message.message_id
    context.bot_data.get("reclassify_pending", {}).pop(msg_id, None)
    await query.edit_message_text("Cancelado.")


def _cleanup_pending(context: ContextTypes.DEFAULT_TYPE, *keys: str) -> None:
    """Limpia estados pendientes del user_data.

    Si no se pasan keys, limpia todos los estados conocidos.
    Si se pasan keys, limpia solo esos + cleanup de temp files.
    """
    if not keys:
        keys = (
            "pending_note", "pending_operation", "original_content",
            "pending_raw_content", "pending_transcript",
            "pending_extraction", "pending_description",
            "pending_read_status", "manage_missing_fields",
        )

    for key in keys:
        data = context.user_data.pop(key, None)
        # Limpiar archivo temporal si existe
        if isinstance(data, dict) and "temp_path" in data:
            Path(data["temp_path"]).unlink(missing_ok=True)


async def _cb_transcript_ok(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Confirma transcripción y clasifica el texto."""
    pt = context.user_data.pop("pending_transcript", None)
    if not pt:
        await update.callback_query.edit_message_text("No hay transcripción pendiente.")
        return

    text = pt["text"]
    temp_path = pt.get("temp_path")

    # Limpiar archivo temporal de audio
    if temp_path:
        Path(temp_path).unlink(missing_ok=True)

    await update.callback_query.edit_message_text("Clasificando...")

    await _classify_and_preview(
        update, context, text,
        media_type="audio",
    )


async def _cb_extraction_ok(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Confirma texto extraído y clasifica."""
    pe = context.user_data.pop("pending_extraction", None)
    if not pe:
        await update.callback_query.edit_message_text("No hay extracción pendiente.")
        return

    # classify_content es el extracto compacto para el LLM (papers: secciones clave;
    # genéricos: truncado). Si el usuario corrigió el texto manualmente, no hay
    # classify_content y se usa el texto corregido directamente.
    classify_text = pe.get("classify_content") or pe["text"]
    resource_info = None
    extra_fm = {}

    if pe.get("original_filename"):
        resource_info = {
            "temp_path": pe["temp_path"],
            "filename": pe["original_filename"],
        }
    else:
        # Audio o similar sin archivo para Resources
        if pe.get("temp_path"):
            Path(pe["temp_path"]).unlink(missing_ok=True)

    if pe.get("read_status"):
        extra_fm["read_status"] = pe["read_status"]
    # Título exacto del PDF: override del LLM para no perder el título original
    if pe.get("paper_title"):
        extra_fm["title"] = pe["paper_title"]

    await update.callback_query.edit_message_text("Clasificando...")

    await _classify_and_preview(
        update, context, classify_text,
        media_type=pe.get("media_type", "document"),
        resource_file=resource_info,
        extra_fm=extra_fm or None,
    )


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
    """Procesa captura desde un callback (ej: desambiguación).

    Si el resultado del LLM no es capture (ej: fue clasificado como query),
    construye una nota inbox mínima con el texto original para que el usuario
    pueda corregirla antes de confirmar.
    """
    payload = result["payload"]

    if "frontmatter" not in payload:
        # El LLM clasificó como query/manage pero el usuario eligió guardar.
        # Construir nota inbox con el texto original.
        original_text = context.user_data.get("original_content", "")
        payload = {
            "frontmatter": {
                "title": original_text[:80].strip(),
                "type": "inbox",
                "tags": [],
                "status": "pending-classification",
            },
            "body": original_text,
            "suggested_links": [],
        }
        result["payload"] = payload
        result["mode"] = "capture"

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
    """Job periódico: reintenta clasificar notas de Inbox pendientes.

    Si la reclasificación tiene éxito, manda preview al usuario para confirmación
    en vez de escribir directamente al vault.
    """
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path
    chat_id = settings.telegram_allowed_user_id

    inbox_notes = await find_by_property(
        "status", "pending-classification", vault_path,
        scope="00-Inbox",
    )

    if not inbox_notes:
        return

    projects, areas = await _get_existing_items(vault_path)
    existing_tags = await _get_existing_tags(vault_path)

    # Si ya hay una reclasificación pendiente de confirmación, no mandar más
    pending_map: dict = context.bot_data.get("reclassify_pending", {})
    if pending_map:
        logger.info("Reclasificación: hay %d previews pendientes de confirmación, esperando.", len(pending_map))
        return

    # Si el usuario está en medio de un flujo (nota pendiente, operación, etc.), no interrumpir
    _PENDING_FLOW_KEYS = {
        "pending_note", "pending_operation", "pending_raw_content",
        "pending_extraction", "pending_transcript", "pending_description",
        "manage_missing_fields",
    }
    user_data: dict = context.application.user_data.get(chat_id, {})
    if any(k in user_data for k in _PENDING_FLOW_KEYS):
        logger.info("Reclasificación: usuario tiene flujo pendiente, posponiendo.")
        return

    for ref in inbox_notes:
        try:
            note = await read_note(ref.path)

            # Ignorar notas sin body real (ej: mensajes de gestión guardados en degradado)
            if not note.body or not note.body.strip():
                logger.info("Reclasificación: saltando nota sin body: %s", ref.path)
                continue

            result = await classify(
                content=note.body,
                media_type=note.frontmatter.get("media_type", "text"),
                existing_projects=projects,
                existing_areas=areas,
                existing_tags=existing_tags,
                disambiguation_threshold=settings.llm.disambiguation_threshold,
            )

            if result.get("mode") == "degraded":
                continue

            # Ignorar si el LLM clasificó como manage (no es una nota capturable)
            if result.get("mode") != "capture":
                logger.info("Reclasificación: nota %s clasificada como '%s', omitiendo.", ref.path, result.get("mode"))
                continue

            payload = result["payload"]
            if "frontmatter" not in payload:
                logger.warning("Reclasificación: payload sin frontmatter en %s", ref.path)
                continue

            # Preservar metadatos originales
            fm = payload["frontmatter"]
            fm["date_created"] = note.frontmatter.get("date_created", "")
            fm["source"] = "telegram"
            fm["media_type"] = note.frontmatter.get("media_type", "text")
            body = payload.get("body", note.body)

            # Mandar preview al usuario — de a uno por cron
            preview_text = build_preview(fm, body, [])
            preview_text = "♻️ <b>Nota reclasificada del Inbox</b>\n\n" + preview_text
            has_dest = bool(fm.get("project") or fm.get("area"))
            keyboard = build_capture_keyboard(fm, has_dest)

            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=preview_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

            if "reclassify_pending" not in context.bot_data:
                context.bot_data["reclassify_pending"] = {}
            context.bot_data["reclassify_pending"][msg.message_id] = {
                "result": result,
                "inbox_path": str(ref.path),
            }
            logger.info("Reclasificación pendiente de confirmación: %s", ref.path)
            # Mandar solo una por ciclo de cron
            return

        except Exception as e:
            logger.warning("Error reclasificando %s: %s", ref.path, e)


# ---------------------------------------------------------------------------
async def _post_init(app: Application) -> None:
    """Inicialización async del vault, ejecutada por PTB antes de arrancar el polling."""
    settings: Settings = app.bot_data["settings"]
    await ensure_vault_structure(settings.vault_path)
    await seed_vault(settings.vault_path, settings.vault_seed)
    logger.info("ADSO iniciando — vault en %s", settings.vault_path)


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

    app = Application.builder().token(settings.telegram_token).post_init(_post_init).build()

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
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
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


def run_bot() -> None:
    """Punto de entrada: inicializa y arranca el bot. PTB gestiona el event loop."""
    settings = load_settings()
    app = create_application(settings)
    app.run_polling()
