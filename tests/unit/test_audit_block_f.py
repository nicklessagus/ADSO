"""Tests del bloque F de la auditoría 2026-07-31 — otros medios.

F1  — `mark_bot_written` corre aunque el backup esté deshabilitado.
F2  — el watcher debouncea con trailing edge en vez de descartar el último evento.
F3  — el scope de reportes sobrevive a nombres largos.
F4  — el `callback_data` de destino nunca supera los 64 bytes de Telegram.
F5  — los selectores de `[Reubicar]` ven proyectos/áreas sin `_index.md`.
F6  — `/reset` y `[Cancelar]` borran el temporal del adjunto (`_resource_file`).
F7  — el entry de error de la API de arXiv no se parsea como paper.
F8  — IDs viejos de arXiv con subclase (`math.GT/0309136`) se detectan.
F10 — el `rglob` del reindex no bloquea el event loop.
F11 — `remove_broken_wikilinks` no reescribe notas que no cambió.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adso.arxiv_client import extract_arxiv_id


# ---------------------------------------------------------------------------
# F8 — IDs viejos con subclase
# ---------------------------------------------------------------------------


class TestF8ArxivIdsViejos:
    """`[a-z\\-]+/\\d+` no incluía el punto de la subclase del formato viejo, así
    que esos links caían en silencio al flujo de link genérico."""

    def test_id_viejo_con_subclase(self) -> None:
        assert extract_arxiv_id("https://arxiv.org/abs/math.GT/0309136") == (
            "math.GT/0309136"
        )

    def test_id_viejo_con_subclase_y_guion(self) -> None:
        assert extract_arxiv_id(
            "https://arxiv.org/abs/cond-mat.str-el/0509127"
        ) == "cond-mat.str-el/0509127"

    def test_id_viejo_sin_subclase_sigue_andando(self) -> None:
        assert extract_arxiv_id("https://arxiv.org/abs/hep-ph/0512345") == (
            "hep-ph/0512345"
        )

    def test_id_moderno_sigue_andando(self) -> None:
        assert extract_arxiv_id("https://arxiv.org/abs/2301.12345") == "2301.12345"

    def test_id_moderno_con_version_se_strippea(self) -> None:
        """La versión se remueve a propósito: una nota por paper, no por
        revisión (`source_url` apunta a arxiv.org sin versión)."""
        assert extract_arxiv_id("https://arxiv.org/pdf/2301.12345v2") == "2301.12345"

    def test_no_arxiv(self) -> None:
        assert extract_arxiv_id("https://example.com/paper") is None


# ---------------------------------------------------------------------------
# F6 — el temporal del adjunto quedaba filtrado en tmpfs
# ---------------------------------------------------------------------------


class TestF6TempDelAdjunto:
    """`capture.py` guarda `_resource_file` (con underscore) pero
    `_cleanup_pending` buscaba `resource_file`. Con una imagen clasificada y
    preview pendiente, `/reset` o `[Cancelar]` popeaban el estado sin borrar el
    temporal. En la RPi4 `/tmp` es tmpfs: es RAM filtrada hasta el reinicio."""

    def test_cleanup_borra_el_temp_de_resource_file_con_underscore(
        self, tmp_path: Path
    ) -> None:
        from unittest.mock import MagicMock

        from adso.bot_utils import _cleanup_pending

        temp = tmp_path / "adjunto.png"
        temp.write_bytes(b"x")

        context = MagicMock()
        context.user_data = {
            "pending_note": {
                "payload": {},
                "_resource_file": {"temp_path": str(temp), "filename": "adjunto.png"},
            }
        }

        _cleanup_pending(context, "pending_note")

        assert not temp.exists(), "el temporal quedó filtrado en tmpfs"

    def test_sigue_borrando_el_de_resource_file_sin_underscore(
        self, tmp_path: Path
    ) -> None:
        from unittest.mock import MagicMock

        from adso.bot_utils import _cleanup_pending

        temp = tmp_path / "audio.ogg"
        temp.write_bytes(b"x")

        context = MagicMock()
        context.user_data = {
            "pending_transcript": {"resource_file": {"temp_path": str(temp)}}
        }

        _cleanup_pending(context, "pending_transcript")

        assert not temp.exists()


# ---------------------------------------------------------------------------
# F7 — el entry de error de arXiv se parseaba como paper
# ---------------------------------------------------------------------------


class TestF7EntryDeErrorDeArxiv:
    """Ante un ID bien formado pero inexistente, la API devuelve un feed CON un
    entry (título "Error", `<id>` apuntando a `.../api/errors`), así que el
    chequeo `if not entries` no lo atrapa. El usuario veía el preview de una
    "nota" titulada Error en vez del fallback de link genérico."""

    _FEED_ERROR = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/api/errors#incorrect_id_format</id>
    <title>Error</title>
    <summary>incorrect id format for 9999.99999</summary>
  </entry>
</feed>"""

    _FEED_OK = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2301.12345v1</id>
    <title>Un paper de verdad</title>
    <summary>Este es el abstract.</summary>
    <published>2023-01-15T00:00:00Z</published>
  </entry>
