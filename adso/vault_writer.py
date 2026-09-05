"""Escritura y modificación de archivos .md en el vault de Obsidian.

No llama a LLMs ni a ChromaDB. Toda operación es async.
Referencia: docs/vault-interface.md
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import stat
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote

import frontmatter
from slugify import slugify

from adso.constants import NOTE_TYPES, STATUS_BY_TYPE, VALID_PRIORITY

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

# Tipos y status persistibles: la taxonomía vive en `constants.py`.
VALID_TYPES = NOTE_TYPES
VALID_STATUS = STATUS_BY_TYPE
VALID_MEDIA = {"text", "audio", "image", "link", "document"}
VALID_SOURCE = {"telegram", "system"}

# Un adjunto más nuevo que esto no se archiva aunque parezca huérfano: puede ser
# una captura a medio confirmar (ver la barrida en `reconcile_vault`).
_ORPHAN_MIN_AGE_SECONDS = 600

VAULT_DIRS = ["00-Inbox", "01-Projects", "02-Areas", "03-Resources", "05-Archive"]

MAX_SLUG_LENGTH = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """Timestamp local actual en ISO 8601 sin zona, el formato de todo el frontmatter."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _fsync_dir_sync(directory: Path) -> None:
    """Sincroniza a disco la entrada de directorio de ``directory``.

    El ``fsync`` del archivo garantiza que su contenido llegó al disco, pero no
    que el *rename* que lo publicó haya llegado: la entrada de directorio vive
    en otro bloque. Un corte de luz pocos segundos después de "Nota guardada"
    podía evaporar el rename y dejar el vault sin la nota — y la RPi4 no tiene
    UPS, así que es el modo de fallo más probable de todos (#37B).

    El fallo es no fatal a propósito: acá el contenido ya está publicado, y
    propagar el error haría que el caller borrara una nota buena (hay
    filesystems que ni siquiera permiten ``fsync`` sobre un directorio).
    Regla de oro: sin pérdida de datos.
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError as exc:
        logger.debug("No se pudo abrir %s para sincronizar: %s", directory, exc)
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        logger.debug("No se pudo sincronizar el directorio %s: %s", directory, exc)
    finally:
        os.close(fd)


def _atomic_write_sync(path: Path, content: str) -> None:
    """Escribe ``content`` a ``path`` de forma atómica.

    Escribe a un temporal en el mismo directorio, hace fsync y luego
    ``os.replace`` (rename atómico en el mismo filesystem). Si el proceso muere
    a mitad de la escritura (OOM en RPi4, ``docker stop``), el archivo destino
    queda intacto — nunca truncado ni vacío. Regla de oro: sin pérdida de datos.

    Debe correr en un thread (``asyncio.to_thread``): hace I/O bloqueante.
    """
    # Sufijo `.tmp` (no `.suffix` de la nota): el temporal vive en un directorio
    # observado por VaultWatcher; con un sufijo distinto de `.md` el filtro del
    # handler lo saltea aunque el nombre no empezara con `.` — defensa extra sobre
    # `_is_hidden`, y evita que un `git add -A` concurrente lo commitee.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".adso-tmp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # `mkstemp` crea el temporal con 0600 y `os.replace` los conserva: toda
        # nota escrita por el bot quedaba 0600, distinta de una creada a mano y
        # sin acceso por grupo. Se preserva el modo del destino si ya existía
        # (el usuario pudo ajustarlo a propósito) y si no, 0644.
        # G4 de docs/audit-2026-07-31.md.
        try:
            modo = stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            modo = 0o644
        os.chmod(tmp, modo)
        os.replace(tmp, path)
        # El fsync del directorio vive acá, en el helper compartido, y no en
        # `create_note`: así lo heredan `append_to_note`, `set_property` y la
        # actualización de wikilinks, que renombran sobre el vault igual que la
        # creación y tenían la misma ventana de pérdida abierta (#37B).
        _fsync_dir_sync(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _file_hash_sync(path: Path) -> str:
    """SHA-256 de un archivo, leído por chunks (memory-safe en la RPi4)."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_date_value(value: str) -> "date | datetime | str":
    """Convierte un string ISO 8601 a objeto date/datetime para serialización YAML sin comillas.

    Devuelve date para fechas sin hora (YYYY-MM-DD) y datetime para fechas con hora.
    Los objetos nativos son serializados por PyYAML como timestamps YAML sin comillas,
    lo que permite que Obsidian los reconozca como tipo Date & time en Properties.
    Devuelve el valor original si no coincide con ningún patrón o si la fecha es
    sintácticamente válida pero imposible (`2026-02-30`): el regex solo valida
    forma y `fromisoformat` lanzaría `ValueError` al escribir la nota, es decir
    después de que el usuario ya confirmó — pérdida de la captura.
    """
    try:
        if _DATE_ONLY_RE.match(value):
            return date.fromisoformat(value)
        if _DATETIME_RE.match(value):
            return datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Fecha inválida en el frontmatter, se deja como string: %r", value)
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

    if note_type not in ("reference", "task", "idea"):
        return vault_path / "00-Inbox"

    # Mismo orden para los tres tipos: project > area > fallback. Solo cambia
    # el fallback: una task sin destino va al Inbox; reference/idea devuelven
    # None para que el caller decida (preview → Inbox, gestión → preguntar).
    if project:
        base = vault_path / "01-Projects" / project
        return base / section if section else base
    if area:
        return vault_path / "02-Areas" / area
    return vault_path / "00-Inbox" if note_type == "task" else None


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


