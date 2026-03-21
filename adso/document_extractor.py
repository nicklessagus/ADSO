"""Extracción de contenido de PDFs y archivos de texto.

PDF: usa pymupdf para extraer texto + metadata.
Texto: lectura directa de archivos planos (.md, .txt, .py, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Extensiones que se leen como texto plano
TEXT_EXTENSIONS = {
    ".md", ".txt", ".py", ".csv", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".sh", ".bash", ".zsh",
    ".js", ".ts", ".html", ".css", ".xml", ".sql", ".r", ".R",
    ".tex", ".bib", ".log", ".rst", ".org",
}


def is_text_file(filename: str) -> bool:
    """Determina si un archivo es texto plano por extensión.

    Args:
        filename: Nombre del archivo (con extensión).

    Returns:
        True si es un archivo de texto plano reconocido.
    """
    return Path(filename).suffix.lower() in TEXT_EXTENSIONS


def is_pdf(filename: str) -> bool:
    """Determina si un archivo es PDF.

    Args:
        filename: Nombre del archivo.

    Returns:
        True si es PDF.
    """
    return Path(filename).suffix.lower() == ".pdf"


async def extract_pdf(file_path: Path) -> tuple[str, dict]:
    """Extrae texto y metadata de un PDF con pymupdf.

    Args:
        file_path: Path al archivo PDF.

    Returns:
        Tupla (texto_extraído, metadata_dict).
        metadata_dict contiene: title, author, subject, pages.
        Si no hay texto extraíble, texto será string vacío.

    Raises:
        FileNotFoundError: Si el archivo no existe.
        RuntimeError: Si pymupdf no puede abrir el archivo.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"PDF no encontrado: {file_path}")

    def _do_extract() -> tuple[str, dict]:
        import pymupdf

        try:
            doc = pymupdf.open(str(file_path))
        except Exception as e:
            raise RuntimeError(f"No se pudo abrir el PDF: {e}") from e

        # Extraer metadata
        meta = doc.metadata or {}
        metadata = {
            "title": meta.get("title", "") or "",
            "author": meta.get("author", "") or "",
            "subject": meta.get("subject", "") or "",
            "pages": doc.page_count,
        }

        # Extraer texto de todas las páginas
        text_parts = []
        for page in doc:
            page_text = page.get_text().strip()
            if page_text:
                text_parts.append(page_text)

        doc.close()

        full_text = "\n\n".join(text_parts)
        logger.info(
            "PDF extraído: %d páginas, %d chars de texto",
            metadata["pages"],
            len(full_text),
        )

        return full_text, metadata

    return await asyncio.to_thread(_do_extract)


async def extract_text_file(
    file_path: Path,
    max_chars: Optional[int] = None,
) -> str:
    """Lee contenido de un archivo de texto plano.

    Args:
        file_path: Path al archivo.
        max_chars: Límite de caracteres (None = sin límite).

    Returns:
        Contenido del archivo como string.

    Raises:
        FileNotFoundError: Si el archivo no existe.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

    def _do_read() -> str:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        if max_chars and len(content) > max_chars:
            content = content[:max_chars]
            logger.info("Archivo truncado a %d chars", max_chars)
        return content

    return await asyncio.to_thread(_do_read)
