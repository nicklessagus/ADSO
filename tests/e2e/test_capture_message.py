"""Tests E2E: flujo completo de captura de mensaje."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from adso.bot import handle_text, handle_callback, CB_CONFIRM
from adso.vault_writer import read_note


class TestCaptureMessage:

    @pytest.mark.asyncio
    @patch("adso.bot.classify")
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_text_to_preview_to_confirm_to_vault(
        self, mock_classify, make_update, make_callback_query, mock_context
    ) -> None:
        """Texto → preview → confirm → nota en vault."""
        vault_path = mock_context.bot_data["settings"].vault_path

        # Mock classify retorna una nota
        mock_classify.return_value = {
            "mode": "capture",
            "confidence": 0.95,
            "needs_disambiguation": False,
            "payload": {
                "frontmatter": {
                    "title": "Resultado del experimento",
                    "type": "note",
                    "tags": ["ml"],
                    "status": "active",
                    "project": "tesis",
                    "section": None,
                    "area": None,
                    "priority": None,
                },
                "body": "Accuracy 0.87 en el baseline.",
                "suggested_links": [],
                "summary": None,
            },
        }

        # Paso 1: enviar texto
        update = make_update(text="Hoy el baseline dio accuracy 0.87")
        await handle_text(update, mock_context)

        # Verificar que se mostró preview
        update.message.reply_text.assert_called_once()
        call_kwargs = update.message.reply_text.call_args
        assert "Preview de nota" in call_kwargs[0][0] or "Preview de nota" in str(call_kwargs)

        # Verificar que hay nota pendiente
        assert "pending_note" in mock_context.user_data

        # Paso 2: confirmar
        cb_update = make_callback_query(data=CB_CONFIRM)
        await handle_callback(cb_update, mock_context)

        # Verificar que la nota se escribió al vault
        md_files = list(vault_path.rglob("*.md"))
        assert len(md_files) >= 1

        # Leer la nota y verificar contenido
        note_path = [f for f in md_files if f.stem != "_index"][0]
        note = await read_note(note_path)
        assert note.frontmatter["title"] == "Resultado del experimento"
        assert note.frontmatter["type"] == "note"
        assert "0.87" in note.body

    @pytest.mark.asyncio
    @patch("adso.bot.classify")
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_degraded_mode_saves_to_inbox(
        self, mock_classify, make_update, mock_context
    ) -> None:
        """LLM falla → nota en Inbox con pending-classification."""
        vault_path = mock_context.bot_data["settings"].vault_path

        mock_classify.return_value = {
            "mode": "degraded",
            "confidence": 0.0,
            "needs_disambiguation": False,
            "payload": {
                "frontmatter": {
                    "title": "Sin clasificar",
                    "type": "inbox",
                    "tags": [],
                    "status": "pending-classification",
                },
                "body": "Contenido que no se pudo clasificar.",
                "suggested_links": [],
                "summary": None,
            },
        }

        update = make_update(text="algo random")
        await handle_text(update, mock_context)

        # Verificar que se guardó en inbox
        inbox_files = list((vault_path / "00-Inbox").rglob("*.md"))
        assert len(inbox_files) >= 1

        note = await read_note(inbox_files[0])
        assert note.frontmatter["status"] == "pending-classification"
        assert "Contenido que no se pudo clasificar" in note.body

    @pytest.mark.asyncio
    @patch("adso.bot.classify")
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_disambiguation_shows_buttons(
        self, mock_classify, make_update, mock_context
    ) -> None:
        """Confianza baja → desambiguación con botones."""
        mock_classify.return_value = {
            "mode": "capture",
            "confidence": 0.4,
            "needs_disambiguation": True,
            "payload": {
                "frontmatter": {
                    "title": "Ambiguo",
                    "type": "note",
                    "tags": [],
                    "status": "active",
                    "project": None,
                    "area": None,
                },
                "body": "contenido ambiguo",
                "suggested_links": [],
                "summary": None,
            },
        }

        update = make_update(text="transformers astronomía")
        await handle_text(update, mock_context)

        # Verificar que se mostró desambiguación
        call_kwargs = update.message.reply_text.call_args
        assert "seguro" in str(call_kwargs[0][0]).lower() or "Guardar" in str(call_kwargs)
