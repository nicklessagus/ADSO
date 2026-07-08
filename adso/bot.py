"""Orquestador principal del bot de Telegram.

Solo contiene la configuración de la Application, registro de handlers y run_bot().
Toda la lógica está en los módulos de handlers/.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

import asyncio
import logging

from adso import vault_cache
from adso.config import Settings, load_settings
from adso.embeddings import EmbeddingsClient
from adso.handlers.callbacks import handle_callback
from adso.tasks_client import TasksClient
from adso.handlers.commands import handle_clasificar, handle_help, handle_reset, handle_start, handle_status
from adso.handlers.reports import handle_reporte_command, handle_reporte_full_command
from adso.handlers.input import handle_audio, handle_document, handle_photo, handle_text
from adso.handlers.query import handle_buscar
from adso.handlers.capture import _index_note_safe
from adso.handlers.jobs import heartbeat_job, reindex_job, reclassify_inbox
from adso.security import is_authorized
from adso.vault_watcher import VaultWatcher
from adso.vault_writer import GitBackup, ensure_vault_structure, remove_broken_wikilinks, seed_vault

_bot_logger = logging.getLogger(__name__)


async def _post_init(app: Application) -> None:
    """Inicialización async del vault, ejecutada por PTB antes de arrancar el polling."""
    settings: Settings = app.bot_data["settings"]
    await ensure_vault_structure(settings.vault_path)
    await seed_vault(settings.vault_path, settings.vault_seed)

    embeddings: EmbeddingsClient = app.bot_data["embeddings"]
    vault_path = settings.vault_path

    async def _reindex_external_note(path: Path) -> None:
        """Lee una nota modificada externamente y actualiza su embedding.

        Si el path fue escrito por el bot (capture/jobs ya disparó indexado y
        backup), no se reindexa ni se notifica al git_backup: evita double-embed
        y entradas duplicadas en commits.
        """
        bot_written: set = app.bot_data.setdefault("bot_written_paths", set())
        if path in bot_written:
            bot_written.discard(path)
            return
        try:
            note = await asyncio.to_thread(vault_cache.parse_cached, path)
            if note is None:
                return
            body = note.body.strip()
            if not body:
                return
            await _index_note_safe(embeddings, path, body, note.frontmatter, vault_path)
            _bot_logger.info("Reindex externo completado: %s", path)
        except Exception as exc:
            _bot_logger.warning("Reindex externo fallido para %s: %s", path, exc)
        git_backup: Optional[GitBackup] = app.bot_data.get("git_backup")
        if git_backup:
            await git_backup.notify(path.stem)

    async def _remove_external_note(path: Path) -> None:
        """Elimina de ChromaDB el embedding de una nota borrada externamente y limpia wikilinks rotos."""
        try:
            rel = path.relative_to(vault_path)
            note_id = str(rel).replace(".md", "")
            await embeddings.remove_note(note_id)
            _bot_logger.info("Embedding eliminado por borrado externo: %s", note_id)
        except Exception as exc:
            _bot_logger.warning("Error eliminando embedding de %s: %s", path, exc)
        try:
            count = await remove_broken_wikilinks(vault_path, path)
            if count:
                _bot_logger.info("Wikilinks rotos eliminados en %d notas tras borrado de %s", count, path.name)
                await app.bot.send_message(
                    chat_id=settings.telegram_allowed_user_id,
                    text=(
                        f"🔗 Wikilinks rotos limpiados en {count} nota{'s' if count > 1 else ''} "
                        f"tras borrar <code>{path.name}</code>."
                    ),
                    parse_mode="HTML",
                )
        except Exception as exc:
            _bot_logger.warning("Error limpiando wikilinks rotos para %s: %s", path, exc)
        git_backup: Optional[GitBackup] = app.bot_data.get("git_backup")
        if git_backup:
            await git_backup.notify(path.stem)

    watcher = VaultWatcher(
        vault_path=vault_path,
        bot=app.bot,
        chat_id=settings.telegram_allowed_user_id,
        debug=settings.watcher.debug,
        on_external_change=_reindex_external_note,
        on_external_delete=_remove_external_note,
    )
    await watcher.start()
    app.bot_data["vault_watcher"] = watcher

    import logging
    logging.getLogger(__name__).info("ADSO iniciando — vault en %s", settings.vault_path)


async def _post_shutdown(app: Application) -> None:
    """Limpieza async ejecutada por PTB al detener el bot."""
    watcher: Optional[VaultWatcher] = app.bot_data.get("vault_watcher")
    if watcher:
        await watcher.stop()
    # Vaciar el debounce del backup: una nota escrita en los últimos segundos
    # quedaría sin commit/push hasta la próxima escritura si no forzamos el flush.
    git_backup: Optional[GitBackup] = app.bot_data.get("git_backup")
    if git_backup:
        try:
            await git_backup.flush()
        except Exception as exc:  # noqa: BLE001 — el shutdown no debe fallar por el backup
            _bot_logger.warning("Error en flush de git backup al shutdown: %s", exc)


# BadRequests esperables que no ameritan log de error ni aviso al usuario:
# - "message is not modified": reintento de edición con contenido idéntico —
#   el contenido ya está aplicado (típico tras un timeout de red a mitad de flujo).
# - "query is too old": el callback llegó tarde a Telegram (>~30s por lag de red);
#   la interacción en sí ya se procesó o el usuario va a re-tapear.
_BENIGN_BADREQUEST = ("message is not modified", "query is too old")


async def _global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Error handler global de PTB: loguea excepciones no capturadas y avisa al usuario.

    Sin esto registrado, PTB solo emite "No error handlers are registered" y el
    usuario no recibe ninguna señal cuando un handler muere a mitad de flujo.
    Los errores de red (TimedOut, etc.) solo se loguean: intentar notificar por
    la misma red caída fallaría de nuevo.
    """
    err = context.error
    if isinstance(err, BadRequest) and any(m in str(err).lower() for m in _BENIGN_BADREQUEST):
        _bot_logger.info("BadRequest benigno ignorado: %s", err)
        return
    _bot_logger.error("Error no manejado procesando update: %s", err, exc_info=err)
    if isinstance(err, NetworkError):
        return
    chat_id = update.effective_chat.id if isinstance(update, Update) and update.effective_chat else None
    if chat_id is None:
        return
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Ocurrió un error inesperado. Reintentar la operación o usar /reset.",
        )
    except Exception:
        _bot_logger.debug("No se pudo notificar el error al usuario.")


