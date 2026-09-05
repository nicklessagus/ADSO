"""Búsqueda estructural sobre el vault de Obsidian.

Solo lectura. No escribe nada. No llama a APIs externas ni a ChromaDB.
Opera sobre archivos .md del filesystem.
Referencia: docs/vault-interface.md
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Optional

from adso.constants import DEFAULT_EXCLUDE_DIRS
from adso.vault_cache import parse_cached
from adso.vault_writer import NoteRef, NoteData

logger = logging.getLogger(__name__)

# Regex para extraer wikilinks (excluye code blocks)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?]]")

# Regex para extraer tags inline (no dentro de code blocks)
_INLINE_TAG_RE = re.compile(r"(?<!\[)#([\w/-]+)")

# Regex para checkboxes
_CHECKBOX_PENDING_RE = re.compile(r"^- \[ ] (.+)$", re.MULTILINE)
_CHECKBOX_DONE_RE = re.compile(r"^- \[x] (.+)$", re.MULTILINE)

# Default exclude dirs (la taxonomía vive en constants.py)
_DEFAULT_EXCLUDE = list(DEFAULT_EXCLUDE_DIRS)

# Tokens de `search()` que filtran por un campo del frontmatter con igualdad.
_FRONTMATTER_TOKENS = ("type", "status", "project", "area")

# `03-Resources/` es la carpeta de adjuntos según la taxonomía: un `.md` ahí no
# es una nota del vault. Se excluye **siempre**, no vía `exclude_dirs`, porque
# los callers reales pasan su propia lista (`_get_existing_tags` en
# `bot_utils.py` arma una literal para sacar `00-Inbox`) y ampliar solo el
# default habría dejado sin arreglar justo el camino que alimenta el prompt de
# clasificación. Misma forma que `_index.md` en `embeddings.should_index`: no es
# configurable porque no es una preferencia, es la taxonomía. Issue #58.
_ALWAYS_EXCLUDE = ("03-Resources",)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _scan_vault(
    vault_path: Path,
    exclude_dirs: Optional[list[str]] = None,
    scope: Optional[str] = None,
) -> list[Path]:
    """Lista todos los .md del vault respetando exclusiones.

    Args:
        vault_path: Raíz del vault.
        exclude_dirs: Carpetas a excluir. `03-Resources` se excluye siempre,
            la pase o no el caller (ver `_ALWAYS_EXCLUDE`).
        scope: Subdirectorio que restringe la búsqueda.

    Returns:
        Lista de Paths a archivos .md.
    """
    if exclude_dirs is None:
        exclude_dirs = _DEFAULT_EXCLUDE
    exclude_dirs = [*exclude_dirs, *_ALWAYS_EXCLUDE]

    base = vault_path / scope if scope else vault_path

    if not base.exists():
        return []

    results = []
    for md_file in base.rglob("*.md"):
        # Verificar si está en un dir excluido
        rel = md_file.relative_to(vault_path)
        parts = rel.parts
        if any(exc in parts for exc in exclude_dirs):
            continue
        results.append(md_file)

    return results


def _parse_note_safe(path: Path) -> Optional[NoteData]:
    """Lee una nota de forma segura. Retorna None si falla el parsing.

    No rompe la búsqueda si una nota tiene frontmatter inválido.
    Delega en `vault_cache.parse_cached`, que cachea el parse por
    (mtime, size) para evitar re-leer notas sin cambios en scans repetidos.
    """
    return parse_cached(path)


def _note_ref_from_data(note: NoteData, snippet: Optional[str] = None) -> NoteRef:
    """Construye un NoteRef desde un NoteData."""
    # Los valores se coaccionan a str: una nota editada a mano puede traer
    # `title: 2024` (int) o `status:` vacío (None) y NoteRef declara str.
    fm = note.frontmatter
    return NoteRef(
        path=note.path,
        title=str(fm.get("title") or note.path.stem),
        note_type=str(fm.get("type") or ""),
        status=str(fm.get("status") or ""),
        snippet=snippet,
    )


def _strip_code_blocks(text: str) -> str:
    """Remueve code blocks (fenced y inline) para no extraer links/tags falsos."""
    # Fenced code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Inline code
    text = re.sub(r"`[^`]+`", "", text)
    # Obsidian comments %%...%%
    return re.sub(r"%%[\s\S]*?%%", "", text)


def _extract_tags_from_note(note: NoteData) -> set[str]:
    """Extrae todos los tags de una nota (frontmatter + inline)."""
    tags = set()

    # Tags del frontmatter
    fm_tags = note.frontmatter.get("tags", [])
    if isinstance(fm_tags, str):
        # Nota editada a mano con `tags: foo, bar` (string en vez de lista).
        fm_tags = [p for p in fm_tags.split(",") if p.strip()]
    if isinstance(fm_tags, list):
        for t in fm_tags:
            tags.add(str(t).strip().lower().lstrip("#"))

    # Tags inline del body
    clean_body = _strip_code_blocks(note.body)
    for match in _INLINE_TAG_RE.finditer(clean_body):
        tags.add(match.group(1).lower())

    return tags


# ---------------------------------------------------------------------------
# Funciones públicas
# ---------------------------------------------------------------------------


async def get_backlinks(
    note_name: str,
    vault_path: Path,
    exclude_dirs: Optional[list[str]] = None,
) -> list[NoteRef]:
    """Encuentra todas las notas que referencian a note_name con [[wikilink]].

    Args:
        note_name: Stem del archivo (sin .md, sin path).
        vault_path: Raíz del vault.
        exclude_dirs: Carpetas a excluir del escaneo.

    Returns:
        Lista de NoteRef ordenada por path.
    """
    # Regex que matchea todas las formas de referenciar
    pattern = re.compile(
        r"\[\[" + re.escape(note_name) + r"(?:[|#][^\]]*)?\]\]"
    )

    def _scan() -> list[NoteRef]:
        results = []
        for md_path in _scan_vault(vault_path, exclude_dirs):
            # No buscar backlinks en la nota misma
            if md_path.stem == note_name:
                continue

            note = _parse_note_safe(md_path)
            if note is None:
                continue

            clean_body = _strip_code_blocks(note.body)
            match = pattern.search(clean_body)
            if match:
                # Snippet: la línea completa que contiene el match
                for line in clean_body.splitlines():
                    if pattern.search(line):
                        snippet = line.strip()
                        break
                else:
                    snippet = None
                results.append(_note_ref_from_data(note, snippet))

        results.sort(key=lambda r: r.path)
        return results

    return await asyncio.to_thread(_scan)


async def search(
    query: str,
    vault_path: Path,
    scope: Optional[str] = None,
    exclude_dirs: Optional[list[str]] = None,
) -> list[NoteRef]:
    """Búsqueda combinada con tokens especiales y texto libre.

    Tokens soportados: tag:X, type:X, status:X, project:X, area:X, path:X, file:X

    Args:
        query: String de búsqueda con tokens opcionales.
        vault_path: Raíz del vault.
        scope: Subdirectorio que restringe la búsqueda.
        exclude_dirs: Carpetas a excluir.

    Returns:
        Lista de NoteRef ordenada por relevancia.
    """
    # Parsear tokens
    tokens: dict[str, list[str]] = {}
    free_text_parts: list[str] = []

    for word in query.split():
        if ":" in word and not word.startswith("http"):
            key, value = word.split(":", 1)
            key_lower = key.lower()
            if key_lower in (*_FRONTMATTER_TOKENS, "tag", "path", "file"):
                tokens.setdefault(key_lower, []).append(value.lower())
                continue
        free_text_parts.append(word)

    free_text = " ".join(free_text_parts).lower()

    def _scan() -> list[NoteRef]:
        title_exact: list[NoteRef] = []
        title_partial: list[NoteRef] = []
        body_match: list[NoteRef] = []

        for md_path in _scan_vault(vault_path, exclude_dirs, scope):
            note = _parse_note_safe(md_path)
            if note is None:
                continue

            fm = note.frontmatter

            # Aplicar filtros por tokens
            skip = any(
                str(fm.get(field) or "").lower() not in tokens[field]
                for field in _FRONTMATTER_TOKENS
                if field in tokens
            )
            if "tag" in tokens:
                note_tags = _extract_tags_from_note(note)
                if not any(t in note_tags for t in tokens["tag"]):
                    skip = True
            if "path" in tokens:
                rel = str(md_path.relative_to(vault_path)).lower()
                if not any(p in rel for p in tokens["path"]):
                    skip = True
            if "file" in tokens and not any(
                f in md_path.stem.lower() for f in tokens["file"]
            ):
                skip = True

            if skip:
                continue

            # Si no hay texto libre, incluir todo lo que pasó los filtros
            if not free_text:
                title_exact.append(_note_ref_from_data(note))
                continue

            # Búsqueda por texto libre
            title = str(fm.get("title") or "").lower()
            body_lower = note.body.lower()

            if free_text in title:
                title_exact.append(_note_ref_from_data(note, _snippet(note.body, free_text)))
            elif any(w in title for w in free_text.split()):
                title_partial.append(_note_ref_from_data(note, _snippet(note.body, free_text)))
            elif free_text in body_lower:
                body_match.append(_note_ref_from_data(note, _snippet(note.body, free_text)))

        return title_exact + title_partial + body_match

    return await asyncio.to_thread(_scan)


def _snippet(body: str, query: str, context_chars: int = 100) -> Optional[str]:
    """Extrae un snippet del body alrededor de la primera ocurrencia de query."""
    idx = body.lower().find(query.lower())
    if idx == -1:
        return None
    start = max(0, idx - context_chars)
    end = min(len(body), idx + len(query) + context_chars)
    snip = body[start:end].strip()
    if start > 0:
        snip = "..." + snip
    if end < len(body):
        snip = snip + "..."
    return snip


async def find_by_tag(
    tag: str,
    vault_path: Path,
    hierarchical: bool = True,
) -> list[NoteRef]:
    """Encuentra notas que contengan un tag específico.

    Args:
        tag: Tag a buscar (con o sin #).
        vault_path: Raíz del vault.
        hierarchical: Si True, tag:metodo matchea metodo/cnn.

    Returns:
        Lista de NoteRef.
    """
    normalized = tag.lower().lstrip("#")

    def _scan() -> list[NoteRef]:
        results = []
        for md_path in _scan_vault(vault_path):
            note = _parse_note_safe(md_path)
            if note is None:
                continue

            note_tags = _extract_tags_from_note(note)

            matched = any(
                nt == normalized or (hierarchical and nt.startswith(normalized + "/"))
                for nt in note_tags
            )
            if matched:
                results.append(_note_ref_from_data(note))

        return results

    return await asyncio.to_thread(_scan)


async def find_by_property(
    key: str,
    value: Optional[Any],
    vault_path: Path,
    scope: Optional[str] = None,
) -> list[NoteRef]:
    """Encuentra notas por un campo del frontmatter.

    Args:
        key: Nombre del campo.
        value: Valor a buscar. None = tiene el campo con cualquier valor.
        vault_path: Raíz del vault.
        scope: Subdirectorio que restringe la búsqueda.

    Returns:
        Lista de NoteRef.
    """

    def _scan() -> list[NoteRef]:
        results = []
        for md_path in _scan_vault(vault_path, scope=scope):
            note = _parse_note_safe(md_path)
            if note is None:
                continue

            fm = note.frontmatter
            if key not in fm:
                continue

            if value is None:
                # Solo verificar que el campo existe
                results.append(_note_ref_from_data(note))
                continue

            fm_value = fm[key]
            str_value = str(value).lower()

            if isinstance(fm_value, list):
                # Buscar si value está contenido en la lista
                if any(str(v).lower() == str_value for v in fm_value):
                    results.append(_note_ref_from_data(note))
            else:
                # Comparación case-insensitive
                if str(fm_value).lower() == str_value:
                    results.append(_note_ref_from_data(note))

        return results

    return await asyncio.to_thread(_scan)


async def find_tasks(
    vault_path: Path,
    status: Optional[str] = None,
    area: Optional[str] = None,
    project: Optional[str] = None,
    include_inline: bool = True,
) -> list[NoteRef]:
    """Encuentra tareas en el vault.

    Fuente 1: notas con type: task.
    Fuente 2: checkboxes inline - [ ] / - [x] en cualquier nota.

    Args:
        vault_path: Raíz del vault.
        status: Filtrar por status (pending, done, etc.).
        area: Filtrar por área.
        project: Filtrar por proyecto.
        include_inline: Incluir checkboxes inline.

    Returns:
        Lista combinada de NoteRef.
    """

    def _scan() -> list[NoteRef]:
        results = []
        seen_paths: set[Path] = set()

        for md_path in _scan_vault(vault_path):
            note = _parse_note_safe(md_path)
            if note is None:
                continue

            fm = note.frontmatter

            # Fuente 1: notas type: task
            is_task_note = fm.get("type") == "task"
            if is_task_note:
                if status and fm.get("status", "") != status:
                    continue
                if area and fm.get("area", "") != area:
                    continue
                if project and fm.get("project", "") != project:
                    continue
                results.append(_note_ref_from_data(note))
                seen_paths.add(md_path)

            # Fuente 2: checkboxes inline (incluye notas type: task).
            #
            # Una nota `type: task` aporta la nota Y cada uno de sus checkboxes:
            # los checkboxes de una tarea son sus subtareas y se listan como
            # ítems propios (ver `test_inline_checkboxes_included`). `seen_paths`
            # se puebla pero no se consulta: es residuo de un diseño anterior que
            # deduplicaba. Ver #61 — la asimetría real entre las dos fuentes es
            # que la 1 filtra por status+area+project y la 2 solo por
            # area+project, y que el `continue` de la 1 silencia los checkboxes
            # de una tarea filtrada.
            if include_inline:
                if status == "done":
                    matches = _CHECKBOX_DONE_RE.findall(note.body)
                elif status == "pending":
                    matches = _CHECKBOX_PENDING_RE.findall(note.body)
                elif status is None:
                    matches = (
                        _CHECKBOX_PENDING_RE.findall(note.body)
                        + _CHECKBOX_DONE_RE.findall(note.body)
                    )
                else:
                    matches = []

                for m in matches:
                    if area and fm.get("area", "") != area:
                        continue
                    if project and fm.get("project", "") != project:
                        continue
                    results.append(_note_ref_from_data(note, snippet=m.strip()))

        return results

    return await asyncio.to_thread(_scan)


async def get_wikilinks(note_path: Path) -> list[str]:
    """Extrae todos los outgoing wikilinks de una nota.

    Args:
        note_path: Path al archivo .md.

    Returns:
        Lista de stems (sin alias, sin heading, sin .md). Sin duplicados.
    """
    raw = await asyncio.to_thread(note_path.read_text, "utf-8")
    clean = _strip_code_blocks(raw)

    seen: set[str] = set()
    result: list[str] = []
    for match in _WIKILINK_RE.finditer(clean):
        stem = match.group(1).strip()
        if stem not in seen:
            seen.add(stem)
            result.append(stem)
    return result


async def get_all_tags(
    vault_path: Path,
    exclude_dirs: Optional[list[str]] = None,
) -> dict[str, int]:
    """Retorna todos los tags del vault con frecuencias.

    Args:
        vault_path: Raíz del vault.
        exclude_dirs: Carpetas a excluir.

    Returns:
        Dict {tag: count} ordenado por frecuencia descendente.
    """

    def _scan() -> dict[str, int]:
        counts: dict[str, int] = {}
        for md_path in _scan_vault(vault_path, exclude_dirs):
            note = _parse_note_safe(md_path)
            if note is None:
                continue
            for tag in _extract_tags_from_note(note):
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    return await asyncio.to_thread(_scan)


async def scan_notes(
    vault_path: Path,
    scope: Optional[str] = None,
    exclude_dirs: Optional[list[str]] = None,
    filters: Optional[dict] = None,
) -> list[NoteData]:
    """Retorna NoteData completos (con todo el frontmatter) para notas que cumplan los filtros.

    Args:
        vault_path: Raíz del vault.
        scope: Subdirectorio que restringe la búsqueda (relativo a vault_path).
        exclude_dirs: Carpetas a excluir (default: _DEFAULT_EXCLUDE).
        filters: Dict {campo: valor} — comparación case-insensitive.
            El valor puede ser str, list[str] (OR), o None (campo existe con cualquier valor).

    Returns:
        Lista de NoteData que cumplan todos los filtros.
    """

    def _scan() -> list[NoteData]:
        results = []
        for md_path in _scan_vault(vault_path, exclude_dirs, scope):
            note = _parse_note_safe(md_path)
            if note is None:
                continue

            if filters:
                skip = False
                for field_name, expected in filters.items():
                    fm_value = note.frontmatter.get(field_name)
                    if expected is None:
                        # Solo verificar que el campo existe
                        if fm_value is None:
                            skip = True
                            break
                    elif isinstance(expected, list):
                        # OR: el valor del frontmatter debe estar en la lista
                        fm_str = str(fm_value).lower() if fm_value is not None else ""
                        if not any(str(v).lower() == fm_str for v in expected):
                            skip = True
                            break
                    else:
                        # Comparación directa case-insensitive
                        if fm_value is None or str(fm_value).lower() != str(expected).lower():
                            skip = True
                            break
                if skip:
                    continue

            results.append(note)
        return results

    return await asyncio.to_thread(_scan)


async def get_note_index(vault_path: Path) -> dict[str, Path]:
    """Construye un índice de los .md del vault, direccionable por stem.

    Los stems repetidos (los `_index.md`, uno por proyecto y área) no se pisan:
    el primero conserva su clave por stem y **todos** los homónimos se exponen
    además bajo su ruta relativa sin extensión — el mismo `note_id` que usa el
    índice de embeddings. B7 de la auditoría 2026-08.

    Args:
        vault_path: Raíz del vault.

    Returns:
        Dict con dos clases de clave: `{stem: Path}` para los stems únicos y
        `{ruta/relativa/sin-extension: Path}` para los que colisionan. Iterar
        las claves **no** equivale a iterar las notas: usar `.values()`.
    """

    def _scan() -> dict[str, Path]:
        index: dict[str, Path] = {}
        colisiones: set[str] = set()
        for md_path in _scan_vault(vault_path):
            stem = md_path.stem
            if stem not in index:
                index[stem] = md_path
                continue
            # Stems repetidos: `index[stem] = path` hacía que ganara el último
            # que devolviera rglob y los demás desaparecieran del índice. En un
            # vault real son los `_index.md` — uno por proyecto y área, todos
            # con el mismo stem. Las entradas ambiguas se exponen además bajo su
            # ruta relativa sin extensión, que es el mismo `note_id` que usa el
            # índice de embeddings.
            if stem not in colisiones:
                primero = index[stem]
                index[str(primero.relative_to(vault_path).with_suffix(""))] = primero
                colisiones.add(stem)
            index[str(md_path.relative_to(vault_path).with_suffix(""))] = md_path
        return index

    return await asyncio.to_thread(_scan)
