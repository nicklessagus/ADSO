```
      ,
     /|
    / |   █████     ██████      █████     █████
   / /   ██   ██    ██   ██    ██        ██   ██
  | /    ██   ██    ██   ██     ████     ██   ██
  |/     ███████    ██   ██        ██    ██   ██
  |      ██   ██    ██████     █████      █████
 _|_
/   \    Autonomous Data Structuring Orchestrator
|>_ |
\___/    𝘴𝘤𝘳𝘪𝘱𝘵𝘰𝘳𝘪𝘶𝘮 𝘥𝘪𝘨𝘪𝘵𝘢𝘭𝘦
```

# Estrategia de Testing

Define cómo se testea ADSO: qué se testea, a qué nivel, con qué herramientas y con qué cobertura esperada.

---

## Principios

- **Sin pérdida de datos es la prioridad #1.** Todo camino que toca el vault (escritura, edición, borrado) debe tener test. Si un test puede fallar sin que nadie lo note y eso causa pérdida de datos, falta un test.
- **Tests rápidos primero.** Los unit tests deben correr en segundos. Si un test necesita red, es integration o e2e.
- **Mocks para APIs externas, reales para filesystem.** Gemini, Telegram y Google APIs se mockean siempre. ChromaDB y el vault usan directorios temporales reales — así se detectan errores de path, permisos y encoding.
- **Los tests corren en dev, no en producción.** La RPi4 no ejecuta tests. El CI corre en GitHub Actions (o local en la máquina de desarrollo).
- **Fixtures reproducibles.** Las respuestas del LLM se graban como JSON y se replayan. No hay tests que dependan de una API externa en tiempo real.

---

## Stack

```
pytest                  # framework principal
pytest-asyncio          # soporte para tests async (todo ADSO es async)
pytest-cov              # cobertura de código
tmp_path (built-in)     # directorios temporales para vault y ChromaDB
unittest.mock           # mocks y patches (stdlib, sin deps extra)
```

Todas las dependencias de testing van en un grupo separado (`requirements-dev.txt` o `[project.optional-dependencies.dev]` en `pyproject.toml`).

---

## Estructura de archivos

