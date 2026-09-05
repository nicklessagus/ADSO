"""Handlers de comandos de Telegram: /start, /status, /clasificar, /reporte, /reset, /help."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from adso import __version__ as ADSO_VERSION
from adso.bot_utils import (
    _cleanup_pending,
    _get_existing_items,
    _get_existing_tags,
    _has_pending_keyboard,
    _is_awaiting_text_input,
    has_destination,
    reply_blocked,
)
from adso.config import GEMINI_MODEL, GEMINI_VISION_MODEL, Settings
from adso.constants import CB_CLASIFICAR_INBOX
from adso.handlers.capture import (
    _redirect_unimplemented_mode,
    _remember_preview_msg,
    inherit_inbox_frontmatter,
)
from adso.keyboards import build_capture_keyboard, build_preview
from adso.llm_client import classify, extract_original_from_degraded
from adso import vault_cache
from adso.security import authorized
from adso.vault_search import find_by_property
from adso.vault_watcher import VaultWatcher, WatcherStats
from adso.vault_writer import GitBackup, read_note

logger = logging.getLogger(__name__)


_HELP_TEXT = """\
<b>Comandos disponibles</b>

/reporte — Generar un reporte del vault (proyecto, área, ideas, salud, cola de lectura)
/reporte_full — Igual a /reporte pero incluye el contenido completo de cada nota
/clasificar — Clasificar notas de Inbox sin destino asignado
/buscar &lt;consulta&gt; — Buscar notas del vault por similitud semántica
/status — Estado del sistema (vault, embeddings, inbox)
/reset — Cancelar cualquier operación pendiente y volver al estado inicial
/start — Verificar que el bot está activo
/help — Mostrar este mensaje
"""


@authorized
async def handle_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /help."""
    await update.message.reply_text(_HELP_TEXT, parse_mode="HTML")


@authorized
async def handle_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /start."""
    await update.message.reply_text(
        "ADSO activo. Enviar texto, audio, imágenes, PDFs o links y se clasifican para el vault. /help para ver los comandos."
    )


@authorized
async def handle_reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /reset — cancela cualquier operación pendiente y limpia el estado.

    Funciona en cualquier momento, incluso durante correcciones o teclados pendientes.
    No requiere confirmación. No escribe ni borra nada del vault.
    """
    _cleanup_pending(context)
    await update.message.reply_text("Estado reiniciado. Listo para nueva captura.")


def _format_watcher_status(watcher: Optional[VaultWatcher]) -> list[str]:
    """Genera las líneas de estado del VaultWatcher para /status."""
    if watcher is None:
        return ["<b>Watcher vault:</b> no iniciado"]

    stats: WatcherStats = watcher.stats
    label = "activo · debug" if stats.debug else "activo"
    lines = [f"<b>Watcher vault:</b> {label}"]

    if stats.last_event_at is None:
        lines.append("  Sin eventos desde el inicio")
    else:
        ts = stats.last_event_at.strftime("%H:%M")
        lines.append(f"  Último evento: {ts}")
        if stats.conflicts_detected:
            ultimo = stats.last_conflict_at.strftime("%H:%M") if stats.last_conflict_at else "?"
            lines.append(f"  Conflictos detectados: {stats.conflicts_detected} (último: {ultimo})")
        if stats.debug and stats.changes_detected:
            lines.append(f"  Cambios externos: {stats.changes_detected}")
        if stats.debug and stats.deletions_detected:
            lines.append(f"  Borrados externos: {stats.deletions_detected}")

    return lines


def _gather_vault_counts(vault_path: Path) -> tuple[int, int, int, int]:
    """Cuenta notas totales y del inbox (con desglose de pendientes).

    Corre bajo ``asyncio.to_thread``: el rglob del vault y el parseo de las notas
    del inbox son I/O bloqueante y en la RPi4 con SD lenta congelarían el event
    loop. Usa ``parse_cached`` para reutilizar el caché de parsing en vez de
    releer cada nota.

    Returns:
        Tupla ``(total_notes, inbox_count, pending_auto, pending_manual)``.
    """
    total_notes = sum(1 for _ in vault_path.rglob("*.md"))
    inbox_dir = vault_path / "00-Inbox"
    inbox_count = pending_auto = pending_manual = 0
    if inbox_dir.exists():
        for f in inbox_dir.glob("*.md"):
            inbox_count += 1
            note = vault_cache.parse_cached(f)
            if note is not None and note.frontmatter.get("status") == "pending-classification":
                if has_destination(note.frontmatter):
                    pending_auto += 1
                else:
                    pending_manual += 1
    return total_notes, inbox_count, pending_auto, pending_manual