def _reserve_name_sync(
    directory: Path,
    filename: str,
    *,
    sep: str,
    start: int,
    reuse_if: Optional[Callable[[Path], bool]] = None,
) -> tuple[Path, bool]:
    """Reserva con ``O_EXCL`` el primer nombre libre de la familia ``stem{sep}N``.

    Es el bucle común de `_reserve_and_write_sync`, `_save_resource_sync` y
    `_archive_orphan_sync`: los tres reservaban a mano con la misma forma y
    solo cambiaba la convención del sufijo. La reserva es atómica a nivel
    kernel, así que dos escrituras concurrentes nunca ganan el mismo nombre.

    Args:
        directory: Carpeta destino (ya creada).
        filename: Nombre deseado; si está tomado se prueba ``stem{sep}N``.
        sep: Separador del sufijo numérico (``-`` para notas, ``_`` para adjuntos).
        start: Primer N que se prueba tras el nombre original.
        reuse_if: Predicado opcional sobre un candidato ya existente. Si
            devuelve True, ese archivo se devuelve tal cual (dedup) sin
            reservar nada nuevo.

    Returns:
        ``(path, reserved)``: ``reserved`` es True si el path es un placeholder
        de 0 bytes recién creado por esta llamada, False si es un archivo
        existente que ``reuse_if`` aceptó.
    """
    stem, suffix = Path(filename).stem, Path(filename).suffix
    candidate = directory / filename
    counter = start
    while True:
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if reuse_if is not None and reuse_if(candidate):
                return candidate, False
            candidate = directory / f"{stem}{sep}{counter}{suffix}"
            counter += 1
            continue
        os.close(fd)
        return candidate, True


