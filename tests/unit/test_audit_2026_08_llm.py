"""Reproductores de los bugs de la auditoría 2026-08-22 — capa LLM y config.

Mismo contrato que `test_audit_2026_08_vault.py`: cada test **especifica el
comportamiento correcto** y se escribió reproduciendo el bug (fallaba) antes de
aplicar el fix. Ahora pasan y quedan como regresión: si alguno de estos defectos
vuelve, fallan.

Issues:
  L1 — un `type` inválido degrada una captura de texto/audio cuyo `type` el
       propio bot iba a pisar con el elegido por los botones.
  L2 — `status: ""` / `priority: ""` (y `"In Progress"`) tiran toda la respuesta
       a modo degradado en vez de descartar/normalizar el campo.
  L3 — el fallback "título = content[:80]" vive dentro del try de Gemini, así que
       no cubre ni a Groq ni al redirect de modos no implementados.
  L4 — `due_date`/`scheduled` se validan pero no se coaccionan a string.
  L5 — `frontmatter: null` en un `mode=manage` revienta la inyección de `extra_fm`.
  L6 — `reclassify_inbox` y `/clasificar` no usan `_redirect_unimplemented_mode`.
  L7 — `vault_seed` como lista aborta el arranque con AttributeError crudo.
  L8 — PyYAML resuelve `12:00` sin comillas como el int 720 y el ConfigError
       habla de un valor que no aparece en el archivo del usuario.

El hilo común: una respuesta del LLM perfectamente utilizable —o un config
perfectamente legible— termina en modo degradado, en un crash, o en una nota
atrapada en el Inbox para siempre.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from adso.config import ConfigError, load_settings
from adso.llm_schema import validate_llm_response


def _config(tmp_path: Path, contenido: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(contenido, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# L1 — el `type` del LLM se descarta en texto/audio, pero antes degrada la nota
# ---------------------------------------------------------------------------
#
# `_validate_capture_payload` lanza `LLMResponseError` si `type` no está en
# VALID_TYPES; el `except` de `classify()` quema los 3 reintentos y cae a modo
# degradado (Inbox + pending-classification). Pero para `media_type` text/audio el
# tipo lo eligió el usuario con los botones [Tarea]/[Nota] y `capture.py:324-331`
# lo pisa DESPUÉS con `forced_type`/`prevent_task`: la nota se degrada por un
# campo que el bot iba a descartar igual. "note"/"nota" es justo lo que devuelve
# un modelo chico cuando el prompt le habla de notas.


class TestL1TypeInvalidoEnTextoYAudio:
    def _respuesta(self) -> str:
        return json.dumps({
            "mode": "capture",
            "confidence": 0.9,
            "payload": {
                "frontmatter": {
                    "title": "Comprar pan",
                    "type": "note",  # inválido, pero irrelevante para text/audio
                    "tags": ["compras"],
                    "status": "active",
                },
                "body": "comprar pan mañana",
            },
        })

    @pytest.mark.parametrize("media_type", ["text", "audio"])
    async def test_type_invalido_no_degrada_la_captura(self, media_type: str) -> None:
        from adso import llm_client

        with patch.object(
            llm_client, "_call_gemini", AsyncMock(return_value=self._respuesta())
        ), patch.object(llm_client.asyncio, "sleep", AsyncMock()):
            result = await llm_client.classify(
                content="comprar pan mañana",
                media_type=media_type,
                existing_projects=[],
                existing_areas=[],
                existing_tags=[],
            )

        assert result["mode"] == "capture", (
            "la captura cayó a modo degradado por un `type` que el flujo de "
            "text/audio reemplaza con el elegido en los botones [Tarea]/[Nota]"
        )

    async def test_en_documento_el_type_invalido_si_degrada(self) -> None:
        """Contra-caso: en PDF/imagen/link el `type` sí lo decide el LLM, así que
        un valor inválido debe seguir cayendo a degradado."""
        from adso import llm_client

        with patch.object(
            llm_client, "_call_gemini", AsyncMock(return_value=self._respuesta())
        ), patch.object(llm_client.asyncio, "sleep", AsyncMock()):
            result = await llm_client.classify(
                content="texto de un pdf",
                media_type="document",
                existing_projects=[],
                existing_areas=[],
                existing_tags=[],
            )

        assert result["mode"] == "degraded"


# ---------------------------------------------------------------------------
# L2 — enums vacíos o con espacio tiran toda la respuesta a degradado
# ---------------------------------------------------------------------------
#
# `_norm_enum` normaliza case y espacios sobrantes, pero no cubre las dos formas
# más triviales de "sin valor" de un modelo chico: el string vacío y el separador
# equivocado. `priority: ""` → `""` ∉ VALID_PRIORITY → LLMResponseError → modo
# degradado para TODA la respuesta, cuando el campo es opcional y `None` ya se
# acepta sin chistar. Ídem `status: ""`, e `"In Progress"` (que normaliza a
# "in progress", con espacio, y no matchea "in-progress").


class TestL2EnumsVaciosONormalizables:
    def _respuesta(self, **fm_extra) -> dict:
        fm = {"title": "Mandar el informe", "type": "task", "tags": []}
        fm.update(fm_extra)
        return {
            "mode": "capture",
            "confidence": 0.9,
            "payload": {"frontmatter": fm, "body": "mandar el informe"},
        }

    def test_priority_vacia_se_descarta(self) -> None:
        r = validate_llm_response(self._respuesta(priority="", status="pending"))

        assert not r["payload"]["frontmatter"].get("priority"), (
            "un string vacío es 'sin prioridad', igual que None: se descarta, no se degrada"
        )

    def test_status_vacio_se_descarta(self) -> None:
        r = validate_llm_response(self._respuesta(status=""))

        assert not r["payload"]["frontmatter"].get("status"), (
            "un string vacío es 'sin estado': lo completa _STATUS_DEFAULT aguas abajo"
        )

    def test_status_con_espacio_se_normaliza_a_kebab(self) -> None:
        r = validate_llm_response(self._respuesta(status="In Progress"))

        assert r["payload"]["frontmatter"]["status"] == "in-progress"

    def test_none_sigue_aceptandose(self) -> None:
        """Contra-caso: `None` ya está contemplado — el bug es solo el vacío."""
        r = validate_llm_response(self._respuesta(status=None, priority=None))
        fm = r["payload"]["frontmatter"]
        assert fm["status"] is None and fm["priority"] is None


# ---------------------------------------------------------------------------
# L3 — el fallback de título no cubre al fallback de Groq
# ---------------------------------------------------------------------------
#
# `_validate_capture_payload` deja `title: ""` a propósito ("will be filled with
# content fallback in classify()"), pero ese relleno vive DENTRO del `try` del
# loop de Gemini (llm_client.py:454-457). El `return groq_result` del camino de
# cuota diaria lo saltea entero, así que la nota se escribe con título vacío —
# `create_note` termina nombrando el archivo con lo que pueda y el preview
# muestra un título en blanco. Mismo agujero para `_redirect_unimplemented_mode`.


class TestL3TituloVacioEnElFallbackDeGroq:
    async def test_groq_sin_titulo_cae_al_contenido(self, monkeypatch) -> None:
        from adso import llm_client

        monkeypatch.setenv("GROQ_API_KEY", "gsk-dummy")
        cuota = Exception(
            "429 RESOURCE_EXHAUSTED: quota exceeded for metric "
            "generate_content_free_tier_requests, limit: GenerateRequestsPerDayPerProject"
        )
        groq = json.dumps({
            "mode": "capture",
            "confidence": 0.8,
            "payload": {
                "frontmatter": {"title": None, "type": "reference", "tags": [], "status": "active"},
                "body": "cuerpo",
            },
        })

        with patch.object(llm_client, "_call_gemini", AsyncMock(side_effect=cuota)), \
             patch.object(llm_client, "_call_groq", AsyncMock(return_value=groq)), \
             patch.object(llm_client.asyncio, "sleep", AsyncMock()):
            result = await llm_client.classify(
                content="Reunión con el director sobre el capítulo 3",
                media_type="text",
                existing_projects=[],
                existing_areas=[],
                existing_tags=[],
            )

        assert result["mode"] == "capture"
        assert result["payload"]["frontmatter"]["title"], (
            "la nota queda con título vacío: el fallback content[:80] no corre "
            "para el camino de Groq"
        )


# ---------------------------------------------------------------------------
# L4 — `due_date` numérico sobrevive la validación y revienta en Google Tasks
# ---------------------------------------------------------------------------
#
# La sanitización hace `_dt.fromisoformat(str(val))` pero guarda el valor CRUDO.
# Con `due_date: 20260101` (un int — Groq no tiene schema constrained) el parseo
# pasa (Python ≥3.11 acepta el formato básico YYYYMMDD) y el int llega intacto al
# frontmatter. Después `tasks_client.py:158` hace `due_date[:10]` → TypeError, y
# el push de la tarea a Google Tasks muere.


class TestL4FechasNoString:
    @pytest.mark.parametrize("campo", ["due_date", "scheduled"])
    def test_fecha_numerica_se_coacciona_a_string(self, campo: str) -> None:
        fm = {"title": "Entregar el informe", "type": "task", "status": "pending", "tags": []}
        fm[campo] = 20260101

        r = validate_llm_response({
            "mode": "capture",
            "confidence": 0.9,
            "payload": {"frontmatter": fm, "body": "entregar el informe"},
        })

        valor = r["payload"]["frontmatter"][campo]
        assert isinstance(valor, str), (
            f"{campo}={valor!r} ({type(valor).__name__}) rompe el slice `due_date[:10]` "
            "de tasks_client.build_task_notes/create_task"
        )

    def test_fecha_iso_string_pasa_intacta(self) -> None:
        """Contra-caso: el camino normal no se altera."""
        r = validate_llm_response({
            "mode": "capture",
            "confidence": 0.9,
            "payload": {
                "frontmatter": {
                    "title": "T", "type": "task", "status": "pending",
                    "tags": [], "due_date": "2026-01-01",
                },
                "body": "b",
            },
        })
        assert r["payload"]["frontmatter"]["due_date"] == "2026-01-01"


# ---------------------------------------------------------------------------
# L5 — `frontmatter: null` + `extra_fm` = AttributeError con el texto ya popeado
# ---------------------------------------------------------------------------
#
# `capture.py:235-238` hace `fm = payload.get("frontmatter", {})` y después
# `fm.update(extra_fm)`. El default de `.get` no aplica cuando la clave EXISTE
# con valor None — y `frontmatter` es `nullable: True` en el schema de Gemini,
# además de que Groq lo emite libremente. Con `mode="manage"` el flujo ni siquiera
# pasa por `_redirect_unimplemented_mode` (que solo cubre query/edit), así que el
# AttributeError sube al error handler global. Los flujos que traen `extra_fm`
# (read_status de un PDF escaneado, OCR/Vision confirmado) ya popearon su estado:
# el texto extraído se pierde entero.


class TestL5FrontmatterNullConExtraFm:
    async def test_manage_con_frontmatter_null_no_lanza(
        self, mock_context, make_update
    ) -> None:
        from adso.handlers import capture

        update = make_update()
        respuesta = {
            "mode": "manage",
            "confidence": 0.9,
            "payload": {
                "frontmatter": None,
                "operation": "create_project",
                "params": {"name": "divulgación", "description": "x"},
            },
        }

        with patch.object(capture, "classify", AsyncMock(return_value=respuesta)):
            await capture._classify_and_preview(
                update,
                mock_context,
                "texto que salió del OCR de un PDF escaneado",
                media_type="document",
                extra_fm={"read_status": "unread"},
            )

        assert update.message.reply_text.await_args_list, (
            "el flujo murió con AttributeError: el texto del OCR se perdió y el "
            "usuario solo ve el mensaje genérico del error handler global"
        )


# ---------------------------------------------------------------------------
# L6 — una nota de Inbox que "parece pregunta" queda atrapada para siempre
# ---------------------------------------------------------------------------
#
# `_redirect_unimplemented_mode` existe justamente porque el LLM sigue devolviendo
# `query`/`edit` pese a que el prompt ya no los ofrece, pero solo lo usa
# `_classify_and_preview`. En `reclassify_inbox` (jobs.py:113-118) y en
# `/clasificar` (commands.py:283-285) un `mode != "capture"` es un `continue` /
# "No se pudo clasificar": la nota degradada cuyo contenido tiene forma de
# pregunta ("¿qué papers hay sobre X?") vuelve a clasificarse igual en CADA
# pasada del cron, quema quota cada 30 minutos y nunca sale del Inbox.


class TestL6ModoNoImplementadoAlReclasificar:
    def _nota_de_inbox(self, vault_path: Path) -> Path:
        from adso import vault_cache

        vault_cache.clear()
        p = vault_path / "00-Inbox" / "pregunta.md"
        p.write_text(
            "---\n"
            "title: '[Sin clasificar] qué tengo sobre transformers'\n"
            "type: idea\n"
            "status: pending-classification\n"
            "area: investigacion\n"
            "media_type: text\n"
            "source: telegram\n"
            "tags: []\n"
            "---\n\n"
            "qué tengo sobre transformers y atención\n",
            encoding="utf-8",
        )
        return p

    def _respuesta_query(self) -> dict:
        return {
            "mode": "query",
            "confidence": 0.9,
            "payload": {
                "frontmatter": {
                    "title": "Transformers y atención",
                    "type": "reference",
                    "tags": ["transformers"],
                    "status": "active",
                },
                "body": "qué tengo sobre transformers y atención",
            },
        }

    async def test_el_cron_saca_la_nota_del_inbox(
        self, mock_context, vault_path: Path
    ) -> None:
        from adso.handlers import jobs

        nota = self._nota_de_inbox(vault_path)
        mock_context.application.user_data = {}
        mock_context.bot.send_message = AsyncMock()

        with patch.object(jobs, "classify", AsyncMock(return_value=self._respuesta_query())):
            await jobs.reclassify_inbox(mock_context)

        assert not nota.exists(), (
            "la nota sigue en 00-Inbox: el cron la va a reclasificar (y a quemar "
            "quota) en cada pasada, para siempre"
        )
        assert list((vault_path / "02-Areas").rglob("*.md")), (
            "la nota debía quedar en su área, que es el destino que el usuario ya eligió"
        )

    @patch("adso.security.ALLOWED_USER_IDS", {42})
    async def test_clasificar_manual_muestra_el_preview(
        self, mock_context, make_update, vault_path: Path
    ) -> None:
        from adso import vault_cache
        from adso.handlers import commands

        vault_cache.clear()
        # Caso B (sin destino): es el que atiende /clasificar.
        nota = vault_path / "00-Inbox" / "pregunta.md"
        nota.write_text(
            "---\ntitle: 'X'\ntype: idea\nstatus: pending-classification\n"
            "media_type: text\nsource: telegram\ntags: []\n---\n\n"
            "qué tengo sobre transformers y atención\n",
            encoding="utf-8",
        )

        update = make_update("/clasificar")
        with patch.object(
            commands, "classify", AsyncMock(return_value=self._respuesta_query())
        ):
            await commands.handle_clasificar(update, mock_context)

        assert mock_context.user_data.get("pending_note"), (
            "sin preview el usuario no tiene forma de sacar la nota del Inbox: "
            "/clasificar le va a contestar lo mismo cada vez"
        )


# ---------------------------------------------------------------------------
# L7 — `vault_seed` mal tipado mata el arranque con un traceback crudo
# ---------------------------------------------------------------------------
#
# `_build_section` chequea `isinstance(data, dict)` y da un ConfigError claro
# ("se esperaba un mapa de claves"); `_build_vault_seed` es la única sección que
# no lo hace y va directo a `data.get("projects", [])`. Un `vault_seed` escrito
# como lista —la confusión natural, porque sus hijos SÍ son listas— mata el
# arranque con `AttributeError: 'list' object has no attribute 'get'`, sin decir
# qué clave del YAML hay que tocar.


class TestL7VaultSeedNoEsUnMapa:
    def test_vault_seed_como_lista_da_config_error(self, tmp_path: Path) -> None:
        path = _config(tmp_path, """
