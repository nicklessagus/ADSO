"""Tests de resiliencia ante fallos de escritura al vault (bloque A de la auditoría 2026-07-31).

A2 — `_cb_confirm` no debe descartar `pending_note` si `create_note` falla.
A3 — `reclassify_inbox` no debe borrar la nota de Inbox antes de crear la nueva.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from unittest.mock import AsyncMock, patch

from adso.handlers.capture import _cb_confirm
from adso.handlers.jobs import _reclassify_inbox_impl
from adso.vault_writer import VAULT_DIRS, create_note, read_note


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for d in VAULT_DIRS:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


class _FakeQuery:
    """Stub mínimo de CallbackQuery: registra los textos editados."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def edit_message_text(self, text: str, **kwargs: Any) -> None:
        self.texts.append(text)


def _make_context(vault: Path) -> SimpleNamespace:
    settings = SimpleNamespace(
        vault_path=vault,
        telegram_allowed_user_id=12345,
        tasks=SimpleNamespace(debug=False),
        llm=SimpleNamespace(disambiguation_threshold=0.6),
    )
    return SimpleNamespace(
        user_data={},
        bot_data={"settings": settings},
        bot=SimpleNamespace(send_message=AsyncMock()),
        application=SimpleNamespace(user_data={}),
    )


def _pending_note() -> dict:
    return {
        "mode": "capture",
        "payload": {
            "frontmatter": {
                "title": "Transcripción irrecuperable",
                "type": "reference",
                "status": "active",
                "project": "tesis",
                "tags": [],
            },
            "body": "Texto que solo existe en memoria.",
            "suggested_links": [],
        },
    }


class TestConfirmKeepsPendingOnFailure:
    """A2 — un fallo de I/O no debe perder la captura."""

    @pytest.mark.asyncio
    async def test_pending_note_survives_create_note_error(self, vault: Path) -> None:
        context = _make_context(vault)
        context.user_data["pending_note"] = _pending_note()
        query = _FakeQuery()

        with patch(
            "adso.handlers.capture.create_note",
            side_effect=OSError("No space left on device"),
        ):
            with pytest.raises(OSError):
                await _cb_confirm(query, context, vault)

        assert "pending_note" in context.user_data

    @pytest.mark.asyncio
    async def test_second_confirm_writes_the_note(self, vault: Path) -> None:
        context = _make_context(vault)
        context.user_data["pending_note"] = _pending_note()
        query = _FakeQuery()

        with patch(
            "adso.handlers.capture.create_note",
            side_effect=OSError("No space left on device"),
        ):
            with pytest.raises(OSError):
                await _cb_confirm(query, context, vault)

        # Segundo tap de [Confirmar]: el estado sigue ahí y la nota se escribe.
        await _cb_confirm(query, context, vault)

        assert "pending_note" not in context.user_data
        notes = list((vault / "01-Projects" / "tesis").rglob("*.md"))
        assert len(notes) == 1
        note = await read_note(notes[0])
        assert note.body.strip() == "Texto que solo existe en memoria."

    @pytest.mark.asyncio
    async def test_clasificar_inbox_path_survives_failure(self, vault: Path) -> None:
        context = _make_context(vault)
        context.user_data["pending_note"] = _pending_note()
        context.user_data["clasificar_inbox_path"] = str(vault / "00-Inbox" / "x.md")
        query = _FakeQuery()

        with patch("adso.handlers.capture.create_note", side_effect=OSError("boom")):
            with pytest.raises(OSError):
                await _cb_confirm(query, context, vault)

        assert context.user_data.get("clasificar_inbox_path")


class TestReclassifyKeepsInboxNoteOnFailure:
    """A3 — crear primero, borrar después."""

    @pytest.mark.asyncio
    async def test_inbox_note_survives_create_note_error(self, vault: Path) -> None:
        inbox_path = await create_note(
            {
                "title": "Sin clasificar",
                "type": "idea",
                "status": "pending-classification",
                "project": "tesis",
                "tags": [],
                "media_type": "text",
            },
            "Contenido original del usuario.",
            vault,
            # forzar destino Inbox
            dry_run=False,
        )
        # create_note enruta por project; moverla al Inbox a mano para el escenario real.
        real_inbox = vault / "00-Inbox" / inbox_path.name
        inbox_path.rename(real_inbox)

        context = _make_context(vault)
        note_ref = SimpleNamespace(path=real_inbox)

        classify_result = {
            "mode": "capture",
            "payload": {
                "frontmatter": {
                    "title": "Clasificada",
                    "type": "idea",
                    "status": "raw",
                    "tags": [],
                },
                "body": "Contenido original del usuario.",
            },
        }

        with (
            patch("adso.handlers.jobs.find_by_property", AsyncMock(return_value=[note_ref])),
            patch("adso.handlers.jobs._get_existing_items", AsyncMock(return_value=([], []))),
            patch("adso.handlers.jobs._get_existing_tags", AsyncMock(return_value=[])),
            patch("adso.handlers.jobs.classify", AsyncMock(return_value=classify_result)),
            patch(
                "adso.handlers.jobs.create_note",
                AsyncMock(side_effect=OSError("No space left on device")),
            ) as mock_create,
            patch("adso.handlers.jobs.delete_note", AsyncMock()) as mock_delete,
        ):
            await _reclassify_inbox_impl(context)

        # El fallo debe ocurrir en create_note (no antes) y la nota debe seguir viva.
        assert mock_create.await_count == 1
        assert mock_delete.await_count == 0
        assert real_inbox.exists()

    @pytest.mark.asyncio
    async def test_delete_runs_after_successful_create(self, vault: Path) -> None:
        inbox_path = vault / "00-Inbox" / "2026-07-31-sin-clasificar.md"
        inbox_path.write_text(
            "---\ntitle: Sin clasificar\ntype: idea\n"
            "status: pending-classification\nproject: tesis\n---\n\nContenido.\n",
            encoding="utf-8",
        )

        context = _make_context(vault)
        note_ref = SimpleNamespace(path=inbox_path)

        classify_result = {
            "mode": "capture",
            "payload": {
                "frontmatter": {
                    "title": "Clasificada",
                    "type": "idea",
                    "status": "raw",
                    "tags": [],
                },
                "body": "Contenido.",
            },
        }

        with (
            patch("adso.handlers.jobs.find_by_property", AsyncMock(return_value=[note_ref])),
            patch("adso.handlers.jobs._get_existing_items", AsyncMock(return_value=([], []))),
            patch("adso.handlers.jobs._get_existing_tags", AsyncMock(return_value=[])),
            patch("adso.handlers.jobs.classify", AsyncMock(return_value=classify_result)),
        ):
            await _reclassify_inbox_impl(context)

        assert not inbox_path.exists()
        created = list((vault / "01-Projects" / "tesis").rglob("*.md"))
        assert len(created) == 1