def _reserve_and_write_sync(dest_dir: Path, filename: str, content: str) -> Path:
    """Reserva un nombre libre y escribe el contenido, sin ventana TOCTOU.

    `_unique_path` elegía el nombre y recién varios `await` después el
    `os.replace` escribía. Dos escrituras concurrentes con el mismo título el
    mismo día —una captura del usuario y `reclassify_inbox`, por ejemplo—
    elegían el mismo candidato y la segunda **sobrescribía a la primera en
    silencio**. Acá la reserva se hace con `O_EXCL`, que es atómico a nivel
    kernel: dos procesos no pueden ganar el mismo nombre.
    G1 de docs/audit-2026-07-31.md.

    Corre entero en un thread (I/O bloqueante).

    Args:
        dest_dir: Directorio destino (se crea si no existe).
        filename: Nombre deseado; si está tomado se prueba `stem-2`, `stem-3`…
        content: Contenido completo del archivo.

    Returns:
        Path efectivamente escrito.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidate, _ = _reserve_name_sync(dest_dir, filename, sep="-", start=2)

    # El contenido se escribe con el mismo write atómico de siempre: el
    # placeholder vacío que dejó la reserva se reemplaza de una. Un crash entre
    # medio deja una nota vacía, nunca una nota pisada.
    try:
        _atomic_write_sync(candidate, content)
    except BaseException:
        # La reserva se deshace si la escritura falla (#37A): sin esto el
        # placeholder vacío quedaba en el vault para siempre —se commiteaba al
        # backup, disparaba el watcher y aparecía como nota en blanco— y encima
        # ocupaba el nombre, así que el reintento del usuario escribía `-2`.
        # Solo se borra el archivo que ESTA llamada reservó con O_EXCL: nunca
        # puede llevarse puesta una nota ajena. El caso del crash (proceso
        # muerto de golpe) sigue siendo el documentado en decisions-log.md.
        try:
            os.unlink(candidate)
        except OSError:
            pass
        raise
    return candidate


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


def _build_post(body: str, clean_fm: dict) -> frontmatter.Post:
    """Construye un `frontmatter.Post` asignando la metadata por atributo.

    Nunca usar `frontmatter.Post(body, **fm)`: la firma real es
    `Post(content, handler=None, **metadata)`, así que una clave `handler` en el
    frontmatter (posible via fallback de Groq sin schema, prompt injection o
    edición externa) se interpretaría como handler de serialización y
    `frontmatter.dumps()` escribiría ese string como contenido total del archivo
    —perdiendo body y frontmatter en silencio—, y una clave `content` lanzaría
    `TypeError`. Asignar `post.metadata` deja ambas como campos normales.

    Args:
        body: Cuerpo markdown de la nota.
        clean_fm: Frontmatter ya pasado por `_clean_frontmatter`.

    Returns:
        El Post listo para `frontmatter.dumps()`.
    """
    post = frontmatter.Post(body)
    post.metadata = clean_fm
    return post


def load_post(raw: str) -> frontmatter.Post:
    """Parsea un `.md` con frontmatter tolerando claves `handler`/`content`.

    `frontmatter.loads()` hace `Post(content, handler, **metadata)` internamente,
    así que una nota editada externamente cuyo YAML tenga una clave `handler` o
    `content` lo hace lanzar `TypeError` y rompe cualquier scan que la toque.
    Este helper cae a `frontmatter.parse()` (que no tiene el choque de kwargs) y
    reconstruye el Post con `_build_post`.

    Args:
        raw: Contenido completo del archivo `.md`.

    Returns:
        El Post parseado.

    Raises:
        Lo mismo que `frontmatter.loads` para YAML inválido (`yaml.YAMLError`).
    """
    try:
        return frontmatter.loads(raw)
    except TypeError:
        handler = frontmatter.detect_format(raw, frontmatter.handlers)
        metadata, content = frontmatter.parse(raw, handler=handler)
        return _build_post(content, dict(metadata))


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

    # Defensa en profundidad: hasta acá `VALID_TYPES`/`VALID_STATUS` solo se
    # usaban en `set_property`, así que `create_note` escribía al vault
    # cualquier cosa que le llegara (el flujo de índices de `manage.py` y
    # cualquier escritor que no venga del LLM no pasan por
    # `_validate_capture_payload`). Un `type` inválido rompe el routing —
    # `_resolve_dest_dir` cae a Inbox — y además desactiva en silencio la
    # validación de status de `set_property`.
    #
    # Se COACCIONA en vez de lanzar: el caller típico es `_cb_confirm`, o sea
    # el usuario ya apretó [Confirmar], y el texto de audio/OCR/Vision no
    # existe en ningún otro lado. Regla de oro: sin pérdida de datos.
    note_type = fm.get("type")
    if note_type not in VALID_TYPES:
        logger.warning(
            "type inválido en create_note: %r — se degrada a idea/pending-classification",
            note_type,
        )
        fm["type"] = "idea"
        fm["status"] = "pending-classification"
    else:
        valid_status = VALID_STATUS.get(note_type, frozenset())
        # `area-index` declara el set vacío a propósito (no tiene ciclo de
        # vida): ahí no hay nada que validar.
        if valid_status and "status" in fm and fm["status"] not in valid_status:
            fallback = (
                "pending-classification"
                if "pending-classification" in valid_status
                else "active"
            )
            logger.warning(
                "status %r inválido para type=%r — se degrada a %r",
                fm["status"], note_type, fallback,
            )
            fm["status"] = fallback

    # Setear campos automáticos si no vienen
    now = now_iso()
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

    if dry_run:
        # Solo para el preview: acá sí alcanza con mirar qué nombre quedaría
        # libre, porque no se escribe nada.
        return _unique_path(dest_dir, filename)

    # Construir contenido con python-frontmatter
    clean_fm = _clean_frontmatter(fm)
    post = _build_post(body, clean_fm)
    content = frontmatter.dumps(post)

    # mkdir + reserva del nombre + escritura atómica, todo en un solo thread:
    # entre elegir el nombre y escribirlo no puede haber ningún `await` o dos
    # escrituras concurrentes se pisan (G1 de docs/audit-2026-07-31.md).
    file_path = await asyncio.to_thread(
        _reserve_and_write_sync, dest_dir, filename, content
    )

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
    post = load_post(raw)

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

    note.frontmatter["date_modified"] = now_iso()

    clean_fm = _clean_frontmatter(note.frontmatter)
    post = _build_post(new_body, clean_fm)
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

    if key == "type" and value not in VALID_TYPES:
        raise ValueError(f"type inválido: {value!r} (válidos: {sorted(VALID_TYPES)})")

    if key == "status":
        # Sin este guard, un `type` fuera del enum devolvía un set vacío y el
        # `if valid and ...` de abajo desactivaba la validación en silencio —
        # justo en las notas que ya están malformadas.
        if note_type not in VALID_TYPES:
            raise ValueError(
                f"type inválido en la nota: {note_type!r} — no se puede validar status"
            )
        valid = VALID_STATUS.get(note_type, frozenset())
        if valid and value not in valid:
            raise ValueError(
                f"status inválido para type={note_type!r}: {value!r} "
                f"(válidos: {valid})"
            )

    for enum_key, valid_values in (
        ("priority", VALID_PRIORITY),
        ("media_type", VALID_MEDIA),
        ("source", VALID_SOURCE),
    ):
        if key == enum_key and value not in valid_values:
            raise ValueError(
                f"{key} inválido: {value!r} (válidos: {sorted(valid_values)})"
            )

    if key in DATE_FIELDS and value is not None:
        try:
            datetime.fromisoformat(str(value))
        except ValueError:
            raise ValueError(f"{key} debe ser ISO 8601: {value!r}") from None

    if key == "tags" and not isinstance(value, list):
        raise ValueError(f"tags debe ser una lista: {value!r}")

    # Aplicar cambio
    fm[key] = value

    if update_date_modified and key != "date_modified":
        fm["date_modified"] = now_iso()

    clean_fm = _clean_frontmatter(fm)
    post = _build_post(note.body, clean_fm)
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


def _fence_line_flags(lines: list[str]) -> list[bool]:
    """Marca qué líneas caen dentro de un bloque de código markdown.

    Args:
        lines: Líneas del documento.

    Returns:
        Lista paralela a ``lines``: True si esa línea está dentro de un fence
        (los delimitadores ``` en sí se marcan como dentro).
    """
    flags: list[bool] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            flags.append(True)
            continue
        flags.append(in_fence)
    return flags


def _remove_empty_ver_tambien(content: str) -> str:
    """Elimina el header '## Ver también' si no quedan items de lista bajo él."""
    lines = content.split("\n")
    result: list[str] = []
    fence_lines = _fence_line_flags(lines)
    i = 0
    while i < len(lines):
        if lines[i].strip() == "## Ver también" and not fence_lines[i]:
            # Buscar si hay algún item de lista antes del próximo heading o EOF.
            # Cuenta cualquier item `- `, no solo wikilinks: un bloque con un
            # link roto y un item de texto plano del usuario debe conservar su
            # header en vez de dejar el item huérfano.
            j = i + 1
            has_items = False
            while j < len(lines):
                # El escaneo también saltea los fences: un `- algo` dentro de un
                # bloque de código no es un item del bloque "Ver también".
                if fence_lines[j]:
                    j += 1
                    continue
                if lines[j].startswith("- "):
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


def _ver_tambien_item_re(stem: str) -> re.Pattern[str]:
    """Regex de un item ``- [[stem]]`` (con alias o anchor opcional) del bloque Ver también.

    Anclado en ``^- [[``: solo líneas que son un item de lista con el
    wikilink. Se aplica exclusivamente DENTRO del bloque "## Ver también" para
    no borrar texto del usuario que use ese wikilink en otra parte de la nota.
    """
    return re.compile(r"^- \[\[" + re.escape(stem) + r"(?:[|#][^\]]+)?\]\].*$")


def _strip_broken_links_in_ver_tambien(content: str, link_re: re.Pattern[str]) -> str:
    """Elimina items ``- [[stem]]`` rotos, pero SOLO dentro del bloque '## Ver también'.

    Recorre las líneas manteniendo el estado "dentro del bloque Ver también"
    (entre el header ``## Ver también`` y el siguiente ``## `` o EOF). Fuera de
    ese bloque, las líneas se preservan tal cual — así un wikilink que el usuario
    haya escrito en un párrafo o en otra lista nunca se borra.
    """
    lines = content.split("\n")
    result: list[str] = []
    in_block = False
    in_fence = False
    for line in lines:
        stripped = line.strip()
        # Un "## Ver también" dentro de un bloque de código es un ejemplo del
        # usuario, no un bloque real: se preserva tal cual.
        if stripped.startswith("```"):
            in_fence = not in_fence
            result.append(line)
            continue
        if in_fence:
            result.append(line)
            continue
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
    link_re = _ver_tambien_item_re(stem)

    modified = 0
    # El generator de rglob hace un readdir bloqueante en cada paso, y esta
    # función corre en el callback de delete del watcher — se materializa la
    # lista en un hilo. F11 de docs/audit-2026-07-31.md.
    md_files = await asyncio.to_thread(lambda: list(vault_path.rglob("*.md")))

    # Los wikilinks de Obsidian resuelven por stem, no por path: si otra nota
    # con el mismo stem sigue viva, el link NO está roto y borrarlo es pérdida
    # de datos. Pasa siempre que el usuario MUEVE una nota (el watcher emite un
    # delete del origen) y también con stems duplicados en carpetas distintas.
    if any(p.stem == stem and p != deleted_path for p in md_files):
        logger.debug(
            "Limpieza de wikilinks omitida: [[%s]] sigue resolviendo a otra nota.", stem
        )
        return 0

    for md_path in md_files:
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

        # La comparación va ANTES de normalizar el newline final: aplicando el
        # rstrip siempre, una nota que menciona el link fuera de "## Ver
        # también" y cuyo newline final difiere se reescribía sin cambio real →
        # mtime bump → evento del watcher → re-embed espurio (llamada a Gemini)
        # + churn del backup, por cada delete externo. F11 de
        # docs/audit-2026-07-31.md.
        if new_content == raw:
            continue

        new_content = new_content.rstrip("\n") + "\n"

        try:
            await asyncio.to_thread(_atomic_write_sync, md_path, new_content)
        except OSError as exc:
            logger.warning("No se pudo escribir %s al limpiar wikilinks: %s", md_path, exc)
            continue
        modified += 1
        logger.info("Wikilink roto eliminado: %s → [[%s]]", md_path.name, stem)

    return modified


# ---------------------------------------------------------------------------
# Vault reconciliation (nightly maintenance)
# ---------------------------------------------------------------------------

# Cualquier wikilink del documento, incluidos los embeds `![[archivo.pdf]]` y
# los que viven en el frontmatter (`source_file: "[[paper.pdf]]"`).
_ANY_WIKILINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")

# Item de lista del bloque "## Ver también" (misma forma que limpia
# `remove_broken_wikilinks`: anclado en el margen izquierdo).
_VER_TAMBIEN_ITEM_RE = re.compile(r"^- \[\[([^\]\n]+)\]\]")

# Link markdown `[texto](ruta)`. El bot siempre escribe wikilinks, pero Obsidian
# puede estar configurado para escribir links markdown: una nota editada a mano
# referencia su adjunto así, y la barrida de huérfanos no puede ignorarlo.
_MARKDOWN_LINK_RE = re.compile(r"\]\(([^)\s]+)")


def _link_target_key(raw_target: str) -> str:
    """Normaliza el target de un wikilink a la clave con la que se resuelve.

    Descarta el alias (``|``), el anchor (``#``) y cualquier componente de
    directorio: Obsidian resuelve por nombre de archivo, no por ruta.
    """
    target = raw_target.split("|", 1)[0].split("#", 1)[0].strip()
    return target.replace("\\", "/").rsplit("/", 1)[-1]


def _ver_tambien_link_targets(content: str) -> list[str]:
    """Devuelve los targets de los items de wikilink del bloque '## Ver también'.

    Espeja el recorrido de `_strip_broken_links_in_ver_tambien`: fuera del
    bloque no hay nada que reconciliar, y dentro de un bloque de código tampoco
    (es un ejemplo del usuario, no un link — #5).
    """
    targets: list[str] = []
    in_block = False
    in_fence = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped == "## Ver también":
            in_block = True
            continue
        if in_block and stripped.startswith("## "):
            in_block = False
        if not in_block:
            continue
        match = _VER_TAMBIEN_ITEM_RE.match(line)
        if match:
            targets.append(match.group(1))
    return targets


def _vault_files_sync(vault_path: Path) -> list[Path]:
    """Lista los archivos visibles del vault (sin dotfiles ni carpetas ocultas)."""
    files: list[Path] = []
    for path in vault_path.rglob("*"):
        try:
            rel_parts = path.relative_to(vault_path).parts
        except ValueError:
            continue
        # `.obsidian/`, `.trash/`, los temporales `.adso-tmp-*`: nada de eso es
        # una nota ni un adjunto del usuario.
        if any(part.startswith(".") for part in rel_parts):
            continue
        try:
            if path.is_file():
                files.append(path)
        except OSError:
            continue
    return files


def _archive_orphan_sync(path: Path, archive_dir: Path) -> Path:
    """Mueve un adjunto huérfano a ``archive_dir`` sin pisar nada.

    El nombre se reserva con ``O_EXCL`` y se desambigua con sufijo numérico
    (misma convención que `save_resource`): dos huérfanos homónimos archivados
    en noches distintas conviven. Pisar el primero sería perder el binario justo
    en el paso que existe para no perderlo.
    """
    import shutil

    archive_dir.mkdir(parents=True, exist_ok=True)
    candidate, _ = _reserve_name_sync(archive_dir, path.name, sep="_", start=1)
    # `shutil.move` sobre el placeholder que acaba de reservar el nombre: un
    # rename si es el mismo filesystem, copy2+unlink si no.
    shutil.move(str(path), str(candidate))
    _fsync_dir_sync(archive_dir)
    return candidate


def _reconcile_vault_sync(vault_path: Path) -> tuple[list[Path], list[Path]]:
    """Reconciliación local del vault: links rotos y adjuntos sin dueño.

    Corre entera en un thread y en un solo recorrido del vault. No usa red ni
    ChromaDB a propósito: es mantenimiento del vault, no del índice.

    Returns:
        (notas_modificadas, adjuntos_archivados).
    """
    files = _vault_files_sync(vault_path)

    # Criterio de resolución: **existencia en disco**, nunca pertenencia al
    # índice semántico. Una nota en `05-Archive/` está excluida del índice pero
    # Obsidian abre su link; borrarlo sería pérdida de datos (archivar no es
    # borrar). Ídem los adjuntos de `03-Resources/`. Es el mismo criterio de #3:
    # mover una nota no rompe sus links, porque resuelven por stem.
    existing_names = {p.name for p in files}
    existing_stems = {p.stem for p in files}

    modified: list[Path] = []
    referenced: set[str] = set()
    unreadable = False

    for md_path in files:
        if md_path.suffix != ".md":
            continue
        try:
            raw = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("No se pudo leer %s en la reconciliación: %s", md_path, exc)
            unreadable = True
            continue

        for match in _ANY_WIKILINK_RE.finditer(raw):
            referenced.add(_link_target_key(match.group(1)))
        for match in _MARKDOWN_LINK_RE.finditer(raw):
            destino = _link_target_key(match.group(1))
            referenced.add(destino)
            # Obsidian escapa los espacios como %20 al insertar el link.
            referenced.add(unquote(destino))

        # Los índices se dejan como están: los mantiene el flujo de gestión.
        if md_path.stem == "_index":
            continue

        broken = {
            _link_target_key(target)
            for target in _ver_tambien_link_targets(raw)
            if _link_target_key(target) not in existing_names
            and _link_target_key(target) not in existing_stems
        }
        if not broken:
            continue

        new_content = raw
        for stem in sorted(broken):
            new_content = _strip_broken_links_in_ver_tambien(
                new_content, _ver_tambien_item_re(stem)
            )
        new_content = _remove_empty_ver_tambien(new_content)

        # Sin cambio real no se reescribe: bumpear el mtime dispara un evento
        # del watcher → re-embed espurio (llamada a Gemini) + churn del backup,
        # por cada nota y cada noche (F11).
        if new_content == raw:
            continue
        new_content = new_content.rstrip("\n") + "\n"

        try:
            _atomic_write_sync(md_path, new_content)
        except OSError as exc:
            logger.warning(
                "No se pudo escribir %s al reconciliar wikilinks: %s", md_path, exc
            )
            continue
        modified.append(md_path)
        logger.info(
            "Wikilinks rotos reconciliados en %s: %s", md_path.name, sorted(broken)
        )

    archived: list[Path] = []
    if unreadable:
        # Una sola nota ilegible ya alcanza para que un adjunto parezca huérfano
        # sin serlo. Se saltea la barrida entera: mover un binario referenciado
        # es justo el error que la regla de oro prohíbe.
        logger.warning(
            "Barrida de adjuntos huérfanos omitida: hubo notas ilegibles en el vault."
        )
        return modified, archived

    resources_dir = vault_path / "03-Resources"
    archive_dir = vault_path / "05-Archive" / "03-Resources"
    for path in files:
        if resources_dir not in path.parents:
            continue
        # Solo adjuntos binarios: un `.md` en 03-Resources/ es material de
        # referencia permanente, no basura — moverlo sería perder una nota.
        if path.suffix == ".md":
            continue
        if path.name in referenced or path.stem in referenced:
            continue
        # Un adjunto recién escrito es indistinguible de una captura en vuelo:
        # `_cb_confirm` guarda el binario con `save_resource` y recién después
        # escribe la nota que lo referencia. Si la barrida cae en ese hueco, se
        # lleva un adjunto cuya nota está por nacer y deja el embed roto. Los
        # huérfanos reales son viejos (los del vault de producción tenían
        # meses), así que la espera no cuesta nada.
        try:
            if time.time() - path.stat().st_mtime < _ORPHAN_MIN_AGE_SECONDS:
                continue
        except OSError:
            continue
        try:
            destino = _archive_orphan_sync(path, archive_dir)
        except OSError as exc:
            logger.warning("No se pudo archivar el adjunto huérfano %s: %s", path, exc)
            continue
        archived.append(destino)
        logger.info("Adjunto huérfano archivado: %s → %s", path.name, destino)

    return modified, archived


async def reconcile_vault(vault_path: Path) -> tuple[list[Path], list[Path]]:
    """Reconcilia el vault: wikilinks rotos y adjuntos que nadie referencia.

    Pensada para el trabajo nocturno. La limpieza de wikilinks corría solo desde
    el evento de borrado del watcher: una nota borrada con el contenedor parado
    (o desde otro dispositivo mientras ADSO estaba caído) nunca disparaba
    inotify y el link quedaba roto para siempre (#57).

    Args:
        vault_path: Raíz del vault.

    Returns:
        (notas_modificadas, adjuntos_archivados) — los adjuntos se **mueven** a
        `05-Archive/03-Resources/`, nunca se borran.
    """
    return await asyncio.to_thread(_reconcile_vault_sync, vault_path)


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


def build_index_note(kind: str, name: str, description: str) -> tuple[dict, str]:
    """Frontmatter y body del ``_index.md`` de un proyecto o un área nuevos.

    Único constructor del índice: lo usan la siembra inicial (`seed_vault`) y
    el flujo de gestión (`manage.py`). Antes cada uno armaba el suyo y
    divergían — la siembra escribía el nombre crudo como tag y sin el marcador
    ``system``, partiendo en dos el vocabulario de tags que #58 unificó.

    El nombre va crudo a ``project``/``area`` (direccionan la carpeta en disco)
    y kebab-caseado al tag, que es lo que el prompt reutiliza.

    Args:
        kind: ``"project"`` o ``"area"``.
        name: Nombre tal como lo escribió el usuario.
        description: Descripción obligatoria (scope de clasificación).

    Returns:
        ``(frontmatter, body)`` listos para `create_note`.

    Raises:
        ValueError: Si ``kind`` no es ``project`` ni ``area``.
    """
    if kind not in ("project", "area"):
        raise ValueError(f"kind inválido: {kind!r}")
    # Import local: `llm_schema` importa `constants`, no este módulo, así que
    # no hay ciclo — pero el helper de tags vive ahí y no se necesita en el
    # camino de escritura de notas comunes.
    from adso.llm_schema import _to_kebab

    title = name.replace("-", " ").title()
    fm: dict[str, Any] = {
        "title": title,
        "type": f"{kind}-index",
        "description": description,
        "tags": ["system", _to_kebab(name)],
        "source": "system",
        kind: name,
    }
    body = f"# {title}\n\n## Descripción\n{description}\n"
    if kind == "project":
        fm["status"] = "active"
        fm["sections"] = []
        body += f"\n## Secciones\n\n## Estado\n- Creado: {now_iso()[:10]}\n"
    return fm, body


async def seed_vault(vault_path: Path, vault_seed: Any) -> None:
    """Siembra proyectos y áreas iniciales desde config.

    Args:
        vault_path: Path raíz del vault.
        vault_seed: VaultSeedConfig con proyectos y áreas a crear.
    """
    for kind, folder, items in (
        ("project", "01-Projects", vault_seed.projects),
        ("area", "02-Areas", vault_seed.areas),
    ):
        for item in items:
            if (vault_path / folder / item.name / "_index.md").exists():
                continue
            fm, body = build_index_note(kind, item.name, item.description)
            await create_note(fm, body, vault_path)
            logger.info("%s seed creado: %s", kind.capitalize(), item.name)


# ---------------------------------------------------------------------------
# Guardar archivos en Resources
# ---------------------------------------------------------------------------


def _save_resource_sync(
    source_path: Path,
    safe_name: str,
    resources_dir: Path,
) -> Path:
    """Copia un adjunto a ``resources_dir`` reservando el nombre y sin dejar parciales.

    Corre entero en un thread (I/O bloqueante). Tres garantías:

    1. La reserva del nombre usa ``O_EXCL`` (atómico a nivel kernel), así que
       dos guardados concurrentes de contenido distinto nunca ganan el mismo
       nombre — antes el segundo pisaba al primero y el ``![[...]]`` de la
       primera nota apuntaba a otro binario.
    2. El dedup por SHA-256 (con short-circuit por tamaño) se mantiene: si el
       nombre está tomado por un archivo de contenido idéntico, se reutiliza.
    3. La copia se hace a un temporal y se publica con ``os.replace``: un corte
       a mitad (OOM, ``docker stop``, corte de luz) nunca deja un adjunto
       truncado visible en ``03-Resources/``.

    Args:
        source_path: Archivo a copiar.
        safe_name: Nombre destino, ya saneado (sin componentes de path).
        resources_dir: Carpeta ``03-Resources/`` del vault.

    Returns:
        Path del archivo en el vault (nuevo o reutilizado por dedup).

    Raises:
        FileNotFoundError: Si ``source_path`` no existe.
        OSError: Si la copia falla; en ese caso no queda ningún parcial.
    """
    import shutil

    if not source_path.exists():
        raise FileNotFoundError(f"Archivo fuente no encontrado: {source_path}")

    resources_dir.mkdir(parents=True, exist_ok=True)

    source_size = source_path.stat().st_size
    source_hash: Optional[str] = None

    def _same_content(candidate: Path) -> bool:
        # El nombre está tomado: si el contenido es el mismo, se reutiliza
        # (dedup por hash; comparar solo tamaño confundía archivos distintos
        # y descartaba el nuevo en silencio).
        nonlocal source_hash
        try:
            if candidate.stat().st_size != source_size:
                return False
            if source_hash is None:
                source_hash = _file_hash_sync(source_path)
            return _file_hash_sync(candidate) == source_hash
        except OSError:
            # Un archivo ilegible o borrado en medio del scan no puede tumbar
            # la captura: se prueba el siguiente nombre.
            return False

    # La reserva deja un archivo de 0 bytes hasta el `os.replace` de abajo. Es
    # una ventana corta y solo afecta al dedup: dos guardados *simultáneos* del
    # mismo binario pueden terminar en dos archivos (uno de más), nunca en uno
    # pisado (pérdida de datos).
    candidate, reserved = _reserve_name_sync(
        resources_dir, safe_name, sep="_", start=1, reuse_if=_same_content
    )
    if not reserved:
        logger.info("Recurso ya existe (mismo contenido), reutilizando: %s", candidate)
        return candidate

    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(resources_dir), prefix=".adso-tmp-", suffix=".tmp"
    )
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(str(source_path), str(tmp))
        # `copy2` preserva el modo del origen, y el origen es el temporal de la
        # descarga que `tempfile` crea en 0600: sin este chmod todo PDF o imagen
        # de 03-Resources/ quedaba ilegible para cualquier otro usuario o
        # proceso (Syncthing corre con otro UID). G4 de audit-2026-07-31.md.
        os.chmod(tmp, 0o644)
        os.replace(tmp, candidate)
        _fsync_dir_sync(resources_dir)
    except BaseException:
        # Ni el temporal ni el placeholder de la reserva pueden sobrevivir a un
        # fallo: un PDF truncado en 03-Resources/ es peor que no tenerlo, porque
        # Obsidian lo lista como si estuviera bien y nadie se entera.
        for leftover in (tmp, candidate):
            try:
                os.unlink(leftover)
            except OSError:
                pass
        raise

    logger.info("Recurso guardado: %s", candidate)
    return candidate


