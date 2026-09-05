"""Caracterización del bloque de sugerencia de links en `capture.py` (I5).

El invariante que CLAUDE.md marca como delicado: **el embedding del preview se
reutiliza al indexar solo si el body no cambió**. La regla vivía copiada en tres
flujos con variaciones sutiles, y una cuarta copia (arXiv) que embebe el
*abstract*, no el body — guardar ese vector como `_body_embedding` indexaría la
nota con un embedding que no corresponde a su texto.

Estos tests fijan el comportamiento observable ANTES de extraer el helper, para
que el refactor no pueda cambiarlo sin que algo se ponga en rojo.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adso.embeddings import SimilarNote


def _fake_embeddings(vector: list[float] | None = None) -> MagicMock:
    emb = MagicMock()
    emb.compute_embedding = AsyncMock(return_value=vector or [0.1, 0.2, 0.3])
    emb.query_similar = AsyncMock(return_value=[
        SimilarNote(
            note_id="01-Projects/tesis/otra",
            path="/vault/01-Projects/tesis/otra.md",
            distance=0.2,
            metadata={"title": "Otra nota"},
            snippet=None,
        )
    ])
    return emb


def _capture_result(body: str = "el cuerpo de la nota") -> dict:
    return {
        "mode": "capture",
        "confidence": 0.9,
        "needs_disambiguation": False,
        "payload": {
            "frontmatter": {"title": "Una nota", "type": "reference", "status": "active"},
            "body": body,
        },
    }


class TestClassifyAndPreviewGuardaElEmbedding:
    """El primer flujo embebe el body una vez y lo deja en el payload para que
    `_cb_confirm` no vuelva a llamar a la API."""

    @pytest.mark.asyncio
    async def test_guarda_el_embedding_del_body(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import capture

        emb = _fake_embeddings([1.0, 2.0])
        mock_context.bot_data["embeddings"] = emb
        update = make_update("texto")

        with patch.object(
            capture, "classify", AsyncMock(return_value=_capture_result())
        ):
            await capture._classify_and_preview(
                update, mock_context, "el cuerpo de la nota", media_type="text"
            )

        payload = mock_context.user_data["pending_note"]["payload"]
        assert payload["_body_embedding"] == [1.0, 2.0]

    @pytest.mark.asyncio
    async def test_el_embedding_guardado_es_el_que_se_uso_para_buscar(
        self, mock_context, make_update
    ) -> None:
        """Si se guardara un vector distinto del usado en la query, el reuso
        posterior indexaría la nota con un embedding ajeno."""
        from adso.handlers import capture

        emb = _fake_embeddings([9.9])
        mock_context.bot_data["embeddings"] = emb
        update = make_update("texto")

        with patch.object(
            capture, "classify", AsyncMock(return_value=_capture_result())
        ):
            await capture._classify_and_preview(
                update, mock_context, "el cuerpo de la nota", media_type="text"
            )

        usado = emb.query_similar.await_args.kwargs["query_embedding"]
        guardado = mock_context.user_data["pending_note"]["payload"]["_body_embedding"]
        assert usado == guardado

    @pytest.mark.asyncio
    async def test_los_links_llegan_al_payload(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import capture

        mock_context.bot_data["embeddings"] = _fake_embeddings()
        update = make_update("texto")

        with patch.object(
            capture, "classify", AsyncMock(return_value=_capture_result())
        ):
            await capture._classify_and_preview(
                update, mock_context, "el cuerpo de la nota", media_type="text"
            )

        links = mock_context.user_data["pending_note"]["payload"]["suggested_links"]
        assert links == [{"note_id": "01-Projects/tesis/otra", "title": "Otra nota"}]

    @pytest.mark.asyncio
    async def test_fallo_de_embeddings_no_rompe_el_preview(
        self, mock_context, make_update
    ) -> None:
        """La sugerencia de links es best-effort: si la API falla, la captura
        sigue (perder el preview sería perder la nota)."""
        from adso.handlers import capture

        emb = _fake_embeddings()
        emb.compute_embedding = AsyncMock(side_effect=RuntimeError("API caída"))
        mock_context.bot_data["embeddings"] = emb
        update = make_update("texto")

        with patch.object(
            capture, "classify", AsyncMock(return_value=_capture_result())
        ):
            await capture._classify_and_preview(
                update, mock_context, "el cuerpo de la nota", media_type="text"
            )

        assert mock_context.user_data["pending_note"]["payload"]["suggested_links"] == []
        update.message.reply_text.assert_awaited()

    @pytest.mark.asyncio
    async def test_sin_cliente_de_embeddings_no_explota(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import capture

        mock_context.bot_data["embeddings"] = None
        update = make_update("texto")

        with patch.object(
            capture, "classify", AsyncMock(return_value=_capture_result())
        ):
            await capture._classify_and_preview(
                update, mock_context, "el cuerpo de la nota", media_type="text"
            )

        assert mock_context.user_data["pending_note"]["payload"]["suggested_links"] == []


class TestArxivNoGuardaElEmbeddingDelAbstract:
    """El flujo de arXiv busca links por el **abstract**, pero el body de la nota
    es otro (callout + abstract + Personal Notes). Guardar ese vector como
    `_body_embedding` haría que `_cb_confirm` indexe la nota con el embedding
    del abstract en vez del de su texto real."""

    @pytest.mark.asyncio
    async def test_no_deja_body_embedding_en_el_payload(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import capture

        mock_context.bot_data["embeddings"] = _fake_embeddings()
        update = make_update("https://arxiv.org/abs/2301.12345")

        metadata = {
            "title": "Un paper",
            "authors": ["A. Autora"],
            "year": 2023,
            "abstract": "El abstract del paper.",
            "doi": "10.1234/x",
            "keywords": ["ml"],
            "arxiv_id": "2301.12345",
            "source_url": "https://arxiv.org/abs/2301.12345",
        }

        with patch.object(
            capture, "classify", AsyncMock(return_value=_capture_result())
        ):
            await capture._classify_and_preview_arxiv(
                update, mock_context, metadata, "https://arxiv.org/abs/2301.12345"
            )

        payload = mock_context.user_data["pending_note"]["payload"]
        assert "_body_embedding" not in payload, (
            "el vector del abstract no puede viajar como embedding del body"
        )

    @pytest.mark.asyncio
    async def test_los_links_se_buscan_por_el_abstract(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import capture

        emb = _fake_embeddings()
        mock_context.bot_data["embeddings"] = emb
        update = make_update("https://arxiv.org/abs/2301.12345")

        metadata = {
            "title": "Un paper",
            "authors": [],
            "year": 2023,
            "abstract": "El abstract del paper.",
            "doi": "",
            "keywords": [],
            "arxiv_id": "2301.12345",
            "source_url": "https://arxiv.org/abs/2301.12345",
        }

        with patch.object(
            capture, "classify", AsyncMock(return_value=_capture_result())
        ):
            await capture._classify_and_preview_arxiv(
                update, mock_context, metadata, "https://arxiv.org/abs/2301.12345"
            )

        assert emb.query_similar.await_args.kwargs["query_text"] == (
            "El abstract del paper."
        )
