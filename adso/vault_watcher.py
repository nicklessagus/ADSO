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
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileSystemEventHandler

logger = logging.getLogger(__name__)

# Patrón de Syncthing: nombre.sync-conflict-YYYYMMDD-HHMMSS-DEVICEID.md
CONFLICT_RE = re.compile(r"\.sync-conflict-\d{8}-\d{6}-[A-Z0-9]+\.md$", re.IGNORECASE)


@dataclass(frozen=True)
class _VaultEvent:
    path: Path
    is_conflict: bool


@dataclass
class WatcherStats:
    """Estadísticas del watcher para exponer en /status."""
    debug: bool = False
    last_event_at: Optional[datetime] = None
    last_conflict_at: Optional[datetime] = None
    conflicts_detected: int = 0
    changes_detected: int = 0


class _VaultEventHandler(FileSystemEventHandler):
    """Handler de watchdog: detecta conflictos (on_created) y cambios externos (on_modified)."""

    def __init__(
        self,
        queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._queue = queue
        self._loop = loop

    def on_created(self, event: FileCreatedEvent) -> None:
        """Solo encola conflictos — las notas nuevas las crea ADSO y ya las indexa."""
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix == ".md" and CONFLICT_RE.search(path.name):
            asyncio.run_coroutine_threadsafe(
                self._queue.put(_VaultEvent(path=path, is_conflict=True)),
                self._loop,
            )

    def on_modified(self, event: FileModifiedEvent) -> None:
        """Encola toda modificación de .md no-conflicto para re-embed."""
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix == ".md" and not CONFLICT_RE.search(path.name):
            asyncio.run_coroutine_threadsafe(
                self._queue.put(_VaultEvent(path=path, is_conflict=False)),
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
    """

    def __init__(
        self,
        vault_path: Path,
        bot,
        chat_id: int,
        debug: bool = False,
        on_external_change: Optional[Callable[[Path], Awaitable[None]]] = None,
    ) -> None:
        self._vault_path = vault_path
        self._bot = bot
        self._chat_id = chat_id
        self._debug = debug
        self._on_external_change = on_external_change
        self._queue: asyncio.Queue[_VaultEvent] = asyncio.Queue()
        self._observer = None
        self._task: Optional[asyncio.Task] = None
        self._stats = WatcherStats(debug=debug)

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
        logger.info("VaultWatcher detenido.")

    async def _dispatch_loop(self) -> None:
        """Lee la queue y despacha a la notificación correspondiente."""
        while True:
            event = await self._queue.get()
            now = datetime.now()
            self._stats.last_event_at = now

            if event.is_conflict:
                self._stats.last_conflict_at = now
                self._stats.conflicts_detected += 1
                try:
                    await self._notify_conflict(event.path)
                except Exception as exc:
                    logger.error(
                        "VaultWatcher: error notificando conflicto %s: %s", event.path, exc
                    )
            else:
                self._stats.changes_detected += 1
                if self._on_external_change:
                    asyncio.create_task(self._on_external_change(event.path))
                if self._debug:
                    try:
                        await self._notify_change(event.path)
                    except Exception as exc:
                        logger.error(
                            "VaultWatcher: error notificando cambio %s: %s", event.path, exc
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
