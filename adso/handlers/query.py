"""Handler de consultas al vault (Fase 7.0 — retrieval puro).

Comando /buscar y el flujo de resultados. Recupera notas por similitud semántica
(vía knowledge_query) y las presenta: inline para pocas, informe .md para muchas.
No sintetiza ni razona — solo recupera y presenta (Fase 7.2 agrega síntesis).

Referencia: docs/fase7-rag-design.md
"""

from __future__ import annotations

import io
import logging
from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from adso.config import Settings
from adso.constants import CB_QUERY_REPORT
from adso.embeddings import EmbeddingsClient
from adso.keyboards import _esc
from adso.knowledge_query import QueryResult, retrieve
from adso.reporters import _obsidian_link, _report_header
from adso.security import authorized

logger = logging.getLogger(__name__)

# Umbral de presentación: hasta este número se muestra inline; más → informe .md.
_INLINE_MAX = 3


@authorized
async def handle_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /buscar <consulta>: retrieval semántico sobre el vault."""
    query_text = " ".join(context.args).strip() if context.args else ""
    if not query_text:
        await update.message.reply_text(
            "Uso: <code>/buscar &lt;qué buscás&gt;</code>\n"
            "Ej: <code>/buscar métodos de detección de exoplanetas</code>",
            parse_mode="HTML",
        )
        return
    await run_query(update, context, query_text)


async def run_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query_text: str,
) -> None:
    """Ejecuta la consulta y presenta resultados. Reutilizable (comando o botón)."""
    settings: Settings = context.bot_data["settings"]
    embeddings: Optional[EmbeddingsClient] = context.bot_data.get("embeddings")
    reply = update.effective_message.reply_text

    if embeddings is None:
        await reply("El índice semántico no está disponible.")
        return

    status_msg = await reply(f"🔎 Buscando: <i>{_esc(query_text)}</i>…", parse_mode="HTML")

    try:
        result = await retrieve(
            query=query_text,
            vault_path=settings.vault_path,
            embeddings=embeddings,
            threshold=settings.rag.similarity_threshold,
            max_results=settings.rag.max_results,
        )
    except Exception as e:
        logger.exception("Error en consulta '%s': %s", query_text, e)
        await status_msg.edit_text(f"Error al buscar: {_esc(str(e))}")
        return

    if not result.notes:
        await status_msg.edit_text(
            f"No encontré nada en el vault sobre <i>{_esc(query_text)}</i>.",
            parse_mode="HTML",
        )
        return

    # Guardar el resultado para el botón de informe (evita re-consultar).
    context.user_data["pending_query"] = result

    if len(result.notes) <= _INLINE_MAX:
        await status_msg.edit_text(
            _format_inline(result),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Generar informe .md", callback_data=CB_QUERY_REPORT),
            ]]),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await _send_report(update, context, result)
        try:
            await status_msg.delete()
        except Exception:
            pass


def _scope_label(note) -> str:
    """Etiqueta corta de ubicación: proyecto o área."""
    if note.project:
        return f"📁 {note.project}"
    if note.area:
        return f"🗂 {note.area}"
    return "Inbox"


def _format_inline(result: QueryResult) -> str:
    """Arma el texto inline (HTML) para pocos resultados."""
    lines = [f"🔎 <b>{_esc(result.query)}</b>"]
    if result.below_threshold:
        lines.append("<i>(nada superó el umbral — mostrando lo más cercano, baja confianza)</i>")
    lines.append("")
    for i, n in enumerate(result.notes, 1):
        sim = f"{round(n.similarity * 100)}%"
        head = f"{i}. <b>{_esc(n.title)}</b> · {_esc(_scope_label(n))}"
        if n.status:
            head += f" · {_esc(n.status)}"
        head += f" · {sim}"
        lines.append(head)
        if n.snippet:
            snippet = n.snippet.strip().replace("\n", " ")
            if len(snippet) > 160:
                snippet = snippet[:160] + "…"
            lines.append(f"<i>{_esc(snippet)}</i>")
        lines.append("")
    return "\n".join(lines).strip()


def _build_report(result: QueryResult, vault_path) -> bytes:
    """Construye el informe .md (bytes) con header estándar y links obsidian://."""
    lines = [_report_header(f"Consulta: {result.query}")]
    if result.below_threshold:
        lines.append(
            "> [!warning] Baja confianza\n"
            "> Ningún resultado superó el umbral de similitud. "
            "Se muestran las notas más cercanas.\n"
        )
    lines.append(f"## Resultados ({len(result.notes)})\n")
    for i, n in enumerate(result.notes, 1):
        loc = n.project or n.area or "Inbox"
        lines.append(f"### {i}. {n.title}")
        meta = f"- **Similitud:** {round(n.similarity * 100)}%  |  **Ubicación:** {loc}"
        if n.status:
            meta += f"  |  **Estado:** {n.status}"
        lines.append(meta)
        if n.snippet:
            lines.append(f"\n> {n.snippet.strip()}\n")
        lines.append(f"- [Abrir en Obsidian]({_obsidian_link(vault_path, n.path)})\n")
    return "\n".join(lines).encode("utf-8")


async def _send_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: QueryResult,
) -> None:
    """Envía el informe .md de una consulta como documento."""
    settings: Settings = context.bot_data["settings"]
    report_bytes = _build_report(result, settings.vault_path)
    doc = io.BytesIO(report_bytes)
    doc.name = "consulta.md"
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=doc,
        filename="consulta.md",
    )


async def cb_query_report(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback [Generar informe .md]: envía el informe de la última consulta."""
    result: Optional[QueryResult] = context.user_data.get("pending_query")
    if not result:
        await query.answer("La consulta expiró.", show_alert=True)
        return
    settings: Settings = context.bot_data["settings"]
    report_bytes = _build_report(result, settings.vault_path)
    doc = io.BytesIO(report_bytes)
    doc.name = "consulta.md"
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=doc,
        filename="consulta.md",
    )
    await query.answer("Informe generado.")
