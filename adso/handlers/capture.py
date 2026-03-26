"""Flujo central de captura, clasificación y confirmación de notas.

Contiene _classify_and_preview (el orquestador principal), los callbacks de
confirmación/cancelación/destino, y los helpers del flujo de captura.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from telegram import Update
from telegram.ext import ContextTypes

from adso.bot_utils import _cleanup_pending, _get_existing_items, _get_existing_tags, _has_destination
from adso.config import Settings
from adso.embeddings import EmbeddingsClient
from adso.keyboards import (
    build_capture_keyboard,
    build_destination_keyboard,
    build_preview,
)
from adso.llm_client import VALID_TYPES, classify
from adso.vault_search import find_by_property
from adso.vault_writer import GitBackup, create_note, read_note, save_resource

logger = logging.getLogger(__name__)


async def _classify_and_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    media_type: str,
    resource_file: Optional[dict] = None,
    extra_fm: Optional[dict] = None,
    user_context: Optional[str] = None,
    force_capture: bool = False,
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
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
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
            "Podés confirmar, corregir o cancelar.\n\n" + preview,
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
            fm_forced["type"] = "draft"
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
    suggested_links: list[str] = []

    # Para texto libre y audio el body es siempre el texto original del usuario
    if media_type in ("text", "audio"):
        body = text
        payload["body"] = text
    else:
        body = payload.get("body", "")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    fm["date_created"] = now
    fm["date_modified"] = now
    fm["source"] = "telegram"
    fm["media_type"] = media_type

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

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
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
                suggested_links = [s.note_id for s in similar]
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

    await create_note(fm, payload["body"], vault_path)

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
    payload = pending["payload"]
    fm = payload["frontmatter"]

    text_lower = text.lower().strip()

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
        if new_type in ("reference", "referencia", "note", "nota"):
            fm["type"] = "reference"
        elif new_type in ("task", "tarea"):
            fm["type"] = "task"
        elif new_type in ("idea",):
            fm["type"] = "idea"
    else:
        fm["title"] = text.strip()

    fm["date_modified"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

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
                "type": "draft",
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
    suggested_links: list[str] = payload.get("suggested_links", [])

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
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
                    suggested_links = [s.note_id for s in similar]
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

    suggested_links = payload.get("suggested_links", [])
    if suggested_links:
        wikilinks = " ".join(f"[[{link}]]" for link in suggested_links)
        body = body.rstrip() + f"\n\n## Ver también\n\n{wikilinks}"

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

    git_backup: Optional[GitBackup] = context.bot_data.get("git_backup")
    if git_backup:
        await git_backup.notify(fm.get("title", "Sin título"))

    embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")
    if embeddings and body:
        asyncio.create_task(
            _index_note_safe(embeddings, path, body, fm, vault_path)
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
                    "Mandá /clasificar para continuar."
                )
        except Exception as e:
            logger.warning("Error calculando notas pendientes tras confirmar: %s", e)


async def _cb_cancel(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancela la operación pendiente."""
    _cleanup_pending(context)
    context.user_data.pop("clasificar_inbox_path", None)
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
        fm["type"] = "draft"
        fm["project"] = None
        fm["section"] = None
        fm["area"] = None
        fm["status"] = "draft"
    elif dest_type == "resources":
        fm["project"] = None
        fm["section"] = None
        fm["area"] = None
        fm["_dest_resources"] = True
    elif dest_type == "area":
        fm["project"] = None
        fm["section"] = None
        fm["area"] = dest_name
        if fm.get("type") == "draft":
            fm["type"] = "reference"
        if fm.get("status") == "pending-classification":
            fm["status"] = "active"
    elif dest_type == "project":
        fm["project"] = dest_name
        fm["section"] = None
        fm["area"] = None
        if fm.get("type") == "draft":
            fm["type"] = "reference"
        if fm.get("status") == "pending-classification":
            fm["status"] = "active"

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
    """Confirma transcripción y clasifica el texto."""
    pt = context.user_data.pop("pending_transcript", None)
    if not pt:
        await update.callback_query.edit_message_text("No hay transcripción pendiente.")
        return

    text = pt["text"]
    media_type = pt.get("media_type", "audio")
    temp_path = pt.get("temp_path")
    resource_file = pt.get("resource_file")

    if temp_path and not resource_file:
        # Audio: borrar el temp. Para imagen/doc el temp es el recurso — lo borra _cb_confirm.
        Path(temp_path).unlink(missing_ok=True)

    await update.callback_query.edit_message_text("Clasificando...")

    await _classify_and_preview(
        update, context, text,
        media_type=media_type,
        resource_file=resource_file,
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

    await _classify_and_preview(
        update, context, classify_text,
        media_type=pe.get("media_type", "document"),
        resource_file=resource_info,
        extra_fm=extra_fm or None,
        user_context=pe.get("user_context"),
    )
