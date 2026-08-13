"""Tests del bloque E de la auditoría 2026-07-31 — flujos de captura/UI.

E1 — el caption de un PDF (`user_context`) llega al LLM.
E3 — un error en OCR/Vision no deja el bot bloqueado sin teclado.
E4 — corrección `tag <algo>` se normaliza a kebab (no rompe el HTML del preview).
E5 — corrección `titulo` con salto de línea usa el grupo del match.
E6 — `pending_description` bloquea otro binario en vez de perderlo.
E7 — `/clasificar` via botón no crashea con AttributeError.
E8 — una nota de inbox sin body no bloquea la cola para siempre.
E9 — si falla el reply de la transcripción, no queda estado colgado.
E11 — un fallo de `save_resource` se avisa y no filtra el temporal.
E12 — `on_retry` que lanza no se saltea el modo degradado.

Casi todos son "el bot queda inutilizable hasta /reset" o "se pierde algo que
el usuario escribió": los dos modos de falla que la regla de oro del proyecto
prohíbe.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adso.bot_utils import _is_awaiting_text_input
from adso.handlers.capture import _apply_note_corrections, _apply_task_corrections


def _ctx(**user_data) -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = dict(user_data)
    return ctx


# ---------------------------------------------------------------------------
# E4 — tag sin sanitizar rompe el preview de forma irreversible
# ---------------------------------------------------------------------------


class TestE4TagSanitizado:
    """Un tag con `<` rompía el parse HTML de Telegram.

    El `edit` fallaba, el fallback `reply_text` fallaba igual, y como
    `awaiting_correction` ya estaba en False el usuario quedaba con un preview
    sin teclado que no se podía re-renderizar: solo `/reset`. Si llegaba a
    confirmar, el tag basura entraba al frontmatter.
    """

    def test_tag_con_html_se_normaliza(self) -> None:
        fm: dict = {"type": "reference"}
        _apply_note_corrections(fm, "tag <b>malicioso</b>", "tag <b>malicioso</b>")
        assert "<" not in "".join(fm["tags"])
        assert ">" not in "".join(fm["tags"])

    def test_tag_con_acentos_y_mayusculas_a_kebab(self) -> None:
        fm: dict = {"type": "reference"}
        _apply_note_corrections(fm, "tag Investigación", "tag investigación")
        assert fm["tags"] == ["investigacion"]

    def test_tag_en_tarea_tambien_se_normaliza(self) -> None:
        fm: dict = {"type": "task"}
        _apply_task_corrections(fm, "agregar tag <script>", "agregar tag <script>")
        assert "<" not in "".join(fm["tags"])

    def test_tag_normal_no_se_altera(self) -> None:
        fm: dict = {"type": "reference"}
        _apply_note_corrections(fm, "tag machine-learning", "tag machine-learning")
        assert fm["tags"] == ["machine-learning"]

    def test_tag_vacio_tras_normalizar_no_se_agrega(self) -> None:
        """`tag <>` no debe dejar un string vacío en la lista."""
        fm: dict = {"type": "reference"}
        _apply_note_corrections(fm, "tag <>", "tag <>")
        assert "" not in fm.get("tags", [])


# ---------------------------------------------------------------------------
# E5 — `titulo` con salto de línea
# ---------------------------------------------------------------------------


class TestE5TituloConSaltoDeLinea:
    """El regex matchea con `\\n` como separador pero la asignación
    re-spliteaba por espacio literal: sin espacios, IndexError."""

    def test_titulo_sin_espacios_no_lanza(self) -> None:
        fm: dict = {"type": "task"}
        _apply_task_corrections(fm, "titulo\nMitítulo", "titulo\nmitítulo")
        assert fm["title"] == "Mitítulo"

    def test_titulo_con_salto_toma_el_texto_completo(self) -> None:
        fm: dict = {"type": "task"}
        _apply_task_corrections(fm, "titulo\nMi título largo", "titulo\nmi título largo")
        assert fm["title"] == "Mi título largo"

    def test_titulo_con_espacio_sigue_funcionando(self) -> None:
        fm: dict = {"type": "task"}
        _apply_task_corrections(fm, "titulo Mi título", "titulo mi título")
        assert fm["title"] == "Mi título"

    def test_notas_exigen_espacio_y_no_crashean(self) -> None:
        """El parser de notas usa `startswith("titulo ")`, así que
        "titulo\\nX" simplemente no matchea — cae al fallback, sin IndexError.
        El bug era exclusivo del parser de tareas, que matchea con regex."""
        fm: dict = {"type": "reference"}
        assert _apply_note_corrections(fm, "titulo\nX", "titulo\nx") is False
        assert "title" not in fm


# ---------------------------------------------------------------------------
# E6 — pending_description no bloqueaba otro binario
# ---------------------------------------------------------------------------


class TestE6PendingDescription:
    """Mandar un segundo archivo mientras hay uno esperando descripción
    sobreescribía el estado: el primer temporal quedaba huérfano y el archivo
    se perdía sin aviso."""

    def test_pending_description_bloquea_input_binario(self) -> None:
        assert _is_awaiting_text_input(_ctx(pending_description={"temp_path": "/x"})) is True

    def test_sin_pending_description_no_bloquea(self) -> None:
        assert _is_awaiting_text_input(_ctx()) is False

    def test_pending_description_vacio_no_bloquea(self) -> None:
        assert _is_awaiting_text_input(_ctx(pending_description=None)) is False


# ---------------------------------------------------------------------------
# E7 / E8 — /clasificar
# ---------------------------------------------------------------------------


class TestE7ClasificarViaBoton:
    """Via `CB_CLASIFICAR_INBOX`, `update.message` es None.

    Dos ramas lo usaban igual: el guard de corrección pendiente y el de
    teclado pendiente. Ambas → AttributeError → error handler global con
    mensaje genérico, justo cuando el usuario apretó un botón del bot.
    """

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_guard_de_correccion_no_crashea(
        self, mock_context, make_callback_query
    ) -> None:
        from adso.handlers.commands import handle_clasificar

        update = make_callback_query("clasificar_inbox")
        mock_context.user_data["pending_note"] = {"awaiting_correction": True}

        await handle_clasificar(update, mock_context)

        update.callback_query.message.reply_text.assert_awaited()
        texto = update.callback_query.message.reply_text.await_args[0][0]
        assert "corrección pendiente" in texto

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_guard_de_teclado_pendiente_no_crashea(
        self, mock_context, make_callback_query
    ) -> None:
        from adso.handlers.commands import handle_clasificar

        update = make_callback_query("clasificar_inbox")
        mock_context.user_data["pending_report"] = {"algo": 1}

        await handle_clasificar(update, mock_context)

        update.callback_query.message.reply_text.assert_awaited()
        assert "acción pendiente" in (
            update.callback_query.message.reply_text.await_args[0][0]
        )


class TestE8NotaVaciaNoBloqueaLaCola:
    """`caso_b[0]` se elegía siempre en el mismo orden y una nota sin body
    hacía `return` (el mensaje decía "saltando", pero no saltaba). La misma
    nota vacía se elegía en cada invocación y las demás quedaban
    inalcanzables para siempre."""

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_salta_la_vacia_y_procesa_la_siguiente(
        self, mock_context, make_update, tmp_path: Path
    ) -> None:
        from adso.handlers import commands

        vacia = tmp_path / "vacia.md"
        buena = tmp_path / "buena.md"
        vacia.write_text("x", encoding="utf-8")
        buena.write_text("x", encoding="utf-8")

        refs = [MagicMock(path=vacia), MagicMock(path=buena)]
        notas = {
            vacia: MagicMock(frontmatter={"media_type": "text"}, body="   "),
            buena: MagicMock(frontmatter={"media_type": "text"}, body="contenido real"),
        }

        update = make_update("/clasificar")

        with patch.object(commands, "find_by_property", AsyncMock(return_value=refs)), \
             patch.object(commands, "read_note", AsyncMock(side_effect=lambda p: notas[p])), \
             patch.object(commands, "_get_existing_items", AsyncMock(return_value=([], []))), \
             patch.object(commands, "_get_existing_tags", AsyncMock(return_value=[])), \
             patch.object(commands, "classify", AsyncMock(return_value={
                 "mode": "capture",
                 "payload": {"frontmatter": {"title": "T", "type": "reference"}, "body": "b"},
             })) as mock_classify:
            await commands.handle_clasificar(update, mock_context)

        # Se saltó la vacía y clasificó la siguiente, en la MISMA invocación.
        mock_classify.assert_awaited_once()
        assert mock_classify.await_args.kwargs["content"] == "contenido real"
        assert mock_context.user_data["clasificar_inbox_path"] == str(buena)

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_todas_vacias_informa_y_no_cuelga(
        self, mock_context, make_update, tmp_path: Path
    ) -> None:
        from adso.handlers import commands

        vacia = tmp_path / "vacia.md"
        vacia.write_text("x", encoding="utf-8")
        refs = [MagicMock(path=vacia)]
        nota = MagicMock(frontmatter={}, body="")

        update = make_update("/clasificar")

        with patch.object(commands, "find_by_property", AsyncMock(return_value=refs)), \
             patch.object(commands, "read_note", AsyncMock(return_value=nota)):
            await commands.handle_clasificar(update, mock_context)

        textos = " ".join(c[0][0] for c in update.message.reply_text.await_args_list)
        assert "contenido" in textos
        assert "vacia.md" in textos, "el mensaje debe nombrar la nota problemática"


# ---------------------------------------------------------------------------
# E1 — el caption del PDF se perdía siempre
# ---------------------------------------------------------------------------


class TestE1CaptionDePdf:
    """`handle_document` guardaba `user_context: msg.caption`, pero
    `_process_pdf_after_read_status` no lo copiaba a `pending_extraction` ni al
    fallback de PDFs escaneados. `_cb_extraction_ok` leía siempre None: lo que
    el usuario escribió junto al PDF nunca llegaba al LLM."""

    @pytest.mark.asyncio
    async def test_caption_llega_a_pending_extraction(self, mock_context) -> None:
        from adso.handlers import input as input_mod

        mock_context.user_data["pending_read_status"] = {
            "temp_path": "/tmp/x.pdf",
            "original_filename": "x.pdf",
            "media_type": "document",
            "user_context": "esto es para la tesis",
        }
        update = MagicMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch.object(
            input_mod, "extract_pdf", AsyncMock(return_value=("texto del pdf", {}))
        ), patch.object(input_mod, "detect_paper", MagicMock(return_value=False)), \
             patch.object(input_mod, "build_classify_content", MagicMock(return_value="c")):
            await input_mod._process_pdf_after_read_status(update, mock_context, "unread")

        assert mock_context.user_data["pending_extraction"]["user_context"] == (
            "esto es para la tesis"
        )

    @pytest.mark.asyncio
    async def test_caption_llega_al_fallback_de_pdf_escaneado(self, mock_context) -> None:
        from adso.handlers import input as input_mod

        mock_context.user_data["pending_read_status"] = {
            "temp_path": "/tmp/x.pdf",
            "original_filename": "x.pdf",
            "media_type": "document",
            "user_context": "paper de Fulano",
        }
        update = MagicMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch.object(input_mod, "extract_pdf", AsyncMock(return_value=("", {}))):
            await input_mod._process_pdf_after_read_status(update, mock_context, "read")

        assert mock_context.user_data["pending_fallback_pdf"]["user_context"] == (
            "paper de Fulano"
        )


# ---------------------------------------------------------------------------
# E12 — on_retry que lanza se saltaba el modo degradado
# ---------------------------------------------------------------------------


class TestE12OnRetryQueLanza:
    """El `on_retry` de captura hace `edit_message_text`, que puede fallar por
    red — plausible justo cuando Gemini no responde. Esa excepción abortaba
    `classify()` sin pasar por el fallback degradado, y como los `_cb_intent_*`
    ya habían popeado `pending_raw_content`, el texto del usuario se perdía."""

    @pytest.mark.asyncio
    async def test_on_retry_que_lanza_no_aborta_classify(self) -> None:
        from adso import llm_client

        async def on_retry_roto(attempt: int, total: int) -> None:
            raise RuntimeError("red caída")

        with patch.object(
            llm_client, "_call_gemini", AsyncMock(side_effect=RuntimeError("gemini caído"))
        ), patch.object(llm_client, "_try_groq_fallback", AsyncMock(return_value=None)), \
             patch.object(llm_client.asyncio, "sleep", AsyncMock()):
            result = await llm_client.classify(
                content="algo que el usuario escribió",
                media_type="text",
                existing_projects=[],
                existing_areas=[],
                existing_tags=[],
                on_retry=on_retry_roto,
            )

        assert result["mode"] == "degraded"
        assert "algo que el usuario escribió" in str(result)


# ---------------------------------------------------------------------------
# E3 / E9 — estado colgado que bloquea el bot hasta /reset
# ---------------------------------------------------------------------------


class TestE3ErrorEnOcrNoBloquea:
    """Ante excepción, `_cb_ocr`/`_cb_vision` editaban el mensaje con el error
    (sin teclado) y retornaban sin limpiar `pending_fallback_pdf`.
    `_has_pending_keyboard` seguía en True → todo input posterior recibía "Hay
    una acción pendiente" cuando ya no había botones. Única salida: `/reset`,
    que borra el temporal — había que reenviar la imagen."""

    @pytest.mark.asyncio
    async def test_error_en_ocr_limpia_el_estado(self, mock_context, tmp_path: Path) -> None:
        from adso.handlers import callbacks

        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        mock_context.user_data["pending_fallback_pdf"] = {
            "temp_path": str(img),
            "original_filename": "x.png",
            "media_type": "image",
        }

        update = MagicMock()
        update.callback_query.edit_message_text = AsyncMock()
        update.callback_query.answer = AsyncMock()

        import asyncio as _asyncio
        with patch.object(
            _asyncio, "to_thread", AsyncMock(side_effect=RuntimeError("tesseract roto"))
        ):
            await callbacks._cb_ocr(update, mock_context)

        from adso.bot_utils import _has_pending_keyboard
        assert _has_pending_keyboard(mock_context) is False, (
            "el estado quedó colgado: el bot rechaza todo input sin mostrar botones"
        )


class TestE9TranscripcionConReplyRoto:
    """`pending_transcript` se seteaba antes del `reply_text`; si el reply
    lanzaba, el `except` borraba el temporal pero dejaba el estado apuntando a
    un archivo que ya no existe."""

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_reply_roto_no_deja_estado_colgado(
        self, mock_context, make_update, tmp_path: Path
    ) -> None:
        from adso.handlers import input as input_mod

        update = make_update()
        tg_file = MagicMock()
        tg_file.file_path = "a.ogg"
        tg_file.download_to_drive = AsyncMock()
        voice = MagicMock(file_id="f", file_size=1000)
        voice.get_file = AsyncMock(return_value=tg_file)
        update.message.voice = voice
        update.message.audio = None
        # El primer reply ("Transcribiendo audio...") funciona; el que muestra
        # la transcripción falla — es el orden real de una red que se cae.
        update.message.reply_text = AsyncMock(
            side_effect=[
                MagicMock(message_id=9),
                RuntimeError("red caída"),
                MagicMock(message_id=10),  # el aviso de error del except
            ]
        )

        with patch.object(input_mod, "transcribe_audio", AsyncMock(return_value="hola")), \
             patch.object(
                 input_mod, "_exceeds_size_after_download", AsyncMock(return_value=False)
             ):
            await input_mod.handle_audio(update, mock_context)

        assert not mock_context.user_data.get("pending_transcript"), (
            "queda un pending_transcript apuntando a un temporal ya borrado"
        )


# ---------------------------------------------------------------------------
# E11 — fallo de save_resource silencioso
# ---------------------------------------------------------------------------


class TestE11AdjuntoQueFalla:
    """El `except` solo logueaba: el usuario veía "Nota guardada" sin saber que
    el adjunto no se copió, y el temporal no se borraba (el unlink estaba solo
    en el camino feliz)."""

    @pytest.mark.asyncio
    async def test_avisa_al_usuario_y_borra_el_temporal(
        self, mock_context, vault_path: Path, tmp_path: Path
    ) -> None:
        from adso.handlers import capture

        temp = tmp_path / "adjunto.png"
        temp.write_bytes(b"contenido")

        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "Con adjunto", "type": "reference"},
                "body": "cuerpo",
                "suggested_links": [],
            },
            "_resource_file": {"temp_path": str(temp), "filename": "adjunto.png"},
        }

        query = MagicMock()
        query.edit_message_text = AsyncMock()

        with patch.object(
            capture, "save_resource", AsyncMock(side_effect=OSError("disco lleno"))
        ):
            await capture._cb_confirm(query, mock_context, vault_path)

        mensaje = query.edit_message_text.await_args[0][0]
        assert "Nota guardada" in mensaje
        assert "adjunto.png" in mensaje and "no se" in mensaje, (
            "el usuario debe enterarse de que el adjunto no se copió"
        )
        assert not temp.exists(), "el temporal quedó filtrado"


# ---------------------------------------------------------------------------
# E2 — el read_status elegido se perdía para PDFs escaneados
# ---------------------------------------------------------------------------


class TestE2ReadStatusDePdfEscaneado:
    """`pending_fallback_pdf` lleva el `read_status` que el usuario eligió con
    `[Ya lo leí]`/`[Lo quiero leer]`, pero `_cb_ocr`/`_cb_vision` armaban
    `pending_transcript` sin copiarlo y `_cb_transcript_ok` no lo pasaba como
    `extra_fm`. El paper escaneado terminaba sin `read_status` en el
    frontmatter pese a la elección explícita."""

    def _pending(self, tmp_path: Path) -> dict:
        img = tmp_path / "scan.png"
        img.write_bytes(b"fake")
        return {
            "temp_path": str(img),
            "original_filename": "scan.png",
            "media_type": "image",
            "read_status": "unread",
        }

    @pytest.mark.asyncio
    async def test_ocr_propaga_read_status(self, mock_context, tmp_path: Path) -> None:
        from adso.handlers import callbacks

        mock_context.user_data["pending_fallback_pdf"] = self._pending(tmp_path)
        update = MagicMock()
        update.callback_query.edit_message_text = AsyncMock(
            return_value=MagicMock(message_id=1)
        )

        import asyncio as _asyncio
        with patch.object(_asyncio, "to_thread", AsyncMock(return_value="texto ocr")):
            await callbacks._cb_ocr(update, mock_context)

        assert mock_context.user_data["pending_transcript"]["read_status"] == "unread"

    @pytest.mark.asyncio
    async def test_transcript_ok_lo_manda_como_extra_fm(
        self, mock_context, tmp_path: Path
    ) -> None:
        from adso.handlers import capture

        mock_context.user_data["pending_transcript"] = {
            "text": "texto del scan",
            "media_type": "image",
            "read_status": "read",
        }
        update = MagicMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch.object(capture, "_classify_and_preview", AsyncMock()) as mock_cp:
            await capture._cb_transcript_ok(update, mock_context)

        assert mock_cp.await_args.kwargs["extra_fm"] == {"read_status": "read"}

    @pytest.mark.asyncio
    async def test_sin_read_status_no_inventa_extra_fm(
        self, mock_context, tmp_path: Path
    ) -> None:
        """Una imagen común (sin flujo de PDF) no debe recibir read_status."""
        from adso.handlers import capture

        mock_context.user_data["pending_transcript"] = {
            "text": "una foto cualquiera",
            "media_type": "image",
        }
        update = MagicMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch.object(capture, "_classify_and_preview", AsyncMock()) as mock_cp:
            await capture._cb_transcript_ok(update, mock_context)

        assert mock_cp.await_args.kwargs.get("extra_fm") is None


# ---------------------------------------------------------------------------
# E10 — "Error al guardar" falso tras un guardado exitoso
# ---------------------------------------------------------------------------


class TestE10ErrorFalsoTrasGuardar:
    """Si el edit final falla por red DESPUÉS de que `create_note` escribió, el
    except de `handle_callback` reportaba "Error al guardar" —falso: la nota, el
    push a Tasks y el indexado ya habían corrido— e intentaba otro edit por la
    misma red caída."""

    @pytest.mark.asyncio
    async def test_fallo_del_edit_final_no_se_reporta_como_fallo_de_guardado(
        self, mock_context, vault_path: Path
    ) -> None:
        from adso.handlers import capture

        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "Nota que sí se guarda", "type": "reference"},
                "body": "cuerpo",
                "suggested_links": [],
            },
        }

        query = MagicMock()
        query.edit_message_text = AsyncMock(side_effect=RuntimeError("red caída"))

        # No debe propagar: si propaga, handle_callback miente al usuario.
        await capture._cb_confirm(query, mock_context, vault_path)

        escritas = list(vault_path.rglob("*.md"))
        assert escritas, "la nota debía quedar escrita"
        assert "pending_note" not in mock_context.user_data, (
            "el estado debe quedar limpio: la escritura fue exitosa"
        )