```
tests/
├── conftest.py                    # fixtures globales
├── unit/
│   ├── test_frontmatter.py        # generación y validación de YAML
│   ├── test_file_naming.py        # slug, fecha, kebab-case
│   ├── test_config.py             # carga de config.yaml, defaults, merge con env
│   ├── test_classification.py     # parsing del modo (capture/manage) y validación de schema
│   ├── test_security.py           # auth middleware: allow, reject, edge cases
│   ├── test_vault_search.py       # parsing de wikilinks, tags, frontmatter YAML
│   ├── test_vault_cache.py        # caché de parsing por (mtime, size), invalidación, LRU
│   ├── test_arxiv_client.py       # parsing de metadata arXiv (Atom feed)
│   ├── test_tasks_client.py       # Google Tasks API async
│   ├── test_vault_watcher.py      # eventos watchdog, deduplicación inotify
│   ├── test_vault_writer_ops.py   # operaciones vault_writer (read, append, set_property)
│   ├── test_embeddings.py         # ChromaDB: index, remove, query_similar
│   ├── test_knowledge_query.py    # retrieval semántico (Fase 7.0)
│   ├── test_document_extractor.py # detección de papers, extracción de secciones
│   ├── test_transcriber.py        # faster-whisper: modelo, idioma, fallback
│   ├── test_jobs.py               # crons: reclassify_inbox, heartbeat, skip por flujo activo
│   ├── test_reporters.py          # formateo de reportes (reporte, reporte_full)
│   ├── test_bot_errors.py         # error handler global de PTB
│   ├── test_confirm_failure.py    # "crear antes de descartar": reintento tras fallo de escritura
│   ├── test_capture_links.py      # caracterización de `_suggest_links`: reutilización del embedding del preview (I5)
│   ├── test_timing.py             # `Stopwatch` de la captura + silenciado del log de apscheduler
│   ├── test_audit_block_b.py      # guards de regresión del bloque B (auditoría 2026-07-31)
│   ├── test_audit_block_cd.py     # guards de regresión de los bloques C y D
│   ├── test_audit_block_e.py      # bloque E: flujos de captura/UI (bot bloqueado sin teclado, pérdida de contenido)
│   ├── test_audit_block_f.py      # bloque F: otros medios (watcher, reportes, arXiv, callback_data)
│   ├── test_audit_block_g.py      # bloque G: hallazgos bajos (TOCTOU de `_unique_path`, auth, permisos, config)
│   ├── test_audit_2026_08_vault.py    # auditoría 2026-08: capa de vault (wikilinks al mover, watcher, code fences)
│   ├── test_audit_2026_08_capture.py  # auditoría 2026-08: flujo de captura (reintento, estado colgado, OCR→Vision)
│   ├── test_audit_2026_08_input.py    # auditoría 2026-08: entrada de medios (`edited_message`, estado antes del reply)
│   ├── test_audit_2026_08_llm.py      # auditoría 2026-08: capa LLM y config (type/status inválidos, fallback de título)
│   ├── test_audit_2026_08_query.py    # auditoría 2026-08: embeddings y retrieval (metadata de Chroma, huérfanos, warmup)
│   ├── test_audit_2026_08_reports.py  # auditoría 2026-08: reportes y jobs (reporte vacío, scope borrado, escapado)
│   ├── test_audit_2026_08_data.py     # auditoría 2026-08: datos escritos al vault (bugs con evidencia en el vault real)
│   └── test_suite_hygiene.py      # markers por directorio (guard de G15)
├── integration/
│   ├── test_capture_flow.py       # LLM mock → vault_writer → archivo en disco
│   ├── test_degraded_mode.py      # LLM falla → nota en 00-Inbox/pending
│   ├── test_embeddings_integration.py  # vault_writer → embeddings → ChromaDB
│   ├── test_git_backup.py         # debounce, commit messages, push failures
│   └── test_vault_search_integration.py  # backlinks y filtros contra vault temporal
├── e2e/
│   ├── test_capture_message.py    # Update simulado → respuesta + vault escrito
│   ├── test_confirmation_flow.py  # Update → preview → confirm/reject → resultado
│   ├── test_media_handlers.py     # audio, imagen, documento → flujo completo
│   ├── test_query_handler.py      # /buscar — retrieval semántico (Fase 7.0)
│   └── test_bot_extra.py          # casos edge: /reset, modo corrección, estado pendiente
```

Tests planificados (no implementados aún):
- `tests/integration/test_calendar_sync.py` — mock Google API (Fase 6 Calendar, diferida)
- e2e de síntesis RAG con scope y expansión (Fase 7 completa)

---

## Niveles de testing

### Unit tests

Testean funciones puras sin I/O externo. Son el grueso de la suite.

#### `test_frontmatter.py`

Qué se testea:
- Generación de frontmatter válido por cada tipo (`reference`, `task`, `idea`, `project-index`, `area-index`)
- Campos base siempre presentes: `title`, `date_created`, `date_modified`, `type`, `tags`, `source`, `media_type`, `status`
- Campos académicos opcionales en `reference` (`authors`, `year`, `doi`, `methods`, etc.) presentes solo cuando aplica
- `date_created` y `date_modified` en formato ISO 8601
- `tags` en kebab-case
- `source` es `"telegram"` para notas de usuario y `"system"` para `project-index`
- `media_type` correcto según origen (`text`, `audio`, `image`, `link`, `document`)
- Frontmatter con caracteres especiales en `title` (comillas, dos puntos, unicode)
- YAML generado es parseable por cualquier parser YAML estándar

#### `test_file_naming.py`

Qué se testea:
- Formato: `YYYY-MM-DD-titulo-en-kebab-case.md`
- Caracteres especiales removidos o transliterados
- Títulos largos truncados a longitud razonable
- Títulos con acentos y ñ: `á` → `a`, `ñ` → `n` (o se preservan — definir)
- Títulos vacíos o solo espacios → fallback a slug genérico
- Sin colisión: si el archivo ya existe, se agrega sufijo numérico

#### `test_config.py`

