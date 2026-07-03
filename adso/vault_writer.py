"""Escritura y modificación de archivos .md en el vault de Obsidian.

No llama a LLMs ni a ChromaDB. Toda operación es async.
Referencia: docs/vault-interface.md
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import frontmatter
from slugify import slugify

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tipos de datos
# ---------------------------------------------------------------------------


@dataclass
class NoteRef:
    """Referencia ligera a una nota. Se usa en resultados de búsqueda."""

    path: Path
    title: str
    note_type: str
    status: str
    snippet: Optional[str]


@dataclass
class NoteData:
    """Contenido completo de una nota."""

    path: Path
    frontmatter: dict
    body: str


# ---------------------------------------------------------------------------
# Constantes de validación
# ---------------------------------------------------------------------------

# Campos de fecha que deben serializarse como tipo nativo YAML (sin comillas)
# para que Obsidian los reconozca como Date & time / Date en Properties.
DATE_FIELDS = {"date_created", "date_modified", "due_date", "scheduled"}

# Patrones para detectar strings ISO 8601
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

VALID_TYPES = {"reference", "task", "idea", "project-index", "area-index"}

VALID_STATUS: dict[str, set[str]] = {
    "reference": {"active", "pending-classification"},
    "task": {"pending", "in-progress", "done", "pending-classification"},
    "idea": {"raw", "implemented", "discarded", "pending-classification"},
    "project-index": {"active", "on-hold", "completed", "archived"},
    "area-index": set(),
}

VALID_PRIORITY = {"low", "medium", "high"}
VALID_MEDIA = {"text", "audio", "image", "link", "document"}
VALID_SOURCE = {"telegram", "system"}

VAULT_DIRS = ["00-Inbox", "01-Projects", "02-Areas", "03-Resources", "05-Archive"]

MAX_SLUG_LENGTH = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Retorna timestamp actual en ISO 8601."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write_sync(path: Path, content: str) -> None:
    """Escribe ``content`` a ``path`` de forma atómica.

    Escribe a un temporal en el mismo directorio, hace fsync y luego
    ``os.replace`` (rename atómico en el mismo filesystem). Si el proceso muere
    a mitad de la escritura (OOM en RPi4, ``docker stop``), el archivo destino
    queda intacto — nunca truncado ni vacío. Regla de oro: sin pérdida de datos.

    Debe correr en un thread (``asyncio.to_thread``): hace I/O bloqueante.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".adso-tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _parse_date_value(value: str) -> "date | datetime | str":
    """Convierte un string ISO 8601 a objeto date/datetime para serialización YAML sin comillas.

    Devuelve date para fechas sin hora (YYYY-MM-DD) y datetime para fechas con hora.
    Los objetos nativos son serializados por PyYAML como timestamps YAML sin comillas,
    lo que permite que Obsidian los reconozca como tipo Date & time en Properties.
    Devuelve el valor original si no coincide con ningún patrón.
    """
    if _DATE_ONLY_RE.match(value):
        return date.fromisoformat(value)
    if _DATETIME_RE.match(value):
        return datetime.fromisoformat(value)
    return value


def _make_filename(title: str, date_val: "Optional[str | date | datetime]" = None) -> str:
    """Genera nombre de archivo: YYYY-MM-DD-slug.md.

    Args:
        title: Título de la nota.
        date_val: Fecha ISO 8601 (str) o date/datetime para el prefijo. Si None, usa hoy.

    Returns:
        Nombre de archivo como string.
    """
    if date_val:
        if isinstance(date_val, (datetime, date)):
            prefix = date_val.strftime("%Y-%m-%d")
        else:
            prefix = str(date_val)[:10]  # YYYY-MM-DD desde string ISO
    else:
        prefix = datetime.now().strftime("%Y-%m-%d")

    slug_text = slugify(title, max_length=MAX_SLUG_LENGTH, word_boundary=True)
    if not slug_text:
        slug_text = "nota"

    return f"{prefix}-{slug_text}.md"


