"""Dispatcher central de inline keyboard callbacks.

Solo routing: despacha a los handlers específicos según callback_data.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from adso.bot_utils import _has_destination
from adso.config import Settings
from adso.constants import (
    CB_ARXIV_CREATE_ANYWAY,
    CB_BACK,
    CB_CANCEL,
    CB_CHOOSE_AREA,
    CB_CHOOSE_PROJECT,
    CB_CLASIFICAR_INBOX,
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
    CB_INTENT_SAVE,
    CB_INTENT_TASK,
    CB_MANAGE_CANCEL,
    CB_QUERY_REPORT,
    CB_MANAGE_CONFIRM,
    CB_OCR,
    CB_READ_STATUS_READ,
    CB_READ_STATUS_UNREAD,
    CB_REPORT_HEALTH,
    CB_REPORT_IDEAS,
    CB_REPORT_IDEAS_PREFIX,
    CB_REPORT_IDEAS_SHOW_A,
    CB_REPORT_IDEAS_SHOW_P,
    CB_REPORT_MENU,
    CB_REPORT_READING,
    CB_REPORT_READING_PREFIX,
    CB_REPORT_READING_SHOW_A,
    CB_REPORT_READING_SHOW_P,
    CB_REPORT_SCOPE,
    CB_REPORT_SCOPE_PREFIX,
    CB_REPORT_SCOPE_SHOW_A,
    CB_REPORT_SCOPE_SHOW_P,
    CB_NOTE_CORRECT,
    CB_TRANSCRIPT_CANCEL,
    CB_TRANSCRIPT_CORRECT,
    CB_TRANSCRIPT_OK,
    CB_VISION,
)
from adso.handlers.capture import (
    _cb_cancel,
    _cb_confirm,
    _cb_correct,
    _cb_dest,
    _cb_extraction_ok,
    _cb_note_correct,
    _cb_transcript_ok,
    _handle_capture_from_callback,
)
from adso.bot_utils import _cleanup_pending
from adso.handlers.commands import handle_clasificar
from adso.handlers.input import _process_pdf_after_read_status
from adso.handlers.manage import _cb_intent_create, _cb_intent_note, _cb_intent_save, _cb_intent_task, _cb_manage_confirm
from adso.keyboards import (
    _esc,
    build_area_selector,
    build_capture_keyboard,
    build_project_selector,
)
from adso.handlers.reports import handle_report_callback
from adso.security import authorized

logger = logging.getLogger(__name__)


@authorized
async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de inline keyboard callbacks."""
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest as e:
        # "Query is too old": el ack llegó tarde a Telegram (lag de red). No
        # abortar — el tap del usuario sigue siendo válido y debe procesarse.
        logger.info("query.answer() falló (se procesa igual): %s", e)
    data = query.data

    # Borrar mensajes de bloqueo acumulados
    for mid in context.user_data.pop("block_msg_ids", []):
        try:
            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=mid)
        except Exception:
            pass

    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    if data == CB_CONFIRM:
        try:
            await _cb_confirm(query, context, vault_path)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                # Edición con contenido idéntico: la confirmación ya se aplicó
                # (reintento tras timeout o doble tap). No es un error.
                logger.info("Edición idéntica ignorada en _cb_confirm.")
            else:
                logger.exception("Error en _cb_confirm: %s", e)
                await query.edit_message_text(f"Error al guardar: {e}")
        except Exception as e:
            logger.exception("Error en _cb_confirm: %s", e)
            await query.edit_message_text(f"Error al guardar: {e}")

    elif data == CB_CANCEL:
        await _cb_cancel(query, context)

    elif data == CB_CORRECT:
        await _cb_correct(query, context, vault_path)

    elif data == CB_NOTE_CORRECT:
        await _cb_note_correct(query, context)

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

    elif data == CB_BACK:
        pending = context.user_data.get("pending_note")
        if pending:
            fm = pending["payload"]["frontmatter"]
            keyboard = build_capture_keyboard(fm, _has_destination(fm))
            await query.edit_message_reply_markup(reply_markup=keyboard)

    elif data == CB_INTENT_SAVE:
        await _cb_intent_save(update, context)

    elif data == CB_INTENT_TASK:
        await _cb_intent_task(update, context)

    elif data == CB_INTENT_NOTE:
        await _cb_intent_note(update, context)

    elif data == CB_INTENT_CREATE_PROJECT:
        await _cb_intent_create(update, context, "create_project")

    elif data == CB_INTENT_CREATE_AREA:
        await _cb_intent_create(update, context, "create_area")

    elif data == CB_DISAMBIG_CAPTURE:
        pending = context.user_data.get("pending_note")
        if pending:
            pending["needs_disambiguation"] = False
            await _handle_capture_from_callback(query, context, pending)

    elif data == CB_DISAMBIG_QUERY:
        from adso.handlers.query import run_query
        pending_text = context.user_data.pop("pending_raw_content", None)
        # Limpiar estado de captura pendiente: el usuario eligió buscar, no guardar.
        context.user_data.pop("pending_note", None)
        context.user_data.pop("pending_capture_ctx", None)
        if pending_text:
            await run_query(update, context, pending_text, keyboard_msg=query.message)
        else:
            await query.edit_message_text("No hay texto para buscar.")

    elif data == CB_QUERY_REPORT:
        from adso.handlers.query import cb_query_report
        await cb_query_report(query, context)

    elif data == CB_MANAGE_CONFIRM:
        await _cb_manage_confirm(query, context, vault_path)

    elif data == CB_MANAGE_CANCEL:
        await query.edit_message_text("Operación cancelada.")
        context.user_data.pop("pending_operation", None)

    elif data == CB_TRANSCRIPT_OK:
        await _cb_transcript_ok(update, context)

    elif data == CB_TRANSCRIPT_CORRECT:
        pt = context.user_data.get("pending_transcript")
        if pt:
            pt["awaiting_correction"] = True
            pt["msg_id"] = query.message.message_id
            await query.edit_message_text(
                _build_extract_preview(
                    "Transcripción actual", pt["text"],
                    footer="Texto corregido (escribir a continuación):",
                ),
                parse_mode="HTML",
            )

    elif data == CB_TRANSCRIPT_CANCEL:
        _cleanup_pending(context, "pending_transcript")
        await query.edit_message_text("Cancelado.")

    elif data == CB_READ_STATUS_READ:
        await _process_pdf_after_read_status(update, context, "read")

    elif data == CB_READ_STATUS_UNREAD:
        await _process_pdf_after_read_status(update, context, "unread")

    elif data == CB_EXTRACTION_OK:
        await _cb_extraction_ok(update, context)

    elif data == CB_EXTRACTION_CORRECT:
        pe = context.user_data.get("pending_extraction")
        if pe:
            pe["awaiting_correction"] = True
            pe["msg_id"] = query.message.message_id
            await query.edit_message_text(
                _build_extract_preview(
                    "Texto extraído", pe.get("text", ""),
                    footer="Texto corregido (escribir a continuación):",
                ),
                parse_mode="HTML",
            )

    elif data == CB_EXTRACTION_CANCEL:
        _cleanup_pending(context, "pending_extraction", "pending_description", "pending_fallback_pdf")
        await query.edit_message_text("Cancelado.")

    elif data == CB_DESCRIBE:
        pdf_info = context.user_data.pop("pending_fallback_pdf", None)
        if pdf_info:
            caption = pdf_info.get("user_context")
            if caption:
                # El usuario ya escribió una descripción al enviar la imagen
                # (caption): usarla directamente en vez de volver a pedírsela.
                from adso.handlers.capture import _classify_and_preview

                await query.edit_message_text("Clasificando...")
                await _classify_and_preview(
                    update, context, caption,
                    media_type=pdf_info.get("media_type", "image"),
                    resource_file={
                        "temp_path": pdf_info["temp_path"],
                        "filename": pdf_info.get("original_filename", "imagen.jpg"),
                    },
                    preserve_body=True,
                )
            else:
                context.user_data["pending_description"] = pdf_info
                await query.edit_message_text(
                    "Describir el contenido del archivo para clasificarlo:"
                )

    elif data == CB_OCR:
        await _cb_ocr(update, context)

    elif data == CB_VISION:
        await _cb_vision(update, context)

    elif data == CB_CLASIFICAR_INBOX:
        await handle_clasificar(update, context)

    elif data == CB_ARXIV_CREATE_ANYWAY:
        await _cb_arxiv_create_anyway(update, context)

    elif data in (
        CB_REPORT_MENU, CB_REPORT_SCOPE, CB_REPORT_IDEAS, CB_REPORT_HEALTH, CB_REPORT_READING,
        CB_REPORT_SCOPE_SHOW_P, CB_REPORT_SCOPE_SHOW_A,
        CB_REPORT_IDEAS_SHOW_P, CB_REPORT_IDEAS_SHOW_A,
        CB_REPORT_READING_SHOW_P, CB_REPORT_READING_SHOW_A,
    ) or (
        data.startswith(CB_REPORT_SCOPE_PREFIX)
        or data.startswith(CB_REPORT_IDEAS_PREFIX)
        or data.startswith(CB_REPORT_READING_PREFIX)
    ):
        await handle_report_callback(query, context, data)