</feed>"""

    def test_entry_de_error_lanza_en_vez_de_devolver_basura(self) -> None:
        from adso import arxiv_client

        with pytest.raises(ValueError):
            arxiv_client._parse_feed_xml(self._FEED_ERROR)

    def test_feed_valido_se_parsea(self) -> None:
        from adso import arxiv_client

        meta = arxiv_client._parse_feed_xml(self._FEED_OK)
        assert meta["title"] == "Un paper de verdad"
        assert meta["arxiv_id"] == "2301.12345"

    def test_feed_sin_entries_lanza(self) -> None:
        from adso import arxiv_client

        vacio = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        with pytest.raises(ValueError):
            arxiv_client._parse_feed_xml(vacio)


# ---------------------------------------------------------------------------
# F11 — reescrituras espurias al limpiar wikilinks
# ---------------------------------------------------------------------------


class TestF11WikilinksSinReescrituraEspuria:
    """`new_content.rstrip("\\n") + "\\n"` se aplicaba siempre: una nota que
    menciona el link fuera de `## Ver también` y cuyo newline final difiere se
    reescribía sin cambio real → mtime bump → evento del watcher → re-embed
    espurio (llamada a Gemini) + churn del backup, por cada delete externo."""

    @pytest.mark.asyncio
    async def test_nota_sin_cambios_reales_no_se_reescribe(
        self, vault_path: Path
    ) -> None:
        from adso.vault_writer import remove_broken_wikilinks

        # Menciona [[borrada]] pero NO dentro de "## Ver también", y termina
        # con doble newline: los strippers no la tocan, solo el rstrip.
        nota = vault_path / "00-Inbox" / "otra.md"
        nota.write_text(
            "---\ntitle: Otra\n---\n\nMenciono [[borrada]] en el cuerpo.\n\n",
            encoding="utf-8",
        )
        mtime_antes = nota.stat().st_mtime_ns

        await remove_broken_wikilinks(vault_path, vault_path / "00-Inbox" / "borrada.md")

        assert nota.stat().st_mtime_ns == mtime_antes, (
            "la nota se reescribió sin cambios reales → re-embed espurio"
        )
        assert "[[borrada]]" in nota.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_link_en_ver_tambien_si_se_limpia(self, vault_path: Path) -> None:
        from adso.vault_writer import remove_broken_wikilinks

        nota = vault_path / "00-Inbox" / "conlink.md"
        nota.write_text(
            "---\ntitle: Con link\n---\n\nCuerpo.\n\n## Ver también\n\n"
            "- [[borrada]] — Nota borrada\n- [[otra]] — Otra\n",
            encoding="utf-8",
        )

        modificadas = await remove_broken_wikilinks(
            vault_path, vault_path / "00-Inbox" / "borrada.md"
        )

        contenido = nota.read_text(encoding="utf-8")
        assert modificadas == 1
        assert "[[borrada]]" not in contenido
        assert "[[otra]]" in contenido


# ---------------------------------------------------------------------------
# F1 — mark_bot_written atrapado dentro del `if git_backup`
# ---------------------------------------------------------------------------


class TestF1MarkBotWrittenSinBackup:
    """Con `backup.enabled: false` no hay `git_backup` en `bot_data`, así que
    el registro anti-doble-embed no corría: la nota se indexaba inline y además
    el watcher la trataba como cambio externo y la re-embebía — llamada
    redundante a Gemini Embedding por cada captura."""

    @pytest.mark.asyncio
    async def test_registra_el_path_aunque_no_haya_backup(
        self, mock_context, vault_path: Path
    ) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from adso.handlers import capture

        mock_context.bot_data["git_backup"] = None
        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "Sin backup", "type": "reference"},
                "body": "cuerpo",
                "suggested_links": [],
            },
        }

        query = MagicMock()
        query.edit_message_text = AsyncMock()

        await capture._cb_confirm(query, mock_context, vault_path)

        escritas = [p for p in vault_path.rglob("*.md")]
        assert escritas, "la nota debía escribirse"
        registrados = mock_context.bot_data.get("bot_written_paths") or set()
        assert str(escritas[0]) in {str(p) for p in registrados}, (
            "el path no quedó registrado: el watcher lo va a re-embeber"
        )


# ---------------------------------------------------------------------------
# F2 — el dedup del watcher descartaba el ÚLTIMO evento
# ---------------------------------------------------------------------------


class TestF2DebounceConTrailingEdge:
    """`_is_duplicate` dropeaba el segundo evento de la ventana sin programar
    nada. Obsidian guarda dos veces en <2s (autosave) y el usuario deja de
    editar: el primer save disparaba el re-embed con contenido INTERMEDIO y el
    save final se descartaba → el embedding quedaba desactualizado hasta el
    reindex nocturno. Es lo contrario del objetivo del watcher."""

    def _watcher(self, vault_path: Path, cambios: list):
        from unittest.mock import AsyncMock, MagicMock

        from adso.vault_watcher import VaultWatcher

        async def on_change(path: Path) -> None:
            cambios.append(path)

        w = VaultWatcher(
            vault_path=vault_path,
            bot=MagicMock(send_message=AsyncMock()),
            chat_id=1,
            on_external_change=on_change,
        )
        from datetime import timedelta
        w._dedup_window = timedelta(seconds=0.05)
        return w

    @pytest.mark.asyncio
    async def test_el_ultimo_evento_no_se_pierde(self, vault_path: Path) -> None:
        import asyncio

        from adso.vault_watcher import _VaultEvent

        cambios: list[Path] = []
        w = self._watcher(vault_path, cambios)
        nota = vault_path / "00-Inbox" / "editada.md"

        task = asyncio.create_task(w._dispatch_loop())
        try:
            # Dos saves seguidos dentro de la ventana (autosave de Obsidian).
            await w._queue.put(_VaultEvent(path=nota, is_conflict=False))
            await w._queue.put(_VaultEvent(path=nota, is_conflict=False))
            # Esperar a que venza la ventana + margen del trailing edge.
            await asyncio.sleep(0.25)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.gather(*w._bg_tasks, return_exceptions=True)

        assert len(cambios) == 2, (
            f"el save final se descartó (llamadas: {len(cambios)}): el embedding "
            "queda con contenido intermedio hasta el reindex nocturno"
        )

    @pytest.mark.asyncio
    async def test_un_solo_evento_dispara_una_sola_vez(self, vault_path: Path) -> None:
        import asyncio

        from adso.vault_watcher import _VaultEvent

        cambios: list[Path] = []
        w = self._watcher(vault_path, cambios)
        nota = vault_path / "00-Inbox" / "unica.md"

        task = asyncio.create_task(w._dispatch_loop())
        try:
            await w._queue.put(_VaultEvent(path=nota, is_conflict=False))
            await asyncio.sleep(0.25)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.gather(*w._bg_tasks, return_exceptions=True)

        assert len(cambios) == 1, "el dedup no debe duplicar un evento único"

    @pytest.mark.asyncio
    async def test_rafaga_larga_colapsa_a_dos_llamadas(self, vault_path: Path) -> None:
        """Cinco eventos seguidos → uno inmediato + uno al final de la ventana.

        El punto del debounce: no llamar a Gemini cinco veces, pero tampoco
        perder el estado final.
        """
        import asyncio

        from adso.vault_watcher import _VaultEvent

        cambios: list[Path] = []
        w = self._watcher(vault_path, cambios)
        nota = vault_path / "00-Inbox" / "rafaga.md"

        task = asyncio.create_task(w._dispatch_loop())
        try:
            for _ in range(5):
                await w._queue.put(_VaultEvent(path=nota, is_conflict=False))
            await asyncio.sleep(0.3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.gather(*w._bg_tasks, return_exceptions=True)

        assert len(cambios) == 2


# ---------------------------------------------------------------------------
# F3 / F4 / F5 — selectores de destino y de scope
# ---------------------------------------------------------------------------


_NOMBRE_LARGO = "Introducción a la ciencia de datos aplicada a la astronomía"


class TestF4CallbackDataDentroDelLimite:
    """Telegram corta el `callback_data` en 64 **bytes**. `dest:area:{nombre}`
    iba sin truncar: un directorio de ~27 chars acentuados ya lo supera y
    `[Elegir área]` respondía BadRequest."""

    @pytest.mark.asyncio
    async def test_selector_de_area_con_nombre_larguisimo(self, vault_path: Path) -> None:
        from adso.keyboards import build_area_selector

        (vault_path / "02-Areas" / _NOMBRE_LARGO).mkdir(parents=True)
        (vault_path / "02-Areas" / _NOMBRE_LARGO / "nota.md").write_text(
            "---\ntitle: x\n---\n", encoding="utf-8"
        )

        kb = await build_area_selector(vault_path)
        for fila in kb.inline_keyboard:
            for boton in fila:
                assert len(boton.callback_data.encode("utf-8")) <= 64, (
                    f"callback_data de {len(boton.callback_data.encode('utf-8'))} bytes"
                )

    @pytest.mark.asyncio
    async def test_selector_de_proyecto_con_nombre_larguisimo(
        self, vault_path: Path
    ) -> None:
        from adso.keyboards import build_project_selector

        (vault_path / "01-Projects" / _NOMBRE_LARGO).mkdir(parents=True)
        (vault_path / "01-Projects" / _NOMBRE_LARGO / "nota.md").write_text(
            "---\ntitle: x\n---\n", encoding="utf-8"
        )

        kb = await build_project_selector(vault_path)
        for fila in kb.inline_keyboard:
            for boton in fila:
                assert len(boton.callback_data.encode("utf-8")) <= 64

    def test_teclado_de_reportes_dentro_del_limite(self) -> None:
        from adso.keyboards import build_report_items_keyboard

        items = [{"name": _NOMBRE_LARGO, "description": ""}]
        kb = build_report_items_keyboard(items, True, "rep:scope:", "rep:menu")
        for fila in kb.inline_keyboard:
            for boton in fila:
                assert len(boton.callback_data.encode("utf-8")) <= 64


class TestF3ScopeSobreviveNombresLargos:
    """El nombre truncado a 32 chars viajaba en el `callback_data` y
    `scope_report` armaba `01-Projects/{truncado}` — un path inexistente →
    "No se encontraron notas", reporte vacío engañoso y sin error."""

    @pytest.mark.asyncio
    async def test_el_token_resuelve_al_nombre_completo(self, vault_path: Path) -> None:
        from adso.keyboards import build_report_items_keyboard, resolve_item_token

        (vault_path / "01-Projects" / _NOMBRE_LARGO).mkdir(parents=True)
        (vault_path / "01-Projects" / _NOMBRE_LARGO / "n.md").write_text(
            "---\ntitle: x\n---\n", encoding="utf-8"
        )

        items = [{"name": _NOMBRE_LARGO, "description": ""}]
        kb = build_report_items_keyboard(items, True, "rep:scope:", "rep:menu")
        token = kb.inline_keyboard[0][0].callback_data.split(":")[-1]

        assert await resolve_item_token(token, vault_path, is_project=True) == (
            _NOMBRE_LARGO
        )

    @pytest.mark.asyncio
    async def test_nombre_literal_viejo_sigue_resolviendo(
        self, vault_path: Path
    ) -> None:
        """Un teclado emitido antes del cambio sigue funcionando."""
        from adso.keyboards import resolve_item_token

        (vault_path / "02-Areas" / "docencia").mkdir(parents=True)
        (vault_path / "02-Areas" / "docencia" / "n.md").write_text(
            "---\ntitle: x\n---\n", encoding="utf-8"
        )

        assert await resolve_item_token("docencia", vault_path, is_project=False) == (
            "docencia"
        )


class TestF5SelectoresVenCarpetasSinIndice:
    """CLAUDE.md garantiza que todo proyecto/área con notas aparece "en los
    reportes y teclados" aunque no tenga `_index.md`. Los reportes usan
    `_get_existing_items`, pero estos selectores buscaban por
    `type: area-index`: un área sin índice era invisible al reubicar aunque
    apareciera en `/reporte`."""

    @pytest.mark.asyncio
    async def test_area_sin_index_aparece(self, vault_path: Path) -> None:
        from adso.keyboards import build_area_selector

        (vault_path / "02-Areas" / "docencia").mkdir(parents=True)
        (vault_path / "02-Areas" / "docencia" / "clase.md").write_text(
            "---\ntitle: Clase\n---\n", encoding="utf-8"
        )

        kb = await build_area_selector(vault_path)
        etiquetas = [b.text for fila in kb.inline_keyboard for b in fila]
        assert "docencia" in etiquetas

    @pytest.mark.asyncio
    async def test_proyecto_sin_index_aparece(self, vault_path: Path) -> None:
        from adso.keyboards import build_project_selector

        (vault_path / "01-Projects" / "tesis").mkdir(parents=True)
        (vault_path / "01-Projects" / "tesis" / "cap1.md").write_text(
            "---\ntitle: Cap 1\n---\n", encoding="utf-8"
        )

        kb = await build_project_selector(vault_path)
        etiquetas = [b.text for fila in kb.inline_keyboard for b in fila]
        assert "tesis" in etiquetas
