"""Lote 6 — Gemini Vision resilience, readable errors and report links.

Spec: lote 6 SPEC.md (V1, V2, V3, R1). Trigger: on 2026-09-22 Gemini Vision
returned `503 UNAVAILABLE` six times in a row; each time the bot gave up at
once, threw away the image and printed the raw API error. Each requirement
test is born `xfail(strict=True)`; counter-cases carry no mark and pass today.
"""

from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from google.genai import errors as genai_errors

from adso.constants import (
    CB_DOC_CREATE_ANYWAY,
    CB_EXTRACTION_CANCEL,
    CB_OCR,
    CB_READ_STATUS_READ,
    CB_VISION,
)

SECRET = "SECRET-DETAIL /app/data/x {'error': 503}"
AUTH = patch("adso.security.ALLOWED_USER_IDS", {42})


def _xfail(item_id: str, why: str):
    return pytest.mark.xfail(strict=True, reason=f"LOTE6 {item_id}: {why}")


def _server_503() -> genai_errors.ServerError:
    return genai_errors.ServerError(
        503,
        {"error": {"code": 503, "message": "This model is currently experiencing high demand.",
                   "status": "UNAVAILABLE"}},
    )


def _client_429() -> genai_errors.ClientError:
    return genai_errors.ClientError(
        429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
    )


def _ok(text: str = "Texto de Vision") -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


@contextlib.contextmanager
def _vision_api(side_effect):
    """Real `describe_image_with_vision`, fake genai client, no real sleeping."""
    client = MagicMock()
    client.models.generate_content = MagicMock(side_effect=side_effect)
    sleep = AsyncMock()
    with patch("adso.llm_client._get_genai_client", return_value=client), \
         patch("asyncio.sleep", sleep):
        yield client.models.generate_content, sleep


async def _describe(**kwargs):
    from adso.llm_client import describe_image_with_vision

    return await describe_image_with_vision([(b"\x89PNG", "image/png")], **kwargs)


# ===========================================================================
# V1 — Vision retries transient failures
# ===========================================================================


class TestV1VisionRetries:
    def test_retry_delays_constant(self) -> None:
        from adso.llm_client import VISION_RETRY_DELAYS

        assert list(VISION_RETRY_DELAYS) == [2, 5]

    async def test_503_then_success_returns_text(self) -> None:
        with _vision_api([_server_503(), _ok("hola")]) as (call, sleep):
            text = await _describe()
        assert text == "hola"
        assert call.call_count == 2
        assert [c.args[0] for c in sleep.await_args_list] == [2]

    async def test_two_503_then_success_waits_2_then_5(self) -> None:
        with _vision_api([_server_503(), _server_503(), _ok("hola")]) as (call, sleep):
            text = await _describe()
        assert text == "hola"
        assert call.call_count == 3
        assert [c.args[0] for c in sleep.await_args_list] == [2, 5]

    async def test_three_503_raise_last_after_three_attempts_no_final_wait(self) -> None:
        last = _server_503()
        with _vision_api([_server_503(), _server_503(), last]) as (call, sleep):
            with pytest.raises(genai_errors.ServerError) as exc:
                await _describe()
        assert exc.value is last
        assert call.call_count == 3
        assert [c.args[0] for c in sleep.await_args_list] == [2, 5]

    async def test_any_5xx_is_transient(self) -> None:
        err_500 = genai_errors.ServerError(500, {"error": {"code": 500, "message": "x",
                                                           "status": "INTERNAL"}})
        with _vision_api([err_500, _ok("hola")]) as (call, _):
            assert await _describe() == "hola"
        assert call.call_count == 2

    async def test_timeout_is_transient(self) -> None:
        with _vision_api([httpx.ReadTimeout("slow"), _ok("hola")]) as (call, _):
            assert await _describe() == "hola"
        assert call.call_count == 2

    async def test_transport_error_is_transient(self) -> None:
        with _vision_api([httpx.ConnectError("down"), _ok("hola")]) as (call, _):
            assert await _describe() == "hola"
        assert call.call_count == 2

    async def test_on_retry_called_with_attempt_and_max(self) -> None:
        on_retry = AsyncMock()
        with _vision_api([_server_503(), _server_503(), _ok()]):
            await _describe(on_retry=on_retry)
        assert [c.args for c in on_retry.await_args_list] == [(2, 3), (3, 3)]

    async def test_on_retry_failure_does_not_abort_retry(self) -> None:
        on_retry = AsyncMock(side_effect=RuntimeError("telegram down"))
        with _vision_api([_server_503(), _ok("hola")]) as (call, _):
            assert await _describe(on_retry=on_retry) == "hola"
        assert call.call_count == 2

    async def test_first_success_never_calls_on_retry(self) -> None:
        on_retry = AsyncMock()
        with _vision_api([_ok("hola")]) as (call, sleep):
            assert await _describe(on_retry=on_retry) == "hola"
        on_retry.assert_not_awaited()
        assert call.call_count == 1
        sleep.assert_not_awaited()

    # counter-cases
    async def test_first_success_single_call_no_sleep(self) -> None:
        with _vision_api([_ok("hola")]) as (call, sleep):
            assert await _describe() == "hola"
        assert call.call_count == 1
        sleep.assert_not_awaited()

    async def test_429_is_not_retried(self) -> None:
        with _vision_api([_client_429(), _ok()]) as (call, sleep):
            with pytest.raises(genai_errors.ClientError):
                await _describe()
        assert call.call_count == 1
        sleep.assert_not_awaited()

    async def test_empty_response_is_not_retried(self) -> None:
        with _vision_api([_ok(""), _ok("hola")]) as (call, _):
            with pytest.raises(RuntimeError):
                await _describe()
        assert call.call_count == 1

    async def test_other_exception_is_not_retried(self) -> None:
        with _vision_api([ValueError("bad"), _ok("hola")]) as (call, _):
            with pytest.raises(ValueError):
                await _describe()
        assert call.call_count == 1


