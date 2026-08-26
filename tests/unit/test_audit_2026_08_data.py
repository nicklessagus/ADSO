"""Reproductores de los bugs de la auditoría 2026-08 — datos escritos al vault.

Cada test **especifica el comportamiento correcto** y se escribió reproduciendo
el bug (fallaba) antes de aplicar el fix. Los cinco están arreglados, así que
todos pasan y quedan como guards de regresión.

A diferencia del resto de la auditoría —que salió de leer código— estos cinco
bugs salieron de **auditar el vault real de producción**: son defectos con
evidencia en disco, no hipótesis. Por eso las evidencias quedan anotadas: son lo
que hace verificable que el bug era real y no una lectura pesimista del código.

Issues:
  #59 (B1) — un `note_id` que empieza con `.` (temporal de escritura atómica)
       podía convertirse en wikilink. Evidencia: `- [[.adso-tmp-pejoj6nh]]`
       fosilizado en una nota de `00-Inbox/`.
  #52 (B4) — el embed del recurso adjunto quedaba estructuralmente dentro de la
       sección "Ver también". Evidencia: 6 de las 8 notas con `source_file`.
  #54 (B6) — `create_note` no validaba `type`/`status`, y en `set_property` un
       `type` inválido desactivaba en silencio la validación de `status`.
       Evidencia: 3 notas con `type` fuera de `VALID_TYPES` (`note` ×2, `draft`).
  #55 (B7) — `get_note_index` colapsaba todos los `_index.md` en una sola
       entrada. Evidencia: los 7 `_index.md` del vault real comparten stem.
  #56 (B8) — `_validate_manage_payload` chequeaba la presencia de la clave
       `description`, no su contenido. Evidencia: 3 de 7 `_index.md` tienen
       `description: ''`, y ese campo es el scope de cada destino en el prompt.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from adso.embeddings import SimilarNote
from adso.llm_schema import LLMResponseError, _validate_manage_payload
from adso.vault_search import get_note_index
from adso.vault_writer import VALID_TYPES, create_note, read_note, set_property


# ---------------------------------------------------------------------------
# B1 — un note_id oculto puede terminar como wikilink en el vault
# ---------------------------------------------------------------------------
#
# La escritura atómica de `vault_writer` deja temporales `.adso-tmp-*` en el
# mismo directorio de la nota. El `VaultWatcher` los ignora (`_is_hidden`), así
# que hoy el índice de ChromaDB de producción está limpio — pero eso es una
# tapa aguas arriba: `_suggest_links` toma lo que venga de `query_similar` y lo
# convierte en `- [[stem]]` sin mirar nada. La evidencia de que la tapa no
# siempre estuvo ahí es la nota `00-Inbox/2026-07-05-vamos-juntes-…md`, que
# tiene fosilizado `- [[.adso-tmp-pejoj6nh]]` al lado del link correcto de la
# misma nota. Un dotfile nunca es una nota del vault: filtrarlo en el punto
# donde se arma el link cierra el paso a cualquier fuga futura del índice.


def _similar(note_id: str, title: str = "") -> SimilarNote:
    return SimilarNote(
        note_id=note_id,
        path=f"{note_id}.md",
        distance=0.1,
        metadata={"title": title},
        snippet=None,
    )


def _context_con_embeddings(mock_context, resultados: list[SimilarNote]):
    """Cablea un cliente de embeddings falso que devuelve `resultados`."""
    embeddings = MagicMock()
    embeddings.compute_embedding = AsyncMock(return_value=[0.1, 0.2, 0.3])
    embeddings.query_similar = AsyncMock(return_value=resultados)
    mock_context.bot_data["embeddings"] = embeddings
    return mock_context


class TestB1LinksOcultos:
    async def test_temporal_de_escritura_atomica_no_se_sugiere(self, mock_context) -> None:
        from adso.handlers.capture import _suggest_links

        ctx = _context_con_embeddings(
            mock_context,
            [
                _similar(".adso-tmp-pejoj6nh", "Vamos juntes"),
                _similar("2026-07-05-vamos-juntes", "Vamos juntes"),
            ],
        )

        links, _ = await _suggest_links(ctx, "texto de la nota")

        ids = [lnk["note_id"] for lnk in links]
        assert ".adso-tmp-pejoj6nh" not in ids, (
            "un temporal de la escritura atómica se convierte en wikilink y "
            "queda fosilizado en el vault (evidencia real en 00-Inbox/)"
        )
        assert "2026-07-05-vamos-juntes" in ids, "el link legítimo debe sobrevivir"

    async def test_dotfile_en_subcarpeta_tampoco_se_sugiere(self, mock_context) -> None:
        # El slug del wikilink es `note_id.rsplit("/", 1)[-1]`, así que un
        # dotfile anidado produce el mismo link roto que uno en la raíz.
        from adso.handlers.capture import _suggest_links

        ctx = _context_con_embeddings(
            mock_context, [_similar("01-Projects/tesis/.adso-tmp-abc123")]
        )

        links, _ = await _suggest_links(ctx, "texto de la nota")

        assert links == [], "un dotfile anidado no es una nota del vault"

    async def test_ids_normales_si_generan_links(self, mock_context) -> None:
        """Contra-caso: el camino normal no debe alterarse."""
        from adso.handlers.capture import _suggest_links

        ctx = _context_con_embeddings(
            mock_context,
            [
                _similar("2026-08-01-nota-a", "Nota A"),
                _similar("01-Projects/tesis/2026-08-02-nota-b", "Nota B"),
            ],
        )

        links, vector = await _suggest_links(ctx, "texto de la nota")

        assert [lnk["note_id"] for lnk in links] == [
            "2026-08-01-nota-a",
            "01-Projects/tesis/2026-08-02-nota-b",
        ]
        assert vector == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# B4 — el adjunto queda dentro de la sección "Ver también"
# ---------------------------------------------------------------------------
#
# En `_cb_confirm` el bloque de links se concatena primero
# (`body = body.rstrip() + "\n\n## Ver también\n\n" + ...`) y el embed del
# recurso después (`body += f"\n\n![[{res_path.name}]]"`). El resultado es un
# `![[archivo.pdf]]` colgando al final de la lista de links: en Obsidian el
# adjunto aparece como si fuera una nota relacionada más, y cualquier lectura
# estructural del bloque (la limpieza de wikilinks rotos ya opera acotada a
# `## Ver también`) lo ve como parte de la lista.
# Evidencia: 6 de las 8 notas con `source_file` del vault de producción.


class TestB4AdjuntoAntesDeVerTambien:
    def _pending(self, temp: Path) -> dict:
        return {
            "payload": {
                "frontmatter": {
                    "title": "Paper con adjunto",
                    "type": "reference",
                    "status": "active",
                },
                "body": "Cuerpo de la nota.",
                "suggested_links": [
                    {"note_id": "2026-08-01-nota-a", "title": "Nota A"},
                ],
            },
            "_resource_file": {"temp_path": str(temp), "filename": "paper.pdf"},
        }

    async def test_el_embed_del_adjunto_va_antes_del_bloque_de_links(
        self, mock_context, vault_path: Path, tmp_path: Path
    ) -> None:
        from adso.handlers import capture

        temp = tmp_path / "paper.pdf"
        temp.write_bytes(b"%PDF-1.4 contenido")
        mock_context.user_data["pending_note"] = self._pending(temp)

        query = MagicMock()
        query.edit_message_text = AsyncMock()

        await capture._cb_confirm(query, mock_context, vault_path)

        nota = next((vault_path / "00-Inbox").glob("*.md"))
        body = (await read_note(nota)).body

        assert "![[paper.pdf]]" in body
        assert "## Ver también" in body
        assert body.index("![[paper.pdf]]") < body.index("## Ver también"), (
            "el adjunto quedó estructuralmente dentro de la sección "
            "'Ver también': en Obsidian se lee como una nota relacionada más"
        )

    async def test_solo_adjunto_sin_links(
        self, mock_context, vault_path: Path, tmp_path: Path
    ) -> None:
        """Contra-caso: sin links sugeridos el embed queda bien al final."""
        from adso.handlers import capture

        temp = tmp_path / "paper.pdf"
        temp.write_bytes(b"%PDF-1.4 contenido")
        pending = self._pending(temp)
        pending["payload"]["suggested_links"] = []
        mock_context.user_data["pending_note"] = pending

        query = MagicMock()
        query.edit_message_text = AsyncMock()

        await capture._cb_confirm(query, mock_context, vault_path)

        nota = next((vault_path / "00-Inbox").glob("*.md"))
        body = (await read_note(nota)).body

        assert "## Ver también" not in body
        assert body.rstrip().endswith("![[paper.pdf]]")

    async def test_solo_links_sin_adjunto(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso: sin adjunto el bloque de links queda intacto."""
        from adso.handlers import capture

        pending = self._pending(Path("/dev/null"))
        pending.pop("_resource_file")
        mock_context.user_data["pending_note"] = pending

        query = MagicMock()
        query.edit_message_text = AsyncMock()

        await capture._cb_confirm(query, mock_context, vault_path)

        nota = next((vault_path / "00-Inbox").glob("*.md"))
        body = (await read_note(nota)).body

        assert body.rstrip().endswith("- [[2026-08-01-nota-a]] — Nota A")


