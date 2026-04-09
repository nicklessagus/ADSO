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
    if ud.get("pending_raw_content"):
        return True
    pt = ud.get("pending_transcript", {})
    if pt and not pt.get("awaiting_correction"):
        return True
    pe = ud.get("pending_extraction")
    if pe and not pe.get("awaiting_correction"):
        return True
    if ud.get("pending_fallback_pdf"):
        return True
    if ud.get("pending_report"):
        return True
    if ud.get("pending_read_status"):
        return True
    if ud.get("pending_arxiv"):
        return True
    if ud.get("pending_operation"):
        return True
    return False


def _is_awaiting_text_input(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True si hay un flujo esperando texto de corrección (awaiting_correction=True).

    Complementa _has_pending_keyboard: mientras que esa función devuelve False
    cuando awaiting_correction=True (para permitir texto), esta devuelve True para
    bloquear audio, fotos, documentos y comandos en esos mismos estados.
    """
    ud = context.user_data
    if ud.get("pending_transcript", {}).get("awaiting_correction"):
        return True
    if ud.get("pending_extraction", {}).get("awaiting_correction"):
        return True
    if ud.get("pending_note", {}).get("awaiting_correction"):
        return True
    return False


def _has_destination(fm: dict) -> bool:
    """Determina si el frontmatter tiene un destino claro."""
    if fm.get("type") == "task":
        return True  # task va a inbox si no tiene destino
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
    """Limpia estados pendientes del user_data y archivos temporales asociados.

    Si no se pasan keys, limpia todos los estados conocidos.
    Si se pasan keys, limpia solo esos.

    Busca temp_path tanto en el nivel raíz del dict como anidado en
    ``resource_file`` (estructura de pending_transcript).
    """
    if not keys:
        keys = (
            "pending_note", "pending_operation", "original_content",
            "pending_raw_content", "pending_capture_ctx", "pending_transcript",
            "pending_extraction", "pending_description",
            "pending_read_status", "pending_fallback_pdf",
            "pending_arxiv", "manage_missing_fields", "pending_report",
            "block_msg_ids", "clasificar_inbox_path",
        )

    for key in keys:
        data = context.user_data.pop(key, None)
        if not isinstance(data, dict):
            continue
        # temp_path puede estar en la raíz (pending_fallback_pdf) o
        # anidado en resource_file (pending_transcript)
        temp_path = data.get("temp_path") or (
            data.get("resource_file") or {}
        ).get("temp_path")
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


async def _get_existing_items(vault_path: Path) -> tuple[list[dict], list[dict]]:
    """Obtiene proyectos y áreas existentes leyendo los subdirectorios de
    01-Projects/ y 02-Areas/ directamente. Si existe un _index.md con
    campo project:/area: y description:, los usa; si no, usa el nombre del
    directorio como nombre y descripción vacía.
    """
    def _read_index(dir_path: Path, field: str) -> dict:
        index = dir_path / "_index.md"
        name = dir_path.name
        description = ""
        if index.exists():
            try:
                import frontmatter as fm
                post = fm.load(str(index))
                name = post.get(field, name)
                description = post.get("description", "")
            except Exception:
                pass
        return {"name": name, "description": description}

    projects_dir = vault_path / "01-Projects"
    areas_dir = vault_path / "02-Areas"

    projects = [
        _read_index(d, "project")
        for d in sorted(projects_dir.iterdir())
        if d.is_dir()
    ] if projects_dir.exists() else []

    areas = [
        _read_index(d, "area")
        for d in sorted(areas_dir.iterdir())
        if d.is_dir()
    ] if areas_dir.exists() else []

    return projects, areas


async def _get_existing_tags(vault_path: Path, limit: int = 100) -> list[str]:
    """Retorna los tags confirmados del vault (sin Inbox), ordenados por frecuencia.

    Excluye 00-Inbox para que solo se propaguen tags de notas ya confirmadas por
    el usuario. Limita a `limit` tags para no inflar el system prompt.
    """
    exclude = ["05-Archive", ".obsidian", ".trash", "00-Inbox"]
    tag_counts = await get_all_tags(vault_path, exclude_dirs=exclude)
    return list(tag_counts.keys())[:limit]
