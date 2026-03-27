"""Escritura y modificación de archivos .md en el vault de Obsidian.

No llama a LLMs ni a ChromaDB. Toda operación es async.
Referencia: docs/vault-interface.md
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
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

VALID_TYPES = {"reference", "task", "idea", "draft", "project-index", "area-index"}

VALID_STATUS: dict[str, set[str]] = {
    "reference": {"active", "pending-classification"},
    "task": {"pending", "in-progress", "done", "pending-classification"},
    "idea": {"raw", "implemented", "discarded", "pending-classification"},
    "draft": {"pending-classification"},
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
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


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
        prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    slug_text = slugify(title, max_length=MAX_SLUG_LENGTH, word_boundary=True)
    if not slug_text:
        slug_text = "nota"

    return f"{prefix}-{slug_text}.md"


def _resolve_dest_dir(fm: dict, vault_path: Path) -> Optional[Path]:
    """Calcula el directorio destino según el frontmatter.

    Returns:
        Path del directorio destino, o None si el destino no se puede resolver
        (nota sin proyecto ni área — el caller debe preguntar al usuario).
    """
    note_type = fm.get("type", "draft")
    project = fm.get("project")
    section = fm.get("section")
    area = fm.get("area")

    if note_type == "draft":
        return vault_path / "00-Inbox"

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

    # Escribir archivo
    await asyncio.to_thread(file_path.write_text, content, "utf-8")

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

    await asyncio.to_thread(note_path.write_text, output, "utf-8")


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

    await asyncio.to_thread(note_path.write_text, output, "utf-8")


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
        await asyncio.to_thread(note_path.write_text, output, "utf-8")
        logger.info("Wikilinks actualizados en: %s", note_path)


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

    dest = resources_dir / original_filename

    # Si ya existe con el mismo nombre y tamaño, reutilizar — no duplicar
    if dest.exists() and dest.stat().st_size == source_path.stat().st_size:
        logger.info("Recurso ya existe (mismo nombre y tamaño), reutilizando: %s", dest.relative_to(vault_path))
        return dest

    # Mismo nombre pero distinto tamaño — agregar sufijo numérico
    if dest.exists():
        stem = Path(original_filename).stem
        suffix = Path(original_filename).suffix
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

    def __init__(self, vault_path: Path, debounce_seconds: int = 30) -> None:
        self.vault_path = vault_path
        self.debounce_seconds = debounce_seconds
        self._pending_titles: list[str] = []
        self._timer: Optional[asyncio.TimerHandle] = None
        self._lock = asyncio.Lock()

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
            self._timer = loop.call_later(
                self.debounce_seconds,
                lambda: asyncio.ensure_future(self._do_backup()),
            )

    async def _do_backup(self) -> None:
        """Ejecuta git add + commit + push."""
        async with self._lock:
            if not self._pending_titles:
                return

            titles = list(self._pending_titles)
            self._pending_titles.clear()
            self._timer = None

        # Generar mensaje de commit
        if len(titles) == 1:
            message = f"Add note: {titles[0]}"
        else:
            title_list = ", ".join(titles[:5])
            if len(titles) > 5:
                title_list += f" (+{len(titles) - 5} más)"
            message = f"Add {len(titles)} notes: {title_list}"

        try:
            import git

            repo = git.Repo(str(self.vault_path))

            # Stage all changes
            repo.git.add(A=True)

            # Check if there are changes to commit
            if repo.is_dirty(untracked_files=True):
                repo.index.commit(message)
                logger.info("Git commit: %s", message)

                # Push (puede fallar si no hay remote)
                try:
                    origin = repo.remote("origin")
                    await asyncio.to_thread(origin.push)
                    logger.info("Git push exitoso")
                except Exception as e:
                    logger.warning("Git push falló (nota segura en disco): %s", e)
            else:
                logger.debug("Git: sin cambios para commit")

        except ImportError:
            logger.warning("GitPython no instalado, backup deshabilitado")
        except git.InvalidGitRepositoryError:
            logger.warning("El vault no es un repo git: %s", self.vault_path)
        except Exception as e:
            logger.error("Error en git backup: %s", e)
