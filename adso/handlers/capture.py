"""Flujo central de captura, clasificación y confirmación de notas.

Contiene _classify_and_preview (el orquestador principal), los callbacks de
confirmación/cancelación/destino, y los helpers del flujo de captura.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import Update
from telegram.ext import ContextTypes

from adso.bot_utils import (
    _cleanup_pending,
    _get_existing_items,
    _get_existing_tags,
    _has_destination,
    spawn_tracked,
)
from adso.config import Settings
from adso.embeddings import EmbeddingsClient
from adso.keyboards import (
    build_capture_keyboard,
    build_destination_keyboard,
    build_preview,
)
from adso.llm_client import VALID_TYPES, check_injection_risk, classify, extract_original_from_degraded
from adso.vault_search import find_by_property
from adso.tasks_client import TasksClient, build_task_notes
from adso.vault_writer import GitBackup, create_note, read_note, save_resource

logger = logging.getLogger(__name__)

_INJECTION_PREVIEW_WARNING = (
    "⚠️ El contenido extraído contiene un patrón de posible inyección de "
    "instrucciones. Revisar el preview con atención antes de confirmar.\n\n"
)


async def _classify_and_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    media_type: str,
    resource_file: Optional[dict] = None,
    extra_fm: Optional[dict] = None,
    user_context: Optional[str] = None,
    force_capture: bool = False,
    preserve_body: bool = False,
    forced_type: Optional[str] = None,
    prevent_task: bool = False,
    original_text: Optional[str] = None,
) -> None:
    """Clasifica texto extraído y muestra preview.

    Flujo compartido por audio, PDF y documentos de texto.

    Args:
        update: Telegram update.
        context: Bot context.
        text: Texto a clasificar.
        media_type: 'text', 'audio' o 'document'.
        resource_file: Info del archivo para guardar en Resources {temp_path, filename}.
        extra_fm: Campos adicionales para el frontmatter (read_status, etc.).
        user_context: Mensaje del usuario enviado junto al archivo (caption). Se guarda
            en el frontmatter si la nota cae en modo degradado para que el cron pueda
            usarlo al reclasificar.
        force_capture: Si True, ignora el mode del LLM y fuerza flujo de captura.
            Usar cuando el usuario eligió explícitamente guardar como nota.
        preserve_body: Si True, usa `text` como body verbatim sin importar el media_type.
            Activar cuando el texto proviene del usuario directamente (descripción manual,
            OCR confirmado) y no debe ser reescrito por el LLM.
        forced_type: Si se provee ('task'), sobreescribe el type inferido por el LLM.
            El LLM sigue clasificando título, tags, proyecto y área, pero el type queda fijo.
        prevent_task: Si True, impide que el LLM devuelva type=task. Usar cuando el usuario
            eligió explícitamente guardar como nota. Si el LLM devuelve task, se cambia a reference.
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
        user_context=user_context,
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
        payload = result["payload"]
        fm = payload["frontmatter"]
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        fm["date_created"] = now
        fm["date_modified"] = now
        fm["source"] = "telegram"
        fm["media_type"] = media_type
        if extra_fm:
            fm.update(extra_fm)
        if user_context:
            fm["user_context"] = user_context

        context.user_data["pending_note"] = result
        result["payload"]["suggested_links"] = []

        preview = build_preview(fm, payload.get("body", text), [])
        keyboard = build_capture_keyboard(fm, False)

        reply_fn = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text
        await reply_fn(
            "⚠️ No pude clasificar bien — guardado en Inbox como borrador. "
            "Confirmar, corregir o cancelar.\n\n" + preview,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    # Modos no implementados (query, edit) → tratar como captura
    if mode in ("query", "edit"):
        mode = "capture"
        result["mode"] = "capture"

    # Si el usuario forzó captura explícitamente, ignorar el mode del LLM
    if force_capture and mode != "capture":
        result["mode"] = "capture"
        mode = "capture"

    # Reparar payload si el modo fue forzado a capture desde un mode sin frontmatter
    if mode == "capture" and (
        not isinstance(result.get("payload"), dict)
        or not isinstance(result.get("payload", {}).get("frontmatter"), dict)
    ):
        if not isinstance(result.get("payload"), dict):
            result["payload"] = {}
        if not isinstance(result["payload"].get("frontmatter"), dict):
            result["payload"]["frontmatter"] = {}
        fm_forced = result["payload"]["frontmatter"]
        if not fm_forced.get("type") or fm_forced["type"] not in VALID_TYPES:
            fm_forced["type"] = "idea"
        if not fm_forced.get("title"):
            fm_forced["title"] = text[:80].strip()
        if not result["payload"].get("body"):
            result["payload"]["body"] = text

    if mode != "capture":
        reply_fn = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text
        await reply_fn("No entendí el mensaje como una nota para guardar. Intentá de nuevo.")
        return

    payload = result["payload"]
    fm = payload.get("frontmatter")
    if not isinstance(fm, dict):
        reply_fn = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text
        await reply_fn("Respuesta inesperada del LLM. Intentá de nuevo.")
        return
    suggested_links: list[dict] = []

    # Para texto libre y audio el body es siempre el texto original del usuario.
    # preserve_body extiende esto a imágenes/documentos cuando el texto viene
    # directamente del usuario (descripción manual, OCR confirmado).
    # original_text permite que el LLM clasifique con un fragmento (text) pero
    # el body de la nota use el contenido completo (original_text).
    if media_type in ("text", "audio") or preserve_body:
        body = original_text or text
        payload["body"] = original_text or text
    else:
        body = payload.get("body", "")

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fm["date_created"] = now
    fm["date_modified"] = now
    fm["source"] = "telegram"
    fm["media_type"] = media_type

    # El usuario eligió explícitamente el tipo — ignorar lo que infirió el LLM
    if forced_type:
        fm["type"] = forced_type
        if forced_type == "task":
            fm["status"] = "pending"
    elif prevent_task and fm.get("type") == "task":
        fm["type"] = "reference"
        fm["status"] = "active"

    # due_date y scheduled solo son relevantes para tareas
    if fm.get("type") != "task":
        fm.pop("due_date", None)
        fm.pop("scheduled", None)
    else:
        # Override LLM date with local parser — more reliable for relative expressions
        # ("el martes", "mañana", etc.) because the LLM often gets weekday arithmetic wrong.
        local_date = _parse_date_from_text(text)
        if local_date:
            fm["due_date"] = local_date

    embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")
    if embeddings and body:
        try:
            similar = await embeddings.query_similar(
                query_text=body,
                n_results=settings.links.max_suggestions,
                threshold=settings.links.similarity_threshold,
            )
            if similar:
                suggested_links = [{"note_id": s.note_id, "title": s.metadata.get("title", "")} for s in similar]
        except Exception as e:
            logger.warning("Error buscando links similares: %s", e)

    context.user_data["pending_note"] = result
    result["payload"]["suggested_links"] = suggested_links

    has_dest = _has_destination(fm)
    preview = build_preview(fm, body, suggested_links)
    keyboard = build_capture_keyboard(fm, has_dest)

    # Contenido extraído (PDF/OCR/Vision/documento) que trae un patrón de posible
    # inyección: el <input> ya va blindado en classify(), pero avisar para que el
    # usuario escrute el preview antes de confirmar. No bloquea — igual se confirma.
    if check_injection_risk(text):
        logger.warning("Patrón de inyección detectado en contenido a clasificar")
        preview = _INJECTION_PREVIEW_WARNING + preview

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
    suggested_links: list[dict] = []

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fm["date_created"] = now
    fm["date_modified"] = now
    fm["source"] = "telegram"
    fm["media_type"] = "text"

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
                suggested_links = [{"note_id": s.note_id, "title": s.metadata.get("title", "")} for s in similar]
        except Exception as e:
            logger.warning("Error buscando links similares: %s", e)

    context.user_data["pending_note"] = result
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

    written_path = await create_note(fm, payload["body"], vault_path)

    git_backup: Optional[GitBackup] = context.bot_data.get("git_backup")
    if git_backup:
        context.bot_data.setdefault("bot_written_paths", set()).add(written_path)
        await git_backup.notify(fm.get("title", "Sin título"))

    await update.message.reply_text(
        "No pude clasificar — guardado en Inbox. "
        "Se reintenta automáticamente."
    )


_WEEKDAYS_ES: dict[str, int] = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}