async def _global_auth_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Descarta updates de usuarios no autorizados antes de cualquier handler.

    Registrado en group=-1: si el usuario no está en la allow-list, corta el
    procesamiento (ApplicationHandlerStop) y el update nunca llega a los handlers
    reales del group 0. Defensa en profundidad: hace que el decorador `authorized`
    por handler sea cinturón-y-tiradores en vez del único control — un handler
    nuevo sin decorar ya no es un bypass.
    """
    if not is_authorized(update):
        raise ApplicationHandlerStop


def create_application(settings: Optional[Settings] = None) -> Application:
    """Crea y configura la Application de python-telegram-bot.

    Args:
        settings: Settings cargados. Si None, los carga de config.yaml.

    Returns:
        Application configurada lista para run_polling().
    """
    if settings is None:
        settings = load_settings()

    app = (
        Application.builder()
        .token(settings.telegram_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # Bot data compartida
    app.bot_data["settings"] = settings
    app.bot_data["git_backup"] = (
        GitBackup(
            settings.vault_path,
            settings.backup.debounce_seconds,
            bot=app.bot,
            chat_id=settings.telegram_allowed_user_id,
            debug=settings.watcher.debug,
        )
        if settings.backup.enabled
        else None
    )
    app.bot_data["embeddings"] = EmbeddingsClient(
        chroma_data_dir=settings.chroma_data_dir,
        gemini_api_key=settings.gemini_api_key,
    )
    app.bot_data["tasks_client"] = TasksClient(settings.google_calendar_creds)

    # Gate de auth global (defensa en profundidad): corre antes que todo y
    # descarta updates no autorizados. Los handlers siguen decorados con
    # @authorized como segunda barrera.
    app.add_handler(TypeHandler(Update, _global_auth_gate), group=-1)
    app.add_error_handler(_global_error_handler)

    # Handlers
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("reset", handle_reset))
    app.add_handler(CommandHandler("clasificar", handle_clasificar))
    app.add_handler(CommandHandler("buscar", handle_buscar))
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