@authorized
async def handle_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler de /status — muestra estado del sistema."""
    if _is_awaiting_text_input(context):
        await update.message.reply_text("Hay una corrección pendiente. Escribir el texto primero.")
        return
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    total_notes, inbox_count, pending_auto, pending_manual = await asyncio.to_thread(
        _gather_vault_counts, vault_path
    )
    total_pending = pending_auto + pending_manual

    embeddings = context.bot_data.get("embeddings")
    embeddings_status = "activo" if embeddings else "no iniciado"

    git_backup: Optional[GitBackup] = context.bot_data.get("git_backup")
    backup_status = "activo" if git_backup else "no configurado"

    watcher: Optional[VaultWatcher] = context.bot_data.get("vault_watcher")
    watcher_lines = _format_watcher_status(watcher)

    cache_stats = vault_cache.stats()

    lines = [
        f"<b>ADSO v{ADSO_VERSION} — Estado</b>",
        "",
        f"<b>Modelo LLM:</b> {GEMINI_MODEL}",
        f"<b>Modelo Vision:</b> {GEMINI_VISION_MODEL}",
        f"<b>Embeddings:</b> {embeddings_status}",
        f"<b>Git backup:</b> {backup_status}",
        *watcher_lines,
        "",
        f"<b>Notas en vault:</b> {total_notes}",
        f"<b>En inbox:</b> {inbox_count}",
        f"<b>Caché de notas:</b> {cache_stats['entries']} entradas · "
        f"{cache_stats['hit_ratio']:.0%} hit ratio",
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

    Guard: bloquea si hay corrección de texto pendiente.

    Toma la primera nota pendiente sin project/area, llama al LLM y muestra el
    preview para confirmación del usuario (mismo flujo que captura normal).
    Si hay más notas pendientes, avisa al usuario para que vuelva a invocar el comando.
    """
    settings: Settings = context.bot_data["settings"]
    vault_path = settings.vault_path

    # `reply` se resuelve ANTES de los dos guards: invocado via
    # CB_CLASIFICAR_INBOX, `update.message` es None y ambos guards lo usaban
    # directamente → AttributeError → error handler global con mensaje
    # genérico, justo al apretar un botón del propio bot. E7 de
    # docs/audit-2026-07-31.md.
    if update.callback_query:
        await update.callback_query.answer()
        reply = update.callback_query.message.reply_text
    else:
        reply = update.message.reply_text

    if _is_awaiting_text_input(context):
        await reply("Hay una corrección pendiente. Escribir el texto primero.")
        return

    if _has_pending_keyboard(context):
        await reply_blocked(context, reply, update.effective_message)
        return

    inbox_notes = await find_by_property(
        "status", "pending-classification", vault_path,
        scope="00-Inbox",
    )

    caso_b: list[tuple] = []
    for ref in inbox_notes:
        try:
            note = await read_note(ref.path)
            if not has_destination(note.frontmatter):
                caso_b.append((ref, note))
        except Exception as e:
            logger.warning("Error leyendo nota de inbox para /clasificar: %s", e)

    if not caso_b:
        await reply("No hay notas pendientes de clasificar.")
        return

    # Se busca la primera nota CON contenido. Antes se tomaba `caso_b[0]` y, si
    # estaba vacía, se hacía `return` — el mensaje decía "saltando" pero no
    # saltaba: la misma nota vacía se elegía en cada invocación y el resto de la
    # cola quedaba inalcanzable para siempre. E8 de docs/audit-2026-07-31.md
    # (`_reclassify_inbox_impl` ya usaba `continue`).
    vacias: list[str] = []
    for ref, note in caso_b:
        if note.body and note.body.strip():
            break
        vacias.append(ref.path.name)
    else:
        await reply(
            "Las notas pendientes no tienen contenido: "
            f"{', '.join(vacias)}. Revisar en el vault."
        )
        return

    orig_fm = note.frontmatter

    if vacias:
        await reply(f"Saltando {len(vacias)} nota(s) sin contenido: {', '.join(vacias)}.")

    projects, areas = await _get_existing_items(vault_path)
    existing_tags = await _get_existing_tags(vault_path)

    await reply("Clasificando...")

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
        await reply("El LLM no está disponible. La nota quedó en Inbox.")
        return

    # Mismo redirect que el flujo interactivo de captura: sin él, una nota cuyo
    # texto parece pregunta recibía "No se pudo clasificar" en cada /clasificar
    # y no había forma de sacarla del Inbox desde el bot.
    mode = _redirect_unimplemented_mode(result, contenido)
    if mode != "capture" or "frontmatter" not in result.get("payload", {}):
        await reply("No se pudo clasificar la nota.")
        return

    payload = result["payload"]
    new_fm = payload["frontmatter"]
    inherit_inbox_frontmatter(new_fm, orig_fm)
    body = contenido
    payload["body"] = body

    context.user_data["pending_note"] = result
    context.user_data["clasificar_inbox_path"] = str(ref.path)

    preview_text = "♻️ <b>Nota de Inbox</b>\n\n" + build_preview(new_fm, body, [])
    keyboard = build_capture_keyboard()

    sent = await reply(preview_text, reply_markup=keyboard, parse_mode="HTML")
    # El guard G14 de `_cb_confirm` compara contra este `msg_id`: sin
    # registrarlo, un [Confirmar] de un preview viejo sigue pasando. C2 de la
    # auditoría 2026-08.
    _remember_preview_msg(result, sent)
