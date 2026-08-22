"""Instrumentación de tiempos de la captura y silenciado del log de jobs.

Motivación (2026-08-22): una captura percibida como lenta no se pudo diagnosticar
con los logs existentes — entre el inicio de la llamada a Gemini y el preview no
había ninguna marca de tiempo, y el 96% del log eran las dos líneas INFO que
apscheduler emite por cada corrida del `heartbeat_job` (2880 de 3001 líneas en
24h). Estos tests fijan las dos piezas que faltaban: un cronómetro por etapa y
una config de logging que no ahogue lo que importa.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adso.bot_utils import Stopwatch
from adso.embeddings import SimilarNote


class _FakeClock:
    """Reloj monótono controlado: cada lectura avanza lo que diga la lista."""

    def __init__(self, deltas: list[float]) -> None:
        self._deltas = list(deltas)
        self.now = 0.0

    def __call__(self) -> float:
        if self._deltas:
            self.now += self._deltas.pop(0)
        return self.now


class TestStopwatch:
    def test_registra_las_etapas_en_orden_de_ejecucion(self) -> None:
        """El resumen se lee como la secuencia real del pipeline, no alfabético."""
        sw = Stopwatch(clock=_FakeClock([0, 0, 1.0, 0, 2.0]))

        with sw.stage("scan"):
            pass
        with sw.stage("classify"):
            pass

        assert list(sw.stages) == ["scan", "classify"]
        assert sw.stages["scan"] == pytest.approx(1.0)
        assert sw.stages["classify"] == pytest.approx(2.0)

    def test_acumula_una_etapa_que_se_entra_dos_veces(self) -> None:
        """`_classify_and_preview` corre dos scans; deben sumarse, no pisarse."""
        sw = Stopwatch(clock=_FakeClock([0, 0, 1.0, 0, 0.5]))

        with sw.stage("scan"):
            pass
        with sw.stage("scan"):
            pass

        assert sw.stages["scan"] == pytest.approx(1.5)

    def test_registra_la_duracion_aunque_la_etapa_lance(self) -> None:
        """El caso lento es justo el que falla: si no se mide, no sirve de nada."""
        sw = Stopwatch(clock=_FakeClock([0, 0, 7.0]))

        with pytest.raises(RuntimeError):
            with sw.stage("classify"):
                raise RuntimeError("Gemini caído")

        assert sw.stages["classify"] == pytest.approx(7.0)

    def test_el_total_incluye_trabajo_fuera_de_las_etapas(self) -> None:
        """Si total >> suma de etapas, lo lento está sin instrumentar."""
        clock = _FakeClock([0, 0, 1.0, 9.0])
        sw = Stopwatch(clock=clock)

        with sw.stage("classify"):
            pass

        assert sw.total() == pytest.approx(10.0)

    def test_summary_incluye_cada_etapa_y_el_total(self) -> None:
        sw = Stopwatch(clock=_FakeClock([0, 0, 1.5, 0, 6.25, 0]))

        with sw.stage("scan"):
            pass
        with sw.stage("classify"):
            pass

        assert sw.summary() == "scan 1.50s | classify 6.25s | total 7.75s"

    def test_summary_sin_etapas_reporta_solo_el_total(self) -> None:
        sw = Stopwatch(clock=_FakeClock([0, 3.0]))

        assert sw.summary() == "total 3.00s"


def _fake_embeddings() -> MagicMock:
    emb = MagicMock()
    emb.compute_embedding = AsyncMock(return_value=[0.1, 0.2])
    emb.query_similar = AsyncMock(return_value=[
        SimilarNote(
            note_id="01-Projects/tesis/otra",
            path="/vault/01-Projects/tesis/otra.md",
            distance=0.2,
            metadata={"title": "Otra nota"},
            snippet=None,
        )
    ])
    return emb


def _capture_result() -> dict:
    return {
        "mode": "capture",
        "confidence": 0.9,
        "needs_disambiguation": False,
        "payload": {
            "frontmatter": {"title": "Una nota", "type": "reference", "status": "active"},
            "body": "el cuerpo de la nota",
        },
    }


def _timing_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "adso.handlers.capture" and "total" in r.getMessage()
    ]


class TestCapturaLogueaSusTiempos:
    """Una captura deja una línea con el desglose por etapa."""

    @pytest.mark.asyncio
    async def test_loguea_scan_classify_links_y_total(
        self, mock_context, make_update, caplog
    ) -> None:
        from adso.handlers import capture

        mock_context.bot_data["embeddings"] = _fake_embeddings()
        update = make_update("texto")

        with caplog.at_level(logging.INFO, logger="adso.handlers.capture"):
            with patch.object(
                capture, "classify", AsyncMock(return_value=_capture_result())
            ):
                await capture._classify_and_preview(
                    update, mock_context, "el cuerpo de la nota", media_type="text"
                )

        assert len(_timing_records(caplog)) == 1
        linea = _timing_records(caplog)[0]
        for etapa in ("scan", "classify", "links", "total"):
            assert etapa in linea
        assert "text" in linea, "el media_type distingue captura de texto de PDF/audio"

    @pytest.mark.asyncio
    async def test_tambien_loguea_en_modo_degradado(
        self, mock_context, make_update, caplog
    ) -> None:
        """El degradado quema los 3 reintentos: es el caso que más urge medir."""
        from adso.handlers import capture
        from adso.llm_client import make_degraded_result

        update = make_update("texto")

        with caplog.at_level(logging.INFO, logger="adso.handlers.capture"):
            with patch.object(
                capture,
                "classify",
                AsyncMock(return_value=make_degraded_result("el cuerpo")),
            ):
                await capture._classify_and_preview(
                    update, mock_context, "el cuerpo", media_type="text"
                )

        assert len(_timing_records(caplog)) == 1
        assert "classify" in _timing_records(caplog)[0]

    @pytest.mark.parametrize(
        "resultado",
        [
            pytest.param({"mode": "manage", "payload": {}}, id="mode-no-capture"),
            pytest.param(
                {"mode": "capture", "payload": {"frontmatter": "no soy un dict"}},
                id="frontmatter-invalido",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_loguea_en_las_salidas_por_respuesta_inesperada(
        self, mock_context, make_update, caplog, resultado
    ) -> None:
        """Todas las salidas miden: si una no lo hace, el flujo lento que se va
        por ahí queda invisible y el próximo diagnóstico vuelve a ser a mano."""
        from adso.handlers import capture

        update = make_update("texto")

        with caplog.at_level(logging.INFO, logger="adso.handlers.capture"):
            with patch.object(
                capture, "classify", AsyncMock(return_value=resultado)
            ):
                await capture._classify_and_preview(
                    update, mock_context, "el cuerpo", media_type="text"
                )

        assert len(_timing_records(caplog)) == 1


class TestConfigureLogging:
    """El heartbeat toca un archivo cada 60s; su log no aporta nada y tapa todo."""

    @pytest.fixture(autouse=True)
    def _restaura_niveles(self):
        afectados = [
            "apscheduler.executors.default",
            "httpx",
            "telegram",
            "chromadb",
            "adso",
        ]
        previos = {n: logging.getLogger(n).level for n in afectados}
        root_previo = logging.getLogger().level
        yield
        for nombre, nivel in previos.items():
            logging.getLogger(nombre).setLevel(nivel)
        logging.getLogger().setLevel(root_previo)

    def test_silencia_el_executor_de_apscheduler(self) -> None:
        from adso.logging_setup import configure_logging

        configure_logging()

        assert logging.getLogger("apscheduler.executors.default").level == logging.WARNING

    def test_no_silencia_el_scheduler_entero(self) -> None:
        """`apscheduler.scheduler` avisa arranque y jobs perdidos — eso se conserva."""
        from adso.logging_setup import configure_logging

        configure_logging()

        assert logging.getLogger("apscheduler.scheduler").level == logging.NOTSET

    def test_respeta_log_level(self, monkeypatch) -> None:
        from adso.logging_setup import configure_logging

        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        configure_logging()

        assert logging.getLogger().level == logging.DEBUG

    def test_log_level_invalido_cae_a_info(self, monkeypatch) -> None:
        from adso.logging_setup import configure_logging

        monkeypatch.setenv("LOG_LEVEL", "VERBOSO")
        configure_logging()

        assert logging.getLogger().level == logging.INFO

    def test_log_level_que_resuelve_a_un_atributo_no_entero_cae_a_info(
        self, monkeypatch
    ) -> None:
        """`logging.BASIC_FORMAT` existe y es un str: sin el guard, `basicConfig`
        recibiría una cadena y el bot no arrancaría por una env var mal puesta."""
        from adso.logging_setup import configure_logging

        monkeypatch.setenv("LOG_LEVEL", "BASIC_FORMAT")
        configure_logging()

        assert logging.getLogger().level == logging.INFO

    def test_no_toca_el_nivel_de_los_loggers_de_adso(self) -> None:
        from adso.logging_setup import configure_logging

        configure_logging()

        assert logging.getLogger("adso").level == logging.NOTSET