# ===========================================================================
# V2 — a failed extraction keeps the image and the buttons
# ===========================================================================


def _image_state(tmp_path: Path) -> tuple[dict, Path]:
    img = tmp_path / "foto.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    return {
        "temp_path": str(img),
        "original_filename": "foto.jpg",
        "media_type": "image",
    }, img


def _callback_data(markup) -> set[str]:
    if markup is None:
        return set()
    return {b.callback_data for row in markup.inline_keyboard for b in row}


def _last_markup(query) -> object:
    for call in reversed(query.edit_message_text.await_args_list):
        markup = call.kwargs.get("reply_markup")
        if markup is not None:
            return markup
    return None


def _edited_texts(query) -> list[str]:
    out = []
    for call in query.edit_message_text.await_args_list:
        if call.args:
            out.append(str(call.args[0]))
        elif "text" in call.kwargs:
            out.append(str(call.kwargs["text"]))
    return out


async def _vision_fails(make_callback_query, ctx, exc) -> MagicMock:
    from adso.handlers.callbacks import _cb_vision

    update = make_callback_query(CB_VISION)
    with patch("adso.llm_client.describe_image_with_vision", AsyncMock(side_effect=exc)):
        await _cb_vision(update, ctx)
    return update


async def _ocr_fails(make_callback_query, ctx, exc) -> MagicMock:
    from adso.handlers.callbacks import _cb_ocr

    update = make_callback_query(CB_OCR)
    with patch("PIL.Image.open", return_value=MagicMock()), \
         patch("pytesseract.image_to_string", side_effect=exc):
        await _cb_ocr(update, ctx)
    return update


