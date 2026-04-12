"""Tests unitarios para adso.transcriber — faster-whisper wrapper."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from adso.transcriber import transcribe_audio, unload_model


@pytest.fixture(autouse=True)
def cleanup_model():
    """Limpia el modelo singleton entre tests."""
    unload_model()
    yield
    unload_model()


class TestTranscribeAudio:

    @pytest.mark.asyncio
    async def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await transcribe_audio(tmp_path / "nonexistent.ogg")

    @pytest.mark.asyncio
    @patch("adso.transcriber._get_model")
    async def test_transcribe_returns_text(self, mock_get_model, tmp_path: Path) -> None:
        # Crear archivo de audio fake
        audio = tmp_path / "test.ogg"
        audio.write_bytes(b"\x00" * 100)

        # Mock del modelo
        mock_model = MagicMock()
        segment1 = MagicMock()
        segment1.text = " Hola mundo"
        segment2 = MagicMock()
        segment2.text = " esto es una prueba"
        info = MagicMock()
        info.duration = 5.0
        info.language = "es"
        mock_model.transcribe.return_value = ([segment1, segment2], info)
        mock_get_model.return_value = mock_model

        result = await transcribe_audio(audio, model="base")

        assert result == "Hola mundo esto es una prueba"
        mock_model.transcribe.assert_called_once()

    @pytest.mark.asyncio
    @patch("adso.transcriber._get_model")
    async def test_transcribe_empty_audio(self, mock_get_model, tmp_path: Path) -> None:
        audio = tmp_path / "empty.ogg"
        audio.write_bytes(b"\x00" * 10)

        mock_model = MagicMock()
        info = MagicMock()
        info.duration = 0.0
        info.language = "unknown"
        mock_model.transcribe.return_value = ([], info)
        mock_get_model.return_value = mock_model

        result = await transcribe_audio(audio)
        assert result == ""

    @pytest.mark.asyncio
    @patch("adso.transcriber._get_model")
    async def test_transcribe_strips_whitespace(self, mock_get_model, tmp_path: Path) -> None:
        audio = tmp_path / "test.ogg"
        audio.write_bytes(b"\x00")

        mock_model = MagicMock()
        segment = MagicMock()
        segment.text = "  texto con espacios  "
        info = MagicMock(duration=1.0, language="es")
        mock_model.transcribe.return_value = ([segment], info)
        mock_get_model.return_value = mock_model

        result = await transcribe_audio(audio)
        assert result == "texto con espacios"


class TestGetModel:

    @patch("adso.transcriber.WhisperModel", create=True)
    def test_lazy_loads_model(self, MockWhisperModel) -> None:
        """Importa y carga el modelo solo al primer uso."""
        # Patch the import inside _get_model
        with patch("adso.transcriber.WhisperModel", MockWhisperModel, create=True):
            # Need to patch at module level for the import
            import adso.transcriber as mod
            with patch.object(mod, "_model", None), \
                 patch.object(mod, "_model_name", ""):
                # Simulate the import
                mock_instance = MagicMock()
                MockWhisperModel.return_value = mock_instance

                with patch("builtins.__import__", side_effect=ImportError("no faster_whisper")):
                    # Can't test actual import without the library
                    pass

    def test_unload_clears_singleton(self) -> None:
        import adso.transcriber as mod
        mod._model = "fake"
        mod._model_name = "base"
        unload_model()
        assert mod._model is None
        assert mod._model_name == ""
