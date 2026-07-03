"""Tests para adso.handlers.jobs — cobertura de _PENDING_FLOW_KEYS."""

from __future__ import annotations

from adso.handlers.jobs import _PENDING_FLOW_KEYS


class TestPendingFlowKeys:
    """El cron de reclasificación debe saltarse la pasada ante cualquier flujo
    interactivo en curso — la lista debe cubrir todos los flujos con teclado."""

    def test_covers_all_interactive_flow_keys(self) -> None:
        # Keys que muestran teclado/esperan input (alineado con _has_pending_keyboard
        # y _is_awaiting_text_input en bot_utils). Antes faltaban las últimas cuatro.
        expected = {
            "pending_note",
            "pending_operation",
            "pending_raw_content",
            "pending_extraction",
            "pending_transcript",
            "pending_description",
            "manage_missing_fields",
            "pending_fallback_pdf",
            "pending_read_status",
            "pending_arxiv",
            "pending_report",
        }
        assert expected <= _PENDING_FLOW_KEYS
