"""Tests E2E: flujo de confirmación con inline keyboards."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from adso.handlers.input import handle_text
from adso.handlers.callbacks import handle_callback
from adso.constants import (
    CB_CONFIRM,
    CB_CANCEL,
    CB_CORRECT,
    CB_DEST_INBOX,
    CB_DISAMBIG_CAPTURE,
    CB_MANAGE_CONFIRM,
    CB_MANAGE_CANCEL,
    CB_INTENT_SAVE,
    CB_INTENT_CREATE_PROJECT,
)


def _setup_pending_note(context, title="Test", note_type="reference", project="tesis"):
    """Simula una nota pendiente de confirmación en el contexto."""
    context.user_data["pending_note"] = {
        "mode": "capture",
        "confidence": 0.9,
        "payload": {
            "frontmatter": {
                "title": title,
                "type": note_type,
                "tags": ["test"],
                "status": "active",
                "project": project,
                "section": None,
                "area": None,
                "priority": None,
                "source": "telegram",
                "media_type": "text",
                "date_created": "2025-01-15T00:00:00",
                "date_modified": "2025-01-15T00:00:00",
            },
            "body": "Cuerpo de la nota.",
            "summary": None,
        },
    }


class TestConfirmation:

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_confirm_writes_note(
        self, make_callback_query, mock_context
    ) -> None:
        """Confirmar → nota escrita al vault."""
        vault_path = mock_context.bot_data["settings"].vault_path
        _setup_pending_note(mock_context)

        update = make_callback_query(data=CB_CONFIRM)
        await handle_callback(update, mock_context)

        md_files = list(vault_path.rglob("*.md"))
        assert len(md_files) >= 1
        assert "pending_note" not in mock_context.user_data

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_cancel_does_not_write(
        self, make_callback_query, mock_context
    ) -> None:
        """Cancelar → nota NO escrita."""
        vault_path = mock_context.bot_data["settings"].vault_path
        _setup_pending_note(mock_context)

        update = make_callback_query(data=CB_CANCEL)
        await handle_callback(update, mock_context)

        md_files = list(vault_path.rglob("*.md"))
        assert len(md_files) == 0
        assert "pending_note" not in mock_context.user_data

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_correct_shows_destination_selector(
        self, make_callback_query, mock_context
    ) -> None:
        """Corregir → muestra selector de destino."""
        _setup_pending_note(mock_context)

        update = make_callback_query(data=CB_CORRECT)
        await handle_callback(update, mock_context)

        # Verificar que se editó el reply markup
        update.callback_query.edit_message_reply_markup.assert_called_once()

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_dest_inbox_clears_destination(
        self, make_callback_query, mock_context
    ) -> None:
        """Elegir Inbox → project/area se limpian, type se preserva."""
        _setup_pending_note(mock_context)

        update = make_callback_query(data=CB_DEST_INBOX)
        await handle_callback(update, mock_context)

        fm = mock_context.user_data["pending_note"]["payload"]["frontmatter"]
        assert fm["type"] == "reference"  # tipo original preservado
        assert not fm.get("project")
        assert not fm.get("area")

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_dest_area_changes_destination(
        self, make_callback_query, mock_context
    ) -> None:
        """Elegir área → destino cambiado."""
        _setup_pending_note(mock_context)

        update = make_callback_query(data="dest:area:investigacion")
        await handle_callback(update, mock_context)

        fm = mock_context.user_data["pending_note"]["payload"]["frontmatter"]
        assert fm["area"] == "investigacion"
        assert fm["project"] is None


class TestTextCorrection:

    @pytest.mark.asyncio
    @patch("adso.handlers.capture.classify")
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_title_correction(
        self, mock_classify, make_update, mock_context
    ) -> None:
        """Con preview pendiente, 'título X' actualiza el título."""
        _setup_pending_note(mock_context)

        update = make_update(text="título Nuevo título corregido")
        await handle_text(update, mock_context)

        fm = mock_context.user_data["pending_note"]["payload"]["frontmatter"]
        assert fm["title"] == "Nuevo título corregido"

    @pytest.mark.asyncio
    @patch("adso.handlers.capture.classify")
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_priority_correction(
        self, mock_classify, make_update, mock_context
    ) -> None:
        """Con preview pendiente, 'prioridad alta' cambia priority."""
        _setup_pending_note(mock_context)

        update = make_update(text="prioridad alta")
        await handle_text(update, mock_context)

        fm = mock_context.user_data["pending_note"]["payload"]["frontmatter"]
        assert fm["priority"] == "high"

    @pytest.mark.asyncio
    @patch("adso.handlers.capture.classify")
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_add_tag(
        self, mock_classify, make_update, mock_context
    ) -> None:
        """Con preview pendiente, 'tag X' agrega tag."""
        _setup_pending_note(mock_context)

        update = make_update(text="agregar tag deep-learning")
        await handle_text(update, mock_context)

        fm = mock_context.user_data["pending_note"]["payload"]["frontmatter"]
        assert "deep-learning" in fm["tags"]


class TestManageFlow:

    @pytest.mark.asyncio
    @patch("adso.handlers.capture.classify")
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_create_project_flow(
        self, mock_classify, make_update, make_callback_query, mock_context
    ) -> None:
        """Crear proyecto → confirmar → carpeta + _index.md."""
        vault_path = mock_context.bot_data["settings"].vault_path

        mock_classify.return_value = {
            "mode": "manage",
            "confidence": 0.95,
            "needs_disambiguation": False,
            "payload": {
                "operation": "create_project",
                "params": {
                    "name": "nuevo-proyecto",
                    "description": "Un proyecto nuevo de prueba.",
                },
            },
        }

        # Paso 1: enviar texto → keyword 'proyecto' detectado → intent keyboard
        update = make_update(text="crear proyecto nuevo-proyecto")
        await handle_text(update, mock_context)
        assert "pending_raw_content" in mock_context.user_data

        # Paso 2: click "Crear proyecto" → LLM infiere nombre → pending_operation
        cb_create = make_callback_query(data=CB_INTENT_CREATE_PROJECT)
        await handle_callback(cb_create, mock_context)
        assert "pending_operation" in mock_context.user_data

        # Paso 3: confirmar
        cb_update = make_callback_query(data=CB_MANAGE_CONFIRM)
        await handle_callback(cb_update, mock_context)

        # Verificar proyecto creado
        project_dir = vault_path / "01-Projects" / "nuevo-proyecto"
        assert project_dir.exists()
        index_path = project_dir / "_index.md"
        assert index_path.exists()

    @pytest.mark.asyncio
    @patch("adso.handlers.capture.classify")
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_manage_cancel(
        self, mock_classify, make_update, make_callback_query, mock_context
    ) -> None:
        """Cancelar gestión → no se ejecuta."""
        mock_classify.return_value = {
            "mode": "manage",
            "confidence": 0.95,
            "needs_disambiguation": False,
            "payload": {
                "operation": "create_project",
                "params": {"name": "cancelado", "description": "No se crea."},
            },
        }

        update = make_update(text="crear proyecto cancelado")
        await handle_text(update, mock_context)

        cb_update = make_callback_query(data=CB_MANAGE_CANCEL)
        await handle_callback(cb_update, mock_context)

        vault_path = mock_context.bot_data["settings"].vault_path
        assert not (vault_path / "01-Projects" / "cancelado").exists()
