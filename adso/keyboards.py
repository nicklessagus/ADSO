"""Builders de teclados inline y preview para Telegram.

Módulo de UI puro: sin lógica de negocio, sin escritura al vault.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

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


def _esc(text: object) -> str:
    """Escapa caracteres HTML para Telegram.

    Acepta cualquier tipo y coacciona a `str` como defensa en profundidad: el
    frontmatter editado a mano y la metadata de ChromaDB pueden traer int/float
    (`title: 2024` lo parsea YAML como int). Sin la coacción, `int.replace`
    lanzaba `AttributeError` y el error handler global mataba la interacción
    entera (E1 de la auditoría 2026-08). El dueño del contrato sigue siendo
    quien construye el dato — esto solo evita que un descuido tumbe el render.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
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


def build_capture_keyboard() -> InlineKeyboardMarkup:
    """Construye inline keyboard para captura (notas y tareas).

    El teclado es idéntico para todos los tipos y con o sin destino: fila 1
    ``[Cancelar] [Corregir] [Reubicar]``, fila 2 ``[Confirmar]``.
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


_TOKEN_RE = re.compile(r"[0-9a-f]{10}")


def item_token(name: str) -> str:
    """Token corto, ASCII y estable para meter un nombre en `callback_data`.

    Telegram corta el `callback_data` en **64 bytes**, y los nombres de
    proyecto/área van sin truncar (`dest:area:{nombre}`) o truncados a 32
    *chars* (reportes). Ambas cosas rompían: un directorio de ~27 chars
    acentuados supera el límite de bytes → `BadRequest` al abrir el selector; y
    un nombre truncado producía un path inexistente → reporte vacío sin error.
    F3 y F4 de docs/audit-2026-07-31.md.

    Se usa un hash y no un índice para que el token sea estable entre
    reinicios: un teclado viejo sigue resolviendo después de reiniciar el bot.

    Args:
        name: Nombre del proyecto o área.

    Returns:
        10 chars hexadecimales.
    """
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]


async def resolve_item_token(
    token: str, vault_path: Path, is_project: bool
) -> Optional[str]:
    """Resuelve un token de `callback_data` al nombre real del proyecto/área.

    Args:
        token: Token emitido por `item_token`, o un nombre literal (los
            teclados emitidos antes de este cambio mandaban el nombre crudo).
        vault_path: Raíz del vault.
        is_project: True para buscar en proyectos, False para áreas.

    Returns:
        Nombre completo, o None si el token no corresponde a nada existente.
    """
    from adso.bot_utils import _get_existing_items

    projects, areas = await _get_existing_items(vault_path)
    items = projects if is_project else areas
    for item in items:
        if item_token(item["name"]) == token:
            return item["name"]
    # Compatibilidad: teclado viejo con el nombre literal en el callback_data.
    for item in items:
        if item["name"] == token:
            return item["name"]
    # Nada matcheó. Si tiene forma de token, el proyecto/área se borró entre que
    # se dibujó el teclado y se apretó el botón: devolver None para avisar. Si
    # no, es un nombre literal de un teclado viejo y se respeta tal cual — si no,
    # el hash terminaría usándose como nombre de carpeta.
    if _TOKEN_RE.fullmatch(token):
        return None
    return token


async def build_area_selector(vault_path: Path) -> InlineKeyboardMarkup:
    """Construye teclado con áreas existentes. Si no hay áreas, solo muestra Volver."""
    # `_get_existing_items` (subdirectorios) y no `find_by_property` por
    # `area-index`: CLAUDE.md garantiza que toda área con notas aparece en los
    # teclados aunque no tenga `_index.md`. Los reportes ya lo cumplían; estos
    # selectores no, así que un área sin índice era invisible al reubicar.
    # F5 de docs/audit-2026-07-31.md.
    from adso.bot_utils import _get_existing_items

    _, areas = await _get_existing_items(vault_path)
    buttons = [
        InlineKeyboardButton(
            area["name"],
            callback_data=f"{CB_DEST_AREA_PREFIX}{item_token(area['name'])}",
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
    # Ver F5 en `build_area_selector`.
    from adso.bot_utils import _get_existing_items

    projects, _ = await _get_existing_items(vault_path)
    buttons = [
        InlineKeyboardButton(
            proj["name"],
            callback_data=f"{CB_DEST_PROJECT_PREFIX}{item_token(proj['name'])}",
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


def build_duplicate_keyboard(
    create_callback: str = CB_ARXIV_CREATE_ANYWAY,
) -> InlineKeyboardMarkup:
    """Teclado para cuando el contenido recibido ya existe en el vault.

    Lo comparten el duplicado de arXiv (detectado por `source_url`/`doi`) y el
    de archivo subido (detectado por hash del contenido, issue #53): mismo par
    [Cancelar]/[Crear igual], y solo cambia el callback del "Crear igual"
    porque el estado pendiente que hay que retomar es distinto en cada flujo.

    Args:
        create_callback: callback_data del botón "Crear igual".
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
            InlineKeyboardButton("Crear igual", callback_data=create_callback),
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
    # La etiqueta se trunca (es cosmética); el `callback_data` lleva el token,
    # nunca el nombre truncado — antes el truncado a 32 chars viajaba en el
    # callback y `scope_report` armaba un path inexistente. F3 de
    # docs/audit-2026-07-31.md.
    _MAX_LABEL = 32
    item_type = "p" if is_project else "a"

    buttons = [
        InlineKeyboardButton(
            item["name"][:_MAX_LABEL],
            callback_data=f"{prefix}{item_type}:{item_token(item['name'])}",
        )
        for item in items
    ]

    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
        InlineKeyboardButton("← Volver", callback_data=back_cb),
    ])

    return InlineKeyboardMarkup(rows)