# ---------------------------------------------------------------------------
# B6 — las constantes de validación existen pero casi nadie las usaba (ARREGLADO)
# ---------------------------------------------------------------------------
#
# `VALID_TYPES` / `VALID_STATUS` (vault_writer.py) son la definición canónica del
# schema, pero el único consumidor era `set_property`. `create_note` —el camino
# por el que entra el 100% de las notas— no las miraba: escribía el `type` que le
# pasaran. De ahí las 3 notas del vault real con `type` fuera del enum (`note`
# ×2, `draft`).
#
# Y había un segundo defecto encadenado: en `set_property`,
# `valid = VALID_STATUS.get(note_type, set())` seguido de
# `if valid and value not in valid` significaba que una nota con `type` inválido
# devolvía un set vacío → falsy → la validación de status se desactivaba en
# silencio. Las mismas notas que el primer defecto dejó entrar eran las que
# quedaban sin ninguna validación de estado para siempre.
#
# **Decisión de diseño (a): coaccionar, no rechazar.** `create_note` se llama
# desde `_cb_confirm`, o sea DESPUÉS de que el usuario apretó [Confirmar] sobre
# un preview. La regla de oro del repo es "sin pérdida de datos": levantar
# `ValueError` ahí sube al `except` de `handle_callback`, que muestra "Error al
# guardar" y —por el bug C1 de la misma auditoría— deja el mensaje sin teclado,
# o sea sin forma de reintentar. Un texto de audio/OCR/Vision no existe en
# ningún otro lado. La salida correcta es escribir la nota con un `type` válido
# y loguear un warning. Los tests de abajo asertan el invariante —lo que se
# escribe al vault está dentro del enum— sin atarse a qué valor concreto elija
# la coacción.
#
# En `set_property` (b) la decisión es la inversa y no hay tensión: ahí el
# caller es código del bot, no una captura del usuario, y la firma ya documenta
# `Raises: ValueError`. Tragarse el status inválido era justamente el bug.
#
# Ambos quedan como guards de regresión: sin marca xfail, tienen que pasar.


