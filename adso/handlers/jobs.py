"""Jobs periódicos del bot: reclasificación de inbox y reindex de embeddings."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from telegram.ext import ContextTypes

from adso.bot_utils import _get_existing_items, _get_existing_tags, mark_bot_written, spawn_tracked
from adso.config import Settings
from adso.embeddings import EmbeddingsClient
from adso.handlers.capture import _index_note_safe, _redirect_unimplemented_mode
from adso.keyboards import _esc
from adso.llm_client import classify, extract_original_from_degraded
from adso.vault_search import find_by_property
from adso.vault_writer import GitBackup, create_note, delete_note, read_note

logger = logging.getLogger(__name__)

# Claves que indican un flujo interactivo en curso: si alguna está presente, el
# cron de reclasificación se salta la pasada para no pisar/notificar en medio de
# una interacción del usuario. Debe cubrir todas las keys de flujo (alineado con
# _has_pending_keyboard / _is_awaiting_text_input en bot_utils.py): incluye los
# flujos de PDF escaneado, read_status, arXiv y reportes que antes faltaban.
_PENDING_FLOW_KEYS = {
    "pending_note", "pending_operation", "pending_raw_content",
    "pending_extraction", "pending_transcript", "pending_description",
    "manage_missing_fields", "pending_fallback_pdf", "pending_read_status",
    "pending_arxiv", "pending_report",
}

_STATUS_DEFAULT = {"reference": "active", "task": "pending", "idea": "raw"}

# Lock compartido entre los jobs pesados sobre el vault (reclassify_inbox y
# reindex_job). Evita: (a) dos invocaciones del mismo job en paralelo si una
# tarda más que el intervalo de scheduling (delete_note sobre el mismo path
# dispararía error), y (b) que la reclasificación corra encima del reindex
# nocturno, sumando carga de CPU/red concurrente en la RPi4. El reindex espera
# el lock (debe correr sí o sí); la reclasificación saltea la pasada y
# reintenta en el próximo ciclo.
_vault_heavy_lock = asyncio.Lock()


async def reclassify_inbox(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job periódico: clasifica silenciosamente notas de Inbox con destino ya asignado (Caso A).

    Caso A — nota con project/area en frontmatter: el LLM genera tags, summary y body
    limpio, preserva el destino del usuario y mueve la nota al directorio correcto.
    Notificación breve al usuario al completar.

    Caso B — nota sin destino: se ignora. El usuario debe usar /clasificar.
    """
    if _vault_heavy_lock.locked():
        logger.debug("Reclasificación: reindex o invocación previa en curso, saltando.")
        return

    async with _vault_heavy_lock:
        await _reclassify_inbox_impl(context)


