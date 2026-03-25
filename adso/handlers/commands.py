"""Handlers de comandos de Telegram: /start, /status, /clasificar."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from adso.bot_utils import _get_existing_items, _get_existing_tags
from adso.config import Settings
from adso.constants import CB_CLASIFICAR_INBOX
from adso.keyboards import _esc, build_capture_keyboard, build_preview
from adso.llm_client import classify, extract_original_from_degraded
from adso.security import authorized
from adso.vault_search import find_by_property
from adso.vault_writer import GitBackup, read_note

logger = logging.getLogger(__name__)


@authorized
async def handle_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /start."""
    await update.message.reply_text(
        "ADSO activo. Mandame texto y lo clasifico para tu vault."
    )


@authorized
async def handle_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /status — muestra estado del sistema."""
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    total_notes = len(list(vault_path.rglob("*.md")))
    inbox_dir = vault_path / "00-Inbox"
    inbox_count = 0
    pending_auto = 0
    pending_manual = 0
    if inbox_dir.exists():
        for f in inbox_dir.glob("*.md"):
            inbox_count += 1
            try:
                note = await read_note(f)
                if note.frontmatter.get("status") == "pending-classification":
                    if note.frontmatter.get("project") or note.frontmatter.get("area"):
                        pending_auto += 1
                    else:
                        pending_manual += 1
            except Exception:
                pass
    total_pending = pending_auto + pending_manual

    llm_model = "gemini-2.5-flash-lite"

    embeddings = context.bot_data.get("embeddings")
    embeddings_status = "activo" if embeddings else "no iniciado"

    git_backup: Optional[GitBackup] = context.bot_data.get("git_backup")
    backup_status = "activo" if git_backup else "no configurado"

    lines = [
        "<b>ADSO — Estado</b>",
        "",
        f"<b>Modelo LLM:</b> {llm_model}",
        f"<b>Embeddings:</b> {embeddings_status}",
        f"<b>Git backup:</b> {backup_status}",
        "",
        f"<b>Notas en vault:</b> {total_notes}",
        f"<b>En inbox:</b> {inbox_count}",
        "",
        f"<b>Vault:</b> <code>{vault_path}</code>",
    ]

    markup = None
    if total_pending > 0:
        lines.append("")
        lines.append(f"⚠️ <b>Inbox pendiente:</b> {total_pending}")
        if pending_auto:
            lines.append(f"  · Con destino asignado: {pending_auto} (el bot las procesa automáticamente)")
        if pending_manual:
            lines.append(f"  · Sin destino: {pending_manual}")
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Clasificar inbox", callback_data=CB_CLASIFICAR_INBOX)]
            ])

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=markup)


@authorized
async def handle_clasificar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /clasificar — procesa notas de Inbox sin destino asignado (Caso B).

    Toma la primera nota pendiente sin project/area, llama al LLM y muestra el
    preview para confirmación del usuario (mismo flujo que captura normal).
    Si hay más notas pendientes, avisa al usuario para que vuelva a invocar el comando.
    """
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    if update.callback_query:
        await update.callback_query.answer()
        reply = update.callback_query.message.reply_text
    else:
        reply = update.message.reply_text

    inbox_notes = await find_by_property(
        "status", "pending-classification", vault_path,
        scope="00-Inbox",
    )

    caso_b: list[tuple] = []
    for ref in inbox_notes:
        try:
            note = await read_note(ref.path)
            fm = note.frontmatter
            if not fm.get("project") and not fm.get("area"):
                caso_b.append((ref, note))
        except Exception as e:
            logger.warning("Error leyendo nota de inbox para /clasificar: %s", e)

    if not caso_b:
        await reply("No hay notas pendientes de clasificar.")
        return

    ref, note = caso_b[0]
    orig_fm = note.frontmatter

    if not note.body or not note.body.strip():
        await reply(f"Nota {ref.path.name} sin contenido, saltando. Volvé a intentar.")
        return

    projects, areas = await _get_existing_items(vault_path)
    existing_tags = await _get_existing_tags(vault_path)

    await reply("Clasificando...")

    result = await classify(
        content=extract_original_from_degraded(note.body),
        media_type=orig_fm.get("media_type", "text"),
        existing_projects=projects,
        existing_areas=areas,
        existing_tags=existing_tags,
        disambiguation_threshold=settings.llm.disambiguation_threshold,
        user_context=orig_fm.get("user_context") or None,
    )

    if result.get("mode") == "degraded":
        await reply("El LLM no está disponible. La nota quedó en Inbox.")
        return

    if result.get("mode") != "capture" or "frontmatter" not in result.get("payload", {}):
        await reply("No se pudo clasificar la nota.")
        return

    payload = result["payload"]
    new_fm = payload["frontmatter"]
    new_fm["date_created"] = orig_fm.get("date_created", "")
    new_fm["source"] = "telegram"
    new_fm["media_type"] = orig_fm.get("media_type", "text")
    new_fm.pop("user_context", None)
    body = payload.get("body", extract_original_from_degraded(note.body))

    context.user_data["pending_note"] = result
    context.user_data["clasificar_inbox_path"] = str(ref.path)

    preview_text = "♻️ <b>Nota de Inbox</b>\n\n" + build_preview(new_fm, body, [])
    has_dest = bool(new_fm.get("project") or new_fm.get("area"))
    keyboard = build_capture_keyboard(new_fm, has_dest)

    await reply(preview_text, reply_markup=keyboard, parse_mode="HTML")

    remaining = len(caso_b) - 1
    if remaining > 0:
        await reply(
            f"Quedan {remaining} nota{'s' if remaining > 1 else ''} más. "
            "Mandá /clasificar para continuar."
        )