def _safe_component(name: Any) -> Optional[str]:
    """Sanitiza un componente de path (project/area/section) contra path traversal.

    Estos valores vienen del LLM (que procesa contenido externo susceptible a
    injection) o de comandos del usuario, y se concatenan al path del vault. Un
    valor como ``"../../etc"`` escribiría fuera del vault.

    Returns:
        El nombre limpio (stripped) si es un único componente seguro, o None si
        es inválido/vacío/con traversal. El caller debe tratar None como
        "sin destino" (→ Inbox o preguntar al usuario).
    """
    if not isinstance(name, str):
        return None
    cleaned = name.strip()
    if not cleaned or cleaned in (".", ".."):
        return None
    if cleaned.startswith("."):
        return None
    if any(sep in cleaned for sep in ("/", "\\", "\x00")):
        return None
    # Path(...).name descarta cualquier componente de directorio; si difiere del
    # original, el valor contenía separadores u otra construcción de path.
    if Path(cleaned).name != cleaned:
        return None
    return cleaned


def _resolve_dest_dir(fm: dict, vault_path: Path) -> Optional[Path]:
    """Calcula el directorio destino según el frontmatter.

    Returns:
        Path del directorio destino, o None si el destino no se puede resolver
        (nota sin proyecto ni área — el caller debe preguntar al usuario).
    """
    note_type = fm.get("type", "idea")
    project = _safe_component(fm.get("project"))
    section = _safe_component(fm.get("section"))
    area = _safe_component(fm.get("area"))

    if note_type == "project-index":
        if project:
            return vault_path / "01-Projects" / project
        return vault_path / "00-Inbox"

    if note_type == "area-index":
        if area:
            return vault_path / "02-Areas" / area
        return vault_path / "00-Inbox"

    if note_type == "reference":
        if project:
            base = vault_path / "01-Projects" / project
            if section:
                return base / section
            return base
        if area:
            return vault_path / "02-Areas" / area
        # Sin proyecto ni área → caller decide
        return None

    if note_type == "task":
        if project:
            base = vault_path / "01-Projects" / project
            if section:
                return base / section
            return base
        if area:
            return vault_path / "02-Areas" / area
        return vault_path / "00-Inbox"

    if note_type == "idea":
        if project:
            base = vault_path / "01-Projects" / project
            if section:
                return base / section
            return base
        if area:
            return vault_path / "02-Areas" / area
        return None

    return vault_path / "00-Inbox"


def _unique_path(dest_dir: Path, filename: str) -> Path:
    """Retorna un path único, agregando sufijo numérico si hay colisión."""
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        candidate = dest_dir / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _clean_frontmatter(fm: dict) -> dict:
    """Limpia el frontmatter: remueve None y convierte fechas a objetos nativos.

    Los campos de DATE_FIELDS se convierten de strings ISO 8601 a objetos date/datetime
    para que PyYAML los serialice como timestamps YAML sin comillas, habilitando el
    tipo Date & time en la UI de Properties de Obsidian.
    """
    result = {}
    for k, v in fm.items():
        if v is None:
            continue
        if isinstance(k, str) and k.startswith("_"):
            # Convención: claves con prefijo `_` son estado interno del bot
            # (flags de flujo, metadatos transitorios) y nunca se persisten.
            continue
        if k in DATE_FIELDS and isinstance(v, str):
            result[k] = _parse_date_value(v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Funciones públicas
# ---------------------------------------------------------------------------


async def create_note(
    note_frontmatter: dict,
    body: str,
    vault_path: Path,
    dry_run: bool = False,
) -> Path:
    """Crea una nota .md en el vault.

    Args:
        note_frontmatter: Dict con campos del frontmatter YAML.
        body: Cuerpo de la nota en Markdown.
        vault_path: Path raíz del vault.
        dry_run: Si True, retorna el path sin escribir.

    Returns:
        Path absoluto del archivo creado (o que se crearía en dry_run).

    Raises:
        ValueError: Si falta title o type en el frontmatter.
        PermissionError: Si no hay permisos de escritura.
    """
    if "title" not in note_frontmatter or not note_frontmatter["title"]:
        raise ValueError("El frontmatter requiere 'title'")
    if "type" not in note_frontmatter:
        raise ValueError("El frontmatter requiere 'type'")

    fm = dict(note_frontmatter)

    # Setear campos automáticos si no vienen
    now = _now_iso()
    fm.setdefault("date_created", now)
    fm.setdefault("date_modified", now)
    fm.setdefault("source", "telegram")
    fm.setdefault("media_type", "text")

    # Calcular destino
    dest_dir = _resolve_dest_dir(fm, vault_path)
    if dest_dir is None:
        # Fallback a Inbox si no se puede resolver
        dest_dir = vault_path / "00-Inbox"

    # Defensa en profundidad: nunca escribir fuera del vault, pase lo que pase
    # con los componentes del frontmatter.
    if not dest_dir.resolve().is_relative_to(vault_path.resolve()):
        logger.warning("Destino fuera del vault (%s) — redirigiendo a Inbox", dest_dir)
        dest_dir = vault_path / "00-Inbox"

    # Nombre del archivo
    note_type = fm.get("type")
    if note_type in ("project-index", "area-index"):
        filename = "_index.md"
    else:
        filename = _make_filename(fm["title"], fm.get("date_created"))

    # Path único
    file_path = _unique_path(dest_dir, filename)

    if dry_run:
        return file_path

    # Crear directorios intermedios
    await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)

    # Construir contenido con python-frontmatter
    clean_fm = _clean_frontmatter(fm)
    post = frontmatter.Post(body, **clean_fm)
    content = frontmatter.dumps(post)

    # Escribir archivo (atómico: temp + fsync + replace)
    await asyncio.to_thread(_atomic_write_sync, file_path, content)

    logger.info("Nota creada: %s", file_path)
    return file_path


