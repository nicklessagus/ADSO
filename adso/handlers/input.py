"""Handlers de entrada: texto, audio, documento y procesamiento post-read_status.

Todos los tipos de input convergen aquí antes de pasar al flujo de clasificación.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from adso.bot_utils import _cleanup_pending, _detect_manage_keywords, _has_destination
from adso.config import Settings
from adso.constants import CB_EXTRACTION_CANCEL
from adso.document_extractor import (
    build_classify_content,
    detect_paper,
    extract_paper_sections,
    extract_pdf,
    extract_text_file,
    is_pdf,
    is_text_file,
)
from adso.keyboards import (
    _esc,
    build_extraction_keyboard,
    build_intent_keyboard,
    build_read_status_keyboard,
    build_save_keyboard,
    build_transcript_keyboard,
)
from adso.llm_client import check_injection_risk
from adso.security import authorized
from adso.transcriber import transcribe_audio

logger = logging.getLogger(__name__)


@authorized
async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler principal de mensajes de texto."""
    from adso.handlers.capture import _classify_and_preview, _handle_text_correction
    from adso.handlers.manage import _handle_manage, _handle_manage_missing_fields

    settings: Settings = context.bot_data["settings"]
    text = update.message.text

    # Transcripción pendiente esperando corrección
    if context.user_data.get("pending_transcript", {}).get("awaiting_correction"):
        pt = context.user_data["pending_transcript"]
        pt["text"] = text
        pt["awaiting_correction"] = False
        snippet = text[:500] + ("..." if len(text) > 500 else "")
        msg_id = pt.get("msg_id")
        if msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=update.message.chat_id,
                    message_id=msg_id,
                    text=f"<b>Transcripción corregida:</b>\n\n<code>{_esc(snippet)}</code>",
                    reply_markup=build_transcript_keyboard(),
                    parse_mode="HTML",
                )
                await update.message.delete()
            except Exception:
                pass
        return

    # Extracción pendiente: tratar como corrección del texto extraído
    if context.user_data.get("pending_extraction"):
        pe = context.user_data["pending_extraction"]
        pe["text"] = text
        pe.pop("classify_content", None)
        await update.message.reply_text(
            f"<b>Texto corregido.</b>\n\n<i>{_esc(text[:500])}</i>",
            reply_markup=build_extraction_keyboard(),
            parse_mode="HTML",
        )
        return

    # Descripción de archivo binario
    if context.user_data.get("pending_description"):
        pd = context.user_data.pop("pending_description")
        resource_info = {
            "temp_path": pd["temp_path"],
            "filename": pd["original_filename"],
        }
        extra_fm = {}
        if pd.get("read_status"):
            extra_fm["read_status"] = pd["read_status"]
        await _classify_and_preview(
            update, context, text,
            media_type=pd["media_type"],
            resource_file=resource_info,
            extra_fm=extra_fm or None,
        )
        return

    # Campos faltantes de operación manage
    if context.user_data.get("manage_missing_fields") and context.user_data.get("pending_operation"):
        await _handle_manage_missing_fields(update, context, text)
        return

    # Preview pendiente: corrección por texto libre
    pending = context.user_data.get("pending_note")
    if pending:
        await _handle_text_correction(update, context, text, pending)
        return

    # Nuevo contenido
    context.user_data["pending_raw_content"] = text

    if check_injection_risk(text):
        logger.warning("Patrón de inyección detectado en mensaje")
        from adso.constants import CB_CANCEL, CB_INTENT_SAVE
        await update.message.reply_text(
            "Detecté un patrón sospechoso en el contenido. "
            "¿Querés procesarlo de todas formas?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
                InlineKeyboardButton("Sí, procesar", callback_data=CB_INTENT_SAVE),
            ]]),
        )
        return

    intents = _detect_manage_keywords(text)
    if intents:
        await update.message.reply_text(
            "¿Qué querés hacer?",
            reply_markup=build_intent_keyboard(intents),
        )
    else:
        await update.message.reply_text(
            "¿Guardar como nota?",
            reply_markup=build_save_keyboard(),
        )


