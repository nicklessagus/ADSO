"""Builders de teclados inline y preview para Telegram.

Módulo de UI puro: sin lógica de negocio, sin escritura al vault.
"""

from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from adso.constants import (
    CB_ARXIV_CREATE_ANYWAY,
    CB_BACK,
    CB_CANCEL,
    CB_CHOOSE_AREA,
    CB_CHOOSE_PROJECT,
    CB_CONFIRM,
    CB_CORRECT,
    CB_DESCRIBE,
    CB_DEST_AREA_PREFIX,
    CB_DEST_INBOX,
    CB_DEST_PROJECT_PREFIX,
    CB_DISAMBIG_CAPTURE,
    CB_DISAMBIG_QUERY,
    CB_EXTRACTION_CANCEL,
    CB_EXTRACTION_CORRECT,
    CB_EXTRACTION_OK,
    CB_INTENT_CREATE_AREA,
    CB_INTENT_CREATE_PROJECT,
    CB_INTENT_NOTE,
    CB_INTENT_TASK,
    CB_MANAGE_CANCEL,
    CB_MANAGE_CONFIRM,
    CB_OCR,
    CB_READ_STATUS_READ,
    CB_READ_STATUS_UNREAD,
    CB_REPORT_HEALTH,
    CB_REPORT_IDEAS,
    CB_REPORT_READING,
    CB_REPORT_SCOPE,
    CB_NOTE_CORRECT,
    CB_TRANSCRIPT_CANCEL,
    CB_TRANSCRIPT_CORRECT,
    CB_TRANSCRIPT_OK,
    CB_VISION,
)
from adso.llm_client import extract_original_from_degraded
from adso.vault_search import find_by_property


def _esc(text: str) -> str:
    """Escapa caracteres HTML para Telegram."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_preview(
    frontmatter: dict,
    body: str,
    suggested_links: list[dict],
) -> str:
    """Construye texto de preview para mostrar al usuario.

    Regla de destino:
    - project → 01-Projects/{project}/{section}
    - area    → 02-Areas/{area}
    - sin destino (cualquier tipo) → 00-Inbox

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
        lines.append("<b>Destino:</b> 00-Inbox")

    if fm.get("status"):
        lines.append(f"<b>Status:</b> {fm['status']}")
    if fm.get("priority"):
        lines.append(f"<b>Prioridad:</b> {fm['priority']}")
    if fm.get("tags"):
        lines.append(f"<b>Tags:</b> {', '.join(fm['tags'])}")
    if fm.get("due_date"):
        lines.append(f"<b>Fecha límite:</b> {fm['due_date']}")

    if suggested_links:
        link_labels = [lnk.get("title") or lnk["note_id"] for lnk in suggested_links]
        lines.append(f"\n<b>Links sugeridos:</b> {', '.join(_esc(lbl) for lbl in link_labels)}")

    clean_body = extract_original_from_degraded(body)
    snippet = clean_body[:200].strip()
    if len(clean_body) > 200:
        snippet += "..."
    lines.append(f"\n<code>{_esc(snippet)}</code>")

    return "\n".join(lines)


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
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
            InlineKeyboardButton("Corregir", callback_data=CB_NOTE_CORRECT),
            InlineKeyboardButton("Reubicar", callback_data=CB_CORRECT),
        ],
        [
            InlineKeyboardButton("Confirmar", callback_data=CB_CONFIRM),
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
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
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
            InlineKeyboardButton("Cancelar", callback_data=CB_MANAGE_CANCEL),
            InlineKeyboardButton("Confirmar", callback_data=CB_MANAGE_CONFIRM),
        ]
    ])


