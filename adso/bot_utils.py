"""Helpers y utilidades compartidas del bot.

Funciones puras y getters async sin lógica de negocio de Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Optional

from telegram.ext import ContextTypes

from adso.constants import DEFAULT_EXCLUDE_DIRS, MANAGE_KEYWORDS
from adso.vault_cache import parse_cached
from adso.vault_search import find_by_property, get_all_tags

logger = logging.getLogger(__name__)

# Referencias fuertes a tareas de fondo. asyncio solo guarda weak-refs a las
# tareas creadas con create_task: sin una referencia fuerte el GC puede
# recolectarlas a mitad de ejecución y cancelarlas silenciosamente (re-embed,
# push a Tasks, etc. perdidos). Se descartan solas al terminar.
_BG_TASKS: "set[asyncio.Task]" = set()



class Stopwatch:
    """Cronómetro por etapas para loguear la latencia de un flujo en una línea.

    Existe porque una captura lenta no se podía diagnosticar: entre el inicio de
    la llamada al LLM y el preview no había ninguna marca de tiempo, y las únicas
    anclas del log eran la línea que emite el SDK de Gemini al abrir la request y
    el "Nota creada" de `vault_writer` — que llega *después* de que el usuario
    confirma, así que no mide nada del bot.

    Una sola línea de resumen en vez de una por etapa: se lee de un vistazo y se
    correlaciona sin tener que cruzar timestamps.

    Args:
        clock: Fuente de tiempo monótono. Inyectable para tests (misma convención
            que el `now` de `_parse_date_from_text`).
    """

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._start = clock()
        self.stages: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Mide el bloque y lo acumula bajo `name`.

        Acumula (no pisa) porque un mismo flujo puede entrar dos veces a la misma
        etapa — la captura corre dos scans del vault seguidos. El registro va en
        `finally`: una etapa que lanza es justo la que hay que medir.
        """
        inicio = self._clock()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (self._clock() - inicio)

    def total(self) -> float:
        """Wall-clock desde la construcción, incluyendo lo no instrumentado.

        Si `total` es mucho mayor que la suma de las etapas, lo lento está en un
        tramo sin medir.
        """
        return self._clock() - self._start

    def summary(self) -> str:
        """Devuelve `"scan 0.13s | classify 6.11s | total 7.48s"`.

        Las etapas salen en orden de ejecución (el dict preserva inserción), así
        que la línea se lee como la secuencia real del pipeline.
        """
        partes = [f"{nombre} {dur:.2f}s" for nombre, dur in self.stages.items()]
        partes.append(f"total {self.total():.2f}s")
        return " | ".join(partes)


def spawn_tracked(coro: Awaitable, *, name: str | None = None) -> "asyncio.Task":
    """Crea una tarea de fondo con referencia fuerte y logging de excepciones.

    Reemplaza ``asyncio.create_task(coro)`` cuando no se espera el resultado:
    evita el GC prematuro y no deja excepciones sin loguear.
    """
    task = asyncio.ensure_future(coro)
    if name:
        try:
            task.set_name(name)
        except AttributeError:
            pass
    _BG_TASKS.add(task)

    def _done(t: "asyncio.Task") -> None:
        _BG_TASKS.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc is not None:
                logger.error("Tarea de fondo %s falló: %r", t.get_name(), exc)

    task.add_done_callback(_done)
    return task


# Tope del set bot_written_paths. En operación normal el set se drena solo (el
# VaultWatcher consume cada entrada al procesar el evento inotify de la escritura,
# ahora que on_moved está implementado). El cap es una red de seguridad: si algún
# evento se pierde y una entrada nunca se drena, el set no crece sin límite en
# uptime largo. 512 es muy holgado para un bot single-user.
_BOT_WRITTEN_CAP = 512


def mark_bot_written(bot_data: dict, path: Path) -> None:
    """Registra un path escrito por el bot para que VaultWatcher saltee su evento.

    El watcher chequea este set y descarta el evento inotify de la propia
    escritura del bot (evita doble embed). Acota el tamaño del set: descartar una
    entrada aún no drenada solo provoca un re-embed redundante (idempotente),
    nunca pérdida de datos.
    """
    paths: set = bot_data.setdefault("bot_written_paths", set())
    paths.add(path)
    if len(paths) > _BOT_WRITTEN_CAP:
        for stale in list(paths)[: len(paths) - _BOT_WRITTEN_CAP]:
            paths.discard(stale)


# Estados que dejan un teclado inline a la vista y bloquean todo input nuevo.
_KEYBOARD_STATE_KEYS = (
    "pending_note", "pending_raw_content", "pending_fallback_pdf",
    "pending_report", "pending_read_status", "pending_arxiv",
    "pending_duplicate_doc", "pending_operation",
)


