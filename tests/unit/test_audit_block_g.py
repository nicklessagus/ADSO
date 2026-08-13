"""Tests del bloque G de la auditoría 2026-07-31 — hallazgos bajos.

G1  — `_unique_path` + escritura sin ventana TOCTOU.
G2  — el caché devuelve una copia profunda del frontmatter.
G3  — `GitBackup` cierra el `Repo`.
G4  — las notas quedan 0644, no 0600.
G5  — `note_id`/links no rompen con directorios que contengan ".md".
G6  — `authors` como string no corrompe el reporte.
G7  — `TELEGRAM_ALLOWED_USER_ID` inválido falla con mensaje claro, no en silencio.
G8  — `/buscar` respeta el lock de corrección.
G9  — `reindex.time` inválido da `ConfigError`.
G10 — no se crea proyecto/área con `description` vacía.
G11 — la etiqueta de `create_section` dice "sección".
G12 — los `_index.md` registran `mark_bot_written` y notifican al backup.
G13 — gestión no vuelca la excepción cruda al chat.
G14 — `[Confirmar]` de un preview viejo no consume el `pending_note` actual.
"""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# G2 — copia shallow del frontmatter cacheado
# ---------------------------------------------------------------------------


class TestG2CopiaProfundaDelCache:
    """El docstring promete copia fresca, pero `dict(entry[2])` es shallow: las
    listas (`tags`, `authors`, `keywords`) eran las MISMAS del caché. Un
    `frontmatter["tags"].append(...)` en cualquier caller envenenaba el caché
    para todos los scans siguientes, y cross-thread."""

    def test_mutar_una_lista_no_envenena_el_cache(self, tmp_path: Path) -> None:
        from adso import vault_cache

        vault_cache.clear()
        nota = tmp_path / "n.md"
        nota.write_text(
            "---\ntitle: N\ntags: [uno, dos]\n---\n\ncuerpo\n", encoding="utf-8"
        )

        primera = vault_cache.parse_cached(nota)
        primera.frontmatter["tags"].append("INYECTADO")

        segunda = vault_cache.parse_cached(nota)
        assert "INYECTADO" not in segunda.frontmatter["tags"], (
            "el caché quedó envenenado por una mutación del caller"
        )

    def test_mutar_el_dict_tampoco(self, tmp_path: Path) -> None:
        from adso import vault_cache

        vault_cache.clear()
        nota = tmp_path / "n2.md"
        nota.write_text("---\ntitle: N\n---\n\ncuerpo\n", encoding="utf-8")

        vault_cache.parse_cached(nota).frontmatter["nuevo"] = 1
        assert "nuevo" not in vault_cache.parse_cached(nota).frontmatter


# ---------------------------------------------------------------------------
# G4 — permisos 0600
# ---------------------------------------------------------------------------


