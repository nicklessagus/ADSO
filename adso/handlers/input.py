"""Handlers de entrada: texto, audio, documento y procesamiento post-read_status.

Todos los tipos de input convergen aquí antes de pasar al flujo de clasificación.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from adso.bot_utils import _detect_manage_keywords, _has_pending_keyboard, _is_awaiting_text_input
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
    build_fallback_pdf_keyboard,
    build_intent_keyboard,
    build_read_status_keyboard,
    build_save_keyboard,
    build_transcript_keyboard,
)
from adso.llm_client import check_injection_risk
from adso.security import authorized
from adso.transcriber import transcribe_audio


async def _exceeds_size_after_download(
    tmp_path: Path,
    declared_size: Optional[int],
    max_bytes: int,
) -> bool:
    """Aplica el límite de tamaño sobre el archivo ya descargado.

    Telegram puede no informar ``file_size`` (None) — en ese caso el pre-check
    antes de descargar se saltea y el límite debe verificarse acá. Si el
    archivo excede el límite, se borra el temporal y retorna True.
    """
    if declared_size:
        return False  # ya validado contra max_bytes antes de descargar
    size = await asyncio.to_thread(lambda: tmp_path.stat().st_size)
    if size > max_bytes:
        tmp_path.unlink(missing_ok=True)
        return True
    return False

logger = logging.getLogger(__name__)


@authorized
async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler principal de mensajes de texto."""
    from adso.handlers.capture import _classify_and_preview
    from adso.handlers.manage import _handle_manage_missing_fields

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

    # Extracción pendiente esperando corrección inline
    if context.user_data.get("pending_extraction", {}).get("awaiting_correction"):
        pe = context.user_data["pending_extraction"]
        pe["text"] = text
        pe["awaiting_correction"] = False
        pe.pop("classify_content", None)
        snippet = text[:500] + ("..." if len(text) > 500 else "")
        msg_id = pe.get("msg_id")
        if msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=update.message.chat_id,
                    message_id=msg_id,
                    text=f"<b>Texto corregido:</b>\n\n<code>{_esc(snippet)}</code>",
                    reply_markup=build_extraction_keyboard(),
                    parse_mode="HTML",
                )
                await update.message.delete()
            except Exception:
                pass
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
            preserve_body=True,
        )
        return

    # Campos faltantes de operación manage
    if context.user_data.get("manage_missing_fields") and context.user_data.get("pending_operation"):
        await _handle_manage_missing_fields(update, context, text)
        return

    # Corrección de preview pendiente (pending_note)
    if context.user_data.get("pending_note"):
        pn = context.user_data["pending_note"]
        if pn.get("awaiting_correction"):
            from adso.handlers.capture import _handle_text_correction
            await _handle_text_correction(
                update, context, text, pn,
                locked_msg_id=pn.get("msg_id"),
            )
        else:
            ids = context.user_data.setdefault("block_msg_ids", [])
            ids.append(update.message.message_id)
            sent = await update.message.reply_text(
                "Usar botón Corregir para modificar."
            )
            ids.append(sent.message_id)
        return

    # Bloquear si hay cualquier teclado pendiente de resolución
    if _has_pending_keyboard(context):
        ids = context.user_data.setdefault("block_msg_ids", [])
        ids.append(update.message.message_id)  # mensaje del usuario
        sent = await update.message.reply_text(
            "Hay una acción pendiente. Resolver los botones antes de continuar."
        )
        ids.append(sent.message_id)  # respuesta del bot
        return

    # Detectar URL de arXiv antes del flujo genérico
    from adso.arxiv_client import extract_arxiv_id
    arxiv_id = extract_arxiv_id(text)
    if arxiv_id:
        context.user_data["pending_raw_content"] = text.strip()
        await _handle_arxiv(update, context, text.strip(), arxiv_id)
        return

    # Nuevo contenido
    context.user_data["pending_raw_content"] = text

    if check_injection_risk(text):
        logger.warning("Patrón de inyección detectado en mensaje")
        from adso.constants import CB_CANCEL, CB_INTENT_NOTE, CB_INTENT_TASK
        await update.message.reply_text(
            "Contenido con patrón sospechoso. ¿Guardar de todas formas?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Cancelar", callback_data=CB_CANCEL),
                InlineKeyboardButton("Tarea", callback_data=CB_INTENT_TASK),
                InlineKeyboardButton("Nota", callback_data=CB_INTENT_NOTE),
            ]]),
        )
        return

    intents = _detect_manage_keywords(text)
    if intents:
        await update.message.reply_text(
            "¿Qué hacer?",
            reply_markup=build_intent_keyboard(intents),
        )
    else:
        await update.message.reply_text(
            "¿Guardar como tarea o como nota?",
            reply_markup=build_save_keyboard(),
        )