_PDF_SCAN_PAGES = 2  # páginas a procesar en OCR y Vision para PDFs escaneados
_MAX_RENDER_PIXELS = 16_000_000  # tope de píxeles por página renderizada (protege la RAM de la RPi4)


def _pdf_page_count(pdf_path: "Path") -> int:
    """Cuenta las páginas de un PDF. Síncrono — llamar via asyncio.to_thread."""
    import fitz

    with fitz.open(str(pdf_path)) as doc:
        return len(doc)


def _render_pdf_pages(tmp_path: "Path", n_pages: int, dpi: int = 200) -> list[tuple[bytes, str]]:
    """Renderiza las primeras n_pages de un PDF como imágenes PNG en memoria.

    Síncrono y CPU-intensivo (segundos en la RPi4) — llamar siempre via
    asyncio.to_thread para no bloquear el event loop. Si una página declara
    dimensiones enormes, el DPI efectivo se reduce para no superar
    _MAX_RENDER_PIXELS (evita OOM por PDFs maliciosos o malformados).

    Returns:
        Lista de (bytes, mime_type) lista para enviar a Vision o pytesseract.
    """
    import fitz

    result = []
    with fitz.open(str(tmp_path)) as doc:
        for i in range(min(n_pages, len(doc))):
            page = doc[i]
            scale = dpi / 72.0
            est_pixels = (page.rect.width * scale) * (page.rect.height * scale)
            if est_pixels > _MAX_RENDER_PIXELS:
                scale *= (_MAX_RENDER_PIXELS / est_pixels) ** 0.5
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
            result.append((pix.tobytes("png"), "image/png"))
    return result


