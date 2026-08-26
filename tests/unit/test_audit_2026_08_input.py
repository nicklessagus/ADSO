"""Reproductores de los bugs de la auditoría 2026-08-22 — entrada de medios.

Mismo contrato que `test_audit_2026_08_vault.py`: cada test **especifica el
comportamiento correcto** y se escribió reproduciendo el bug (fallaba) antes de
aplicar el fix. Ahora pasan y quedan como regresión: si alguno de estos defectos
vuelve, fallan.

Issues:
  M1 — los cuatro `MessageHandler` también matchean `edited_message`, y en esos
       updates `update.message` es `None`: editar un mensaje ya mandado (o el
       caption de una foto/PDF) mata al handler con `AttributeError`.
  M2 — el estado (`pending_*`) se setea ANTES del reply/edit que muestra los
       botones. Si ese envío falla, el estado queda colgado sin teclado:
       `_has_pending_keyboard` / `_is_awaiting_text_input` rechazan todo input
       posterior y la única salida es `/reset`. Es el mismo modo de falla que el
       fix E9 cerró para `handle_audio` (ver `TestE9TranscripcionConReplyRoto` en
       `test_audit_block_e.py`).
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Chat, Document, Message, PhotoSize, Update, User, Voice
from telegram.ext import MessageHandler

from adso.bot_utils import _has_pending_keyboard, _is_awaiting_text_input


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _edited_update(**campos) -> Update:
    """Update de EDICIÓN real de PTB: el mensaje viaja en `edited_message`.

    `update.message` queda en `None` (es una property que solo mira
    `message`/`channel_post`), que es exactamente lo que ven los handlers.
    """
    msg = Message(
        message_id=5,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Test"),
        **campos,
    )
    return Update(update_id=1, edited_message=msg)


def _foto() -> PhotoSize:
    return PhotoSize(file_id="f", file_unique_id="u", width=100, height=100, file_size=1000)


def _doc_real() -> Document:
    return Document(file_id="f", file_unique_id="u", file_name="paper.pdf", file_size=1000)


def _voz() -> Voice:
    return Voice(file_id="f", file_unique_id="u", duration=3)


def _tg_file() -> MagicMock:
    """Archivo de Telegram ya descargable (la descarga es un no-op)."""
    f = MagicMock()
    f.file_path = "archivo.dat"
    f.download_to_drive = AsyncMock()
    return f


def _documento(filename: str) -> MagicMock:
    doc = MagicMock()
    doc.file_name = filename
    doc.file_size = 1000  # declarado ⇒ el re-check de tamaño post-descarga no hace stat
    doc.get_file = AsyncMock(return_value=_tg_file())
    return doc


# ---------------------------------------------------------------------------
# M1 — los handlers reciben updates de edición y mueren con AttributeError
# ---------------------------------------------------------------------------
#
# `filters.TEXT & ~filters.COMMAND`, `filters.PHOTO`, `filters.Document.ALL` y
# `filters.VOICE | filters.AUDIO` filtran por CONTENIDO, no por tipo de update:
# los cuatro devuelven True para un `Update(edited_message=...)` (verificado con
# `check_update`). En esos updates `update.message` es None, así que la primera
# línea de cada handler (`update.message.text`, `msg.document`, `msg.photo`,
# `msg.voice or msg.audio`) lanza `AttributeError` y el error handler global le
# contesta al usuario "Ocurrió un error inesperado" por corregir un typo.
#
# Editar un mensaje no es contenido nuevo: el bot no tiene ningún flujo de
# re-procesamiento, así que el comportamiento correcto es ignorar la edición
# (idealmente en el filtro, con `filters.UpdateType.MESSAGE`).


class TestM1UpdatesDeEdicion:
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_handle_text_ignora_la_edicion(self, mock_context) -> None:
        from adso.handlers import input as input_mod

        await input_mod.handle_text(_edited_update(text="texto corregido"), mock_context)

        assert mock_context.user_data == {}, "una edición no debe abrir un flujo de captura"

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_handle_photo_ignora_la_edicion_de_caption(self, mock_context) -> None:
        from adso.handlers import input as input_mod

        update = _edited_update(photo=(_foto(),), caption="caption corregido")
        await input_mod.handle_photo(update, mock_context)

        assert not _has_pending_keyboard(mock_context)

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_handle_document_ignora_la_edicion_de_caption(self, mock_context) -> None:
        from adso.handlers import input as input_mod

        update = _edited_update(document=_doc_real(), caption="caption corregido")
        await input_mod.handle_document(update, mock_context)

        assert not _has_pending_keyboard(mock_context)

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_handle_audio_ignora_la_edicion(self, mock_context) -> None:
        from adso.handlers import input as input_mod

        await input_mod.handle_audio(_edited_update(voice=_voz()), mock_context)

        assert not _has_pending_keyboard(mock_context)


class TestM1FiltrosRegistrados:
    """El fix natural es el filtro, no cuatro guards: documenta el contrato.

    Los `MessageHandler` que registra `create_application` no deberían aceptar
    updates de edición — con `filters.UpdateType.MESSAGE` en el `&` ninguno de
    los cuatro handlers se entera de que existieron.
    """

    def test_ningun_message_handler_matchea_una_edicion(self, mock_context) -> None:
        from adso.bot import create_application

        app = create_application(mock_context.bot_data["settings"])
        ediciones = [
            _edited_update(text="hola"),
            _edited_update(photo=(_foto(),)),
            _edited_update(document=_doc_real()),
            _edited_update(voice=_voz()),
        ]

        matcheados = [
            (h.callback.__name__, str(h.filters))
            for grupo in app.handlers.values()
            for h in grupo
            if isinstance(h, MessageHandler)
            for upd in ediciones
            if h.check_update(upd)
        ]

        assert matcheados == [], (
            f"estos handlers reciben updates de edición y crashean: {matcheados}"
        )


# ---------------------------------------------------------------------------
# M2 — estado seteado antes del reply: si el envío falla, queda colgado
# ---------------------------------------------------------------------------
#
# Patrón repetido en `input.py`: primero `context.user_data["pending_*"] = {...}`
# (y `transferred = True`, que desactiva el borrado del temporal en el `finally`)
# y recién después el `reply_text`/`edit_message_text` que dibuja los botones. Un
# `TimedOut`/`NetworkError` ahí —la falla más común de PTB— deja el estado vivo
# sin ningún teclado en pantalla: `_has_pending_keyboard` (o
# `_is_awaiting_text_input` para `pending_description`) hace que todo mensaje
# posterior reciba "Hay una acción pendiente" sin que haya nada que resolver.
# Único escape: `/reset`.


@pytest.fixture
def temporales(tmp_path: Path, monkeypatch) -> Path:
    """Redirige `tempfile` a un directorio propio para detectar temporales huérfanos.

    No se puede usar `tmp_path` a secas: el vault y el config.yaml de los
    fixtures viven ahí.
    """
    d = tmp_path / "descargas"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


class TestM2DocumentoConReplyRoto:
    """`handle_document`: los tres destinos setean el estado antes del reply.

    El `except` genérico de `:480` avisa del error pero no limpia el estado, y el
    `finally` no borra el temporal porque `transferred` ya está en True.
    """

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_pdf_con_reply_roto_no_deja_estado_colgado(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        from adso.handlers import input as input_mod

        update = make_update()
        update.message.document = _documento("paper.pdf")
        update.message.caption = None
        # El reply con el teclado [Ya lo leí]/[Lo quiero leer] falla; el aviso de
        # error del `except` sí sale (la red vuelve).
        update.message.reply_text = AsyncMock(
            side_effect=[RuntimeError("red caída"), MagicMock(message_id=2)]
        )

        await input_mod.handle_document(update, mock_context)

        assert not _has_pending_keyboard(mock_context), (
            "queda pending_read_status sin teclado: el bot rechaza todo input hasta /reset"
        )
        assert not list(temporales.iterdir()), "el temporal del PDF quedó huérfano"

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_texto_con_reply_roto_no_deja_estado_colgado(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        from adso.handlers import input as input_mod

        update = make_update()
        update.message.document = _documento("notas.txt")
        update.message.caption = None
        update.message.reply_text = AsyncMock(
            side_effect=[RuntimeError("red caída"), MagicMock(message_id=2)]
        )

        with patch.object(
            input_mod, "extract_text_file", AsyncMock(return_value="contenido del archivo")
        ):
            await input_mod.handle_document(update, mock_context)

        assert not _has_pending_keyboard(mock_context), (
            "queda pending_extraction sin teclado: el bot rechaza todo input hasta /reset"
        )
        assert not list(temporales.iterdir()), "el temporal del .txt quedó huérfano"

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_binario_con_reply_roto_no_deja_estado_colgado(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        from adso.handlers import input as input_mod

        update = make_update()
        update.message.document = _documento("diagrama.psd")
        update.message.caption = None
        update.message.reply_text = AsyncMock(
            side_effect=[RuntimeError("red caída"), MagicMock(message_id=2)]
        )

        await input_mod.handle_document(update, mock_context)

        # `pending_description` espera texto, así que el que bloquea es el otro guard.
        assert not _is_awaiting_text_input(mock_context), (
            "queda pending_description colgado: todo binario/audio posterior se rechaza"
        )
        assert not list(temporales.iterdir()), "el temporal del binario quedó huérfano"


class TestM2ImagenConReplyRoto:
    """`handle_photo` es el caso más crudo: el reply de `:537` no está dentro de
    ningún `try`, así que la excepción escapa del handler (la atrapa el error
    handler global) con `pending_fallback_pdf` ya seteado y el temporal en /tmp
    —que en la RPi4 es tmpfs: RAM filtrada hasta el reinicio—."""

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_imagen_con_reply_roto_no_deja_estado_colgado(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        from adso.handlers import input as input_mod

        photo = MagicMock()
        photo.file_size = 1000
        photo.file_unique_id = "abc123"
        photo.get_file = AsyncMock(return_value=_tg_file())

        update = make_update()
        update.message.photo = [photo]
        update.message.caption = None
        update.message.reply_text = AsyncMock(side_effect=RuntimeError("red caída"))

        await input_mod.handle_photo(update, mock_context)

        assert not _has_pending_keyboard(mock_context), (
            "queda pending_fallback_pdf sin teclado: el bot rechaza todo input hasta /reset"
        )
        assert not list(temporales.iterdir()), "el temporal de la imagen quedó huérfano"


class TestM2PdfExtraidoConEditRoto:
    """`_process_pdf_after_read_status` combina los dos modos de falla.

    `pending_extraction` se setea en `:594` y el `edit_message_text` del preview
    va en `:627`. Si falla, el `except` de `:634` avisa y **borra el temporal**
    (`tmp_path.unlink`) sin popear el estado: queda un `pending_extraction`
    apuntando a un archivo que ya no existe, con `_has_pending_keyboard` en True
    y sin ningún botón. Un `[Confirmar]` de un teclado fantasma leería ese path.
    """

    async def test_edit_roto_no_deja_estado_apuntando_a_un_temporal_borrado(
        self, mock_context, tmp_path: Path
    ) -> None:
        from adso.handlers import input as input_mod

        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_context.user_data["pending_read_status"] = {
            "temp_path": str(pdf),
            "original_filename": "paper.pdf",
            "media_type": "document",
            "user_context": None,
        }

        update = MagicMock()
        # 1) "Extrayendo texto del PDF..." 2) el preview (falla) 3) el aviso de error
        update.callback_query.edit_message_text = AsyncMock(
            side_effect=[MagicMock(), RuntimeError("red caída"), MagicMock()]
        )

        with patch.object(
            input_mod, "extract_pdf", AsyncMock(return_value=("texto del pdf", {"pages": 3}))
        ), patch.object(input_mod, "detect_paper", MagicMock(return_value=False)):
            await input_mod._process_pdf_after_read_status(update, mock_context, "unread")

        pendiente = mock_context.user_data.get("pending_extraction")
        if pendiente:
            assert Path(pendiente["temp_path"]).exists(), (
                "el estado sobrevive apuntando a un temporal que el except ya borró"
            )
        assert not _has_pending_keyboard(mock_context), (
            "queda pending_extraction sin teclado: el bot rechaza todo input hasta /reset"
        )