class TestB6aCreateNoteValidaElType:
    async def test_type_fuera_del_enum_no_llega_al_vault(self, vault_path: Path) -> None:
        path = await create_note(
            {"title": "Una nota", "type": "note", "status": "active"},
            "Contenido que el usuario ya confirmó.",
            vault_path,
        )

        nota = await read_note(path)
        assert nota.frontmatter["type"] in VALID_TYPES, (
            f"se escribió type={nota.frontmatter['type']!r} al vault: es el "
            "origen de las 3 notas con type inválido del vault real"
        )
        # Coaccionar, no rechazar: el contenido confirmado por el usuario no se
        # pierde en ningún caso.
        assert "Contenido que el usuario ya confirmó." in nota.body

    async def test_status_incoherente_con_el_type_no_llega_al_vault(
        self, vault_path: Path
    ) -> None:
        from adso.vault_writer import VALID_STATUS

        # `raw` es un estado de idea; una task nunca puede tenerlo (es
        # exactamente lo que produce el bug C7b del flujo de captura).
        path = await create_note(
            {"title": "Mandar el informe", "type": "task", "status": "raw"},
            "mandar el informe",
            vault_path,
        )

        nota = await read_note(path)
        assert nota.frontmatter["status"] in VALID_STATUS["task"], (
            f"status {nota.frontmatter['status']!r} es inválido para una task: "
            "los reportes y filtros por status dejan de ver esa tarea"
        )

    async def test_frontmatter_valido_se_escribe_sin_cambios(
        self, vault_path: Path
    ) -> None:
        """Contra-caso: una combinación válida no se toca."""
        path = await create_note(
            {"title": "Nota buena", "type": "reference", "status": "active"},
            "cuerpo",
            vault_path,
        )

        fm = (await read_note(path)).frontmatter
        assert fm["type"] == "reference"
        assert fm["status"] == "active"