_PREVIEW_LIMIT = 3900  # margen bajo el límite de 4096 caracteres de un mensaje de Telegram


def _build_extract_preview(label: str, text: str, footer: str = "") -> str:
    """Arma el preview HTML del texto extraído (OCR/Vision) para Telegram.

    Muestra el máximo posible dentro del límite de un mensaje (~4096 chars),
    dentro de un bloque ``<code>`` para que el usuario pueda copiarlo entero de
    un toque. Solo trunca si el texto excede lo que entra en un mensaje; en ese
    caso avisa que la versión completa se guarda igual al confirmar (el texto
    íntegro vive en ``pending_transcript``).

    ``footer`` es una instrucción opcional (ej. en modo corrección) que se
    agrega después del bloque copiable y se cuenta dentro del presupuesto.
    """
    header = f"<b>{label}:</b>\n\n"
    note = "\n\n<i>[…texto truncado en el preview; se guarda completo al confirmar]</i>"
    foot = f"\n\n{footer}" if footer else ""
    body = text
    truncated = False
    while True:
        suffix = (note if truncated else "") + foot
        rendered = f"{header}<code>{_esc(body)}</code>{suffix}"
        if len(rendered) <= _PREVIEW_LIMIT or not body:
            return rendered
        truncated = True
        body = body[: max(0, int(len(body) * 0.9))]