async def read_note(note_path: Path) -> NoteData:
    """Lee una nota del vault.

    Args:
        note_path: Path absoluto al archivo .md.

    Returns:
        NoteData con frontmatter y body.

    Raises:
        FileNotFoundError: Si el archivo no existe.
        ValueError: Si no tiene frontmatter YAML válido.
    """
    if not note_path.exists():
        raise FileNotFoundError(f"Nota no encontrada: {note_path}")

    raw = await asyncio.to_thread(note_path.read_text, "utf-8")
    post = frontmatter.loads(raw)

    if not post.metadata:
        raise ValueError(f"Sin frontmatter YAML válido: {note_path}")

    return NoteData(
        path=note_path,
        frontmatter=dict(post.metadata),
        body=post.content,
    )


async def append_to_note(
    note_path: Path,
    content: str,
    separator: str = "\n\n---\n\n",
) -> None:
    """Agrega contenido al final de una nota existente.

    Args:
        note_path: Path al archivo .md.
        content: Texto a agregar.
        separator: Separador entre contenido existente y nuevo.

    Raises:
        FileNotFoundError: Si el archivo no existe.
    """
    note = await read_note(note_path)
    new_body = note.body + separator + content

    note.frontmatter["date_modified"] = _now_iso()

    clean_fm = _clean_frontmatter(note.frontmatter)
    post = frontmatter.Post(new_body, **clean_fm)
    output = frontmatter.dumps(post)

    await asyncio.to_thread(_atomic_write_sync, note_path, output)


