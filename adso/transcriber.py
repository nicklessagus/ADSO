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
    model_dir: str = "/app/data/whisper",
    language: str = "es",
) -> str:
    """Transcribe un archivo de audio a texto.

    Args:
        file_path: Path al archivo de audio (ogg, mp3, wav, etc.).
        model: Nombre del modelo whisper ('tiny' o 'base').
        model_dir: Directorio donde se almacena/descarga el modelo. Debe ser
            escribible; en Docker usar un path dentro del volumen /app/data.

    Returns:
        Texto transcripto.

    Raises:
        FileNotFoundError: Si el archivo no existe.
        RuntimeError: Si la transcripción falla.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Archivo de audio no encontrado: {file_path}")

    def _do_transcribe() -> str:
        mdl = _get_model(model, model_dir)
        # beam_size=1 (greedy): en CPU ARM int8 el beam search de 5 es 3-5x
        # más lento con ganancia marginal para notas de voz cortas.
        segments, info = mdl.transcribe(
            str(file_path),
            beam_size=1,
            language=language,
        )
        text = " ".join(segment.text.strip() for segment in segments)
        logger.info(
            "Transcripción completa: %.1fs de audio, idioma=%s",
            info.duration,
            info.language,
        )
        return text.strip()

    return await asyncio.to_thread(_do_transcribe)


def _get_model(model_name: str, model_dir: str = "/app/data/whisper") -> object:
    """Obtiene o carga el modelo WhisperModel (singleton lazy).

    Args:
        model_name: 'tiny' o 'base'.
        model_dir: Directorio local donde se descarga/cachea el modelo.

    Returns:
        Instancia de WhisperModel.
    """
    global _model, _model_name

    if _model is not None and _model_name == model_name:
        return _model

    from faster_whisper import WhisperModel
    import os

    def _load(target_dir: str) -> object:
        os.makedirs(target_dir, exist_ok=True)
        return WhisperModel(model_name, device="cpu", compute_type="int8", download_root=target_dir)

    logger.info("Cargando modelo whisper '%s' en %s (CPU, int8)...", model_name, model_dir)
    try:
        _model = _load(model_dir)
    except OSError as e:
        fallback = "/tmp/whisper_models"
        logger.warning(
            "No se pudo usar model_dir=%s (%s) — usando %s (no persistente, "
            "el modelo se re-descargará en cada reinicio)",
            model_dir, e, fallback,
        )
        _model = _load(fallback)
    _model_name = model_name
    logger.info("Modelo whisper '%s' cargado.", model_name)

    return _model


def unload_model() -> None:
    """Descarga el modelo de memoria (útil para tests)."""
    global _model, _model_name
    _model = None
    _model_name = ""
