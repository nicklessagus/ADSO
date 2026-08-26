"""Reproductores de los bugs de la auditoría 2026-08-22 — capa de vault.

Cada test de este archivo **especifica el comportamiento correcto** y se
escribió reproduciendo el bug (fallaba) antes de aplicar el fix. Ahora pasan y
quedan como regresión: si alguno de estos defectos vuelve, fallan.

Issues: #3 (wikilinks borrados al mover), #4 (stop() tras start fallido),
#5 (code fences destripados), #61 (find_tasks duplica tareas).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adso.vault_search import find_tasks
from adso.vault_writer import VAULT_DIRS, create_note, read_note, remove_broken_wikilinks
from adso.vault_watcher import VaultWatcher


@pytest.fixture
async def vault(tmp_path: Path) -> Path:
    for d in VAULT_DIRS:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# V1 — mover una nota borra wikilinks que siguen resolviendo
# ---------------------------------------------------------------------------
#
# Los wikilinks de Obsidian resuelven por stem, no por path: CLAUDE.md garantiza
# que "mover una nota no rompe links". Pero `on_moved` emite un delete del
# origen y `_remove_external_note` (bot.py) llama a `remove_broken_wikilinks`
# sin verificar si otra nota con el mismo stem sigue existiendo en el vault.


class TestV1MoverNotaNoRompeWikilinks:
    async def test_mover_nota_preserva_el_wikilink(self, vault: Path) -> None:
        origen = await create_note(
            {"title": "Paper X", "type": "reference", "status": "active", "area": "investigacion"},
            "Contenido del paper.\n",
            vault,
        )
        citante = await create_note(
            {"title": "Citante", "type": "reference", "status": "active", "area": "investigacion"},
            f"Texto.\n\n## Ver también\n\n- [[{origen.stem}]] — el paper\n",
            vault,
        )

        # El usuario arrastra la nota a un proyecto desde Obsidian.
        destino = vault / "01-Projects" / "tesis"
        destino.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origen), str(destino / origen.name))

        # El watcher despacha el delete del ORIGEN, pero el stem sigue existiendo.
        modificadas = await remove_broken_wikilinks(vault, origen)

        assert modificadas == 0
        assert f"[[{origen.stem}]]" in (await read_note(citante)).body

    async def test_borrar_una_de_dos_notas_homonimas_preserva_el_link(self, vault: Path) -> None:
        # Dos notas con el mismo stem en carpetas distintas: borrar una deja la
        # otra resolviendo el wikilink, así que el link no está roto.
        gemela_a = vault / "02-Areas" / "docencia" / "resumen.md"
        gemela_b = vault / "02-Areas" / "investigacion" / "resumen.md"
        for p in (gemela_a, gemela_b):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("---\ntitle: Resumen\n---\ncuerpo\n", encoding="utf-8")

        citante = await create_note(
            {"title": "Citante2", "type": "reference", "status": "active", "area": "docencia"},
            "Texto.\n\n## Ver también\n\n- [[resumen]] — link\n",
            vault,
        )

        gemela_a.unlink()
        modificadas = await remove_broken_wikilinks(vault, gemela_a)

        assert modificadas == 0
        assert "[[resumen]]" in (await read_note(citante)).body

    async def test_borrado_real_si_limpia_el_wikilink(self, vault: Path) -> None:
        """Contra-caso: sin ninguna nota con ese stem, el link SÍ está roto."""
        citante = await create_note(
            {"title": "Citante3", "type": "reference", "status": "active", "area": "docencia"},
            "Texto.\n\n## Ver también\n\n- [[nota-inexistente]] — link\n",
            vault,
        )
        modificadas = await remove_broken_wikilinks(vault, vault / "nota-inexistente.md")

        assert modificadas == 1
        assert "[[nota-inexistente]]" not in (await read_note(citante)).body


# ---------------------------------------------------------------------------
# V2 — stop() lanza si el observer nunca arrancó → el flush del backup no corre
# ---------------------------------------------------------------------------


class TestV2StopTrasStartFallido:
    async def test_stop_no_lanza_si_el_observer_no_arranco(self, tmp_path: Path) -> None:
        watcher = VaultWatcher(vault_path=tmp_path, bot=MagicMock(), chat_id=1)

        observer = MagicMock()
        observer.start.side_effect = OSError("inotify watch limit reached")
        # threading.Thread.join() sobre un thread nunca arrancado lanza RuntimeError.
        observer.join.side_effect = RuntimeError("cannot join thread before it is started")

        with patch("adso.vault_watcher._make_observer", return_value=observer):
            await watcher.start()

        # El shutdown de PTB llama stop() antes del flush del git backup: si esto
        # lanza, el flush nunca corre y una nota escrita dentro del debounce
        # queda sin backup.
        await watcher.stop()

    async def test_start_fallido_no_lanza(self, tmp_path: Path) -> None:
        """Guard existente: el fallo de start() se traga (no es el bug)."""
        watcher = VaultWatcher(vault_path=tmp_path, bot=MagicMock(), chat_id=1)
        observer = MagicMock()
        observer.start.side_effect = OSError("boom")
        with patch("adso.vault_watcher._make_observer", return_value=observer):
            await watcher.start()


class TestV2FlushDelBackupEnShutdown:
    async def test_flush_corre_aunque_el_watcher_falle_al_detenerse(self) -> None:
        from adso.bot import _post_shutdown

        watcher = MagicMock()
        watcher.stop = AsyncMock(side_effect=RuntimeError("cannot join thread"))
        git_backup = MagicMock()
        git_backup.flush = AsyncMock()

        app = MagicMock()
        app.bot_data = {"vault_watcher": watcher, "git_backup": git_backup}

        await _post_shutdown(app)

        git_backup.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# V3 — la limpieza de "Ver también" no reconoce code fences
# ---------------------------------------------------------------------------


class TestV3CodeFences:
    async def test_bloque_de_codigo_no_se_toca(self, vault: Path) -> None:
        cuerpo = (
            "Ejemplo de cómo se ve un bloque de links:\n\n"
            "```markdown\n"
            "## Ver también\n"
            "- [[nota-a]] — ejemplo\n"
            "```\n\n"
            "Fin.\n"
        )
        nota = await create_note(
            {"title": "Doc", "type": "reference", "status": "active", "area": "docencia"},
            cuerpo,
            vault,
        )

        modificadas = await remove_broken_wikilinks(vault, vault / "nota-a.md")

        body = (await read_note(nota)).body
        assert modificadas == 0
        assert "## Ver también" in body
        assert "- [[nota-a]] — ejemplo" in body

    async def test_un_fence_no_cuenta_como_item_del_bloque(self, vault: Path) -> None:
        """El escaneo de items también saltea los bloques de código.

        Si un `## Ver también` real queda sin links y más abajo hay un bloque de
        código con una línea `- algo`, esa línea no es un item del bloque: el
        header debe borrarse igual. Hueco que dejó la primera versión del fix
        (chequeaba el header contra los fences pero no el escaneo interno).
        """
        nota = await create_note(
            {"title": "Doc3", "type": "reference", "status": "active", "area": "docencia"},
            "Texto.\n\n## Ver también\n\n- [[nota-c]] — link\n\n## Ejemplo\n\n"
            "```yaml\n- item de una lista yaml\n```\n",
            vault,
        )

        await remove_broken_wikilinks(vault, vault / "nota-c.md")

        body = (await read_note(nota)).body
        assert "## Ver también" not in body, "el bloque quedó sin items: el header se va"
        assert "- item de una lista yaml" in body, "el bloque de código no se toca"

    async def test_items_de_texto_plano_sobreviven_al_borrar_el_header(self, vault: Path) -> None:
        # Un bloque real de "Ver también" con un link roto y un item de texto
        # plano: al irse el link, el header se borra y el item queda huérfano.
        nota = await create_note(
            {"title": "Doc2", "type": "reference", "status": "active", "area": "docencia"},
            "Texto.\n\n## Ver también\n\n- [[nota-b]] — link\n- ver el capítulo 3 del manual\n",
            vault,
        )

        await remove_broken_wikilinks(vault, vault / "nota-b.md")

        body = (await read_note(nota)).body
        assert "ver el capítulo 3 del manual" in body
        assert "## Ver también" in body



# ---------------------------------------------------------------------------
# V4 — find_tasks: las dos fuentes filtran distinto (issue #61)
# ---------------------------------------------------------------------------
#
# NO es un bug de duplicación, aunque lo parezca: una nota `type: task` se emite
# como nota Y una vez por cada checkbox de su body. Eso es deliberado — los
# checkboxes de una tarea son sus subtareas y se listan como ítems propios (lo
# fija `test_inline_checkboxes_included` en tests/integration/, cuyo fixture usa
# checkboxes llamados "Subtarea"). `seen_paths` se puebla y nunca se consulta:
# es residuo de un diseño anterior que sí deduplicaba.
#
# Lo que sí es una inconsistencia real es que las dos fuentes filtran distinto,
# y estos tests la caracterizan para que la decisión quede explícita.


class TestV4FindTasksFiltradoAsimetrico:
    async def test_los_subitems_de_una_tarea_se_listan(self, vault: Path) -> None:
        """Comportamiento vigente y deliberado: la nota más sus subtareas."""
        await create_note(
            {"title": "Preparar clase", "type": "task", "status": "pending", "area": "docencia"},
            "Pendiente:\n\n- [ ] armar slides\n- [ ] subir el apunte\n",
            vault,
        )

        tareas = await find_tasks(vault, include_inline=True)

        assert len(tareas) == 3
        assert {t.snippet for t in tareas if t.snippet} == {"armar slides", "subir el apunte"}

    async def test_una_tarea_filtrada_por_status_esconde_sus_checkboxes(
        self, vault: Path
    ) -> None:
        """Caracteriza la asimetría: el `continue` de la fuente 1 corta la 2.

        Una nota `type: task` que no pasa el filtro de `status` queda excluida
        por completo, checkboxes incluidos — mientras que los checkboxes de una
        nota que NO es `type: task` se listan sin importar su `status`. Las dos
        fuentes filtran distinto: la 1 por status+area+project, la 2 solo por
        area+project. Ver #61: hay que decidir cuál es la correcta.
        """
        await create_note(
            {"title": "Tarea hecha", "type": "task", "status": "done", "area": "docencia"},
            "- [ ] quedó pendiente igual\n",
            vault,
        )
        await create_note(
            {"title": "Apuntes", "type": "reference", "status": "active", "area": "docencia"},
            "- [ ] revisar el presupuesto\n",
            vault,
        )

        pendientes = await find_tasks(vault, status="pending", include_inline=True)

        snippets = {t.snippet for t in pendientes if t.snippet}
        assert "revisar el presupuesto" in snippets
        assert "quedó pendiente igual" not in snippets
