"""Pipeline de embeddings y ChromaDB para indexado semántico del vault.

Calcula embeddings via Gemini Embedding API (remoto, nunca local).
Almacena en ChromaDB embebido (PersistentClient).
No importa vault_writer ni vault_search — bot.py orquesta.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from adso.constants import DEFAULT_EXCLUDE_DIRS

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


# ---------------------------------------------------------------------------
# Conversión similitud ↔ distancia
# ---------------------------------------------------------------------------


def similarity_to_distance(similarity: float) -> float:
    """Convierte similitud coseno [0,1] a distancia coseno [0,2].

    similarity = 1 - (distance / 2)
    distance = 2 * (1 - similarity)
    """
    return 2.0 * (1.0 - similarity)


def build_note_metadata(rel_path: Path, fm: dict, body: str) -> dict:
    """Metadata que ChromaDB guarda junto al embedding de una nota.

    Único constructor: lo usan el reindex nocturno y el indexado inline al
    confirmar (`_index_note_safe`). El ``content_hash`` es lo que permite al
    reindex saltear notas cuyo cuerpo no cambió.

    Args:
        rel_path: Ruta de la nota relativa al vault.
        fm: Frontmatter de la nota.
        body: Cuerpo que se embebe.

    Returns:
        Dict listo para `index_note` (se serializa allí).
    """
    return {
        "path": str(rel_path),
        "type": fm.get("type", ""),
        "status": fm.get("status", ""),
        "project": fm.get("project", ""),
        "area": fm.get("area", ""),
        "tags": fm.get("tags", []),
        "media_type": fm.get("media_type", ""),
        "title": fm.get("title", ""),
        "content_hash": hashlib.md5(body.encode()).hexdigest(),
    }


def should_index(
    md_path: Path,
    vault_path: Path,
    exclude_dirs: Optional[list[str]] = None,
) -> bool:
    """True si ese archivo corresponde a una nota indexable del vault.

    Predicado único de "qué entra al índice semántico". Existe porque los dos
    caminos que indexan —el reindex nocturno y el reindex externo del watcher—
    tenían criterios distintos: el watcher no filtraba nada, así que editar
    desde Obsidian una nota de `05-Archive` (o un `_index.md`) la metía al
    índice contra el diseño y esa misma noche el reindex la borraba como
    huérfana. El resultado era un ciclo diario de embed + delete que gastaba
    quota de la Embedding API y ensuciaba `/buscar` hasta las 3 AM (E2).

    Args:
        md_path: Path del archivo (absoluto).
        vault_path: Raíz del vault.
        exclude_dirs: Directorios excluidos. None = default del vault.

    Returns:
        False para paths fuera del vault, bajo `exclude_dirs`, `_index.md`,
        conflictos de Syncthing o cualquier archivo que no sea `.md`.
    """
    if exclude_dirs is None:
        exclude_dirs = list(DEFAULT_EXCLUDE_DIRS)
    try:
        rel = md_path.relative_to(vault_path)
    except ValueError:
        return False
    if rel.suffix != ".md":
        return False
    if any(part in exclude_dirs for part in rel.parts):
        return False
    if rel.stem == "_index":
        return False
    if ".sync-conflict-" in md_path.name:
        return False
    return True


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
        max_concurrent_embeds: int = 4,
    ) -> None:
        self._chroma_data_dir = chroma_data_dir
        self._gemini_api_key = gemini_api_key
        self._collection = None
        self._initialized = False
        self._genai_client = None
        # Limita concurrencia contra Gemini Embedding API; protege contra bursts
        # del watcher (ej: sync masivo de Syncthing → muchos eventos en paralelo).
        self._embed_semaphore = asyncio.Semaphore(max_concurrent_embeds)

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
        client = self._get_genai_client()

        last_error = None
        async with self._embed_semaphore:
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

    def _get_genai_client(self):
        """Cliente genai lazy y reutilizado entre llamadas (evita recrearlo por request)."""
        if self._genai_client is None:
            from google import genai

            self._genai_client = genai.Client(api_key=self._gemini_api_key or None)
        return self._genai_client

    async def compute_embedding(self, content: str) -> list[float]:
        """Calcula el embedding de un texto (API pública para reutilizar el vector).

        Permite embeder una sola vez cuando el mismo texto se usa en más de una
        operación (ej: query_similar para sugerir links + index_note al confirmar).
        """
        return await self._compute_embedding(content)

    async def index_note(
        self,
        note_id: str,
        content: str,
        metadata: dict,
        embedding: Optional[list[float]] = None,
    ) -> None:
        """Calcula embedding y lo almacena en ChromaDB (upsert).

        Args:
            note_id: ID único (stem del archivo).
            content: Cuerpo de la nota para embedding.
            metadata: Frontmatter serializable.
            embedding: Vector precomputado de `content` (evita re-embeder si el
                caller ya lo calculó). Si None, se computa acá.
        """
        self._ensure_initialized()

        if embedding is None:
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
        query_embedding: Optional[list[float]] = None,
    ) -> list[SimilarNote]:
        """Busca notas similares por texto.

        Args:
            query_text: Texto de consulta.
            n_results: Máximo de resultados.
            threshold: Similitud mínima (0-1). Si None, no filtra.
            where: Filtro de metadata ChromaDB.
            query_embedding: Vector precomputado de `query_text` (evita
                re-embeder el mismo texto en llamadas repetidas). Si None,
                se computa acá.

        Returns:
            Lista de SimilarNote ordenada por distancia ascendente.
        """
        self._ensure_initialized()

        # ChromaDB falla si n_results > número de documentos indexados
        count = await asyncio.to_thread(self._collection.count)
        if count == 0:
            return []
        n_results = min(n_results, count)

        # Calcular embedding de la consulta (si no vino precomputado)
        if query_embedding is None:
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
        """Reconciliación completa: indexa notas nuevas/modificadas y borra huérfanos.

        El ID de cada nota es su ruta relativa al vault sin extensión
        (ej: "01-Projects/tesis/metodologia"). Esto evita colisiones entre
        archivos con el mismo nombre en distintos directorios.

        Solo re-embede notas cuyo contenido cambió (via content_hash en metadata).
        Archivos .sync-conflict-* de Syncthing se ignoran.

        Args:
            vault_path: Raíz del vault.
            exclude_dirs: Directorios a excluir.

        Returns:
            Stats: {"indexed": N, "skipped": M, "removed": K, "errors": J}
        """
        self._ensure_initialized()

        if exclude_dirs is None:
            exclude_dirs = list(DEFAULT_EXCLUDE_DIRS)

        from adso import vault_cache

        stats = {"indexed": 0, "skipped": 0, "removed": 0, "errors": 0}

        # Cargar hashes existentes en una sola llamada batch
        existing_hashes: dict[str, str] = {}
        try:
            existing_docs = await asyncio.to_thread(
                self._collection.get,
                include=["metadatas"],
            )
            for doc_id, meta in zip(
                existing_docs["ids"], existing_docs["metadatas"] or []
            ):
                existing_hashes[doc_id] = (meta or {}).get("content_hash", "")
        except Exception as e:
            logger.warning("No se pudo cargar hashes existentes: %s", e)

        # Escanear vault
        vault_note_ids: set[str] = set()
        # En un hilo: es la única operación de filesystem del método fuera de
        # to_thread, y con SD lenta y cientos de notas congelaba el bot al
        # inicio de cada reindex nocturno. F10 de docs/audit-2026-07-31.md.
        md_files = await asyncio.to_thread(lambda: sorted(vault_path.rglob("*.md")))

        for md_path in md_files:
            # exclude_dirs, conflictos de Syncthing y `_index.md`: el mismo
            # predicado que usa el reindex externo del watcher (E2).
            if not should_index(md_path, vault_path, exclude_dirs):
                continue

            rel = md_path.relative_to(vault_path)
            # ID: ruta relativa sin extensión (ej: "01-Projects/tesis/nota")
            note_id = str(rel.with_suffix(""))

            try:
                # parse_cached evita releer/reparsear notas sin cambios desde
                # el último scan (mismo caché que usa vault_search).
                note = await asyncio.to_thread(vault_cache.parse_cached, md_path)
                if note is None:
                    # Sin frontmatter, ilegible o YAML inválido (ya logueado).
                    # Cuenta como viva a propósito: un YAML roto transitorio
                    # (editado a mano, a mitad de sync) no debe borrar el
                    # embedding — ver docs/decisions-log.md.
                    vault_note_ids.add(note_id)
                    continue

                fm = note.frontmatter
                body = note.body

                if not body.strip():
                    # Nota vaciada desde Obsidian: el embedding es del texto
                    # ANTERIOR, así que /buscar seguía devolviéndola por
                    # contenido que ya no existe en el archivo (E3). No alcanza
                    # con no sumarla a `vault_note_ids`: el sweep de huérfanos
                    # ahora re-verifica el disco y el .md sí existe. Hay que
                    # borrarla explícitamente.
                    await self.remove_note(note_id)
                    continue

                vault_note_ids.add(note_id)

                # Comparar hash para evitar re-embeds innecesarios
                metadata = build_note_metadata(rel, fm, body)
                if existing_hashes.get(note_id) == metadata["content_hash"]:
                    stats["skipped"] += 1
                    continue

                await self.index_note(note_id, body, metadata)
                stats["indexed"] += 1

                # Rate limiting: pequeño delay entre calls
                await asyncio.sleep(0.2)

            except Exception as e:
                logger.warning("Error indexando %s: %s", md_path, e)
                stats["errors"] += 1

        def _sigue_en_el_vault(note_id: str) -> bool:
            """True si el .md del ID existe en disco y el scan lo indexaría.

            El snapshot de `rglob` se toma al principio y el reindex tarda
            minutos (0,2 s de rate limiting por nota + latencia de la API): una
            captura confirmada en esa ventana entra a ChromaDB pero no al
            snapshot, y el sweep la borraba como huérfana — la nota existía en
            el vault y quedaba invisible para /buscar hasta el reindex de la
            noche siguiente (E4). Re-verificar en disco cierra la carrera.

            Los filtros del scan se repiten a propósito: un ID bajo
            `exclude_dirs`, un `_index.md` o un conflicto de Syncthing SÍ deben
            borrarse del índice aunque el archivo exista.
            """
            md_path = vault_path / f"{note_id}.md"
            return should_index(md_path, vault_path, exclude_dirs) and md_path.is_file()

        # Detectar huérfanos en ChromaDB (notas borradas del vault)
        try:
            all_docs = await asyncio.to_thread(
                self._collection.get,
                include=[],
            )
            chroma_ids = set(all_docs["ids"])
            candidates = chroma_ids - vault_note_ids
            # El stat() de cada candidato va a un hilo: en la RPi4 la SD es
            # lenta y esto corre con el event loop del bot vivo.
            orphans = await asyncio.to_thread(
                lambda: {oid for oid in candidates if not _sigue_en_el_vault(oid)}
            )

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
            "Reindex completo: %d indexados, %d sin cambios, %d removidos, %d errores",
            stats["indexed"], stats["skipped"], stats["removed"], stats["errors"],
        )
        return stats

    def count(self) -> int:
        """Retorna cantidad de documentos en la colección."""
        self._ensure_initialized()
        return self._collection.count()