async def set_property(
    note_path: Path,
    key: str,
    value: Any,
    update_date_modified: bool = True,
) -> None:
    """Modifica un campo del frontmatter de una nota existente.

    Args:
        note_path: Path al archivo .md.
        key: Nombre del campo a modificar.
        value: Nuevo valor.
        update_date_modified: Si True, actualiza date_modified.

    Raises:
        FileNotFoundError: Si no existe.
        ValueError: Si la validación del campo falla.
    """
    note = await read_note(note_path)
    fm = note.frontmatter

    # Validaciones por campo
    note_type = fm.get("type", "")

    if key == "type":
        if value not in VALID_TYPES:
            raise ValueError(f"type inválido: {value!r} (válidos: {VALID_TYPES})")

    if key == "status":
        valid = VALID_STATUS.get(note_type, set())
        if valid and value not in valid:
            raise ValueError(
                f"status inválido para type={note_type!r}: {value!r} "
                f"(válidos: {valid})"
            )

    if key == "priority":
        if value not in VALID_PRIORITY:
            raise ValueError(f"priority inválido: {value!r} (válidos: {VALID_PRIORITY})")

    if key == "media_type":
        if value not in VALID_MEDIA:
            raise ValueError(f"media_type inválido: {value!r} (válidos: {VALID_MEDIA})")

    if key == "source":
        if value not in VALID_SOURCE:
            raise ValueError(f"source inválido: {value!r} (válidos: {VALID_SOURCE})")

    if key in ("date_created", "date_modified", "due_date", "scheduled"):
        if value is not None:
            try:
                datetime.fromisoformat(str(value))
            except ValueError:
                raise ValueError(f"{key} debe ser ISO 8601: {value!r}")

    if key == "tags":
        if not isinstance(value, list):
            raise ValueError(f"tags debe ser una lista: {value!r}")

    # Aplicar cambio
    fm[key] = value

    if update_date_modified and key != "date_modified":
        fm["date_modified"] = _now_iso()

    clean_fm = _clean_frontmatter(fm)
    post = frontmatter.Post(note.body, **clean_fm)
    output = frontmatter.dumps(post)

    await asyncio.to_thread(_atomic_write_sync, note_path, output)


async def delete_note(note_path: Path) -> None:
    """Elimina una nota del filesystem.

    No toca ChromaDB — el caller es responsable de limpiar embeddings.

    Args:
        note_path: Path al archivo .md.

    Raises:
        FileNotFoundError: Si no existe.
    """
    if not note_path.exists():
        raise FileNotFoundError(f"Nota no encontrada: {note_path}")

    await asyncio.to_thread(note_path.unlink)
    logger.info("Nota eliminada: %s", note_path)


async def move_note(source: Path, dest_dir: Path) -> Path:
    """Mueve una nota a otro directorio.

    Args:
        source: Path actual del archivo .md.
        dest_dir: Directorio destino.

    Returns:
        Nuevo path del archivo.

    Raises:
        FileNotFoundError: Si source no existe.
    """
    if not source.exists():
        raise FileNotFoundError(f"Nota no encontrada: {source}")

    await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)

    dest_path = _unique_path(dest_dir, source.name)
    await asyncio.to_thread(source.rename, dest_path)

    logger.info("Nota movida: %s → %s", source, dest_path)
    return dest_path


async def update_wikilinks(
    note_path: Path,
    old_name: str,
    new_name: str,
) -> None:
    """Actualiza wikilinks en una nota (renombrado).

    Reemplaza [[old_name]] → [[new_name]] preservando alias.

    Args:
        note_path: Path al archivo .md a modificar.
        old_name: Nombre viejo del stem.
        new_name: Nombre nuevo del stem.
    """
    raw = await asyncio.to_thread(note_path.read_text, "utf-8")

    # [[old_name]] → [[new_name]]
    pattern_simple = re.compile(
        r"\[\[" + re.escape(old_name) + r"\]\]"
    )
    # [[old_name|alias]] → [[new_name|alias]]
    pattern_alias = re.compile(
        r"\[\[" + re.escape(old_name) + r"(\|[^\]]+)\]\]"
    )
    # [[old_name#heading]] → [[new_name#heading]]
    pattern_heading = re.compile(
        r"\[\[" + re.escape(old_name) + r"(#[^\]|]+)\]\]"
    )
    # [[old_name#heading|alias]] → [[new_name#heading|alias]]
    pattern_heading_alias = re.compile(
        r"\[\[" + re.escape(old_name) + r"(#[^\]|]+)(\|[^\]]+)\]\]"
    )

    new_content = raw
    new_content = pattern_heading_alias.sub(
        f"[[{new_name}\\1\\2]]", new_content
    )
    new_content = pattern_heading.sub(
        f"[[{new_name}\\1]]", new_content
    )
    new_content = pattern_alias.sub(
        f"[[{new_name}\\1]]", new_content
    )
    new_content = pattern_simple.sub(
        f"[[{new_name}]]", new_content
    )

    if new_content != raw:
        # Actualizar date_modified en frontmatter
        post = frontmatter.loads(new_content)
        clean_meta = _clean_frontmatter({**dict(post.metadata), "date_modified": _now_iso()})
        final_post = frontmatter.Post(post.content, **clean_meta)
        output = frontmatter.dumps(final_post)
        await asyncio.to_thread(_atomic_write_sync, note_path, output)
        logger.info("Wikilinks actualizados en: %s", note_path)


