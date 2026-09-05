"""Reproductores de los bugs de la auditoría 2026-08-22 — embeddings y retrieval.

Mismo contrato que `test_audit_2026_08_vault.py` y `test_audit_2026_08_capture.py`:
cada test **especifica el comportamiento correcto** y está marcado
`xfail(strict=True)` mientras el bug siga abierto. La suite queda verde hoy, y el
día que alguien arregle el bug el test pasa a XPASS y `strict` lo convierte en
fallo, obligando a sacar la marca en el mismo commit del fix.

Los ocho ya están arreglados: las marcas se sacaron en el commit de cada fix y
los tests quedan como regresión.

Issues:
  E1 — metadata no-string de ChromaDB revienta el render inline de `/buscar`.
  E2 — el reindex externo del watcher no filtra `exclude_dirs` ni `_index.md`.
  E3 — una nota vaciada externamente nunca se considera huérfana.
  E4 — el sweep de huérfanos borra notas creadas *durante* el reindex.
  E5 — nada precalienta ChromaDB: el primer mensaje congela el event loop.
  E6 — `pending_query` es global: el botón de informe de una consulta vieja
       genera el informe de la última.
  E7 — `cb_query_report` usa `.chat_id`, que `InaccessibleMessage` no tiene.
  E8 — el mensaje de error escapa HTML pero no manda `parse_mode`.

El hilo común: el camino semántico (`embeddings` + `/buscar`) quedó fuera de las
pasadas de hardening que sí endurecieron `vault_search`, `reporters` y el flujo
de captura — arrastra las mismas clases de bug ya cerradas en el resto del bot.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import InaccessibleMessage

from adso.embeddings import EmbeddingsClient, SimilarNote
from adso.knowledge_query import QueryResult, ScoredNote, _to_scored
from tests.helpers import write_note

FAKE_EMBEDDING = [0.1] * 768


# ---------------------------------------------------------------------------
# Helpers compartidos
# ---------------------------------------------------------------------------


def _hit(note_id: str, distance: float, metadata: dict) -> SimilarNote:
    """SimilarNote como lo devuelve `EmbeddingsClient.query_similar`."""
    return SimilarNote(
        note_id=note_id,
        path=f"01-Projects/tesis/{note_id}.md",
        distance=distance,
        metadata=metadata,
        snippet="un fragmento de la nota",
    )


def _status_msg() -> MagicMock:
    """Mensaje de status con métodos async (lo que devuelve reply_text)."""
    msg = MagicMock()
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    return msg


def _prep_query(mock_context, make_update, texto: str, hits: list[SimilarNote]):
    """Arma update/context para `run_query` con el cliente de embeddings mockeado.

    Se mockea `query_similar` (no `retrieve`) a propósito: así el test recorre el
    `_to_scored` real, que es donde vive el bug E1.
    """
    update = make_update(text=texto)
    update.effective_message = update.message
    update.effective_chat = MagicMock()
    update.effective_chat.id = 42
    status = _status_msg()
    update.message.reply_text = AsyncMock(return_value=status)

    emb = MagicMock()
    emb.compute_embedding = AsyncMock(return_value=[0.1] * 8)
    emb.query_similar = AsyncMock(return_value=hits)
    mock_context.bot_data["embeddings"] = emb
    mock_context.bot = MagicMock()
    mock_context.bot.send_document = AsyncMock()
    return update, status


async def _post_init_con_watcher_mockeado(app) -> MagicMock:
    """Corre `_post_init` sin arrancar el watcher real y devuelve su constructor.

    `_post_init` es el único lugar donde se definen los callbacks del watcher
    (`_reindex_external_note` / `_remove_external_note`): no hay forma de
    obtenerlos sin ejecutarlo. El observer de watchdog se sustituye por un mock
    para no tocar inotify en el test.
    """
    from adso import bot as bot_mod

    with patch.object(bot_mod, "VaultWatcher") as ctor:
        ctor.return_value.start = AsyncMock()
        await bot_mod._post_init(app)
    return ctor


def _callback_de_reindex(ctor: MagicMock):
    """Extrae `on_external_change` de la construcción del VaultWatcher."""
    return ctor.call_args.kwargs["on_external_change"]


def _make_app(settings, embeddings) -> SimpleNamespace:
    """App de PTB mínima para `_post_init` (bot_data real: se muta adentro)."""
    return SimpleNamespace(
        bot_data={"settings": settings, "embeddings": embeddings},
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


# ---------------------------------------------------------------------------
# E1 — metadata no-string de ChromaDB revienta el render de `/buscar`
# ---------------------------------------------------------------------------
#
# `_serialize_metadata` (embeddings.py) deja pasar int/float/bool tal cual, así
# que una nota editada a mano con `title: 2024` (YAML lo parsea como int) se
# indexa con un int en la metadata. Después `_to_scored` hace
# `meta.get("title") or Path(rel).stem`: un int truthy sobrevive al `or`, viaja
# hasta `_format_inline` y ahí `_esc(n.title)` llama a `int.replace` →
# AttributeError, que mata la consulta entera con el error handler global.
#
# Es exactamente la clase C4 (valores no-string de frontmatter editado a mano)
# que ya se coaccionó en `vault_search` y `reporters`, pero que nunca se aplicó
# al camino semántico.


class TestE1MetadataNoString:
    def test_to_scored_coacciona_los_campos_a_str(self) -> None:
        scored = _to_scored(
            _hit("nota", 0.2, {"title": 2024, "status": 3, "project": 7}),
            Path("/vault"),
        )

        assert isinstance(scored.title, str), (
            f"title quedó como {type(scored.title).__name__}: `_esc(n.title)` "
            "en _format_inline lanza AttributeError"
        )
        assert isinstance(scored.status, str)
        assert isinstance(scored.project, str)

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_buscar_no_muere_con_un_titulo_numerico(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers.query import run_query

        hits = [_hit("a", 0.2, {"title": 2024, "status": "active", "project": ""})]
        update, _status = _prep_query(mock_context, make_update, "/buscar 2024", hits)

        await run_query(update, mock_context, "2024")

    async def test_titulo_string_sigue_renderizando(
        self, mock_context, make_update
    ) -> None:
        """Contra-caso: el camino normal (metadata string) no debe alterarse."""
        from adso.handlers.query import run_query

        hits = [_hit("a", 0.2, {"title": "Nota Alfa", "status": "active", "project": ""})]
        update, status = _prep_query(mock_context, make_update, "/buscar alfa", hits)

        await run_query(update, mock_context, "alfa")

        assert "Nota Alfa" in str(status.edit_text.await_args)


# ---------------------------------------------------------------------------
# E2 — el reindex externo del watcher no filtra nada
# ---------------------------------------------------------------------------
#
# `reindex_vault` respeta `vault.exclude_dirs` y saltea los `_index.md`
# (embeddings.py:401 y :412). `_reindex_external_note` (bot.py), que corre ante
# cada cambio detectado por inotify, no aplica NINGÚN filtro: editar desde
# Obsidian una nota archivada la mete al índice contra el diseño, `/buscar` la
# devuelve, y esa misma noche el reindex la borra como huérfana (su ID nunca
# entra en `vault_note_ids`). El resultado es un ciclo diario de embed + delete
# que gasta quota de la Embedding API y ensucia los resultados hasta las 3 AM.


class TestE2ReindexExternoFiltraPaths:
    async def test_nota_archivada_no_se_indexa(self, vault_path: Path) -> None:
        from adso import bot as bot_mod

        settings = SimpleNamespace(
            vault_path=vault_path,
            vault_seed=SimpleNamespace(projects=[], areas=[]),
            vault=SimpleNamespace(exclude_dirs=["05-Archive", ".obsidian", ".trash"]),
            watcher=SimpleNamespace(debug=False),
            telegram_allowed_user_id=42,
        )
        app = _make_app(settings, MagicMock())
        nota = write_note(vault_path / "05-Archive" / "vieja.md", "Contenido viejo.")

        with patch.object(bot_mod, "_index_note_safe", AsyncMock()) as indexar:
            ctor = await _post_init_con_watcher_mockeado(app)
            await _callback_de_reindex(ctor)(nota)

        indexar.assert_not_awaited()

    async def test_index_md_no_se_indexa(self, vault_path: Path) -> None:
        from adso import bot as bot_mod

        settings = SimpleNamespace(
            vault_path=vault_path,
            vault_seed=SimpleNamespace(projects=[], areas=[]),
            vault=SimpleNamespace(exclude_dirs=["05-Archive", ".obsidian", ".trash"]),
            watcher=SimpleNamespace(debug=False),
            telegram_allowed_user_id=42,
        )
        app = _make_app(settings, MagicMock())
        indice = write_note(
            vault_path / "01-Projects" / "tesis" / "_index.md",
            "Índice del proyecto.",
            type="project-index",
            project="tesis",
            description="Doctorado",
        )

        with patch.object(bot_mod, "_index_note_safe", AsyncMock()) as indexar:
            ctor = await _post_init_con_watcher_mockeado(app)
            await _callback_de_reindex(ctor)(indice)

        indexar.assert_not_awaited()

    async def test_nota_normal_si_se_indexa(self, vault_path: Path) -> None:
        """Contra-caso: el filtro nuevo no debe romper el camino legítimo."""
        from adso import bot as bot_mod

        settings = SimpleNamespace(
            vault_path=vault_path,
            vault_seed=SimpleNamespace(projects=[], areas=[]),
            vault=SimpleNamespace(exclude_dirs=["05-Archive", ".obsidian", ".trash"]),
            watcher=SimpleNamespace(debug=False),
            telegram_allowed_user_id=42,
        )
        app = _make_app(settings, MagicMock())
        nota = write_note(
            vault_path / "01-Projects" / "tesis" / "metodo.md", "Contenido vivo."
        )

        with patch.object(bot_mod, "_index_note_safe", AsyncMock()) as indexar:
            ctor = await _post_init_con_watcher_mockeado(app)
            await _callback_de_reindex(ctor)(nota)

        indexar.assert_awaited_once()


# ---------------------------------------------------------------------------
# E3 — una nota vaciada externamente conserva su embedding para siempre
# ---------------------------------------------------------------------------
#
# En `reindex_vault` el orden es `vault_note_ids.add(note_id)` (línea 415) y
# recién después el guard `if not body.strip(): continue`. Como el ID ya entró
# al set de "notas vivas", el sweep de huérfanos del final nunca lo ve, así que
# el embedding del contenido ANTERIOR queda indexado indefinidamente: `/buscar`
# sigue devolviendo la nota por un texto que ya no está en el archivo. El camino
# del watcher tiene el mismo hueco (`if not body: return`, sin `remove_note`).


class TestE3NotaVaciadaPierdeSuEmbedding:
    async def test_vaciar_el_body_borra_el_embedding(self, tmp_path: Path) -> None:
        from adso import vault_cache

        vault_cache.clear()
        cliente = EmbeddingsClient(chroma_data_dir=tmp_path / "chroma", gemini_api_key="fake")

        async def _fake_embed(_content: str) -> list[float]:
            return FAKE_EMBEDDING

        cliente._compute_embedding = _fake_embed

        vault = tmp_path / "vault"
        nota = write_note(
            vault / "01-Projects" / "tesis" / "metodo.md", "Contenido sobre ML."
        )
        await cliente.reindex_vault(vault)
        assert cliente.count() == 1, "precondición: la nota quedó indexada"

        # El usuario vacía la nota desde Obsidian y deja solo el frontmatter.
        write_note(nota, "")
        vault_cache.clear()

        await cliente.reindex_vault(vault)

        ids = cliente._collection.get(include=[])["ids"]
        assert "01-Projects/tesis/metodo" not in ids, (
            "el embedding del texto viejo sigue indexado: /buscar devuelve la "
            "nota por contenido que ya no existe en el archivo"
        )

    async def test_nota_borrada_si_se_limpia(self, tmp_path: Path) -> None:
        """Contra-caso: el sweep de huérfanos sí funciona cuando el .md se borra."""
        from adso import vault_cache

        vault_cache.clear()
        cliente = EmbeddingsClient(chroma_data_dir=tmp_path / "chroma", gemini_api_key="fake")

        async def _fake_embed(_content: str) -> list[float]:
            return FAKE_EMBEDDING

        cliente._compute_embedding = _fake_embed

        vault = tmp_path / "vault"
        nota = write_note(
            vault / "01-Projects" / "tesis" / "metodo.md", "Contenido sobre ML."
        )
        await cliente.reindex_vault(vault)

        nota.unlink()
        vault_cache.clear()
        stats = await cliente.reindex_vault(vault)

        assert stats["removed"] == 1
        assert cliente.count() == 0


# ---------------------------------------------------------------------------
# E4 — el sweep de huérfanos borra notas creadas *durante* el reindex
# ---------------------------------------------------------------------------
#
# `reindex_vault` toma el snapshot de `rglob` al inicio (embeddings.py:395) y al
# final compara `chroma_ids - vault_note_ids` sin re-verificar existencia en
# disco (:459-474). Entre ambos momentos pasan minutos: cada nota indexada tiene
# un `await asyncio.sleep(0.2)` de rate limiting más la latencia de la Embedding
# API. Si el usuario confirma una captura en esa ventana, `_cb_confirm` escribe
# el .md e indexa via `spawn_tracked` — el ID entra a ChromaDB pero no al
# snapshot, así que el sweep lo borra como huérfano. La nota existe en el vault
# y es invisible para `/buscar` hasta el reindex de la noche siguiente.
#
# El test simula esa carrera desde el hook de embedding: mientras se indexa la
# nota A, aparece la nota B (archivo en disco + upsert en la colección), que es
# exactamente lo que hace una captura confirmada.


class TestE4CarreraConCapturaConfirmada:
    async def test_nota_creada_durante_el_reindex_no_se_borra(
        self, tmp_path: Path
    ) -> None:
        from adso import vault_cache

        vault_cache.clear()
        cliente = EmbeddingsClient(chroma_data_dir=tmp_path / "chroma", gemini_api_key="fake")

        vault = tmp_path / "vault"
        write_note(vault / "01-Projects" / "tesis" / "vieja.md", "Contenido previo.")

        nueva_rel = "01-Projects/tesis/recien-confirmada"
        llamadas: list[str] = []

        async def _fake_embed(content: str) -> list[float]:
            llamadas.append(content)
            if len(llamadas) == 1:
                # El usuario aprieta [Confirmar] a mitad del reindex nocturno:
                # se escribe el .md y `spawn_tracked` indexa el embedding.
                write_note(vault / f"{nueva_rel}.md", "Captura recién confirmada.")
                cliente._collection.upsert(
                    ids=[nueva_rel],
                    documents=["Captura recién confirmada."],
                    embeddings=[FAKE_EMBEDDING],
                    metadatas=[{"path": f"{nueva_rel}.md"}],
                )
            return FAKE_EMBEDDING

        cliente._compute_embedding = _fake_embed

        await cliente.reindex_vault(vault)

        assert (vault / f"{nueva_rel}.md").exists(), "precondición: la nota está en disco"
        ids = cliente._collection.get(include=[])["ids"]
        assert nueva_rel in ids, (
            "el sweep borró como huérfana una nota que existe en el vault: "
            "queda invisible para /buscar hasta el reindex de la noche siguiente"
        )


# ---------------------------------------------------------------------------
# E5 — nada precalienta ChromaDB
# ---------------------------------------------------------------------------
#
# `_ensure_initialized` es SÍNCRONA y se llama directo desde corutinas
# (`query_similar`, `index_note`, `remove_note`, `reindex_vault`). Adentro hace
# `import chromadb` — medido en esta RPi4: 4,4 s — más `PersistentClient` y
# `get_or_create_collection`. Como nada la dispara en el arranque, el primer
# mensaje del usuario paga ese costo con el event loop bloqueado: durante esos
# segundos el bot no responde a nada, y apscheduler emite "Run time of job was
# missed" (la señal que `logging_setup.py` deja pasar a propósito).
#
# El comportamiento correcto es precalentar en `_post_init`, que es asíncrono y
# corre antes del polling: ahí el costo no le pega a ninguna interacción.


class TestE5WarmUpDeChromaDB:
    async def test_el_arranque_deja_chromadb_inicializado(self, vault_path: Path) -> None:
        settings = SimpleNamespace(
            vault_path=vault_path,
            vault_seed=SimpleNamespace(projects=[], areas=[]),
            vault=SimpleNamespace(exclude_dirs=["05-Archive"]),
            watcher=SimpleNamespace(debug=False),
            telegram_allowed_user_id=42,
        )
        cliente = EmbeddingsClient(
            chroma_data_dir=vault_path / ".chroma", gemini_api_key="fake"
        )
        app = _make_app(settings, cliente)

        await _post_init_con_watcher_mockeado(app)

        assert cliente._initialized, (
            "el import de chromadb (4,4 s) queda para el primer mensaje del "
            "usuario y bloquea el event loop"
        )

    async def test_la_inicializacion_lazy_sigue_existiendo(self, tmp_path: Path) -> None:
        """Contra-caso: el guard lazy no se toca — un cliente frío se inicializa solo.

        El fix es agregar un warm-up en el arranque, no sacar el guard: los tests
        y los flujos que construyen su propio cliente siguen dependiendo de él.
        """
        cliente = EmbeddingsClient(chroma_data_dir=tmp_path / "chroma", gemini_api_key="fake")
        assert cliente._initialized is False

        cliente._compute_embedding = AsyncMock(return_value=FAKE_EMBEDDING)
        await cliente.query_similar("consulta")

        assert cliente._initialized is True


# ---------------------------------------------------------------------------
# E6 — el botón de informe de una consulta vieja genera el informe de la última
# ---------------------------------------------------------------------------
#
# `run_query` pisa `context.user_data["pending_query"]` en cada consulta
# (query.py:115) y `cb_query_report` lo lee sin mirar de qué mensaje viene el
# callback (:207). Los mensajes con resultados quedan en el historial con su
# botón [Generar informe .md] activo: tocar el de una consulta de hace media
# hora manda el informe de la última, con un nombre de archivo idéntico
# (`consulta.md`) y sin ninguna señal de que no es lo pedido.
#
# Misma clase que G14, que sí se cerró en `_cb_confirm` comparando `msg_id`.


class TestE6InformeDeLaConsultaCorrecta:
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_boton_viejo_no_genera_el_informe_de_la_ultima_consulta(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers.query import cb_query_report, run_query

        # Consulta 1 → su mensaje de resultados queda con el botón activo.
        update1, status1 = _prep_query(
            mock_context, make_update, "/buscar uno",
            [_hit("a", 0.2, {"title": "Nota A", "status": "active", "project": ""})],
        )
        status1.message_id = 111
        await run_query(update1, mock_context, "consulta uno")

        # Consulta 2 → pisa pending_query.
        update2, status2 = _prep_query(
            mock_context, make_update, "/buscar dos",
            [_hit("b", 0.3, {"title": "Nota B", "status": "active", "project": ""})],
        )
        status2.message_id = 222
        await run_query(update2, mock_context, "consulta dos")

        # El usuario scrollea y toca el botón del mensaje de la consulta 1.
        query = MagicMock()
        query.answer = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = 111
        query.message.chat_id = 42

        await cb_query_report(query, mock_context)

        enviado = mock_context.bot.send_document.await_args
        contenido = enviado.kwargs["document"].getvalue() if enviado else b""
        assert b"consulta dos" not in contenido, (
            "el botón de una consulta vieja mandó el informe de la última: "
            "mismo nombre de archivo, sin ningún aviso"
        )

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_el_boton_de_la_consulta_vigente_si_funciona(
        self, mock_context, make_update
    ) -> None:
        """Contra-caso: el camino normal (última consulta) debe seguir intacto."""
        from adso.handlers.query import cb_query_report, run_query

        update, status = _prep_query(
            mock_context, make_update, "/buscar uno",
            [_hit("a", 0.2, {"title": "Nota A", "status": "active", "project": ""})],
        )
        status.message_id = 111
        await run_query(update, mock_context, "consulta uno")

        query = MagicMock()
        query.answer = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = 111
        query.message.chat_id = 42

        await cb_query_report(query, mock_context)

        enviado = mock_context.bot.send_document.await_args
        assert enviado is not None
        assert b"consulta uno" in enviado.kwargs["document"].getvalue()


# ---------------------------------------------------------------------------
# E7 — `InaccessibleMessage` no tiene `chat_id`
# ---------------------------------------------------------------------------
#
# Telegram deja de exponer el contenido de un mensaje con más de 48 h: el
# callback llega con `message` de tipo `InaccessibleMessage`, que en PTB 21.11.1
# expone `chat` y `message_id` pero NO `chat_id` (verificado:
# `hasattr(InaccessibleMessage, "chat_id")` es False). `cb_query_report` hace
# `query.message.chat_id` (query.py:216) → AttributeError → error handler global.
# Los botones [Generar informe .md] viven en el historial indefinidamente, así
# que este es el caso esperable, no el raro. `.chat.id` sí existe siempre.


class TestE7MensajeInaccesible:
    async def test_informe_sobre_un_mensaje_de_mas_de_48h(self, mock_context) -> None:
        from adso.handlers.query import cb_query_report

        mock_context.bot = MagicMock()
        mock_context.bot.send_document = AsyncMock()
        mock_context.user_data["pending_query"] = QueryResult(
            query="exoplanetas",
            notes=[
                ScoredNote(
                    note_id="a",
                    path=mock_context.bot_data["settings"].vault_path / "00-Inbox" / "a.md",
                    title="Nota A",
                    snippet="fragmento",
                    similarity=0.9,
                )
            ],
        )

        query = MagicMock()
        query.answer = AsyncMock()
        # spec=InaccessibleMessage: solo los atributos que la clase realmente
        # tiene. Acceder a `.chat_id` levanta AttributeError, igual que en prod.
        query.message = MagicMock(spec=InaccessibleMessage)
        query.message.chat = MagicMock()
        query.message.chat.id = 99

        await cb_query_report(query, mock_context)

        enviado = mock_context.bot.send_document.await_args
        assert enviado is not None, "el informe no se mandó"
        assert enviado.kwargs["chat_id"] == 99


# ---------------------------------------------------------------------------
# E8 — el mensaje de error escapa HTML pero no lo declara
# ---------------------------------------------------------------------------
#
# `run_query` hace `edit_text(f"Error al buscar: {_esc(str(e))}")` (query.py:104)
# sin `parse_mode="HTML"`. Todos los demás `edit_text` de ese handler sí lo
# pasan. Sin el parse_mode, Telegram muestra el texto crudo y el usuario lee
# `&lt;boom&gt;` en vez de `<boom>` — justo en el mensaje que tiene que leer
# para entender qué falló.


class TestE8ParseModeEnElMensajeDeError:
    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_el_error_no_muestra_entidades_escapadas(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import query as query_mod

        update, status = _prep_query(mock_context, make_update, "/buscar x", [])

        with patch.object(
            query_mod, "retrieve", AsyncMock(side_effect=Exception("timeout en <host>"))
        ):
            await query_mod.run_query(update, mock_context, "x")

        llamada = status.edit_text.await_args
        texto = llamada.args[0] if llamada.args else llamada.kwargs.get("text", "")
        assert "Error al buscar" in texto, "precondición: se avisó el error"
        assert llamada.kwargs.get("parse_mode") == "HTML" or "&lt;" not in texto, (
            "el usuario lee '&lt;host&gt;' literal: se escapó el HTML pero no se "
            "declaró parse_mode"
        )


# ---------------------------------------------------------------------------
# E3b — vaciar una nota desde Obsidian deja su embedding viejo hasta el reindex
# ---------------------------------------------------------------------------
#
# Hermano de E3, del lado del watcher: `_reindex_external_note` hace
# `if not body: return` sin llamar a `remove_note`, así que el documento viejo
# sigue en ChromaDB. El reindex nocturno ya lo limpia (E3), pero hasta las 3am
# `/buscar` devuelve la nota con un snippet del contenido que el usuario borró.
#
# Ojo con el caso hermano: `note is None` (YAML roto) NO debe borrar nada — es
# defensivo a propósito (docs/decisions-log.md), porque un error de parseo
# transitorio no significa que la nota dejó de existir.


class TestE3bNotaVaciadaEnElWatcher:
    def _settings(self, vault_path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            vault_path=vault_path,
            vault_seed=SimpleNamespace(projects=[], areas=[]),
            vault=SimpleNamespace(exclude_dirs=["05-Archive", ".obsidian", ".trash"]),
            watcher=SimpleNamespace(debug=False),
            telegram_allowed_user_id=42,
        )

    async def test_vaciar_el_body_borra_el_embedding(self, vault_path: Path) -> None:
        embeddings = MagicMock()
        embeddings.remove_note = AsyncMock()
        app = _make_app(self._settings(vault_path), embeddings)
        nota = write_note(vault_path / "00-Inbox" / "vaciada.md", "")

        ctor = await _post_init_con_watcher_mockeado(app)
        await _callback_de_reindex(ctor)(nota)

        embeddings.remove_note.assert_awaited_once()

    async def test_yaml_roto_no_borra_el_embedding(self, vault_path: Path) -> None:
        """Contra-caso: un error de parseo no puede borrar el embedding.

        Es la decisión documentada en docs/decisions-log.md — el archivo puede
        estar a medio sincronizar, y borrar por un parseo fallido pierde el
        índice de una nota que sigue existiendo.
        """
        from adso import bot as bot_mod

        embeddings = MagicMock()
        embeddings.remove_note = AsyncMock()
        app = _make_app(self._settings(vault_path), embeddings)
        nota = vault_path / "00-Inbox" / "rota.md"
        nota.parent.mkdir(parents=True, exist_ok=True)
        nota.write_text("---\ntitle: [sin cerrar\n---\ncuerpo\n", encoding="utf-8")

        with patch.object(bot_mod.vault_cache, "parse_cached", return_value=None):
            ctor = await _post_init_con_watcher_mockeado(app)
            await _callback_de_reindex(ctor)(nota)

        embeddings.remove_note.assert_not_awaited()