def _user_tz() -> timezone | ZoneInfo:
    """Zona horaria del usuario para parsear fechas relativas.

    Los días de la semana y "mañana"/"hoy" deben resolverse en la hora local del
    usuario: computarlos en UTC produce un off-by-one cerca de medianoche (ej. un
    usuario en UTC-3 escribiendo "el viernes" un jueves 22:00 local, que en UTC ya
    es viernes).

    Orden de resolución: ``ADSO_TIMEZONE`` (override explícito) → ``TZ`` (la que
    docker-compose ya define para el contenedor) → UTC. Requiere el paquete
    ``tzdata`` para que ``zoneinfo`` resuelva los nombres en imágenes slim.
    """
    tz_name = os.getenv("ADSO_TIMEZONE", "").strip() or os.getenv("TZ", "").strip()
    if not tz_name:
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Zona horaria inválida (%r) — usando UTC", tz_name)
        return timezone.utc


def _parse_date_from_text(text: str, now: Optional[datetime] = None) -> Optional[str]:
    """Intenta extraer una fecha en español del texto. Retorna ISO 8601 o None.

    Soporta:
    - ISO: "2026-04-15" o "15/04/2026"
    - Relativos: "hoy", "mañana", "pasado mañana"
    - Días de semana: "el viernes", "el próximo lunes"
    - Hora: "15hs", "15:30", "a las 15"

    Args:
        text: Texto en lenguaje natural (puede contener más cosas además de la fecha).
        now: Momento de referencia (para tests). Si None, usa la hora local del
            usuario (ADSO_TIMEZONE).

    Returns:
        String ISO 8601 (con hora si se detectó, solo fecha si no), o None.
    """
    t = text.lower()
    if now is None:
        now = datetime.now(_user_tz())

    # Hora: "15hs", "15h", "15:30", "a las 15"
    time_m = re.search(r'\b(\d{1,2}):(\d{2})\b', t) or \
             re.search(r'\b(\d{1,2})\s*hs?\b', t) or \
             re.search(r'a las\s+(\d{1,2})\b', t)
    hour, minute, has_time = 0, 0, False
    if time_m:
        parsed_hour = int(time_m.group(1))
        parsed_minute = int(time_m.group(2)) if time_m.lastindex and time_m.lastindex >= 2 else 0
        # Descartar horas/minutos fuera de rango ("a las 25", "30hs") en vez de
        # dejar que datetime.replace() lance ValueError más abajo.
        if 0 <= parsed_hour <= 23 and 0 <= parsed_minute <= 59:
            hour, minute, has_time = parsed_hour, parsed_minute, True

    target: Optional[datetime] = None

    # ISO: "2026-04-15"
    iso_m = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', t)
    if iso_m:
        try:
            target = datetime(
                int(iso_m.group(1)), int(iso_m.group(2)), int(iso_m.group(3)),
                hour, minute, tzinfo=timezone.utc,
            )
        except ValueError:
            pass

    # DD/MM/YYYY
    if not target:
        slash_m = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', t)
        if slash_m:
            try:
                target = datetime(
                    int(slash_m.group(3)), int(slash_m.group(2)), int(slash_m.group(1)),
                    hour, minute, tzinfo=timezone.utc,
                )
            except ValueError:
                pass

    # Relativos (con límites de palabra para no matchear dentro de otras palabras)
    if not target:
        if re.search(r'\bpasado\s+ma[ñn]ana\b', t):
            base = now + timedelta(days=2)
            target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        elif re.search(r'\bma[ñn]ana\b', t):
            base = now + timedelta(days=1)
            target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        elif re.search(r'\bhoy\b', t):
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Día de semana
    if not target:
        for name, weekday in _WEEKDAYS_ES.items():
            if re.search(r'\b' + name + r'\b', t):
                days_ahead = weekday - now.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                base = now + timedelta(days=days_ahead)
                target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
                break

    if target is None:
        return None
    if has_time:
        return target.strftime("%Y-%m-%dT%H:%M:%S")
    return target.strftime("%Y-%m-%d")


