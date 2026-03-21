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
pytest-tmp-files        # directorios temporales para vault y ChromaDB (o tmp_path built-in)
unittest.mock           # mocks y patches (stdlib, sin deps extra)
```

Todas las dependencias de testing van en un grupo separado (`requirements-dev.txt` o `[project.optional-dependencies.dev]` en `pyproject.toml`).

---

## Estructura de archivos

```
tests/
├── conftest.py                    # fixtures globales
├── fixtures/
│   ├── llm_responses/             # JSONs grabados de Gemini para replay
│   │   ├── classify_text.json
│   │   ├── classify_audio.json
│   │   ├── classify_link.json
│   │   ├── classify_image.json
│   │   ├── classify_document.json
│   │   ├── generate_frontmatter_note.json
│   │   ├── generate_frontmatter_note_academic.json  # note con campos académicos opcionales
│   │   ├── generate_frontmatter_task.json
│   │   ├── generate_frontmatter_idea.json
│   │   ├── disambiguation_response.json  # respuesta con confianza baja en modo
│   │   ├── query_response.json    # respuesta RAG con notas como contexto
│   │   ├── malformed_json.json    # respuesta inválida para test de error handling
│   │   └── empty_response.json    # respuesta vacía para test de modo degradado
│   └── sample_notes/              # notas .md de ejemplo con frontmatter válido
│       ├── note.md
│       ├── note_academic.md          # note con campos académicos (authors, doi, etc.)
│       ├── task.md
│       ├── idea.md
│       ├── inbox_pending.md
│       ├── project_index.md          # _index.md de proyecto con description y sections
│       └── area_index.md             # _index.md de área con description
├── unit/
│   ├── test_frontmatter.py        # generación y validación de YAML
│   ├── test_file_naming.py        # slug, fecha, kebab-case
│   ├── test_config.py             # carga de config.yaml, defaults, merge con env
│   ├── test_classification.py     # parsing del modo (captura/consulta/edición/gestión)
│   ├── test_knowledge_query.py    # parsing de resultados ChromaDB, threshold, dedup
│   ├── test_security.py           # auth middleware: allow, reject, edge cases
│   └── test_vault_search.py       # parsing de wikilinks, tags, frontmatter YAML
├── integration/
│   ├── test_capture_flow.py       # LLM mock → vault_writer → archivo en disco
│   ├── test_degraded_mode.py      # LLM falla → nota en 00-Inbox/pending
│   ├── test_embeddings_pipeline.py # vault_writer → embeddings → ChromaDB
│   ├── test_edit_flow.py          # edición de nota existente → re-index
│   ├── test_rename_flow.py        # renombrado → backlinks actualizados → ChromaDB path actualizado
│   ├── test_git_backup.py         # debounce, commit messages, push failures
│   ├── test_vault_search_integration.py  # backlinks y filtros contra vault temporal
│   └── test_calendar_sync.py      # mock Google API → parsing de eventos
├── e2e/
│   ├── test_capture_message.py    # Update simulado → respuesta + vault escrito
│   ├── test_query_message.py      # Update simulado → RAG → respuesta con notas
│   ├── test_task_creation.py      # Update simulado → task creada → Google Tasks + Calendar si tiene fecha
│   ├── test_confirmation_flow.py  # Update → preview → confirm/reject → resultado
└── README.md                      # instrucciones para correr tests (opcional)
```

---

## Niveles de testing

### Unit tests

Testean funciones puras sin I/O externo. Son el grueso de la suite.

#### `test_frontmatter.py`

Qué se testea:
- Generación de frontmatter válido por cada tipo (`note`, `task`, `idea`, `inbox`, `project-index`, `area-index`)
- Campos base siempre presentes: `title`, `date_created`, `date_modified`, `type`, `tags`, `source`, `media_type`, `status`
- Campos académicos opcionales en `note` (`authors`, `year`, `doi`, `methods`, etc.) presentes solo cuando aplica
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
- Cada modo reconocido: `captura`, `consulta`, `edición`, `gestión`
- JSON malformado del LLM → error manejable, no excepción sin capturar
- Campos faltantes en la respuesta → defaults razonables o error explícito

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

#### `test_embeddings_pipeline.py`

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

#### `test_edit_flow.py`

Setup:
- Vault temporal con nota pre-existente
- Mock de `llm_client`

Qué se testea:
- Edición de nota existente → `date_modified` actualizado, `date_created` intacto
- Contenido anterior preservado si la edición es parcial (append)
- Re-indexado en ChromaDB post-edición

#### `test_rename_flow.py`

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

#### `test_calendar_sync.py`

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

#### `test_query_message.py`

Qué se testea:
- "qué tengo sobre X" → búsqueda semántica en ChromaDB → respuesta con notas relevantes
- Query sin resultados → mensaje claro "No encontré nada relevante sobre X"
- Query con scope seleccionado via inline keyboard → busca en proyecto, luego ofrece ampliar
- Expansión desde nodo: "todo lo relacionado con [[nota]]" → backlinks + vecinos semánticos
- Query mixta: filtro estructural + semántico ("papers pendientes sobre ML")
- Resultado corto (≤ 3 notas) → respuesta inline con botón `[Informe .md]`
- Resultado largo → bot envía archivo `.md` con links `obsidian://`

#### `test_task_creation.py`

Qué se testea:
- Mensaje clasificado como task → task creada en vault y Google Tasks mock
- Task con `scheduled` (fecha/hora) → evento en Calendar ADSO mock
- Task con `due_date` (solo fecha) → chip de fecha en Google Tasks
- Campo `notes` de Google Tasks contiene: descripción + subtareas como bullets + links `obsidian://`
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
    """Mock de llm_client con respuestas default."""
    client = AsyncMock()
    # Respuesta default: note clasificada
    client.classify.return_value = {
        "mode": "captura",
        "type": "note",
        "project": "tesis",
        "section": "experimentos",
        ...
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
  include:
    - notes_created
    - active_project
    - new_methods
    - paper_queue
    - stale_ideas
    - tasks_review
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
| `knowledge_query.py` | ≥ 75% | Solo lectura, no destructivo. |
| `calendar_client.py` | ≥ 80% | Escribe a Calendar/Tasks externo. |
| `tasks_client.py` | ≥ 80% | Escribe a Google Tasks externo. |
| `transcriber.py` | ≥ 70% | Wrapper de faster-whisper, poco código propio. |
| `bot.py` | ≥ 70% | Handlers + inline keyboard callbacks. Lo cubre e2e. |

**Target global: ≥ 80%.**

### Qué NO se mide

- Código de terceros (`python-telegram-bot`, `chromadb`, `faster-whisper`)
- Archivos de configuración y fixtures
- `__init__.py` vacíos

---

## Cómo correr los tests

```bash
# Todos los tests
pytest tests/ -v

# Solo unit tests (rápidos, < 5 segundos)
pytest tests/unit/ -v

# Solo integration
pytest tests/integration/ -v

# Solo e2e
pytest tests/e2e/ -v

# Con cobertura
pytest tests/ --cov=adso --cov-report=term-missing

# Con cobertura y reporte HTML
pytest tests/ --cov=adso --cov-report=html
```

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
- La suite completa debe correr en **< 30 segundos** en una máquina de desarrollo. Si se pasa, hay un test que está haciendo algo que no debería.
