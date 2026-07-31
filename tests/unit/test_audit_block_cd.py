"""Tests de los bloques C y D de la auditoría 2026-07-31.

C1 — `body: null` se normaliza a "".
C2 — `tags` como string se parte y normaliza.
C3 — enums y título del fallback de Groq (case/tipo) no tiran la respuesta.
C4 — valores no-string del frontmatter no crashean filtros ni agrupamientos.
C5 — `scope_report` tolera fechas aware y naive mezcladas.
C6 — fecha imposible (`2026-02-30`) no revienta la escritura de la nota.
D1 — `flush()` espera el backup en vuelo y no lanza uno concurrente.
D2 — un fallo de `add`/`commit` notifica y re-encola los títulos.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adso.llm_schema import LLMResponseError, _validate_capture_payload
from adso.reporters import _priority_key, scope_report
from adso.vault_search import _extract_tags_from_note, _note_ref_from_data, search
from adso.vault_writer import GitBackup, NoteData, _parse_date_value, create_note


def _payload(**fm_extra) -> dict:
    fm = {"title": "Una nota", "type": "reference", "status": "active"}
    fm.update(fm_extra)
    return {"frontmatter": fm, "body": "cuerpo"}


def _note(path: Path, **fm) -> NoteData:
    return NoteData(path=path, frontmatter=fm, body="cuerpo")


# ---------------------------------------------------------------------------
# C1 — body null
# ---------------------------------------------------------------------------


class TestBodyNull:

    def test_null_body_becomes_empty_string(self) -> None:
        payload = _payload()
        payload["body"] = None
        _validate_capture_payload(payload)
        assert payload["body"] == ""

    def test_missing_body_still_becomes_empty_string(self) -> None:
        payload = _payload()
        del payload["body"]
        _validate_capture_payload(payload)
        assert payload["body"] == ""

    def test_existing_body_is_preserved(self) -> None:
        payload = _payload()
        _validate_capture_payload(payload)
        assert payload["body"] == "cuerpo"


# ---------------------------------------------------------------------------
# C2 — tags como string
# ---------------------------------------------------------------------------


class TestTagsString:

    def test_comma_separated_string_is_split(self) -> None:
        payload = _payload(tags="Machine Learning, ML ops")
        _validate_capture_payload(payload)
        assert payload["frontmatter"]["tags"] == ["machine-learning", "ml-ops"]

    def test_single_tag_string(self) -> None:
        payload = _payload(tags="python")
        _validate_capture_payload(payload)
        assert payload["frontmatter"]["tags"] == ["python"]

    def test_unexpected_type_becomes_empty_list(self) -> None:
        payload = _payload(tags={"a": 1})
        _validate_capture_payload(payload)
        assert payload["frontmatter"]["tags"] == []

    def test_none_becomes_empty_list(self) -> None:
        payload = _payload(tags=None)
        _validate_capture_payload(payload)
        assert payload["frontmatter"]["tags"] == []


# ---------------------------------------------------------------------------
# C3 — enums y título del fallback de Groq
# ---------------------------------------------------------------------------


class TestGroqEnums:

    def test_null_title_does_not_raise(self) -> None:
        payload = _payload(title=None)
        _validate_capture_payload(payload)
        assert payload["frontmatter"]["title"] == ""

    def test_non_string_title_is_coerced(self) -> None:
        payload = _payload(title=2024)
        _validate_capture_payload(payload)
        assert payload["frontmatter"]["title"] == "2024"

    def test_heading_and_label_combined_are_stripped(self) -> None:
        # El regex ancla ambas alternativas en ^: hace falta más de una pasada.
        payload = _payload(title="## Tarea: Revisar el paper")
        _validate_capture_payload(payload)
        assert payload["frontmatter"]["title"] == "Revisar el paper"

    def test_capitalized_type_and_status(self) -> None:
        payload = _payload(title="X", type="Task", status="Pending", priority="High")
        _validate_capture_payload(payload)
        fm = payload["frontmatter"]
        assert fm["type"] == "task"
        assert fm["status"] == "pending"
        assert fm["priority"] == "high"

    def test_capitalized_status_alias(self) -> None:
        payload = _payload(title="X", type="task", status="TODO")
        _validate_capture_payload(payload)
        assert payload["frontmatter"]["status"] == "pending"

    def test_unhashable_status_raises_llm_error_not_typeerror(self) -> None:
        payload = _payload(title="X", type="task", status={"value": "pending"})
        with pytest.raises(LLMResponseError):
            _validate_capture_payload(payload)

    def test_invalid_type_still_rejected(self) -> None:
        payload = _payload(type="paper")
        with pytest.raises(LLMResponseError):
            _validate_capture_payload(payload)


# ---------------------------------------------------------------------------
# C4 — valores no-string en el frontmatter
# ---------------------------------------------------------------------------


class TestNonStringFrontmatter:

    def test_note_ref_coerces_non_string_values(self, tmp_path: Path) -> None:
        note = _note(tmp_path / "n.md", title=2024, type=None, status=None)
        ref = _note_ref_from_data(note)
        assert ref.title == "2024"
        assert ref.note_type == ""
        assert ref.status == ""

    def test_tags_as_string_in_vault_note(self, tmp_path: Path) -> None:
        note = _note(tmp_path / "n.md", tags="Foo, bar")
        assert _extract_tags_from_note(note) == {"foo", "bar"}

    @pytest.mark.asyncio
    async def test_search_survives_hand_edited_note(self, tmp_path: Path) -> None:
        # `title: 2024` (int) y `project:` vacío (None) rompían toda la búsqueda.
        (tmp_path / "rota.md").write_text(
            "---\ntitle: 2024\ntype: reference\nstatus:\nproject:\narea:\n---\ncontenido\n"
        )
        (tmp_path / "ok.md").write_text(
            "---\ntitle: Buena\ntype: reference\nstatus: active\n---\ncontenido buscado\n"
        )
        results = await search("type:reference contenido", tmp_path)
        assert any(r.title == "Buena" for r in results)

    def test_priority_key_tolerates_non_string(self, tmp_path: Path) -> None:
        assert _priority_key(_note(tmp_path / "a.md", priority="High")) == 0
        assert _priority_key(_note(tmp_path / "b.md", priority=3)) == 3
        assert _priority_key(_note(tmp_path / "c.md")) == 3


# ---------------------------------------------------------------------------
# C5 — datetimes aware y naive en scope_report
# ---------------------------------------------------------------------------


class TestScopeReportDates:

    @pytest.mark.asyncio
    async def test_mixed_aware_and_naive_dates(self, tmp_path: Path) -> None:
        (tmp_path / "aware.md").write_text(
            "---\ntitle: Aware\ntype: reference\nstatus: active\n"
            "date_modified: '2026-01-02T10:00:00+0000'\n---\ncuerpo\n"
        )
        (tmp_path / "naive.md").write_text(
            "---\ntitle: Naive\ntype: reference\nstatus: active\n"
            "date_modified: '2026-01-03T10:00:00'\n---\ncuerpo\n"
        )
        content = await scope_report(tmp_path)
        assert b"Aware" in content and b"Naive" in content


# ---------------------------------------------------------------------------
# C6 — fechas imposibles
# ---------------------------------------------------------------------------


class TestInvalidDates:

    def test_impossible_date_returns_original_string(self) -> None:
        assert _parse_date_value("2026-02-30") == "2026-02-30"

    def test_impossible_datetime_returns_original_string(self) -> None:
        assert _parse_date_value("2026-02-30T10:00:00") == "2026-02-30T10:00:00"

    def test_valid_dates_still_parsed(self) -> None:
        assert _parse_date_value("2026-02-28") == date(2026, 2, 28)
        assert _parse_date_value("2026-02-28T10:00:00") == datetime(2026, 2, 28, 10, 0)

    @pytest.mark.asyncio
    async def test_create_note_with_impossible_due_date(self, tmp_path: Path) -> None:
        fm = {
            "title": "Tarea rota",
            "type": "task",
            "status": "pending",
            "due_date": "2026-02-30",
            "date_created": "2026-02-30T09:00:00",
        }
        path = await create_note(fm, "cuerpo", tmp_path)
        assert path.exists()
        assert "2026-02-30" in path.read_text()


# ---------------------------------------------------------------------------
# D — GitBackup
# ---------------------------------------------------------------------------


class TestGitBackupConcurrency:
    """D1 — flush() espera el backup en vuelo; D2 — fallo notifica y re-encola."""

    @staticmethod
    async def _wait_for(flag: threading.Event, timeout: float = 5.0) -> None:
        """Espera un `threading.Event` sin bloquear el event loop."""
        deadline = asyncio.get_running_loop().time() + timeout
        while not flag.is_set():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("timeout esperando el evento")
            await asyncio.sleep(0.01)

    @pytest.mark.asyncio
    async def test_flush_waits_for_in_flight_backup(self, tmp_path: Path) -> None:
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def fake_sync(message: str) -> tuple[str, str]:
            calls.append(message)
            started.set()
            release.wait(5)
            return ("pushed", "")

        backup = GitBackup(tmp_path, debounce_seconds=0)
        backup._sync_backup = fake_sync  # type: ignore[method-assign]

        await backup.notify("Nota A")
        await self._wait_for(started)

        flush_task = asyncio.ensure_future(backup.flush())
        await asyncio.sleep(0.05)
        # El backup sigue en vuelo → flush no puede haber terminado.
        assert not flush_task.done()

        release.set()
        await asyncio.wait_for(flush_task, 5)

        # Un solo _sync_backup: flush no lanzó uno concurrente.
        assert calls == ["Add note: Nota A"]

    @pytest.mark.asyncio
    async def test_flush_backs_up_titles_added_during_in_flight(self, tmp_path: Path) -> None:
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []
        concurrent = 0
        max_concurrent = 0

        def fake_sync(message: str) -> tuple[str, str]:
            nonlocal concurrent, max_concurrent
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            calls.append(message)
            started.set()
            release.wait(5)
            concurrent -= 1
            return ("pushed", "")

        backup = GitBackup(tmp_path, debounce_seconds=300)
        backup._sync_backup = fake_sync  # type: ignore[method-assign]

        await backup.notify("Nota A")
        # Forzar el primer backup y dejarlo en vuelo.
        first = asyncio.ensure_future(backup.flush())
        await self._wait_for(started)

        await backup.notify("Nota B")
        release.set()
        await asyncio.wait_for(first, 5)
        await asyncio.wait_for(backup.flush(), 5)

        assert calls == ["Add note: Nota A", "Add note: Nota B"]
        assert max_concurrent == 1  # nunca dos git en paralelo
        assert backup._pending_titles == []

    @pytest.mark.asyncio
    async def test_commit_failure_notifies_and_requeues(self, tmp_path: Path) -> None:
        bot = SimpleNamespace(send_message=AsyncMock())

        def boom(message: str) -> tuple[str, str]:
            raise RuntimeError("index.lock existe")

        backup = GitBackup(tmp_path, debounce_seconds=0, bot=bot, chat_id=99)
        backup._sync_backup = boom  # type: ignore[method-assign]

        await backup.notify("Nota perdida")
        await asyncio.sleep(0.2)

        # Re-encolada: el próximo backup la incluye.
        assert backup._pending_titles == ["Nota perdida"]
        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.kwargs["text"]
        assert "index.lock existe" in text

    @pytest.mark.asyncio
    async def test_requeued_titles_included_in_next_backup(self, tmp_path: Path) -> None:
        calls: list[str] = []
        fail = True

        def flaky(message: str) -> tuple[str, str]:
            calls.append(message)
            if fail:
                raise RuntimeError("disco lleno")
            return ("pushed", "")

        backup = GitBackup(tmp_path, debounce_seconds=0)
        backup._sync_backup = flaky  # type: ignore[method-assign]

        await backup.notify("Nota A")
        await asyncio.sleep(0.2)

        fail = False
        await backup.notify("Nota B")
        await asyncio.sleep(0.2)

        assert calls[-1] == "Add 2 notes: Nota A, Nota B"
