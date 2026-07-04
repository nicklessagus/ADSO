"""Tests del handler de consultas /buscar (Fase 7.0)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adso.embeddings import SimilarNote
from adso.handlers.query import handle_buscar, cb_query_report


def _hit(note_id: str, distance: float, title: str) -> SimilarNote:
    return SimilarNote(
        note_id=note_id,
        path=f"01-Projects/tesis/{note_id}.md",
        distance=distance,
        metadata={"title": title, "status": "active", "project": "tesis"},
        snippet="fragmento de la nota",
    )


def _status_msg() -> MagicMock:
    """Mensaje de status con métodos async (lo que devuelve reply_text)."""
    msg = MagicMock()
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    return msg


def _prep(mock_context, make_update, query: str, hits):
    """Arma update/context para /buscar con embeddings mockeados."""
    update = make_update(text=f"/buscar {query}")
    update.effective_message = update.message
    update.effective_chat = MagicMock()
    update.effective_chat.id = 42
    status = _status_msg()
    update.message.reply_text = AsyncMock(return_value=status)

    emb = MagicMock()
    emb.compute_embedding = AsyncMock(return_value=[0.1] * 8)
    emb.query_similar = AsyncMock(return_value=hits)
    mock_context.bot_data["embeddings"] = emb
    mock_context.bot = MagicMock()
    mock_context.bot.send_document = AsyncMock()
    mock_context.args = query.split()
    return update, status


@patch("adso.security.ALLOWED_USER_IDS", {42})
class TestHandleBuscar:

    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self, make_update, mock_context) -> None:
        update = make_update(text="/buscar")
        update.message.reply_text = AsyncMock()
        mock_context.args = []

        await handle_buscar(update, mock_context)

        sent = str(update.message.reply_text.call_args)
        assert "/buscar" in sent

    @pytest.mark.asyncio
    async def test_inline_results(self, make_update, mock_context) -> None:
        hits = [_hit("a", 0.2, "Nota Alfa"), _hit("b", 0.4, "Nota Beta")]
        update, status = _prep(mock_context, make_update, "exoplanetas", hits)

        await handle_buscar(update, mock_context)

        text = str(status.edit_text.call_args)
        assert "Nota Alfa" in text and "Nota Beta" in text
        # guardó el resultado para el botón de informe
        assert "pending_query" in mock_context.user_data

    @pytest.mark.asyncio
    async def test_no_results(self, make_update, mock_context) -> None:
        update, status = _prep(mock_context, make_update, "nada de nada", [])

        await handle_buscar(update, mock_context)

        assert "No se encontró nada" in str(status.edit_text.call_args)
        assert "pending_query" not in mock_context.user_data

    @pytest.mark.asyncio
    async def test_many_results_sends_report(self, make_update, mock_context) -> None:
        hits = [_hit(f"n{i}", 0.1 + i * 0.05, f"Nota {i}") for i in range(6)]
        update, status = _prep(mock_context, make_update, "redes neuronales", hits)

        await handle_buscar(update, mock_context)

        # >3 resultados → informe .md como documento
        mock_context.bot.send_document.assert_awaited_once()
        _, kwargs = mock_context.bot.send_document.call_args
        assert kwargs["filename"] == "consulta.md"


class TestCbQueryReport:

    @pytest.mark.asyncio
    async def test_report_from_pending(self, mock_context) -> None:
        from adso.knowledge_query import QueryResult, ScoredNote

        vault_path = mock_context.bot_data["settings"].vault_path
        note_path = vault_path / "01-Projects" / "tesis" / "a.md"
        mock_context.user_data["pending_query"] = QueryResult(
            query="q",
            notes=[ScoredNote("a", note_path, "A", "snip", 0.9)],
        )
        mock_context.bot = MagicMock()
        mock_context.bot.send_document = AsyncMock()
        query = MagicMock()
        query.message.chat_id = 42
        query.answer = AsyncMock()

        await cb_query_report(query, mock_context)

        mock_context.bot.send_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_expired(self, mock_context) -> None:
        query = MagicMock()
        query.answer = AsyncMock()

        await cb_query_report(query, mock_context)

        query.answer.assert_awaited_once()
        assert "expiró" in str(query.answer.call_args)
