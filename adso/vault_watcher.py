"""Monitoreo del vault en busca de conflictos de Syncthing y cambios externos.

Corre como tarea async en background. Siempre alerta sobre archivos
.sync-conflict-* y dispara re-embed cuando una nota es modificada externamente.
En modo debug, también notifica cada cambio externo por Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)

logger = logging.getLogger(__name__)

# Patrón de Syncthing: nombre.sync-conflict-YYYYMMDD-HHMMSS-DEVICEID.md
CONFLICT_RE = re.compile(r"\.sync-conflict-\d{8}-\d{6}-[A-Z0-9]+\.md$", re.IGNORECASE)


def _is_hidden(path: Path) -> bool:
    """Archivos ocultos no son notas: temporales .adso-tmp-* de la escritura
    atómica del bot, dotfiles de Syncthing/Obsidian, etc. Sin este filtro el
    watcher los indexa en ChromaDB como notas fantasma."""
    return path.name.startswith(".")


@dataclass(frozen=True)
class _VaultEvent:
    path: Path
    is_conflict: bool
    is_delete: bool = False


@dataclass
class WatcherStats:
    """Estadísticas del watcher para exponer en /status."""
    debug: bool = False
    last_event_at: Optional[datetime] = None
    last_conflict_at: Optional[datetime] = None
    conflicts_detected: int = 0
    changes_detected: int = 0
    deletions_detected: int = 0


class _VaultEventHandler(FileSystemEventHandler):
    """Handler de watchdog: detecta conflictos, cambios y borrados externos
    (on_created/on_modified/on_deleted/on_moved)."""

    def __init__(
        self,
        queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._queue = queue
        self._loop = loop

    def on_created(self, event: FileCreatedEvent) -> None:
        """Encola conflictos y notas .md nuevas creadas externamente (ej: desde Obsidian)."""
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix != ".md" or _is_hidden(path):
            return
        if CONFLICT_RE.search(path.name):
            asyncio.run_coroutine_threadsafe(
                self._queue.put(_VaultEvent(path=path, is_conflict=True)),
                self._loop,
            )
        else:
            asyncio.run_coroutine_threadsafe(
                self._queue.put(_VaultEvent(path=path, is_conflict=False)),
                self._loop,
            )

    def on_modified(self, event: FileModifiedEvent) -> None:
        """Encola toda modificación de .md no-conflicto para re-embed."""
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix == ".md" and not _is_hidden(path) and not CONFLICT_RE.search(path.name):
            asyncio.run_coroutine_threadsafe(
                self._queue.put(_VaultEvent(path=path, is_conflict=False)),
                self._loop,
            )

    def on_deleted(self, event: FileDeletedEvent) -> None:
        """Encola borrados de .md para eliminar su embedding de ChromaDB."""
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix == ".md" and not _is_hidden(path) and not CONFLICT_RE.search(path.name):
            asyncio.run_coroutine_threadsafe(
                self._queue.put(_VaultEvent(path=path, is_conflict=False, is_delete=True)),
                self._loop,
            )

    def on_moved(self, event: FileMovedEvent) -> None:
        """Encola renames/moves: inotify los reporta como FileMovedEvent.

        Cubre dos casos críticos que on_created/on_modified no ven:
        1. Syncthing aplica cambios remotos escribiendo un temporal y
           renombrándolo sobre la nota → sin esto, las ediciones sincronizadas
           desde otros dispositivos no disparaban re-embed hasta el reindex
           nocturno (el caso de uso central del watcher).
        2. Editores externos con guardado atómico (vim, etc.).

        También lo dispara la escritura atómica del propio bot (temp → nota); el
        temp es hidden y su suffix es `.tmp`, así que el origen se saltea y el
        destino (la nota) cae en bot_written_paths → no genera doble embed.

        Emite un delete para el origen (su embedding queda huérfano) y un change
        —o conflicto— para el destino, respetando los filtros de siempre.
        """
        if event.is_directory:
            return
        src = Path(event.src_path)
        if src.suffix == ".md" and not _is_hidden(src) and not CONFLICT_RE.search(src.name):
            asyncio.run_coroutine_threadsafe(
                self._queue.put(_VaultEvent(path=src, is_conflict=False, is_delete=True)),
                self._loop,
            )
        dest = Path(event.dest_path)
        if dest.suffix == ".md" and not _is_hidden(dest):
            asyncio.run_coroutine_threadsafe(
                self._queue.put(
                    _VaultEvent(path=dest, is_conflict=bool(CONFLICT_RE.search(dest.name)))
                ),
                self._loop,
            )


class VaultWatcher:
    """Monitorea el vault y notifica por Telegram eventos relevantes.

    Siempre: detecta conflictos .sync-conflict-* y notifica.
    Siempre: re-embeds notas modificadas externamente (via on_external_change).
    En modo debug: además notifica cada cambio externo por Telegram.

    Usa inotify en Linux con fallback a PollingObserver si inotify no propaga
    eventos correctamente (ej: algunos bind mounts de Docker).

    Args:
        vault_path: Raíz del vault a monitorear.
        bot: Instancia del bot de Telegram para enviar mensajes.
        chat_id: ID del chat al que enviar las notificaciones.
        debug: Si True, notifica también cambios externos por Telegram.
        on_external_change: Callback async llamado con el Path de cada .md
            modificado externamente. Usado para disparar re-embed inmediato.
        on_external_delete: Callback async llamado con el Path de cada .md
            borrado externamente. Usado para eliminar su embedding de ChromaDB.
    """

    def __init__(
        self,
        vault_path: Path,
        bot,
        chat_id: int,
        debug: bool = False,
        on_external_change: Optional[Callable[[Path], Awaitable[None]]] = None,
        on_external_delete: Optional[Callable[[Path], Awaitable[None]]] = None,
    ) -> None:
        self._vault_path = vault_path
        self._bot = bot
        self._chat_id = chat_id
        self._debug = debug
        self._on_external_change = on_external_change
        self._on_external_delete = on_external_delete
        self._queue: asyncio.Queue[_VaultEvent] = asyncio.Queue()
        self._observer = None
        self._task: Optional[asyncio.Task] = None
        # Referencias fuertes a las tareas de callback (evita GC prematuro) y
        # permite drenarlas en stop().
        self._bg_tasks: "set[asyncio.Task]" = set()
        self._stats = WatcherStats(debug=debug)
        # Deduplicación: evita notificar el mismo path dos veces en menos de 2s
        # (inotify dispara CREATE + MODIFY al escribir un archivo nuevo)
        self._recent_events: Dict[Path, datetime] = {}
        self._dedup_window = timedelta(seconds=2)
        # Cambios deduplicados esperando el trailing edge de su ventana (F2).
        self._trailing_tasks: Dict[Path, asyncio.Task] = {}

    @property
    def stats(self) -> WatcherStats:
        return self._stats

    async def start(self) -> None:
        """Arranca el observer de watchdog y la tarea de dispatch."""
        loop = asyncio.get_running_loop()
        handler = _VaultEventHandler(self._queue, loop)

        self._observer = _make_observer()
        self._observer.schedule(handler, str(self._vault_path), recursive=True)

        try:
            self._observer.start()
        except Exception as exc:
            logger.error("VaultWatcher: no se pudo iniciar observer: %s", exc)
            # Sin esto el observer queda seteado y `stop()` hace join() sobre un
            # thread nunca arrancado -> RuntimeError, que en el shutdown de PTB
            # abortaba la corutina ANTES del flush del git backup.
            self._observer = None
            return

        self._task = asyncio.create_task(self._dispatch_loop(), name="vault_watcher")
        mode = "debug" if self._debug else "normal"
        logger.info("VaultWatcher iniciado en %s (modo %s)", self._vault_path, mode)

    async def stop(self) -> None:
        """Detiene el observer y cancela la tarea de dispatch."""
        if self._observer:
            await asyncio.to_thread(self._observer.stop)
            await asyncio.to_thread(self._observer.join)
            self._observer = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Cambios esperando el trailing edge: se disparan YA en vez de
        # cancelarse. Si no, un shutdown dentro de la ventana pierde el último
        # save igual que antes del fix de F2 — con el agravante de que el
        # usuario cree que el bot se apagó limpio.
        if self._trailing_tasks:
            pendientes = list(self._trailing_tasks.items())
            self._trailing_tasks.clear()
            for path, task in pendientes:
                task.cancel()
                if self._on_external_change:
                    self._spawn(self._on_external_change(path))
        # Drenar tareas de callback en vuelo (re-embed/delete) antes de salir.
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        logger.info("VaultWatcher detenido.")

    def _spawn(self, coro) -> None:
        """Lanza una tarea de callback guardando referencia fuerte y logueando errores."""
        task = asyncio.ensure_future(coro)
        self._bg_tasks.add(task)

        def _done(t: "asyncio.Task") -> None:
            self._bg_tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    logger.error("VaultWatcher: callback falló: %r", exc)

        task.add_done_callback(_done)

    def _spawn_notification(self, coro, path: Path, kind: str) -> None:
        """Despacha una notificación de Telegram sin frenar el drenado de la cola.

        Antes se esperaba `send_message` **dentro** del loop que consume la
        cola: un envío lento o con timeout (~5 s de PTB) congelaba el drenado
        justo en el peor momento, una ráfaga de Syncthing, que es cuando más
        eventos llegan y cuando el re-embed más necesita ir al día (#38).

        La tarea queda en `_bg_tasks`, así que `stop()` la espera: lanzar en
        background no puede degenerar en "Task was destroyed but it is
        pending", que además se lleva puesto el flush del git backup.

        Args:
            coro: Corrutina de notificación ya construida.
            path: Path del evento, solo para el log de error.
            kind: Etiqueta del evento para el log ("conflicto", "cambio"…).
        """

        async def _runner() -> None:
            try:
                await coro
            except Exception as exc:
                logger.error(
                    "VaultWatcher: error notificando %s %s: %s", kind, path, exc
                )

        self._spawn(_runner())

    def _is_duplicate(self, path: Path) -> bool:
        """Devuelve True si el path fue procesado hace menos de dedup_window."""
        now = datetime.now()
        last = self._recent_events.get(path)
        if last and (now - last) < self._dedup_window:
            return True
        self._recent_events[path] = now
        # Limpiar entradas viejas para no acumular memoria
        cutoff = now - self._dedup_window * 10
        self._recent_events = {p: t for p, t in self._recent_events.items() if t > cutoff}
        return False

    def _schedule_trailing_change(self, path: Path) -> None:
        """Re-agenda un cambio deduplicado para el final de la ventana.

        El dedup solo descartaba el evento, y eso perdía el **último** save:
        Obsidian autosalva dos veces en menos de la ventana y después el
        usuario deja de editar, así que el re-embed corría con el contenido
        intermedio y el estado final no se indexaba hasta el reindex nocturno.
        Con trailing edge, una ráfaga colapsa a dos llamadas —una inmediata y
        una al final— en vez de a una sola con contenido viejo. F2 de
        docs/audit-2026-07-31.md.

        Args:
            path: Path del archivo cuyo evento se dedupeó.
        """
        previa = self._trailing_tasks.get(path)
        if previa and not previa.done():
            # Ráfaga larga: la ventana se corre hacia adelante, no se acumulan
            # tareas por evento.
            previa.cancel()
        self._trailing_tasks[path] = asyncio.create_task(self._fire_trailing_change(path))

    async def _fire_trailing_change(self, path: Path) -> None:
        """Espera a que venza la ventana y despacha el cambio pendiente."""
        try:
            await asyncio.sleep(self._dedup_window.total_seconds())
        except asyncio.CancelledError:
            return
        self._trailing_tasks.pop(path, None)
        self._recent_events[path] = datetime.now()
        self._stats.changes_detected += 1
        if self._on_external_change:
            self._spawn(self._on_external_change(path))
        if self._debug:
            try:
                await self._notify_change(path)
            except Exception as exc:
                logger.error(
                    "VaultWatcher: error notificando cambio %s: %s", path, exc
                )

    async def _dispatch_loop(self) -> None:
        """Lee la queue y despacha a la notificación correspondiente."""
        while True:
            event = await self._queue.get()
            now = datetime.now()
            self._stats.last_event_at = now

            if not event.is_conflict and not event.is_delete and self._is_duplicate(event.path):
                # No se descarta: se re-agenda para el final de la ventana, si
                # no se pierde el último save de la ráfaga (F2).
                self._schedule_trailing_change(event.path)
                continue

            if event.is_conflict:
                self._stats.last_conflict_at = now
                self._stats.conflicts_detected += 1
                self._spawn_notification(
                    self._notify_conflict(event.path), event.path, "conflicto"
                )
            elif event.is_delete:
                self._stats.deletions_detected += 1
                if self._on_external_delete:
                    self._spawn(self._on_external_delete(event.path))
                if self._debug:
                    self._spawn_notification(
                        self._notify_delete(event.path), event.path, "borrado"
                    )
            else:
                self._stats.changes_detected += 1
                if self._on_external_change:
                    self._spawn(self._on_external_change(event.path))
                if self._debug:
                    self._spawn_notification(
                        self._notify_change(event.path), event.path, "cambio"
                    )

    async def _notify_conflict(self, path: Path) -> None:
        """Notifica al usuario sobre un conflicto de Syncthing."""
        rel, dir_part = self._rel_parts(path)
        lines = [
            "⚠️ Conflicto de sincronización detectado:",
            f"  <code>{path.name}</code>",
        ]
        if dir_part:
            lines.append(f"  en: <code>{dir_part}/</code>")
        lines.append("")
        lines.append("Resolver el conflicto manualmente.")

        await self._bot.send_message(
            chat_id=self._chat_id,
            text="\n".join(lines),
            parse_mode="HTML",
        )

    async def _notify_change(self, path: Path) -> None:
        """Notifica sobre un cambio externo en modo debug."""
        rel, dir_part = self._rel_parts(path)
        lines = [
            "📝 [debug] Cambio externo detectado:",
            f"  <code>{path.name}</code>",
        ]
        if dir_part:
            lines.append(f"  en: <code>{dir_part}/</code>")
        lines.append("")
        lines.append("Reindexando embedding...")

        await self._bot.send_message(
            chat_id=self._chat_id,
            text="\n".join(lines),
            parse_mode="HTML",
        )

    async def _notify_delete(self, path: Path) -> None:
        """Notifica sobre un borrado externo en modo debug."""
        rel, dir_part = self._rel_parts(path)
        lines = [
            "🗑 [debug] Nota borrada externamente:",
            f"  <code>{path.name}</code>",
        ]
        if dir_part:
            lines.append(f"  en: <code>{dir_part}/</code>")
        lines.append("")
        lines.append("Eliminando embedding de ChromaDB...")

        await self._bot.send_message(
            chat_id=self._chat_id,
            text="\n".join(lines),
            parse_mode="HTML",
        )

    def _rel_parts(self, path: Path) -> tuple[str, str]:
        """Devuelve (ruta relativa, directorio relativo) para mostrar en notificaciones."""
        try:
            rel = path.relative_to(self._vault_path)
            dir_part = str(rel.parent) if str(rel.parent) != "." else ""
        except ValueError:
            dir_part = ""
        return str(path.name), dir_part


def _make_observer():
    """Crea un Observer preferiendo inotify en Linux, con fallback a polling."""
    try:
        from watchdog.observers.inotify import InotifyObserver
        return InotifyObserver()
    except Exception:
        logger.warning("VaultWatcher: inotify no disponible, usando PollingObserver (10s)")
        from watchdog.observers.polling import PollingObserver
        return PollingObserver(timeout=10)
