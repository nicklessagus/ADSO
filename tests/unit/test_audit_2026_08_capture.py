"""Reproductores de los bugs de la auditoría 2026-08-22 — flujo de captura.

Mismo contrato que `test_audit_2026_08_vault.py`: cada test **especifica el
comportamiento correcto**. Los once bugs están arreglados, así que las marcas
`xfail(strict=True)` con las que nacieron ya se sacaron: de acá en adelante son
tests de regresión y cualquier falla es un bug reintroducido.

Issues (todos cerrados):
  C1  — el aviso de "Error al guardar" borra el teclado y mata el reintento.
  C2  — nadie guarda `pending_note["msg_id"]`, así que el guard G14 no aplica.
  C3  — `pop` del estado ANTES del edit: si el edit falla, el texto se pierde.
  C4  — `_cb_vision` desde OCR deja `pending_transcript` vivo sin botones.
  C5  — `_cb_vision` desde OCR pierde el `read_status` elegido.
  C6  — un `mode=manage` del LLM descarta contenido que el usuario ya confirmó.
  C7  — `status` incoherente con `type` tras corregir el tipo o el destino.
  C8  — doble tap sin estado destruye el preview vigente con sus botones.
  C9  — `summary` no-string del fallback de Groq revienta el flujo de arXiv.
  C10 — `_index.md` con `project:` vacío rompe el selector de proyectos.
  C11 — "Esa área ya no existe" borra el selector y deja la nota sin botones.

El hilo común es el de siempre en este proyecto: o se pierde algo que el
usuario escribió (regla de oro: sin pérdida de datos), o el bot queda con
estado pendiente y sin botones — inutilizable hasta `/reset`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import TimedOut

from adso.constants import CB_CONFIRM, CB_DEST_AREA_PREFIX


def _markup_de(mock_edit: AsyncMock):
    """Devuelve el `reply_markup` del último `edit_message_text` (o None)."""
    if not mock_edit.await_args_list:
        return None
    return mock_edit.await_args.kwargs.get("reply_markup")


def _callback_datas(markup) -> list[str]:
    """Aplana los `callback_data` de un InlineKeyboardMarkup."""
    if markup is None:
        return []
    return [btn.callback_data for fila in markup.inline_keyboard for btn in fila]


# ---------------------------------------------------------------------------
# C1 — el error de guardado borra el teclado, así que el reintento es inalcanzable
# ---------------------------------------------------------------------------
#
# El fix A2 dejó el estado en `user_data` cuando `create_note` falla, para que un
# segundo [Confirmar] reintente (regla de oro: el texto de audio/OCR/Vision no
# existe en ningún otro lado). Pero el `except` de `handle_callback` responde con
# `edit_message_text(...)` SIN `reply_markup`: Telegram interpreta la ausencia
# como "sacar el teclado", así que el mensaje queda sin botón [Confirmar] y el
# reintento que el fix prometió no se puede disparar. El estado sobrevive, sí,
# pero muerto: `_has_pending_keyboard` bloquea todo input nuevo.


class TestC1ErrorDeGuardadoConservaElBotonDeReintento:
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_error_al_guardar_repone_el_boton_confirmar(
        self, mock_context, make_callback_query
    ) -> None:
        from adso.handlers import callbacks, capture

        update = make_callback_query(CB_CONFIRM)
        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "Texto de un audio", "type": "reference"},
                "body": "lo único que existe de esta captura",
                "suggested_links": [],
            },
        }

        with patch.object(
            capture, "create_note", AsyncMock(side_effect=OSError("no space left on device"))
        ):
            await callbacks.handle_callback(update, mock_context)

        edit = update.callback_query.edit_message_text
        assert edit.await_args_list, "el usuario tiene que enterarse del fallo"
        assert "Error al guardar" in edit.await_args[0][0]
        assert CB_CONFIRM in _callback_datas(_markup_de(edit)), (
            "sin botón [Confirmar] el reintento del fix A2 es inalcanzable: "
            "el estado sigue en user_data pero no hay forma de dispararlo"
        )

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_el_estado_sobrevive_al_fallo(
        self, mock_context, make_callback_query
    ) -> None:
        """Contra-caso: la mitad del fix A2 que SÍ está — el estado no se popea."""
        from adso.handlers import callbacks, capture

        update = make_callback_query(CB_CONFIRM)
        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "Texto de un audio", "type": "reference"},
                "body": "cuerpo",
                "suggested_links": [],
            },
        }

        with patch.object(
            capture, "create_note", AsyncMock(side_effect=OSError("no space left"))
        ):
            await callbacks.handle_callback(update, mock_context)

        assert mock_context.user_data.get("pending_note")


# ---------------------------------------------------------------------------
# C2 — el guard del preview vigente nunca se activa
# ---------------------------------------------------------------------------
#
# `_cb_confirm` compara `pending["msg_id"]` con el `message_id` del callback para
# rechazar un [Confirmar] de un preview viejo (G14). Pero el único lugar que
# escribe esa clave es `_cb_note_correct`: ningún sitio que RENDERIZA el preview
# la guarda. Con `msg_id` ausente el guard se saltea a propósito ("estado de una
# versión anterior del bot"), así que en la práctica G14 solo protege a las notas
# que además pasaron por [Corregir]. El fix va donde se manda el preview.


class TestC2PreviewGuardaSuMsgId:
    async def test_classify_and_preview_registra_el_msg_id(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import capture

        update = make_update("comprar pan")
        update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=777))

        with patch.object(
            capture,
            "classify",
            AsyncMock(
                return_value={
                    "mode": "capture",
                    "confidence": 0.9,
                    "payload": {
                        "frontmatter": {"title": "Comprar pan", "type": "reference"},
                        "body": "comprar pan",
                    },
                }
            ),
        ):
            await capture._classify_and_preview(
                update, mock_context, "comprar pan", media_type="text"
            )

        pending = mock_context.user_data["pending_note"]
        assert pending.get("msg_id") == 777, (
            "sin msg_id el guard G14 de _cb_confirm se saltea siempre"
        )


# ---------------------------------------------------------------------------
# C3 — `pop` del estado antes del edit: si el edit falla, el texto se pierde
# ---------------------------------------------------------------------------
#
# `_cb_transcript_ok` y `_cb_extraction_ok` popean el estado al ENTRAR y recién
# después editan el mensaje ("Clasificando...") y llaman al LLM. Un `TimedOut` en
# ese edit —la falla más común de PTB— propaga con el estado ya consumido: el
# texto de OCR/Vision/extracción no existe en ningún otro lado y el temporal del
# adjunto queda huérfano en /tmp (que en la RPi4 es tmpfs: RAM filtrada hasta el
# reinicio). Mismo patrón que la regla "crear antes de descartar" de `_cb_confirm`.


class TestC3PopAntesDelEdit:
    async def test_transcript_ok_no_pierde_el_texto_si_falla_el_edit(
        self, mock_context, tmp_path: Path
    ) -> None:
        from adso.handlers import capture

        img = tmp_path / "scan.png"
        img.write_bytes(b"fake-png")
        mock_context.user_data["pending_transcript"] = {
            "text": "texto que salió del OCR y no existe en ningún otro lado",
            "media_type": "image",
            "resource_file": {"temp_path": str(img), "filename": "scan.png"},
        }

        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock(side_effect=TimedOut())

        try:
            await capture._cb_transcript_ok(update, mock_context)
        except TimedOut:
            pass

        assert mock_context.user_data.get("pending_transcript"), (
            "el texto de OCR se evaporó por un timeout de red al editar el mensaje"
        )
        assert img.exists(), "el temporal del adjunto quedó huérfano"

    async def test_extraction_ok_no_pierde_el_texto_si_falla_el_edit(
        self, mock_context, tmp_path: Path
    ) -> None:
        from adso.handlers import capture

        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mock_context.user_data["pending_extraction"] = {
            "text": "texto extraído del PDF",
            "media_type": "document",
            "temp_path": str(pdf),
            "original_filename": "paper.pdf",
        }

        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock(side_effect=TimedOut())

        try:
            await capture._cb_extraction_ok(update, mock_context)
        except TimedOut:
            pass

        assert mock_context.user_data.get("pending_extraction"), (
            "la extracción se perdió por un timeout de red al editar el mensaje"
        )
        assert pdf.exists(), "el temporal del PDF quedó huérfano"


# ---------------------------------------------------------------------------
# C4 — error de Vision desde el resultado OCR deja el bot muerto
# ---------------------------------------------------------------------------
#
# El `except` de `_cb_vision` está escrito para el camino `pending_fallback_pdf`:
# popea esa clave y borra el temporal. Pero cuando se llega desde el resultado
# OCR (`from_ocr`), el estado vivo es `pending_transcript` — el `pop` es un no-op,
# el temporal SÍ se borra, y el mensaje de error va sin `reply_markup`. Resultado:
# `_has_pending_keyboard` sigue en True y todo input posterior recibe "Hay una
# acción pendiente" cuando ya no queda ningún botón. Exactamente el dead-end que
# E3 arregló para el otro camino.


class TestC4VisionDesdeOcrNoDejaDeadEnd:
    async def test_error_de_vision_desde_ocr_no_bloquea_el_bot(
        self, mock_context, tmp_path: Path
    ) -> None:
        from adso import llm_client
        from adso.bot_utils import _has_pending_keyboard
        from adso.handlers import callbacks

        img = tmp_path / "scan.png"
        img.write_bytes(b"fake-png")
        mock_context.user_data["pending_transcript"] = {
            "text": "texto del OCR",
            "media_type": "image",
            "resource_file": {"temp_path": str(img), "filename": "scan.png"},
        }

        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch.object(
            llm_client,
            "describe_image_with_vision",
            AsyncMock(side_effect=RuntimeError("Vision caído")),
        ):
            await callbacks._cb_vision(update, mock_context)

        edit = update.callback_query.edit_message_text
        sin_estado_colgado = not _has_pending_keyboard(mock_context)
        repone_teclado = _markup_de(edit) is not None and img.exists()

        assert sin_estado_colgado or repone_teclado, (
            "dead-end: queda estado pendiente sin botones y sin el temporal para "
            "reintentar — el bot rechaza todo input hasta /reset"
        )


# ---------------------------------------------------------------------------
# C5 — el read_status elegido se pierde al pasar de OCR a Vision
# ---------------------------------------------------------------------------
#
# El dict `pending` que `_cb_vision` reconstruye desde `pending_transcript` copia
# `temp_path`, `media_type`, `original_filename` y `user_context`, pero NO
# `read_status`. Después, el nuevo `pending_transcript` se arma con
# `pending.get("read_status")` → siempre None. El usuario eligió [Ya lo leí] /
# [Lo quiero leer] antes de que el PDF resultara escaneado, y esa elección
# explícita se evapora si además pide Vision tras ver el OCR. Es el mismo hueco
# que E2 cerró en los otros dos caminos.


class TestC5VisionDesdeOcrPreservaReadStatus:
    async def test_read_status_sobrevive_al_pasar_de_ocr_a_vision(
        self, mock_context, tmp_path: Path
    ) -> None:
        from adso import llm_client
        from adso.handlers import callbacks

        img = tmp_path / "scan.png"
        img.write_bytes(b"fake-png")
        mock_context.user_data["pending_transcript"] = {
            "text": "texto del OCR",
            "media_type": "image",
            "read_status": "unread",
            "resource_file": {"temp_path": str(img), "filename": "scan.png"},
        }

        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch.object(
            llm_client,
            "describe_image_with_vision",
            AsyncMock(return_value="descripción de Gemini Vision"),
        ):
            await callbacks._cb_vision(update, mock_context)

        assert mock_context.user_data["pending_transcript"]["read_status"] == "unread", (
            "la elección explícita de [Lo quiero leer] se perdió al pedir Vision"
        )

    async def test_el_texto_de_vision_si_reemplaza_al_del_ocr(
        self, mock_context, tmp_path: Path
    ) -> None:
        """Contra-caso: lo que el camino sí hace bien (no es el bug)."""
        from adso import llm_client
        from adso.handlers import callbacks

        img = tmp_path / "scan.png"
        img.write_bytes(b"fake-png")
        mock_context.user_data["pending_transcript"] = {
            "text": "texto del OCR",
            "media_type": "image",
            "resource_file": {"temp_path": str(img), "filename": "scan.png"},
        }

        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch.object(
            llm_client,
            "describe_image_with_vision",
            AsyncMock(return_value="descripción de Gemini Vision"),
        ):
            await callbacks._cb_vision(update, mock_context)

        pt = mock_context.user_data["pending_transcript"]
        assert pt["text"] == "descripción de Gemini Vision"
        assert pt["resource_file"]["temp_path"] == str(img)


# ---------------------------------------------------------------------------
# C6 — el LLM descarta contenido que el usuario ya confirmó querer guardar
# ---------------------------------------------------------------------------
#
# `_classify_and_preview` respeta el `mode` del LLM salvo que el caller pase
# `force_capture=True`. Los flujos que llegan DESPUÉS de un [Confirmar] explícito
# del usuario (texto extraído de un PDF, transcripción de audio, OCR aceptado) no
# lo pasan. Si el LLM devuelve `mode=manage` —fácil: un PDF que hable de "crear
# un proyecto de X" dispara las keywords— el flujo responde "No se interpretó el
# mensaje como una nota para guardar", el estado ya fue popeado y el texto
# extraído se pierde entero. El usuario ya había dicho que quería guardarlo.


class TestC6ContenidoConfirmadoNoSeDescarta:
    async def test_mode_manage_no_descarta_la_extraccion(self, mock_context) -> None:
        from adso.handlers import capture

        mock_context.user_data["pending_extraction"] = {
            "text": "El plan es crear un proyecto de divulgación para el año que viene.",
            "media_type": "document",
        }

        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch.object(
            capture,
            "classify",
            AsyncMock(
                return_value={
                    "mode": "manage",
                    "confidence": 0.9,
                    "payload": {
                        "operation": "create_project",
                        "params": {"name": "divulgación"},
                    },
                }
            ),
        ):
            await capture._cb_extraction_ok(update, mock_context)

        assert mock_context.user_data.get("pending_note"), (
            "el texto extraído se descartó pese a que el usuario ya había "
            "confirmado que quería guardarlo"
        )


# ---------------------------------------------------------------------------
# C7 — `status` incoherente con `type`
# ---------------------------------------------------------------------------
#
# `VALID_STATUS` (llm_schema.py) define estados disjuntos por tipo: una `task`
# admite {pending, in-progress, done, pending-classification} y nunca `active` ni
# `raw`. Dos caminos de UI dejan la combinación inválida:
#   (a) `_apply_note_corrections` cambia `type` a `task` sin tocar `status`, que
#       venía en `active` de la nota `reference`.
#   (b) `_cb_dest` remapea `pending-classification` con
#       `"active" if type=="reference" else "raw"` — el `else` mete `raw`
#       (estado de idea) en cualquier task.
# Ninguno de los dos vuelve a pasar por `_validate_capture_payload`, así que el
# frontmatter inválido se escribe al vault tal cual y los reportes y filtros por
# `status` dejan de ver esa tarea.


class TestC7StatusCoherenteConType:
    def test_corregir_a_tarea_ajusta_el_status(self) -> None:
        from adso.handlers.capture import _apply_note_corrections

        fm: dict = {"type": "reference", "status": "active"}
        _apply_note_corrections(fm, "tipo tarea", "tipo tarea")

        assert fm["type"] == "task"
        assert fm["status"] == "pending", (
            f"status {fm['status']!r} es inválido para una task (VALID_STATUS)"
        )

    async def test_elegir_destino_no_deja_una_task_en_raw(self, mock_context) -> None:
        from adso.handlers import capture

        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {
                    "title": "Mandar el informe",
                    "type": "task",
                    "status": "pending-classification",
                },
                "body": "mandar el informe",
                "suggested_links": [],
            },
        }

        query = MagicMock()
        query.edit_message_text = AsyncMock()

        await capture._cb_dest(query, mock_context, dest_type="project", dest_name="tesis")

        fm = mock_context.user_data["pending_note"]["payload"]["frontmatter"]
        assert fm["status"] == "pending", (
            f"status {fm['status']!r} es inválido para una task (VALID_STATUS)"
        )

    async def test_el_remap_de_reference_e_idea_sigue_bien(self, mock_context) -> None:
        """Contra-caso: los dos tipos que el remap sí resuelve correctamente."""
        from adso.handlers import capture
        from adso.llm_schema import VALID_STATUS

        for note_type in ("reference", "idea"):
            mock_context.user_data["pending_note"] = {
                "payload": {
                    "frontmatter": {
                        "title": "X",
                        "type": note_type,
                        "status": "pending-classification",
                    },
                    "body": "x",
                    "suggested_links": [],
                },
            }
            query = MagicMock()
            query.edit_message_text = AsyncMock()

            await capture._cb_dest(query, mock_context, dest_type="area", dest_name="docencia")

            fm = mock_context.user_data["pending_note"]["payload"]["frontmatter"]
            assert fm["status"] in VALID_STATUS[note_type]


# ---------------------------------------------------------------------------
# C8 — el doble tap destruye el preview vigente
# ---------------------------------------------------------------------------
#
# Con lag de red el usuario toca [Confirmar] dos veces. El primer tap consume el
# estado y deja el PREVIEW de la nota (con teclado) en ese mismo mensaje; el
# segundo entra sin estado y hace `edit_message_text("No hay transcripción
# pendiente.")` — sobre el mensaje del callback, que a esa altura es justamente el
# preview vigente. El teclado desaparece y el `pending_note` recién creado queda
# sin botones. Un aviso efímero de "no hay nada pendiente" es exactamente para lo
# que existe `query.answer(..., show_alert=True)`, que es lo que ya hacen
# `_cb_ocr`, `_cb_vision` y `_cb_arxiv_create_anyway`.


class TestC8DobleTapNoDestruyeElPreview:
    async def test_transcript_ok_sin_estado_no_edita_el_mensaje(
        self, mock_context
    ) -> None:
        from adso.handlers import capture

        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        await capture._cb_transcript_ok(update, mock_context)

        update.callback_query.edit_message_text.assert_not_awaited()
        update.callback_query.answer.assert_awaited()

    async def test_extraction_ok_sin_estado_no_edita_el_mensaje(
        self, mock_context
    ) -> None:
        from adso.handlers import capture

        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        await capture._cb_extraction_ok(update, mock_context)

        update.callback_query.edit_message_text.assert_not_awaited()
        update.callback_query.answer.assert_awaited()


# ---------------------------------------------------------------------------
# C9 — `summary` no-string revienta el flujo de arXiv
# ---------------------------------------------------------------------------
#
# `_classify_and_preview_arxiv` hace `(payload.get("summary") or "").strip()`. El
# sanitizador coacciona `title`, `tags`, `year`, `authors`, `keywords`,
# `read_status`, `confidence` y `body` justamente porque el fallback de Groq no
# tiene schema constrained — pero `summary` quedó fuera. Un dict o una lista ahí
# es truthy, el `or` lo deja pasar y `.strip()` lanza AttributeError: la captura
# del paper muere con el error handler global.


class TestC9SummaryNoString:
    @pytest.mark.parametrize("basura", [{"x": 1}, ["a", "b"], 42])
    def test_summary_no_string_se_sanea(self, basura) -> None:
        from adso.llm_schema import validate_llm_response

        respuesta = validate_llm_response({
            "mode": "capture",
            "confidence": 0.9,
            "payload": {
                "frontmatter": {"title": "Un paper", "type": "reference"},
                "body": "cuerpo",
                "summary": basura,
            },
        })

        summary = respuesta["payload"].get("summary")
        assert summary is None or isinstance(summary, str), (
            "`(summary or '').strip()` en _classify_and_preview_arxiv lanza "
            f"AttributeError con {type(basura).__name__}"
        )

    def test_summary_string_pasa_intacto(self) -> None:
        """Contra-caso: el camino normal no debe alterarse."""
        from adso.llm_schema import validate_llm_response

        respuesta = validate_llm_response({
            "mode": "capture",
            "confidence": 0.9,
            "payload": {
                "frontmatter": {"title": "Un paper", "type": "reference"},
                "body": "cuerpo",
                "summary": "Resumen breve del paper.",
            },
        })
        assert respuesta["payload"]["summary"] == "Resumen breve del paper."


# ---------------------------------------------------------------------------
# C10 — `_index.md` con la clave presente pero vacía rompe el selector
# ---------------------------------------------------------------------------
#
# `_read_index` hace `note.frontmatter.get(field, name)`: el default de `.get`
# solo aplica si la clave FALTA. Un `_index.md` con `project:` a secas (YAML lo
# parsea como None) —trivial de producir editando el índice desde Obsidian— hace
# que el item quede como `{"name": None}`. `item_token(None)` llama a
# `None.encode("utf-8")` → AttributeError al construir el teclado, así que
# [Elegir proyecto] muere con el error handler global y el proyecto entero se
# vuelve inalcanzable como destino.


class TestC10IndexConCampoVacio:
    def _vault_con_index_vacio(self, vault_path: Path) -> None:
        from adso import vault_cache

        vault_cache.clear()
        index = vault_path / "01-Projects" / "Tesis" / "_index.md"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(
            "---\ntitle: Tesis\ntype: project-index\nproject:\ndescription: Doctorado\n---\n\n",
            encoding="utf-8",
        )

    async def test_cae_al_nombre_del_directorio(self, vault_path: Path) -> None:
        from adso.bot_utils import _get_existing_items

        self._vault_con_index_vacio(vault_path)
        projects, _ = await _get_existing_items(vault_path)

        assert projects[0]["name"] == "Tesis"

    async def test_el_selector_de_proyectos_no_lanza(self, vault_path: Path) -> None:
        from adso.keyboards import build_project_selector

        self._vault_con_index_vacio(vault_path)
        teclado = await build_project_selector(vault_path)

        etiquetas = [btn.text for fila in teclado.inline_keyboard for btn in fila]
        assert "Tesis" in etiquetas

    async def test_index_bien_formado_sigue_andando(self, vault_path: Path) -> None:
        """Contra-caso: con `project:` poblado el nombre del índice manda."""
        from adso import vault_cache
        from adso.bot_utils import _get_existing_items

        vault_cache.clear()
        index = vault_path / "01-Projects" / "tesis" / "_index.md"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(
            "---\ntitle: Tesis\nproject: Tesis doctoral\ndescription: D\n---\n\n",
            encoding="utf-8",
        )

        projects, _ = await _get_existing_items(vault_path)
        assert projects[0]["name"] == "Tesis doctoral"


# ---------------------------------------------------------------------------
# C11 — "Esa área ya no existe" borra el selector
# ---------------------------------------------------------------------------
#
# Si el área/proyecto se borró entre que se dibujó el selector y el usuario tocó
# el botón, `resolve_item_token` devuelve None y el dispatcher edita el mensaje
# con el aviso — sin `reply_markup`. El `pending_note` sigue vivo (correcto: la
# nota no se perdió) pero el mensaje que lo acompañaba se quedó sin ningún botón,
# y el propio texto le dice al usuario "Elegir otro destino" cuando ya no hay
# nada que elegir. `_has_pending_keyboard` bloquea todo input: solo `/reset`.


class TestC11DestinoInexistenteConservaElSelector:
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_area_borrada_repone_botones(
        self, mock_context, make_callback_query
    ) -> None:
        from adso.handlers import callbacks

        update = make_callback_query(CB_DEST_AREA_PREFIX + "deadbeef00")
        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "Una nota", "type": "reference"},
                "body": "cuerpo",
                "suggested_links": [],
            },
        }

        with patch.object(
            callbacks, "resolve_item_token", AsyncMock(return_value=None)
        ):
            await callbacks.handle_callback(update, mock_context)

        edit = update.callback_query.edit_message_text
        assert "ya no existe" in edit.await_args[0][0]
        assert _markup_de(edit) is not None, (
            "el selector desapareció con la nota todavía pendiente: el mensaje "
            "pide 'elegir otro destino' sin dejar ningún botón para hacerlo"
        )

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_la_nota_pendiente_sobrevive(
        self, mock_context, make_callback_query
    ) -> None:
        """Contra-caso: lo que el camino sí hace bien — no descarta la captura."""
        from adso.handlers import callbacks

        update = make_callback_query(CB_DEST_AREA_PREFIX + "deadbeef00")
        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "Una nota", "type": "reference"},
                "body": "cuerpo",
                "suggested_links": [],
            },
        }

        with patch.object(
            callbacks, "resolve_item_token", AsyncMock(return_value=None)
        ):
            await callbacks.handle_callback(update, mock_context)

        assert mock_context.user_data.get("pending_note")