async def _handle_arxiv(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    arxiv_id: str,
) -> None:
    """Handler para links de arXiv: obtiene metadata via API y muestra preview de paper.

    Si la API de arXiv falla, informa al usuario y ofrece guardar el link como
    nota genérica con el teclado estándar.

    Args:
        update: Telegram update.
        context: Bot context.
        url: URL original enviada por el usuario.
        arxiv_id: ID de arXiv extraído de la URL (ej: "2301.12345").
    """
    from adso.arxiv_client import fetch_arxiv_metadata
    from adso.handlers.capture import _classify_and_preview_arxiv

    msg = update.message
    status_msg = await msg.reply_text(f"Obteniendo metadata de arXiv ({arxiv_id})...")

    try:
        metadata = await fetch_arxiv_metadata(arxiv_id)
    except Exception as e:
        logger.warning("Error consultando arXiv API para %s: %s", arxiv_id, e)
        await status_msg.edit_text(
            "No se pudo obtener la metadata de arXiv. "
            "¿Guardar el link como nota genérica?",
            reply_markup=build_save_keyboard(),
        )
        return

    canonical_url = metadata.get("source_url") or url

    # Chequear duplicados en el vault (por source_url y por doi)
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path
    from adso.vault_search import find_by_property
    existing = await find_by_property("source_url", canonical_url, vault_path)
    if not existing and metadata.get("doi"):
        existing = await find_by_property("doi", metadata["doi"], vault_path)

    if existing:
        from adso.keyboards import build_arxiv_duplicate_keyboard
        note = existing[0]
        rel_path = note.path.relative_to(vault_path)
        context.user_data["pending_arxiv"] = {
            "metadata": metadata,
            "url": canonical_url,
        }
        await status_msg.edit_text(
            f"Este paper ya existe en el vault:\n<code>{rel_path}</code>\n\n"
            "¿Crear una nota igual de todas formas?",
            reply_markup=build_arxiv_duplicate_keyboard(),
            parse_mode="HTML",
        )
        context.user_data.pop("pending_raw_content", None)
        return

    await status_msg.edit_text(
        f"<b>{_esc(metadata['title'])}</b>\nClasificando...",
        parse_mode="HTML",
    )

    # Pasar el status_msg para que _classify_and_preview_arxiv lo edite con el preview
    # en vez de enviar un mensaje nuevo.
    await _classify_and_preview_arxiv(update, context, metadata, canonical_url, reply_msg=status_msg)

    # pending_raw_content ya no es necesario: pending_note (seteado por
    # _classify_and_preview_arxiv) se encarga del bloqueo. Si lo dejamos,
    # el card de vista previa que Telegram genera para la URL (que puede
    # llegar como update de foto separado) dispara "Hay una acción pendiente".
    context.user_data.pop("pending_raw_content", None)