class TestV2KeepImageAndButtons:
    async def test_vision_failure_keeps_state_and_file(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        state, img = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        await _vision_fails(make_callback_query, mock_context, _server_503())
        assert mock_context.user_data.get("pending_fallback_pdf", {}).get("temp_path") == str(img)
        assert img.exists()

    async def test_vision_failure_shows_fallback_keyboard(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        state, _ = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        update = await _vision_fails(make_callback_query, mock_context, _server_503())
        data = _callback_data(_last_markup(update.callback_query))
        assert {CB_VISION, CB_OCR, CB_EXTRACTION_CANCEL} <= data

    async def test_ocr_failure_keeps_state_and_file(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        state, img = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        await _ocr_fails(make_callback_query, mock_context, RuntimeError("tesseract roto"))
        assert mock_context.user_data.get("pending_fallback_pdf", {}).get("temp_path") == str(img)
        assert img.exists()

    async def test_ocr_failure_shows_fallback_keyboard_with_ocr(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        state, _ = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        update = await _ocr_fails(make_callback_query, mock_context, RuntimeError("roto"))
        data = _callback_data(_last_markup(update.callback_query))
        assert {CB_VISION, CB_OCR, CB_EXTRACTION_CANCEL} <= data

    async def test_scanned_pdf_vision_failure_keeps_state_and_buttons(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        pdf = tmp_path / "scan.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_context.user_data["pending_fallback_pdf"] = {
            "temp_path": str(pdf),
            "original_filename": "scan.pdf",
            "media_type": "document",
        }
        with patch("adso.handlers.callbacks._render_pdf_pages",
                   return_value=[(b"\x89PNG", "image/png")]):
            update = await _vision_fails(make_callback_query, mock_context, _server_503())
        assert mock_context.user_data.get("pending_fallback_pdf", {}).get("temp_path") == str(pdf)
        assert pdf.exists()
        data = _callback_data(_last_markup(update.callback_query))
        assert {CB_VISION, CB_OCR, CB_EXTRACTION_CANCEL} <= data

    async def test_second_vision_tap_retries_with_same_file(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        from adso.handlers.callbacks import _cb_vision

        state, img = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        await _vision_fails(make_callback_query, mock_context, _server_503())

        second = make_callback_query(CB_VISION)
        vision = AsyncMock(return_value="Texto recuperado")
        with patch("adso.llm_client.describe_image_with_vision", vision):
            await _cb_vision(second, mock_context)

        vision.assert_awaited_once()
        transcript = mock_context.user_data.get("pending_transcript")
        assert transcript and transcript["text"] == "Texto recuperado"
        assert "pending_fallback_pdf" not in mock_context.user_data

    async def test_status_says_retrying_while_vision_retries(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        from adso.handlers.callbacks import _cb_vision

        state, _ = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        update = make_callback_query(CB_VISION)
        with _vision_api([_server_503(), _ok("hola")]):
            await _cb_vision(update, mock_context)
        texts = _edited_texts(update.callback_query)
        assert any("reintentando" in t.lower() for t in texts), texts
        assert mock_context.user_data["pending_transcript"]["text"] == "hola"

    # counter-cases
    @AUTH
    async def test_cancel_clears_state_and_file(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        from adso.handlers.callbacks import handle_callback

        state, img = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        await handle_callback(make_callback_query(CB_EXTRACTION_CANCEL), mock_context)
        assert "pending_fallback_pdf" not in mock_context.user_data
        assert not img.exists()

    async def test_successful_vision_unchanged(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        from adso.handlers.callbacks import _cb_vision

        state, _ = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        update = make_callback_query(CB_VISION)
        with patch("adso.llm_client.describe_image_with_vision",
                   AsyncMock(return_value="Descripción.")):
            await _cb_vision(update, mock_context)
        assert mock_context.user_data["pending_transcript"]["text"] == "Descripción."
        assert "pending_fallback_pdf" not in mock_context.user_data

    async def test_vision_from_ocr_failure_keeps_transcript(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        """From-OCR branch keeps today's behavior: transcript kept, OCR keyboard."""
        from adso.constants import CB_TRANSCRIPT_OK

        img = tmp_path / "foto.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        mock_context.user_data["pending_transcript"] = {
            "text": "texto ocr",
            "media_type": "image",
            "resource_file": {"temp_path": str(img), "filename": "foto.jpg"},
        }
        update = await _vision_fails(make_callback_query, mock_context, _server_503())
        assert mock_context.user_data["pending_transcript"]["text"] == "texto ocr"
        assert img.exists()
        assert {CB_TRANSCRIPT_OK, CB_VISION} <= _callback_data(_last_markup(update.callback_query))


# ===========================================================================
# V3 — no raw exception text in chat; traceback in the log
# ===========================================================================


def _all_sent_texts(*mocks) -> str:
    out = []
    for m in mocks:
        for call in m.await_args_list:
            if call.args:
                out.append(str(call.args[0]))
            if "text" in call.kwargs:
                out.append(str(call.kwargs["text"]))
    return "\n".join(out)


def _assert_clean(sent: str) -> None:
    assert sent.strip(), "nothing was sent to the user"
    assert "SECRET-DETAIL" not in sent, sent
    assert "{'error'" not in sent and "{&#x27;error&#x27;" not in sent, sent


def _assert_logged_with_traceback(caplog) -> None:
    assert any(r.levelno >= logging.ERROR and r.exc_info for r in caplog.records), (
        [(r.levelname, r.getMessage(), bool(r.exc_info)) for r in caplog.records]
    )


class TestV3NoRawErrors:
    # --- callbacks: Vision (both branches), OCR, duplicate-doc processing ---

    async def test_vision_error_is_clean(
        self, make_callback_query, mock_context, tmp_path, caplog
    ) -> None:
        state, _ = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        with caplog.at_level(logging.ERROR):
            update = await _vision_fails(make_callback_query, mock_context, RuntimeError(SECRET))
        q = update.callback_query
        _assert_clean(_all_sent_texts(q.edit_message_text, q.message.reply_text))
        _assert_logged_with_traceback(caplog)

    async def test_vision_from_ocr_error_is_clean(
        self, make_callback_query, mock_context, tmp_path, caplog
    ) -> None:
        img = tmp_path / "foto.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        mock_context.user_data["pending_transcript"] = {
            "text": "texto ocr",
            "media_type": "image",
            "resource_file": {"temp_path": str(img), "filename": "foto.jpg"},
        }
        with caplog.at_level(logging.ERROR):
            update = await _vision_fails(make_callback_query, mock_context, RuntimeError(SECRET))
        q = update.callback_query
        _assert_clean(_all_sent_texts(q.edit_message_text, q.message.reply_text))
        _assert_logged_with_traceback(caplog)

    async def test_vision_503_message_says_saturated(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        state, _ = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        update = await _vision_fails(make_callback_query, mock_context, _server_503())
        texts = _edited_texts(update.callback_query)
        final = texts[-1]
        assert "Gemini" in final
        assert "saturad" in final.lower() or "no está disponible" in final.lower(), final
        assert "{'error'" not in final and "UNAVAILABLE" not in final, final

    async def test_vision_from_ocr_503_message_says_saturated(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        img = tmp_path / "foto.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        mock_context.user_data["pending_transcript"] = {
            "text": "texto ocr",
            "media_type": "image",
            "resource_file": {"temp_path": str(img), "filename": "foto.jpg"},
        }
        update = await _vision_fails(make_callback_query, mock_context, _server_503())
        final = _edited_texts(update.callback_query)[-1]
        assert "Gemini" in final
        assert "saturad" in final.lower() or "no está disponible" in final.lower(), final
        assert "UNAVAILABLE" not in final, final

    async def test_ocr_error_is_clean(
        self, make_callback_query, mock_context, tmp_path, caplog
    ) -> None:
        state, _ = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        with caplog.at_level(logging.ERROR):
            update = await _ocr_fails(make_callback_query, mock_context, RuntimeError(SECRET))
        q = update.callback_query
        _assert_clean(_all_sent_texts(q.edit_message_text, q.message.reply_text))
        _assert_logged_with_traceback(caplog)

    async def test_doc_create_anyway_error_is_clean(
        self, make_callback_query, mock_context, tmp_path, caplog
    ) -> None:
        from adso.handlers.callbacks import _cb_doc_create_anyway

        f = tmp_path / "x.pdf"
        f.write_bytes(b"%PDF-1.4")
        mock_context.user_data["pending_duplicate_doc"] = {
            "temp_path": str(f), "original_filename": "x.pdf",
            "user_context": None, "mime_type": None,
        }
        update = make_callback_query(CB_DOC_CREATE_ANYWAY)
        with caplog.at_level(logging.ERROR), \
             patch("adso.handlers.input._dispatch_document",
                   AsyncMock(side_effect=RuntimeError(SECRET))):
            await _cb_doc_create_anyway(update, mock_context)
        q = update.callback_query
        sent = _all_sent_texts(q.message.reply_text)
        _assert_clean(sent)
        _assert_logged_with_traceback(caplog)

    # --- input.py sites ---

    @AUTH
    async def test_transcription_error_is_clean(
        self, make_update, mock_context, tmp_path, caplog
    ) -> None:
        from adso.handlers import input as input_mod

        update = make_update()
        update.message.voice = MagicMock(file_id="f", file_size=1000)
        update.message.audio = None
        ogg = tmp_path / "a.ogg"
        ogg.write_bytes(b"OggS")
        with caplog.at_level(logging.ERROR), \
             patch.object(input_mod, "_download_to_tmp", AsyncMock(return_value=ogg)), \
             patch.object(input_mod, "_exceeds_size_after_download",
                          AsyncMock(return_value=False)), \
             patch.object(input_mod, "transcribe_audio",
                          AsyncMock(side_effect=RuntimeError(SECRET))):
            await input_mod.handle_audio(update, mock_context)
        sent = _all_sent_texts(update.message.reply_text)
        _assert_clean(sent)
        _assert_logged_with_traceback(caplog)

    async def test_text_file_read_error_is_clean(
        self, make_update, mock_context, tmp_path, caplog
    ) -> None:
        from adso.handlers import input as input_mod

        update = make_update()
        f = tmp_path / "notas.txt"
        f.write_text("hola", encoding="utf-8")
        with caplog.at_level(logging.ERROR), \
             patch.object(input_mod, "extract_text_file",
                          AsyncMock(side_effect=RuntimeError(SECRET))):
            await input_mod._dispatch_document(
                update.message, mock_context, f, "notas.txt", None, "text/plain"
            )
        sent = _all_sent_texts(update.message.reply_text)
        _assert_clean(sent)
        _assert_logged_with_traceback(caplog)

    @AUTH
    async def test_document_error_is_clean(
        self, make_update, mock_context, tmp_path, caplog
    ) -> None:
        from adso.handlers import input as input_mod

        update = make_update()
        doc = MagicMock()
        doc.file_name = "x.pdf"
        doc.file_size = 1024
        doc.mime_type = "application/pdf"
        update.message.document = doc
        update.message.caption = None
        f = tmp_path / "x.pdf"
        f.write_bytes(b"%PDF-1.4")
        with caplog.at_level(logging.ERROR), \
             patch.object(input_mod, "_download_to_tmp", AsyncMock(return_value=f)), \
             patch.object(input_mod, "_exceeds_size_after_download",
                          AsyncMock(return_value=False)), \
             patch.object(input_mod, "_aviso_de_duplicado", AsyncMock(return_value=None)), \
             patch.object(input_mod, "_dispatch_document",
                          AsyncMock(side_effect=RuntimeError(SECRET))):
            await input_mod.handle_document(update, mock_context)
        sent = _all_sent_texts(update.message.reply_text)
        _assert_clean(sent)
        _assert_logged_with_traceback(caplog)

    @AUTH
    async def test_image_error_is_clean(
        self, make_update, mock_context, tmp_path, caplog
    ) -> None:
        from adso.handlers import input as input_mod

        update = make_update()
        update.message.photo = [MagicMock(file_size=1000, file_unique_id="u1")]
        update.message.caption = None
        img = tmp_path / "p.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        update.message.reply_text = AsyncMock(
            side_effect=[RuntimeError(SECRET), MagicMock(message_id=2)]
        )
        with caplog.at_level(logging.ERROR), \
             patch.object(input_mod, "_download_to_tmp", AsyncMock(return_value=img)), \
             patch.object(input_mod, "_exceeds_size_after_download",
                          AsyncMock(return_value=False)):
            await input_mod.handle_photo(update, mock_context)
        calls = update.message.reply_text.await_args_list
        # The first call is the one that failed; what reached the user is the rest.
        sent = "\n".join(str(c.args[0]) for c in calls[1:] if c.args)
        _assert_clean(sent)
        _assert_logged_with_traceback(caplog)

    @AUTH
    async def test_pdf_extraction_error_is_clean(
        self, make_callback_query, mock_context, tmp_path, caplog
    ) -> None:
        from adso.handlers import input as input_mod
        from adso.handlers.callbacks import handle_callback

        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_context.user_data["pending_read_status"] = {
            "temp_path": str(pdf), "original_filename": "x.pdf", "media_type": "document",
        }
        update = make_callback_query(CB_READ_STATUS_READ)
        with caplog.at_level(logging.ERROR), \
             patch.object(input_mod, "extract_pdf", AsyncMock(side_effect=RuntimeError(SECRET))):
            await handle_callback(update, mock_context)
        q = update.callback_query
        _assert_clean(_all_sent_texts(q.edit_message_text, q.message.reply_text))
        _assert_logged_with_traceback(caplog)

    # counter-case: non-error path messages unchanged
    async def test_successful_vision_message_has_no_error_wording(
        self, make_callback_query, mock_context, tmp_path
    ) -> None:
        from adso.handlers.callbacks import _cb_vision

        state, _ = _image_state(tmp_path)
        mock_context.user_data["pending_fallback_pdf"] = state
        update = make_callback_query(CB_VISION)
        with patch("adso.llm_client.describe_image_with_vision",
                   AsyncMock(return_value="Descripción.")):
            await _cb_vision(update, mock_context)
        texts = _edited_texts(update.callback_query)
        assert texts[0] == "Consultando Gemini Vision..."
        assert not any("Error" in t for t in texts)


# ===========================================================================
# R1 — report links survive brackets in titles
# ===========================================================================

_LINK_LINE = re.compile(r"^- \[((?:\\.|[^\]\\])*)\]\((obsidian://[^)]+)\)")


def _note(vault: Path, title: str):
    from adso.vault_writer import NoteData

    return NoteData(path=vault / "00-Inbox" / "n.md", frontmatter={"title": title}, body="cuerpo")


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


class TestR1BracketsInReportLinks:
    def test_note_line_escapes_brackets(self, tmp_path) -> None:
        from adso.reporters import _note_line

        title = "[Sin clasificar] Lácteos"
        line = _note_line(tmp_path, _note(tmp_path, title), vault_name="ADSO")
        assert line == (
            r"- [\[Sin clasificar\] Lácteos](obsidian://open?vault=ADSO&file=00-Inbox/n)"
        )

    def test_note_line_is_one_well_formed_link(self, tmp_path) -> None:
        from adso.reporters import _note_line

        title = "[Sin clasificar] Lácteos [v2]"
        line = _note_line(tmp_path, _note(tmp_path, title), extra="en inbox hace 3d")
        m = _LINK_LINE.match(line)
        assert m, line
        assert _unescape(m.group(1)) == title

    def test_note_block_heading_escapes_brackets(self, tmp_path) -> None:
        from adso.reporters import _note_block

        title = "[Sin clasificar] Lácteos"
        block = _note_block(tmp_path, _note(tmp_path, title), vault_name="ADSO")
        heading = block.splitlines()[0]
        assert heading == (
            r"#### [\[Sin clasificar\] Lácteos](obsidian://open?vault=ADSO&file=00-Inbox/n)"
        )

    # counter-cases
    def test_plain_title_unchanged_line(self, tmp_path) -> None:
        from adso.reporters import _note_line

        line = _note_line(tmp_path, _note(tmp_path, "Nota simple"), vault_name="ADSO")
        assert line == "- [Nota simple](obsidian://open?vault=ADSO&file=00-Inbox/n)"

    def test_plain_title_unchanged_block(self, tmp_path) -> None:
        from adso.reporters import _note_block

        block = _note_block(tmp_path, _note(tmp_path, "Nota simple"), vault_name="ADSO")
        assert block.splitlines()[0] == (
            "#### [Nota simple](obsidian://open?vault=ADSO&file=00-Inbox/n)"
        )
