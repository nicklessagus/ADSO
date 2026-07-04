"""Retrieval semántico para consultas RAG (Fase 7 — retrieval puro).

Orquesta el retrieval semántico sobre ChromaDB: embebe la consulta, busca notas
similares y las envuelve en estructuras de resultado. **No llama al LLM** — la
síntesis en lenguaje natural es Fase 7.2. La expansión estructural (backlinks) es
Fase 7.1.

Referencia: docs/fase7-rag-design.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from adso.embeddings import EmbeddingsClient, SimilarNote, distance_to_similarity

logger = logging.getLogger(__name__)

# Cuántos resultados mostrar cuando nada supera el umbral de similitud (fallback
# de "baja confianza"): se relaja el umbral y se devuelven los mejores N.
_FALLBACK_RESULTS = 3


@dataclass
class ScoredNote:
    """Una nota recuperada, con su score y procedencia."""

    note_id: str
    path: Path                 # path absoluto en el vault
    title: str
    snippet: Optional[str]
    similarity: float          # 0-1 (1 = idéntico)
    status: str = ""
    project: str = ""
    area: str = ""
    via: str = "semantic"      # semantic | backlink | outgoing (Fase 7.1)


@dataclass
class QueryResult:
    """Resultado de una consulta al vault."""

    query: str
    notes: list[ScoredNote]
    scope: Optional[dict] = None      # {"project": ...} | {"area": ...} | None
    below_threshold: bool = False     # True si se relajó el umbral por falta de hits


def _to_scored(hit: SimilarNote, vault_path: Path) -> ScoredNote:
    """Convierte un SimilarNote de embeddings en un ScoredNote."""
    rel = hit.path or f"{hit.note_id}.md"
    meta = hit.metadata or {}
    return ScoredNote(
        note_id=hit.note_id,
        path=vault_path / rel,
        title=meta.get("title") or Path(rel).stem,
        snippet=hit.snippet,
        similarity=round(distance_to_similarity(hit.distance), 3),
        status=meta.get("status", "") or "",
        project=meta.get("project", "") or "",
        area=meta.get("area", "") or "",
        via="semantic",
    )


async def retrieve(
    query: str,
    vault_path: Path,
    embeddings: EmbeddingsClient,
    scope: Optional[dict] = None,
    threshold: Optional[float] = None,
    max_results: int = 10,
) -> QueryResult:
    """Retrieval semántico puro sobre ChromaDB. Sin LLM.

    Args:
        query: Texto de la consulta en lenguaje natural.
        vault_path: Raíz del vault (para resolver paths absolutos).
        embeddings: Cliente de embeddings/ChromaDB.
        scope: Filtro de metadata para acotar (`where` de ChromaDB). None = todo.
        threshold: Similitud mínima. None = usar el default del caller.
        max_results: Máximo de resultados.

    Returns:
        QueryResult. Si nada supera `threshold`, reintenta sin umbral y devuelve
        hasta `_FALLBACK_RESULTS` marcados con `below_threshold=True` (baja
        confianza) en vez de un resultado vacío.

    Comportamiento ante error: propaga excepciones de red/embedding al caller
    (el handler decide cómo notificar). No silencia.
    """
    # Embeder la consulta una sola vez: el reintento con umbral relajado
    # reutiliza el mismo vector (evita una segunda llamada a la API).
    query_embedding = await embeddings.compute_embedding(query)

    hits = await embeddings.query_similar(
        query_text=query,
        n_results=max_results,
        threshold=threshold,
        where=scope,
        query_embedding=query_embedding,
    )

    below_threshold = False
    if not hits and threshold is not None:
        # Nada superó el umbral: relajar y mostrar los mejores con aviso.
        hits = await embeddings.query_similar(
            query_text=query,
            n_results=_FALLBACK_RESULTS,
            threshold=None,
            where=scope,
            query_embedding=query_embedding,
        )
        below_threshold = bool(hits)

    notes = [_to_scored(h, vault_path) for h in hits]
    logger.info(
        "Consulta (%d chars) → %d resultados%s",
        len(query), len(notes), " (baja confianza)" if below_threshold else "",
    )
    return QueryResult(
        query=query,
        notes=notes,
        scope=scope,
        below_threshold=below_threshold,
    )
