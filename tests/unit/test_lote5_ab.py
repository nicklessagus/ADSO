"""Lote 5 — groups A (capture) and B (input, callbacks, extractor).

Spec: audit 2026-09-22, lote 5 SPEC.md (groups A and B). Each requirement test
is born `xfail(strict=True)`; counter-cases carry no mark and pass today.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from telegram.error import BadRequest, NetworkError

from adso.constants import (
    CB_CANCEL,
    CB_CHOOSE_AREA,
    CB_CONFIRM,
    CB_DESCRIBE,
    CB_DEST_INBOX,
    CB_DOC_CREATE_ANYWAY,
    CB_OCR,
    CB_QUERY_REPORT,
    CB_READ_STATUS_READ,
)

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
WED = datetime(2026, 9, 23, 10, 0, tzinfo=TZ)  # Wednesday
STALE_TEXT = "Este preview ya no está vigente. Usar los botones del último mensaje."
TOO_OLD = "Query is too old and response timeout expired or query id is invalid"


def _xfail(item_id: str, why: str):
    return pytest.mark.xfail(strict=True, reason=f"LOTE5 {item_id}: {why}")


def _parse(text: str):
    from adso.handlers.capture import _parse_date_from_text

    return _parse_date_from_text(text, now=WED)


# ===========================================================================
# A1 — "a la mañana" is a time of day, not "tomorrow"
# ===========================================================================


class TestA1MorningIsTimeOfDay:
    @_xfail("A1", "'a la mañana' matched as 'mañana' (tomorrow)")
    def test_viernes_a_la_manana_is_friday(self) -> None:
        assert _parse("reunión el viernes a la mañana") == "2026-09-25"

    @_xfail("A1", "'a la mañana' matched as 'mañana' (tomorrow)")
    def test_hoy_a_la_manana_is_today(self) -> None:
        assert _parse("llamar hoy a la mañana") == "2026-09-23"

    @_xfail("A1", "'por la mañana' matched as 'mañana' (tomorrow)")
    def test_por_la_manana_alone_gives_no_date(self) -> None:
        assert _parse("por la mañana revisar mails") is None

    @_xfail("A1", "'de la mañana' matched as 'mañana' (tomorrow)")
    def test_de_la_manana_hour_keeps_the_weekday(self) -> None:
        assert _parse("el viernes a las 9 de la mañana") == "2026-09-25T09:00:00"

    # counter-cases
    def test_manana_alone_is_tomorrow(self) -> None:
        assert _parse("mañana") == "2026-09-24"

    def test_pasado_manana(self) -> None:
        assert _parse("pasado mañana") == "2026-09-25"

    def test_manana_a_la_manana_is_tomorrow(self) -> None:
        assert _parse("mañana a la mañana") == "2026-09-24"


# ===========================================================================
# A7 — "de la tarde" / "de la noche" are PM
# ===========================================================================


class TestA7PmMarkers:
    @_xfail("A7", "'de la tarde' ignored, hour read as AM")
    def test_cinco_de_la_tarde_is_17h(self) -> None:
        assert _parse("el viernes a las 5 de la tarde") == "2026-09-25T17:00:00"

    @_xfail("A7", "'de la noche' ignored, hour read as AM")
    def test_nueve_de_la_noche_is_21h(self) -> None:
        assert _parse("el viernes a las 9 de la noche") == "2026-09-25T21:00:00"

    # counter-cases
    def test_24h_hour_unchanged(self) -> None:
        assert _parse("el viernes a las 17") == "2026-09-25T17:00:00"

    def test_doce_de_la_tarde_is_noon(self) -> None:
        assert _parse("el viernes a las 12 de la tarde") == "2026-09-25T12:00:00"


# ===========================================================================
# A2 — arXiv forced reference resyncs status and drops task fields
# ===========================================================================


def _llm_result(fm: dict, summary: str = "resumen") -> dict:
    return {
        "mode": "capture",
        "confidence": 0.9,
        "needs_disambiguation": False,
        "payload": {"frontmatter": fm, "body": "", "summary": summary},
    }


_ARXIV_META = {
    "title": "Paper",
    "authors": ["A"],
    "year": 2024,
    "doi": None,
    "keywords": ["cs.LG"],
    "abstract": "abs",
}


async def _run_arxiv(mock_context, make_update, fm: dict) -> dict:
    from adso.handlers import capture

    with patch.object(capture, "classify", AsyncMock(return_value=_llm_result(fm))):
        await capture._classify_and_preview_arxiv(
            make_update("leer para el viernes"),
            mock_context,
            dict(_ARXIV_META),
            "https://arxiv.org/abs/2301.12345",
            user_context="leer para el viernes",
        )
    return mock_context.user_data["pending_note"]["payload"]["frontmatter"]


class TestA2ArxivResync:
    @_xfail("A2", "arXiv forces reference but keeps task status/due_date/scheduled")
    async def test_llm_task_becomes_clean_reference(self, mock_context, make_update) -> None:
        fm = await _run_arxiv(
            mock_context,
            make_update,
            {
                "title": "x",
                "type": "task",
                "status": "pending",
                "priority": "high",
                "due_date": "2026-09-25",
                "scheduled": "2026-09-25T10:00:00",
                "tags": [],
            },
        )
        assert fm["type"] == "reference"
        assert fm["status"] == "active"
        assert "due_date" not in fm
        assert "scheduled" not in fm

    # counter-case
    async def test_llm_reference_unchanged(self, mock_context, make_update) -> None:
        fm = await _run_arxiv(
            mock_context,
            make_update,
            {"title": "x", "type": "reference", "status": "active", "tags": []},
        )
        assert fm["type"] == "reference"
        assert fm["status"] == "active"
        assert fm["read_status"] == "unread"


# ===========================================================================
# A3 — degraded mode keeps the user's [Tarea]/[Nota] choice
# ===========================================================================


async def _run_degraded(mock_context, make_update, text: str, **kwargs) -> dict:
    from adso.handlers import capture
    from adso.llm_client import make_degraded_result

    with patch.object(
        capture, "classify", AsyncMock(return_value=make_degraded_result(text))
    ):
        await capture._classify_and_preview(
            make_update(text), mock_context, text, media_type="text", **kwargs
        )
    return mock_context.user_data["pending_note"]["payload"]["frontmatter"]


class TestA3DegradedKeepsChoice:
    @_xfail("A3", "degraded branch returns before forced_type is applied")
    async def test_forced_task_survives_degraded(self, mock_context, make_update) -> None:
        fm = await _run_degraded(
            mock_context, make_update, "pagar la luz el viernes", forced_type="task"
        )
        assert fm["type"] == "task"
        assert fm["status"] == "pending-classification"
        assert not fm.get("project")
        assert not fm.get("area")

    @_xfail("A3", "degraded branch never runs the local date parser")
    async def test_forced_task_degraded_gets_local_due_date(
        self, mock_context, make_update
    ) -> None:
        fm = await _run_degraded(
            mock_context, make_update, "pagar la luz mañana", forced_type="task"
        )
        tomorrow = (datetime.now(TZ) + timedelta(days=1)).date().isoformat()
        assert str(fm.get("due_date", "")).startswith(tomorrow)

    # counter-cases
    async def test_prevent_task_degraded_stays_idea(self, mock_context, make_update) -> None:
        fm = await _run_degraded(
            mock_context, make_update, "una idea suelta", prevent_task=True
        )
        assert fm["type"] == "idea"
        assert fm["status"] == "pending-classification"

    async def test_degraded_without_choice_is_idea(self, mock_context, make_update) -> None:
        fm = await _run_degraded(mock_context, make_update, "algo sin tipo")
        assert fm["type"] == "idea"
        assert fm["status"] == "pending-classification"


# ===========================================================================
# A4 — an explicit prefix in a TASK correction never becomes the title
# ===========================================================================


def _task_pending() -> dict:
    return {
        "payload": {
            "frontmatter": {
                "title": "Pagar la luz",
                "type": "task",
                "status": "pending",
                "priority": "medium",
                "tags": ["hogar"],
            },
            "body": "b",
            "suggested_links": [],
        },
        "awaiting_correction": True,
        "msg_id": 10,
    }


async def _correct(mock_context, make_update, pending: dict, text: str) -> None:
    from adso.handlers import capture

    mock_context.user_data["pending_note"] = pending
    mock_context.bot = MagicMock(
        edit_message_text=AsyncMock(), delete_message=AsyncMock()
    )
    upd = make_update(text)
    upd.message.delete = AsyncMock()
    upd.message.chat_id = 1
    await capture._handle_text_correction(
        upd, mock_context, text, pending, locked_msg_id=10
    )


class TestA4PrefixNeverBecomesTitle:
    @_xfail("A4", "task branch: unapplied prefix falls back to title")
    @pytest.mark.parametrize(
        "correction", ["fecha el finde", "tag hogar", "prioridad urgente"]
    )
    async def test_unapplied_prefix_keeps_title(
        self, mock_context, make_update, correction
    ) -> None:
        pending = _task_pending()
        await _correct(mock_context, make_update, pending, correction)
        assert pending["payload"]["frontmatter"]["title"] == "Pagar la luz"
        assert "pending_note" in mock_context.user_data

    # counter-cases
    async def test_prioridad_alta_applies(self, mock_context, make_update) -> None:
        pending = _task_pending()
        await _correct(mock_context, make_update, pending, "prioridad alta")
        fm = pending["payload"]["frontmatter"]
        assert fm["priority"] == "high"
        assert fm["title"] == "Pagar la luz"

    async def test_short_text_without_prefix_becomes_title(
        self, mock_context, make_update
    ) -> None:
        pending = _task_pending()
        await _correct(mock_context, make_update, pending, "Comprar pan")
        assert pending["payload"]["frontmatter"]["title"] == "Comprar pan"

    async def test_note_branch_unchanged(self, mock_context, make_update) -> None:
        pending = _task_pending()
        pending["payload"]["frontmatter"].update(type="reference", status="active")
        await _correct(mock_context, make_update, pending, "prioridad urgente")
        assert pending["payload"]["frontmatter"]["title"] == "Pagar la luz"


# ===========================================================================
# A5 — stale [Cancelar]/[Corregir]/[Reubicar] don't touch the current capture
# ===========================================================================


def _current_pending(tmp_path: Path, msg_id: int | None = 100) -> tuple[dict, Path]:
    tmp = tmp_path / "ocr.jpg"
    tmp.write_bytes(b"\xff\xd8")
    pending = {
        "payload": {
            "frontmatter": {
                "title": "B",
                "type": "reference",
                "status": "active",
                "project": "Tesis",
            },
            "body": "ocr text",
            "suggested_links": [],
        },
        "_resource_file": {"temp_path": str(tmp), "filename": "ocr.jpg"},
    }
    if msg_id is not None:
        pending["msg_id"] = msg_id
    return pending, tmp


def _edited_text(query) -> str:
    call = query.edit_message_text.await_args
    return call.args[0] if call.args else call.kwargs.get("text", "")


class TestA5StaleButtons:
    @_xfail("A5", "stale [Cancelar] clears the current pending_note")
    async def test_stale_cancel_keeps_current_capture(
        self, mock_context, make_callback_query, tmp_path
    ) -> None:
        from adso.handlers import capture

        pending, tmp = _current_pending(tmp_path)
        mock_context.user_data["pending_note"] = pending
        upd = make_callback_query(CB_CANCEL)
        assert upd.callback_query.message.message_id != 100
        await capture._cb_cancel(upd.callback_query, mock_context)
        assert mock_context.user_data.get("pending_note") is pending
        assert tmp.exists()
        assert _edited_text(upd.callback_query) == STALE_TEXT

    @_xfail("A5", "stale [Corregir] rebinds msg_id / activates correction")
    async def test_stale_note_correct_does_not_touch_pending(
        self, mock_context, make_callback_query, tmp_path
    ) -> None:
        from adso.handlers import capture

        pending, _ = _current_pending(tmp_path)
        mock_context.user_data["pending_note"] = pending
        upd = make_callback_query("note:correct")
        upd.callback_query.message.text_html = "old preview"
        await capture._cb_note_correct(upd.callback_query, mock_context)
        assert pending["msg_id"] == 100
        assert not pending.get("awaiting_correction")
        assert _edited_text(upd.callback_query) == STALE_TEXT

    @_xfail("A5", "stale [Reubicar] changes the current destination")
    async def test_stale_dest_does_not_touch_pending(
        self, mock_context, make_callback_query, tmp_path
    ) -> None:
        from adso.handlers import capture

        pending, _ = _current_pending(tmp_path)
        mock_context.user_data["pending_note"] = pending
        upd = make_callback_query(CB_DEST_INBOX)
        await capture._cb_dest(upd.callback_query, mock_context, dest_type="inbox")
        assert pending["payload"]["frontmatter"]["project"] == "Tesis"
        assert pending["msg_id"] == 100
        assert _edited_text(upd.callback_query) == STALE_TEXT

    # counter-cases
    async def test_cancel_from_current_preview_clears(
        self, mock_context, make_callback_query, tmp_path
    ) -> None:
        from adso.handlers import capture

        upd = make_callback_query(CB_CANCEL)
        current_id = upd.callback_query.message.message_id
        pending, tmp = _current_pending(tmp_path, msg_id=current_id)
        mock_context.user_data["pending_note"] = pending
        await capture._cb_cancel(upd.callback_query, mock_context)
        assert "pending_note" not in mock_context.user_data
        assert not tmp.exists()
        assert _edited_text(upd.callback_query) == "Cancelado."

    async def test_cancel_without_pending_note_still_cancels(
        self, mock_context, make_callback_query
    ) -> None:
        from adso.handlers import capture

        mock_context.user_data["pending_transcript"] = {"text": "t"}
        upd = make_callback_query(CB_CANCEL)
        await capture._cb_cancel(upd.callback_query, mock_context)
        assert "pending_transcript" not in mock_context.user_data
        assert _edited_text(upd.callback_query) == "Cancelado."

    async def test_cancel_with_legacy_pending_without_msg_id_is_accepted(
        self, mock_context, make_callback_query, tmp_path
    ) -> None:
        from adso.handlers import capture

        pending, _ = _current_pending(tmp_path, msg_id=None)
        mock_context.user_data["pending_note"] = pending
        upd = make_callback_query(CB_CANCEL)
        await capture._cb_cancel(upd.callback_query, mock_context)
        assert "pending_note" not in mock_context.user_data
        assert _edited_text(upd.callback_query) == "Cancelado."


# ===========================================================================
# A6 — degraded document keeps the full original text
# ===========================================================================


def _long_doc() -> tuple[str, str]:
    from adso.document_extractor import build_classify_content

    full = "INICIO " + ("x" * 3000) + " MEDIO_UNICO " + ("y" * 3000) + " FIN"
    fragment = build_classify_content(full, {}, is_paper=False)
    assert "MEDIO_UNICO" not in fragment  # precondition: the fragment is cut
    return full, fragment


class TestA6DegradedDocumentFullText:
    @_xfail("A6", "degraded body is built from the classify fragment")
    async def test_degraded_document_body_has_full_text(
        self, mock_context, make_callback_query
    ) -> None:
        from adso.handlers import capture
        from adso.llm_client import make_degraded_result

        full, fragment = _long_doc()
        with patch.object(
            capture, "classify", AsyncMock(return_value=make_degraded_result(fragment))
        ):
            await capture._classify_and_preview(
                make_callback_query("extraction:ok"),
                mock_context,
                fragment,
                media_type="document",
                preserve_body=True,
                original_text=full,
                force_capture=True,
            )
        body = mock_context.user_data["pending_note"]["payload"]["body"]
        assert "MEDIO_UNICO" in body

    # counter-case
    async def test_non_degraded_preserve_body_is_original_text(
        self, mock_context, make_callback_query
    ) -> None:
        from adso.handlers import capture

        full, fragment = _long_doc()
        result = _llm_result({"title": "Doc", "type": "reference", "tags": []})
        with patch.object(capture, "classify", AsyncMock(return_value=result)):
            await capture._classify_and_preview(
                make_callback_query("extraction:ok"),
                mock_context,
                fragment,
                media_type="document",
                preserve_body=True,
                original_text=full,
                force_capture=True,
            )
        assert mock_context.user_data["pending_note"]["payload"]["body"] == full


# ===========================================================================
# B1 — each callback query is answered exactly once, alerts are delivered
# ===========================================================================


def _telegram_like_answer(first_raises: bool = False):
    """answerCallbackQuery semantics: one answer per query; later calls 400."""
    calls: list[tuple] = []

    async def _answer(*args, **kwargs):
        calls.append((args, kwargs))
        if first_raises or len(calls) > 1:
            raise BadRequest(TOO_OLD)
        return True

    return AsyncMock(side_effect=_answer), calls


def _alert_text(call: tuple) -> str:
    args, kwargs = call
    return args[0] if args else kwargs.get("text", "")


def _is_alert(call: tuple) -> bool:
    args, kwargs = call
    return bool(kwargs.get("show_alert") or (len(args) > 1 and args[1]))


async def _dispatch(update, context) -> None:
    from adso.handlers.callbacks import handle_callback

    # A second answer raises BadRequest; PTB's error handler would swallow it.
    # The assertions are on the recorded calls, not on the exception.
    with contextlib.suppress(BadRequest):
        await handle_callback(update, context)


@patch("adso.security.ALLOWED_USER_IDS", {42})
class TestB1SingleAnswer:
    @_xfail("B1", "answer() up front + alert answer = two answers")
    async def test_stale_confirm_does_not_overwrite_saved_message(
        self, make_callback_query, mock_context
    ) -> None:
        upd = make_callback_query(CB_CONFIRM)
        answer, calls = _telegram_like_answer()
        upd.callback_query.answer = answer
        await _dispatch(upd, mock_context)  # pending_note already consumed
        upd.callback_query.edit_message_text.assert_not_called()
        assert len(calls) == 1
        assert _alert_text(calls[0]) == "No hay nota pendiente."
        assert _is_alert(calls[0])

    @_xfail("B1", "OCR alert is a second answer and never shows")
    async def test_ocr_without_pending_image_alerts_once(
        self, make_callback_query, mock_context
    ) -> None:
        upd = make_callback_query(CB_OCR)
        answer, calls = _telegram_like_answer()
        upd.callback_query.answer = answer
        await _dispatch(upd, mock_context)
        assert len(calls) == 1
        assert _alert_text(calls[0]) == "No hay imagen pendiente."
        assert _is_alert(calls[0])

    @_xfail("B1", "_cb_dest alert is a second answer")
    async def test_dest_without_pending_note_alerts_once(
        self, make_callback_query, mock_context
    ) -> None:
        upd = make_callback_query(CB_DEST_INBOX)
        answer, calls = _telegram_like_answer()
        upd.callback_query.answer = answer
        await _dispatch(upd, mock_context)
        assert len(calls) == 1
        assert _alert_text(calls[0]) == "No hay nota pendiente."
        assert _is_alert(calls[0])

    @_xfail("B1", "stale query-report alert is a second answer")
    async def test_expired_query_report_alerts_once(
        self, make_callback_query, mock_context
    ) -> None:
        upd = make_callback_query(CB_QUERY_REPORT)
        answer, calls = _telegram_like_answer()
        upd.callback_query.answer = answer
        await _dispatch(upd, mock_context)
        assert len(calls) == 1
        assert _alert_text(calls[0]) == "La consulta expiró."
        assert _is_alert(calls[0])

    # counter-cases
    async def test_plain_branch_answers_once(self, make_callback_query, mock_context) -> None:
        upd = make_callback_query(CB_CHOOSE_AREA)
        answer, calls = _telegram_like_answer()
        upd.callback_query.answer = answer
        await _dispatch(upd, mock_context)
        assert len(calls) == 1
        upd.callback_query.edit_message_reply_markup.assert_awaited()

    async def test_failed_single_answer_still_processes_tap(
        self, make_callback_query, mock_context
    ) -> None:
        upd = make_callback_query(CB_CHOOSE_AREA)
        answer, calls = _telegram_like_answer(first_raises=True)
        upd.callback_query.answer = answer
        await _dispatch(upd, mock_context)
        upd.callback_query.edit_message_reply_markup.assert_awaited()

    async def test_normal_confirm_answers_once_and_saves(
        self, make_callback_query, mock_context
    ) -> None:
        from adso.handlers import capture

        upd = make_callback_query(CB_CONFIRM)
        answer, calls = _telegram_like_answer()
        upd.callback_query.answer = answer
        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "Nota", "type": "reference", "status": "active"},
                "body": "cuerpo",
                "suggested_links": [],
            },
        }
        vault = mock_context.bot_data["settings"].vault_path
        written = vault / "00-Inbox" / "2026-09-22-nota.md"
        with patch.object(capture, "create_note", AsyncMock(return_value=written)) as cn:
            await _dispatch(upd, mock_context)
        cn.assert_awaited_once()
        assert len(calls) == 1
        assert not _is_alert(calls[0])


# ===========================================================================
# B2 — no state is popped before an unprotected step
# ===========================================================================


def _survives(user_data: dict, key: str, tmp: Path) -> bool:
    state = user_data.get(key)
    return isinstance(state, dict) and state.get("temp_path") == str(tmp) and tmp.exists()


@patch("adso.security.ALLOWED_USER_IDS", {42})
class TestB2StateSurvivesFailures:
    @_xfail("B2", "pending_read_status popped before the unguarded status edit")
    async def test_read_status_edit_failure(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_context.user_data["pending_read_status"] = {
            "temp_path": str(pdf),
            "original_filename": "x.pdf",
            "media_type": "document",
        }
        upd = make_callback_query(CB_READ_STATUS_READ)
        upd.callback_query.edit_message_text = AsyncMock(side_effect=NetworkError("down"))
        with contextlib.suppress(Exception):
            await _dispatch(upd, mock_context)
        assert _survives(mock_context.user_data, "pending_read_status", pdf)

    @_xfail("B2", "pending_duplicate_doc popped before the unguarded edit")
    async def test_doc_create_anyway_edit_failure(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        f = tmp_path / "x.pdf"
        f.write_bytes(b"%PDF-1.4")
        mock_context.user_data["pending_duplicate_doc"] = {
            "temp_path": str(f),
            "original_filename": "x.pdf",
            "user_context": None,
            "mime_type": None,
        }
        upd = make_callback_query(CB_DOC_CREATE_ANYWAY)
        upd.callback_query.edit_message_text = AsyncMock(side_effect=NetworkError("down"))
        with contextlib.suppress(Exception):
            await _dispatch(upd, mock_context)
        assert _survives(mock_context.user_data, "pending_duplicate_doc", f)

    @_xfail("B2", "pending_fallback_pdf popped before 'Clasificando...' edit")
    async def test_describe_with_caption_edit_failure(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        img = tmp_path / "x.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_context.user_data["pending_fallback_pdf"] = {
            "temp_path": str(img),
            "original_filename": "x.jpg",
            "media_type": "image",
            "user_context": "pizarrón de la clase",
        }
        upd = make_callback_query(CB_DESCRIBE)
        upd.callback_query.edit_message_text = AsyncMock(side_effect=NetworkError("down"))
        with contextlib.suppress(Exception):
            await _dispatch(upd, mock_context)
        assert _survives(mock_context.user_data, "pending_fallback_pdf", img)

    @_xfail("B2", "pending_description popped before _classify_and_preview")
    async def test_description_classify_failure(
        self, make_update, mock_context, tmp_path
    ) -> None:
        from adso.handlers.input import handle_text

        binf = tmp_path / "x.bin"
        binf.write_bytes(b"\x00\x01")
        mock_context.user_data["pending_description"] = {
            "temp_path": str(binf),
            "original_filename": "x.bin",
            "media_type": "document",
        }
        upd = make_update("planilla de notas del curso")
        with patch(
            "adso.handlers.capture._get_existing_items",
            AsyncMock(side_effect=OSError("EIO")),
        ):
            with contextlib.suppress(Exception):
                await handle_text(upd, mock_context)
        assert _survives(mock_context.user_data, "pending_description", binf)

    # counter-cases: on success each key is consumed
    async def test_read_status_success_consumes_key(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_context.user_data["pending_read_status"] = {
            "temp_path": str(pdf),
            "original_filename": "x.pdf",
            "media_type": "document",
        }
        upd = make_callback_query(CB_READ_STATUS_READ)
        meta = {"title": "", "author": "", "subject": "", "pages": 1}
        with patch(
            "adso.handlers.input.extract_pdf",
            AsyncMock(return_value=("texto plano de una factura", meta)),
        ):
            await _dispatch(upd, mock_context)
        assert "pending_read_status" not in mock_context.user_data
        assert mock_context.user_data["pending_extraction"]["temp_path"] == str(pdf)
        assert pdf.exists()

    async def test_doc_create_anyway_success_consumes_key(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        f = tmp_path / "x.pdf"
        f.write_bytes(b"%PDF-1.4")
        mock_context.user_data["pending_duplicate_doc"] = {
            "temp_path": str(f),
            "original_filename": "x.pdf",
            "user_context": None,
            "mime_type": None,
        }
        upd = make_callback_query(CB_DOC_CREATE_ANYWAY)
        with patch(
            "adso.handlers.input._dispatch_document", AsyncMock(return_value=True)
        ):
            await _dispatch(upd, mock_context)
        assert "pending_duplicate_doc" not in mock_context.user_data

    async def test_describe_with_caption_success_consumes_key(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        img = tmp_path / "x.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_context.user_data["pending_fallback_pdf"] = {
            "temp_path": str(img),
            "original_filename": "x.jpg",
            "media_type": "image",
            "user_context": "pizarrón de la clase",
        }
        upd = make_callback_query(CB_DESCRIBE)
        with patch(
            "adso.handlers.capture._classify_and_preview", AsyncMock()
        ) as cap:
            await _dispatch(upd, mock_context)
        cap.assert_awaited_once()
        assert "pending_fallback_pdf" not in mock_context.user_data

    async def test_description_success_consumes_key(
        self, make_update, mock_context, tmp_path
    ) -> None:
        from adso.handlers.input import handle_text

        binf = tmp_path / "x.bin"
        binf.write_bytes(b"\x00\x01")
        mock_context.user_data["pending_description"] = {
            "temp_path": str(binf),
            "original_filename": "x.bin",
            "media_type": "document",
        }
        upd = make_update("planilla de notas del curso")
        with patch(
            "adso.handlers.capture._classify_and_preview", AsyncMock()
        ) as cap:
            await handle_text(upd, mock_context)
        cap.assert_awaited_once()
        assert "pending_description" not in mock_context.user_data


# ===========================================================================
# B3 — paper preview fits Telegram's 4096-char limit
# ===========================================================================

_PAPER_TEXT = (
    "Abstract\nWe study things.\n1 Introduction\nStuff.\nReferences\n[1] X"
)


async def _paper_preview(make_callback_query, mock_context, tmp_path, title: str) -> str:
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    mock_context.user_data["pending_read_status"] = {
        "temp_path": str(pdf),
        "original_filename": "p.pdf",
        "media_type": "document",
    }
    meta = {"title": title, "author": "", "subject": "", "pages": 3}
    upd = make_callback_query(CB_READ_STATUS_READ)
    with patch(
        "adso.handlers.input.extract_pdf", AsyncMock(return_value=(_PAPER_TEXT, meta))
    ):
        await _dispatch(upd, mock_context)
    assert mock_context.user_data["pending_extraction"]["is_paper"]  # precondition
    last = upd.callback_query.edit_message_text.await_args
    return last.args[0] if last.args else last.kwargs["text"]


@patch("adso.security.ALLOWED_USER_IDS", {42})
class TestB3PaperPreviewLength:
    @_xfail("B3", "paper preview has no length cap")
    async def test_huge_title_fits(self, make_callback_query, mock_context, tmp_path) -> None:
        sent = await _paper_preview(
            make_callback_query, mock_context, tmp_path, "T" * 5000
        )
        assert len(sent) <= 4096
        assert "T" * 50 in sent

    # counter-case
    async def test_normal_title_whole(self, make_callback_query, mock_context, tmp_path) -> None:
        title = "A Normal Paper Title About Galaxies"
        sent = await _paper_preview(make_callback_query, mock_context, tmp_path, title)
        assert title in sent


# ===========================================================================
# B4 — UTF-16 / BOM text files
# ===========================================================================


class TestB4TextEncodings:
    @_xfail("B4", "UTF-16 falls back to latin-1: NULs and 'ÿþ'")
    async def test_utf16_with_bom(self, tmp_path) -> None:
        from adso.document_extractor import extract_text_file

        f = tmp_path / "notas.txt"
        f.write_bytes("año de cursada\n".encode("utf-16"))
        out = await extract_text_file(f)
        assert "\x00" not in out
        assert "ÿþ" not in out
        assert "año de cursada" in out

    @_xfail("B4", "UTF-8 BOM kept as \\ufeff")
    async def test_utf8_with_bom(self, tmp_path) -> None:
        from adso.document_extractor import extract_text_file

        f = tmp_path / "notas.txt"
        f.write_bytes("año de cursada\n".encode("utf-8-sig"))
        out = await extract_text_file(f)
        assert "﻿" not in out
        assert "año de cursada" in out

    # counter-cases
    async def test_plain_utf8(self, tmp_path) -> None:
        from adso.document_extractor import extract_text_file

        f = tmp_path / "notas.txt"
        f.write_bytes("año de cursada\n".encode("utf-8"))
        assert "año de cursada" in await extract_text_file(f)

    async def test_latin1_fallback(self, tmp_path) -> None:
        from adso.document_extractor import extract_text_file

        f = tmp_path / "notas.txt"
        f.write_bytes("año de cursada\n".encode("latin-1"))
        assert "año de cursada" in await extract_text_file(f)


# ===========================================================================
# B5 — inline keywords followed by a heading
# ===========================================================================

_PAPER_HEAD = "A Great Paper About Galaxies\nAbstract\nWe present results.\n"


class TestB5InlineKeywords:
    @_xfail("B5", "keywords regex requires a blank line after the line")
    def test_keywords_followed_by_heading(self) -> None:
        from adso.document_extractor import extract_paper_sections

        text = (
            _PAPER_HEAD
            + "Keywords: galaxies, dark matter\n1 Introduction\nBody.\nReferences\n[1] X"
        )
        kw = extract_paper_sections(text, {})["keywords"]
        assert "galaxies" in kw
        assert "dark matter" in kw
        assert "Introduction" not in str(kw)

    # counter-case
    def test_keywords_followed_by_blank_line(self) -> None:
        from adso.document_extractor import extract_paper_sections

        text = (
            _PAPER_HEAD
            + "Keywords: galaxies, dark matter\n\n1 Introduction\nBody.\nReferences\n[1] X"
        )
        kw = extract_paper_sections(text, {})["keywords"]
        assert "galaxies" in kw
        assert "dark matter" in kw