class TestG4PermisosDeLasNotas:
    """`mkstemp` crea el temporal con 0600 y `os.replace` los conserva: toda
    nota escrita por el bot quedaba 0600 en vez de 0644. Con el mismo UID
    funciona, pero rompe el acceso por grupo y difiere de una nota creada a
    mano desde Obsidian."""

    def test_nota_nueva_queda_0644(self, tmp_path: Path) -> None:
        from adso.vault_writer import _atomic_write_sync

        destino = tmp_path / "nueva.md"
        _atomic_write_sync(destino, "contenido\n")

        modo = stat.S_IMODE(destino.stat().st_mode)
        assert modo == 0o644, f"quedó {oct(modo)}"

    def test_reescritura_preserva_el_modo_existente(self, tmp_path: Path) -> None:
        """Si el usuario ajustó los permisos a mano, se respetan."""
        from adso.vault_writer import _atomic_write_sync

        destino = tmp_path / "existente.md"
        destino.write_text("viejo\n", encoding="utf-8")
        destino.chmod(0o600)

        _atomic_write_sync(destino, "nuevo\n")

        assert stat.S_IMODE(destino.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# G5 — .replace(".md", "") en cualquier posición del path
# ---------------------------------------------------------------------------


class TestG5NoteIdConDirectorioMd:
    """`.replace(".md", "")` sobre el path relativo completo corrompe el id
    cuando un directorio contiene ".md" en el nombre (`_safe_component` lo
    permite): `01-Projects/tesis.md-notas/n.md` → `01-Projects/tesis-notas/n`."""

    def test_obsidian_link_no_corrompe_el_directorio(self, tmp_path: Path) -> None:
        from adso.reporters import _obsidian_link

        nota = tmp_path / "01-Projects" / "tesis.md-notas" / "n.md"
        nota.parent.mkdir(parents=True)
        nota.write_text("x", encoding="utf-8")

        link = _obsidian_link(tmp_path, nota)
        assert "tesis.md-notas" in link, f"el directorio quedó corrupto: {link}"
        assert not link.endswith("n.md")


# ---------------------------------------------------------------------------
# G6 — authors como string
# ---------------------------------------------------------------------------


class TestG6AuthorsComoString:
    """`authors[:2]` sobre un string devuelve 2 CARACTERES: el join imprimía
    "S, m". El coercer de `llm_schema` solo cubre el payload del LLM, no una
    nota editada a mano o creada por otro cliente."""

    def test_string_no_se_parte_en_caracteres(self) -> None:
        from adso.reporters import _normalize_authors

        assert _normalize_authors("Smith, J.") == ["Smith, J."]

    def test_lista_pasa_intacta(self) -> None:
        from adso.reporters import _normalize_authors

        assert _normalize_authors(["A", "B"]) == ["A", "B"]

    def test_vacio_da_lista_vacia(self) -> None:
        from adso.reporters import _normalize_authors

        assert _normalize_authors(None) == []
        assert _normalize_authors("") == []


# ---------------------------------------------------------------------------
# G9 — reindex.time inválido
# ---------------------------------------------------------------------------


class TestG9HoraInvalida:
    """`datetime.strptime(settings.reindex.time, "%H:%M")` con "3am" moría con
    traceback crudo al arrancar, mientras el resto de validaciones da
    `ConfigError` con mensaje claro."""

    def test_reindex_time_invalido_da_config_error(self, tmp_path: Path) -> None:
        from adso.config import ConfigError, load_settings

        cfg = tmp_path / "config.yaml"
        cfg.write_text("reindex:\n  time: '3am'\n", encoding="utf-8")

        with pytest.raises(ConfigError, match="reindex.time"):
            load_settings(cfg)

    def test_weekly_report_time_invalido_da_config_error(self, tmp_path: Path) -> None:
        from adso.config import ConfigError, load_settings

        cfg = tmp_path / "config.yaml"
        cfg.write_text("weekly_report:\n  time: '25:00'\n", encoding="utf-8")

        with pytest.raises(ConfigError, match="weekly_report.time"):
            load_settings(cfg)

    def test_hora_valida_pasa(self, tmp_path: Path) -> None:
        from adso.config import load_settings

        cfg = tmp_path / "config.yaml"
        cfg.write_text("reindex:\n  time: '03:00'\n", encoding="utf-8")
        assert load_settings(cfg).reindex.time == "03:00"


# ---------------------------------------------------------------------------
# G11 — etiqueta de create_section
# ---------------------------------------------------------------------------


class TestG11EtiquetaDeSeccion:
    """`"proyecto" if "project" in operation else "área"` — como "project" no
    está en "create_section", el prompt decía "Para crear el **área** hacen
    falta: nombre de la sección…"."""

    def test_seccion(self) -> None:
        from adso.handlers.manage import _operation_label

        assert _operation_label("create_section") == "sección"

    def test_proyecto(self) -> None:
        from adso.handlers.manage import _operation_label

        assert _operation_label("create_project") == "proyecto"

    def test_area(self) -> None:
        from adso.handlers.manage import _operation_label

        assert _operation_label("create_area") == "área"


# ---------------------------------------------------------------------------
# G8 — /buscar ignoraba el lock de corrección
# ---------------------------------------------------------------------------


class TestG8BuscarRespetaElLock:
    """A diferencia de `/status`, `/clasificar` y `/reporte`, `/buscar` no
    chequeaba `_is_awaiting_text_input` ni `_has_pending_keyboard` —
    contradice CLAUDE.md ("durante el lock … `/comandos` quedan bloqueados")."""

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_bloqueado_en_modo_correccion(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import query as query_mod

        update = make_update("/buscar algo")
        mock_context.args = ["algo"]
        mock_context.user_data["pending_note"] = {"awaiting_correction": True}

        with patch.object(query_mod, "run_query", AsyncMock()) as mock_run:
            await query_mod.handle_buscar(update, mock_context)

        mock_run.assert_not_awaited()
        assert "pendiente" in update.message.reply_text.await_args[0][0]

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_bloqueado_con_teclado_pendiente(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import query as query_mod

        update = make_update("/buscar algo")
        mock_context.args = ["algo"]
        mock_context.user_data["pending_report"] = {"x": 1}

        with patch.object(query_mod, "run_query", AsyncMock()) as mock_run:
            await query_mod.handle_buscar(update, mock_context)

        mock_run.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_sin_estado_pendiente_busca(self, mock_context, make_update) -> None:
        from adso.handlers import query as query_mod

        update = make_update("/buscar algo")
        mock_context.args = ["algo"]

        with patch.object(query_mod, "run_query", AsyncMock()) as mock_run:
            await query_mod.handle_buscar(update, mock_context)

        mock_run.assert_awaited_once()


# ---------------------------------------------------------------------------
# G14 — [Confirmar] de un preview viejo
# ---------------------------------------------------------------------------


class TestG14ConfirmarPreviewViejo:
    """`_cb_confirm` no vinculaba el callback con el mensaje: un `Confirmar`
    de un preview anterior (scrolleando hacia arriba) escribía la nota NUEVA
    editando el mensaje VIEJO, y el preview vigente quedaba con botones sin
    estado detrás."""

    @pytest.mark.asyncio
    async def test_confirmar_desde_otro_mensaje_no_consume_el_pendiente(
        self, mock_context, vault_path: Path
    ) -> None:
        from adso.handlers import capture

        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "La vigente", "type": "reference"},
                "body": "cuerpo",
                "suggested_links": [],
            },
            "msg_id": 500,
        }

        query = MagicMock()
        query.message = MagicMock(message_id=123)  # preview viejo
        query.edit_message_text = AsyncMock()

        await capture._cb_confirm(query, mock_context, vault_path)

        assert not list(vault_path.rglob("*.md")), "escribió desde un preview viejo"
        assert mock_context.user_data.get("pending_note"), (
            "el pendiente vigente se consumió desde otro mensaje"
        )

    @pytest.mark.asyncio
    async def test_confirmar_desde_el_preview_vigente_escribe(
        self, mock_context, vault_path: Path
    ) -> None:
        from adso.handlers import capture

        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "La vigente", "type": "reference"},
                "body": "cuerpo",
                "suggested_links": [],
            },
            "msg_id": 500,
        }

        query = MagicMock()
        query.message = MagicMock(message_id=500)
        query.edit_message_text = AsyncMock()

        await capture._cb_confirm(query, mock_context, vault_path)

        assert list(vault_path.rglob("*.md"))

    @pytest.mark.asyncio
    async def test_sin_msg_id_sigue_funcionando(
        self, mock_context, vault_path: Path
    ) -> None:
        """Estado de una versión anterior del bot (sin msg_id): no se rompe."""
        from adso.handlers import capture

        mock_context.user_data["pending_note"] = {
            "payload": {
                "frontmatter": {"title": "Sin msg_id", "type": "reference"},
                "body": "cuerpo",
                "suggested_links": [],
            },
        }

        query = MagicMock()
        query.message = MagicMock(message_id=999)
        query.edit_message_text = AsyncMock()

        await capture._cb_confirm(query, mock_context, vault_path)

        assert list(vault_path.rglob("*.md"))


