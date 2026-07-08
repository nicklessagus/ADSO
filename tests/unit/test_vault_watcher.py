"""Tests unitarios para adso/vault_watcher.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adso.vault_watcher import CONFLICT_RE, VaultWatcher, _VaultEventHandler


# ---------------------------------------------------------------------------
# CONFLICT_RE
# ---------------------------------------------------------------------------

class TestConflictRe:
    @pytest.mark.parametrize("name", [
        "nota.sync-conflict-20240315-143022-ABCD1234.md",
        "mi nota.sync-conflict-20260101-000000-DEVICE01.md",
        "archivo.sync-conflict-20250601-120000-ABC.md",
    ])
    def test_matches_valid_conflict_names(self, name: str) -> None:
        assert CONFLICT_RE.search(name)

    @pytest.mark.parametrize("name", [
        "nota.md",
        "nota-backup.md",
        "sync-conflict-file.md",
        "nota.sync-conflict-notadate-ABC.md",
    ])
    def test_ignores_non_conflict_names(self, name: str) -> None:
        assert not CONFLICT_RE.search(name)


# ---------------------------------------------------------------------------
# _VaultEventHandler
# ---------------------------------------------------------------------------

class TestVaultEventHandler:
    @pytest.mark.asyncio
    async def test_on_created_queues_conflict(self) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/01-Projects/tesis/nota.sync-conflict-20240315-143022-ABCD1234.md"

        handler.on_created(event)
        await asyncio.sleep(0.01)

        assert not queue.empty()
        item = queue.get_nowait()
        assert item.path == Path(event.src_path)
        assert item.is_conflict is True

    @pytest.mark.asyncio
    async def test_on_created_queues_external_md(self) -> None:
        """on_created encola .md normales creados externamente (ej: desde Obsidian) para re-embed."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/01-Projects/tesis/nota.md"

        handler.on_created(event)
        await asyncio.sleep(0.01)

        assert not queue.empty()
        item = queue.get_nowait()
        assert item.path == Path(event.src_path)
        assert item.is_conflict is False

    @pytest.mark.asyncio
    async def test_on_modified_queues_external_change(self) -> None:
        """on_modified siempre encola cambios .md para re-embed."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/01-Projects/tesis/nota.md"

        handler.on_modified(event)
        await asyncio.sleep(0.01)

        assert not queue.empty()
        item = queue.get_nowait()
        assert item.path == Path(event.src_path)
        assert item.is_conflict is False

    @pytest.mark.asyncio
    async def test_on_created_ignores_atomic_write_tmp(self) -> None:
        """on_created ignora los temporales .adso-tmp-* de la escritura atómica del bot."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/00-Inbox/.adso-tmp-pejoj6nh.md"

        handler.on_created(event)
        await asyncio.sleep(0.01)

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_on_modified_and_deleted_ignore_hidden_files(self) -> None:
        """on_modified y on_deleted ignoran cualquier dotfile (temporales, ocultos de sync)."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/01-Projects/ADSO/.adso-tmp-38kxvvnz.md"

        handler.on_modified(event)
        handler.on_deleted(event)
        await asyncio.sleep(0.01)

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_on_modified_ignores_conflict_files(self) -> None:
        """on_modified ignora .sync-conflict-* (los detecta on_created)."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/nota.sync-conflict-20240315-143022-ABC.md"

        handler.on_modified(event)
        await asyncio.sleep(0.01)

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_on_deleted_queues_delete_event(self) -> None:
        """on_deleted encola borrados de .md con is_delete=True."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/01-Projects/tesis/nota.md"

        handler.on_deleted(event)
        await asyncio.sleep(0.01)

        assert not queue.empty()
        item = queue.get_nowait()
        assert item.path == Path(event.src_path)
        assert item.is_conflict is False
        assert item.is_delete is True

    @pytest.mark.asyncio
    async def test_on_deleted_ignores_conflict_files(self) -> None:
        """on_deleted ignora .sync-conflict-* — no son notas reales."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/nota.sync-conflict-20240315-143022-ABC.md"

        handler.on_deleted(event)
        await asyncio.sleep(0.01)

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_on_moved_emits_delete_for_src_and_change_for_dest(self) -> None:
        """Un rename externo (A.md → B.md) emite un delete para el origen y un
        change para el destino."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/01-Projects/tesis/vieja.md"
        event.dest_path = "/vault/01-Projects/tesis/nueva.md"

        handler.on_moved(event)
        await asyncio.sleep(0.01)

        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
        assert len(items) == 2
        delete_evt = next(i for i in items if i.is_delete)
        change_evt = next(i for i in items if not i.is_delete)
        assert delete_evt.path == Path(event.src_path)
        assert change_evt.path == Path(event.dest_path)
        assert change_evt.is_conflict is False

    @pytest.mark.asyncio
    async def test_on_moved_skips_hidden_temp_src(self) -> None:
        """La escritura atómica del bot (temp .adso-tmp-*.tmp → nota.md) no debe
        emitir delete del temporal, solo el change del destino real."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/00-Inbox/.adso-tmp-abc123.tmp"
        event.dest_path = "/vault/00-Inbox/nota.md"

        handler.on_moved(event)
        await asyncio.sleep(0.01)

        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
        assert len(items) == 1
        assert items[0].is_delete is False
        assert items[0].path == Path(event.dest_path)

    @pytest.mark.asyncio
    async def test_on_moved_dest_conflict_flagged(self) -> None:
        """Si el destino de un move es un .sync-conflict-*, se marca is_conflict."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/.adso-tmp-xyz.tmp"
        event.dest_path = "/vault/nota.sync-conflict-20240315-143022-ABC.md"

        handler.on_moved(event)
        await asyncio.sleep(0.01)

        assert not queue.empty()
        item = queue.get_nowait()
        assert item.is_conflict is True
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_ignores_non_md_files(self) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/vault/.obsidian/config.json"

        handler.on_created(event)
        handler.on_modified(event)
        await asyncio.sleep(0.01)

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_ignores_directory_events(self) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _VaultEventHandler(queue, loop)

        event = MagicMock()
        event.is_directory = True
        event.src_path = "/vault/01-Projects/nueva-carpeta"

        handler.on_created(event)
        await asyncio.sleep(0.01)

        assert queue.empty()


# ---------------------------------------------------------------------------
# VaultWatcher._notify_conflict / _notify_change
# ---------------------------------------------------------------------------

class TestVaultWatcherNotify:
    @pytest.fixture
    def watcher(self, tmp_path: Path) -> VaultWatcher:
        bot = MagicMock()
        bot.send_message = AsyncMock()
        vault = tmp_path / "vault"
        vault.mkdir()
        return VaultWatcher(vault_path=vault, bot=bot, chat_id=12345, debug=True)

    @pytest.mark.asyncio
    async def test_notify_conflict_includes_filename_and_dir(
        self, watcher: VaultWatcher, tmp_path: Path
    ) -> None:
        conflict_path = (
            tmp_path / "vault" / "01-Projects" / "tesis"
            / "nota.sync-conflict-20240315-143022-ABCD1234.md"
        )
        await watcher._notify_conflict(conflict_path)

        watcher._bot.send_message.assert_awaited_once()
        kwargs = watcher._bot.send_message.call_args.kwargs
        assert kwargs["chat_id"] == 12345
        assert "sync-conflict-20240315-143022-ABCD1234" in kwargs["text"]
        assert "01-Projects/tesis" in kwargs["text"]
        assert "⚠️" in kwargs["text"]
        assert kwargs["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_notify_conflict_omits_dir_when_in_root(
        self, watcher: VaultWatcher, tmp_path: Path
    ) -> None:
        conflict_path = (
            tmp_path / "vault" / "nota.sync-conflict-20240315-143022-ABCD1234.md"
        )
        await watcher._notify_conflict(conflict_path)
        text = watcher._bot.send_message.call_args.kwargs["text"]
        assert "en:" not in text

    @pytest.mark.asyncio
    async def test_notify_change_includes_debug_label(
        self, watcher: VaultWatcher, tmp_path: Path
    ) -> None:
        change_path = tmp_path / "vault" / "02-Areas" / "docencia" / "apuntes.md"
        await watcher._notify_change(change_path)

        kwargs = watcher._bot.send_message.call_args.kwargs
        assert "📝" in kwargs["text"]
        assert "debug" in kwargs["text"]
        assert "apuntes.md" in kwargs["text"]
        assert "02-Areas/docencia" in kwargs["text"]

    @pytest.mark.asyncio
    async def test_notify_delete_includes_debug_label(
        self, watcher: VaultWatcher, tmp_path: Path
    ) -> None:
        delete_path = tmp_path / "vault" / "02-Areas" / "docencia" / "apuntes.md"
        await watcher._notify_delete(delete_path)

        kwargs = watcher._bot.send_message.call_args.kwargs
        assert "🗑" in kwargs["text"]
        assert "debug" in kwargs["text"]
        assert "apuntes.md" in kwargs["text"]
        assert "02-Areas/docencia" in kwargs["text"]


# ---------------------------------------------------------------------------
# VaultWatcher stats
# ---------------------------------------------------------------------------

class TestVaultWatcherStats:
    @pytest.fixture
    def watcher(self, tmp_path: Path) -> VaultWatcher:
        bot = MagicMock()
        bot.send_message = AsyncMock()
        vault = tmp_path / "vault"
        vault.mkdir()
        return VaultWatcher(vault_path=vault, bot=bot, chat_id=12345, debug=True)

    @pytest.mark.asyncio
    async def test_on_external_change_callback_called(
        self, tmp_path: Path
    ) -> None:
        """El callback on_external_change se ejecuta para cambios no-conflicto."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        vault = tmp_path / "vault"
        vault.mkdir()
        callback = AsyncMock()

        watcher = VaultWatcher(
            vault_path=vault, bot=bot, chat_id=12345,
            debug=False, on_external_change=callback,
        )
        mock_observer = MagicMock()
        with patch("adso.vault_watcher._make_observer", return_value=mock_observer):
            await watcher.start()
            change_path = vault / "01-Projects" / "tesis" / "nota.md"
            from adso.vault_watcher import _VaultEvent
            await watcher._queue.put(_VaultEvent(path=change_path, is_conflict=False))
            await asyncio.sleep(0.05)

            callback.assert_awaited_once_with(change_path)
            # En modo no-debug no notifica por Telegram
            bot.send_message.assert_not_awaited()
            await watcher.stop()

    @pytest.mark.asyncio
    async def test_on_external_change_no_notification_without_debug(
        self, tmp_path: Path
    ) -> None:
        """Sin debug, cambios externos no generan mensaje Telegram."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        vault = tmp_path / "vault"
        vault.mkdir()

        watcher = VaultWatcher(vault_path=vault, bot=bot, chat_id=12345, debug=False)
        mock_observer = MagicMock()
        with patch("adso.vault_watcher._make_observer", return_value=mock_observer):
            await watcher.start()
            from adso.vault_watcher import _VaultEvent
            await watcher._queue.put(_VaultEvent(path=vault / "nota.md", is_conflict=False))
            await asyncio.sleep(0.05)

            bot.send_message.assert_not_awaited()
            await watcher.stop()

    @pytest.mark.asyncio
    async def test_on_external_delete_callback_called(
        self, tmp_path: Path
    ) -> None:
        """El callback on_external_delete se ejecuta para borrados externos."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        vault = tmp_path / "vault"
        vault.mkdir()
        callback = AsyncMock()

        watcher = VaultWatcher(
            vault_path=vault, bot=bot, chat_id=12345,
            debug=False, on_external_delete=callback,
        )
        mock_observer = MagicMock()
        with patch("adso.vault_watcher._make_observer", return_value=mock_observer):
            await watcher.start()
            delete_path = vault / "01-Projects" / "tesis" / "nota.md"
            from adso.vault_watcher import _VaultEvent
            await watcher._queue.put(
                _VaultEvent(path=delete_path, is_conflict=False, is_delete=True)
            )
            await asyncio.sleep(0.05)

            callback.assert_awaited_once_with(delete_path)
            # En modo no-debug no notifica por Telegram
            bot.send_message.assert_not_awaited()
            await watcher.stop()

    @pytest.mark.asyncio
    async def test_stats_update_on_delete(
        self, watcher: VaultWatcher, tmp_path: Path
    ) -> None:
        mock_observer = MagicMock()
        with patch("adso.vault_watcher._make_observer", return_value=mock_observer):
            await watcher.start()
            delete_path = tmp_path / "vault" / "01-Projects" / "tesis" / "nota.md"
            from adso.vault_watcher import _VaultEvent
            await watcher._queue.put(
                _VaultEvent(path=delete_path, is_conflict=False, is_delete=True)
            )
            await asyncio.sleep(0.05)

            assert watcher.stats.deletions_detected == 1
            assert watcher.stats.changes_detected == 0
            assert watcher.stats.last_event_at is not None
            await watcher.stop()

    def test_initial_stats(self, watcher: VaultWatcher) -> None:
        stats = watcher.stats
        assert stats.conflicts_detected == 0
        assert stats.changes_detected == 0
        assert stats.deletions_detected == 0
        assert stats.last_event_at is None
        assert stats.debug is True

    @pytest.mark.asyncio
    async def test_stats_update_on_conflict(
        self, watcher: VaultWatcher, tmp_path: Path
    ) -> None:
        mock_observer = MagicMock()
        with patch("adso.vault_watcher._make_observer", return_value=mock_observer):
            await watcher.start()
            conflict_path = tmp_path / "vault" / "nota.sync-conflict-20240315-143022-ABC.md"
            await watcher._queue.put(
                __import__("adso.vault_watcher", fromlist=["_VaultEvent"])._VaultEvent(
                    path=conflict_path, is_conflict=True
                )
            )
            await asyncio.sleep(0.05)

            assert watcher.stats.conflicts_detected == 1
            assert watcher.stats.last_event_at is not None
            assert watcher.stats.last_conflict_at is not None
            await watcher.stop()

    @pytest.mark.asyncio
    async def test_stats_update_on_change(
        self, watcher: VaultWatcher, tmp_path: Path
    ) -> None:
        mock_observer = MagicMock()
        with patch("adso.vault_watcher._make_observer", return_value=mock_observer):
            await watcher.start()
            change_path = tmp_path / "vault" / "01-Projects" / "tesis" / "nota.md"
            from adso.vault_watcher import _VaultEvent
            await watcher._queue.put(_VaultEvent(path=change_path, is_conflict=False))
            await asyncio.sleep(0.05)

            assert watcher.stats.changes_detected == 1
            assert watcher.stats.last_event_at is not None
            assert watcher.stats.last_conflict_at is None
            await watcher.stop()