def _remove_empty_ver_tambien(content: str) -> str:
    """Elimina el header '## Ver también' si no quedan items de lista bajo él."""
    lines = content.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "## Ver también":
            # Buscar si hay algún item de lista antes del próximo heading o EOF
            j = i + 1
            has_items = False
            while j < len(lines):
                if lines[j].startswith("- [["):
                    has_items = True
                    break
                if lines[j].startswith("## "):
                    break
                j += 1
            if has_items:
                result.append(lines[i])
            else:
                # Saltar también las líneas en blanco que siguen al header
                while i + 1 < len(lines) and lines[i + 1].strip() == "":
                    i += 1
        else:
            result.append(lines[i])
        i += 1
    return "\n".join(result)


def _strip_broken_links_in_ver_tambien(content: str, link_re: "re.Pattern[str]") -> str:
    """Elimina items ``- [[stem]]`` rotos, pero SOLO dentro del bloque '## Ver también'.

    Recorre las líneas manteniendo el estado "dentro del bloque Ver también"
    (entre el header ``## Ver también`` y el siguiente ``## `` o EOF). Fuera de
    ese bloque, las líneas se preservan tal cual — así un wikilink que el usuario
    haya escrito en un párrafo o en otra lista nunca se borra.
    """
    lines = content.split("\n")
    result: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped == "## Ver también":
            in_block = True
            result.append(line)
            continue
        if in_block and stripped.startswith("## "):
            in_block = False
        if in_block and link_re.match(line):
            # Item de lista roto dentro del bloque → descartar la línea
            continue
        result.append(line)
    return "\n".join(result)


async def remove_broken_wikilinks(vault_path: Path, deleted_path: Path) -> int:
    """Elimina de todas las notas del vault wikilinks rotos que apuntaban a una nota borrada.

    Busca líneas del bloque '## Ver también' que referencien el stem del archivo borrado
    y las elimina. Si el bloque queda sin items, elimina también el header.

    Args:
        vault_path: Raíz del vault.
        deleted_path: Path del archivo .md borrado.

    Returns:
        Número de archivos modificados.
    """
    stem = deleted_path.stem
    # Ancla ^- \[\[stem...\]\]: solo líneas que son un item de lista con el wikilink.
    # Se aplica exclusivamente DENTRO del bloque "## Ver también" (ver más abajo)
    # para no borrar texto del usuario que use ese wikilink en otra parte de la nota.
    link_re = re.compile(
        r"^- \[\[" + re.escape(stem) + r"(?:[|#][^\]]+)?\]\].*$"
    )

    modified = 0
    for md_path in vault_path.rglob("*.md"):
        if md_path == deleted_path or md_path.stem == "_index":
            continue
        try:
            raw = await asyncio.to_thread(md_path.read_text, "utf-8")
        except OSError as exc:
            logger.warning("No se pudo leer %s para limpiar wikilinks: %s", md_path, exc)
            continue

        if f"[[{stem}]]" not in raw and f"[[{stem}|" not in raw and f"[[{stem}#" not in raw:
            continue

        new_content = _strip_broken_links_in_ver_tambien(raw, link_re)
        new_content = _remove_empty_ver_tambien(new_content)
        new_content = new_content.rstrip("\n") + "\n"

        if new_content == raw:
            continue

        try:
            await asyncio.to_thread(_atomic_write_sync, md_path, new_content)
        except OSError as exc:
            logger.warning("No se pudo escribir %s al limpiar wikilinks: %s", md_path, exc)
            continue
        modified += 1
        logger.info("Wikilink roto eliminado: %s → [[%s]]", md_path.name, stem)

    return modified