def _has_pending_keyboard(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True si hay una acción con teclado inline pendiente de resolución.

    Cubre los estados que muestran un teclado al usuario y esperan que
    presione un botón antes de procesar nuevo contenido.
    No bloquea estados que explícitamente esperan texto (awaiting_correction,
    pending_description, manage_missing_fields).
    """
    ud = context.user_data
    if any(ud.get(key) for key in _KEYBOARD_STATE_KEYS):
        return True
    # Estos dos muestran teclado salvo mientras esperan el texto corregido.
    return any(
        ud.get(key) and not ud[key].get("awaiting_correction")
        for key in ("pending_transcript", "pending_extraction")
    )


def _is_awaiting_text_input(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True si hay un flujo esperando texto de corrección (awaiting_correction=True).

    Complementa _has_pending_keyboard: mientras que esa función devuelve False
    cuando awaiting_correction=True (para permitir texto), esta devuelve True para
    bloquear audio, fotos, documentos y comandos en esos mismos estados.
    """
    ud = context.user_data
    if any(
        (ud.get(key) or {}).get("awaiting_correction")
        for key in ("pending_transcript", "pending_extraction", "pending_note")
    ):
        return True
    # `pending_description` espera texto (la descripción de un archivo sin
    # caption), así que `_has_pending_keyboard` lo excluye a propósito. Pero sin
    # incluirlo acá, mandar un segundo binario pasaba todos los guards y
    # sobreescribía el estado: el temporal del primer archivo quedaba huérfano
    # y el archivo se perdía sin aviso. E6 de docs/audit-2026-07-31.md.
    return bool(ud.get("pending_description"))


def _extract_name_from_command(text: str, operation: str) -> str:
    """Extrae el nombre de proyecto/área de un comando de creación.

    Maneja patrones como:
      - crear proyecto "Introducción a la ciencia de datos"
      - nuevo proyecto Tesis
      - crear área investigacion

    Args:
        text: Texto original del usuario.
        operation: 'create_project' o 'create_area'.

    Returns:
        Nombre extraído, o string vacío si no se pudo parsear.
    """
    keyword = r"proyecto" if operation == "create_project" else r"[aá]rea"
    # Con comillas simples o dobles
    m = re.search(
        rf'(?:crear?|nuev[ao]?|agrega[r]?|add)\s+{keyword}\s+["\u201c]([^"\u201d]+)["\u201d]',
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    # Sin comillas: todo lo que viene después de la keyword
    m = re.search(
        rf'(?:crear?|nuev[ao]?|agrega[r]?|add)\s+{keyword}\s+(.+)',
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return ""


def _detect_manage_keywords(text: str) -> list[str]:
    """Detecta intenciones de gestión en el texto por keywords.

    Args:
        text: Texto del usuario.

    Returns:
        Lista de intenciones detectadas: 'project', 'area', 'archive', 'delete', 'rename'.
    """
    lower = text.lower()
    return [
        intent for intent, kws in MANAGE_KEYWORDS.items()
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in kws)
    ]


async def render_with_keyboard(
    primary: Callable[..., Awaitable[Any]],
    fallback_msg: Any,
    text: str,
    *,
    reply_markup: Any = None,
    parse_mode: Optional[str] = None,
) -> Any:
    """Draws a message that carries a keyboard, retrying as a brand-new message.

    Every preview follows the same shape: the pending state is set and only then
    the message is edited. When that edit fails (the user deleted the message,
    the network dropped), the state stays alive with **no buttons on screen** —
    `_has_pending_keyboard` then rejects every input until `/reset`, and the
    text behind it (audio, OCR, Vision, an extraction) exists nowhere else.

    Args:
        primary: Coroutine function that should draw the message, normally
            ``query.edit_message_text``.
        fallback_msg: Message to reply to when ``primary`` fails. If None, the
            original exception propagates (there is nothing better to try).
        text: Message body.
        reply_markup: Keyboard. The whole point of the fallback: a message
            without it leaves the pending state unreachable.
        parse_mode: Passed through untouched.

    Returns:
        Whatever actually reached the user — the caller must register *its*
        ``message_id`` as the live preview, or the "current preview" guard (G14)
        would reject the [Confirmar] of the message the user is looking at.
    """
    kwargs: dict[str, Any] = {}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode

    try:
        return await primary(text, **kwargs)
    except Exception as e:
        if fallback_msg is None:
            raise
        logger.warning(
            "No se pudo renderizar el preview (%s) — se reenvía como mensaje nuevo", e
        )
        return await fallback_msg.reply_text(text, **kwargs)


async def reply_blocked(
    context: ContextTypes.DEFAULT_TYPE, reply: Callable[..., Awaitable[Any]], message: Any
) -> None:
    """Rechaza un input porque hay un teclado pendiente, dejando rastro para borrarlo.

    El mensaje del usuario y el aviso del bot se anotan en ``block_msg_ids``:
    `handle_callback` los borra del chat al resolver el teclado. Antes cada
    handler de entrada repetía las cuatro líneas.

    Args:
        context: Bot context (``user_data``).
        reply: Corrutina con la que contestar (``msg.reply_text`` o equivalente).
        message: Mensaje del usuario que se rechaza; puede ser None.
    """
    ids = context.user_data.setdefault("block_msg_ids", [])
    if message is not None:
        ids.append(message.message_id)
    sent = await reply("Hay una acción pendiente. Resolver los botones antes de continuar.")
    ids.append(sent.message_id)


def _cleanup_pending(context: ContextTypes.DEFAULT_TYPE, *keys: str) -> None:
    """Limpia estados pendientes del user_data y archivos temporales asociados.

    Si no se pasan keys, limpia todos los estados conocidos.
    Si se pasan keys, limpia solo esos.

    Busca temp_path tanto en el nivel raíz del dict como anidado en
    ``resource_file`` (estructura de pending_transcript).
    """
    if not keys:
        keys = (
            "pending_note", "pending_operation", "original_content",
            "pending_raw_content", "pending_capture_ctx", "pending_transcript",
            "pending_extraction", "pending_description",
            "pending_read_status", "pending_fallback_pdf",
            "pending_arxiv", "pending_duplicate_doc",
            "manage_missing_fields", "pending_report",
            "block_msg_ids", "clasificar_inbox_path",
        )

    for key in keys:
        data = context.user_data.pop(key, None)
        if not isinstance(data, dict):
            continue
        # temp_path puede estar en la raíz (pending_fallback_pdf), anidado en
        # `resource_file` (pending_transcript) o en `_resource_file` — el
        # payload de `pending_note` usa el nombre con underscore, y sin
        # contemplarlo `/reset` y [Cancelar] popeaban el estado sin borrar el
        # temporal. En la RPi4 /tmp es tmpfs: RAM filtrada hasta el reinicio.
        # F6 de docs/audit-2026-07-31.md.
        temp_path = (
            data.get("temp_path")
            or (data.get("resource_file") or {}).get("temp_path")
            or (data.get("_resource_file") or {}).get("temp_path")
        )
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


async def _get_existing_items(vault_path: Path) -> tuple[list[dict], list[dict]]:
    """Obtiene proyectos y áreas existentes leyendo los subdirectorios de
    01-Projects/ y 02-Areas/ directamente. Si existe un _index.md con
    campo project:/area: y description:, los usa; si no, usa el nombre del
    directorio como nombre y descripción vacía.

    El escaneo (iterdir + parse de cada _index.md) es I/O bloqueante y corre en
    todo flujo de clasificación antes de cada classify(); se ejecuta en un thread
    para no congelar el event loop en la RPi4 con SD lenta.
    """
    def _scan() -> tuple[list[dict], list[dict]]:
        def _read_index(dir_path: Path, field: str) -> dict:
            index = dir_path / "_index.md"
            name = dir_path.name
            description = ""
            note = parse_cached(index)
            if note is not None:
                # `.get(field, name)` solo aplica el default si la clave FALTA:
                # un `_index.md` con `project:` a secas (YAML lo parsea como
                # None, trivial de producir editando el índice desde Obsidian)
                # dejaba `name=None`, y `item_token(None)` reventaba al construir
                # el selector — el proyecto entero quedaba inalcanzable como
                # destino. C10 de la auditoría 2026-08.
                name = str(note.frontmatter.get(field) or "").strip() or dir_path.name
                description = str(note.frontmatter.get("description") or "").strip()
            return {"name": name, "description": description}

        projects_dir = vault_path / "01-Projects"
        areas_dir = vault_path / "02-Areas"

        projects = [
            _read_index(d, "project")
            for d in sorted(projects_dir.iterdir())
            if d.is_dir()
        ] if projects_dir.exists() else []

        areas = [
            _read_index(d, "area")
            for d in sorted(areas_dir.iterdir())
            if d.is_dir()
        ] if areas_dir.exists() else []

        return projects, areas

    return await asyncio.to_thread(_scan)


async def _get_existing_tags(vault_path: Path, limit: int = 100) -> list[str]:
    """Retorna los tags confirmados del vault (sin Inbox), ordenados por frecuencia.

    Excluye 00-Inbox para que solo se propaguen tags de notas ya confirmadas por
    el usuario. Limita a `limit` tags para no inflar el system prompt.
    """
    exclude = [*DEFAULT_EXCLUDE_DIRS, "00-Inbox"]
    tag_counts = await get_all_tags(vault_path, exclude_dirs=exclude)
    return list(tag_counts.keys())[:limit]


async def count_unclassified_inbox(vault_path: Path) -> int:
    """Cuenta las notas del Inbox pendientes de clasificar y sin destino (Caso B).

    Es la regla que comparten `/clasificar` (para elegir qué nota procesar) y
    `_cb_confirm` (para avisar cuántas quedan): `status: pending-classification`
    en `00-Inbox/` y ni `project` ni `area` en el frontmatter. Antes cada uno
    la tenía copiada.

    Comportamiento ante error: una nota ilegible no cuenta y no interrumpe el
    conteo.
    """
    refs = await find_by_property(
        "status", "pending-classification", vault_path, scope="00-Inbox"
    )

    def _count() -> int:
        total = 0
        for ref in refs:
            note = parse_cached(ref.path)
            if note is None:
                continue
            if not note.frontmatter.get("project") and not note.frontmatter.get("area"):
                total += 1
        return total

    return await asyncio.to_thread(_count)