async def _cb_ocr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Extrae texto de imagen o PDF escaneado usando pytesseract.

    Lee ``pending_fallback_pdf`` del contexto. Si encuentra texto, mueve el
    estado a ``pending_transcript`` y muestra el resultado con
    ``build_ocr_result_keyboard`` (que incluye la opción de cambiar a Gemini
    Vision). Si no encuentra texto, ofrece un teclado de fallback sin OCR.
    """
    import asyncio
    from pathlib import Path

    query = update.callback_query
    pending = context.user_data.get("pending_fallback_pdf")
    if not pending:
        await query.answer("No hay imagen pendiente.", show_alert=True)
        return

    tmp_path = Path(pending["temp_path"])
    media_type = pending.get("media_type", "image")

    try:
        import pytesseract
        from PIL import Image

        if media_type == "document":
            import io

            total_pages = await asyncio.to_thread(_pdf_page_count, tmp_path)
            pages_to_scan = min(_PDF_SCAN_PAGES, total_pages)
            await query.edit_message_text(
                f"Ejecutando OCR en las primeras {pages_to_scan} página(s) del PDF..."
            )
            page_images = await asyncio.to_thread(_render_pdf_pages, tmp_path, pages_to_scan)
            texts = []
            for img_bytes, _ in page_images:
                t = await asyncio.to_thread(
                    lambda b=img_bytes: pytesseract.image_to_string(
                        Image.open(io.BytesIO(b)), lang="spa+eng"
                    )
                )
                texts.append(t)
            text = "\n\n".join(texts)
        else:
            await query.edit_message_text("Ejecutando OCR...")
            text = await asyncio.to_thread(
                lambda: pytesseract.image_to_string(Image.open(tmp_path), lang="spa+eng")
            )

        if not text.strip():
            await query.edit_message_text(
                "OCR no encontró texto. Intentar con Gemini Vision o describir el contenido.",
                reply_markup=_build_fallback_keyboard_without_ocr(),
            )
            return

    except Exception as e:
        logger.error("Error en OCR: %s", e)
        await query.edit_message_text(f"Error en OCR: {e}")
        return

    context.user_data.pop("pending_fallback_pdf", None)
    context.user_data["pending_transcript"] = {
        "text": text,
        "media_type": media_type,
        "user_context": pending.get("user_context"),
        "resource_file": {
            "temp_path": str(tmp_path),
            "filename": pending.get("original_filename", "imagen.jpg"),
        },
    }

    from adso.keyboards import build_ocr_result_keyboard
    sent = await query.edit_message_text(
        _build_extract_preview("Texto extraído (OCR)", text),
        reply_markup=build_ocr_result_keyboard(),
        parse_mode="HTML",
    )
    context.user_data["pending_transcript"]["msg_id"] = sent.message_id if sent else None


async def _cb_vision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Describe imagen o PDF escaneado usando Gemini Vision.

    Puede ser invocado desde dos estados:
    - ``pending_fallback_pdf``: flujo original de imagen/PDF sin texto extraíble.
    - ``pending_transcript``: el usuario eligió Gemini Vision tras ver el resultado OCR.

    En ambos casos procesa la imagen y reemplaza el estado por un nuevo
    ``pending_transcript`` con el texto de Vision.
    """
    from pathlib import Path

    query = update.callback_query
    from_ocr = False
    pending = context.user_data.get("pending_fallback_pdf")
    if not pending:
        # Invocado desde el resultado OCR: recuperar imagen de pending_transcript
        transcript = context.user_data.get("pending_transcript")
        if transcript and transcript.get("resource_file"):
            pending = {
                "temp_path": transcript["resource_file"]["temp_path"],
                "media_type": transcript.get("media_type", "image"),
                "original_filename": transcript["resource_file"].get("filename", "imagen.jpg"),
                "user_context": transcript.get("user_context"),
            }
            from_ocr = True
        else:
            await query.answer("No hay imagen pendiente.", show_alert=True)
            return

    await query.edit_message_text("Consultando Gemini Vision...")

    tmp_path = Path(pending["temp_path"])
    media_type = pending.get("media_type", "image")

    try:
        from adso.llm_client import (
            describe_image_with_vision,
            _VISION_PROMPT_IMAGE,
            _VISION_PROMPT_PDF,
        )

        if media_type == "document":
            images = await asyncio.to_thread(_render_pdf_pages, tmp_path, _PDF_SCAN_PAGES)
            text = await describe_image_with_vision(images, prompt=_VISION_PROMPT_PDF)
        else:
            image_bytes = await asyncio.to_thread(tmp_path.read_bytes)
            text = await describe_image_with_vision(
                [(image_bytes, "image/jpeg")], prompt=_VISION_PROMPT_IMAGE
            )

    except Exception as e:
        logger.error("Error en Gemini Vision: %s", e)
        await query.edit_message_text(f"Error consultando Gemini Vision: {e}")
        return

    # Limpiar el estado previo solo tras éxito
    if from_ocr:
        context.user_data.pop("pending_transcript", None)
    else:
        context.user_data.pop("pending_fallback_pdf", None)

    context.user_data["pending_transcript"] = {
        "text": text,
        "media_type": media_type,
        "user_context": pending.get("user_context"),
        "resource_file": {
            "temp_path": str(tmp_path),
            "filename": pending.get("original_filename", "imagen.jpg"),
        },
    }

    from adso.keyboards import build_transcript_keyboard
    sent = await query.edit_message_text(
        _build_extract_preview("Texto extraído (Gemini Vision)", text),
        reply_markup=build_transcript_keyboard(),
        parse_mode="HTML",
    )
    context.user_data["pending_transcript"]["msg_id"] = sent.message_id if sent else None


async def _cb_arxiv_create_anyway(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """El usuario eligió crear la nota a pesar de que el paper ya existe en el vault.

    Lee el estado guardado en ``pending_arxiv`` y retoma el flujo normal de
    clasificación y preview, editando el mensaje de aviso de duplicado.
    """
    query = update.callback_query
    pending = context.user_data.pop("pending_arxiv", None)
    if not pending:
        await query.answer("No hay paper pendiente.", show_alert=True)
        return

    from adso.handlers.capture import _classify_and_preview_arxiv

    await query.edit_message_text(
        f"<b>{_esc(pending['metadata']['title'])}</b>\nClasificando...",
        parse_mode="HTML",
    )
    await _classify_and_preview_arxiv(
        update, context,
        metadata=pending["metadata"],
        url=pending["url"],
        reply_msg=query.message,
    )


def _build_fallback_keyboard_without_ocr():
    """Teclado de fallback cuando OCR no encontró texto (sin botón OCR)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Gemini Vision", callback_data=CB_VISION)],
        [
            InlineKeyboardButton("Cancelar", callback_data=CB_EXTRACTION_CANCEL),
            InlineKeyboardButton("Describir", callback_data=CB_DESCRIBE),
        ],
    ])