# ---------------------------------------------------------------------------
# VaultWatcher lifecycle
# ---------------------------------------------------------------------------

class TestVaultWatcherLifecycle:
    @pytest.fixture
    def watcher(self, tmp_path: Path) -> VaultWatcher:
        bot = MagicMock()
        bot.send_message = AsyncMock()
        vault = tmp_path / "vault"
        vault.mkdir()
        return VaultWatcher(vault_path=vault, bot=bot, chat_id=12345)

    @pytest.mark.asyncio
    async def test_start_creates_task(self, watcher: VaultWatcher) -> None:
        mock_observer = MagicMock()
        with patch("adso.vault_watcher._make_observer", return_value=mock_observer):
            await watcher.start()
            assert watcher._task is not None
            assert not watcher._task.done()
            await watcher.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, watcher: VaultWatcher) -> None:
        mock_observer = MagicMock()
        with patch("adso.vault_watcher._make_observer", return_value=mock_observer):
            await watcher.start()
            await watcher.stop()
            assert watcher._task is None
            assert watcher._observer is None

    @pytest.mark.asyncio
    async def test_start_observer_failure_does_not_raise(
        self, watcher: VaultWatcher
    ) -> None:
        mock_observer = MagicMock()
        mock_observer.start.side_effect = OSError("inotify limit reached")
        with patch("adso.vault_watcher._make_observer", return_value=mock_observer):
            await watcher.start()
            assert watcher._task is None