class TestB6bSetPropertyConTypeInvalido:
    async def _nota_con_type(self, vault_path: Path, note_type: str) -> Path:
        """Escribe una nota a mano — así entraron al vault real las notas malas."""
        path = vault_path / "00-Inbox" / f"nota-{note_type}.md"
        path.write_text(
            f"---\ntitle: Nota\ntype: {note_type}\nstatus: active\n---\n\ncuerpo\n",
            encoding="utf-8",
        )
        return path

    async def test_status_arbitrario_se_rechaza_aunque_el_type_sea_invalido(
        self, vault_path: Path
    ) -> None:
        path = await self._nota_con_type(vault_path, "note")

        with pytest.raises(ValueError):
            await set_property(path, "status", "cualquier-cosa")

    async def test_idem_con_type_draft(self, vault_path: Path) -> None:
        path = await self._nota_con_type(vault_path, "draft")

        with pytest.raises(ValueError):
            await set_property(path, "status", "lo-que-sea")

    async def test_type_valido_si_rechaza_el_status_invalido(
        self, vault_path: Path
    ) -> None:
        """Contra-caso: con un `type` del enum la validación sí corre."""
        path = await self._nota_con_type(vault_path, "task")

        with pytest.raises(ValueError):
            await set_property(path, "status", "cualquier-cosa")

    async def test_status_valido_se_aplica(self, vault_path: Path) -> None:
        """Contra-caso: el camino feliz no se altera."""
        path = await self._nota_con_type(vault_path, "task")

        await set_property(path, "status", "done")

        assert (await read_note(path)).frontmatter["status"] == "done"


# ---------------------------------------------------------------------------
# B7 — el índice de notas colapsaba los stems repetidos (ARREGLADO)
# ---------------------------------------------------------------------------
#
# `get_note_index` hacía `index[md_path.stem] = md_path`: el último path que
# devolviera `rglob` pisaba a todos los anteriores, sin log ni señal alguna para
# el caller. No era un caso patológico: el propio bot crea un `_index.md` por
# cada proyecto y cada área (`create_note` fuerza ese nombre para
# `project-index`/`area-index`), así que el vault real tiene 7 archivos con ese
# stem y el índice devolvía UNO, elegido por el orden de `rglob`.
#
# El bug era latente —`get_note_index` no tiene callers en `adso/`— pero la firma
# promete "índice de todos los .md del vault" y no lo era. Cualquier consumidor
# futuro (resolver un wikilink a su path, por ejemplo) heredaba el problema en
# silencio. El fix expone las entradas ambiguas además bajo su ruta relativa sin
# extensión (el mismo `note_id` que usa el índice de embeddings) y le deja al
# primer stem su clave, para no romper a quien resuelva por stem.