async def save_resource(
    source_path: Path,
    original_filename: str,
    vault_path: Path,
) -> Path:
    """Copia un archivo a 03-Resources/ en el vault.

    Si ya existe un archivo con el mismo nombre y contenido distinto, agrega
    sufijo numérico; si el contenido es idéntico, reutiliza el existente.

    Todo el I/O corre en un solo thread: en la RPi4 con SD lenta, el `stat` y el
    bucle de nombres bloqueaban el event loop en cada captura con adjunto, y
    además cualquier `await` entre elegir el nombre y escribirlo abría una
    ventana TOCTOU (#36).

    Args:
        source_path: Path al archivo temporal a copiar.
        original_filename: Nombre original del archivo.
        vault_path: Raíz del vault.

    Returns:
        Path al archivo copiado en el vault.

    Raises:
        FileNotFoundError: Si source_path no existe.
        OSError: Si la copia falla (el caller debe avisar al usuario: no queda
            ningún archivo parcial en el vault).
    """
    # Strip directory components to prevent path traversal (e.g. "../../.env").
    # Path(...).name keeps only the final component regardless of separators.
    safe_name = Path(original_filename).name or "resource"

    return await asyncio.to_thread(
        _save_resource_sync, source_path, safe_name, vault_path / "03-Resources"
    )