vault_seed:
  - name: tesis
    description: doctorado
""")
        with pytest.raises(ConfigError):
            load_settings(path)

    def test_otra_seccion_como_lista_ya_da_config_error(self, tmp_path: Path) -> None:
        """Contra-caso: el resto de las secciones sí lo hacen bien."""
        path = _config(tmp_path, """
links:
  - similarity_threshold: 0.8
""")
        with pytest.raises(ConfigError):
            load_settings(path)


# ---------------------------------------------------------------------------
# L8 — YAML 1.1 convierte `12:00` en 720 y el error habla de un valor fantasma
# ---------------------------------------------------------------------------
#
# PyYAML resuelve un escalar sin comillas con dos puntos como sexagesimal
# (YAML 1.1): `12:00` → `720`. El usuario recibe entonces
# `ConfigError: reindex.time: '720' no es una hora válida (formato HH:MM, ej: 03:00)`
# — un valor que NO aparece en su archivo, así que el mensaje no ayuda a
# arreglarlo. Peor: con `03:00` no pasa (el cero inicial lo deja string), así que
# el ejemplo del propio mensaje de error funciona y el del usuario no.
#
# Comportamiento correcto elegido: seguir rechazando (aceptar en silencio un int
# obligaría a adivinar la intención — 720 también puede ser un typo de otra cosa),
# pero nombrar la causa real. El fix es una línea en el mensaje: si el valor es
# un int, decir que hay que poner la hora entre comillas.


class TestL8HoraSexagesimalDeYaml:
    def test_el_error_explica_que_faltan_las_comillas(self, tmp_path: Path) -> None:
        path = _config(tmp_path, """
reindex:
  enabled: true
  time: 12:00
""")
        with pytest.raises(ConfigError) as exc:
            load_settings(path)

        mensaje = str(exc.value).lower()
        assert "comillas" in mensaje, (
            f"el mensaje habla de un valor que no está en el archivo del usuario: {exc.value}"
        )

    def test_lo_mismo_en_weekly_report(self, tmp_path: Path) -> None:
        path = _config(tmp_path, """
weekly_report:
  enabled: true
  time: 12:00
""")
        with pytest.raises(ConfigError) as exc:
            load_settings(path)

        assert "comillas" in str(exc.value).lower()

    def test_con_cero_inicial_no_hay_problema(self, tmp_path: Path) -> None:
        """Contra-caso que hace al bug tan confuso: `03:00` sin comillas es string
        para PyYAML (el cero inicial rompe el resolver sexagesimal) y carga bien."""
        path = _config(tmp_path, """
reindex:
  enabled: true
  time: 03:00
""")
        assert load_settings(path).reindex.time == "03:00"