Qué se testea:
- Carga de `config.yaml` válido → valores correctos
- `config.yaml` ausente → todos los defaults aplicados
- `config.yaml` parcial → merge con defaults
- Valores inválidos → error claro (no falla silencioso)
- Tipos correctos: `similarity_threshold` es float, `max_results` es int, etc.

#### `test_classification.py`

Qué se testea:
- Parsing de la respuesta JSON del LLM que indica el modo
- Modos activos: `capture` (captura) y `manage` (gestión)
- Modos redirigidos: `query` y `edit` → redirigidos a `capture` (Fase 7 no implementada)
- JSON malformado del LLM → `LLMResponseError` manejable, no excepción desnuda
- Campos faltantes en la respuesta → error explícito o default razonable
- Validación de `type` (`reference` | `task` | `idea`), `status` por tipo, `priority`, fechas ISO 8601
- Status aliases: `todo` → `pending`, `draft` → `raw`
- Patrones de injection: `check_injection_risk()` detecta variantes en inglés y español

#### `test_vault_search.py`

Qué se testea:
- Extracción de `[[wikilinks]]` de un texto markdown (simples, con alias `[[nota|texto]]`, de sección `[[nota#heading]]`)
- Extracción de tags (`#tag`, `#nested/tag`, tags en frontmatter)
- Parsing de frontmatter YAML: campos correctos, tipos correctos, YAML inválido manejado sin excepción
- Tags jerárquicos: `#metodo/cnn` matchea búsqueda por `#metodo`
- Wikilinks dentro de code blocks o comentarios `%%` no se extraen (son falsos positivos)

#### `test_knowledge_query.py`

Qué se testea:
- Parsing de resultados de ChromaDB: scores, metadata, deduplicación
- Filtrado por `rag.similarity_threshold` — notas debajo del umbral no se incluyen
- `max_results` respetado — no se devuelven más notas que el límite
- Query vacía o sin embedding → error manejable
- Resultados vacíos → lista vacía (no excepción)

#### `test_security.py`

Qué se testea:
- User ID autorizado → handler se ejecuta
- User ID no autorizado → silencio total (sin respuesta, sin log del contenido)
- User ID ausente (update sin `effective_user`) → reject
- Múltiples user IDs si se soporta en el futuro
- Middleware aplicado correctamente (no bypasseable)

---

### Integration tests

Testean la interacción entre módulos. APIs externas mockeadas, filesystem real (temporal).

#### `test_capture_flow.py`

Setup:
- Vault temporal (`tmp_path`)
- Mock de `llm_client` que retorna JSON fijo (desde `fixtures/llm_responses/`)
- `vault_writer` real apuntando al vault temporal

Qué se testea:
- Texto → `llm_client` clasifica → `vault_writer` escribe → archivo existe en path correcto
- Frontmatter del archivo escrito matchea lo que devolvió el LLM
- Body del archivo contiene el contenido original
- Directorios intermedios se crean si no existen
- `date_modified` se actualiza al escribir

#### `test_degraded_mode.py`

Setup:
- Mock de `llm_client` que lanza excepción (simula Gemini caído)
- `vault_writer` real con vault temporal

Qué se testea:
- Input llega → LLM falla → nota se escribe en `00-Inbox/`
- `status: pending-classification` en frontmatter
- Body preserva el contenido original íntegro (sin pérdida de datos)
- `media_type` correcto aunque no haya clasificación
- Mensaje al usuario informando del modo degradado

#### `test_embeddings_integration.py`

Setup:
- ChromaDB temporal (directorio efímero)
- Mock de Gemini Embedding API (retorna vector fijo de 768 dims)
- Nota de ejemplo en vault temporal

Qué se testea:
- Nota escrita → embedding generado → almacenado en ChromaDB
- Búsqueda por similitud retorna la nota recién indexada
- Carpetas en `vault.exclude_dirs` no se indexan
- Nota editada → embedding actualizado (no duplicado)
- Nota borrada → embedding removido de ChromaDB

#### `test_edit_flow.py` *(planificado — modo edición es Fase 7)*

Setup:
- Vault temporal con nota pre-existente
- Mock de `llm_client`

