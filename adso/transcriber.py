"""Transcripción de audio con faster-whisper.

Modelo lazy-loaded para minimizar uso de RAM en RPi4.
Soporta modelos 'tiny' (~75MB) y 'base' (~140MB).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Singleton del modelo — lazy loaded
_model: Optional[object] = None
_model_name: str = ""


async def transcribe_audio(
    file_path: Path,
    model: str = "base",
) -> str:
    """Transcribe un archivo de audio a texto.

    Args:
        file_path: Path al archivo de audio (ogg, mp3, wav, etc.).
        model: Nombre del modelo whisper ('tiny' o 'base').

    Returns:
        Texto transcripto.

    Raises:
        FileNotFoundError: Si el archivo no existe.
        RuntimeError: Si la transcripción falla.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Archivo de audio no encontrado: {file_path}")

    def _do_transcribe() -> str:
        mdl = _get_model(model)
        segments, info = mdl.transcribe(
            str(file_path),
            beam_size=5,
            language=None,  # auto-detect
        )
        text = " ".join(segment.text.strip() for segment in segments)
        logger.info(
            "Transcripción completa: %.1fs de audio, idioma=%s",
            info.duration,
            info.language,
        )
        return text.strip()

    return await asyncio.to_thread(_do_transcribe)


def _get_model(model_name: str) -> object:
    """Obtiene o carga el modelo WhisperModel (singleton lazy).

    Args:
        model_name: 'tiny' o 'base'.

    Returns:
        Instancia de WhisperModel.
    """
    global _model, _model_name

    if _model is not None and _model_name == model_name:
        return _model

    from faster_whisper import WhisperModel

    logger.info("Cargando modelo whisper '%s' (CPU, int8)...", model_name)
    _model = WhisperModel(model_name, device="cpu", compute_type="int8")
    _model_name = model_name
    logger.info("Modelo whisper '%s' cargado.", model_name)

    return _model


def unload_model() -> None:
    """Descarga el modelo de memoria (útil para tests)."""
    global _model, _model_name
    _model = None
    _model_name = ""
