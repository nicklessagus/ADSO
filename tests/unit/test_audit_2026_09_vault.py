"""Auditoría 2026-09-18 — secciones A, D, E, F y G de la spec.

Los tests salen del documento de spec, no del código: especifican la ventana de
gracia de las escrituras del bot (#66), el `status` por defecto de `create_note`
(#69), la validación de `VAULT_PATH` al arrancar (#70), la canonicalización del
destino que propone el LLM (#71) y la exclusión de `03-Resources/` del índice
semántico (#72).

Los símbolos que la spec crea se importan **dentro** de cada test a propósito:
un import al tope del módulo rompería la colección entera del archivo en vez de
fallar solo el test que los especifica.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.helpers import write_note

from adso.bot import _watcher_callbacks
from adso.bot_utils import _BOT_WRITTEN_CAP, mark_bot_written
from adso.constants import STATUS_BY_TYPE, STATUS_ON_CONFIRM
from adso.embeddings import should_index
from adso.llm_client import classify
from adso.vault_search import _scan_vault
from adso.vault_writer import VAULT_DIRS, create_note, ensure_vault_structure, read_note


# ---------------------------------------------------------------------------
# A. Las escrituras del bot no las reprocesa el watcher (#66)
# ---------------------------------------------------------------------------


@pytest.fixture
def watcher(mock_context, vault_path: Path):
    """Callbacks del `VaultWatcher` con embeddings, indexado y backup mockeados."""
    app = SimpleNamespace(bot_data=dict(mock_context.bot_data))
    app.bot_data["embeddings"] = AsyncMock()
    app.bot_data["git_backup"] = AsyncMock()
    with patch("adso.bot._index_note_safe", new=AsyncMock()) as index_mock:
        on_change, _on_delete = _watcher_callbacks(app)
        yield SimpleNamespace(
            bot_data=app.bot_data,
            on_change=on_change,
            index=index_mock,
            backup=app.bot_data["git_backup"],
        )


@pytest.fixture
def external_note(vault_path: Path) -> Path:
    """Nota indexable del vault, la que el watcher ve cambiar."""
    return write_note(vault_path / "00-Inbox" / "externa.md", "cuerpo de la nota")


class TestMarcaDeEscrituraDelBot:
    """A1-A4: la marca es un dict path → timestamp con ventana de gracia."""

    def test_la_marca_guarda_el_momento_en_un_dict(self) -> None:
        bot_data: dict = {}
        antes = time.monotonic()
        mark_bot_written(bot_data, Path("/vault/00-Inbox/x.md"))
        despues = time.monotonic()

        marcas = bot_data["bot_written_paths"]
        assert isinstance(marcas, dict)
        assert antes <= marcas[Path("/vault/00-Inbox/x.md")] <= despues

    def test_la_marca_poda_las_entradas_viejas(self) -> None:
        bot_data: dict = {}
        vieja = Path("/vault/00-Inbox/vieja.md")
        nueva = Path("/vault/00-Inbox/nueva.md")

        mark_bot_written(bot_data, vieja, now=0.0)
        mark_bot_written(bot_data, nueva, now=1000.0)

        assert vieja not in bot_data["bot_written_paths"]
        assert nueva in bot_data["bot_written_paths"]

    def test_la_marca_respeta_el_tope_de_512(self) -> None:
        """Contra-caso A2: el cap sigue acotando el dict aunque nada esté vencido."""
        bot_data: dict = {}
        for i in range(_BOT_WRITTEN_CAP + 100):
            mark_bot_written(bot_data, Path(f"/vault/00-Inbox/n{i}.md"))

        assert len(bot_data["bot_written_paths"]) <= _BOT_WRITTEN_CAP

    def test_la_ventana_de_gracia_es_de_diez_segundos(self) -> None:
        from adso.bot_utils import BOT_WRITE_GRACE_SECONDS

        assert BOT_WRITE_GRACE_SECONDS == 10.0

    def test_consultar_la_marca_no_la_consume(self) -> None:
        from adso.bot_utils import was_bot_written

        bot_data: dict = {}
        path = Path("/vault/00-Inbox/x.md")
        mark_bot_written(bot_data, path, now=100.0)

        assert was_bot_written(bot_data, path, now=100.5) is True
        assert was_bot_written(bot_data, path, now=101.0) is True
        assert path in bot_data["bot_written_paths"]

    def test_una_marca_vencida_no_cuenta_como_escritura_del_bot(self) -> None:
        from adso.bot_utils import was_bot_written

        bot_data: dict = {}
        path = Path("/vault/00-Inbox/x.md")
        mark_bot_written(bot_data, path, now=100.0)

        assert was_bot_written(bot_data, path, now=111.0) is False

    def test_un_path_nunca_marcado_no_cuenta_como_escritura_del_bot(self) -> None:
        from adso.bot_utils import was_bot_written

        bot_data: dict = {}
        mark_bot_written(bot_data, Path("/vault/00-Inbox/otra.md"), now=100.0)

        assert was_bot_written(bot_data, Path("/vault/00-Inbox/x.md"), now=100.1) is False

    def test_las_marcas_vencidas_se_descartan(self) -> None:
        from adso.bot_utils import was_bot_written

        bot_data: dict = {}
        vieja = Path("/vault/00-Inbox/vieja.md")
        mark_bot_written(bot_data, vieja, now=0.0)

        # Cualquiera de los dos caminos puede podarla (spec A4).
        was_bot_written(bot_data, vieja, now=1000.0)
        mark_bot_written(bot_data, Path("/vault/00-Inbox/nueva.md"), now=1000.0)

        assert vieja not in bot_data["bot_written_paths"]


class TestWatcherIgnoraLasEscriturasDelBot:
    """A5-A8: el callback de cambio externo saltea lo que escribió el bot."""

    async def test_una_nota_no_marcada_se_reindexa_y_respalda(
        self, watcher, external_note: Path
    ) -> None:
        """Contra-caso A6: una edición externa sigue disparando embed + backup."""
        await watcher.on_change(external_note)

        assert watcher.index.await_count == 1
        assert watcher.backup.notify.await_count == 1

    async def test_una_nota_recien_marcada_no_se_reindexa(
        self, watcher, external_note: Path
    ) -> None:
        """A5 (primer evento): la escritura propia del bot no vuelve al índice."""
        mark_bot_written(watcher.bot_data, external_note)

        await watcher.on_change(external_note)

        assert watcher.index.await_count == 0
        assert watcher.backup.notify.await_count == 0

    async def test_los_dos_eventos_de_una_escritura_se_saltean(
        self, watcher, external_note: Path
    ) -> None:
        mark_bot_written(watcher.bot_data, external_note)

        await watcher.on_change(external_note)
        await watcher.on_change(external_note)

        assert watcher.index.await_count == 0
        assert watcher.backup.notify.await_count == 0

    async def test_una_marca_vieja_no_bloquea_una_edicion_externa(
        self, watcher, external_note: Path
    ) -> None:
        mark_bot_written(watcher.bot_data, external_note, now=time.monotonic() - 3600)

        await watcher.on_change(external_note)

        assert watcher.index.await_count == 1
        assert watcher.backup.notify.await_count == 1


# ---------------------------------------------------------------------------
# D. `create_note` escribe un `status` para toda nota (#69)
# ---------------------------------------------------------------------------


async def _fm_de_nota_creada(fm: dict, vault_path: Path) -> dict:
    """Crea la nota y devuelve el frontmatter tal como quedó en disco."""
    path = await create_note(fm, "cuerpo", vault_path)
    note = await read_note(path)
    return note.frontmatter


class TestStatusPorDefecto:

    def test_la_tabla_de_defaults_deriva_de_status_on_confirm(self) -> None:
        from adso.constants import DEFAULT_STATUS_BY_TYPE

        assert DEFAULT_STATUS_BY_TYPE == {**STATUS_ON_CONFIRM, "project-index": "active"}
        for note_type, status in DEFAULT_STATUS_BY_TYPE.items():
            assert status in STATUS_BY_TYPE[note_type]

    async def test_sin_status_usa_el_default_del_tipo(self, vault_path: Path) -> None:
        fm = await _fm_de_nota_creada(
            {"title": "Sin status", "type": "reference"}, vault_path
        )

        assert fm.get("status") == "active"

    async def test_status_none_usa_el_default_del_tipo(self, vault_path: Path) -> None:
        fm = await _fm_de_nota_creada(
            {"title": "Status nulo", "type": "task", "status": None}, vault_path
        )

        assert fm["status"] == "pending"

    async def test_status_vacio_o_en_blanco_usa_el_default_del_tipo(
        self, vault_path: Path
    ) -> None:
        vacio = await _fm_de_nota_creada(
            {"title": "Status vacío", "type": "idea", "status": ""}, vault_path
        )
        blanco = await _fm_de_nota_creada(
            {"title": "Status en blanco", "type": "reference", "status": "   "},
            vault_path,
        )

        assert vacio["status"] == "raw"
        assert blanco["status"] == "active"

    async def test_project_index_sin_status_usa_active(self, vault_path: Path) -> None:
        fm = await _fm_de_nota_creada(
            {
                "title": "Tesis",
                "type": "project-index",
                "project": "tesis",
                "description": "Tesis doctoral.",
            },
            vault_path,
        )

        assert fm.get("status") == "active"

    async def test_area_index_no_lleva_status(self, vault_path: Path) -> None:
        """D3: `area-index` no tiene ciclo de vida, así que no se le inventa uno."""
        fm = await _fm_de_nota_creada(
            {
                "title": "Docencia",
                "type": "area-index",
                "area": "docencia",
                "description": "Área de docencia.",
            },
            vault_path,
        )

        assert "status" not in fm

    async def test_un_status_valido_explicito_se_respeta(self, vault_path: Path) -> None:
        """Contra-caso D4."""
        fm = await _fm_de_nota_creada(
            {"title": "Pendiente", "type": "task", "status": "done"}, vault_path
        )

        assert fm["status"] == "done"

    async def test_type_invalido_sigue_degradando_a_idea(self, vault_path: Path) -> None:
        """Contra-caso D5."""
        fm = await _fm_de_nota_creada(
            {"title": "Tipo raro", "type": "paper"}, vault_path
        )

        assert fm["type"] == "idea"
        assert fm["status"] == "pending-classification"

    async def test_status_invalido_para_el_tipo_sigue_coaccionandose(
        self, vault_path: Path
    ) -> None:
        """Contra-caso D6: un valor inválido no vuelve al default, se degrada."""
        fm = await _fm_de_nota_creada(
            {"title": "Status raro", "type": "task", "status": "banana"}, vault_path
        )

        assert fm["status"] == "pending-classification"


# ---------------------------------------------------------------------------
# E. Un VAULT_PATH inexistente aborta el arranque (#70)
# ---------------------------------------------------------------------------


class TestEstructuraDelVault:

    async def test_vault_inexistente_aborta(self, tmp_path: Path) -> None:
        faltante = tmp_path / "vault-que-no-existe"

        with pytest.raises(RuntimeError) as excinfo:
            await ensure_vault_structure(faltante)

        mensaje = str(excinfo.value)
        assert str(faltante) in mensaje
        assert "VAULT_PATH" in mensaje
        assert not faltante.exists()

    async def test_vault_que_no_es_directorio_aborta(self, tmp_path: Path) -> None:
        archivo = tmp_path / "vault.md"
        archivo.write_text("no soy un directorio", encoding="utf-8")

        with pytest.raises(RuntimeError) as excinfo:
            await ensure_vault_structure(archivo)

        mensaje = str(excinfo.value)
        assert str(archivo) in mensaje
        assert "VAULT_PATH" in mensaje

    async def test_vault_existente_crea_las_carpetas_faltantes(
        self, tmp_path: Path
    ) -> None:
        """E2: sobre un directorio que existe, sigue siendo la creación de siempre."""
        await ensure_vault_structure(tmp_path)

        for d in VAULT_DIRS:
            assert (tmp_path / d).is_dir()

    async def test_es_idempotente(self, tmp_path: Path) -> None:
        """Contra-caso E3."""
        await ensure_vault_structure(tmp_path)
        await ensure_vault_structure(tmp_path)

        for d in VAULT_DIRS:
            assert (tmp_path / d).is_dir()

    async def test_un_vault_completo_no_se_toca(self, vault_path: Path) -> None:
        """Contra-caso E4: las notas que ya viven en el vault sobreviven."""
        nota = write_note(vault_path / "00-Inbox" / "vieja.md", "contenido previo")

        await ensure_vault_structure(vault_path)

        assert nota.exists()
        assert "contenido previo" in nota.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# F. Un proyecto o área inventado por el LLM nunca crea carpeta (#71)
# ---------------------------------------------------------------------------


_PROYECTOS = [{"name": "tesis", "description": "Tesis doctoral."}]
_AREAS = [{"name": "docencia", "description": "Cursos y clases."}]


class TestCanonicalizacionDeDestino:

    def test_un_proyecto_existente_se_canoniza(self) -> None:
        from adso.llm_client import canonicalize_destination

        fm = {"title": "n", "type": "reference", "project": "  Tesis ", "section": "experimentos"}
        canonicalize_destination(fm, _PROYECTOS, _AREAS)

        assert fm["project"] == "tesis"
        assert fm["section"] == "experimentos"

    def test_un_proyecto_inventado_se_descarta_con_su_seccion(self, caplog) -> None:
        from adso.llm_client import canonicalize_destination

        fm = {"title": "n", "type": "reference", "project": "proyecto-inventado", "section": "sec"}
        with caplog.at_level(logging.WARNING, logger="adso.llm_client"):
            canonicalize_destination(fm, _PROYECTOS, _AREAS)

        assert "project" not in fm
        assert "section" not in fm
        assert caplog.records

    def test_un_area_existente_se_canoniza_y_una_inventada_se_descarta(self) -> None:
        from adso.llm_client import canonicalize_destination

        existente = {"title": "n", "type": "reference", "area": "Docencia "}
        canonicalize_destination(existente, _PROYECTOS, _AREAS)
        assert existente["area"] == "docencia"

        inventada = {"title": "n", "type": "reference", "area": "area-inventada"}
        canonicalize_destination(inventada, _PROYECTOS, _AREAS)
        assert "area" not in inventada

    def test_valores_vacios_o_no_string_se_descartan(self) -> None:
        from adso.llm_client import canonicalize_destination

        fm = {"title": "n", "type": "reference", "project": 123, "area": "   "}
        canonicalize_destination(fm, _PROYECTOS, _AREAS)

        assert "project" not in fm
        assert "area" not in fm

    def test_sin_proyectos_ni_areas_existentes_no_hay_match_posible(self) -> None:
        from adso.llm_client import canonicalize_destination

        fm = {"title": "n", "type": "reference", "project": "tesis", "area": "docencia"}
        canonicalize_destination(fm, [], [])

        assert "project" not in fm
        assert "area" not in fm

    def test_un_proyecto_exacto_sobrevive_con_su_seccion(self) -> None:
        """Contra-caso F4."""
        from adso.llm_client import canonicalize_destination

        fm = {"title": "n", "type": "reference", "project": "tesis", "section": "experimentos"}
        canonicalize_destination(fm, _PROYECTOS, _AREAS)

        assert fm["project"] == "tesis"
        assert fm["section"] == "experimentos"

    def test_un_area_exacta_sobrevive(self) -> None:
        """Contra-caso F5."""
        from adso.llm_client import canonicalize_destination

        fm = {"title": "n", "type": "reference", "area": "docencia"}
        canonicalize_destination(fm, _PROYECTOS, _AREAS)

        assert fm["area"] == "docencia"

    def test_un_frontmatter_sin_destino_queda_igual(self) -> None:
        """Contra-caso F6."""
        from adso.llm_client import canonicalize_destination

        fm = {"title": "n", "type": "idea", "tags": ["x"], "status": "raw"}
        canonicalize_destination(fm, _PROYECTOS, _AREAS)

        assert fm == {"title": "n", "type": "idea", "tags": ["x"], "status": "raw"}

    def test_descartar_el_destino_no_pierde_el_resto_del_frontmatter(self) -> None:
        """Contra-caso F7: la nota va al Inbox, nunca se descarta."""
        from adso.llm_client import canonicalize_destination

        fm = {
            "title": "Nota importante",
            "type": "task",
            "tags": ["python", "curso"],
            "priority": "high",
            "due_date": "2026-09-30",
            "project": "inventado",
        }
        canonicalize_destination(fm, _PROYECTOS, _AREAS)

        assert fm == {
            "title": "Nota importante",
            "type": "task",
            "tags": ["python", "curso"],
            "priority": "high",
            "due_date": "2026-09-30",
        }


def _respuesta(mode: str, frontmatter: dict) -> str:
    return json.dumps(
        {
            "mode": mode,
            "confidence": 0.9,
            "payload": {"frontmatter": frontmatter, "body": "cuerpo", "summary": "s"},
        }
    )


class TestClassifyCanonizaElDestino:

    async def test_captura_con_proyecto_casi_igual_se_canoniza(self) -> None:
        texto = _respuesta("capture", {"title": "n", "type": "reference", "project": "Tesis "})
        with patch("adso.llm_client._call_gemini", new=AsyncMock(return_value=texto)):
            result = await classify("contenido", "text", _PROYECTOS, _AREAS)

        assert result["payload"]["frontmatter"]["project"] == "tesis"

    async def test_captura_con_proyecto_inventado_pierde_el_destino(self) -> None:
        texto = _respuesta(
            "capture",
            {"title": "n", "type": "reference", "project": "inventado", "section": "sec"},
        )
        with patch("adso.llm_client._call_gemini", new=AsyncMock(return_value=texto)):
            result = await classify("contenido", "text", _PROYECTOS, _AREAS)

        fm = result["payload"]["frontmatter"]
        assert "project" not in fm
        assert "section" not in fm
        assert fm["title"] == "n"

    async def test_payload_query_tambien_se_canoniza(self) -> None:
        texto = _respuesta("query", {"title": "n", "type": "reference", "area": "inventada"})
        with patch("adso.llm_client._call_gemini", new=AsyncMock(return_value=texto)):
            result = await classify("contenido", "text", _PROYECTOS, _AREAS)

        assert "area" not in result["payload"]["frontmatter"]

    async def test_payload_edit_tambien_se_canoniza(self) -> None:
        texto = _respuesta(
            "edit",
            {"title": "n", "type": "reference", "project": "Tesis ", "area": "inventada"},
        )
        with patch("adso.llm_client._call_gemini", new=AsyncMock(return_value=texto)):
            result = await classify("contenido", "text", _PROYECTOS, _AREAS)

        fm = result["payload"]["frontmatter"]
        assert fm["project"] == "tesis"
        assert "area" not in fm

    async def test_el_modo_manage_no_se_toca(self) -> None:
        """Contra-caso F2: crear un proyecto nuevo es exactamente lo que hace manage."""
        texto = json.dumps(
            {
                "mode": "manage",
                "confidence": 0.95,
                "payload": {
                    "operation": "create_project",
                    "params": {"name": "curso-python", "description": "Curso nuevo."},
                },
            }
        )
        with patch("adso.llm_client._call_gemini", new=AsyncMock(return_value=texto)):
            result = await classify("crear proyecto curso-python", "text", _PROYECTOS, _AREAS)

        assert result["payload"]["params"]["name"] == "curso-python"

    async def test_el_modo_degradado_no_se_altera(self) -> None:
        """F3: sin destino que canonizar, el modo degradado sigue igual."""
        with patch(
            "adso.llm_client._call_gemini", new=AsyncMock(side_effect=OSError("sin red"))
        ), patch("adso.llm_client.RETRY_DELAYS", [0, 0]):
            result = await classify("contenido perdido", "text", _PROYECTOS, _AREAS)

        fm = result["payload"]["frontmatter"]
        assert result["mode"] == "degraded"
        assert "project" not in fm
        assert "area" not in fm
        assert fm["status"] == "pending-classification"


# ---------------------------------------------------------------------------
# G. `03-Resources/` fuera del índice semántico (#72)
# ---------------------------------------------------------------------------


class TestResourcesFueraDelIndice:

    def test_la_exclusion_vive_en_constants(self) -> None:
        from adso import vault_search
        from adso.constants import ALWAYS_EXCLUDE_DIRS

        assert ALWAYS_EXCLUDE_DIRS == ("03-Resources",)
        assert tuple(vault_search._ALWAYS_EXCLUDE) == ALWAYS_EXCLUDE_DIRS

    def test_una_nota_en_resources_no_se_indexa(self, vault_path: Path) -> None:
        nota = vault_path / "03-Resources" / "apunte.md"

        assert should_index(nota, vault_path) is False

    def test_un_exclude_dirs_explicito_no_reactiva_resources(self, vault_path: Path) -> None:
        nota = vault_path / "03-Resources" / "sub" / "apunte.md"

        assert should_index(nota, vault_path, ["05-Archive"]) is False

    def test_las_notas_de_la_taxonomia_se_siguen_indexando(self, vault_path: Path) -> None:
        """Contra-caso G3."""
        assert should_index(vault_path / "00-Inbox" / "n.md", vault_path) is True
        assert should_index(vault_path / "01-Projects" / "tesis" / "n.md", vault_path) is True
        assert should_index(vault_path / "02-Areas" / "docencia" / "n.md", vault_path) is True

    def test_el_scan_estructural_sigue_excluyendo_resources(self, vault_path: Path) -> None:
        """Contra-caso G5."""
        write_note(vault_path / "03-Resources" / "apunte.md", "adjunto")
        write_note(vault_path / "00-Inbox" / "nota.md", "nota")

        encontrados = _scan_vault(vault_path)

        assert [p.name for p in encontrados] == ["nota.md"]