Qué se testea:
- Edición de nota existente → `date_modified` actualizado, `date_created` intacto
- Contenido anterior preservado si la edición es parcial (append)
- Re-indexado en ChromaDB post-edición

#### `test_rename_flow.py` *(planificado)*

Setup:
- Vault temporal con nota A y notas B, C que la referencian con `[[A]]`
- ChromaDB temporal con embeddings de A, B, C

Qué se testea:
- Renombrar A → A2: archivo renombrado en disco
- Backlinks actualizados: B y C ahora contienen `[[A2]]` en vez de `[[A]]`
- ChromaDB: el path/metadata de A actualizado al nuevo nombre
- ChromaDB: B y C re-indexados con contenido actualizado
- Nota sin backlinks: renombrado no falla, ChromaDB actualizado
- `date_modified` de B y C actualizado tras cambio de backlinks

#### `test_git_backup.py`

Setup:
- Vault temporal inicializado como repo git
- Mock de `git push`

Qué se testea:
- Una nota → commit+push después del debounce
- Varias notas rápidas → un solo commit consolidado con todos los títulos
- Push falla → error logueado, no se pierde la nota (ya está en disco)
- Commit message correcto según cantidad de notas

#### `test_vault_search_integration.py`

Setup:
- Vault temporal (`tmp_path`) con varias notas `.md` pre-creadas, con wikilinks cruzados, tags y frontmatter variado

Qué se testea:
- **Backlinks:** nota A linkea a nota B → buscar backlinks de B retorna A
- **Backlinks múltiples:** varias notas linkean a la misma → retorna todas
- **Backlinks inexistentes:** nota sin backlinks → retorna lista vacía
- **Filtro por frontmatter:** buscar `type=task, status=pending` → solo retorna tasks pendientes
- **Filtro combinado:** `project=tesis AND type=note` → solo notas de tesis
- **Tags:** buscar por `#metodo` retorna notas con `#metodo` y `#metodo/cnn`
- **Vault vacío:** no falla, retorna resultados vacíos
- **Notas con frontmatter inválido:** se ignoran sin romper la búsqueda

#### `test_calendar_sync.py` *(planificado — Fase 6 Calendar, diferida)*

Setup:
- Mock de Google Calendar API con eventos de ejemplo
- Mock de Google Tasks API con tareas de ejemplo

Qué se testea:
- Eventos parseados correctamente (título, fecha, hora, duración)
- Escritura de evento al calendario `ADSO`
- Lectura de todos los calendarios
- Creación de tarea en lista `ADSO`
- Lectura de listas externas (solo lectura, sin escritura)

---

### Tests end-to-end (E2E)

Simulan el flujo completo desde un mensaje de Telegram hasta el resultado final. Usan objetos `Update` construidos programáticamente — no requieren un bot real corriendo ni conexión a Telegram.

#### Construcción de Updates y Callbacks

`conftest.py` expone factories `make_update()` y `make_callback_query()` para simular mensajes y respuestas a inline keyboards. Ver sección "Fixtures globales" más abajo.

#### `test_capture_message.py`

Qué se testea:
- Mensaje de texto → bot responde con preview → usuario confirma → nota en vault
- Mensaje con link → extracción de contenido → preview → vault
- `media_type` correcto en cada caso

#### `test_query_handler.py`

Cubre la Fase 7.0 (retrieval puro con `/buscar`):
- `/buscar <texto>` → búsqueda semántica en ChromaDB → respuesta con notas relevantes
- Query sin resultados → mensaje claro
- Botón `[🔎 Buscar en el vault]` desde el teclado de texto/audio

Escenarios planificados para la Fase 7 completa (scope, expansión, síntesis):
- Query con scope seleccionado via inline keyboard → busca en proyecto, luego ofrece ampliar
- Expansión desde nodo: "todo lo relacionado con [[nota]]" → backlinks + vecinos semánticos
- Query mixta: filtro estructural + semántico ("papers pendientes sobre ML")
- Resultado corto (≤ 3 notas) → respuesta inline con botón `[Informe .md]`
- Resultado largo → bot envía archivo `.md` con links `obsidian://`

#### `test_task_creation.py` *(planificado — parcialmente cubierto por `test_tasks_client.py` unit)*

