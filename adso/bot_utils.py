"""Helpers y utilidades compartidas del bot.

Funciones puras y getters async sin lógica de negocio de Telegram.
"""

from __future__ import annotations

import re
from pathlib import Path

from telegram.ext import ContextTypes

from adso.constants import MANAGE_KEYWORDS
from adso.vault_search import find_by_property, get_all_tags
from adso.vault_writer import read_note


def _has_pending_keyboard(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True si hay una acción con teclado inline pendiente de resolución.

    Cubre los estados que muestran un teclado al usuario y esperan que
    presione un botón antes de procesar nuevo contenido.
    No bloquea estados que explícitamente esperan texto (awaiting_correction,
    pending_description, manage_missing_fields).
    """
    ud = context.user_data
    if ud.get("pending_note"):
        return True
    pt = ud.get("pending_transcript", {})
    if pt and not pt.get("awaiting_correction"):
        return True
    if ud.get("pending_extraction"):
        return True
    return False


def _has_destination(fm: dict) -> bool:
    """Determina si el frontmatter tiene un destino claro."""
    if fm.get("type") in ("draft", "task"):
        return True  # draft va a inbox, task va a su área
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
        rf'(?:crear?|nuev[ao]?|agrega[r]?|add)\s+{keyword}\s+["\u201c]([^"\u201d]+)["\u201d]',
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    # Sin comillas: todo lo que viene después de la keyword
    m = re.search(
        rf'(?:crear?|nuev[ao]?|agrega[r]?|add)\s+{keyword}\s+(.+)',
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return ""


def _detect_manage_keywords(text: str) -> list[str]:
    """Detecta intenciones de gestión en el texto por keywords.

    Args:
        text: Texto del usuario.

    Returns:
        Lista de intenciones detectadas: 'project', 'area', 'archive', 'delete', 'rename'.
    """
    lower = text.lower()
    return [
        intent for intent, kws in MANAGE_KEYWORDS.items()
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in kws)
    ]


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