def build_transcript_keyboard() -> InlineKeyboardMarkup:
    """Teclado para confirmar/corregir/cancelar transcripción de audio."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_TRANSCRIPT_CANCEL),
            InlineKeyboardButton("Corregir", callback_data=CB_TRANSCRIPT_CORRECT),
            InlineKeyboardButton("Confirmar", callback_data=CB_TRANSCRIPT_OK),
        ]
    ])


def build_ocr_result_keyboard() -> InlineKeyboardMarkup:
    """Teclado post-OCR: confirmar, corregir, cambiar a Gemini Vision o cancelar.

    Fila 1: Cancelar (izq) · Corregir (der) — acciones sobre el estado actual.
    Fila 2: Gemini Vision (izq) · Confirmar (der) — método alternativo o aceptar.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_TRANSCRIPT_CANCEL),
            InlineKeyboardButton("Corregir", callback_data=CB_TRANSCRIPT_CORRECT),
        ],
        [
            InlineKeyboardButton("Gemini Vision", callback_data=CB_VISION),
            InlineKeyboardButton("Confirmar", callback_data=CB_TRANSCRIPT_OK),
        ],
    ])


def build_read_status_keyboard() -> InlineKeyboardMarkup:
    """Teclado para marcar si ya se leyó un PDF/link."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
            InlineKeyboardButton("Ya lo leí", callback_data=CB_READ_STATUS_READ),
            InlineKeyboardButton("Lo quiero leer", callback_data=CB_READ_STATUS_UNREAD),
        ]
    ])


def build_extraction_keyboard() -> InlineKeyboardMarkup:
    """Teclado para confirmar/corregir/cancelar texto extraído de un documento."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_EXTRACTION_CANCEL),
            InlineKeyboardButton("Corregir", callback_data=CB_EXTRACTION_CORRECT),
            InlineKeyboardButton("Confirmar", callback_data=CB_EXTRACTION_OK),
        ]
    ])


async def build_area_selector(vault_path: Path) -> InlineKeyboardMarkup:
    """Construye teclado con áreas existentes. Si no hay áreas, solo muestra Volver."""
    areas = await find_by_property("type", "area-index", vault_path)
    buttons = [
        InlineKeyboardButton(
            area.path.parent.name,
            callback_data=f"{CB_DEST_AREA_PREFIX}{area.path.parent.name}",
        )
        for area in areas
    ]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
        InlineKeyboardButton("← Volver", callback_data=CB_BACK),
    ])
    return InlineKeyboardMarkup(rows)


async def build_project_selector(vault_path: Path) -> InlineKeyboardMarkup:
    """Construye teclado con proyectos existentes. Si no hay proyectos, solo muestra Volver."""
    projects = await find_by_property("type", "project-index", vault_path)
    buttons = [
        InlineKeyboardButton(
            proj.path.parent.name,
            callback_data=f"{CB_DEST_PROJECT_PREFIX}{proj.path.parent.name}",
        )
        for proj in projects
    ]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
        InlineKeyboardButton("← Volver", callback_data=CB_BACK),
    ])
    return InlineKeyboardMarkup(rows)


def build_intent_keyboard(intents: list[str]) -> InlineKeyboardMarkup:
    """Teclado de intención cuando se detectan keywords de gestión."""
    manage_buttons = []
    if "project" in intents:
        manage_buttons.append(
            InlineKeyboardButton("Crear proyecto", callback_data=CB_INTENT_CREATE_PROJECT)
        )
    if "area" in intents:
        manage_buttons.append(
            InlineKeyboardButton("Crear área", callback_data=CB_INTENT_CREATE_AREA)
        )

    rows = [manage_buttons[i:i+2] for i in range(0, len(manage_buttons), 2)] if manage_buttons else []
    rows.append([
        InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
        InlineKeyboardButton("Tarea", callback_data=CB_INTENT_TASK),
        InlineKeyboardButton("Nota", callback_data=CB_INTENT_NOTE),
    ])
    return InlineKeyboardMarkup(rows)