Qué se testea:
- Mensaje clasificado como task → task creada en vault y Google Tasks mock
- Task con `scheduled` (fecha/hora) → evento en Calendar ADSO mock
- Task con `due_date` (solo fecha) → chip de fecha en Google Tasks
- Campo `notes` de Google Tasks contiene: descripción + proyecto/área + prioridad (sin links `obsidian://` — no funcionan desde Google Tasks)
- Confirmación antes de crear task

#### `test_confirmation_flow.py`

Qué se testea:
- Preview mostrado → usuario toca `[Confirmar]` (inline keyboard callback) → nota escrita
- Preview mostrado → usuario toca `[Cancelar]` → nota NO escrita
- Preview mostrado → usuario toca `[Corregir]` → selector de destino → confirma → nota escrita con destino corregido
- Desambiguación: bot muestra `[Guardar como nota]` / `[Buscar en vault]` → callback procesado correctamente
- Scope de consulta: bot muestra botones de proyecto → callback filtra resultados
- **Crítico:** sin confirmación explícita via callback, nunca se escribe al vault

---

## Fixtures globales (`conftest.py`)

> Ejemplo ilustrativo del patrón. El `tests/conftest.py` real expone:
> `vault_path`, `sample_config`, `llm_fixture`, `make_update`,
> `make_callback_query`, `mock_context` (+ helpers `make_user`/`make_chat`/
> `make_message`) — ver el archivo para las firmas exactas.

```python
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock

@pytest.fixture
def vault_path(tmp_path) -> Path:
    """Crea estructura de vault temporal con carpetas PARA."""
    for d in ["00-Inbox", "01-Projects", "02-Areas",
              "03-Resources", "05-Archive"]:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path

@pytest.fixture
def chroma_path(tmp_path) -> Path:
    """Directorio temporal para ChromaDB."""
    p = tmp_path / "chroma"
    p.mkdir()
    return p

@pytest.fixture
def mock_llm_client() -> AsyncMock:
    """Mock de llm_client.classify con respuesta default válida."""
    client = AsyncMock()
    # El schema real tiene wrapper {mode, confidence, payload{frontmatter, body, summary}}
    client.classify.return_value = {
        "mode": "capture",
        "confidence": 0.95,
        "needs_disambiguation": False,
        "payload": {
            "frontmatter": {
                "title": "Nota de prueba",
                "type": "reference",
                "tags": ["test"],
                "status": "active",
                "project": "tesis",
                "section": None,
                "area": None,
                "priority": None,
            },
            "body": "Cuerpo de prueba.",
            "summary": None,
        },
    }
    return client

@pytest.fixture
def mock_gemini_embeddings() -> AsyncMock:
    """Mock de Gemini Embedding API que retorna vector fijo."""
    mock = AsyncMock()
    mock.embed.return_value = [0.1] * 768
    return mock

@pytest.fixture
def sample_config(tmp_path) -> Path:
    """config.yaml de ejemplo para tests."""
    config = tmp_path / "config.yaml"
    config.write_text("""
weekly_report:
  enabled: true
  day: friday
  time: "18:00"
  sections:
    notes_summary: true
    most_active_project: true
    papers_queue: true
    inbox_suggestion: true
    tasks_summary: true
    stale_ideas: true
    paper_suggestion: true
  stale_idea_days: 60
rag:
  similarity_threshold: 0.75
  max_results: 10
links:
  similarity_threshold: 0.82
  max_suggestions: 5
vault_seed:
  projects:
    - name: tesis
      description: "Papers de doctorado, experimentos de ML."
  areas:
    - name: docencia
      description: "Preparación de clases, guías de ejercicios."
vault:
  exclude_dirs:
    - "05-Archive"
    - ".obsidian"
    - ".trash"
content_extraction:
  engine: gemini
documents:
  max_size_mb: 20
whisper:
  model: base
reindex:
  enabled: true
  time: "03:00"
sync:
  interval_minutes: 30
backup:
  debounce_seconds: 30
llm:
  max_web_tokens: 8000
  max_paper_tokens: 128000
  degraded_retry_minutes: 30
""")
    return config

@pytest.fixture
def make_update():
    """Factory de objetos Update de Telegram."""
    def _make(text: str = "", photo: bool = False, voice: bool = False) -> Update:
        # Construye Update con effective_user.id = ALLOWED_USER_ID
        ...
    return _make

@pytest.fixture
def make_callback_query():
    """Factory de CallbackQuery para simular respuestas a inline keyboards."""
    def _make(data: str, message: Message = None) -> CallbackQuery:
        # Construye CallbackQuery con callback_data=data
        # message es el mensaje original que contenía los botones
        ...
    return _make
```