# ---------------------------------------------------------------------------
# G7 — TELEGRAM_ALLOWED_USER_ID mal formado
# ---------------------------------------------------------------------------


class TestG7AllowedUserIdInvalido:
    """(a) `security.py` parsea lista separada por comas pero `config.py` hacía
    `int(...)` directo → con "123,456" el bot moría con ValueError crudo.
    (b) el filtro `isdigit()` descartaba valores no numéricos sin error: con
    "12a" el set quedaba vacío → lockout total y silencioso."""

    def test_valor_no_numerico_no_deja_el_set_vacio_en_silencio(self) -> None:
        from adso.security import _parse_allowed_ids

        with pytest.raises(RuntimeError, match="TELEGRAM_ALLOWED_USER_ID"):
            _parse_allowed_ids("12a")

    def test_vacio_lanza(self) -> None:
        from adso.security import _parse_allowed_ids

        with pytest.raises(RuntimeError):
            _parse_allowed_ids("   ")

    def test_multiples_ids(self) -> None:
        from adso.security import _parse_allowed_ids

        assert _parse_allowed_ids("123,456") == {123, 456}

    def test_id_unico(self) -> None:
        from adso.security import _parse_allowed_ids

        assert _parse_allowed_ids("42") == {42}

    def test_config_acepta_multi_id_sin_morir(self, monkeypatch, tmp_path: Path) -> None:
        from adso.config import load_settings

        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123,456")
        cfg = tmp_path / "config.yaml"
        cfg.write_text("---\n", encoding="utf-8")

        # No debe lanzar ValueError crudo; toma el primero como id principal.
        assert load_settings(cfg).telegram_allowed_user_id == 123


