"""Cliente para la API pública de arXiv.

Detecta URLs de arxiv.org, obtiene metadata estructurada via la API Atom
y construye el contenido y body de la nota en el mismo formato que un paper PDF.
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Namespace del feed Atom de arXiv
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

_ARXIV_API = "https://export.arxiv.org/api/query?id_list={id}"

# Tope de lectura de la respuesta: la metadata de un paper son ~decenas de KB.
# Sin este límite, un endpoint hostil/comprometido podría agotar la RAM de la RPi4
# con un read() ilimitado.
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB

# Patrones de URL soportados:
#   https://arxiv.org/abs/2301.12345
#   https://arxiv.org/abs/2301.12345v2
#   https://arxiv.org/pdf/2301.12345
#   https://arxiv.org/pdf/2301.12345.pdf
#   https://arxiv.org/abs/hep-ph/0001234  (formato antiguo)
_ARXIV_URL_RE = re.compile(
    r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/([a-z\-]+/\d+|\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)


def extract_arxiv_id(text: str) -> Optional[str]:
    """Extrae el arXiv ID de un texto que contiene una URL de arxiv.org.

    Args:
        text: Texto completo del mensaje (puede contener solo la URL).

    Returns:
        arXiv ID (ej: "2301.12345" o "hep-ph/0001234"), o None si no hay match.
    """
    m = _ARXIV_URL_RE.search(text.strip())
    if not m:
        return None
    arxiv_id = m.group(1)
    # Remover versión si la hay (2301.12345v2 → 2301.12345)
    arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
    return arxiv_id


def _parse_atom_entry(entry: ET.Element) -> dict:
    """Parsea un elemento <entry> del feed Atom de arXiv y retorna metadata estructurada.

    Args:
        entry: Elemento XML <entry> del feed.

    Returns:
        Dict con keys: title, authors, year, abstract, doi, keywords, arxiv_id, source_url.
    """
    def text(tag: str) -> str:
        el = entry.find(tag, _NS)
        return el.text.strip() if el is not None and el.text else ""

    title = re.sub(r"\s+", " ", text("atom:title"))
    abstract = re.sub(r"\s+", " ", text("atom:summary"))

    authors = [
        name.text.strip()
        for author in entry.findall("atom:author", _NS)
        for name in author.findall("atom:name", _NS)
        if name.text
    ]

    published = text("atom:published")
    year: Optional[int] = None
    if published:
        try:
            year = datetime.fromisoformat(published.replace("Z", "+00:00")).year
        except ValueError:
            pass

    doi_el = entry.find("arxiv:doi", _NS)
    doi = doi_el.text.strip() if doi_el is not None and doi_el.text else None

    # Categorías de arXiv como keywords (ej: "cs.LG", "astro-ph.GA")
    keywords = [
        cat.get("term", "")
        for cat in entry.findall("atom:category", _NS)
        if cat.get("term")
    ]

    # URL canónica (abs page)
    source_url = ""
    arxiv_id = ""
    for link in entry.findall("atom:link", _NS):
        if link.get("rel") == "alternate" or link.get("type") == "text/html":
            source_url = link.get("href", "")
        # Extraer ID desde el campo <id>
    id_el = entry.find("atom:id", _NS)
    if id_el is not None and id_el.text:
        m = _ARXIV_URL_RE.search(id_el.text)
        if m:
            arxiv_id = re.sub(r"v\d+$", "", m.group(1))
        # Siempre construir la URL canónica sin versión
        if arxiv_id:
            source_url = f"https://arxiv.org/abs/{arxiv_id}"

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "abstract": abstract,
        "doi": doi,
        "keywords": keywords,
        "arxiv_id": arxiv_id,
        "source_url": source_url or f"https://arxiv.org/abs/{arxiv_id}",
    }


def _fetch_metadata_sync(arxiv_id: str) -> dict:
    """Llama a la API de arXiv (síncrono, para ejecutar en hilo).

    Args:
        arxiv_id: ID de arXiv (ej: "2301.12345").

    Returns:
        Metadata parseada del paper.

    Raises:
        ValueError: Si la API no devuelve resultados o hay error de parseo.
        urllib.error.URLError: Si falla la conexión.
    """
    url = _ARXIV_API.format(id=arxiv_id)
    with urllib.request.urlopen(url, timeout=10) as resp:
        # Leer un byte de más que el tope para detectar truncamiento.
        raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError(
            f"Respuesta de arXiv excede el tope de {_MAX_RESPONSE_BYTES} bytes"
        )

    root = ET.fromstring(raw)
    entries = root.findall("atom:entry", _NS)
    if not entries:
        raise ValueError(f"arXiv no devolvió resultados para ID: {arxiv_id}")

    return _parse_atom_entry(entries[0])


async def fetch_arxiv_metadata(arxiv_id: str) -> dict:
    """Obtiene metadata de un paper de arXiv via su API pública.

    Args:
        arxiv_id: ID de arXiv (ej: "2301.12345" o "hep-ph/0001234").

    Returns:
        Dict con: title, authors, year, abstract, doi, keywords, arxiv_id, source_url.

    Raises:
        ValueError: Si la API no devuelve resultados.
        Exception: Si falla la conexión o el parseo.
    """
    return await asyncio.to_thread(_fetch_metadata_sync, arxiv_id)


def build_arxiv_classify_content(metadata: dict) -> str:
    """Construye el contenido a enviar al LLM para clasificar un paper de arXiv.

    Incluye título, abstract y keywords para que el LLM pueda inferir proyecto,
    área, tags y summary. El LLM no necesita el resto de campos académicos
    porque se inyectan directamente desde la API.

    Args:
        metadata: Dict retornado por fetch_arxiv_metadata().

    Returns:
        String estructurado listo para enviar dentro de <input>...</input>.
    """
    parts = []
    if metadata.get("title"):
        parts.append(f"TÍTULO: {metadata['title']}")
    if metadata.get("authors"):
        parts.append(f"AUTORES: {', '.join(metadata['authors'][:5])}")
    if metadata.get("year"):
        parts.append(f"AÑO: {metadata['year']}")
    if metadata.get("abstract"):
        parts.append(f"\nABSTRACT:\n{metadata['abstract']}")
    if metadata.get("keywords"):
        parts.append(f"\nCATEGORÍAS arXiv: {', '.join(metadata['keywords'])}")
    return "\n".join(parts)


def build_arxiv_body(metadata: dict, llm_summary: Optional[str]) -> str:
    """Construye el body de la nota en el mismo formato que un paper PDF.

    Estructura:
        > [!summary] AI Summary   (si hay summary del LLM)
        > ...

        ## Abstract
        [Texto literal del abstract de la API]

        ## Personal Notes

    Args:
        metadata: Dict retornado por fetch_arxiv_metadata().
        llm_summary: Síntesis generada por el LLM, o None si no hay.

    Returns:
        String con el body completo de la nota.
    """
    parts = []

    if llm_summary:
        summary_lines = "\n".join(
            f"> {line}" if line.strip() else ">"
            for line in llm_summary.splitlines()
        )
        parts.append(f"> [!summary] AI Summary\n{summary_lines}")

    if metadata.get("abstract"):
        parts.append(f"## Abstract\n{metadata['abstract']}")

    parts.append("## Personal Notes\n")

    return "\n\n".join(parts)
