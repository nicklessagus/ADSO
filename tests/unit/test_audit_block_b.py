"""Tests del bloque B de la auditoría 2026-07-31.

B1 — el redirect query/edit → capture debe re-validar el payload (o degradar).
B2 — `manage_missing_fields` no debe quedar residual al confirmar/cancelar.
B3 — `handle_status` debe tener el decorador `@authorized`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from adso.handlers.capture import _classify_and_preview, _redirect_unimplemented_mode
from adso.handlers.commands import handle_status
from adso.handlers.manage import _cb_manage_confirm, pop_manage_state


# ---------------------------------------------------------------------------
# B1
# ---------------------------------------------------------------------------


def _dirty_query_result() -> dict:
    """Respuesta con mode=query y un frontmatter sin sanitizar (típico de Groq)."""
    return {
        "mode": "query",
        "confidence": 0.9,
        "payload": {
            "frontmatter": {
                "title": "Tarea: Revisar el paper",
                "type": "task",
                "status": "pending",
                "tags": ["Machine Learning", "lunes", "task"],
                "due_date": "el viernes",
                "handler": "boom",
                "year": "no-un-año",
            },
            "body": "cuerpo",
        },
    }


class TestRedirectUnimplementedMode:
    """B1 — el payload de query/edit pasa por _validate_capture_payload."""

    def test_query_payload_is_sanitized(self) -> None:
        result = _dirty_query_result()
        mode = _redirect_unimplemented_mode(result, "revisar el paper")

        assert mode == "capture"
        assert result["mode"] == "capture"
        fm = result["payload"]["frontmatter"]
        assert fm["title"] == "Revisar el paper"          # prefijo label limpiado
        assert fm["tags"] == ["machine-learning"]          # kebab, sin temporal ni type
        assert fm["due_date"] is None                      # no-ISO descartada
        assert fm["year"] is None
        assert "handler" not in fm                         # clave fuera del schema

    def test_null_frontmatter_falls_back_to_degraded(self) -> None:
        # `frontmatter: null` es legal en el schema de Gemini (el caso que
        # crasheaba el flujo arXiv con TypeError).
        result = {"mode": "query", "payload": {"frontmatter": None}}
        mode = _redirect_unimplemented_mode(result, "contenido original")

        assert mode == "degraded"
        assert result["mode"] == "degraded"
        fm = result["payload"]["frontmatter"]
        assert fm["type"] == "idea"
        assert fm["status"] == "pending-classification"
        assert "contenido original" in result["payload"]["body"]

    def test_invalid_type_falls_back_to_degraded(self) -> None:
        result = {
            "mode": "edit",
            "payload": {"frontmatter": {"title": "x", "type": "paper"}},
        }
        assert _redirect_unimplemented_mode(result, "texto") == "degraded"

    def test_missing_payload_falls_back_to_degraded(self) -> None:
        result = {"mode": "query"}
        assert _redirect_unimplemented_mode(result, "texto") == "degraded"

    def test_other_modes_untouched(self) -> None:
        result = {"mode": "capture", "payload": {"frontmatter": {"handler": "x"}}}
        assert _redirect_unimplemented_mode(result, "texto") == "capture"
        # No re-valida lo que validate_llm_response ya sanitizó
        assert result["payload"]["frontmatter"] == {"handler": "x"}


class TestClassifyAndPreviewRedirect:
    """B1 — end-to-end del redirect dentro de _classify_and_preview."""

    @pytest.mark.asyncio
    @patch("adso.handlers.capture.classify")
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_query_mode_reaches_preview_sanitized(
        self, mock_classify: Any, make_update: Any, mock_context: Any
    ) -> None:
        mock_classify.return_value = _dirty_query_result()

        update = make_update(text="revisar el paper")
        await _classify_and_preview(
            update, mock_context, text="revisar el paper", media_type="text",
        )

        fm = mock_context.user_data["pending_note"]["payload"]["frontmatter"]
        assert "handler" not in fm
        assert fm["title"] == "Revisar el paper"
        assert fm["tags"] == ["machine-learning"]

    @pytest.mark.asyncio
    @patch("adso.handlers.capture.classify")
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_query_mode_with_null_frontmatter_degrades(
        self, mock_classify: Any, make_update: Any, mock_context: Any
    ) -> None:
        mock_classify.return_value = {
            "mode": "query",
            "confidence": 0.9,
            "payload": {"frontmatter": None, "body": None},
        }

        update = make_update(text="qué tengo sobre transformers")
        await _classify_and_preview(
            update, mock_context, text="qué tengo sobre transformers", media_type="text",
        )

        pending = mock_context.user_data["pending_note"]
        assert pending["mode"] == "degraded"
        assert pending["payload"]["frontmatter"]["status"] == "pending-classification"
        sent = str(update.message.reply_text.call_args)
        assert "Inbox" in sent


# ---------------------------------------------------------------------------
# B2
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def edit_message_text(self, text: str, **kwargs: Any) -> None:
        self.texts.append(text)


class TestManageStateCleanup:
    """B2 — dejar manage_missing_fields colgada frena reclassify_inbox para siempre."""

    def test_pop_manage_state_clears_both_keys(self) -> None:
        context = SimpleNamespace(
            user_data={"pending_operation": {"a": 1}, "manage_missing_fields": ["correction"]}
        )
        assert pop_manage_state(context) == {"a": 1}
        assert context.user_data == {}

    @pytest.mark.asyncio
    async def test_confirm_clears_missing_fields(self, tmp_path: Any) -> None:
        from adso.vault_writer import VAULT_DIRS

        for d in VAULT_DIRS:
            (tmp_path / d).mkdir(parents=True, exist_ok=True)

        context = SimpleNamespace(
            user_data={
                "pending_operation": {
                    "mode": "manage",
                    "payload": {
                        "operation": "create_project",
                        "params": {"name": "tesis", "description": "doctorado"},
                    },
                },
                "manage_missing_fields": ["correction"],
            },
            bot_data={},
        )
        await _cb_manage_confirm(_FakeQuery(), context, tmp_path)

        assert "pending_operation" not in context.user_data
        assert "manage_missing_fields" not in context.user_data

    @pytest.mark.asyncio
    async def test_confirm_with_invalid_name_clears_missing_fields(
        self, tmp_path: Any
    ) -> None:
        # Camino de salida temprana: nombre inválido → no se crea nada, pero el
        # estado tampoco puede quedar colgado.
        context = SimpleNamespace(
            user_data={
                "pending_operation": {
                    "mode": "manage",
                    "payload": {
                        "operation": "create_project",
                        "params": {"name": "../../etc", "description": "x"},
                    },
                },
                "manage_missing_fields": ["correction"],
            },
            bot_data={},
        )
        await _cb_manage_confirm(_FakeQuery(), context, tmp_path)

        assert "manage_missing_fields" not in context.user_data

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_cancel_clears_missing_fields(
        self, make_callback_query: Any, mock_context: Any
    ) -> None:
        from adso.constants import CB_MANAGE_CANCEL
        from adso.handlers.callbacks import handle_callback

        mock_context.user_data["pending_operation"] = {"something": True}
        mock_context.user_data["manage_missing_fields"] = ["correction"]

        await handle_callback(make_callback_query(data=CB_MANAGE_CANCEL), mock_context)

        assert "pending_operation" not in mock_context.user_data
        assert "manage_missing_fields" not in mock_context.user_data


# ---------------------------------------------------------------------------
# B3
# ---------------------------------------------------------------------------


class TestStatusAuthorized:
    """B3 — segunda barrera de auth: todo handler registrado lleva @authorized."""

    def test_handle_status_is_decorated(self) -> None:
        assert getattr(handle_status, "__wrapped__", None) is not None

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_unauthorized_user_is_rejected(
        self, make_update: Any, mock_context: Any
    ) -> None:
        update = make_update(user_id=99999)
        update.message.reply_text = AsyncMock()

        await handle_status(update, mock_context)

        # El handler no debe llegar a responder con el estado del sistema
        assert all(
            "ADSO" not in str(call) for call in update.message.reply_text.call_args_list
        )
