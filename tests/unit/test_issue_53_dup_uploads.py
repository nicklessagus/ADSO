"""Reproductor del issue #53 — el mismo archivo subido dos veces crea dos notas.

Mismo contrato que `test_audit_2026_08_data.py`: cada test **especifica el
comportamiento correcto** y se escribió reproduciendo el bug (fallaba) antes de
aplicar el fix.

Bug con evidencia en el vault real: `chapter-5.pdf` produjo dos notas el mismo
día (`01-Projects/ADSO/2026-08-13-capitulo-5-simulaciones.md` y
`01-Projects/Tesis/2026-08-13-simulaciones.md`). La detección de duplicados de
la Fase 5 busca por `source_url` y `doi`, y un PDF subido por Telegram no tiene
ninguno de los dos, así que nunca se disparaba.

Lo delator: `save_resource` **ya calculaba el SHA-256 y ya detectaba que era el
mismo archivo** — por eso en `03-Resources/` hay un solo `chapter-5.pdf`. El
dato existía y se descartaba.

La clave del dedup es el **hash del contenido**, no el nombre: dos archivos
distintos pueden llamarse igual, y el mismo archivo puede llegar con nombres
distintos.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adso.bot_utils import _cleanup_pending, _has_pending_keyboard

CONTENIDO = b"%PDF-1.4 capitulo 5: simulaciones\n" + b"x" * 500
OTRO_CONTENIDO = b"%PDF-1.4 otro documento distinto\n" + b"y" * 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _documento(filename: str, contenido: bytes) -> MagicMock:
    """Documento de Telegram cuya descarga escribe `contenido` en el temporal."""
    tg_file = MagicMock()
    tg_file.download_to_drive = AsyncMock(
        side_effect=lambda destino: Path(destino).write_bytes(contenido)
    )
    doc = MagicMock()
    doc.file_name = filename
    doc.file_size = len(contenido)  # declarado ⇒ no hay re-check post-descarga
    doc.get_file = AsyncMock(return_value=tg_file)
    return doc


def _recurso(vault_path: Path, nombre: str, contenido: bytes) -> Path:
    """Deja un archivo en 03-Resources/, como lo haría `save_resource`."""
    destino = vault_path / "03-Resources" / nombre
    destino.write_bytes(contenido)
    return destino


def _nota_con_adjunto(
    vault_path: Path,
    rel_path: str,
    recurso: str,
    *,
    en_frontmatter: bool = True,
) -> Path:
    """Nota que referencia un archivo de 03-Resources/.

    `en_frontmatter=False` deja el embed solo en el body (`![[archivo]]`), que
    es la otra forma en la que `_cb_confirm` ata la nota a su adjunto.
    """
    path = vault_path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_extra = f'source_file: "[[{recurso}]]"\n' if en_frontmatter else ""
    body = f"Cuerpo de la nota.\n\n![[{recurso}]]\n" if not en_frontmatter else "Cuerpo.\n"
    path.write_text(
        "---\n"
        'title: "Capítulo 5"\n'
        "type: reference\n"
        "status: active\n"
        f"{fm_extra}"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )
    return path


async def _subir(mock_context, make_update, filename: str, contenido: bytes) -> MagicMock:
    """Corre `handle_document` con un documento de contenido dado."""
    from adso.handlers import input as input_mod

    update = make_update()
    update.message.document = _documento(filename, contenido)
    update.message.caption = None
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=99))

    await input_mod.handle_document(update, mock_context)
    return update


def _texto_de_reply(update: MagicMock) -> str:
    return str(update.message.reply_text.call_args)


# ---------------------------------------------------------------------------
# Regresión — el mismo contenido ya en el vault avisa en vez de duplicar
# ---------------------------------------------------------------------------


class TestArchivoDuplicado:

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_pdf_ya_referenciado_avisa_en_vez_de_seguir_el_flujo(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        _nota_con_adjunto(vault, "01-Projects/Tesis/2026-08-13-simulaciones.md", "chapter-5.pdf")

        update = await _subir(mock_context, make_update, "chapter-5.pdf", CONTENIDO)

        assert "pending_read_status" not in mock_context.user_data, (
            "el PDF duplicado siguió al flujo normal: termina en una segunda nota"
        )
        assert mock_context.user_data.get("pending_duplicate_doc")
        texto = _texto_de_reply(update)
        assert "01-Projects/Tesis/2026-08-13-simulaciones.md" in texto
        assert "Crear igual" in texto, "debe ofrecerse el teclado [Cancelar]/[Crear igual]"

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_dedup_por_hash_y_no_por_nombre_del_archivo_subido(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        """El mismo contenido llega con otro nombre: sigue siendo el mismo archivo."""
        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        _nota_con_adjunto(vault, "01-Projects/Tesis/simulaciones.md", "chapter-5.pdf")

        await _subir(mock_context, make_update, "cap5-final.pdf", CONTENIDO)

        assert "pending_duplicate_doc" in mock_context.user_data
        assert "pending_read_status" not in mock_context.user_data

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_detecta_la_nota_duena_por_el_embed_del_body(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        """Sin `source_file` en el frontmatter, la nota igual embebe `![[archivo]]`."""
        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        _nota_con_adjunto(
            vault, "01-Projects/Tesis/simulaciones.md", "chapter-5.pdf", en_frontmatter=False
        )

        update = await _subir(mock_context, make_update, "chapter-5.pdf", CONTENIDO)

        assert "pending_duplicate_doc" in mock_context.user_data
        assert "01-Projects/Tesis/simulaciones.md" in _texto_de_reply(update)

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_varias_notas_duenas_se_listan_todas(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        """El dedup por hash de `save_resource` hace que dos notas compartan binario."""
        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        _nota_con_adjunto(vault, "01-Projects/ADSO/capitulo-5.md", "chapter-5.pdf")
        _nota_con_adjunto(
            vault, "01-Projects/Tesis/simulaciones.md", "chapter-5.pdf", en_frontmatter=False
        )

        update = await _subir(mock_context, make_update, "chapter-5.pdf", CONTENIDO)

        texto = _texto_de_reply(update)
        assert "01-Projects/ADSO/capitulo-5.md" in texto
        assert "01-Projects/Tesis/simulaciones.md" in texto

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_el_listado_de_notas_duenas_esta_acotado(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        """El mensaje de Telegram tiene un tope de 4096 chars: se recorta."""
        from adso.handlers.input import _MAX_NOTAS_DUPLICADAS

        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        for i in range(_MAX_NOTAS_DUPLICADAS + 2):
            _nota_con_adjunto(vault, f"01-Projects/Tesis/nota-{i}.md", "chapter-5.pdf")

        update = await _subir(mock_context, make_update, "chapter-5.pdf", CONTENIDO)

        texto = _texto_de_reply(update)
        assert texto.count("01-Projects/Tesis/nota-") == _MAX_NOTAS_DUPLICADAS
        assert "(y 2 más)" in texto

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_el_aviso_bloquea_input_y_registra_el_temporal(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        """Mientras el teclado está en pantalla, el flujo cuenta como pendiente.

        Y el temporal descargado tiene que seguir vivo: `[Crear igual]` lo
        necesita (regla de oro, sin pérdida de datos).
        """
        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        _nota_con_adjunto(vault, "01-Projects/Tesis/simulaciones.md", "chapter-5.pdf")

        await _subir(mock_context, make_update, "chapter-5.pdf", CONTENIDO)

        assert _has_pending_keyboard(mock_context)
        temp = Path(mock_context.user_data["pending_duplicate_doc"]["temp_path"])
        assert temp.exists()

        # [Cancelar] y /reset lo tienen que barrer, temporal incluido.
        _cleanup_pending(mock_context)
        assert not temp.exists(), "el temporal del duplicado quedó huérfano en /tmp (tmpfs)"

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_el_cron_de_reclasificacion_ve_el_flujo_en_curso(self) -> None:
        from adso.handlers.jobs import _PENDING_FLOW_KEYS

        assert "pending_duplicate_doc" in _PENDING_FLOW_KEYS


# ---------------------------------------------------------------------------
# Contra-casos — el dedup no puede agregar fricción donde no hay duplicado
# ---------------------------------------------------------------------------


class TestNoEsDuplicado:

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_mismo_nombre_distinto_contenido_no_es_duplicado(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        _nota_con_adjunto(vault, "01-Projects/Tesis/simulaciones.md", "chapter-5.pdf")

        await _subir(mock_context, make_update, "chapter-5.pdf", OTRO_CONTENIDO)

        assert "pending_duplicate_doc" not in mock_context.user_data
        assert "pending_read_status" in mock_context.user_data, (
            "un archivo distinto con el mismo nombre debe seguir el flujo normal"
        )

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_pdf_nuevo_sigue_el_flujo_normal(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        await _subir(mock_context, make_update, "paper.pdf", CONTENIDO)

        assert "pending_duplicate_doc" not in mock_context.user_data
        assert "pending_read_status" in mock_context.user_data

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_recurso_huerfano_sin_nota_no_bloquea(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        """El archivo está en 03-Resources/ pero ninguna nota lo referencia.

        No hay nota duplicada que mostrar: bloquear sería un dead-end.
        """
        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)

        await _subir(mock_context, make_update, "chapter-5.pdf", CONTENIDO)

        assert "pending_duplicate_doc" not in mock_context.user_data
        assert "pending_read_status" in mock_context.user_data

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_la_nota_archivada_no_cuenta_como_duplicado(
        self, mock_context, make_update, temporales: Path
    ) -> None:
        """Mismo criterio que el duplicado de arXiv: 05-Archive queda afuera."""
        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        _nota_con_adjunto(vault, "05-Archive/vieja.md", "chapter-5.pdf")

        await _subir(mock_context, make_update, "chapter-5.pdf", CONTENIDO)

        assert "pending_duplicate_doc" not in mock_context.user_data
        assert "pending_read_status" in mock_context.user_data


# ---------------------------------------------------------------------------
# [Crear igual] — retoma el flujo normal sin restricciones
# ---------------------------------------------------------------------------


class TestCrearIgual:

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_crear_igual_retoma_el_flujo_del_pdf(
        self, mock_context, make_update, make_callback_query, temporales: Path
    ) -> None:
        from adso.constants import CB_DOC_CREATE_ANYWAY
        from adso.handlers.callbacks import handle_callback

        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        _nota_con_adjunto(vault, "01-Projects/Tesis/simulaciones.md", "chapter-5.pdf")

        await _subir(mock_context, make_update, "chapter-5.pdf", CONTENIDO)
        temp = Path(mock_context.user_data["pending_duplicate_doc"]["temp_path"])

        update = make_callback_query(CB_DOC_CREATE_ANYWAY)
        await handle_callback(update, mock_context)

        assert "pending_duplicate_doc" not in mock_context.user_data
        assert "pending_read_status" in mock_context.user_data, (
            "[Crear igual] tiene que retomar el flujo normal del PDF"
        )
        assert mock_context.user_data["pending_read_status"]["temp_path"] == str(temp)
        assert temp.exists()

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_crear_igual_conserva_el_caption_como_contexto(
        self, mock_context, make_update, make_callback_query, temporales: Path
    ) -> None:
        """El caption es contexto del usuario para el LLM: no se puede perder."""
        from adso.constants import CB_DOC_CREATE_ANYWAY
        from adso.handlers import input as input_mod
        from adso.handlers.callbacks import handle_callback

        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        _nota_con_adjunto(vault, "01-Projects/Tesis/simulaciones.md", "chapter-5.pdf")

        update = make_update()
        update.message.document = _documento("chapter-5.pdf", CONTENIDO)
        update.message.caption = "capítulo de la tesis"
        update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=99))
        await input_mod.handle_document(update, mock_context)

        cb = make_callback_query(CB_DOC_CREATE_ANYWAY)
        await handle_callback(cb, mock_context)

        assert (
            mock_context.user_data["pending_read_status"]["user_context"]
            == "capítulo de la tesis"
        )

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_crear_igual_con_reply_roto_no_filtra_el_temporal(
        self, mock_context, make_update, make_callback_query, temporales: Path
    ) -> None:
        """Si el reply del flujo retomado falla, no queda estado ni temporal.

        Mismo contrato que el `finally` de `handle_document` (M2): en la RPi4
        /tmp es tmpfs, un temporal huérfano es RAM filtrada hasta el reinicio.
        """
        from adso.constants import CB_DOC_CREATE_ANYWAY
        from adso.handlers.callbacks import handle_callback

        vault = mock_context.bot_data["settings"].vault_path
        _recurso(vault, "chapter-5.pdf", CONTENIDO)
        _nota_con_adjunto(vault, "01-Projects/Tesis/simulaciones.md", "chapter-5.pdf")

        await _subir(mock_context, make_update, "chapter-5.pdf", CONTENIDO)
        temp = Path(mock_context.user_data["pending_duplicate_doc"]["temp_path"])

        update = make_callback_query(CB_DOC_CREATE_ANYWAY)
        # 1) el teclado del PDF (falla) 2) el aviso de error (la red vuelve)
        update.callback_query.message.reply_text = AsyncMock(
            side_effect=[RuntimeError("red caída"), MagicMock(message_id=3)]
        )

        await handle_callback(update, mock_context)

        assert not _has_pending_keyboard(mock_context)
        assert not temp.exists(), "el temporal quedó huérfano en /tmp (tmpfs)"

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_crear_igual_sin_estado_no_rompe(
        self, mock_context, make_callback_query
    ) -> None:
        from adso.constants import CB_DOC_CREATE_ANYWAY
        from adso.handlers.callbacks import handle_callback

        update = make_callback_query(CB_DOC_CREATE_ANYWAY)
        await handle_callback(update, mock_context)

        update.callback_query.answer.assert_awaited()
        assert "pending_read_status" not in mock_context.user_data


# ---------------------------------------------------------------------------
# El lookup por hash en 03-Resources/
# ---------------------------------------------------------------------------


class TestFindResourceByHash:

    async def test_encuentra_por_contenido_ignorando_el_nombre(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        from adso.vault_writer import find_resource_by_hash

        _recurso(vault_path, "chapter-5.pdf", CONTENIDO)
        subido = tmp_path / "otro-nombre.pdf"
        subido.write_bytes(CONTENIDO)

        assert await find_resource_by_hash(subido, vault_path) == (
            vault_path / "03-Resources" / "chapter-5.pdf"
        )

    async def test_mismo_nombre_distinto_contenido_no_matchea(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        from adso.vault_writer import find_resource_by_hash

        _recurso(vault_path, "chapter-5.pdf", CONTENIDO)
        subido = tmp_path / "chapter-5.pdf"
        subido.write_bytes(OTRO_CONTENIDO)

        assert await find_resource_by_hash(subido, vault_path) is None

    async def test_un_recurso_ilegible_no_tumba_el_scan(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        """Un archivo borrado o sin permisos en medio del scan se saltea.

        La captura no puede caerse por un archivo suelto de 03-Resources/.
        """
        from adso import vault_writer

        _recurso(vault_path, "a-roto.pdf", CONTENIDO)
        _recurso(vault_path, "b-bueno.pdf", CONTENIDO)
        subido = tmp_path / "subido.pdf"
        subido.write_bytes(CONTENIDO)

        real = vault_writer._file_hash_sync

        def _hash(path: Path) -> str:
            if path.name == "a-roto.pdf":
                raise OSError("permiso denegado")
            return real(path)

        with patch.object(vault_writer, "_file_hash_sync", side_effect=_hash):
            encontrado = await vault_writer.find_resource_by_hash(subido, vault_path)

        assert encontrado == vault_path / "03-Resources" / "b-bueno.pdf"

    async def test_origen_inexistente_devuelve_none(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        """Defensa: el temporal pudo desaparecer entre la descarga y el chequeo."""
        from adso.vault_writer import find_resource_by_hash

        _recurso(vault_path, "chapter-5.pdf", CONTENIDO)

        assert await find_resource_by_hash(tmp_path / "no-existe.pdf", vault_path) is None

    async def test_sin_carpeta_de_recursos_devuelve_none(
        self, tmp_path: Path
    ) -> None:
        """El vault puede no tener 03-Resources/ todavía."""
        from adso.vault_writer import find_resource_by_hash

        vault = tmp_path / "vault"
        vault.mkdir()
        subido = tmp_path / "chapter-5.pdf"
        subido.write_bytes(CONTENIDO)

        assert await find_resource_by_hash(subido, vault) is None