async def _reclassify_inbox_impl(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path
    chat_id = settings.telegram_allowed_user_id

    inbox_notes = await find_by_property(
        "status", "pending-classification", vault_path,
        scope="00-Inbox",
    )

    if not inbox_notes:
        return

    user_data: dict = context.application.user_data.get(chat_id, {})
    if any(k in user_data for k in _PENDING_FLOW_KEYS):
        logger.info("Reclasificación: usuario tiene flujo pendiente, posponiendo.")
        return

    projects, areas = await _get_existing_items(vault_path)
    existing_tags = await _get_existing_tags(vault_path)

    for ref in inbox_notes:
        try:
            note = await read_note(ref.path)
            orig_fm = note.frontmatter

            # Caso B: sin destino — esperar /clasificar
            if not orig_fm.get("project") and not orig_fm.get("area"):
                continue

            # Caso A: destino asignado — clasificar silenciosamente
            if not note.body or not note.body.strip():
                logger.info("Reclasificación: saltando nota sin body: %s", ref.path)
                continue

            contenido = extract_original_from_degraded(note.body)
            result = await classify(
                content=contenido,
                media_type=orig_fm.get("media_type", "text"),
                existing_projects=projects,
                existing_areas=areas,
                existing_tags=existing_tags,
                disambiguation_threshold=settings.llm.disambiguation_threshold,
                user_context=orig_fm.get("user_context") or None,
            )

            if result.get("mode") == "degraded":
                logger.info("Reclasificación: LLM no disponible, reintentará.")
                return

            # El LLM sigue devolviendo query/edit aunque el prompt ya no los
            # ofrezca. Sin este redirect —el mismo que usa el flujo interactivo
            # de captura— una nota degradada cuyo texto tiene forma de pregunta
            # se salteaba en CADA pasada del cron: quemaba quota cada 30 minutos
            # y no salía nunca del Inbox.
            mode = _redirect_unimplemented_mode(result, contenido)
            if mode != "capture":
                logger.info(
                    "Reclasificación: %s clasificada como '%s', omitiendo.",
                    ref.path, mode,
                )
                continue

            payload = result["payload"]
            if "frontmatter" not in payload:
                logger.warning("Reclasificación: payload sin frontmatter en %s", ref.path)
                continue

            new_fm = payload["frontmatter"]

            # Invariante: el destino asignado por el usuario nunca se sobreescribe
            new_fm["project"] = orig_fm.get("project")
            new_fm["section"] = orig_fm.get("section")
            new_fm["area"] = orig_fm.get("area")
            new_fm["date_created"] = orig_fm.get("date_created", "")
            new_fm["source"] = "telegram"
            new_fm["media_type"] = orig_fm.get("media_type", "text")
            new_fm.pop("user_context", None)

            note_type = new_fm.get("type", "reference")
            if new_fm.get("status") in (None, "pending-classification"):
                new_fm["status"] = _STATUS_DEFAULT.get(note_type, "active")

            if orig_fm.get("media_type") == "audio":
                body = extract_original_from_degraded(note.body)
            else:
                body = payload.get("body", extract_original_from_degraded(note.body))

            # Verificar flujo activo justo antes de escribir (classify() tarda segundos)
            user_data_now: dict = context.application.user_data.get(chat_id, {})
            if any(k in user_data_now for k in _PENDING_FLOW_KEYS):
                logger.info("Reclasificación: flujo iniciado durante classify(), posponiendo.")
                return

            # Crear primero, borrar después (mismo orden que _cb_confirm): si
            # create_note falla, la nota original sigue en el Inbox y el próximo
            # ciclo reintenta. Con el orden inverso el contenido solo vivía en
            # memoria y se perdía. Una colisión de nombre la resuelve _unique_path.
            new_path = await create_note(new_fm, body, vault_path)
            await delete_note(ref.path)

            # Fuera del `if git_backup` — ver F1 en docs/audit-2026-07-31.md.
            mark_bot_written(context.bot_data, new_path)

            git_backup: Optional[GitBackup] = context.bot_data.get("git_backup")
            if git_backup:
                await git_backup.notify(new_fm.get("title", "Sin título"))

            embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")
            if embeddings and body:
                spawn_tracked(
                    _index_note_safe(embeddings, new_path, body, new_fm, vault_path),
                    name="index_note",
                )

            title = new_fm.get("title", "Sin título")
            dest = (
                f"01-Projects/{new_fm['project']}"
                if new_fm.get("project")
                else f"02-Areas/{new_fm['area']}"
            )

            logger.info("Reclasificación exitosa: %s → %s", ref.path, new_path)

            # La notificación va DESPUÉS del punto de no retorno y su fallo no
            # puede arrastrar la pasada: con el `return` detrás del send, un
            # BadRequest caía al `except` por-nota y el `for` seguía con la nota
            # siguiente, encadenando classify() contra un free tier de 15 RPM
            # (R3b). El invariante "una nota por ciclo" no depende de que
            # Telegram conteste.
            try:
                # `dest` sale de `new_fm['project']`/`['area']`, que pasaron por
                # `_safe_component`: eso bloquea traversal, pero `<`, `>` y `&`
                # son nombres de carpeta válidos y con parse_mode=HTML rompen el
                # mensaje (R3a). Va escapado, igual que el título.
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"✓ Nota clasificada: <b>{_esc(title)}</b> → "
                        f"<code>{_esc(dest)}</code>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("No se pudo notificar la reclasificación: %s", e)

            return  # Procesar de a una por ciclo

        except Exception as e:
            logger.warning("Error reclasificando %s: %s", ref.path, e)


async def heartbeat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job periódico: actualiza /tmp/adso_heartbeat para el HEALTHCHECK de Docker."""
    Path("/tmp/adso_heartbeat").touch()


async def reindex_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job nocturno: reindexar vault completo en ChromaDB."""
    settings: Settings = context.bot_data["settings"]
    embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")

    if not embeddings:
        return

    logger.info("Reindex nocturno iniciando...")
    try:
        async with _vault_heavy_lock:
            stats = await embeddings.reindex_vault(
                vault_path=settings.vault_path,
                exclude_dirs=settings.vault.exclude_dirs,
            )
        logger.info("Reindex completo: %s", stats)
    except Exception as e:
        logger.error("Error en reindex nocturno: %s", e)