async def find_resource_by_hash(source_path: Path, vault_path: Path) -> Optional[Path]:
    """Busca en 03-Resources/ un archivo con el mismo contenido que `source_path`.

    Es el mismo criterio que usa `save_resource` para reutilizar un adjunto ya
    guardado (SHA-256 con short-circuit por tamaño), pero mirando toda la
    carpeta en vez de solo los candidatos del nombre: el mismo archivo puede
    haber llegado con otro nombre. `save_resource` ya sabía que era el mismo
    binario y ese dato se descartaba, así que el mismo PDF terminaba en dos
    notas (issue #53).

    Args:
        source_path: Archivo a buscar (típicamente el temporal de la descarga).
        vault_path: Raíz del vault.

    Returns:
        Path al archivo existente en 03-Resources/, o None si no hay ninguno con
        ese contenido (o si la carpeta todavía no existe).
    """

    def _scan() -> Optional[Path]:
        resources_dir = vault_path / "03-Resources"
        if not resources_dir.is_dir():
            return None
        try:
            source_size = source_path.stat().st_size
        except OSError:
            return None

        source_hash: Optional[str] = None
        for candidate in sorted(resources_dir.rglob("*")):
            try:
                if not candidate.is_file() or candidate.stat().st_size != source_size:
                    continue
                # El hash del origen se calcula recién cuando hay algún candidato
                # del mismo tamaño: en la RPi4 con SD lenta, el caso normal
                # (archivo nuevo) no paga ninguna lectura.
                if source_hash is None:
                    source_hash = _file_hash_sync(source_path)
                if _file_hash_sync(candidate) == source_hash:
                    return candidate
            except OSError:
                # Un archivo borrado o ilegible en medio del scan no puede
                # tumbar la captura: se saltea.
                continue
        return None

    return await asyncio.to_thread(_scan)