# ---------------------------------------------------------------------------
# G10 / G12 / G13 — gestión
# ---------------------------------------------------------------------------


def _pending_manage(operation: str, **params) -> dict:
    return {"payload": {"operation": operation, "params": params}}


class TestG10DescripcionObligatoria:
    """El flujo por botón dejaba `description=""` y ofrecía confirmar directo;
    `_cb_manage_confirm` no re-validaba, así que se creaba el `project-index`
    con descripción vacía — viola "el bot la pide y no permite omitirla"."""

    @pytest.mark.asyncio
    async def test_no_crea_proyecto_sin_descripcion(
        self, mock_context, vault_path: Path
    ) -> None:
        from adso.handlers import manage

        mock_context.user_data["pending_operation"] = _pending_manage(
            "create_project", name="tesis", description=""
        )
        query = MagicMock()
        query.edit_message_text = AsyncMock()

        await manage._cb_manage_confirm(query, mock_context, vault_path)

        assert not (vault_path / "01-Projects" / "tesis").exists()
        assert "descripción" in query.edit_message_text.await_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_repone_el_estado_para_poder_retomar(
        self, mock_context, vault_path: Path
    ) -> None:
        """Sin reponer `pending_operation`, el texto siguiente del usuario no
        tendría a qué operación aplicarse."""
        from adso.handlers import manage

        mock_context.user_data["pending_operation"] = _pending_manage(
            "create_area", name="docencia", description=""
        )
        query = MagicMock()
        query.edit_message_text = AsyncMock()

        await manage._cb_manage_confirm(query, mock_context, vault_path)

        assert mock_context.user_data.get("pending_operation")
        assert mock_context.user_data.get("manage_missing_fields")

    @pytest.mark.asyncio
    async def test_con_descripcion_si_crea(
        self, mock_context, vault_path: Path
    ) -> None:
        from adso.handlers import manage

        mock_context.user_data["pending_operation"] = _pending_manage(
            "create_project", name="tesis", description="Doctorado en astronomía"
        )
        query = MagicMock()
        query.edit_message_text = AsyncMock()

        await manage._cb_manage_confirm(query, mock_context, vault_path)

        assert (vault_path / "01-Projects" / "tesis" / "_index.md").exists()


class TestG12IndexRegistraEscritura:
    """El commit de backup y el no-doble-embed dependían de que el watcher
    tratara la escritura propia como cambio externo: notificación espuria en
    debug, y nada si el watcher está caído."""

    @pytest.mark.asyncio
    async def test_marca_bot_written_y_notifica_al_backup(
        self, mock_context, vault_path: Path
    ) -> None:
        from adso.handlers import manage

        backup = MagicMock()
        backup.notify = AsyncMock()
        mock_context.bot_data["git_backup"] = backup
        mock_context.user_data["pending_operation"] = _pending_manage(
            "create_area", name="docencia", description="Clases y materiales"
        )
        query = MagicMock()
        query.edit_message_text = AsyncMock()

        await manage._cb_manage_confirm(query, mock_context, vault_path)

        backup.notify.assert_awaited_once()
        registrados = mock_context.bot_data.get("bot_written_paths") or set()
        assert any("_index.md" in str(p) for p in registrados)


