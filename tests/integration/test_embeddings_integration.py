"""Tests de integración: embeddings + vault filesystem."""

from __future__ import annotations

import pytest
from pathlib import Path

import frontmatter as fm_lib

from adso.embeddings import EmbeddingsClient


FAKE_EMBEDDING_ML = [0.9, 0.1, 0.0] + [0.0] * 765
FAKE_EMBEDDING_COOK = [0.0, 0.1, 0.9] + [0.0] * 765
FAKE_EMBEDDING_ML2 = [0.85, 0.15, 0.0] + [0.0] * 765


def _make_note(vault: Path, subdir: str, name: str, title: str, body: str, **fm_fields) -> Path:
    """Helper: crea una nota .md en vault/subdir/name."""
    d = vault / subdir
    d.mkdir(parents=True, exist_ok=True)
    fm = {"title": title, "type": "note", "status": "active", **fm_fields}
    post = fm_lib.Post(body, **fm)
    path = d / name
    path.write_text(fm_lib.dumps(post), encoding="utf-8")
    return path


@pytest.fixture
def client(tmp_path: Path) -> EmbeddingsClient:
    return EmbeddingsClient(
        chroma_data_dir=tmp_path / "chroma",
        gemini_api_key="fake",
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    for d in ["00-Inbox", "01-Projects", "02-Areas", "03-Resources", "05-Archive"]:
        (v / d).mkdir(parents=True)
    return v


class TestSimilarityOrdering:

    @pytest.mark.asyncio
    async def test_most_similar_first(self, client) -> None:
        """Notas más similares al query deben aparecer primero."""
        embed_map = {
            "ML y deep learning": FAKE_EMBEDDING_ML,
            "Receta de pasta italiana": FAKE_EMBEDDING_COOK,
            "Redes neuronales convolucionales": FAKE_EMBEDDING_ML2,
            "neural networks": FAKE_EMBEDDING_ML,
        }

        async def fake_compute(content):
            return embed_map.get(content, FAKE_EMBEDDING_ML)

        client._compute_embedding = fake_compute
        client._ensure_initialized()

        await client.index_note("ml-note", "ML y deep learning", {"path": "a.md", "type": "note"})
        await client.index_note("cook-note", "Receta de pasta italiana", {"path": "b.md", "type": "note"})
        await client.index_note("cnn-note", "Redes neuronales convolucionales", {"path": "c.md", "type": "note"})

        results = await client.query_similar("neural networks", n_results=3)

        assert len(results) == 3
        # ml-note debería ser más similar que cook-note
        ids = [r.note_id for r in results]
        ml_idx = ids.index("ml-note")
        cook_idx = ids.index("cook-note")
        assert ml_idx < cook_idx


class TestReindexIntegration:

    @pytest.mark.asyncio
    async def test_full_reindex_cycle(self, client, vault) -> None:
        """Reindex completo: notas en disco → ChromaDB, huérfanos borrados."""
        async def fake_compute(content):
            return FAKE_EMBEDDING_ML

        client._compute_embedding = fake_compute
        client._ensure_initialized()

        # Crear notas en vault
        _make_note(vault, "01-Projects/tesis", "2025-01-15-exp.md",
                   "Experimento", "Resultado del experimento con CNN.")
        _make_note(vault, "02-Areas/investigacion", "2025-01-16-idea.md",
                   "Idea", "Nueva idea sobre transformers.", type="idea", status="raw")

        # Pre-indexar un huérfano que ya no existe en disco
        await client.index_note("orphan", "old content", {"path": "deleted.md"})
        assert client.count() == 1

        # Reindex
        stats = await client.reindex_vault(vault)

        assert stats["indexed"] == 2
        assert stats["removed"] == 1  # orphan
        assert stats["errors"] == 0
        assert client.count() == 2

    @pytest.mark.asyncio
    async def test_reindex_handles_malformed_notes(self, client, vault) -> None:
        """Notas sin frontmatter no crashean el reindex."""
        async def fake_compute(content):
            return FAKE_EMBEDDING_ML

        client._compute_embedding = fake_compute

        # Nota válida
        _make_note(vault, "00-Inbox", "good.md", "Good", "Valid content.")

        # Nota sin frontmatter
        (vault / "00-Inbox" / "bad.md").write_text("Just text, no YAML.", encoding="utf-8")

        stats = await client.reindex_vault(vault)
        assert stats["indexed"] == 1  # Solo la buena
        assert stats["errors"] == 0   # La mala se ignora silenciosamente

    @pytest.mark.asyncio
    async def test_reindex_empty_body_skipped(self, client, vault) -> None:
        """Notas con body vacío se saltan."""
        async def fake_compute(content):
            return FAKE_EMBEDDING_ML

        client._compute_embedding = fake_compute

        post = fm_lib.Post("", title="Empty", type="note")
        (vault / "00-Inbox" / "empty.md").write_text(
            fm_lib.dumps(post), encoding="utf-8"
        )

        stats = await client.reindex_vault(vault)
        assert stats["indexed"] == 0


class TestCaptureFlowWithEmbeddings:

    @pytest.mark.asyncio
    async def test_index_after_create(self, client, vault) -> None:
        """Simula el flujo: crear nota → indexar embedding con ID de ruta relativa."""
        import hashlib

        async def fake_compute(content):
            return FAKE_EMBEDDING_ML

        client._compute_embedding = fake_compute

        from adso.vault_writer import create_note

        fm = {"title": "ML Experiment", "type": "note", "project": "tesis",
              "tags": ["ml"], "status": "active"}
        body = "El baseline CNN dio accuracy 0.87."
        path = await create_note(fm, body, vault)

        # ID es ruta relativa sin .md (igual que _index_note_safe)
        rel = path.relative_to(vault)
        note_id = str(rel).replace(".md", "")
        metadata = {
            "path": str(rel),
            "type": fm["type"],
            "tags": fm["tags"],
            "project": fm.get("project", ""),
            "content_hash": hashlib.md5(body.encode()).hexdigest(),
        }
        await client.index_note(note_id, body, metadata)

        assert client.count() == 1

        # Buscar similar
        results = await client.query_similar("CNN accuracy", n_results=5)
        assert len(results) == 1
        assert results[0].note_id == note_id