class TestB7IndiceDeNotasConStemsRepetidos:
    def _vault_con_indices(self, vault_path: Path) -> list[Path]:
        creados = []
        for proyecto in ("tesis", "divulgacion", "docencia"):
            path = vault_path / "01-Projects" / proyecto / "_index.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"---\ntitle: {proyecto}\ntype: project-index\n"
                f"project: {proyecto}\ndescription: d\n---\n\n",
                encoding="utf-8",
            )
            creados.append(path)
        return creados

    async def test_todos_los_index_md_son_alcanzables(self, vault_path: Path) -> None:
        creados = self._vault_con_indices(vault_path)

        index = await get_note_index(vault_path)

        alcanzables = set(index.values())
        faltan = [p for p in creados if p not in alcanzables]
        assert not faltan, (
            f"{len(faltan)} de {len(creados)} `_index.md` desaparecieron del "
            "índice: el último que devuelve rglob pisa a los demás"
        )

    async def test_stems_unicos_resuelven_igual_que_antes(
        self, vault_path: Path
    ) -> None:
        """Contra-caso: sin colisiones el índice es exacto."""
        creados = []
        for i, proyecto in enumerate(("tesis", "divulgacion")):
            path = vault_path / "01-Projects" / proyecto / f"nota-{i}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\ntitle: N\ntype: reference\n---\n\ncuerpo\n", encoding="utf-8")
            creados.append(path)

        index = await get_note_index(vault_path)

        assert index["nota-0"] == creados[0]
        assert index["nota-1"] == creados[1]


# ---------------------------------------------------------------------------
# B8 — `description` vacía pasa la validación del modo manage
# ---------------------------------------------------------------------------
#
# `_validate_manage_payload` (llm_schema.py:459-460) chequea
# `if "description" not in params`: presencia de la CLAVE, no contenido. El
# schema de Gemini declara `description` como `nullable: True`, así que el
# modelo puede emitir `""` sin violar nada y la validación lo deja pasar.
#
# No es cosmético: `description` es lo que `_get_existing_items` le pasa a
# `build_system_prompt` como scope de cada proyecto/área, o sea lo que el LLM usa
# para decidir a dónde va cada captura. Un proyecto sin descripción es un
# destino que el clasificador no sabe distinguir de los demás. Evidencia: 3 de
# los 7 `_index.md` del vault real tienen `description: ''` — y el bot **pide la
# descripción como obligatoria** en la creación interactiva, así que la puerta
# por la que entraron es esta.


class TestB8DescriptionVacia:
    @pytest.mark.parametrize("vacia", ["", "   ", "\n\t "])
    @pytest.mark.parametrize("operation", ["create_project", "create_area"])
    def test_description_vacia_se_rechaza(self, operation: str, vacia: str) -> None:
        with pytest.raises(LLMResponseError):
            _validate_manage_payload(
                {"operation": operation, "params": {"name": "tesis", "description": vacia}}
            )

    def test_description_null_se_rechaza(self) -> None:
        with pytest.raises(LLMResponseError):
            _validate_manage_payload(
                {"operation": "create_project", "params": {"name": "tesis", "description": None}}
            )

    def test_description_real_pasa(self) -> None:
        """Contra-caso: una descripción con contenido no debe rechazarse."""
        _validate_manage_payload(
            {
                "operation": "create_project",
                "params": {"name": "tesis", "description": "Doctorado en física"},
            }
        )

    def test_description_ausente_sigue_rechazandose(self) -> None:
        """Contra-caso: el guard que sí funciona hoy."""
        with pytest.raises(LLMResponseError):
            _validate_manage_payload({"operation": "create_project", "params": {"name": "tesis"}})
