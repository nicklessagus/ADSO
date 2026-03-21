"""Pipeline de embeddings y ChromaDB para indexado semántico del vault.

Calcula embeddings via Gemini Embedding API (remoto, nunca local).
Almacena en ChromaDB embebido (PersistentClient).
No importa vault_writer ni vault_search — bot.py orquesta.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Modelo de embeddings
EMBEDDING_MODEL = "gemini-embedding-001"

# Nombre de la colección en ChromaDB
COLLECTION_NAME = "vault_notes"


# ---------------------------------------------------------------------------
# Tipos de datos
# ---------------------------------------------------------------------------


@dataclass
class SimilarNote:
    """Resultado de búsqueda por similitud."""

    note_id: str
    path: str
    distance: float
    metadata: dict
    snippet: Optional[str]


# ---------------------------------------------------------------------------
# Serialización de metadata para ChromaDB
# ---------------------------------------------------------------------------


def _serialize_metadata(metadata: dict) -> dict:
    """Serializa metadata para ChromaDB.

    ChromaDB no soporta None ni listas heterogéneas.
    - None → ""
    - list → string separado por comas
    - Otros tipos → str si no es int/float/bool

    Args:
        metadata: Dict con campos del frontmatter.

    Returns:
        Dict compatible con ChromaDB.
    """
    result = {}
    for key, value in metadata.items():
        if value is None:
            result[key] = ""
        elif isinstance(value, list):
            result[key] = ",".join(str(v) for v in value)
        elif isinstance(value, (str, int, float, bool)):
            result[key] = value
        else:
            result[key] = str(value)
    return result


def _deserialize_tags(tags_str: str) -> list[str]:
    """Convierte tags serializados a lista."""
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Conversión similitud ↔ distancia
# ---------------------------------------------------------------------------


def similarity_to_distance(similarity: float) -> float:
    """Convierte similitud coseno [0,1] a distancia coseno [0,2].

    similarity = 1 - (distance / 2)
    distance = 2 * (1 - similarity)
    """
    return 2.0 * (1.0 - similarity)


def distance_to_similarity(distance: float) -> float:
    """Convierte distancia coseno [0,2] a similitud coseno [0,1]."""
    return 1.0 - (distance / 2.0)


# ---------------------------------------------------------------------------
# Cliente ChromaDB
# ---------------------------------------------------------------------------


class EmbeddingsClient:
    """Maneja ChromaDB + Gemini Embedding API.

    Args:
        chroma_data_dir: Path para persistencia de ChromaDB.
        gemini_api_key: API key para Gemini (embeddings).
    """

    def __init__(
        self,
        chroma_data_dir: Path,
        gemini_api_key: str = "",
    ) -> None:
        self._chroma_data_dir = chroma_data_dir
        self._gemini_api_key = gemini_api_key
        self._collection = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Inicializa ChromaDB lazily al primer uso."""
        if self._initialized:
            return

        import chromadb

        self._chroma_data_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self._chroma_data_dir))
        self._collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._initialized = True
        logger.info("ChromaDB inicializado en %s", self._chroma_data_dir)

    async def _compute_embedding(self, content: str) -> list[float]:
        """Calcula embedding via Gemini Embedding API.

        Args:
            content: Texto a embeder.

        Returns:
            Vector de embedding.

        Raises:
            Exception: Si falla después de 3 reintentos.
        """
        from google import genai

        client = genai.Client(api_key=self._gemini_api_key or None)

        last_error = None
        for attempt in range(1, 4):
            try:
                result = await asyncio.to_thread(
                    client.models.embed_content,
                    model=EMBEDDING_MODEL,
                    contents=content,
                )
                return result.embeddings[0].values
            except Exception as e:
                last_error = e
                if attempt < 3:
                    delay = 2 ** (attempt - 1)  # 1s, 2s
                    logger.warning(
                        "Embedding retry %d/3 tras error: %s", attempt, e
                    )
                    await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    async def index_note(
        self,
        note_id: str,
        content: str,
        metadata: dict,
    ) -> None:
        """Calcula embedding y lo almacena en ChromaDB (upsert).

        Args:
            note_id: ID único (stem del archivo).
            content: Cuerpo de la nota para embedding.
            metadata: Frontmatter serializable.
        """
        self._ensure_initialized()

        embedding = await self._compute_embedding(content)
        serialized = _serialize_metadata(metadata)

        await asyncio.to_thread(
            self._collection.upsert,
            ids=[note_id],
            documents=[content],
            embeddings=[embedding],
            metadatas=[serialized],
        )
        logger.info("Embedding indexado: %s", note_id)

    async def remove_note(self, note_id: str) -> None:
        """Elimina un documento de ChromaDB.

        No-op si el ID no existe.

        Args:
            note_id: ID del documento a eliminar.
        """
        self._ensure_initialized()

        try:
            # Verificar si existe antes de borrar
            existing = await asyncio.to_thread(
                self._collection.get,
                ids=[note_id],
            )
            if existing["ids"]:
                await asyncio.to_thread(
                    self._collection.delete,
                    ids=[note_id],
                )
                logger.info("Embedding eliminado: %s", note_id)
        except Exception as e:
            logger.warning("Error eliminando embedding %s: %s", note_id, e)

    async def update_metadata(
        self,
        note_id: str,
        metadata: dict,
    ) -> None:
        """Actualiza metadata sin recalcular embedding.

        Args:
            note_id: ID del documento.
            metadata: Nueva metadata (se serializa).
        """
        self._ensure_initialized()

        serialized = _serialize_metadata(metadata)
        await asyncio.to_thread(
            self._collection.update,
            ids=[note_id],
            metadatas=[serialized],
        )
        logger.info("Metadata actualizada: %s", note_id)

    async def query_similar(
        self,
        query_text: str,
        n_results: int = 10,
        threshold: Optional[float] = None,
        where: Optional[dict] = None,
    ) -> list[SimilarNote]:
        """Busca notas similares por texto.

        Args:
            query_text: Texto de consulta.
            n_results: Máximo de resultados.
            threshold: Similitud mínima (0-1). Si None, no filtra.
            where: Filtro de metadata ChromaDB.

        Returns:
            Lista de SimilarNote ordenada por distancia ascendente.
        """
        self._ensure_initialized()

        # Calcular embedding de la consulta
        query_embedding = await self._compute_embedding(query_text)

        # Preparar kwargs para query
        query_kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where is not None:
            query_kwargs["where"] = where

        results = await asyncio.to_thread(
            self._collection.query,
            **query_kwargs,
        )

        # Convertir resultados
        similar: list[SimilarNote] = []
        if not results["ids"] or not results["ids"][0]:
            return similar

        ids = results["ids"][0]
        distances = results["distances"][0]
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]

        max_distance = None
        if threshold is not None:
            max_distance = similarity_to_distance(threshold)

        for i, note_id in enumerate(ids):
            dist = distances[i]

            if max_distance is not None and dist > max_distance:
                continue

            meta = metadatas[i] if metadatas[i] else {}
            doc = documents[i] if documents[i] else ""

            similar.append(SimilarNote(
                note_id=note_id,
                path=meta.get("path", ""),
                distance=dist,
                metadata=meta,
                snippet=doc[:200] if doc else None,
            ))

        return similar

    async def reindex_vault(
        self,
        vault_path: Path,
        exclude_dirs: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """Reconciliación completa: indexa todas las notas y borra huérfanos.

        Args:
            vault_path: Raíz del vault.
            exclude_dirs: Directorios a excluir.

        Returns:
            Stats: {"indexed": N, "removed": M, "errors": K}
        """
        self._ensure_initialized()

        if exclude_dirs is None:
            exclude_dirs = ["05-Archive", ".obsidian", ".trash"]

        import frontmatter

        stats = {"indexed": 0, "removed": 0, "errors": 0}

        # Escanear vault
        vault_note_ids: set[str] = set()
        md_files = sorted(vault_path.rglob("*.md"))

        for md_path in md_files:
            # Filtrar por exclude_dirs
            rel = md_path.relative_to(vault_path)
            if any(part in exclude_dirs for part in rel.parts):
                continue

            note_id = md_path.stem

            # Skip _index.md
            if note_id == "_index":
                continue

            vault_note_ids.add(note_id)

            try:
                raw = await asyncio.to_thread(md_path.read_text, "utf-8")
                post = frontmatter.loads(raw)

                if not post.metadata:
                    continue

                fm = dict(post.metadata)
                body = post.content

                if not body.strip():
                    continue

                metadata = {
                    "path": str(rel),
                    "type": fm.get("type", ""),
                    "status": fm.get("status", ""),
                    "project": fm.get("project", ""),
                    "area": fm.get("area", ""),
                    "tags": fm.get("tags", []),
                    "media_type": fm.get("media_type", ""),
                    "title": fm.get("title", ""),
                }

                await self.index_note(note_id, body, metadata)
                stats["indexed"] += 1

                # Rate limiting: pequeño delay entre calls
                await asyncio.sleep(0.2)

            except Exception as e:
                logger.warning("Error indexando %s: %s", md_path, e)
                stats["errors"] += 1

        # Detectar huérfanos en ChromaDB
        try:
            all_docs = await asyncio.to_thread(
                self._collection.get,
                include=[],
            )
            chroma_ids = set(all_docs["ids"])
            orphans = chroma_ids - vault_note_ids

            if orphans:
                await asyncio.to_thread(
                    self._collection.delete,
                    ids=list(orphans),
                )
                stats["removed"] = len(orphans)
                logger.info("Huérfanos eliminados: %d", len(orphans))

        except Exception as e:
            logger.warning("Error detectando huérfanos: %s", e)

        logger.info(
            "Reindex completo: %d indexados, %d removidos, %d errores",
            stats["indexed"], stats["removed"], stats["errors"],
        )
        return stats

    def count(self) -> int:
        """Retorna cantidad de documentos en la colección."""
        self._ensure_initialized()
        return self._collection.count()
