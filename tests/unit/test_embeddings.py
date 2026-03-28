"""Tests unitarios para adso.embeddings — ChromaDB + Gemini Embedding API."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from adso.embeddings import (
    EmbeddingsClient,
    SimilarNote,
    _serialize_metadata,
    _deserialize_tags,
    similarity_to_distance,
    distance_to_similarity,
)


# ---------------------------------------------------------------------------
# Tests de utilidades
# ---------------------------------------------------------------------------


class TestSerializeMetadata:

    def test_none_to_empty_string(self) -> None:
        result = _serialize_metadata({"project": None})
        assert result["project"] == ""

    def test_list_to_comma_separated(self) -> None:
        result = _serialize_metadata({"tags": ["ml", "cnn", "paper"]})
        assert result["tags"] == "ml,cnn,paper"

    def test_string_passthrough(self) -> None:
        result = _serialize_metadata({"type": "reference"})
        assert result["type"] == "reference"

    def test_int_passthrough(self) -> None:
        result = _serialize_metadata({"count": 42})
        assert result["count"] == 42

    def test_bool_passthrough(self) -> None:
        result = _serialize_metadata({"active": True})
        assert result["active"] is True

    def test_other_types_to_str(self) -> None:
        result = _serialize_metadata({"path": Path("/vault/note.md")})
        assert isinstance(result["path"], str)

    def test_empty_list(self) -> None:
        result = _serialize_metadata({"tags": []})
        assert result["tags"] == ""

    def test_mixed_metadata(self) -> None:
        result = _serialize_metadata({
            "type": "reference",
            "tags": ["ml", "cnn"],
            "project": None,
            "status": "active",
        })
        assert result == {
            "type": "reference",
            "tags": "ml,cnn",
            "project": "",
            "status": "active",
        }


class TestDeserializeTags:

    def test_normal_tags(self) -> None:
        assert _deserialize_tags("ml,cnn,paper") == ["ml", "cnn", "paper"]

    def test_empty_string(self) -> None:
        assert _deserialize_tags("") == []

    def test_with_spaces(self) -> None:
        assert _deserialize_tags("ml, cnn , paper") == ["ml", "cnn", "paper"]


class TestConversions:

    def test_similarity_to_distance(self) -> None:
        assert similarity_to_distance(1.0) == pytest.approx(0.0)
        assert similarity_to_distance(0.0) == pytest.approx(2.0)
        assert similarity_to_distance(0.5) == pytest.approx(1.0)
        assert similarity_to_distance(0.82) == pytest.approx(0.36)

    def test_distance_to_similarity(self) -> None:
        assert distance_to_similarity(0.0) == pytest.approx(1.0)
        assert distance_to_similarity(2.0) == pytest.approx(0.0)
        assert distance_to_similarity(1.0) == pytest.approx(0.5)

    def test_roundtrip(self) -> None:
        for sim in [0.0, 0.25, 0.5, 0.75, 0.82, 1.0]:
            assert distance_to_similarity(similarity_to_distance(sim)) == pytest.approx(sim)


# ---------------------------------------------------------------------------
# Tests de EmbeddingsClient con mocks
# ---------------------------------------------------------------------------

FAKE_EMBEDDING = [0.1] * 768  # Simula un vector de 768 dimensiones


@pytest.fixture
def client(tmp_path: Path) -> EmbeddingsClient:
    """EmbeddingsClient con ChromaDB en tmp_path."""
    return EmbeddingsClient(
        chroma_data_dir=tmp_path / "chroma",
        gemini_api_key="fake-key",
    )


class TestIndexNote:

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_index_stores_in_chromadb(self, mock_embed, client) -> None:
        mock_embed.return_value = FAKE_EMBEDDING

        await client.index_note(
            note_id="2025-01-15-test-note",
            content="Contenido de prueba sobre ML.",
            metadata={"type": "reference", "tags": ["ml"], "project": None},
        )

        assert client.count() == 1

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_upsert_updates_existing(self, mock_embed, client) -> None:
        mock_embed.return_value = FAKE_EMBEDDING

        await client.index_note("note-1", "v1", {"type": "reference"})
        await client.index_note("note-1", "v2", {"type": "task"})

        assert client.count() == 1

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_index_serializes_metadata(self, mock_embed, client) -> None:
        mock_embed.return_value = FAKE_EMBEDDING

        await client.index_note(
            "note-1", "content",
            {"tags": ["ml", "cnn"], "project": None, "status": "active"},
        )

        # Verificar que ChromaDB tiene metadata serializada
        result = client._collection.get(ids=["note-1"], include=["metadatas"])
        meta = result["metadatas"][0]
        assert meta["tags"] == "ml,cnn"
        assert meta["project"] == ""
        assert meta["status"] == "active"


class TestRemoveNote:

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_remove_deletes(self, mock_embed, client) -> None:
        mock_embed.return_value = FAKE_EMBEDDING
        await client.index_note("note-1", "content", {"type": "reference"})

        await client.remove_note("note-1")
        assert client.count() == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent_no_error(self, client) -> None:
        # No debería fallar
        await client.remove_note("nonexistent")


class TestUpdateMetadata:

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_update_changes_metadata(self, mock_embed, client) -> None:
        mock_embed.return_value = FAKE_EMBEDDING
        await client.index_note("note-1", "content", {"type": "reference", "status": "active"})

        await client.update_metadata("note-1", {"type": "reference", "status": "done"})

        result = client._collection.get(ids=["note-1"], include=["metadatas"])
        assert result["metadatas"][0]["status"] == "done"

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_update_does_not_recalculate_embedding(self, mock_embed, client) -> None:
        mock_embed.return_value = FAKE_EMBEDDING
        await client.index_note("note-1", "content", {"type": "reference"})

        mock_embed.reset_mock()
        await client.update_metadata("note-1", {"type": "task"})

        mock_embed.assert_not_called()


class TestQuerySimilar:

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_query_returns_results(self, mock_embed, client) -> None:
        # Indexar con embeddings ligeramente diferentes
        mock_embed.return_value = [0.1] * 768
        await client.index_note("note-1", "ML y redes neuronales", {"type": "reference", "path": "a.md"})

        mock_embed.return_value = [0.9] * 768
        await client.index_note("note-2", "Cocina italiana", {"type": "reference", "path": "b.md"})

        # Query similar a note-1
        mock_embed.return_value = [0.1] * 768
        results = await client.query_similar("machine learning", n_results=5)

        assert len(results) >= 1
        assert isinstance(results[0], SimilarNote)
        assert results[0].note_id in ("note-1", "note-2")

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_query_with_threshold_filters(self, mock_embed, client) -> None:
        mock_embed.return_value = [0.1] * 768
        await client.index_note("note-1", "content", {"type": "reference", "path": "a.md"})

        mock_embed.return_value = [0.1] * 768
        # Threshold muy alto (similitud > 0.99) — solo match casi exacto
        results = await client.query_similar("content", n_results=5, threshold=0.99)
        # Con embedding idéntico, la distancia debería ser ~0 → pasa el threshold
        assert len(results) >= 1

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_query_empty_collection(self, mock_embed, client) -> None:
        mock_embed.return_value = FAKE_EMBEDDING
        results = await client.query_similar("anything")
        assert results == []

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_query_with_where_filter(self, mock_embed, client) -> None:
        mock_embed.return_value = FAKE_EMBEDDING
        await client.index_note("note-1", "ML content", {"type": "reference", "path": "a.md"})
        await client.index_note("task-1", "ML task", {"type": "task", "path": "b.md"})

        results = await client.query_similar(
            "ML", n_results=5, where={"type": "reference"},
        )
        # Solo debería retornar note-1
        note_ids = [r.note_id for r in results]
        assert "note-1" in note_ids
        assert "task-1" not in note_ids

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_query_returns_snippet(self, mock_embed, client) -> None:
        mock_embed.return_value = FAKE_EMBEDDING
        await client.index_note("note-1", "Este es el contenido completo.", {"path": "a.md"})

        results = await client.query_similar("contenido", n_results=1)
        assert results[0].snippet is not None
        assert "contenido" in results[0].snippet


class TestComputeEmbedding:

    @pytest.mark.asyncio
    @patch("adso.embeddings.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_failure(self, mock_sleep, client) -> None:
        """Verifica que reintenta 3 veces ante errores."""
        call_count = [0]

        def fake_embed_content(**kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("API down")
            result = MagicMock()
            result.embeddings = [MagicMock(values=FAKE_EMBEDDING)]
            return result

        mock_client = MagicMock()
        mock_client.models.embed_content = fake_embed_content

        client._ensure_initialized()

        with patch("google.genai.Client", return_value=mock_client):
            await client.index_note("note-1", "content", {"type": "reference"})

        assert call_count[0] == 3
        assert client.count() == 1

    @pytest.mark.asyncio
    @patch("adso.embeddings.asyncio.sleep", new_callable=AsyncMock)
    async def test_fails_after_3_retries(self, mock_sleep, client) -> None:
        """Si falla 3 veces, propaga el error."""
        mock_client = MagicMock()
        mock_client.models.embed_content.side_effect = ConnectionError("API permanently down")

        client._ensure_initialized()

        with patch("google.genai.Client", return_value=mock_client):
            with pytest.raises(ConnectionError):
                await client.index_note("note-1", "content", {"type": "reference"})


class TestReindexVault:

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_reindex_indexes_notes(self, mock_embed, client, tmp_path) -> None:
        mock_embed.return_value = FAKE_EMBEDDING

        # Crear vault con notas
        vault = tmp_path / "vault"
        (vault / "01-Projects" / "tesis").mkdir(parents=True)
        import frontmatter as fm_lib
        post = fm_lib.Post("Contenido sobre ML.", title="Test Note", type="reference")
        (vault / "01-Projects" / "tesis" / "2025-01-15-test.md").write_text(
            fm_lib.dumps(post), encoding="utf-8"
        )

        stats = await client.reindex_vault(vault)
        assert stats["indexed"] == 1
        assert stats["errors"] == 0
        assert client.count() == 1

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_reindex_removes_orphans(self, mock_embed, client, tmp_path) -> None:
        mock_embed.return_value = FAKE_EMBEDDING

        # Indexar una nota manualmente
        await client.index_note("orphan-note", "old content", {"path": "old.md"})
        assert client.count() == 1

        # Reindex con vault vacío → debe borrar el huérfano
        vault = tmp_path / "vault"
        vault.mkdir()
        stats = await client.reindex_vault(vault)
        assert stats["removed"] == 1
        assert client.count() == 0

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_reindex_skips_excluded_dirs(self, mock_embed, client, tmp_path) -> None:
        mock_embed.return_value = FAKE_EMBEDDING

        vault = tmp_path / "vault"
        (vault / "05-Archive").mkdir(parents=True)
        import frontmatter as fm_lib
        post = fm_lib.Post("Archived.", title="Old", type="reference")
        (vault / "05-Archive" / "old.md").write_text(
            fm_lib.dumps(post), encoding="utf-8"
        )

        stats = await client.reindex_vault(vault, exclude_dirs=["05-Archive"])
        assert stats["indexed"] == 0

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_reindex_skips_index_files(self, mock_embed, client, tmp_path) -> None:
        mock_embed.return_value = FAKE_EMBEDDING

        vault = tmp_path / "vault"
        (vault / "01-Projects" / "tesis").mkdir(parents=True)
        import frontmatter as fm_lib
        post = fm_lib.Post("Index.", title="Tesis", type="project-index")
        (vault / "01-Projects" / "tesis" / "_index.md").write_text(
            fm_lib.dumps(post), encoding="utf-8"
        )

        stats = await client.reindex_vault(vault)
        assert stats["indexed"] == 0

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_reindex_id_is_relative_path(self, mock_embed, client, tmp_path) -> None:
        """El ID en ChromaDB debe ser la ruta relativa sin .md, no el stem."""
        mock_embed.return_value = FAKE_EMBEDDING

        vault = tmp_path / "vault"
        (vault / "01-Projects" / "tesis").mkdir(parents=True)
        import frontmatter as fm_lib
        post = fm_lib.Post("Contenido.", title="Nota", type="reference")
        (vault / "01-Projects" / "tesis" / "nota.md").write_text(
            fm_lib.dumps(post), encoding="utf-8"
        )

        await client.reindex_vault(vault)

        all_docs = client._collection.get(include=[])
        assert "01-Projects/tesis/nota" in all_docs["ids"]
        assert "nota" not in all_docs["ids"]

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_reindex_no_id_collision_same_stem(self, mock_embed, client, tmp_path) -> None:
        """Dos notas con el mismo nombre en distintos directorios no se pisan."""
        mock_embed.return_value = FAKE_EMBEDDING

        vault = tmp_path / "vault"
        (vault / "01-Projects" / "tesis").mkdir(parents=True)
        (vault / "02-Areas" / "docencia").mkdir(parents=True)
        import frontmatter as fm_lib

        for subdir in ["01-Projects/tesis", "02-Areas/docencia"]:
            post = fm_lib.Post("Contenido.", title="Metodologia", type="reference")
            (vault / subdir / "metodologia.md").write_text(
                fm_lib.dumps(post), encoding="utf-8"
            )

        stats = await client.reindex_vault(vault)
        assert stats["indexed"] == 2
        assert client.count() == 2

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_reindex_skips_sync_conflict_files(self, mock_embed, client, tmp_path) -> None:
        """Archivos .sync-conflict-* de Syncthing se ignoran."""
        mock_embed.return_value = FAKE_EMBEDDING

        vault = tmp_path / "vault"
        vault.mkdir()
        import frontmatter as fm_lib
        post = fm_lib.Post("Contenido.", title="Conflicto", type="reference")
        (vault / "nota.sync-conflict-20250101-123456-ABC.md").write_text(
            fm_lib.dumps(post), encoding="utf-8"
        )

        stats = await client.reindex_vault(vault)
        assert stats["indexed"] == 0

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_reindex_skips_unchanged_notes(self, mock_embed, client, tmp_path) -> None:
        """Segunda pasada no re-embede notas sin cambios (hash coincide)."""
        mock_embed.return_value = FAKE_EMBEDDING

        vault = tmp_path / "vault"
        vault.mkdir()
        import frontmatter as fm_lib
        post = fm_lib.Post("Contenido estable.", title="Nota", type="reference")
        (vault / "nota.md").write_text(fm_lib.dumps(post), encoding="utf-8")

        stats1 = await client.reindex_vault(vault)
        assert stats1["indexed"] == 1
        assert stats1["skipped"] == 0

        # Segunda pasada: misma nota sin cambios
        stats2 = await client.reindex_vault(vault)
        assert stats2["indexed"] == 0
        assert stats2["skipped"] == 1
        assert mock_embed.call_count == 1  # Solo se llamó en la primera pasada

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_reindex_reembeds_modified_notes(self, mock_embed, client, tmp_path) -> None:
        """Nota modificada se re-embede en el siguiente reindex."""
        mock_embed.return_value = FAKE_EMBEDDING

        vault = tmp_path / "vault"
        vault.mkdir()
        import frontmatter as fm_lib
        note_path = vault / "nota.md"

        post = fm_lib.Post("Contenido original.", title="Nota", type="reference")
        note_path.write_text(fm_lib.dumps(post), encoding="utf-8")

        await client.reindex_vault(vault)
        assert mock_embed.call_count == 1

        # Modificar la nota
        post2 = fm_lib.Post("Contenido modificado.", title="Nota", type="reference")
        note_path.write_text(fm_lib.dumps(post2), encoding="utf-8")

        stats = await client.reindex_vault(vault)
        assert stats["indexed"] == 1
        assert stats["skipped"] == 0
        assert mock_embed.call_count == 2  # Re-embede la nota modificada

    @pytest.mark.asyncio
    @patch("adso.embeddings.EmbeddingsClient._compute_embedding")
    async def test_reindex_stats_has_skipped_key(self, mock_embed, client, tmp_path) -> None:
        """Stats siempre incluye la clave 'skipped'."""
        mock_embed.return_value = FAKE_EMBEDDING
        vault = tmp_path / "vault"
        vault.mkdir()

        stats = await client.reindex_vault(vault)
        assert "skipped" in stats


class TestLazyInit:

    def test_not_initialized_on_creation(self, tmp_path) -> None:
        client = EmbeddingsClient(chroma_data_dir=tmp_path / "chroma")
        assert not client._initialized

    def test_initialized_on_first_use(self, tmp_path) -> None:
        client = EmbeddingsClient(chroma_data_dir=tmp_path / "chroma")
        client._ensure_initialized()
        assert client._initialized
        assert client._collection is not None

    def test_creates_data_dir(self, tmp_path) -> None:
        chroma_dir = tmp_path / "new_dir" / "chroma"
        client = EmbeddingsClient(chroma_data_dir=chroma_dir)
        client._ensure_initialized()
        assert chroma_dir.exists()
