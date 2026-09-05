"""Reproductores de los bugs de la auditoría 2026-08-22 — reportes y jobs.

Mismo contrato que `test_audit_2026_08_vault.py`: cada test **especifica el
comportamiento correcto** y se escribió reproduciendo el bug (fallaba) antes de
aplicar el fix. Los tres ya están arreglados, así que las marcas
`xfail(strict=True)` se sacaron en el commit del fix y quedan como regresión: si
alguno de estos defectos vuelve, fallan.

Issues:
  R1 — el umbral de "reporte vacío" (400 bytes) es menor que el header solo,
       así que la rama de aviso es código muerto.
  R2 — un proyecto/área borrado degrada en silencio al reporte del vault entero.
  R3 — el `dest` de la notificación del cron va sin escapar: el `BadRequest`
       rompe el invariante "una nota por ciclo".
"""

from __future__ import annotations

from datetime import date
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.error import BadRequest

from adso.constants import CB_DEST_PROJECT_PREFIX, CB_REPORT_SCOPE_PREFIX
from adso.reporters import _report_header
from tests.helpers import write_note


# ---------------------------------------------------------------------------
# Helpers compartidos
# ---------------------------------------------------------------------------


def _fake_query() -> MagicMock:
    """CallbackQuery mínimo: registra los textos editados."""
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.delete_message = AsyncMock()
    return query


def _textos_editados(query: MagicMock) -> list[str]:
    """Aplana los textos pasados a `edit_message_text`."""
    out = []
    for call in query.edit_message_text.await_args_list:
        out.append(call.args[0] if call.args else call.kwargs.get("text", ""))
    return out


def _context_de_reporte(mock_context) -> MagicMock:
    """Completa `mock_context` con lo que necesita el flujo de reportes."""
    mock_context.bot = MagicMock()
    mock_context.bot.send_document = AsyncMock()
    mock_context.user_data["pending_report"] = True
    mock_context.user_data["report_full"] = False
    return mock_context


# `write_note` con las fechas fijas que estos tests asumen.
_escribir_nota = partial(
    write_note, date_created="2026-08-01T10:00:00", date_modified="2026-08-01T10:00:00"
)


# ---------------------------------------------------------------------------
# R1 — el aviso de "reporte vacío" es código muerto
# ---------------------------------------------------------------------------
#
# `_send_report` decide que un reporte está vacío con `if len(report_bytes) <
# 400` (reports.py:341), y el comentario dice "solo tiene el header". Pero el
# header **solo** ya pesa ~650 bytes: el logo ASCII son caracteres de bloque
# UTF-8 de 3 bytes cada uno. Ningún reporte real cae por debajo del umbral, así
# que la rama nunca se ejecuta: el usuario que pide el reporte de un scope sin
# notas recibe igual un `.md` adjunto lleno de "_Sin referencias activas._",
# "_Sin tareas._", etc., y tiene que abrirlo para descubrir que no hay nada.
#
# El fix correcto no es subir el número mágico sino medir el cuerpo (bytes por
# encima del header, o el conteo de notas del scope); el test especifica el
# efecto observable, no la implementación.


