"""Orquestador principal del bot de Telegram.

Solo contiene la configuración de la Application, registro de handlers y run_bot().
Toda la lógica está en los módulos de handlers/.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from adso.config import Settings, load_settings
from adso.embeddings import EmbeddingsClient
from adso.handlers.callbacks import handle_callback
from adso.tasks_client import TasksClient
from adso.handlers.commands import handle_clasificar, handle_help, handle_reset, handle_start, handle_status
from adso.handlers.reports import handle_reporte_command, handle_reporte_full_command
from adso.handlers.input import handle_audio, handle_document, handle_photo, handle_text
from adso.handlers.jobs import heartbeat_job, reindex_job, reclassify_inbox
from adso.vault_writer import GitBackup, ensure_vault_structure, seed_vault


async def _post_init(app: Application) -> None:
    """Inicialización async del vault, ejecutada por PTB antes de arrancar el polling."""
    settings: Settings = app.bot_data["settings"]
    await ensure_vault_structure(settings.vault_path)
    await seed_vault(settings.vault_path, settings.vault_seed)

    import logging
    logging.getLogger(__name__).info("ADSO iniciando — vault en %s", settings.vault_path)


def create_application(settings: Optional[Settings] = None) -> Application:
    """Crea y configura la Application de python-telegram-bot.

    Args:
        settings: Settings cargados. Si None, los carga de config.yaml.

    Returns:
        Application configurada lista para run_polling().
    """
    if settings is None:
        settings = load_settings()

    app = Application.builder().token(settings.telegram_token).post_init(_post_init).build()

    # Bot data compartida
    app.bot_data["settings"] = settings
    app.bot_data["git_backup"] = (
        GitBackup(settings.vault_path, settings.backup.debounce_seconds)
        if settings.backup.enabled
        else None
    )
    app.bot_data["embeddings"] = EmbeddingsClient(
        chroma_data_dir=settings.chroma_data_dir,
        gemini_api_key=settings.gemini_api_key,
    )
    app.bot_data["tasks_client"] = TasksClient(settings.google_calendar_creds)

    # Handlers
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("reset", handle_reset))
    app.add_handler(CommandHandler("clasificar", handle_clasificar))
    app.add_handler(CommandHandler("reporte", handle_reporte_command))
    app.add_handler(CommandHandler("reporte_full", handle_reporte_full_command))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Jobs periódicos
    app.job_queue.run_repeating(heartbeat_job, interval=60, first=10)

    if settings.llm.degraded_retry_minutes > 0:
        app.job_queue.run_repeating(
            reclassify_inbox,
            interval=settings.llm.degraded_retry_minutes * 60,
            first=60,
        )

    if settings.reindex.enabled:
        reindex_time = datetime.strptime(settings.reindex.time, "%H:%M").time()
        app.job_queue.run_daily(
            reindex_job,
            time=reindex_time,
        )

    return app


def run_bot() -> None:
    """Punto de entrada: inicializa y arranca el bot. PTB gestiona el event loop."""
    settings = load_settings()
    app = create_application(settings)
    app.run_polling()
