"""Builders de teclados inline y preview para Telegram.

Módulo de UI puro: sin lógica de negocio, sin escritura al vault.
"""

from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from adso.constants import (
    CB_BACK,
    CB_CANCEL,
    CB_CHOOSE_AREA,
    CB_CHOOSE_PROJECT,
    CB_CONFIRM,
    CB_CORRECT,
    CB_DEST_AREA_PREFIX,
    CB_DEST_INBOX,
    CB_DEST_PROJECT_PREFIX,
    CB_DISAMBIG_CAPTURE,
    CB_DISAMBIG_QUERY,
    CB_EXTRACTION_CANCEL,
    CB_EXTRACTION_OK,
    CB_INTENT_CREATE_AREA,
    CB_INTENT_CREATE_PROJECT,
    CB_INTENT_SAVE,
    CB_MANAGE_CANCEL,
    CB_MANAGE_CONFIRM,
    CB_READ_STATUS_READ,
    CB_READ_STATUS_UNREAD,
    CB_TRANSCRIPT_CANCEL,
    CB_TRANSCRIPT_CORRECT,
    CB_TRANSCRIPT_OK,
)
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

    snippet = body[:200].strip()
    if len(body) > 200:
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
    if has_destination:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Confirmar", callback_data=CB_CONFIRM),
                InlineKeyboardButton("Reubicar", callback_data=CB_CORRECT),
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
    """Teclado para confirmar/corregir/cancelar transcripción de audio."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirmar", callback_data=CB_TRANSCRIPT_OK),
            InlineKeyboardButton("Corregir", callback_data=CB_TRANSCRIPT_CORRECT),
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
        InlineKeyboardButton("← Volver", callback_data=CB_BACK),
        InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
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
        InlineKeyboardButton("← Volver", callback_data=CB_BACK),
        InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
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