class TestR1ReporteVacio:
    async def test_scope_sin_notas_avisa_en_vez_de_mandar_el_md(
        self, mock_context, vault_path: Path
    ) -> None:
        from adso.handlers import reports
        from adso.reporters import scope_report

        context = _context_de_reporte(mock_context)
        query = _fake_query()

        with patch("adso.reporters._llm_synthesis", AsyncMock(return_value=None)):
            await reports._send_report(
                query,
                context,
                report_bytes_coro=scope_report(vault_path, project="proyecto-fantasma"),
                filename=f"scope-p-fantasma-{date.today()}.md",
            )

        context.bot.send_document.assert_not_awaited()
        assert any("No se encontraron notas" in t for t in _textos_editados(query)), (
            "el usuario recibió un .md adjunto que solo dice '_Sin referencias "
            "activas._' en vez del aviso que la rama muerta prometía"
        )

    def test_el_header_solo_ya_supera_el_umbral(self) -> None:
        """Guard de regresión: documenta por qué el umbral de 400 no sirve.

        El logo ASCII usa caracteres de bloque UTF-8 (3 bytes cada uno), así que
        el header pesa mucho más de lo que sugiere su longitud en caracteres.
        Si alguien "arregla" R1 subiendo el número mágico, este test le recuerda
        contra qué tiene que compararlo.
        """
        header = _report_header("Reporte de scope — Proyecto: x")
        assert len(header.encode("utf-8")) > 400, (
            "si el header pesara menos de 400 bytes el umbral actual sería válido"
        )

    async def test_scope_con_notas_si_manda_el_documento(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso: un scope con contenido debe seguir enviándose como .md."""
        from adso.handlers import reports
        from adso.reporters import scope_report

        _escribir_nota(
            vault_path / "01-Projects" / "tesis" / "metodo.md",
            "Contenido real de la nota.",
            title="Metodología",
            project="tesis",
        )
        context = _context_de_reporte(mock_context)
        query = _fake_query()

        with patch("adso.reporters._llm_synthesis", AsyncMock(return_value=None)):
            await reports._send_report(
                query,
                context,
                report_bytes_coro=scope_report(vault_path, project="tesis"),
                filename="scope-tesis.md",
            )

        context.bot.send_document.assert_awaited_once()


# ---------------------------------------------------------------------------
# R2 — un destino borrado degrada al reporte del vault entero
# ---------------------------------------------------------------------------
#
# `resolve_item_token` devuelve `None` con un significado preciso: "el token
# tiene forma de token pero no corresponde a ningún proyecto/área existente" —
# o sea, se borró entre que se dibujó el teclado y el usuario apretó el botón.
# `callbacks.py:158-174` lo trata así y avisa ("Ese proyecto ya no existe").
#
# `_parse_scope_suffix` (reports.py:302-305) devuelve ese `None` tal cual como
# nombre de proyecto. Aguas abajo `scope_report` cae al `else` con
# `scope_path=None` y `scope_label="Vault completo"`: el usuario pidió el
# reporte de un proyecto y recibe un `.md` con TODO el vault, sin ningún aviso.
# En un vault maduro eso es un adjunto enorme que además parece un reporte
# válido — el título dice "Vault completo", pero nada dice que la selección se
# perdió.


class TestR2DestinoBorradoEnReportes:
    async def test_proyecto_inexistente_avisa_y_no_reporta_todo_el_vault(
        self, mock_context, vault_path: Path
    ) -> None:
        from adso.handlers import reports

        # Vault con contenido: si el bug degrada a "vault completo", el reporte
        # sale gordo y se manda como si fuera lo pedido.
        _escribir_nota(
            vault_path / "02-Areas" / "docencia" / "clase.md",
            "Apuntes de la clase.",
            title="Clase 1",
            area="docencia",
        )
        context = _context_de_reporte(mock_context)
        query = _fake_query()

        with (
            patch.object(reports, "resolve_item_token", AsyncMock(return_value=None)),
            patch("adso.reporters._llm_synthesis", AsyncMock(return_value=None)),
        ):
            await reports.handle_report_callback(
                query, context, f"{CB_REPORT_SCOPE_PREFIX}p:deadbeef00"
            )

        textos = _textos_editados(query)
        assert any("ya no existe" in t for t in textos), (
            f"no se avisó que el proyecto se borró; textos emitidos: {textos}"
        )
        context.bot.send_document.assert_not_awaited()

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_el_flujo_de_captura_si_avisa(
        self, mock_context, make_callback_query
    ) -> None:
        """Contra-caso: el mismo `None` en `callbacks.py` sí se reporta.

        Sirve de referencia de lo que debería hacer el camino de reportes: la
        semántica de `resolve_item_token` ya está resuelta en el otro consumidor.
        """
        from adso.handlers import callbacks

        update = make_callback_query(CB_DEST_PROJECT_PREFIX + "deadbeef00")
        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "Una nota", "type": "reference"},
                "body": "cuerpo",
                "suggested_links": [],
            },
        }

        with patch.object(callbacks, "resolve_item_token", AsyncMock(return_value=None)):
            await callbacks.handle_callback(update, mock_context)

        assert "ya no existe" in update.callback_query.edit_message_text.await_args[0][0]


# ---------------------------------------------------------------------------
# R3 — el `dest` sin escapar rompe el "una nota por ciclo"
# ---------------------------------------------------------------------------
#
# En la notificación del cron de reclasificación (jobs.py:178-185) el `title` va
# con `_esc` pero el `dest` se interpola crudo desde `new_fm['project']` /
# `['area']`. `_safe_component` (vault_writer.py) solo bloquea traversal: `<`,
# `>` y `&` son nombres de carpeta perfectamente válidos y pasan enteros. Con
# `parse_mode="HTML"`, Telegram responde `BadRequest: can't parse entities` y el
# `except` por-nota se lo traga.
#
# El daño real no es el mensaje perdido: el `return  # Procesar de a una por
# ciclo` está DESPUÉS del send, así que la excepción se lo saltea y el `for`
# sigue con la nota siguiente. El invariante de una nota por pasada se rompe y
# se encadenan `classify()` sin pausa contra un free tier de 15 RPM — con un
# Inbox de varias notas afectadas, el ciclo entero se come la quota.


def _context_de_jobs(vault: Path) -> SimpleNamespace:
    """Context mínimo para `_reclassify_inbox_impl` (mismo shape que usa el cron)."""
    settings = SimpleNamespace(
        vault_path=vault,
        telegram_allowed_user_id=12345,
        tasks=SimpleNamespace(debug=False),
        llm=SimpleNamespace(disambiguation_threshold=0.7),
    )
    return SimpleNamespace(
        user_data={},
        bot_data={"settings": settings},
        bot=SimpleNamespace(send_message=AsyncMock()),
        application=SimpleNamespace(user_data={}),
    )


def _resultado_classify(titulo: str) -> dict:
    return {
        "mode": "capture",
        "confidence": 0.9,
        "payload": {
            "frontmatter": {
                "title": titulo,
                "type": "reference",
                "status": "active",
                "tags": [],
            },
            "body": "Contenido clasificado.",
        },
    }


class TestR3NotificacionDelCron:
    async def test_el_destino_va_escapado(self, vault_path: Path) -> None:
        from adso.handlers import jobs

        inbox = _escribir_nota(
            vault_path / "00-Inbox" / "2026-08-01-sin-clasificar.md",
            "Contenido original del usuario.",
            title="Sin clasificar",
            type="idea",
            status="pending-classification",
            project="A<B",
        )
        context = _context_de_jobs(vault_path)

        with (
            patch.object(
                jobs, "find_by_property",
                AsyncMock(return_value=[SimpleNamespace(path=inbox)]),
            ),
            patch.object(jobs, "_get_existing_items", AsyncMock(return_value=([], []))),
            patch.object(jobs, "_get_existing_tags", AsyncMock(return_value=[])),
            patch.object(
                jobs, "classify", AsyncMock(return_value=_resultado_classify("Clasificada"))
            ),
        ):
            await jobs._reclassify_inbox_impl(context)

        texto = context.bot.send_message.await_args.kwargs["text"]
        assert "A&lt;B" in texto, (
            f"`dest` sin escapar en un mensaje con parse_mode=HTML → BadRequest: {texto!r}"
        )

    async def test_un_fallo_al_notificar_no_encadena_la_nota_siguiente(
        self, vault_path: Path
    ) -> None:
        from adso.handlers import jobs

        notas = [
            _escribir_nota(
                vault_path / "00-Inbox" / f"2026-08-0{i}-sin-clasificar.md",
                f"Contenido original {i}.",
                title=f"Sin clasificar {i}",
                type="idea",
                status="pending-classification",
                project="A<B",
            )
            for i in (1, 2)
        ]
        context = _context_de_jobs(vault_path)
        # Lo que devuelve Telegram con `dest` sin escapar.
        context.bot.send_message = AsyncMock(
            side_effect=BadRequest("Can't parse entities: unsupported start tag")
        )
        classify_mock = AsyncMock(return_value=_resultado_classify("Clasificada"))

        with (
            patch.object(
                jobs, "find_by_property",
                AsyncMock(return_value=[SimpleNamespace(path=p) for p in notas]),
            ),
            patch.object(jobs, "_get_existing_items", AsyncMock(return_value=([], []))),
            patch.object(jobs, "_get_existing_tags", AsyncMock(return_value=[])),
            patch.object(jobs, "classify", classify_mock),
        ):
            await jobs._reclassify_inbox_impl(context)

        assert classify_mock.await_count == 1, (
            "el fallo al notificar saltea el `return` de 'una por ciclo': la "
            "pasada siguió con la nota 2 y encadenó otro classify() sin pausa "
            "contra un free tier de 15 RPM"
        )

    async def test_una_nota_por_ciclo_en_el_camino_feliz(self, vault_path: Path) -> None:
        """Contra-caso: sin fallo de red el invariante ya se respeta."""
        from adso.handlers import jobs

        notas = [
            _escribir_nota(
                vault_path / "00-Inbox" / f"2026-08-0{i}-sin-clasificar.md",
                f"Contenido original {i}.",
                title=f"Sin clasificar {i}",
                type="idea",
                status="pending-classification",
                project="tesis",
            )
            for i in (1, 2)
        ]
        context = _context_de_jobs(vault_path)
        classify_mock = AsyncMock(return_value=_resultado_classify("Clasificada"))

        with (
            patch.object(
                jobs, "find_by_property",
                AsyncMock(return_value=[SimpleNamespace(path=p) for p in notas]),
            ),
            patch.object(jobs, "_get_existing_items", AsyncMock(return_value=([], []))),
            patch.object(jobs, "_get_existing_tags", AsyncMock(return_value=[])),
            patch.object(jobs, "classify", classify_mock),
        ):
            await jobs._reclassify_inbox_impl(context)

        assert classify_mock.await_count == 1
        assert notas[1].exists(), "la segunda nota queda para el ciclo siguiente"
