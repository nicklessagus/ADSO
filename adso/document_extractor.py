"""Extracción de contenido de PDFs y archivos de texto.

PDF: usa pymupdf para extraer texto + metadata (local, sin API).
Incluye detección heurística de papers académicos y extracción de
secciones clave (abstract, keywords, métodos, conclusiones) para
minimizar tokens enviados al LLM.
Texto: lectura directa de archivos planos (.md, .txt, .py, etc.).
"""

from __future__ import annotations

import asyncio
import logging
import re
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

# ---------------------------------------------------------------------------
# Detección y extracción de papers académicos
# ---------------------------------------------------------------------------

# Señales que indican un paper académico (busca en los primeros 5000 chars)
_PAPER_SIGNALS = [
    re.compile(r"\babstract\b", re.IGNORECASE),
    re.compile(r"\b10\.\d{4,9}/\S+"),                                      # DOI
    re.compile(r"\breferences?\b", re.IGNORECASE),
    re.compile(r"\bintroduction\b", re.IGNORECASE),
    re.compile(r"\b(?:journal|preprint|arxiv|submitted to|peer.?review)\b", re.IGNORECASE),
]

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s,;]+")

# Símbolos matemáticos Unicode frecuentes en papers
_MATH_SYMBOL_RE = re.compile(r"[⊤⊥√∑∫∂∇·×≈≤≥≠±∞αβγδεζηθλμνξπρστφψωΩΓΔΛΞΠΣΦΨ]")
# Número de ecuación al final o solo en la línea: (1), (2), (A.3), etc.
_EQ_NUMBER_RE = re.compile(r"^\s*\(\d+[\.\d]*\)\s*$")

# Patrones para saltar al buscar el título en las primeras líneas del PDF
_TITLE_SKIP_RE = re.compile(
    r"(?:university|universit|institute|department|©|arxiv|preprint|submitted|"
    r"manuscript|vol\.|issue|astronomy|astrophysics|monthly notices|physical review|"
    r"nature|science|journal|received|accepted|published|\beso\b|\bmnras\b|\baas\b)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b.{0,20}\d{4}",
    re.IGNORECASE,
)


def _extract_title_from_text(lines: list[str], max_lines: int = 50) -> str:
    """Extrae el título del paper de las primeras líneas del texto extraído.

    Fallback para cuando el metadata del PDF no trae título (común en arXiv).
    Salta headers de journals, fechas y afiliaciones; toma las primeras
    líneas sustanciales como candidatos al título.

    Args:
        lines: Líneas del texto completo del PDF.
        max_lines: Cuántas líneas iniciales considerar.

    Returns:
        Título inferido, o string vacío si no se encontró.
    """
    candidates: list[str] = []
    for line in lines[:max_lines]:
        s = line.strip()
        if not s or len(s) < 8 or len(s) > 250:
            continue
        if _TITLE_SKIP_RE.search(s):
            continue
        if _DATE_RE.search(s):
            continue
        if re.match(r"^[\d©\-\*†‡]", s):
            continue
        # Líneas con año de 4 dígitos son headers de journal o fechas
        if re.search(r"\b\d{4}\b", s):
            continue
        # Líneas que parecen affiliaciones: contienen dígitos superíndices y comas
        if re.search(r"[A-Z][a-z]+\d,", s):
            continue
        candidates.append(s)
        if len(candidates) >= 4:
            break

    if not candidates:
        return ""

    title = candidates[0]
    # Si el primer candidato es muy corto (acrónimo como "ASTROMER"), unir con el siguiente
    if len(title) < 30 and len(candidates) > 1:
        title = f"{title}: {candidates[1]}"
    return title