@authorized
async def handle_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler para mensajes de audio y voz."""
    settings: Settings = context.bot_data["settings"]
    msg = update.message

    audio_file = msg.voice or msg.audio
    if not audio_file:
        await msg.reply_text("No se pudo procesar el audio.")
        return

    max_bytes = settings.documents.max_size_mb * 1024 * 1024
    if audio_file.file_size and audio_file.file_size > max_bytes:
        await msg.reply_text(
            f"Audio demasiado grande (máx {settings.documents.max_size_mb}MB)."
        )
        return

    await msg.reply_text("Transcribiendo audio...")

    try:
        import tempfile
        tg_file = await audio_file.get_file()
        suffix = ".ogg" if msg.voice else Path(tg_file.file_path or "audio.ogg").suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        await tg_file.download_to_drive(str(tmp_path))

        text = await transcribe_audio(
            tmp_path,
            model=settings.whisper.model,
            model_dir=settings.whisper.model_dir,
            language=settings.whisper.language,
        )

        if not text.strip():
            await msg.reply_text("No se pudo extraer texto del audio.")
            tmp_path.unlink(missing_ok=True)
            return

        context.user_data["pending_transcript"] = {
            "text": text,
            "temp_path": str(tmp_path),
            "media_type": "audio",
        }

        snippet = text[:500] + ("..." if len(text) > 500 else "")
        sent = await msg.reply_text(
            f"<b>Transcripción:</b>\n\n<code>{_esc(snippet)}</code>",
            reply_markup=build_transcript_keyboard(),
            parse_mode="HTML",
        )
        context.user_data["pending_transcript"]["msg_id"] = sent.message_id

    except Exception as e:
        logger.error("Error transcribiendo audio: %s", e)
        await msg.reply_text(f"Error al transcribir: {e}")
        if "tmp_path" in dir():
            tmp_path.unlink(missing_ok=True)


@authorized
async def handle_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler para documentos (PDF, texto, binarios)."""
    settings: Settings = context.bot_data["settings"]
    msg = update.message
    doc = msg.document

    if not doc:
        await msg.reply_text("No se pudo procesar el documento.")
        return

    filename = doc.file_name or "documento"

    max_bytes = settings.documents.max_size_mb * 1024 * 1024
    if doc.file_size and doc.file_size > max_bytes:
        await msg.reply_text(
            f"Archivo demasiado grande (máx {settings.documents.max_size_mb}MB)."
        )
        return

    import tempfile
    tg_file = await doc.get_file()
    suffix = Path(filename).suffix or ""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    await tg_file.download_to_drive(str(tmp_path))

    if is_pdf(filename):
        context.user_data["pending_read_status"] = {
            "temp_path": str(tmp_path),
            "original_filename": filename,
            "media_type": "document",
            "user_context": msg.caption or None,
        }
        await msg.reply_text(
            f"PDF recibido: <b>{_esc(filename)}</b>",
            reply_markup=build_read_status_keyboard(),
            parse_mode="HTML",
        )

    elif is_text_file(filename):
        try:
            text = await extract_text_file(tmp_path, max_chars=50000)
            if not text.strip():
                await msg.reply_text("El archivo está vacío.")
                tmp_path.unlink(missing_ok=True)
                return

            context.user_data["pending_extraction"] = {
                "text": text,
                "temp_path": str(tmp_path),
                "original_filename": filename,
                "media_type": "document",
                "metadata": {},
                "user_context": msg.caption or None,
            }

            snippet = text[:500]
            if len(text) > 500:
                snippet += "..."
            await msg.reply_text(
                f"<b>Contenido de {_esc(filename)}:</b>\n\n"
                f"<code>{_esc(snippet)}</code>\n\n"
                "Confirmá para clasificar o mandá texto corregido.",
                reply_markup=build_extraction_keyboard(),
                parse_mode="HTML",
            )

        except Exception as e:
            logger.error("Error leyendo archivo de texto: %s", e)
            await msg.reply_text(f"Error leyendo archivo: {e}")
            tmp_path.unlink(missing_ok=True)

    else:
        context.user_data["pending_description"] = {
            "temp_path": str(tmp_path),
            "original_filename": filename,
            "media_type": "document",
        }
        await msg.reply_text(
            f"Archivo recibido: <b>{_esc(filename)}</b>\n\n"
            "No puedo leer este formato. Describí el contenido para clasificarlo, "
            "o cancelá.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Cancelar", callback_data=CB_EXTRACTION_CANCEL)]
            ]),
            parse_mode="HTML",
        )