# ---------------------------------------------------------------------------
# Vault structure
# ---------------------------------------------------------------------------


async def ensure_vault_structure(vault_path: Path) -> None:
    """Crea la estructura de carpetas del vault si no existe.

    Args:
        vault_path: Path raíz del vault.
    """
    for d in VAULT_DIRS:
        dir_path = vault_path / d
        await asyncio.to_thread(dir_path.mkdir, parents=True, exist_ok=True)
    logger.info("Estructura del vault verificada: %s", vault_path)


async def seed_vault(vault_path: Path, vault_seed: Any) -> None:
    """Siembra proyectos y áreas iniciales desde config.

    Args:
        vault_path: Path raíz del vault.
        vault_seed: VaultSeedConfig con proyectos y áreas a crear.
    """
    for project in vault_seed.projects:
        project_dir = vault_path / "01-Projects" / project.name
        index_path = project_dir / "_index.md"
        if index_path.exists():
            continue
        fm = {
            "title": project.name.replace("-", " ").title(),
            "type": "project-index",
            "status": "active",
            "description": project.description,
            "sections": [],
            "tags": [project.name],
            "source": "system",
            "project": project.name,
        }
        body = (
            f"# {fm['title']}\n\n"
            f"## Descripción\n{project.description}\n\n"
            f"## Secciones\n\n"
            f"## Estado\n- Creado: {_now_iso()[:10]}\n"
        )
        await create_note(fm, body, vault_path)
        logger.info("Proyecto seed creado: %s", project.name)

    for area in vault_seed.areas:
        area_dir = vault_path / "02-Areas" / area.name
        index_path = area_dir / "_index.md"
        if index_path.exists():
            continue
        fm = {
            "title": area.name.replace("-", " ").title(),
            "type": "area-index",
            "description": area.description,
            "source": "system",
            "area": area.name,
        }
        body = (
            f"# {fm['title']}\n\n"
            f"## Descripción\n{area.description}\n"
        )
        await create_note(fm, body, vault_path)
        logger.info("Área seed creada: %s", area.name)


# ---------------------------------------------------------------------------
# Guardar archivos en Resources
# ---------------------------------------------------------------------------


async def save_resource(
    source_path: Path,
    original_filename: str,
    vault_path: Path,
) -> Path:
    """Copia un archivo a 03-Resources/ en el vault.

    Si ya existe un archivo con el mismo nombre, agrega sufijo numérico.

    Args:
        source_path: Path al archivo temporal a copiar.
        original_filename: Nombre original del archivo.
        vault_path: Raíz del vault.

    Returns:
        Path al archivo copiado en el vault.

    Raises:
        FileNotFoundError: Si source_path no existe.
    """
    import shutil

    if not source_path.exists():
        raise FileNotFoundError(f"Archivo fuente no encontrado: {source_path}")

    resources_dir = vault_path / "03-Resources"
    resources_dir.mkdir(parents=True, exist_ok=True)

    # Strip directory components to prevent path traversal (e.g. "../../.env").
    # Path(...).name keeps only the final component regardless of separators.
    safe_name = Path(original_filename).name or "resource"
    dest = resources_dir / safe_name

    # Si ya existe con el mismo nombre y tamaño, reutilizar — no duplicar
    if dest.exists() and dest.stat().st_size == source_path.stat().st_size:
        logger.info("Recurso ya existe (mismo nombre y tamaño), reutilizando: %s", dest.relative_to(vault_path))
        return dest

    # Mismo nombre pero distinto tamaño — agregar sufijo numérico
    if dest.exists():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        counter = 1
        while dest.exists():
            dest = resources_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    await asyncio.to_thread(shutil.copy2, str(source_path), str(dest))
    logger.info("Recurso guardado: %s", dest.relative_to(vault_path))
    return dest


# ---------------------------------------------------------------------------
# Git backup
# ---------------------------------------------------------------------------