def _clean_formula_blocks(text: str) -> str:
    """Reemplaza bloques de fórmulas rotas por un placeholder legible.

    pymupdf extrae ecuaciones como fragmentos de texto ilegibles. Usa los
    números de ecuación '(1)', '(2)' como anclas: expande una ventana
    alrededor de cada uno y marca las líneas cortas/simbólicas como fórmula.
    Sin anclas, recurre a detectar runs de líneas con símbolos matemáticos.

    Args:
        text: Texto extraído de una sección del paper.

    Returns:
        Texto con bloques de fórmulas reemplazados por '> [mathematical content — see PDF]'.
    """
    lines = text.splitlines()
    n = len(lines)

    # Paso 1: marcar líneas candidatas a fórmula
    def _is_formula_line(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        if _EQ_NUMBER_RE.match(s):
            return True
        if len(s) > 60:
            return False
        if _MATH_SYMBOL_RE.search(s):
            return True
        # Línea muy corta sin palabras completas (fragmento de ecuación)
        if len(s) <= 8 and not re.match(r"^[A-Za-z]{3,}\.?$", s):
            return True
        # Línea corta con = que parece asignación de variable: "Zi = softmax", "Qi = XWq"
        if len(s) <= 25 and re.match(r"^[A-Za-z]{1,4}[\d_]*\s*=\s*\S", s):
            return True
        return False

    # Paso 2: expandir ventana alrededor de anclas (números de ecuación)
    formula_set: set[int] = set()
    eq_anchors = [i for i, l in enumerate(lines) if _EQ_NUMBER_RE.match(l.strip())]

    for anchor in eq_anchors:
        for k in range(max(0, anchor - 10), min(n, anchor + 3)):
            if _is_formula_line(lines[k]) or not lines[k].strip():
                formula_set.add(k)
        formula_set.add(anchor)

    # Paso 3: sin anclas, buscar runs de 3+ líneas con símbolos
    if not eq_anchors:
        run: list[int] = []
        for i, line in enumerate(lines):
            if _is_formula_line(line):
                run.append(i)
            else:
                if len(run) >= 3:
                    formula_set.update(run)
                run = []
        if len(run) >= 3:
            formula_set.update(run)

    # Paso 4: reconstruir texto reemplazando bloques contiguos
    result: list[str] = []
    in_formula = False
    for i, line in enumerate(lines):
        if i in formula_set:
            if not in_formula:
                result.append("> [mathematical content — see PDF]")
                in_formula = True
        else:
            in_formula = False
            result.append(line)

    return "\n".join(result)

# Headers de sección (match sobre la línea completa, stripped)
# Soporta headers numerados tipo "2. Methods", "II. Methods", etc.
_SECTION_PATTERNS: dict[str, re.Pattern] = {
    "abstract": re.compile(
        r"^(?:i[\.\s]+)?abstract$", re.IGNORECASE
    ),
    "keywords": re.compile(
        r"^(?:key\s*words?|index\s+terms?|palabras?\s*clave)$", re.IGNORECASE
    ),
    "introduction": re.compile(
        r"^(?:[ivxlcdm\d]+[\.\s]+)?introduction$", re.IGNORECASE
    ),
    "methods": re.compile(
        r"^(?:[ivxlcdm\d]+[\.\s]+)?(?:methods?|methodology|materials?\s+and\s+methods?|"
        r"experimental\s+(?:setup|design)|approach|proposed\s+method)$",
        re.IGNORECASE,
    ),
    "results": re.compile(
        r"^(?:[ivxlcdm\d]+[\.\s]+)?(?:results?|experiments?\s+and\s+results?|evaluation)$",
        re.IGNORECASE,
    ),
    "discussion": re.compile(
        r"^(?:[ivxlcdm\d]+[\.\s]+)?discussion$", re.IGNORECASE
    ),
    "conclusions": re.compile(
        r"^(?:[ivxlcdm\d]+[\.\s]+)?(?:conclusions?|concluding\s+remarks?|"
        r"summary(?:\s+and\s+conclusions?)?)$",
        re.IGNORECASE,
    ),
    "references": re.compile(
        r"^(?:[ivxlcdm\d]+[\.\s]+)?(?:references?|bibliography|works?\s+cited)$",
        re.IGNORECASE,
    ),
}

# Límites de chars por sección al enviar al LLM
_SECTION_LIMITS: dict[str, int] = {
    "abstract":    1500,
    "keywords":    300,
    "methods":     2000,
    "conclusions": 1500,
}


def detect_paper(text: str, metadata: dict) -> bool:
    """Detecta heurísticamente si un documento es un paper académico.

    Busca señales en los primeros 5000 chars del texto extraído.
    Dos o más señales → paper.

    Args:
        text: Texto extraído del PDF.
        metadata: Metadata del PDF (title, author, subject, pages).

    Returns:
        True si el documento parece un paper académico.
    """
    sample = text[:5000]
    hits = sum(1 for pattern in _PAPER_SIGNALS if pattern.search(sample))
    return hits >= 2


def _find_section_boundaries(lines: list[str]) -> dict[str, int]:
    """Encuentra los índices de línea donde comienza cada sección.

    Solo considera líneas cortas (headers son típicamente < 80 chars)
    y sin contenido de párrafo (no terminan con punto seguido de texto).

    Args:
        lines: Líneas del texto extraído.

    Returns:
        Dict {nombre_sección: índice_de_línea}.
    """
    found: dict[str, int] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 80:
            continue
        for name, pattern in _SECTION_PATTERNS.items():
            if name not in found and pattern.match(stripped):
                found[name] = i
                break
    return found


def _extract_section_text(
    lines: list[str],
    start: int,
    end: int,
    limit: int,
) -> str:
    """Extrae y trunca el texto entre dos índices de línea."""
    content = "\n".join(lines[start:end]).strip()
    if len(content) > limit:
        content = content[:limit] + "..."
    return content


def extract_paper_sections(text: str, metadata: dict) -> dict:
    """Extrae secciones clave de un paper para clasificación.

    Detecta headers de sección por nombre y extrae abstract, keywords,
    métodos y conclusiones. Si una sección no tiene header explícito,
    intenta inferirla por posición o patrones inline.

    Args:
        text: Texto completo extraído del PDF.
        metadata: Metadata del PDF (title, author, subject, pages).

    Returns:
        Dict con: title, authors, doi, abstract, keywords, methods, conclusions.
        Cada campo es string vacío si no se encontró.
    """
    lines = text.splitlines()
    boundaries = _find_section_boundaries(lines)

    # Orden de secciones según posición en el texto
    section_order = sorted(boundaries.items(), key=lambda x: x[1])
    ordered_names = [name for name, _ in section_order]

    def _get_section(target: str) -> str:
        if target not in boundaries:
            return ""
        start = boundaries[target] + 1
        # Fin: siguiente sección encontrada o fin del texto
        idx = ordered_names.index(target)
        if idx + 1 < len(ordered_names):
            end = boundaries[ordered_names[idx + 1]]
        else:
            end = len(lines)
        return _extract_section_text(lines, start, end, _SECTION_LIMITS[target])

    abstract = _get_section("abstract")

    # Fallback para abstract: buscar "Abstract:" o "Abstract—" inline
    if not abstract:
        m = re.search(r"abstract[:\u2014\-]\s*(.+?)(?=\n\n|\Z)", text[:4000], re.IGNORECASE | re.DOTALL)
        if m:
            abstract = m.group(1).strip()[:_SECTION_LIMITS["abstract"]]

    keywords = _get_section("keywords")

    # Fallback keywords: buscar "Keywords:" inline
    if not keywords:
        m = re.search(r"key\s*words?[:\u2014\-]\s*(.+?)(?=\n\n|\Z)", text[:6000], re.IGNORECASE)
        if m:
            keywords = m.group(1).strip()[:_SECTION_LIMITS["keywords"]]

    methods = _get_section("methods")
    conclusions = _get_section("conclusions")

    # DOI: primero en metadata, luego buscar en texto
    doi = metadata.get("doi", "").strip()
    if not doi:
        m = _DOI_RE.search(text[:3000])
        if m:
            doi = m.group(0).rstrip(".,;)")

    # Título: metadata del PDF primero; fallback a extracción del texto
    title = metadata.get("title", "").strip()
    if not title:
        title = _extract_title_from_text(lines)

    return {
        "title":       title,
        "authors":     metadata.get("author", "").strip(),
        "doi":         doi,
        "abstract":    abstract,
        "keywords":    keywords,
        "methods":     methods,
        "conclusions": conclusions,
    }


def build_classify_content(text: str, metadata: dict, is_paper: bool) -> str:
    """Construye el contenido compacto a enviar al LLM para clasificación.

    Para papers: extrae secciones clave (abstract, keywords, métodos,
    conclusiones) — típicamente ~3000 chars, dentro del límite de Groq.
    Para documentos genéricos: primeros 2500 + últimos 1000 chars.

    Args:
        text: Texto completo extraído del documento.
        metadata: Metadata del PDF.
        is_paper: True si se detectó como paper académico.

    Returns:
        String estructurado listo para enviar dentro de <input>...</input>.
    """
    if is_paper:
        sections = extract_paper_sections(text, metadata)
        parts: list[str] = []

        if sections["title"]:
            parts.append(f"TÍTULO: {sections['title']}")
        if sections["abstract"]:
            parts.append(f"\nABSTRACT:\n{_clean_formula_blocks(sections['abstract'])}")
        if sections["keywords"]:
            parts.append(f"\nKEYWORDS: {sections['keywords']}")
        if sections["methods"]:
            parts.append(f"\nMETHODS:\n{_clean_formula_blocks(sections['methods'])}")
        if sections["conclusions"]:
            parts.append(f"\nCONCLUSIONS:\n{_clean_formula_blocks(sections['conclusions'])}")

        result = "\n".join(parts)
        logger.info(
            "Paper: contenido para LLM construido (%d chars, desde %d chars originales)",
            len(result), len(text),
        )
        return result
    else:
        # Documento genérico: inicio + fin
        if len(text) <= 3500:
            return text
        content = text[:2500] + "\n\n[...]\n\n" + text[-1000:]
        logger.info(
            "Documento genérico: contenido truncado a %d chars (desde %d chars)",
            len(content), len(text),
        )
        return content


# ---------------------------------------------------------------------------
# Funciones públicas
# ---------------------------------------------------------------------------


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