def build_save_keyboard() -> InlineKeyboardMarkup:
    """Teclado de elección para texto/audio recibido: guardar o buscar.

    Fila 1: capturar (cancelar / tarea / nota). Fila 2: buscar ese texto en el
    vault (retrieval semántico, Fase 7) en vez de guardarlo.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
            InlineKeyboardButton("Tarea", callback_data=CB_INTENT_TASK),
            InlineKeyboardButton("Nota", callback_data=CB_INTENT_NOTE),
        ],
        [
            InlineKeyboardButton("🔎 Buscar en el vault", callback_data=CB_DISAMBIG_QUERY),
        ],
    ])


def build_fallback_pdf_keyboard() -> InlineKeyboardMarkup:
    """Teclado para PDF escaneado sin texto extraíble.

    OCR, Gemini Vision y Describir están implementados (Fase 4 completa).
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("OCR", callback_data=CB_OCR),
            InlineKeyboardButton("Gemini Vision", callback_data=CB_VISION),
        ],
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_EXTRACTION_CANCEL),
            InlineKeyboardButton("Describir", callback_data=CB_DESCRIBE),
        ],
    ])


def build_arxiv_duplicate_keyboard() -> InlineKeyboardMarkup:
    """Teclado para cuando el paper de arXiv ya existe en el vault."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
            InlineKeyboardButton("Crear igual", callback_data=CB_ARXIV_CREATE_ANYWAY),
        ]
    ])


def build_report_type_keyboard() -> InlineKeyboardMarkup:
    """Teclado de selección del tipo de reporte.

    Returns:
        InlineKeyboardMarkup con los 4 tipos de reporte disponibles.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Proyecto/Área/Inbox", callback_data=CB_REPORT_SCOPE),
            InlineKeyboardButton("Ideas", callback_data=CB_REPORT_IDEAS),
        ],
        [
            InlineKeyboardButton("Salud del vault", callback_data=CB_REPORT_HEALTH),
            InlineKeyboardButton("Cola de lectura", callback_data=CB_REPORT_READING),
        ],
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
        ],
    ])


def build_report_category_keyboard(
    show_p_cb: str,
    show_a_cb: str,
    extra_cb: str,
    extra_label: str,
) -> InlineKeyboardMarkup:
    """Paso intermedio: elige entre Proyectos, Áreas, o una opción extra (Inbox / Todo).

    Args:
        show_p_cb: callback_data para mostrar lista de proyectos.
        show_a_cb: callback_data para mostrar lista de áreas.
        extra_cb: callback_data para la opción extra (inbox o all).
        extra_label: Etiqueta de la opción extra.

    Returns:
        InlineKeyboardMarkup.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Proyectos", callback_data=show_p_cb),
            InlineKeyboardButton("Áreas", callback_data=show_a_cb),
        ],
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
            InlineKeyboardButton(extra_label, callback_data=extra_cb),
        ],
    ])


def build_report_items_keyboard(
    items: list[dict],
    is_project: bool,
    prefix: str,
    back_cb: str,
) -> InlineKeyboardMarkup:
    """Lista de proyectos o áreas para seleccionar el scope final de un reporte.

    Trunca nombres a 32 caracteres para respetar el límite de 64 bytes de callback_data.

    Args:
        items: Lista de dicts {name, description}.
        is_project: True = proyectos (prefijo p:), False = áreas (prefijo a:).
        prefix: Prefijo del callback_data final (ej: "rpt:s:").
        back_cb: callback_data del botón [← Volver].

    Returns:
        InlineKeyboardMarkup.
    """
    _MAX_NAME = 32
    item_type = "p" if is_project else "a"

    buttons = [
        InlineKeyboardButton(
            item["name"][:_MAX_NAME],
            callback_data=f"{prefix}{item_type}:{item['name'][:_MAX_NAME]}",
        )
        for item in items
    ]

    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
        InlineKeyboardButton("← Volver", callback_data=back_cb),
    ])

    return InlineKeyboardMarkup(rows)