class TestG13SinExcepcionCrudaAlChat:
    """`edit_message_text(f"Error: {e}")` volcaba la excepción cruda —con
    paths internos— al chat del usuario."""

    @pytest.mark.asyncio
    async def test_mensaje_generico(self, mock_context, vault_path: Path) -> None:
        from adso.handlers import manage

        mock_context.user_data["pending_operation"] = _pending_manage(
            "create_project", name="tesis", description="Una descripción"
        )
        query = MagicMock()
        query.edit_message_text = AsyncMock()

        with patch.object(
            manage, "create_note", AsyncMock(side_effect=OSError("/ruta/interna/secreta"))
        ):
            await manage._cb_manage_confirm(query, mock_context, vault_path)

        mensaje = query.edit_message_text.await_args[0][0]
        assert "/ruta/interna/secreta" not in mensaje
        assert "logs" in mensaje.lower()


# ---------------------------------------------------------------------------
# G1 — TOCTOU entre elegir el nombre y escribirlo
# ---------------------------------------------------------------------------


class TestG1SinVentanaTOCTOU:
    """`_unique_path` elegía el nombre y recién varios `await` después el
    `os.replace` escribía. Una captura del usuario y `reclassify_inbox`
    creando notas con el mismo título el mismo día elegían el mismo candidato
    y el segundo `os.replace` **sobrescribía en silencio** la primera."""

    @pytest.mark.asyncio
    async def test_dos_notas_concurrentes_con_el_mismo_titulo(
        self, vault_path: Path
    ) -> None:
        import asyncio

        from adso.vault_writer import create_note

        fm = {"title": "Mismo título", "type": "reference", "status": "active"}

        paths = await asyncio.gather(
            create_note(dict(fm), "cuerpo A", vault_path),
            create_note(dict(fm), "cuerpo B", vault_path),
        )

        assert paths[0] != paths[1], "las dos notas fueron al mismo path"
        cuerpos = {p.read_text(encoding="utf-8").split("---")[-1].strip() for p in paths}
        assert cuerpos == {"cuerpo A", "cuerpo B"}, (
            f"una nota pisó a la otra: {cuerpos}"
        )

    @pytest.mark.asyncio
    async def test_muchas_concurrentes_no_se_pisan(self, vault_path: Path) -> None:
        import asyncio

        from adso.vault_writer import create_note

        fm = {"title": "Colisión", "type": "reference", "status": "active"}
        paths = await asyncio.gather(*[
            create_note(dict(fm), f"cuerpo {i}", vault_path) for i in range(8)
        ])

        assert len(set(paths)) == 8
        cuerpos = {p.read_text(encoding="utf-8").split("---")[-1].strip() for p in paths}
        assert len(cuerpos) == 8

    @pytest.mark.asyncio
    async def test_dry_run_no_escribe(self, vault_path: Path) -> None:
        from adso.vault_writer import create_note

        fm = {"title": "Solo preview", "type": "reference", "status": "active"}
        path = await create_note(fm, "cuerpo", vault_path, dry_run=True)

        assert not path.exists()


class TestG10SoloFaltaLaDescripcion:
    """Caso destapado por el fix de G10: cuando lo único que falta es la
    descripción, `_handle_manage_missing_fields` tomaba el texto entero como
    NOMBRE y pisaba el que ya se había resuelto por regex."""

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_el_texto_es_la_descripcion_no_el_nombre(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import manage

        mock_context.user_data["pending_operation"] = _pending_manage(
            "create_project", name="tesis", description=""
        )
        mock_context.user_data["manage_missing_fields"] = ["descripción"]

        update = make_update("Doctorado en astronomía")
        await manage._handle_manage_missing_fields(
            update, mock_context, "Doctorado en astronomía"
        )

        params = mock_context.user_data["pending_operation"]["payload"]["params"]
        assert params["name"] == "tesis", "el nombre fue pisado por la descripción"
        assert params["description"] == "Doctorado en astronomía"

    @pytest.mark.asyncio
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_formato_nombre_guion_descripcion_sigue_andando(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import manage

        mock_context.user_data["pending_operation"] = _pending_manage(
            "create_area", name="", description=""
        )
        mock_context.user_data["manage_missing_fields"] = ["nombre", "descripción"]

        update = make_update("Docencia — gestión de clases")
        await manage._handle_manage_missing_fields(
            update, mock_context, "Docencia — gestión de clases"
        )

        params = mock_context.user_data["pending_operation"]["payload"]["params"]
        assert params["name"] == "Docencia"
        assert params["description"] == "gestión de clases"
