"""Helpers y utilidades compartidas del bot.

Funciones puras y getters async sin lógica de negocio de Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Awaitable

from telegram.ext import ContextTypes

from adso.constants import MANAGE_KEYWORDS
from adso.vault_cache import parse_cached
from adso.vault_search import get_all_tags

logger = logging.getLogger(__name__)

# Referencias fuertes a tareas de fondo. asyncio solo guarda weak-refs a las
# tareas creadas con create_task: sin una referencia fuerte el GC puede
# recolectarlas a mitad de ejecución y cancelarlas silenciosamente (re-embed,
# push a Tasks, etc. perdidos). Se descartan solas al terminar.
_BG_TASKS: "set[asyncio.Task]" = set()


def spawn_tracked(coro: Awaitable, *, name: str | None = None) -> "asyncio.Task":
    """Crea una tarea de fondo con referencia fuerte y logging de excepciones.

    Reemplaza ``asyncio.create_task(coro)`` cuando no se espera el resultado:
    evita el GC prematuro y no deja excepciones sin loguear.
    """
    task = asyncio.ensure_future(coro)
    if name:
        try:
            task.set_name(name)
        except AttributeError:
            pass
    _BG_TASKS.add(task)

    def _done(t: "asyncio.Task") -> None:
        _BG_TASKS.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc is not None:
                logger.error("Tarea de fondo %s falló: %r", t.get_name(), exc)

    task.add_done_callback(_done)
    return task


# Tope del set bot_written_paths. En operación normal el set se drena solo (el
# VaultWatcher consume cada entrada al procesar el evento inotify de la escritura,
# ahora que on_moved está implementado). El cap es una red de seguridad: si algún
# evento se pierde y una entrada nunca se drena, el set no crece sin límite en
# uptime largo. 512 es muy holgado para un bot single-user.
_BOT_WRITTEN_CAP = 512


def mark_bot_written(bot_data: dict, path: Path) -> None:
    """Registra un path escrito por el bot para que VaultWatcher saltee su evento.

    El watcher chequea este set y descarta el evento inotify de la propia
    escritura del bot (evita doble embed). Acota el tamaño del set: descartar una
    entrada aún no drenada solo provoca un re-embed redundante (idempotente),
    nunca pérdida de datos.
    """
    paths: set = bot_data.setdefault("bot_written_paths", set())
    paths.add(path)
    if len(paths) > _BOT_WRITTEN_CAP:
        for stale in list(paths)[: len(paths) - _BOT_WRITTEN_CAP]:
            paths.discard(stale)


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
    # `pending_description` espera texto (la descripción de un archivo sin
    # caption), así que `_has_pending_keyboard` lo excluye a propósito. Pero sin
    # incluirlo acá, mandar un segundo binario pasaba todos los guards y
    # sobreescribía el estado: el temporal del primer archivo quedaba huérfano
    # y el archivo se perdía sin aviso. E6 de docs/audit-2026-07-31.md.
    if ud.get("pending_description"):
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

    El escaneo (iterdir + parse de cada _index.md) es I/O bloqueante y corre en
    todo flujo de clasificación antes de cada classify(); se ejecuta en un thread
    para no congelar el event loop en la RPi4 con SD lenta.
    """
    def _scan() -> tuple[list[dict], list[dict]]:
        def _read_index(dir_path: Path, field: str) -> dict:
            index = dir_path / "_index.md"
            name = dir_path.name
            description = ""
            note = parse_cached(index)
            if note is not None:
                name = note.frontmatter.get(field, name)
                description = note.frontmatter.get("description", "")
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

    return await asyncio.to_thread(_scan)


async def _get_existing_tags(vault_path: Path, limit: int = 100) -> list[str]:
    """Retorna los tags confirmados del vault (sin Inbox), ordenados por frecuencia.

    Excluye 00-Inbox para que solo se propaguen tags de notas ya confirmadas por
    el usuario. Limita a `limit` tags para no inflar el system prompt.
    """
    exclude = ["05-Archive", ".obsidian", ".trash", "00-Inbox"]
    tag_counts = await get_all_tags(vault_path, exclude_dirs=exclude)
    return list(tag_counts.keys())[:limit]
