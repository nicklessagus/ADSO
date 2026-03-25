"""Dispatcher central de inline keyboard callbacks.

Solo routing: despacha a los handlers específicos según callback_data.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from adso.bot_utils import _has_destination
from adso.config import Settings
from adso.constants import (
    CB_BACK,
    CB_CANCEL,
    CB_CHOOSE_AREA,
    CB_CHOOSE_PROJECT,
    CB_CLASIFICAR_INBOX,
    CB_CONFIRM,
    CB_CORRECT,
    CB_DEST_AREA_PREFIX,
    CB_DEST_INBOX,
    CB_DEST_PROJECT_PREFIX,
    CB_DISAMBIG_CAPTURE,
    CB_DISAMBIG_QUERY,
    CB_EXTRACTION_CANCEL,
    CB_EXTRACTION_OK,
    CB_INTENT_CREATE_AREA,
    CB_INTENT_CREATE_PROJECT,
    CB_INTENT_SAVE,
    CB_MANAGE_CANCEL,
    CB_MANAGE_CONFIRM,
    CB_READ_STATUS_READ,
    CB_READ_STATUS_UNREAD,
    CB_TRANSCRIPT_CANCEL,
    CB_TRANSCRIPT_CORRECT,
    CB_TRANSCRIPT_OK,
)
from adso.handlers.capture import (
    _cb_cancel,
    _cb_confirm,
    _cb_correct,
    _cb_dest,
    _cb_extraction_ok,
    _cb_transcript_ok,
    _handle_capture_from_callback,
)
from adso.bot_utils import _cleanup_pending
from adso.handlers.commands import handle_clasificar
from adso.handlers.input import _process_pdf_after_read_status
from adso.handlers.manage import _cb_intent_create, _cb_intent_save, _cb_manage_confirm
from adso.keyboards import (
    _esc,
    build_area_selector,
    build_capture_keyboard,
    build_project_selector,
)
from adso.security import authorized

logger = logging.getLogger(__name__)


@authorized
async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    if data == CB_CONFIRM:
        try:
            await _cb_confirm(query, context, vault_path)
        except Exception as e:
            logger.exception("Error en _cb_confirm: %s", e)
            await query.edit_message_text(f"Error al guardar: {e}")

    elif data == CB_CANCEL:
        await _cb_cancel(query, context)

    elif data == CB_CORRECT:
        await _cb_correct(query, context, vault_path)

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
        await query.edit_message_text("Modo consulta disponible en próxima versión.")
        context.user_data.pop("pending_note", None)

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
            snippet = pt["text"][:500] + ("..." if len(pt["text"]) > 500 else "")
            await query.edit_message_text(
                f"<b>Transcripción actual:</b>\n\n<code>{_esc(snippet)}</code>\n\n"
                "Enviá el texto corregido:",
                parse_mode="HTML",
            )

    elif data == CB_TRANSCRIPT_CANCEL:
        _cleanup_pending(context, "pending_transcript")
        await query.edit_message_text("Transcripción cancelada.")

    elif data == CB_READ_STATUS_READ:
        await _process_pdf_after_read_status(update, context, "read")

    elif data == CB_READ_STATUS_UNREAD:
        await _process_pdf_after_read_status(update, context, "unread")

    elif data == CB_EXTRACTION_OK:
        await _cb_extraction_ok(update, context)

    elif data == CB_EXTRACTION_CANCEL:
        _cleanup_pending(context, "pending_extraction", "pending_description")
        await query.edit_message_text("Cancelado.")

    elif data == CB_CLASIFICAR_INBOX:
        await handle_clasificar(update, context)
