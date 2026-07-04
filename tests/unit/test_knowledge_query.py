"""Tests para adso.knowledge_query — retrieval semántico (Fase 7.0)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from adso.embeddings import SimilarNote
from adso.knowledge_query import ScoredNote, retrieve


def _hit(note_id: str, distance: float, title: str = "T", project: str = "") -> SimilarNote:
    return SimilarNote(
        note_id=note_id,
        path=f"01-Projects/tesis/{note_id}.md",
        distance=distance,
        metadata={"title": title, "status": "active", "project": project},
        snippet="un fragmento",
    )


def _embeddings(hits_by_call):
    """Mock de EmbeddingsClient.query_similar con respuestas secuenciales."""
    emb = AsyncMock()
    emb.query_similar = AsyncMock(side_effect=hits_by_call)
    return emb


class TestRetrieve:

    @pytest.mark.asyncio
    async def test_returns_scored_notes(self) -> None:
        emb = _embeddings([[_hit("a", 0.2, "Nota A"), _hit("b", 0.5, "Nota B")]])
        result = await retrieve("query", Path("/vault"), emb, threshold=0.75)

        assert len(result.notes) == 2
        assert result.notes[0].title == "Nota A"
        assert isinstance(result.notes[0], ScoredNote)
        # distancia 0.2 → similitud 1 - 0.2/2 = 0.9
        assert result.notes[0].similarity == 0.9
        assert result.notes[0].path == Path("/vault/01-Projects/tesis/a.md")
        assert result.below_threshold is False

    @pytest.mark.asyncio
    async def test_empty_falls_back_below_threshold(self) -> None:
        # Primera llamada (con umbral): vacía. Segunda (sin umbral): trae hits.
        emb = _embeddings([[], [_hit("c", 1.0, "Lejana")]])
        result = await retrieve("query", Path("/vault"), emb, threshold=0.75)

        assert result.below_threshold is True
        assert len(result.notes) == 1
        assert result.notes[0].title == "Lejana"
        assert emb.query_similar.await_count == 2

    @pytest.mark.asyncio
    async def test_truly_empty_stays_empty(self) -> None:
        # Ni con umbral ni sin umbral hay nada.
        emb = _embeddings([[], []])
        result = await retrieve("query", Path("/vault"), emb, threshold=0.75)

        assert result.notes == []
        assert result.below_threshold is False

    @pytest.mark.asyncio
    async def test_scope_passed_as_where(self) -> None:
        emb = _embeddings([[_hit("a", 0.1)]])
        scope = {"project": "tesis"}
        await retrieve("q", Path("/vault"), emb, scope=scope, threshold=0.75)

        _, kwargs = emb.query_similar.call_args
        assert kwargs["where"] == scope

    @pytest.mark.asyncio
    async def test_no_fallback_when_threshold_none(self) -> None:
        # Sin umbral no hay segunda llamada de fallback.
        emb = _embeddings([[]])
        result = await retrieve("q", Path("/vault"), emb, threshold=None)

        assert result.notes == []
        assert result.below_threshold is False
        assert emb.query_similar.await_count == 1