async def _process_pdf_after_read_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    read_status: str,
) -> None:
    """Procesa un PDF después de que el usuario seleccionó read_status."""
    pending = context.user_data.pop("pending_read_status", None)
    if not pending:
        return

    query = update.callback_query
    tmp_path = Path(pending["temp_path"])
    filename = pending["original_filename"]

    await query.edit_message_text("Extrayendo texto del PDF...")

    try:
        text, pdf_meta = await extract_pdf(tmp_path)

        if not text.strip():
            context.user_data["pending_description"] = {
                "temp_path": str(tmp_path),
                "original_filename": filename,
                "media_type": "document",
                "read_status": read_status,
                "pdf_metadata": pdf_meta,
            }
            await query.edit_message_text(
                "No pude extraer texto del PDF (puede ser escaneado).\n"
                "Describí el contenido para clasificarlo.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Cancelar", callback_data=CB_EXTRACTION_CANCEL)]
                ]),
            )
            return

        is_paper = detect_paper(text, pdf_meta)

        paper_title: Optional[str] = None
        paper_authors: Optional[str] = None
        paper_doi: Optional[str] = None
        if is_paper:
            sections = extract_paper_sections(text, pdf_meta)
            paper_title = sections["title"] or pdf_meta.get("title") or None
            paper_authors = sections["authors"] or None
            paper_doi = sections["doi"] or pdf_meta.get("doi") or None

        classify_content = build_classify_content(text, pdf_meta, is_paper)

        context.user_data["pending_extraction"] = {
            "text": text,
            "classify_content": classify_content,
            "is_paper": is_paper,
            "paper_title": paper_title,
            "paper_authors": paper_authors,
            "paper_doi": paper_doi,
            "temp_path": str(tmp_path),
            "original_filename": filename,
            "media_type": "document",
            "read_status": read_status,
            "metadata": pdf_meta,
        }

        if is_paper:
            preview_parts = []
            title = paper_title or ""
            if title:
                preview_parts.append(f"<b>{_esc(title)}</b>")
            if sections["abstract"]:
                abstract_snippet = sections["abstract"][:400]
                if len(sections["abstract"]) > 400:
                    abstract_snippet += "..."
                preview_parts.append(f"\n<i>{_esc(abstract_snippet)}</i>")
            preview_text = "\n".join(preview_parts) or "<i>(sin secciones detectadas)</i>"
        else:
            snippet = text[:500]
            if len(text) > 500:
                snippet += "..."
            pages = pdf_meta.get("pages", "?")
            preview_text = f"Páginas: {pages}\n\n<code>{_esc(snippet)}</code>"

        await query.edit_message_text(
            f"<b>PDF extraído:</b>\n\n{preview_text}\n\n"
            "Confirmá para clasificar o mandá texto corregido.",
            reply_markup=build_extraction_keyboard(),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error("Error extrayendo PDF: %s", e)
        await query.edit_message_text(f"Error extrayendo PDF: {e}")
        tmp_path.unlink(missing_ok=True)
