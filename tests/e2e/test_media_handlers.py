"""Tests E2E para handlers de audio y documentos (Fase 3)."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import ALLOWED_USER_ID, make_message, make_user, make_chat

from adso.bot import (
    handle_audio,
    handle_document,
    handle_callback,
    handle_text,
    build_transcript_keyboard,
    build_read_status_keyboard,
    build_extraction_keyboard,
    CB_TRANSCRIPT_OK,
    CB_TRANSCRIPT_CANCEL,
    CB_READ_STATUS_READ,
    CB_READ_STATUS_UNREAD,
    CB_EXTRACTION_OK,
    CB_EXTRACTION_CANCEL,
    CB_CONFIRM,
    _classify_and_preview,
    _process_pdf_after_read_status,
    _cleanup_pending,
)

# Decorador común para autorizar el user_id de test
AUTH = patch("adso.security.ALLOWED_USER_IDS", {ALLOWED_USER_ID})


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------


class TestNewKeyboards:

    def test_transcript_keyboard(self) -> None:
        kb = build_transcript_keyboard()
        buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert CB_TRANSCRIPT_OK in buttons
        assert CB_TRANSCRIPT_CANCEL in buttons

    def test_read_status_keyboard(self) -> None:
        kb = build_read_status_keyboard()
        buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert CB_READ_STATUS_READ in buttons
        assert CB_READ_STATUS_UNREAD in buttons

    def test_extraction_keyboard(self) -> None:
        kb = build_extraction_keyboard()
        buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert CB_EXTRACTION_OK in buttons
        assert CB_EXTRACTION_CANCEL in buttons


# ---------------------------------------------------------------------------
# handle_audio
# ---------------------------------------------------------------------------


class TestHandleAudio:

    @pytest.mark.asyncio
    @AUTH
    @patch("adso.bot.transcribe_audio")
    async def test_voice_message_transcribes(
        self, mock_transcribe, make_update, mock_context, tmp_path,
    ) -> None:
        mock_transcribe.return_value = "Texto transcripto del audio."

        update = make_update()
        voice = MagicMock()
        voice.file_size = 1024
        tg_file = MagicMock()
        tg_file.file_path = "audio.ogg"
        tg_file.download_to_drive = AsyncMock()
        voice.get_file = AsyncMock(return_value=tg_file)

        update.message.voice = voice
        update.message.audio = None

        with patch("tempfile.NamedTemporaryFile") as mock_tmp:
            fake_tmp = MagicMock()
            fake_tmp.name = str(tmp_path / "audio.ogg")
            fake_tmp.__enter__ = lambda s: s
            fake_tmp.__exit__ = lambda s, *a: None
            mock_tmp.return_value = fake_tmp
            (tmp_path / "audio.ogg").write_bytes(b"\x00")

            await handle_audio(update, mock_context)

        assert "pending_transcript" in mock_context.user_data
        assert mock_context.user_data["pending_transcript"]["text"] == "Texto transcripto del audio."

    @pytest.mark.asyncio
    @AUTH
    async def test_audio_too_large(self, make_update, mock_context) -> None:
        update = make_update()
        voice = MagicMock()
        voice.file_size = 100 * 1024 * 1024  # 100MB
        update.message.voice = voice
        update.message.audio = None

        await handle_audio(update, mock_context)

        update.message.reply_text.assert_called_once()
        assert "grande" in str(update.message.reply_text.call_args)

    @pytest.mark.asyncio
    @AUTH
    async def test_audio_no_file(self, make_update, mock_context) -> None:
        update = make_update()
        update.message.voice = None
        update.message.audio = None

        await handle_audio(update, mock_context)

        update.message.reply_text.assert_called_once()
        assert "No se pudo" in str(update.message.reply_text.call_args)

    @pytest.mark.asyncio
    @AUTH
    @patch("adso.bot.transcribe_audio")
    async def test_audio_file_message(
        self, mock_transcribe, make_update, mock_context, tmp_path,
    ) -> None:
        """Audio enviado como archivo (no voice note)."""
        mock_transcribe.return_value = "Audio transcripto."

        update = make_update()
        audio = MagicMock()
        audio.file_size = 2048
        tg_file = MagicMock()
        tg_file.file_path = "audio.mp3"
        tg_file.download_to_drive = AsyncMock()
        audio.get_file = AsyncMock(return_value=tg_file)

        update.message.voice = None
        update.message.audio = audio

        with patch("tempfile.NamedTemporaryFile") as mock_tmp:
            fake_tmp = MagicMock()
            fake_tmp.name = str(tmp_path / "audio.mp3")
            fake_tmp.__enter__ = lambda s: s
            fake_tmp.__exit__ = lambda s, *a: None
            mock_tmp.return_value = fake_tmp
            (tmp_path / "audio.mp3").write_bytes(b"\x00")

            await handle_audio(update, mock_context)

        assert "pending_transcript" in mock_context.user_data

    @pytest.mark.asyncio
    @AUTH
    @patch("adso.bot.transcribe_audio")
    async def test_empty_transcription(
        self, mock_transcribe, make_update, mock_context, tmp_path,
    ) -> None:
        mock_transcribe.return_value = ""

        update = make_update()
        voice = MagicMock()
        voice.file_size = 1024
        tg_file = MagicMock()
        tg_file.file_path = "audio.ogg"
        tg_file.download_to_drive = AsyncMock()
        voice.get_file = AsyncMock(return_value=tg_file)

        update.message.voice = voice
        update.message.audio = None

        with patch("tempfile.NamedTemporaryFile") as mock_tmp:
            fake_tmp = MagicMock()
            fake_tmp.name = str(tmp_path / "audio.ogg")
            fake_tmp.__enter__ = lambda s: s
            fake_tmp.__exit__ = lambda s, *a: None
            mock_tmp.return_value = fake_tmp
            (tmp_path / "audio.ogg").write_bytes(b"\x00")

            await handle_audio(update, mock_context)

        assert "pending_transcript" not in mock_context.user_data
        assert "No se pudo extraer" in str(update.message.reply_text.call_args_list[-1])


# ---------------------------------------------------------------------------
# handle_document
# ---------------------------------------------------------------------------


class TestHandleDocument:

    @pytest.mark.asyncio
    @AUTH
    async def test_pdf_shows_read_status(
        self, make_update, mock_context, tmp_path,
    ) -> None:
        update = make_update()
        doc = MagicMock()
        doc.file_name = "paper.pdf"
        doc.file_size = 1024
        tg_file = MagicMock()
        tg_file.download_to_drive = AsyncMock()
        doc.get_file = AsyncMock(return_value=tg_file)
        update.message.document = doc

        with patch("tempfile.NamedTemporaryFile") as mock_tmp:
            fake_tmp = MagicMock()
            fake_tmp.name = str(tmp_path / "paper.pdf")
            fake_tmp.__enter__ = lambda s: s
            fake_tmp.__exit__ = lambda s, *a: None
            mock_tmp.return_value = fake_tmp
            (tmp_path / "paper.pdf").write_bytes(b"\x00")

            await handle_document(update, mock_context)

        assert "pending_read_status" in mock_context.user_data

    @pytest.mark.asyncio
    @AUTH
    async def test_text_file_shows_extraction(
        self, make_update, mock_context, tmp_path,
    ) -> None:
        update = make_update()
        doc = MagicMock()
        doc.file_name = "notes.txt"
        doc.file_size = 512
        tg_file = MagicMock()
        tg_file.download_to_drive = AsyncMock()
        doc.get_file = AsyncMock(return_value=tg_file)
        update.message.document = doc

        txt_path = tmp_path / "notes.txt"
        txt_path.write_text("Contenido del archivo de texto.", encoding="utf-8")

        with patch("tempfile.NamedTemporaryFile") as mock_tmp:
            fake_tmp = MagicMock()
            fake_tmp.name = str(txt_path)
            fake_tmp.__enter__ = lambda s: s
            fake_tmp.__exit__ = lambda s, *a: None
            mock_tmp.return_value = fake_tmp

            await handle_document(update, mock_context)

        assert "pending_extraction" in mock_context.user_data
        assert mock_context.user_data["pending_extraction"]["text"] == "Contenido del archivo de texto."

    @pytest.mark.asyncio
    @AUTH
    async def test_binary_file_asks_description(
        self, make_update, mock_context, tmp_path,
    ) -> None:
        update = make_update()
        doc = MagicMock()
        doc.file_name = "data.xlsx"
        doc.file_size = 2048
        tg_file = MagicMock()
        tg_file.download_to_drive = AsyncMock()
        doc.get_file = AsyncMock(return_value=tg_file)
        update.message.document = doc

        with patch("tempfile.NamedTemporaryFile") as mock_tmp:
            fake_tmp = MagicMock()
            fake_tmp.name = str(tmp_path / "data.xlsx")
            fake_tmp.__enter__ = lambda s: s
            fake_tmp.__exit__ = lambda s, *a: None
            mock_tmp.return_value = fake_tmp
            (tmp_path / "data.xlsx").write_bytes(b"\x00")

            await handle_document(update, mock_context)

        assert "pending_description" in mock_context.user_data

    @pytest.mark.asyncio
    @AUTH
    async def test_document_too_large(self, make_update, mock_context) -> None:
        update = make_update()
        doc = MagicMock()
        doc.file_name = "big.pdf"
        doc.file_size = 100 * 1024 * 1024
        update.message.document = doc

        await handle_document(update, mock_context)

        assert "grande" in str(update.message.reply_text.call_args)

    @pytest.mark.asyncio
    @AUTH
    async def test_document_no_doc(self, make_update, mock_context) -> None:
        update = make_update()
        update.message.document = None

        await handle_document(update, mock_context)

        assert "No se pudo" in str(update.message.reply_text.call_args)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestTranscriptCallbacks:

    @pytest.mark.asyncio
    @AUTH
    @patch("adso.bot._classify_and_preview", new_callable=AsyncMock)
    async def test_transcript_ok_classifies(
        self, mock_classify, make_callback_query, mock_context,
    ) -> None:
        mock_context.user_data["pending_transcript"] = {
            "text": "Texto transcripto",
            "temp_path": "/tmp/nonexistent.ogg",
            "media_type": "audio",
        }

        update = make_callback_query(CB_TRANSCRIPT_OK)
        await handle_callback(update, mock_context)

        mock_classify.assert_called_once()
        assert "pending_transcript" not in mock_context.user_data

    @pytest.mark.asyncio
    @AUTH
    async def test_transcript_cancel_clears_state(
        self, make_callback_query, mock_context,
    ) -> None:
        mock_context.user_data["pending_transcript"] = {
            "text": "algo",
            "temp_path": "/tmp/nonexistent.ogg",
        }

        update = make_callback_query(CB_TRANSCRIPT_CANCEL)
        await handle_callback(update, mock_context)

        assert "pending_transcript" not in mock_context.user_data

    @pytest.mark.asyncio
    @AUTH
    async def test_transcript_ok_no_pending(
        self, make_callback_query, mock_context,
    ) -> None:
        update = make_callback_query(CB_TRANSCRIPT_OK)
        await handle_callback(update, mock_context)

        update.callback_query.edit_message_text.assert_called_with(
            "No hay transcripción pendiente."
        )


class TestReadStatusCallbacks:

    @pytest.mark.asyncio
    @AUTH
    @patch("adso.bot.extract_pdf")
    async def test_read_status_triggers_extraction(
        self, mock_extract, make_callback_query, mock_context, tmp_path,
    ) -> None:
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"fake pdf")

        mock_context.user_data["pending_read_status"] = {
            "temp_path": str(pdf_path),
            "original_filename": "test.pdf",
            "media_type": "document",
        }

        mock_extract.return_value = ("Texto extraído del PDF.", {
            "title": "Test",
            "author": "Author",
            "subject": "",
            "pages": 5,
        })

        update = make_callback_query(CB_READ_STATUS_READ)
        await handle_callback(update, mock_context)

        mock_extract.assert_called_once()
        assert "pending_extraction" in mock_context.user_data
        assert mock_context.user_data["pending_extraction"]["read_status"] == "read"

    @pytest.mark.asyncio
    @AUTH
    @patch("adso.bot.extract_pdf")
    async def test_empty_pdf_asks_description(
        self, mock_extract, make_callback_query, mock_context, tmp_path,
    ) -> None:
        pdf_path = tmp_path / "scanned.pdf"
        pdf_path.write_bytes(b"fake pdf")

        mock_context.user_data["pending_read_status"] = {
            "temp_path": str(pdf_path),
            "original_filename": "scanned.pdf",
            "media_type": "document",
        }

        mock_extract.return_value = ("", {
            "title": "",
            "author": "",
            "subject": "",
            "pages": 1,
        })

        update = make_callback_query(CB_READ_STATUS_UNREAD)
        await handle_callback(update, mock_context)

        assert "pending_description" in mock_context.user_data


class TestExtractionCallbacks:

    @pytest.mark.asyncio
    @AUTH
    @patch("adso.bot._classify_and_preview", new_callable=AsyncMock)
    async def test_extraction_ok_classifies(
        self, mock_classify, make_callback_query, mock_context,
    ) -> None:
        mock_context.user_data["pending_extraction"] = {
            "text": "Texto del PDF",
            "temp_path": "/tmp/nonexistent.pdf",
            "original_filename": "paper.pdf",
            "media_type": "document",
            "read_status": "read",
            "metadata": {"title": "Paper"},
        }

        update = make_callback_query(CB_EXTRACTION_OK)
        await handle_callback(update, mock_context)

        mock_classify.assert_called_once()

    @pytest.mark.asyncio
    @AUTH
    async def test_extraction_cancel(
        self, make_callback_query, mock_context,
    ) -> None:
        mock_context.user_data["pending_extraction"] = {
            "text": "algo",
            "temp_path": "/tmp/nonexistent.txt",
            "original_filename": "file.txt",
        }

        update = make_callback_query(CB_EXTRACTION_CANCEL)
        await handle_callback(update, mock_context)

        assert "pending_extraction" not in mock_context.user_data

    @pytest.mark.asyncio
    @AUTH
    async def test_extraction_ok_no_pending(
        self, make_callback_query, mock_context,
    ) -> None:
        update = make_callback_query(CB_EXTRACTION_OK)
        await handle_callback(update, mock_context)

        update.callback_query.edit_message_text.assert_called_with(
            "No hay extracción pendiente."
        )


# ---------------------------------------------------------------------------
# Text corrections in media flows
# ---------------------------------------------------------------------------


class TestTextCorrectionsInMediaFlows:

    @pytest.mark.asyncio
    @AUTH
    async def test_text_corrects_transcript(
        self, make_update, mock_context,
    ) -> None:
        mock_context.user_data["pending_transcript"] = {
            "text": "texto original",
            "temp_path": "/tmp/fake.ogg",
            "media_type": "audio",
        }

        update = make_update(text="texto corregido")
        await handle_text(update, mock_context)

        assert mock_context.user_data["pending_transcript"]["text"] == "texto corregido"

    @pytest.mark.asyncio
    @AUTH
    async def test_text_corrects_extraction(
        self, make_update, mock_context,
    ) -> None:
        mock_context.user_data["pending_extraction"] = {
            "text": "texto original",
            "temp_path": "/tmp/fake.pdf",
            "original_filename": "file.pdf",
            "media_type": "document",
            "metadata": {},
        }

        update = make_update(text="texto corregido")
        await handle_text(update, mock_context)

        assert mock_context.user_data["pending_extraction"]["text"] == "texto corregido"

    @pytest.mark.asyncio
    @AUTH
    @patch("adso.bot._classify_and_preview", new_callable=AsyncMock)
    async def test_text_provides_description(
        self, mock_classify, make_update, mock_context,
    ) -> None:
        mock_context.user_data["pending_description"] = {
            "temp_path": "/tmp/fake.xlsx",
            "original_filename": "data.xlsx",
            "media_type": "document",
        }

        update = make_update(text="Planilla de datos de experimentación")
        await handle_text(update, mock_context)

        mock_classify.assert_called_once()
        assert "pending_description" not in mock_context.user_data


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:

    def test_cleanup_all(self, mock_context) -> None:
        mock_context.user_data["pending_note"] = {"data": "x"}
        mock_context.user_data["pending_transcript"] = {"text": "x"}
        mock_context.user_data["pending_extraction"] = {"text": "x"}

        _cleanup_pending(mock_context)

        assert mock_context.user_data == {}

    def test_cleanup_specific(self, mock_context) -> None:
        mock_context.user_data["pending_note"] = {"data": "x"}
        mock_context.user_data["pending_transcript"] = {"text": "x"}

        _cleanup_pending(mock_context, "pending_transcript")

        assert "pending_note" in mock_context.user_data
        assert "pending_transcript" not in mock_context.user_data


# ---------------------------------------------------------------------------
# save_resource
# ---------------------------------------------------------------------------


class TestSaveResource:

    @pytest.mark.asyncio
    async def test_saves_to_resources(self, vault_path: Path, tmp_path: Path) -> None:
        from adso.vault_writer import save_resource

        src = tmp_path / "paper.pdf"
        src.write_bytes(b"PDF content")

        dest = await save_resource(src, "paper.pdf", vault_path)

        assert dest.parent.name == "03-Resources"
        assert dest.name == "paper.pdf"
        assert dest.read_bytes() == b"PDF content"

    @pytest.mark.asyncio
    async def test_avoids_overwrite(self, vault_path: Path, tmp_path: Path) -> None:
        from adso.vault_writer import save_resource

        (vault_path / "03-Resources" / "paper.pdf").write_bytes(b"old")

        src = tmp_path / "paper.pdf"
        src.write_bytes(b"new")

        dest = await save_resource(src, "paper.pdf", vault_path)

        assert dest.name == "paper_1.pdf"
        assert dest.read_bytes() == b"new"

    @pytest.mark.asyncio
    async def test_source_not_found(self, vault_path: Path, tmp_path: Path) -> None:
        from adso.vault_writer import save_resource

        with pytest.raises(FileNotFoundError):
            await save_resource(tmp_path / "no.pdf", "no.pdf", vault_path)