@authorized
async def handle_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler para mensajes de audio y voz."""
    settings: Settings = context.bot_data["settings"]
    msg = update.message

    if _has_pending_keyboard(context) or _is_awaiting_text_input(context):
        ids = context.user_data.setdefault("block_msg_ids", [])
        ids.append(msg.message_id)
        sent = await msg.reply_text(
            "Hay una acción pendiente. Resolver los botones antes de continuar."
        )
        ids.append(sent.message_id)
        return

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

        if await _exceeds_size_after_download(tmp_path, audio_file.file_size, max_bytes):
            await msg.reply_text(
                f"Audio demasiado grande (máx {settings.documents.max_size_mb}MB)."
            )
            return

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
        # `pending_transcript` se setea antes del reply que muestra la
        # transcripción; si ese reply falla, el estado quedaba apuntando a un
        # temporal que este mismo except borra — todo bloqueado hasta `/reset`,
        # y un `[Confirmar]` de un teclado fantasma leería un path inexistente.
        # E9 de docs/audit-2026-07-31.md.
        logger.error("Error transcribiendo audio: %s", e)
        context.user_data.pop("pending_transcript", None)
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

    if _has_pending_keyboard(context) or _is_awaiting_text_input(context):
        ids = context.user_data.setdefault("block_msg_ids", [])
        ids.append(msg.message_id)
        sent = await msg.reply_text(
            "Hay una acción pendiente. Resolver los botones antes de continuar."
        )
        ids.append(sent.message_id)
        return

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
    tmp_path: Optional[Path] = None
    transferred = False
    try:
        tg_file = await doc.get_file()
        suffix = Path(filename).suffix or ""
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        await tg_file.download_to_drive(str(tmp_path))

        if await _exceeds_size_after_download(tmp_path, doc.file_size, max_bytes):
            await msg.reply_text(
                f"Archivo demasiado grande (máx {settings.documents.max_size_mb}MB)."
            )
            return

        if is_pdf(filename):
            context.user_data["pending_read_status"] = {
                "temp_path": str(tmp_path),
                "original_filename": filename,
                "media_type": "document",
                "user_context": msg.caption or None,
            }
            transferred = True
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
                    return

                context.user_data["pending_extraction"] = {
                    "text": text,
                    "classify_content": build_classify_content(text, {}, is_paper=False),
                    "temp_path": str(tmp_path),
                    "original_filename": filename,
                    "media_type": "document",
                    "metadata": {},
                    "user_context": msg.caption or None,
                    "preserve_body": True,  # texto plano: body verbatim, LLM solo genera frontmatter
                }
                transferred = True

                snippet = text[:500]
                if len(text) > 500:
                    snippet += "..."
                await msg.reply_text(
                    f"<b>Contenido de {_esc(filename)}:</b>\n\n"
                    f"<code>{_esc(snippet)}</code>\n\n"
                    "Confirmar, o enviar texto corregido.",
                    reply_markup=build_extraction_keyboard(),
                    parse_mode="HTML",
                )

            except Exception as e:
                logger.error("Error leyendo archivo de texto: %s", e)
                await msg.reply_text(f"Error leyendo archivo: {e}")

        else:
            context.user_data["pending_description"] = {
                "temp_path": str(tmp_path),
                "original_filename": filename,
                "media_type": "document",
            }
            transferred = True
            await msg.reply_text(
                f"Archivo recibido: <b>{_esc(filename)}</b>\n\n"
                "Formato no compatible. Describir el contenido para clasificarlo, o cancelar.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Cancelar", callback_data=CB_EXTRACTION_CANCEL)]
                ]),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error("Error procesando documento: %s", e)
        await msg.reply_text(f"Error al procesar documento: {e}")
    finally:
        if tmp_path is not None and not transferred:
            tmp_path.unlink(missing_ok=True)


@authorized
async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler para imágenes. Descarga la foto y ofrece OCR, Gemini Vision o descripción manual."""
    settings: Settings = context.bot_data["settings"]
    msg = update.message

    if _has_pending_keyboard(context) or _is_awaiting_text_input(context):
        ids = context.user_data.setdefault("block_msg_ids", [])
        ids.append(msg.message_id)
        sent = await msg.reply_text(
            "Hay una acción pendiente. Resolver los botones antes de continuar."
        )
        ids.append(sent.message_id)
        return

    photo = msg.photo[-1] if msg.photo else None  # mayor resolución disponible
    if not photo:
        await msg.reply_text("No se pudo procesar la imagen.")
        return

    max_bytes = settings.documents.max_size_mb * 1024 * 1024
    if photo.file_size and photo.file_size > max_bytes:
        await msg.reply_text(
            f"Imagen demasiado grande (máx {settings.documents.max_size_mb}MB)."
        )
        return

    import tempfile
    tg_file = await photo.get_file()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    await tg_file.download_to_drive(str(tmp_path))

    if await _exceeds_size_after_download(tmp_path, photo.file_size, max_bytes):
        await msg.reply_text(
            f"Imagen demasiado grande (máx {settings.documents.max_size_mb}MB)."
        )
        return

    context.user_data["pending_fallback_pdf"] = {
        "temp_path": str(tmp_path),
        "original_filename": f"imagen_{photo.file_unique_id}.jpg",
        "media_type": "image",
        "user_context": msg.caption or None,
    }

    await msg.reply_text(
        "Imagen recibida. ¿Cómo extraer el contenido?",
        reply_markup=build_fallback_pdf_keyboard(),
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
            context.user_data["pending_fallback_pdf"] = {
                "temp_path": str(tmp_path),
                "original_filename": filename,
                "media_type": "document",
                "read_status": read_status,
                "pdf_metadata": pdf_meta,
                # El caption que el usuario escribió junto al PDF. Sin esta
                # línea nunca llegaba al LLM: se guardaba en
                # `pending_read_status` y moría ahí. E1 de
                # docs/audit-2026-07-31.md.
                "user_context": pending.get("user_context"),
            }
            await query.edit_message_text(
                "No se pudo extraer texto del PDF (puede ser escaneado).",
                reply_markup=build_fallback_pdf_keyboard(),
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
            "user_context": pending.get("user_context"),
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
            "Confirmar, o enviar texto corregido.",
            reply_markup=build_extraction_keyboard(),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error("Error extrayendo PDF: %s", e)
        await query.edit_message_text(f"Error extrayendo PDF: {e}")
        tmp_path.unlink(missing_ok=True)