def _apply_task_corrections(fm: dict, text: str, text_lower: str) -> bool:
    """Aplica correcciones a un frontmatter de tarea desde texto libre.

    Detecta fecha, prioridad, tags y título en el mismo texto, en cualquier orden.
    Retorna True si se modificó al menos un campo.
    """
    changed = False

    # Fecha: "fecha X" o texto que contiene expresión de fecha
    date_input = text_lower
    if date_input.startswith("fecha "):
        date_input = date_input[6:].strip()
    date_str = _parse_date_from_text(date_input)
    if date_str:
        fm["due_date"] = date_str
        changed = True

    # Prioridad
    prio_m = re.search(r'\bprioridad\s+(alta|high|media|medium|baja|low)\b', text_lower)
    if prio_m:
        prio_map = {
            "alta": "high", "high": "high",
            "media": "medium", "medium": "medium",
            "baja": "low", "low": "low",
        }
        fm["priority"] = prio_map[prio_m.group(1)]
        changed = True

    # Tag
    tag_m = re.search(r'\bagregar\s+tag\s+(\S+)', text_lower) or \
            re.search(r'\btag\s+(\S+)', text_lower)
    if tag_m:
        tag = tag_m.group(1).replace(" ", "-")
        if not fm.get("tags"):
            fm["tags"] = []
        fm["tags"].append(tag)
        changed = True

    # Título explícito
    title_m = re.match(r'^t[ií]tulo\s+(.+)$', text_lower)
    if title_m:
        fm["title"] = text.split(" ", 1)[1].strip()
        changed = True

    # Sin fallback de título acá: el caller decide qué hacer cuando no cambió
    # nada (aplica el mismo guard de longitud/multilínea que la rama no-tarea).
    return changed


