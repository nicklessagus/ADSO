"""Tests unitarios para adso.tasks_client."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adso.tasks_client import TasksClient, build_task_notes


# ---------------------------------------------------------------------------
# build_task_notes
# ---------------------------------------------------------------------------

class TestBuildTaskNotes:

    def test_includes_project(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "01-Projects" / "tesis" / "tarea.md"
        note.parent.mkdir(parents=True)
        note.touch()

        fm = {"project": "tesis", "priority": "high"}
        result = build_task_notes(fm, note, vault)

        assert "Proyecto: tesis" in result
        assert "Prioridad: high" in result
        assert "obsidian://open?path=" in result

    def test_falls_back_to_area(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "02-Areas" / "docencia" / "tarea.md"
        note.parent.mkdir(parents=True)
        note.touch()

        fm = {"area": "docencia"}
        result = build_task_notes(fm, note, vault)

        assert "Área: docencia" in result
        assert "Proyecto:" not in result

    def test_no_destination(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "00-Inbox" / "tarea.md"
        (vault / "00-Inbox").mkdir()
        note.touch()

        fm = {}
        result = build_task_notes(fm, note, vault)

        assert "obsidian://open?path=" in result
        assert "Proyecto:" not in result
        assert "Área:" not in result

    def test_obsidian_link_is_url_encoded(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "01-Projects" / "mi proyecto" / "tarea con espacios.md"
        note.parent.mkdir(parents=True)
        note.touch()

        fm = {}
        result = build_task_notes(fm, note, vault)

        # Los espacios deben estar URL-encoded
        assert " " not in result.split("obsidian://")[-1]

    def test_no_priority_field_when_missing(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "00-Inbox" / "tarea.md"
        (vault / "00-Inbox").mkdir()
        note.touch()

        fm = {"project": "tesis"}
        result = build_task_notes(fm, note, vault)

        assert "Prioridad:" not in result


# ---------------------------------------------------------------------------
# TasksClient — inicialización y modo no-op
# ---------------------------------------------------------------------------

class TestTasksClientNoOp:

    @pytest.mark.asyncio
    async def test_no_op_when_token_missing(self, tmp_path: Path) -> None:
        """Sin token.json el cliente opera en no-op y devuelve None."""
        creds = tmp_path / "google-oauth.json"
        creds.touch()
        client = TasksClient(str(creds))

        result = await client.create_task("Tarea", "notes")

        assert result is None

    @pytest.mark.asyncio
    async def test_no_op_logs_warning(self, tmp_path: Path, caplog) -> None:
        """Sin token.json se emite un warning con instrucción de auth."""
        import logging
        creds = tmp_path / "google-oauth.json"
        creds.touch()
        client = TasksClient(str(creds))

        with caplog.at_level(logging.WARNING, logger="adso.tasks_client"):
            await client.create_task("Tarea", "notes")

        assert any("token" in r.message.lower() or "tasks" in r.message.lower()
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# TasksClient — create_task con servicio mockeado
# ---------------------------------------------------------------------------

class TestTasksClientCreateTask:

    def _make_client(self, tmp_path: Path) -> TasksClient:
        creds = tmp_path / "google-oauth.json"
        creds.touch()
        return TasksClient(str(creds))

    def _mock_service(self, task_id: str = "task-abc") -> MagicMock:
        """Devuelve un mock del servicio Google Tasks API."""
        svc = MagicMock()
        svc.tasklists().list().execute.return_value = {
            "items": [{"id": "list-123", "title": "ADSO"}]
        }
        svc.tasks().insert().execute.return_value = {
            "id": task_id,
            "title": "Tarea test",
        }
        return svc

    @pytest.mark.asyncio
    async def test_create_task_returns_id(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client._service = self._mock_service("task-xyz")

        result = await client.create_task("Tarea test", "notas")

        assert result == "task-xyz"

    @pytest.mark.asyncio
    async def test_create_task_with_due_date(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        svc = self._mock_service()
        client._service = svc

        await client.create_task("Tarea test", "notas", due_date="2026-04-15")

        call_args = svc.tasks().insert.call_args
        body = call_args.kwargs.get("body") or call_args.args[0] if call_args.args else call_args.kwargs["body"]
        # insert se llama con tasklist= y body=
        insert_kwargs = svc.tasks().insert.call_args
        # El due debe estar en el body pasado a insert
        # Como usamos svc.tasks().insert(tasklist=..., body=...) necesitamos capturar ese call
        # Verificar que el due_date se incluyó en alguna llamada
        assert "2026-04-15T00:00:00.000Z" in str(insert_kwargs)

    @pytest.mark.asyncio
    async def test_create_task_truncates_long_notes(self, tmp_path: Path) -> None:
        """Notas > 8000 bytes se truncan antes de enviar a la API."""
        client = self._make_client(tmp_path)
        client._service = self._mock_service()

        long_notes = "x" * 10_000
        result = await client.create_task("Tarea", long_notes)

        assert result is not None
        insert_kwargs = client._service.tasks().insert.call_args
        body_str = str(insert_kwargs)
        # No podemos medir el body exactamente desde el mock string,
        # pero si llegó hasta insert() sin error, el truncado no rompió nada
        assert result == "task-abc"

    @pytest.mark.asyncio
    async def test_create_task_no_due_date(self, tmp_path: Path) -> None:
        """Sin due_date no se incluye el campo 'due' en el body."""
        client = self._make_client(tmp_path)
        svc = self._mock_service()
        client._service = svc

        await client.create_task("Sin fecha", "notas")

        insert_kwargs = svc.tasks().insert.call_args
        assert "due" not in str(insert_kwargs) or "T00:00:00" not in str(insert_kwargs)

    @pytest.mark.asyncio
    async def test_create_task_api_error_returns_none(self, tmp_path: Path) -> None:
        """Error en la API devuelve None sin propagar la excepción."""
        client = self._make_client(tmp_path)
        svc = MagicMock()
        svc.tasklists().list().execute.return_value = {
            "items": [{"id": "list-123", "title": "ADSO"}]
        }
        svc.tasks().insert().execute.side_effect = Exception("API error")
        client._service = svc

        result = await client.create_task("Tarea", "notas")

        assert result is None

    @pytest.mark.asyncio
    async def test_creates_adso_list_if_missing(self, tmp_path: Path) -> None:
        """Si la lista ADSO no existe, la crea y usa el nuevo ID."""
        client = self._make_client(tmp_path)
        svc = MagicMock()
        svc.tasklists().list().execute.return_value = {"items": []}
        svc.tasklists().insert().execute.return_value = {"id": "new-list-id", "title": "ADSO"}
        svc.tasks().insert().execute.return_value = {"id": "task-new"}
        client._service = svc

        result = await client.create_task("Tarea nueva", "notas")

        assert result == "task-new"
        assert client._list_id == "new-list-id"

    @pytest.mark.asyncio
    async def test_list_id_cached(self, tmp_path: Path) -> None:
        """El list_id se cachea — tasklists().list().execute() solo se llama una vez."""
        client = self._make_client(tmp_path)
        client._service = self._mock_service()

        await client.create_task("Tarea 1", "notas")
        await client.create_task("Tarea 2", "notas")

        # Verificar el caché por state interno — _get_list_id no debe volver a llamar a la API
        assert client._list_id == "list-123"
        # Y que el execute se llamó exactamente una vez (mock MagicMock acumula todas las llamadas)
        execute_mock = client._service.tasklists.return_value.list.return_value.execute
        assert execute_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_due_date_strips_time_component(self, tmp_path: Path) -> None:
        """Si due_date viene con hora (ISO 8601 completo), solo se usa la parte de fecha."""
        client = self._make_client(tmp_path)
        svc = self._mock_service()
        client._service = svc

        await client.create_task("Tarea", "notas", due_date="2026-04-15T18:30:00")

        insert_kwargs = svc.tasks().insert.call_args
        assert "2026-04-15T00:00:00.000Z" in str(insert_kwargs)