---

## Cobertura

### Targets por módulo

| Módulo | Target | Justificación |
|---|---|---|
| `vault_writer.py` | ≥ 90% | Toca el vault directamente. Error acá = pérdida de datos. |
| `llm_client.py` | ≥ 85% | Parsing de JSON externo. Error acá = clasificación incorrecta. |
| `security.py` | 100% | Pequeño y crítico. No hay excusa para no cubrir todo. |
| `config.py` | ≥ 90% | Defaults incorrectos pueden causar errores difíciles de debuggear. |
| `vault_search.py` | ≥ 85% | Lee el vault para backlinks/tags/filtros. Errores degradan consultas estructurales. |
| `embeddings.py` | ≥ 80% | Errores no pierden datos (se puede re-indexar) pero degradan consultas. |
| `tasks_client.py` | ≥ 80% | Escribe a Google Tasks externo. |
| `transcriber.py` | ≥ 70% | Wrapper de faster-whisper, poco código propio. |
| `document_extractor.py` | ≥ 90% | Parsea el input **menos confiable** del sistema: PDFs de terceros. Un paper malicioso llega acá antes que a cualquier otra cosa. |

**Target global (CI): ≥ 70%** sobre todo `adso/` menos el bootstrap. Actual: **86%** sobre 5589 statements (2026-08-27).

`adso/handlers/*` **sí se mide** desde 2026-08-13. Antes estaba en el `omit` de
`pyproject.toml` con el argumento de que era territorio e2e — pero los e2e
existen y lo ejercitan, así que lo único que lograba era dejar ~1900 statements
(el 40% del código) fuera del número y publicar un 82% calculado sobre la mitad
del proyecto. Con los handlers omitidos, **un test nuevo sobre un handler no
movía el gate**, que es justo donde la regla test-first más hace falta. Detalle
en I3 de `docs/audit-2026-07-31.md`.

Cobertura actual de handlers (el terreno a ganar): `query.py` 94%, `callbacks.py`
82%, `capture.py` 83%, `commands.py` 75%, `manage.py` 75%, `input.py` 75%,
`jobs.py` 71%, `reports.py` 40%.

Módulos que hoy **no llegan** a su target de la tabla de arriba: `llm_client.py`
84% (target ≥ 85% — el lote 3 lo subió de 72%), `tasks_client.py` 73% (≥ 80%),
`transcriber.py` 56% (≥ 70%) y `security.py` 94% (target 100%). El resto está en
target o por encima: `config.py` 97%, `document_extractor.py` 97%,
`llm_schema.py` 95%, `embeddings.py` 92%, `vault_writer.py` 90%,
`vault_search.py` 87%.

### Qué NO se mide en CI

- `bot.py` y `__main__.py` — bootstrap de PTB (registro de handlers y jobs), sin lógica propia
- Código de terceros (`python-telegram-bot`, `chromadb`, `faster-whisper`)
- Archivos de configuración y fixtures
- `__init__.py` vacíos

---

## Cómo correr los tests

**Requisito:** `adso/security.py` valida `TELEGRAM_ALLOWED_USER_ID` en tiempo de import — sin estas variables de entorno, `pytest` falla en la colección. Exportar valores dummy (los mismos que usa CI):

```bash
export TELEGRAM_ALLOWED_USER_ID=12345
export TELEGRAM_TOKEN=dummy
export GEMINI_API_KEY=dummy
```