async def _handle_text_correction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    pending: dict,
    locked_msg_id: Optional[int] = None,
) -> None:
    """Interpreta texto libre como corrección del preview pendiente.

    Args:
        locked_msg_id: Si se provee, edita ese mensaje con el preview actualizado
            y elimina el mensaje del usuario (flujo con lock, como en audio).
            Si es None, envía un reply nuevo (flujo libre para notas no-tarea).
    """
    payload = pending["payload"]
    fm = payload["frontmatter"]
    text_lower = text.lower().strip()

    handled = True
    if fm.get("type") == "task":
        handled = _apply_task_corrections(fm, text, text_lower)
    elif text_lower.startswith("titulo ") or text_lower.startswith("título "):
        fm["title"] = text.split(" ", 1)[1].strip()
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
        if not fm.get("tags"):
            fm["tags"] = []
        fm["tags"].append(tag)
    elif text_lower.startswith("tipo ") or text_lower.startswith("type "):
        new_type = text_lower.split(" ", 1)[1].strip()
        if new_type in ("reference", "referencia", "note", "nota"):
            fm["type"] = "reference"
        elif new_type in ("task", "tarea"):
            fm["type"] = "task"
        elif new_type in ("idea",):
            fm["type"] = "idea"
    else:
        handled = False

    if not handled:
        # Ningún prefijo/campo reconocido. Solo usar como título si es texto corto
        # de una línea. Texto largo o multi-línea probablemente sea contenido
        # enviado por error en modo corrección — avisar sin modificar nada.
        # Aplica igual a tareas y a notas (antes las tareas pisaban el título sin
        # este guard).
        stripped = text.strip()
        if len(stripped) <= 200 and "\n" not in stripped:
            fm["title"] = stripped
        else:
            sent = await update.message.reply_text(
                "Corrección no reconocida. Usar prefijos: <code>titulo</code>, "
                "<code>tag</code>, <code>tipo</code>, <code>prioridad</code>.",
                parse_mode="HTML",
            )
            pending["error_msg_id"] = sent.message_id
            pending["error_user_msg_id"] = update.message.message_id
            return

    fm["date_modified"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    body = payload.get("body", "")
    suggested_links = payload.get("suggested_links", [])
    has_dest = _has_destination(fm)
    preview = build_preview(fm, body, suggested_links)
    keyboard = build_capture_keyboard(fm, has_dest)

    if locked_msg_id:
        pending["awaiting_correction"] = False
        try:
            await context.bot.edit_message_text(
                chat_id=update.message.chat_id,
                message_id=locked_msg_id,
                text=preview,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            await update.message.delete()
            for key in ("error_msg_id", "error_user_msg_id"):
                mid = pending.pop(key, None)
                if mid:
                    try:
                        await context.bot.delete_message(
                            chat_id=update.message.chat_id,
                            message_id=mid,
                        )
                    except Exception:
                        pass
        except Exception:
            # Si el edit falla, al menos enviar el preview actualizado
            await update.message.reply_text(preview, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(preview, reply_markup=keyboard, parse_mode="HTML")


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
        original_text = context.user_data.get("original_content", "")
        payload = {
            "frontmatter": {
                "title": original_text[:80].strip(),
                "type": "idea",
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
    suggested_links: list[dict] = payload.get("suggested_links", [])

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fm.setdefault("date_created", now)
    fm.setdefault("date_modified", now)
    fm.setdefault("source", "telegram")
    fm.setdefault("media_type", "text")

    if not suggested_links and body:
        settings: Settings = context.bot_data["settings"]
        embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")
        if embeddings:
            try:
                similar = await embeddings.query_similar(
                    query_text=body,
                    n_results=settings.links.max_suggestions,
                    threshold=settings.links.similarity_threshold,
                )
                if similar:
                    suggested_links = [{"note_id": s.note_id, "title": s.metadata.get("title", "")} for s in similar]
            except Exception as e:
                logger.warning("Error buscando links similares en callback: %s", e)
        payload["suggested_links"] = suggested_links

    context.user_data["pending_note"] = result

    has_dest = _has_destination(fm)
    preview = build_preview(fm, body, suggested_links)
    keyboard = build_capture_keyboard(fm, has_dest)

    await query.edit_message_text(
        preview,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def _index_note_safe(
    embeddings: EmbeddingsClient,
    note_path: Path,
    body: str,
    fm: dict,
    vault_path: Path,
) -> None:
    """Indexa embedding de forma segura (no propaga errores)."""
    import hashlib

    try:
        rel = note_path.relative_to(vault_path)
        note_id = str(rel).replace(".md", "")
        metadata = {
            "path": str(rel),
            "type": fm.get("type", ""),
            "status": fm.get("status", ""),
            "project": fm.get("project", ""),
            "area": fm.get("area", ""),
            "tags": fm.get("tags", []),
            "media_type": fm.get("media_type", ""),
            "title": fm.get("title", ""),
            "content_hash": hashlib.md5(body.encode()).hexdigest(),
        }
        await embeddings.index_note(note_id, body, metadata)
    except Exception as e:
        logger.warning("Error indexando embedding para %s: %s", note_path, e)


async def _push_task_safe(
    tasks_client: TasksClient,
    fm: dict,
    note_path: Path,
    vault_path: Path,
    body: str = "",
    notify_fn: Any = None,
    debug: bool = False,
) -> None:
    """Crea la tarea en Google Tasks de forma segura (no propaga errores).

    Args:
        notify_fn: Corrutina callable(str) para enviar mensajes por Telegram.
            Se invoca en caso de fallo de auth o error de API.
        debug: Si True, notifica también los pushes exitosos.
    """
    description = extract_original_from_degraded(body).strip() if body else ""
    notes = build_task_notes(fm, note_path, vault_path, description=description)
    title = fm.get("title", "Sin título")
    task_id = await tasks_client.create_task(
        title=title,
        notes=notes,
        due_date=fm.get("due_date"),
    )
    if task_id is None:
        if notify_fn:
            if tasks_client.auth_failed:
                await notify_fn(
                    "Error Google Tasks: token expirado o revocado.\n"
                    "Ejecutar `scripts/auth_google_tasks.py` para re-autenticar.\n"
                    f"La tarea '{title}' quedó guardada solo en el vault."
                )
            else:
                await notify_fn(
                    f"Error Google Tasks: no se pudo sincronizar '{title}'.\n"
                    "Ver logs para detalles."
                )
    elif debug and notify_fn:
        await notify_fn(f"[debug] Google Tasks: tarea '{title}' sincronizada (id={task_id})")


async def _cb_confirm(query: Any, context: ContextTypes.DEFAULT_TYPE, vault_path: Path) -> None:
    """Confirma y escribe la nota al vault."""
    pending = context.user_data.pop("pending_note", None)
    inbox_path_str: Optional[str] = context.user_data.pop("clasificar_inbox_path", None)

    if not pending:
        await query.edit_message_text("No hay nota pendiente.")
        return

    payload = pending["payload"]
    fm = payload["frontmatter"]
    body = payload.get("body", "")

    original_body = body  # body limpio antes de agregar Ver también (para Tasks)
    suggested_links = payload.get("suggested_links", [])
    if suggested_links:
        link_lines = []
        for lnk in suggested_links:
            note_id = lnk["note_id"]
            title = lnk.get("title", "").strip()
            slug = note_id.rsplit("/", 1)[-1]
            label = title if title else slug
            link_lines.append(f"- [[{slug}]] — {label}")
        body = body.rstrip() + "\n\n## Ver también\n\n" + "\n".join(link_lines)

    resource_file = pending.get("_resource_file")
    if resource_file:
        try:
            res_path = await save_resource(
                Path(resource_file["temp_path"]),
                resource_file["filename"],
                vault_path,
            )
            body += f"\n\n![[{res_path.name}]]"
            fm.setdefault("source_file", f"[[{res_path.name}]]")
            Path(resource_file["temp_path"]).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Error guardando recurso: %s", e)

    # Si viene de /clasificar y el LLM dejó pending-classification, la nota
    # fue revisada y confirmada por el usuario — ya no está pendiente.
    if inbox_path_str and fm.get("status") == "pending-classification":
        _STATUS_CONFIRMED = {"reference": "active", "task": "pending", "idea": "raw"}
        fm["status"] = _STATUS_CONFIRMED.get(fm.get("type", ""), "active")

    path = await create_note(fm, body, vault_path)

    if fm.get("type") == "task":
        tasks_client: Optional[TasksClient] = context.bot_data.get("tasks_client")
        if tasks_client:
            _settings: Settings = context.bot_data["settings"]
            _user_id = _settings.telegram_allowed_user_id

            async def _notify_tasks(msg: str) -> None:
                await context.bot.send_message(chat_id=_user_id, text=msg)

            spawn_tracked(
                _push_task_safe(
                    tasks_client,
                    fm,
                    path,
                    vault_path,
                    body=original_body,
                    notify_fn=_notify_tasks,
                    debug=_settings.tasks.debug,
                ),
                name="push_task",
            )

    git_backup: Optional[GitBackup] = context.bot_data.get("git_backup")
    if git_backup:
        context.bot_data.setdefault("bot_written_paths", set()).add(path)
        await git_backup.notify(fm.get("title", "Sin título"))

    # Indexar el embedding inline (mismo patrón que jobs.reclassify_inbox). El path
    # se registró en bot_written_paths arriba, así que el vault_watcher salteará el
    # evento inotify de esta escritura y no habrá doble embed. Antes se delegaba al
    # watcher, pero el watcher justamente saltea bot_written_paths → la nota quedaba
    # sin embedding hasta el reindex nocturno.
    embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")
    if embeddings and body.strip():
        spawn_tracked(
            _index_note_safe(embeddings, path, body, fm, vault_path),
            name="index_note",
        )

    if inbox_path_str:
        try:
            Path(inbox_path_str).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("No se pudo borrar nota de inbox: %s", e)

    rel_path = path.relative_to(vault_path)
    await query.edit_message_text(
        f"Nota guardada en: <code>{rel_path}</code>",
        parse_mode="HTML",
    )
    context.user_data.pop("original_content", None)

    if inbox_path_str:
        # Recalcular pendientes tras confirmar — mismo filtro que handle_clasificar:
        # pending-classification + sin project ni area (caso B)
        try:
            all_pending = await find_by_property(
                "status", "pending-classification", vault_path, scope="00-Inbox"
            )
            remaining = 0
            for ref in all_pending:
                try:
                    n = await read_note(ref.path)
                    if not n.frontmatter.get("project") and not n.frontmatter.get("area"):
                        remaining += 1
                except Exception:
                    pass
            if remaining > 0:
                await query.message.reply_text(
                    f"Quedan {remaining} nota{'s' if remaining > 1 else ''} más. "
                    "Usar /clasificar para continuar."
                )
        except Exception as e:
            logger.warning("Error calculando notas pendientes tras confirmar: %s", e)


async def _cb_cancel(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancela la operación pendiente."""
    _cleanup_pending(context)
    await query.edit_message_text("Cancelado.")


async def _cb_correct(query: Any, context: ContextTypes.DEFAULT_TYPE, vault_path: Path) -> None:
    """Muestra selector de destino."""
    keyboard = build_destination_keyboard()
    await query.edit_message_reply_markup(reply_markup=keyboard)


async def _cb_note_correct(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Activa modo corrección para la nota de tarea pendiente.

    Bloquea el mensaje con lock (msg_id) y espera texto del usuario para
    corregir campos: fecha, prioridad, tags, título.
    """
    pending = context.user_data.get("pending_note")
    if not pending:
        await query.answer("No hay nota pendiente.")
        return
    pending["awaiting_correction"] = True
    pending["msg_id"] = query.message.message_id
    await query.edit_message_text(
        query.message.text_html + "\n\n<i>Escribir corrección (fecha, prioridad, título, tags):</i>",
        parse_mode="HTML",
    )


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
        fm["project"] = None
        fm["section"] = None
        fm["area"] = None
    elif dest_type == "area":
        fm["project"] = None
        fm["section"] = None
        fm["area"] = dest_name
        if fm.get("status") == "pending-classification":
            fm["status"] = "active" if fm.get("type") == "reference" else "raw"
    elif dest_type == "project":
        fm["project"] = dest_name
        fm["section"] = None
        fm["area"] = None
        if fm.get("status") == "pending-classification":
            fm["status"] = "active" if fm.get("type") == "reference" else "raw"

    body = pending["payload"].get("body", "")
    suggested_links = pending["payload"].get("suggested_links", [])
    preview = build_preview(fm, body, suggested_links)

    await query.edit_message_text(
        preview + "\n\n¿Confirmar?",
        reply_markup=build_capture_keyboard(fm, has_destination=True),
        parse_mode="HTML",
    )


async def _cb_transcript_ok(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Confirma transcripción — para audio muestra [Tarea]/[Nota], para otros clasifica directo."""
    from adso.keyboards import build_save_keyboard
    pt = context.user_data.pop("pending_transcript", None)
    if not pt:
        await update.callback_query.edit_message_text("No hay transcripción pendiente.")
        return

    text = pt["text"]
    media_type = pt.get("media_type", "audio")
    temp_path = pt.get("temp_path")
    resource_file = pt.get("resource_file")

    if temp_path and not resource_file:
        Path(temp_path).unlink(missing_ok=True)

    if media_type == "audio":
        context.user_data["pending_raw_content"] = text
        context.user_data["pending_capture_ctx"] = {
            "media_type": "audio",
            "preserve_body": True,
            "resource_file": resource_file,
        }
        await update.callback_query.edit_message_text(
            "¿Guardar como tarea o como nota?",
            reply_markup=build_save_keyboard(),
        )
    else:
        await update.callback_query.edit_message_text("Clasificando...")
        await _classify_and_preview(
            update, context, text,
            media_type=media_type,
            resource_file=resource_file,
            preserve_body=True,
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

    classify_text = pe.get("classify_content") or pe["text"]
    resource_info = None
    extra_fm = {}

    if pe.get("original_filename"):
        resource_info = {
            "temp_path": pe["temp_path"],
            "filename": pe["original_filename"],
        }
    else:
        if pe.get("temp_path"):
            Path(pe["temp_path"]).unlink(missing_ok=True)

    if pe.get("read_status"):
        extra_fm["read_status"] = pe["read_status"]
    if pe.get("paper_title"):
        extra_fm["title"] = pe["paper_title"]
    if pe.get("paper_authors"):
        extra_fm["authors"] = pe["paper_authors"]
    if pe.get("paper_doi"):
        extra_fm["doi"] = pe["paper_doi"]

    await update.callback_query.edit_message_text("Clasificando...")

    preserve_body = pe.get("preserve_body", False)
    await _classify_and_preview(
        update, context, classify_text,
        media_type=pe.get("media_type", "document"),
        resource_file=resource_info,
        extra_fm=extra_fm or None,
        user_context=pe.get("user_context"),
        preserve_body=preserve_body,
        original_text=pe["text"] if preserve_body else None,
    )


async def _classify_and_preview_arxiv(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    metadata: dict,
    url: str,
    reply_msg: Optional[Any] = None,
) -> None:
    """Clasifica un paper de arXiv y muestra preview.

    Llama al LLM con el contenido académico para inferir proyecto, área y tags.
    Sobreescribe los campos literales del frontmatter (title, authors, year, doi,
    keywords) con los valores de la API de arXiv — el LLM no los inventa.

    Body resultante: ``> [!summary] AI Summary`` (del campo ``summary`` del LLM)
    + ``## Abstract`` (texto literal de la API) + ``## Personal Notes``.

    Nota sobre campos del LLM: se usa ``payload["summary"]`` (resumen breve en
    español, 1-2 oraciones) y NO ``payload["body"]``. El ``body`` que genera el
    LLM es el documento completo con callout + secciones — usarlo causaría
    duplicación del abstract y del callout. El ``summary`` en cambio es solo el
    texto plano del resumen, que ``build_arxiv_body()`` envuelve en el callout
    con el formato correcto.

    Args:
        update: Telegram update.
        context: Bot context.
        metadata: Dict retornado por arxiv_client.fetch_arxiv_metadata().
        url: URL canónica del paper en arxiv.org.
        reply_msg: Mensaje existente a editar con el preview (ej: el status
            "Clasificando..."). Si es None, se envía como reply al mensaje original.
    """
    from adso.arxiv_client import build_arxiv_classify_content, build_arxiv_body

    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    projects, areas = await _get_existing_items(vault_path)
    existing_tags = await _get_existing_tags(vault_path)

    content = build_arxiv_classify_content(metadata)

    async def on_retry(attempt: int, max_attempts: int) -> None:
        reply_fn = (
            update.callback_query.edit_message_text
            if update.callback_query
            else update.message.reply_text
        )
        await reply_fn(f"Servicio caído, reintento {attempt}/{max_attempts}...")

    result = await classify(
        content=content,
        media_type="link",
        existing_projects=projects,
        existing_areas=areas,
        existing_tags=existing_tags,
        disambiguation_threshold=settings.llm.disambiguation_threshold,
        on_retry=on_retry,
    )

    mode = result.get("mode", "")
    if mode in ("query", "edit"):
        result["mode"] = "capture"
        mode = "capture"

    if mode not in ("capture", "degraded"):
        reply_fn = (
            update.callback_query.edit_message_text
            if update.callback_query
            else update.message.reply_text
        )
        await reply_fn("No pude clasificar el paper. Intentá de nuevo.")
        return

    payload = result["payload"]
    fm = payload.get("frontmatter", {})

    # Sobreescribir con datos literales de la API (tienen prioridad absoluta sobre el LLM)
    fm["title"] = metadata["title"] or fm.get("title", "")
    fm["type"] = "reference"
    fm["source_url"] = url
    fm["media_type"] = "link"
    if metadata.get("authors"):
        fm["authors"] = metadata["authors"]
    if metadata.get("year"):
        fm["year"] = metadata["year"]
    if metadata.get("doi"):
        fm["doi"] = metadata["doi"]
    if metadata.get("keywords"):
        fm["keywords"] = metadata["keywords"]
    fm.setdefault("read_status", "unread")
    tags = fm.get("tags") or []
    if "paper" not in tags:
        fm["tags"] = ["paper"] + tags

    # Body: summary del LLM + abstract literal.
    # Usamos el campo "summary" (resumen breve en español, 1-2 oraciones) y no
    # "body", porque el LLM genera el body completo con callout + secciones —
    # nosotros construimos esa estructura con build_arxiv_body().
    llm_summary = (payload.get("summary") or "").strip() or None
    body = build_arxiv_body(metadata, llm_summary)
    payload["body"] = body
    payload["frontmatter"] = fm

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fm["date_created"] = now
    fm["date_modified"] = now
    fm["source"] = "telegram"

    suggested_links: list[dict] = []
    embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")
    if embeddings and metadata.get("abstract"):
        try:
            similar = await embeddings.query_similar(
                query_text=metadata["abstract"],
                n_results=settings.links.max_suggestions,
                threshold=settings.links.similarity_threshold,
            )
            if similar:
                suggested_links = [{"note_id": s.note_id, "title": s.metadata.get("title", "")} for s in similar]
        except Exception as e:
            logger.warning("Error buscando links similares: %s", e)

    result["payload"]["suggested_links"] = suggested_links
    context.user_data["pending_note"] = result

    has_dest = _has_destination(fm)
    preview = build_preview(fm, body, suggested_links)
    keyboard = build_capture_keyboard(fm, has_dest)

    # Abstract/metadata de arXiv con patrón de posible inyección → avisar.
    if check_injection_risk(content):
        logger.warning("Patrón de inyección detectado en metadata de arXiv")
        preview = _INJECTION_PREVIEW_WARNING + preview

    if reply_msg is not None:
        await reply_msg.edit_text(preview, reply_markup=keyboard, parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.edit_message_text(preview, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(preview, reply_markup=keyboard, parse_mode="HTML")