# ---------------------------------------------------------------------------
# Git backup
# ---------------------------------------------------------------------------


def backup_label(note_path: Path) -> str:
    """Human-readable label for a note in a vault backup commit message.

    Every project and area index is named `_index.md`, so labelling by stem
    produced commits reading `Add note: _index` — which of the seven indexes
    changed was anyone's guess. Indexes are labelled by their project or area
    instead.

    Args:
        note_path: Path of the note that changed.

    Returns:
        The parent directory name plus an `(index)` marker for `_index.md`;
        the file stem for any other note.
    """
    if note_path.stem != "_index":
        return note_path.stem
    parent = note_path.parent.name
    # Un `_index.md` suelto en la raíz del vault no tiene proyecto del que tomar
    # el nombre: se cae al stem en vez de inventar una etiqueta.
    return f"{parent} (index)" if parent else note_path.stem


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
        # Task (o task del caller) que está corriendo `_do_backup` ahora mismo.
        # Sirve para dos cosas: que `flush()` espere un backup en vuelo antes de
        # decidir que no hay nada pendiente, y que dos `_do_backup` nunca corran
        # git en paralelo (colisión de `index.lock`).
        self._running: Optional[asyncio.Task] = None
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

    async def _await_running(self) -> None:
        """Espera a que termine el `_do_backup` en vuelo, si hay alguno.

        No se toma ``self._lock``: el backup en vuelo lo necesita para drenar los
        títulos y esperar acá con el lock tomado sería un deadlock. La referencia
        se lee y se espera sin lock — sólo el event loop la muta.
        """
        current = asyncio.current_task()
        while True:
            running = self._running
            if running is None or running is current:
                return
            try:
                await asyncio.shield(running)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — el backup ya logueó el error
                logger.debug("Backup en vuelo terminó con error: %s", e)
                return

    async def flush(self) -> None:
        """Fuerza el backup pendiente de inmediato, cancelando el debounce.

        Se llama en el shutdown (``_post_shutdown``): una nota escrita dentro de
        la ventana de ``debounce_seconds`` (default 30s) antes de detener el bot
        quedaría sin commit/push hasta la *próxima* escritura — potencial pérdida
        de datos si el contenedor no vuelve a arrancar. Regla de oro: sin pérdida
        de datos.

        Si el debounce ya disparó y hay un backup en vuelo, espera a que termine
        antes de evaluar ``_pending_titles``: de otro modo encontraría la cola
        vacía (ya drenada) y el shutdown continuaría sin que el push se haya
        completado.
        """
        async with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        await self._await_running()

        async with self._lock:
            if not self._pending_titles:
                return
        # _do_backup vuelve a tomar el lock y drena _pending_titles; ejecutarlo
        # fuera del `async with` evita el deadlock por lock no reentrante. Se
        # lanza como task propia (y no inline) para que `self._running` apunte a
        # una task dedicada al backup y no a la del caller de `flush()`.
        await asyncio.ensure_future(self._do_backup())

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

        # `with`: GitPython retiene mmaps, file handles y procesos
        # `git cat-file` persistentes que solo libera `close()`. Con uptime de
        # semanas en la RPi y un backup por captura, se acumulan.
        # G3 de docs/audit-2026-07-31.md.
        with repo:
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
        """Ejecuta git add + commit + push (parte bloqueante en un thread).

        Se serializa con cualquier otro `_do_backup` en vuelo: dos ejecuciones
        concurrentes colisionarían en `index.lock` y el batch drenado se perdería
        del mensaje de commit.

        Ante un fallo de `add`/`commit` (disco lleno, `index.lock` de un git
        manual, repo corrupto) los títulos drenados se re-encolan y se notifica al
        usuario — sin eso el vault podía quedar sin backup indefinidamente y en
        silencio.
        """
        await self._await_running()
        self._running = asyncio.current_task()
        try:
            await self._run_backup_once()
        finally:
            if self._running is asyncio.current_task():
                self._running = None

    async def _run_backup_once(self) -> None:
        """Cuerpo del backup, ya serializado por `_do_backup`."""
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
            # Re-encolar al frente: el próximo backup debe incluir estas notas.
            async with self._lock:
                self._pending_titles[:0] = titles
            if self._bot and self._chat_id:
                try:
                    await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=(
                            "⚠️ Git backup falló — vault seguro en disco, "
                            "se reintenta en la próxima escritura.\n"
                            f"<code>{html.escape(str(e))}</code>"
                        ),
                        parse_mode="HTML",
                    )
                except Exception as send_err:  # noqa: BLE001
                    logger.warning("No se pudo notificar el fallo de backup: %s", send_err)
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