```bash
# Todos los tests (lo que corre CI)
pytest tests/ -v

# Por nivel — por ruta o por marker, equivalentes
pytest tests/unit/ -v
pytest -m "not integration and not e2e" -v    # solo unit
pytest -m integration -v
pytest -m e2e -v

# Con cobertura
pytest tests/ --cov=adso --cov-report=term-missing

# Con cobertura y reporte HTML
pytest tests/ --cov=adso --cov-report=html
```

### Markers — se asignan solos, por directorio

Los markers `integration` y `e2e` los aplica un hook en `tests/conftest.py`
(`pytest_collection_modifyitems` + `marker_for_path`) según el directorio del
archivo. **No hay que ponerlos a mano en ningún test**: un archivo nuevo en
`tests/e2e/` queda marcado por existir.

Está hecho así por el hallazgo G15 de `docs/audit-2026-07-31.md`. Los markers
estaban declarados en `pyproject.toml` y documentados acá, pero aplicados en
**cero** tests — nadie se acordaba de escribirlos. Consecuencias:

1. El `-m "not integration and not e2e"` de CI no excluía nada; corría los 618.
2. Peor: era una trampa armada. Aplicar los markers a mano —lo natural al leer
   esta doc— habría sacado 193 tests de CI **en silencio**, sin fallar nada.

Un directorio nuevo bajo `tests/` con tests adentro hace fallar
`test_suite_hygiene.py` hasta que se le decida un marker en `_DIR_MARKERS`
(`tests/conftest.py`) y en `_EXPECTED_DIRS` (el test). Es a propósito: obliga a
decidir en vez de heredar un default silencioso.

CI corre la suite completa en un solo step — ningún test toca la red, así que no
hay razón para segmentar. Si alguna vez se agrega un nivel que sí requiera red,
ese es el momento de excluirlo por marker (y de actualizar esta sección).

---

## Tests de reproducción — `xfail(strict=True)`

Convención introducida por la auditoría 2026-08-26 (`docs/audit-2026-08-26.md`).
Es el mecanismo que hace cumplir la regla test-first del repo **para bugs**, no
solo para features.

Un bug abierto no se documenta con un TODO ni con un issue a secas: se documenta
con un **test que especifica el comportamiento correcto** y lleva la marca
`xfail(strict=True)`.

```python
class TestE1MetadataNoString:
    @pytest.mark.xfail(
        strict=True,
        reason="BUG E1: _to_scored no coacciona a str la metadata de ChromaDB",
    )
    def test_to_scored_coacciona_los_campos_a_str(self) -> None:
        ...
```

Cómo funciona el ciclo:

1. **Se escribe el reproductor** y se lo mira fallar por el mecanismo documentado
   (los de la auditoría se verificaron además con `--runxfail`, para que un test
   que falla por un mock mal armado no se confunda con un defecto real).
2. **Se le pone `xfail(strict=True)`.** La suite queda **verde** — el bug está
   documentado y ejecutable sin romper CI.
3. **El día que alguien arregla el bug**, el test pasa a **XPASS**, y `strict`
   convierte ese XPASS en **fallo**. No hay forma de mergear el fix sin sacar la
   marca en el mismo commit.
4. **Sin la marca, el test queda como guard de regresión**: si el defecto vuelve,
   falla.

El `reason` es obligatorio y nombra el bug (`"BUG E1: ..."`), así que el reporte
de la suite es la lista viva de defectos conocidos. Un issue está cerrado cuando
su `xfail` desapareció y su test pasa.

Los siete archivos `tests/unit/test_audit_2026_08_*.py` siguen esta convención.
**Hoy ninguno lleva la marca puesta:** los 39 bugs de la auditoría se arreglaron
en el mismo commit que sacó sus `xfail`, así que los 897 tests pasan (`0 xfailed`,
`0 xpassed`) y todos esos reproductores quedaron como guards de regresión — el
paso 4 del ciclo. La convención sigue vigente para el próximo bug que se
documente antes de arreglarse.

---

## Harness de regresión de modelo

`scripts/llm_regression.py` + los datos en `tests/llm_regression/`
(`cases.yaml`, `baselines/`). Es parte de la estrategia de testing aunque **no
sea pytest**.

### Qué mide

El **contrato estructural** que el resto del bot asume del modelo. No mide
calidad de redacción ni de resumen — eso lo valida el usuario en el preview antes
de confirmar cada nota. Mide lo que el usuario *no* ve:

- que `validate_llm_response` **no lance** (si lanza, *toda* captura cae a modo
  degradado y termina en `00-Inbox`),
- que el `mode`, el `title`, el `body`, el destino y `confidence` cumplan lo
  mínimo que el código de abajo da por hecho,
- que el modelo **no obedezca prompt injection** ni filtre el system prompt.

Las reglas duras invalidan la corrida; las blandas (higiene de tags, `due_date`)
bajan el score y se comparan contra la baseline. La tabla completa de reglas está
en `tests/llm_regression/README.md`.

### Por qué no es un test de pytest

Porque **pega contra la API real y quema quota**. Si viviera bajo `tests/` como
test de pytest, un `pytest` local o un cambio en CI podría dispararlo por
accidente. Por eso es un script suelto en `scripts/` y `tests/llm_regression/`
guarda solo los datos. Costo por corrida: ~34 requests (11 casos × 3 + 1 de
Vision), holgado dentro del free tier; `--delay` los espacia para no chocar con
el RPM.

### Cuándo correrlo

**Antes de tocar `GEMINI_MODEL`.** Primero la baseline del modelo actual, después
el candidato comparado contra ella:

```bash
make llm-baseline                                        # baseline del modelo actual
make llm-check MODEL=gemini-3.7-flash BASE=gemini-3.5-flash-lite
# --vision-model evalúa un candidato de Vision por separado
```

Con `--compare` el exit code refleja **regresiones contra la baseline**, no
fallas absolutas: lo que decide una actualización no es que el candidato sea
perfecto, sino que no empeore nada. `ADSO_GEMINI_MODEL` overridea `GEMINI_MODEL`
sin tocar código y existe justamente para apuntar el harness a un candidato; en
producción se deja sin setear.

### Dos reglas de diseño que costaron falsos positivos

- **R12 (injection) escanea el frontmatter, `operation`/`params` y `summary` —
  nunca el `body`.** El body es transcripción legítima del input, así que
  cualquier marcador embebido aparece ahí *sin* que el modelo haya obedecido
  nada. R12b cubre el body aparte, buscando frases del propio system prompt (que
  es lo que las inyecciones piden filtrar).
- **R5 (`type`) es regla dura solo cuando `media_type` no es `text`/`audio`.** En
  texto y audio el tipo lo eligen los botones `[Tarea]`/`[Nota]` y el del LLM se
  descarta, así que ahí la regla es informativa.

Además: las reglas de tags (R8-R11) se evalúan sobre el payload **crudo**, antes
de `_validate_capture_payload` — sobre el sanitizado nunca fallarían, y lo que se
quiere medir es el modelo, no el sanitizador.

---

## Grabación de fixtures del LLM

Para mantener los tests deterministas, las respuestas de Gemini se graban como JSON en `tests/fixtures/llm_responses/`. Procedimiento:

1. Ejecutar manualmente una clasificación real contra Gemini API
2. Capturar el JSON de respuesta
3. Guardarlo en `tests/fixtures/llm_responses/` con nombre descriptivo
4. Referenciarlo en el test via `conftest.py` o lectura directa

Si el prompt al LLM cambia significativamente, regenerar las fixtures afectadas. Los tests deben fallar si el schema de respuesta cambia — eso es intencional, fuerza a actualizar fixtures y validar que el cambio es correcto.

---

## Notas

- Todos los tests son **async** (`@pytest.mark.asyncio`) — consistente con el código de producción.
- Los tests **nunca** llaman a APIs externas reales. Si un test hace una request HTTP real, es un bug del test.
- Los tests de filesystem usan `tmp_path` de pytest — se limpian automáticamente.
- ChromaDB en tests usa un directorio temporal — no contamina la DB de producción.
- La suite completa (unit + integration + e2e) corre en ~50 segundos en la RPi4 de desarrollo, y es exactamente lo que corre CI. Son 897 tests: 704 unit, 48 integration, 145 e2e.
- **Test-first es obligatorio** (`CLAUDE.md` § Validación de código): el test se escribe antes que el código. Un cambio que llega sin test se devuelve.