class GitBackup:
    """Maneja git commit+push con debounce configurable.

    Acumula títulos de notas y hace un solo commit+push después del debounce.
    """

    def __init__(
        self,
        vault_path: Path,
        debounce_seconds: int = 30,
        bot=None,
        chat_id: Optional[int] = None,
        debug: bool = False,
    ) -> None:
        self.vault_path = vault_path
        self.debounce_seconds = debounce_seconds
        self._pending_titles: list[str] = []
        self._timer: Optional[asyncio.TimerHandle] = None
        self._lock = asyncio.Lock()
        self._bot = bot
        self._chat_id = chat_id
        self._debug = debug

    async def notify(self, title: str) -> None:
        """Registra una nota para backup y reinicia el debounce.

        Args:
            title: Título de la nota creada/modificada.
        """
        async with self._lock:
            self._pending_titles.append(title)

            # Cancelar timer previo
            if self._timer is not None:
                self._timer.cancel()

            # Programar nuevo commit
            loop = asyncio.get_running_loop()
            def _schedule_backup() -> None:
                task = asyncio.ensure_future(self._do_backup())
                task.add_done_callback(
                    lambda t: logger.error("Backup task failed: %s", t.exception())
                    if not t.cancelled() and t.exception()
                    else None
                )

            self._timer = loop.call_later(self.debounce_seconds, _schedule_backup)

    @staticmethod
    def _build_message(titles: list[str]) -> str:
        """Genera el mensaje de commit a partir de los títulos acumulados."""
        if len(titles) == 1:
            return f"Add note: {titles[0]}"
        title_list = ", ".join(titles[:5])
        if len(titles) > 5:
            title_list += f" (+{len(titles) - 5} más)"
        return f"Add {len(titles)} notes: {title_list}"

    def _sync_backup(self, message: str) -> tuple[str, str]:
        """Parte síncrona del backup: add + commit + push. Corre en un thread.

        Todas las operaciones de GitPython (``Repo``, ``add``, ``is_dirty``,
        ``commit``, ``push``) son bloqueantes y en la RPi4 con SD lenta pueden
        tardar cientos de ms — no deben correr en el event loop o congelan el bot.

        Returns:
            Tupla ``(status, detail)`` donde status ∈ {pushed, clean, push_failed,
            no_git, not_repo} y detail es el mensaje de error si aplica.
        """
        try:
            import git
        except ImportError:
            return ("no_git", "")

        try:
            repo = git.Repo(str(self.vault_path))
        except git.InvalidGitRepositoryError:
            return ("not_repo", "")

        repo.git.add(A=True)
        if not repo.is_dirty(untracked_files=True):
            return ("clean", "")

        author = git.Actor("ADSO", "adso@localhost")
        repo.index.commit(message, author=author, committer=author)
        try:
            origin = repo.remote("origin")
            origin.push()
        except Exception as e:  # noqa: BLE001 — cualquier fallo de push se reporta
            return ("push_failed", str(e))
        return ("pushed", "")

    async def _do_backup(self) -> None:
        """Ejecuta git add + commit + push (parte bloqueante en un thread)."""
        async with self._lock:
            self._timer = None
            if not self._pending_titles:
                return
            titles = list(self._pending_titles)
            self._pending_titles.clear()

        message = self._build_message(titles)

        try:
            status, detail = await asyncio.to_thread(self._sync_backup, message)
        except Exception as e:  # noqa: BLE001
            logger.error("Error en git backup: %s", e)
            return

        if status == "pushed":
            logger.info("Git commit+push exitoso: %s", message)
            if self._debug and self._bot and self._chat_id:
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=f"💾 [debug] Vault backup:\n<code>{html.escape(message)}</code>",
                    parse_mode="HTML",
                )
        elif status == "push_failed":
            logger.warning("Git push falló (nota segura en disco): %s", detail)
            if self._bot and self._chat_id:
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=f"⚠️ Git push falló — vault seguro en disco.\n<code>{html.escape(detail)}</code>",
                    parse_mode="HTML",
                )
        elif status == "clean":
            logger.debug("Git: sin cambios para commit")
        elif status == "no_git":
            logger.warning("GitPython no instalado, backup deshabilitado")
        elif status == "not_repo":
            logger.warning("El vault no es un repo git: %s", self.vault_path)
