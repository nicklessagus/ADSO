# CLAUDE.md — ADSO

Instrucciones para Claude Code al trabajar en este repositorio.

---

## Proyecto

**ADSO** (*Autonomous Data Structuring Orchestrator*) es un bot de Telegram personal escrito en Python que actúa como escriba, observador y clasificador del conocimiento: captura información no estructurada, la clasifica mediante LLMs, la persiste como notas Markdown en un vault de Obsidian y permite recuperarla mediante consultas en lenguaje natural.

Documentación completa en `docs/`.

---

## Setup de desarrollo

```bash
git clone git@github.com:nicklessagus/ADSO.git
cd ADSO
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# pytest necesita env vars dummy (security.py las valida en import time)
export TELEGRAM_ALLOWED_USER_ID=12345 TELEGRAM_TOKEN=dummy GEMINI_API_KEY=dummy
pytest
```

Requiere Python ≥ 3.11. No hay dependencias nativas — venv estándar alcanza, no necesita conda.

Para correr el bot (no solo tests), crear `.env` y `config.yaml`:

```bash
# .env
TELEGRAM_TOKEN=...
TELEGRAM_ALLOWED_USER_ID=...
GEMINI_API_KEY=...
VAULT_PATH=/path/al/vault

# Arrancar
python -m adso
```

Para correr con Docker (producción / RPi4):

```bash
docker compose up --build
```

---

## Infraestructura de despliegue

- **Hardware:** Raspberry Pi 4, 4GB RAM, ARM64
- **Entorno:** Docker + docker-compose
- **Lenguaje:** Python ≥ 3.11 (dev y Docker), implementación asíncrona
- **Vault:** Markdown en filesystem local (Syncthing para sync en vivo + Git para backup/DR — ver `docs/architecture.md`)
- **Health check:** `heartbeat_job` toca `/tmp/adso_heartbeat` cada 60s. Docker verifica que el archivo tenga menos de 2 minutos (`CMD-SHELL test -n "$(find /tmp/adso_heartbeat -mmin -2)"` en compose — `find` solo devuelve exit≠0 si el path no existe, así que sin el `test -n` un heartbeat congelado nunca se detectaba; el `HEALTHCHECK` del Dockerfile usa aritmética con `date`, equivalente); 3 fallos consecutivos → `unhealthy`. `start_period: 30s` para absorber el arranque.

Toda propuesta de implementación debe evaluarse contra las restricciones de CPU y RAM de la RPi4. Mencionar explícitamente el impacto estimado en recursos.

---

## Stack

| Componente | Tecnología |
|---|---|
| Bot | `python-telegram-bot[job-queue]` v21+ (async) |
| LLM primario | Gemini API — modelo `gemini-3.5-flash-lite` (línea flash-lite estable; free tier jul-2026: ~1.000-1.500 RPD, 15 RPM, 250k TPM — Google ya no publica el cap del free tier en la doc, verificarlo por proyecto en AI Studio). Clasificación y síntesis de reportes |
| LLM de Vision | `gemini-3.6-flash` (`GEMINI_VISION_MODEL`) — solo OCR/descripción de imágenes y PDFs escaneados. Constante separada porque la quota del free tier es **por modelo**: rasterizar un PDF de 20 páginas no debe consumir RPD del bucket de la captura diaria |
| LLM fallback | Groq — `llama-3.1-8b-instant` (sin schema constrained; post-validado). `ANTHROPIC_API_KEY` se lee en config pero no hay código que la use aún |
| Embeddings | Gemini Embedding API (remoto, no local) |
| Vector DB | ChromaDB embebido |
| Transcripción | `faster-whisper` (modelo `tiny` o `base`) |
| Extracción web | Gemini nativo |
| Extracción PDF | `pymupdf` (texto + metadata) — detección heurística de papers + extracción local de secciones (abstract, keywords, métodos, conclusiones); preview muestra título + abstract + keywords para papers, texto crudo para genéricos |
| Calendar | Google Calendar API v3 *(diferido — Fase 6; diseño: lectura de todos los calendarios, escritura y borrado solo en calendario `ADSO` dedicado)* |
| Tasks | Google Tasks API — lista `ADSO` dedicada (escritura/borrado) + lectura de listas externas |
| Vault | Markdown + YAML Frontmatter en filesystem |
| Backup vault | Repo git privado en GitHub — push automático con debounce configurable (`backup.debounce_seconds`) |

---

## Estructura de módulos

```
adso/
├── bot.py                  # Bootstrap de la aplicación PTB y registro de handlers — la lógica vive en handlers/
├── handlers/
│   ├── commands.py         # /start /help /status /reset /clasificar
│   ├── input.py            # Entrada de mensajes: texto, audio, imagen, documento, links
│   ├── capture.py          # Flujo de captura: clasificación, preview, corrección, confirmación
│   ├── callbacks.py        # Callbacks de inline keyboards
│   ├── manage.py           # Gestión: crear/archivar/renombrar proyectos y áreas
│   ├── query.py            # /buscar — retrieval semántico (Fase 7.0)
│   ├── reports.py          # /reporte y /reporte_full — flujo interactivo
│   └── jobs.py             # Crons: reclassify_inbox, reindex nocturno, heartbeat (el reporte semanal está configurado pero aún sin job — ver docs/improvements-2026-07.md §2.2)
├── keyboards.py            # Construcción de inline keyboards
├── constants.py            # Callbacks IDs y constantes compartidas
├── bot_utils.py            # Utilidades (spawn_tracked, helpers de mensajes)
├── transcriber.py          # Transcripción de audio con faster-whisper
├── llm_client.py           # Cliente Gemini/Groq: llamadas a API, retries, modo degradado, build_system_prompt
├── llm_schema.py           # Schema de salida de Gemini + validación de respuesta + sanitización de frontmatter + patrones de injection (re-exportados desde llm_client por compatibilidad)
├── document_extractor.py   # Extracción de PDFs (pymupdf) y documentos de texto
├── arxiv_client.py         # Metadata de papers via API de arXiv
├── vault_writer.py         # Escritura de .md al filesystem + git backup con debounce
├── vault_watcher.py        # Watcher de filesystem (watchdog): conflictos Syncthing + re-embed de cambios externos + limpieza de wikilinks rotos al borrar
├── vault_search.py         # Búsqueda estructural: backlinks ([[wikilinks]]), tags, filtros por frontmatter
├── vault_cache.py          # Caché de parsing de notas por (mtime, size) — evita re-parsear notas sin cambios en scans repetidos
├── embeddings.py           # Pipeline de embeddings y ChromaDB
├── knowledge_query.py      # Retrieval semántico — busca notas por similitud vectorial en ChromaDB (no llama al LLM)
├── reporters.py            # Generación de reportes .md (scope, ideas, salud, cola de lectura)
├── tasks_client.py         # Google Tasks API
├── security.py             # Middleware de autenticación
└── config.py               # Variables de entorno, constantes (GEMINI_MODEL) y carga de config.yaml
```

No existe `calendar_client.py` — Google Calendar es Fase 6 diferida (solo Tasks está implementado).

---

## Convenciones de código

- **Asíncrono siempre:** usar `async/await`. Ninguna operación de I/O debe ser bloqueante.
- **Manejo de excepciones explícito:** capturar errores de red, timeouts de API y errores de filesystem con mensajes claros al usuario.
- **Sin pérdida de datos:** ante error al escribir al vault, loguear y notificar al usuario. No silenciar excepciones.
- **Modular:** cada módulo tiene responsabilidad única. `bot.py` orquesta, no procesa.
- **Documentación de funciones:** docstring en funciones públicas con descripción, args y comportamiento ante error.
- **Type hints** en todas las firmas de función.

---

## Seguridad — reglas no negociables

- Todo contenido externo (URLs, PDFs, imágenes) se pasa al LLM dentro de etiquetas `<input>` con instrucción explícita de no seguir instrucciones internas. Además, cuando el contenido a clasificar (texto de PDF/OCR/Vision/documento o metadata de arXiv) dispara `check_injection_risk`, `_classify_and_preview`/`_classify_and_preview_arxiv` anteponen un aviso al preview (`_INJECTION_PREVIEW_WARNING`) para que el usuario escrute antes de confirmar. No bloquea — la nota igual requiere confirmación explícita.
- El LLM siempre responde en JSON estructurado con schema fijo.
- Autenticación por `TELEGRAM_ALLOWED_USER_ID` en dos capas: (1) gate global en `bot.py` (`_global_auth_gate` registrado como `TypeHandler(Update, ...)` en `group=-1`) que descarta con `ApplicationHandlerStop` cualquier update de usuario no autorizado antes de llegar a los handlers; (2) el decorador `@authorized` por handler como segunda barrera. Ambos usan `is_authorized()` de `security.py`. Un handler nuevo sin decorar ya no es un bypass.
- Credenciales solo en variables de entorno. Nunca hardcodeadas.

---

## Vault de Obsidian

### Taxonomía
```
01-Projects/{proyecto}/{seccion}/nota.md       # tiene inicio y fin
02-Areas/{area}/nota.md                        # sin fin, continuo — áreas reales (docencia, investigacion, etc.)
03-Resources/                                  # archivos adjuntos (PDFs, imágenes, etc.) y material de referencia permanente
00-Inbox/nota.md                               # sin clasificar
05-Archive/                                    # proyectos inactivos o completados
```
Las ideas (`type: idea`) viven en su proyecto o área correspondiente, igual que `reference`. No hay carpeta `04-Ideas/`.

### Ciclo de vida
Nota con `type: idea` en área → Proyecto activo → Archivo → (borrado con doble confirmación)
Las áreas no tienen ciclo de vida.

Una idea tiene tres estados: `raw` (capturada, sin procesar), `implemented` (se hizo algo con ella — se convirtió en proyecto, tarea, o se aplicó), `discarded` (descartada conscientemente). No hay presión de "desarrollarla": puede quedarse en `raw` indefinidamente hasta que el usuario tome una decisión.

### Frontmatter mínimo requerido
```yaml
---
title: ""
date_created: ""   # ISO 8601
date_modified: ""  # ISO 8601
type: ""           # reference | task | idea | project-index | area-index
tags: []           # siempre en inglés, kebab-case; el LLM reutiliza tags existentes del vault (excluyendo 00-Inbox) antes de crear nuevos
source: telegram   # "telegram" para notas de usuario, "system" para auto-generadas
media_type: ""     # text | audio | image | link | document — automático
status: active     # valores dependen del type — ver docs/frontmatter-schema.md
---
```

Los tipos `project-index` y `area-index` se generan automáticamente al crear proyecto/área (no por clasificación del LLM). Ambos requieren `description` — el bot la pide obligatoriamente en la creación. Schema completo en `docs/frontmatter-schema.md`.

### Regla de confirmación
Ninguna nota se escribe al vault sin confirmación explícita del usuario. El bot muestra un preview del frontmatter y los links sugeridos, y el usuario confirma con inline keyboard.

- **Notas y tareas** (`reference`, `idea`, `task`): primera fila `[Cancelar]` `[Corregir]` `[Reubicar]`, segunda fila `[Confirmar]`. El texto libre está bloqueado hasta que el usuario apriete `[Corregir]` (activa modo corrección con lock). Durante el lock solo se acepta texto plano — audio, archivos y `/comandos` quedan bloqueados. La corrección puede ajustar título, prioridad, tags y tipo; para tareas también fecha límite en lenguaje natural.

**Prefijos válidos en modo corrección** (`_handle_text_correction` en `capture.py`):
- `titulo <texto>` / `título <texto>` — reemplaza el título
- `prioridad alta|media|baja` — cambia prioridad
- `tag <nombre>` / `agregar tag <nombre>` — añade un tag
- `tipo reference|task|idea` — cambia el tipo
- Sin prefijo y texto ≤ 200 chars sin saltos de línea → se usa como nuevo título (fallback)
- Sin prefijo y texto largo o multi-línea → se rechaza con mensaje de ayuda (`error_msg_id` guardado en `pending`); el lock se mantiene activo para que el usuario reintente. Cuando la siguiente corrección es válida, ese mensaje de error se borra junto con el mensaje del usuario, quedando solo el preview actualizado.

**Failsafe:** `/reset` cancela cualquier operación pendiente y limpia todo el estado (`pending_note`, `pending_capture_ctx`, `block_msg_ids`, etc.). Funciona en cualquier momento, sin confirmación. Implementado en `handle_reset` (`commands.py`).

`[Reubicar]` cambia únicamente el destino (`[Elegir área]` `[Elegir proyecto]` `[Inbox]`) en ambos tipos.

### Prioridad y fecha inferidas
El LLM infiere `priority` y `due_date` del lenguaje del mensaje para tareas. `priority` se usa tal como la devuelve el LLM; si no hay señal, usa `medium`.

`due_date` se resuelve en dos pasos: el LLM propone una fecha (con el prompt incluyendo la fecha UTC actual en inglés y español), pero luego `_classify_and_preview` corre `_parse_date_from_text()` sobre el texto original y overridea el resultado si encuentra una expresión válida. El parser local es determinístico y más fiable que el LLM para expresiones relativas en español ("el viernes", "mañana", "el próximo lunes"). El LLM tiene problemas con la aritmética de días de la semana, especialmente cuando el UTC y la zona horaria del usuario difieren.

`_parse_date_from_text()` computa "ahora" en la zona horaria del usuario para evitar off-by-one en días de semana cerca de medianoche local. `_user_tz()` resuelve la zona en orden `ADSO_TIMEZONE` → `TZ` (docker-compose ya la setea a `America/Argentina/Buenos_Aires`) → UTC. Requiere el paquete `tzdata` (en `requirements.txt`/`pyproject.toml`) para que `zoneinfo` resuelva nombres IANA en la imagen `python:3.11-slim` (que no trae la base de datos de zonas del sistema). Acepta un parámetro `now` inyectable para tests. Valida el rango de hora/minuto (`0≤h≤23`, `0≤m≤59`) y descarta la hora si está fuera de rango en vez de lanzar `ValueError`. Los matches relativos ("mañana", "hoy", "pasado mañana") usan límites de palabra (`\b`) para no matchear dentro de otras palabras.

Ambos campos aparecen en el preview y el usuario puede corregirlos con `[Corregir]`.

**Sanitización del frontmatter LLM** (`_validate_capture_payload` en `llm_schema.py`):
- **Whitelist de claves (`ALLOWED_FRONTMATTER_KEYS`):** solo sobreviven las claves documentadas en `docs/frontmatter-schema.md` (base + destino + tareas + académicas + `description`/`sections` de los índices). Cualquier otra se descarta con log a `warning`. Cierra el vector por el que el fallback de Groq (sin schema constrained) o una prompt injection en un PDF/OCR podían meter claves arbitrarias — en particular `handler` y `content`, que corrompían el archivo al escribirlo (ver `_build_post` abajo). Se aplica antes de que `extra_fm`/`user_context` se inyecten en `capture.py`, así que esos campos no se ven afectados.
- **Título (`_clean_title`):** se coacciona a string (`title: null` o un número del fallback de Groq ya no lanzan `TypeError`) y se le stripean heading markers de markdown (`#`, `##`) y prefijos label (`Tarea:`, `Task:`, `Nota:`, `Recordar:`). El regex se aplica **en bucle** hasta que el título no cambia: ambas alternativas están ancladas en `^`, así que `"## Tarea: X"` necesita dos pasadas.
- **Tags:** un string suelto (`"python, ml"` — típico de Groq) se parte por comas antes de normalizar; cualquier otro tipo inesperado cae a `[]`. Se filtran días de la semana (lunes…domingo, monday…sunday) y expresiones temporales (hoy, mañana, proxima-semana) que no son etiquetas semánticas útiles. También se filtran tags que duplican el `type` (task, note, idea, etc.).
- **Tipos coaccionados (defensa contra respuestas del LLM, sobre todo el fallback de Groq sin schema):** `confidence` se fuerza a float en `[0,1]` (default 0.5 si no es numérico) en `validate_llm_response` — evita `TypeError` en la comparación con el umbral de desambiguación. `year` se coacciona a `int` o se descarta. `authors`/`keywords` se fuerzan a lista de strings (un string suelto se parte por comas; otro tipo → `None`). `read_status` se valida contra `{read, unread}` (`VALID_READ_STATUS`) o se descarta. `type`/`status`/`priority` se normalizan con `_norm_enum()` (str + strip + lower) antes de validarse: `"Task"`/`"Pending"`/`"High"` (capitalización habitual de llama-3.1-8b) ya no tiran toda la respuesta a modo degradado, y un valor no hasheable (dict/list) en `status` produce `LLMResponseError` en vez de `TypeError`. `body` ausente **o `null`** (el schema de Gemini lo permite) se normaliza a `""` — si no, el preview reventaba con `AttributeError`.

---

## Tono y estilo de mensajes

Los mensajes que el bot envía al usuario por Telegram usan **infinitivo impersonal**.

- ✅ `Confirmar, corregir o cancelar.`
- ✅ `Usar /clasificar para continuar.`
- ✅ `¿Guardar como tarea o como nota?`
- ❌ voseo: `Confirmá`, `Mandá`, `Podés`
- ❌ primera persona del bot: `Puedo`, `No pude`
- ❌ ustedeo: `Confirme`, `Cancele`

---

## Modelo de interacción

El bot funciona en un único chat de Telegram. No hay estado de contexto persistente. Toda la interacción se basa en **lenguaje natural + inline keyboards**.

### Estado default: captura
El usuario manda contenido (texto, audio, link, imagen, documento). Para texto y audio el bot pregunta primero `[Tarea]` o `[Nota]` — el LLM nunca decide el type en estos casos. Para PDFs, imágenes y links el type se infiere del contenido. El bot propone clasificación y el usuario confirma, edita o cancela con inline keyboards.

### Estado transiente: consulta
El usuario pregunta algo sobre el vault. El bot resuelve la consulta, devuelve el resultado (inline o como archivo `.md` con links `obsidian://`) y vuelve al estado default.

### Inline keyboards
Los botones son el mecanismo principal de interacción después del lenguaje natural:

| Momento | Botones |
|---|---|
| **Texto / audio recibido** | fila 1: `[Cancelar]` `[Tarea]` `[Nota]` — el usuario elige el tipo; el LLM infiere el resto. fila 2: `[🔎 Buscar en el vault]` — busca ese texto (retrieval semántico) en vez de guardarlo (`CB_DISAMBIG_QUERY`) |
| **PDF recibido** | `[Cancelar]` `[Ya lo leí]` `[Lo quiero leer]` — setea `read_status` en frontmatter |
| **Imagen recibida** | `[OCR]` `[Gemini Vision]` `[Describir]` `[Cancelar]` |
| **Resultado OCR** | `[Cancelar]` `[Corregir]` / `[Gemini Vision]` `[Confirmar]` — dos filas; Gemini Vision descarta el OCR y reprocesa |
| **Resultado Gemini Vision** | `[Cancelar]` `[Corregir]` `[Confirmar]` |
| **Audio transcripto** | `[Cancelar]` `[Corregir]` `[Confirmar]` → al confirmar: `[Cancelar]` `[Tarea]` `[Nota]` |
| **Captura nota o tarea** | `[Cancelar]` `[Corregir]` `[Reubicar]` / `[Confirmar]` — dos filas; igual para notas y tareas, con o sin destino |
| **Reubicar destino** | `[Elegir área]` `[Elegir proyecto]` `[Inbox]` |
| **Consulta** (si falta scope) | `[Todo]` `[Proyecto1]` `[Proyecto2]` ... |
| **Resultado de consulta** | `[Ver referencias completas]` `[Generar informe .md]` |
| **Expansión desde nodo** | `[Solo relaciones directas]` `[Expandir un grado más]` |
| **Desambiguación** (modo incierto) | `[Guardar como nota]` `[Buscar en vault]` *(Fase 7)* |
| **Fallback OCR sin texto** | `[Gemini Vision]` / `[Cancelar]` `[Describir]` — OCR no encontró texto, sin botón OCR |
| **`/reporte` — tipo** | `[Proyecto/Área/Inbox]` `[Ideas]` / `[Salud del vault]` `[Cola de lectura]` / `[Cancelar]` — tres filas |
| **`/reporte` — categoría** | `[Proyectos]` `[Áreas]` / `[Cancelar]` `[Inbox\|Todas\|Toda la cola]` — dos filas |
| **`/reporte` — lista de items** | botones de items en pares / `[Cancelar]` `[← Volver]` — última fila fija |

**Failsafe global:** `/reset` cancela cualquier estado pendiente (teclados, correcciones, capturas) y vuelve al estado inicial. Funciona siempre, sin confirmación.

### Desambiguación de intención
Si el LLM no tiene confianza alta en el modo, el bot pregunta con botones en vez de asumir. `[Buscar en vault]` ejecuta el retrieval semántico real (Fase 7.0, mismo pipeline que `/buscar` via `CB_DISAMBIG_QUERY`). Al buscar desde un teclado, `run_query` recibe el mensaje de los botones como `keyboard_msg` y lo edita como mensaje de estado — el teclado se retira, como en cualquier otro callback (si la edición falla por mensaje viejo, cae a un mensaje nuevo).

El LLM no usa `mode=query` ni `mode=edit` (removidos del prompt hasta Fase 7). Todo input que no sea `manage` se clasifica como `capture`. Si el LLM devuelve `query` o `edit` de todas formas, `_redirect_unimplemented_mode()` (`capture.py`) los redirige a `capture` **re-validando el payload** con `_validate_capture_payload` — `validate_llm_response` no sanitiza los payloads de esos modos, así que sin esa pasada el frontmatter llegaba crudo al vault. Si el payload no se puede sanear (tipo inválido, `frontmatter: null` — legal en el schema de Gemini), cae a modo degradado (`make_degraded_result()` en `llm_client.py`): Inbox + `status: pending-classification`.

### Consultas con refinamiento de scope
El patrón es: el LLM interpreta lo que pueda del lenguaje natural y los botones cubren lo que falta. Si el usuario ya especificó el scope ("papers pendientes de tesis"), el bot responde directo. Si no ("dame todo lo que tengo que hacer"), el bot ofrece botones para elegir scope (toda la bóveda, uno o más proyectos).

### Output de consultas
Formato de cada ítem (igual en inline y en informe): título, estado/área, snippet de contenido, link `obsidian://`.

- **Resultados cortos** (2-3 ítems): inline + botones `[Ver referencias completas]` `[Generar informe .md]`.
- **Resultados largos o expansión desde nodo**: informe `.md` enviado como documento en Telegram. Incluye header con logo ASCII + versión de ADSO + fecha, síntesis LLM (si aplica), todas las notas con snippet + link, sección de relaciones si se expandió.
- **RAG** (Fase 7): síntesis inline primero, notas fuente con links, botones para profundizar.
- **Expansión desde nodo**: bot pregunta `[Solo relaciones directas]` `[Expandir un grado más]` antes de generar el informe. Usa backlinks + outgoing links + ChromaDB en paralelo.

Todos los informes `.md` tienen header estándar con logo ASCII, versión y fecha. Se asume Obsidian instalado y sincronizado.

---

## Modos de operación

El LLM clasifica cada mensaje en uno de estos modos antes de procesarlo:

| Modo | Ejemplos |
|---|---|
| **Captura** | Texto, audio, link, imagen, PDF con contenido a guardar |
| **Consulta** | "qué tengo sobre X", "mostrá relaciones", "todo pendiente" |
| **Edición** | "actualizá la nota X" (solo `reference` e `idea`) |
| **Gestión** | Crear proyecto, archivar, renombrar |

No hay modo Agenda — el agendamiento se maneja via tasks con `due_date` (chip en Calendar) o `scheduled` (evento en Calendar ADSO). Las tasks no se editan via ADSO.

**El bot es un sistema de retrieval, no de razonamiento.** En modo consulta, recupera y presenta notas relevantes del vault. No agrega conocimiento propio ni opina sobre el contenido.

Acciones destructivas (archivar, borrar, renombrar) siempre requieren confirmación explícita.

---

## Embeddings

- Se calculan via **Gemini Embedding API** (nunca localmente).
- Se almacenan en **ChromaDB** en `/app/data/chroma/`.
- Se generan de forma asíncrona inmediatamente después de confirmar una nota.
- Umbral de similitud para sugerir links: `links.similarity_threshold` en `config.yaml` (default: `0.82`).
- Umbral de similitud para consultas RAG: `rag.similarity_threshold` en `config.yaml` (default: `0.75`).
- Carpetas excluidas del índice: `vault.exclude_dirs` en `config.yaml`.

`config.yaml` debe existir siempre; si falta, el bot falla con error claro al arrancar.

Las áreas y proyectos pueden sembrarse opcionalmente desde `config.yaml` en el primer arranque, y luego se gestionan via el bot.

---

## Fases de desarrollo

| Fase | Funcionalidad | Estado |
|---|---|---|
| 1 | Captura de texto, clasificación, confirmación, escritura al vault + búsqueda estructural (backlinks, tags, frontmatter) | ✅ |
| 2 | Indexado del vault + links automáticos (embeddings + ChromaDB) | ✅ |
| 3 | Audio (faster-whisper) + PDFs (pymupdf) + documentos de texto | ✅ |
| 4 | Imágenes y capturas (OCR + Gemini Vision) | ✅ |
| 5 | Integraciones externas (arXiv) | ✅ |
| 6 | Google Calendar + Google Tasks | 🔄 parcial — Tasks implementado; Calendar diferido |
| 7 | Consultas RAG en lenguaje natural | 🔄 parcial — 7.0 retrieval puro (`/buscar`) implementado; scope/expansión/síntesis pendientes. Diseño en `docs/fase7-rag-design.md` |
| 8 | Análisis del vault: reportes a pedido (scope, ideas, salud, cola de lectura), scoring de papers, detección de gaps | 🔄 parcial — reportes implementados |

### Fase 5 — arXiv

Cuando el usuario manda un link de arxiv.org, el bot lo detecta por dominio y usa la **API de arXiv** (no scraping) para extraer metadata literal: título, autores, año, abstract, DOI, keywords. La nota resultante tiene el mismo formato que un paper subido como PDF:

- **Frontmatter:** campos académicos (`authors`, `year`, `doi`, `keywords`, `read_status`). Todos vienen literales de la API — el LLM no los inventa. El LLM solo aporta proyecto, área, tags y summary.
- **Body:** `> [!summary] AI Summary` (del campo `summary` del LLM, resumen breve en español) + `## Abstract` (texto literal de la API) + `## Personal Notes`. Se usa el campo `summary` y **no** `body` del LLM porque `body` contiene el documento completo con callout + secciones — usarlo causaría duplicación del abstract.
- **`source_url`:** apunta a arxiv.org sin versión (ej: `https://arxiv.org/abs/2301.12345`). No se descarga el PDF.
- **`media_type`:** `link`.

El flujo de confirmación es idéntico al de cualquier captura: preview → `[Confirmar]` `[Reubicar]` `[Cancelar]`.

La detección de arXiv ocurre en `handle_text()`, antes del flujo genérico. Soporta URLs `abs/`, `pdf/`, con o sin versión (`v2`), y formato antiguo (`hep-ph/XXXXXXX`). Si la API de arXiv falla, el bot ofrece guardar el link como nota genérica.

**Detección de duplicados:** antes de mostrar el preview, se busca en todo el vault (excluido Archive) si ya existe una nota con el mismo `source_url` o el mismo `doi`. Si se encuentra, se muestra la ruta del archivo existente y un teclado `[Cancelar]` `[Crear igual]`. `[Crear igual]` retoma el flujo normal sin restricciones de destino. La búsqueda por `doi` permite detectar papers subidos previamente como PDF. Diseño: un paper = una nota (los demás proyectos que lo necesiten lo referencian via wikilink).

---

## Ideas futuras (post Fase 8)

Capacidades exploratorias que dependen de tener un vault maduro con suficientes notas y embeddings.

- **Clustering de temas emergentes:** UMAP + HDBSCAN sobre embeddings de ChromaDB, etiquetado por LLM. Viable en RPi4.
- **Transferencia de métodos entre proyectos:** cruzar `methods` de papers entre proyectos para detectar técnicas aplicables que no se están usando.
- **Red de citas interna:** campo `cites` en papers, análisis tipo PageRank para identificar papers fundacionales y gaps de lectura.
- **Análisis temporal:** evolución de temas y métodos en el vault a lo largo del tiempo. Detección de frentes de investigación activos.
- **Detección de conocimiento obsoleto:** trackear `last_retrieved` por nota — las que nunca aparecen en resultados RAG ni tienen links son candidatas a revisión.
- **Generación de Canvas:** crear archivos `.canvas` (JSON) automáticamente desde clusters de embeddings, posicionando notas similares cerca.
- **Bibliografía anotada on-demand:** generar un documento consolidado con papers de un proyecto, agrupados por método o tema, con `relevance`, `contribution` y `conclusions`.
- **NASA ADS:** integración de cuenta para importar colecciones/listas de papers en bloque o por sync periódico. No es flujo de captura individual — requiere OAuth o API key de ADS y un mecanismo de reconciliación con el vault (evitar duplicados por DOI/arXiv ID).
- **Reportes en PDF:** exportar `/reporte` y `/reporte_full` como PDF además de `.md`. Candidato: `fpdf2` (puro Python, sin deps del sistema, ARM64 nativo). Implementación: un helper `_build_pdf(md_content)` que parsea el MD generado y lo renderiza con fpdf2; los reporters reciben `fmt: str = "md" | "pdf"`. Alternativa más simple pero output básico: `markdown` lib → HTML → `xhtml2pdf`. Pendiente de prueba en RPi4.
- **Reintento de git push en heartbeat:** si un push falla (error de red, etc.), los commits quedan locales hasta la próxima actividad. Para garantía extra: el `heartbeat_job` podría revisar si `repo.head.commit` tiene commits sin pushear (`repo.iter_commits('origin/main..HEAD')`) y hacer push silencioso. Solo notificar si también falla ese reintento.

---

## Validación de código

- Todo el código generado es validado con **OpenAI Codex** antes de incorporarse al repositorio.
- Estrategia de testing completa en `docs/testing.md`: unit, integration y e2e con cobertura ≥ 70% (gate de CI sobre todo `adso/` menos el bootstrap `bot.py`/`__main__.py`; actual 74%).
- **Los markers `integration`/`e2e` se asignan solos** por directorio, en un hook de `tests/conftest.py`. No escribirlos a mano en los tests. CI corre la suite completa (623 tests) — ningún test toca la red.
- `adso/handlers/*` **está en la medición de cobertura**. No volver a ponerlo en el `omit` de `pyproject.toml`: los e2e sí lo ejercitan, y omitirlo hacía que un test nuevo sobre un handler no moviera el gate (I3 en `docs/audit-2026-07-31.md`).

### Test-first — obligatorio

Ninguna funcionalidad nueva ni fix entra sin test que lo cubra, y **el test se escribe antes que el código**. El orden no es negociable:

1. **Planificar** la modificación: qué cambia, en qué módulo, qué invariante preserva.
2. **Escribir el test** de unidad que la especifique (y el de cobertura del path nuevo). Correrlo para verlo fallar.
3. **Implementar** el cambio.
4. **Correr el test** y verificar que pasa, junto con la suite completa.

Escribir el test después produce tests que confirman lo implementado en vez de especificar el comportamiento buscado. Un cambio que llega sin test se devuelve al paso 2.

### Harness de regresión de modelo

Antes de tocar `GEMINI_MODEL` hay que correr `scripts/llm_regression.py`, que verifica contra la API real que el modelo respete el **contrato estructural** que el resto del bot asume. No mide calidad de redacción ni de resumen: eso lo valida el usuario en el preview antes de confirmar cada nota. Mide lo que el usuario no ve — sobre todo que `validate_llm_response` no lance (si lanza, *toda* captura cae a modo degradado) y que el modelo no obedezca prompt injection.

**No es un test de pytest a propósito:** pega contra la API y quema quota, así que vive en `scripts/` para que un `pytest` local o un cambio en CI no lo dispare por accidente. Los datos (`cases.yaml`) y las baselines viven en `tests/llm_regression/`. Reglas completas en `tests/llm_regression/README.md`.

```bash
make llm-baseline                                        # baseline del modelo actual
make llm-check MODEL=gemini-3.7-flash BASE=gemini-3.5-flash-lite
# --vision-model evalúa un candidato de Vision por separado
```

`ADSO_GEMINI_MODEL` overridea `GEMINI_MODEL` sin tocar código — existe para que el harness apunte a un candidato; en producción se deja sin setear. Con `--compare` el exit code refleja **regresiones contra la baseline**, no fallas absolutas: lo que decide una actualización no es que el candidato sea perfecto, sino que no empeore nada. Baseline de `gemini-3.5-flash-lite` (ago-2026): 34/34, p50 1.5s.

Dos detalles de diseño que costaron falsos positivos: R12 (injection) escanea frontmatter/`operation`/`params`/`summary` pero **nunca el `body`**, porque el body es transcripción legítima del input y cualquier marcador embebido aparece ahí sin que el modelo obedezca nada; y R5 (`type`) solo es regla dura cuando `media_type` no es `text`/`audio`, porque en texto y audio el type lo eligen los botones `[Tarea]`/`[Nota]` y el del LLM se descarta.

---

## Variables de entorno

```bash
# Requeridas
TELEGRAM_TOKEN
TELEGRAM_ALLOWED_USER_ID
GEMINI_API_KEY
GROQ_API_KEY               # fallback LLM cuando Gemini no responde; sin esta key el bot funciona pero sin fallback

# Opcionales
ANTHROPIC_API_KEY          # LLM secundario alternativo
LOG_LEVEL                  # DEBUG | INFO | WARNING | ERROR — default: INFO
ADSO_GEMINI_MODEL          # overridea GEMINI_MODEL sin tocar código. Para el harness de
                           # regresión (scripts/llm_regression.py); en producción sin setear.
ADSO_GEMINI_VISION_MODEL   # ídem para GEMINI_VISION_MODEL.
ADSO_TIMEZONE              # zona horaria IANA para parsear fechas relativas ("el viernes",
                           # "mañana"). Ej: America/Argentina/Buenos_Aires. Override explícito;
                           # si falta, se usa TZ (que docker-compose ya define) y luego UTC.
                           # Sin zona correcta, los días de semana cerca de medianoche pueden
                           # resolverse con off-by-one respecto a la hora local.

# Paths (defaults para Docker)
VAULT_PATH                 # default: /vault
CHROMA_DATA_DIR            # default: /app/data/chroma
GOOGLE_CALENDAR_CREDS      # path al JSON OAuth (Calendar + Tasks) — default: /credentials/google-oauth.json

# Permisos Docker
ADSO_UID                   # UID del usuario del host — default: 1000
ADSO_GID                   # GID del usuario del host — default: 1000
```

---

## Decisiones clave

Políticas e invariantes que restringen cómo se escribe código nuevo. Los post-mortems de fixes puntuales viven en `docs/decisions-log.md` — leerlo antes de tocar `vault_writer`, `vault_watcher`, `GitBackup`, el flujo de confirmación o el manejo de errores de PTB.

- **Taxonomía de `type`:** `type` refleja propósito, no formato de origen. Los tipos son: `reference`, `task`, `idea`, `project-index`, `area-index`. No existe `type: draft` — cuando el LLM no puede clasificar con confianza usa `type: idea` con `status: pending-classification`. `project-index` y `area-index` son auto-generados por el bot (no por el LLM) y requieren `description` obligatoria al crear — el bot la pide y no permite omitirla. No existe `type: paper` — un paper es un `reference` con campos académicos opcionales (authors, year, doi, methods, etc.) que el pipeline de extracción popula. El lifecycle de lectura de papers se maneja con tasks (`"leer paper X"`). Los papers se identifican por tag `#paper` y/o presencia de campos académicos en frontmatter.

- **Destino en preview (`build_preview`):** project → `01-Projects/...`; area → `02-Areas/...`; sin destino (cualquier tipo) → `00-Inbox`. Modo degradado: `type: idea` + `status: pending-classification` → inbox.

- **Routing de destino (`_resolve_dest_dir`):** todos los tipos (`reference`, `task`, `idea`) siguen el mismo orden: project > area > Inbox/None. `task` con project va a `01-Projects/{project}/` aunque tenga area seteada.

- **Creación de proyecto/área desde bot:** `_extract_name_from_command()` parsea el nombre directamente con regex para patrones simples (`crear proyecto "X"`, `nuevo proyecto X`). Solo llama al LLM cuando el patrón no es reconocible (ej: "quiero un proyecto para mi tesis"). El intent ya viene confirmado por el botón, solo hace falta el nombre.

- **`params` del modo manage con `properties` declaradas (`_GEMINI_RESPONSE_SCHEMA`):** el constrained decoding de Gemini solo puede emitir claves presentes en el schema, así que el `params: {"type": "OBJECT"}` sin `properties` devolvía siempre `{}` — incluso con el nombre del proyecto visible en el input. `_validate_manage_payload` entonces lanzaba `LLMResponseError` y **todo el modo manage por texto libre caía a modo degradado**: el fallback de `_cb_manage_create` (`manage.py`, cuando `_extract_name_from_command` no reconoce el patrón) proponía el texto crudo del usuario como nombre del proyecto tras gastar 3 reintentos. Detectado por `scripts/llm_regression.py` contra el modelo en producción. Guard de regresión en `test_manage_params_declares_properties`.

- **Modo degradado:** si Gemini no responde, el input se guarda en `00-Inbox/` con `status: pending-classification`. Un cron reclasifica cuando la API vuelve. El cron (`reclassify_inbox`) se salta la pasada si hay cualquier flujo interactivo en curso — `_PENDING_FLOW_KEYS` cubre todas las keys de flujo (nota/operación/audio/PDF extraído/PDF escaneado/read_status/arXiv/reporte), alineado con `_has_pending_keyboard`, para no notificar en medio de una interacción.

- **Dedup de recursos (`save_resource`):** al copiar a `03-Resources/`, la reutilización de un archivo existente se decide por hash SHA-256 del contenido (con short-circuit por tamaño), no solo por tamaño. Dos archivos distintos del mismo tamaño ya no se confunden — el nuevo se guarda con sufijo numérico en vez de descartarse. Escritura vía `shutil.copy2`; hashing por chunks (memory-safe en RPi4).

- **Google Tasks (estado real):** hoy solo hay **push unidireccional** — al confirmar una task se crea en la lista `ADSO` de Google Tasks (`create_task`); el `task_id` devuelto no se persiste todavía. El sync bidireccional descrito abajo es **diseño, no implementado** (ver `docs/improvements-2026-07.md` §5). Diseño previsto: sync cada 30 min (`sync.interval_minutes`), Calendar y Tasks reconciliados en el mismo cron; fuentes de verdad → contenido/estructura de la nota al vault; `scheduled`, `due_date`, `status: done` y título → bidireccional (gana el último cambio); borrar una task en Google Tasks movería la nota a `00-Inbox/` con `status: pending-classification`. Requiere primero persistir `gtask_id` en el frontmatter (§5.1) y el job de reconciliación (§5.2).

- **Google Tasks:** lista `ADSO` dedicada (escritura/borrado), lectura de listas externas. `due_date` va al campo de fecha límite de Google Tasks — Google Calendar lo muestra automáticamente como chip, sin crear evento separado. Modelo semanal: planificación + revisión via reporte. Las tasks son intenciones de trabajo (scope = proyecto/área), no punteros a notas individuales. El campo `notes` de Google Tasks recibe: descripción original del usuario + proyecto/área + prioridad + horario si tiene hora no-medianoche. **No incluye links `obsidian://`** — no funcionan desde Google Tasks/Calendar. Las tasks no se editan via ADSO — cambios se hacen en Google Tasks/Calendar directamente. Si el push falla, el bot notifica al usuario por Telegram con el motivo; `tasks.debug: true` en `config.yaml` activa notificación también en push exitoso. Token OAuth en `/credentials/token_tasks.json`; si expira, re-autenticar con `scripts/auth_google_tasks.py` (ver procedimiento headless en el script).

- **Syncthing bidireccional:** ADSO es el escritor principal (toda creación de notas pasa por Telegram). Los clientes Obsidian pueden editar notas existentes. `VaultWatcher` detecta los cambios externos via `inotify` y re-embeds automáticamente. Al borrar una nota externamente, además de eliminar su embedding, se limpian los wikilinks rotos en bloques `## Ver también` de otras notas (`remove_broken_wikilinks` en `vault_writer.py`) — el bot notifica por Telegram si hubo notas modificadas. Mover una nota no rompe links porque los wikilinks usan solo el stem del archivo, no el path. El watcher tiene deduplicación de 2 segundos por path para evitar doble-notificación cuando inotify dispara `on_created` + `on_modified` al escribir un archivo nuevo. Además ignora archivos ocultos (`_is_hidden`): los temporales `.adso-tmp-*` de la escritura atómica y cualquier dotfile — sin ese filtro los temporales se indexaban como notas fantasma en ChromaDB y contaminaban el mensaje del commit de backup.

- **Conflictos Syncthing:** ADSO no resuelve, solo notifica. El usuario resuelve manualmente.

- **Caché de parsing del vault (`vault_cache.py`):** `_parse_note_safe` (usado por todas las funciones de scan de `vault_search.py`) delega en `parse_cached`, que cachea el resultado del parse keyed por `(mtime_ns, size)`. Correctness-preserving: cualquier modificación de una nota cambia el mtime y la entrada se invalida sola en el siguiente `stat()` — no hay acoplamiento con `VaultWatcher` ni ventana de staleness. El costo dominante de un scan en la RPi4 (SD lenta) es el `read()+parse`, no el `rglob`. Una captura corre `get_all_tags` dos veces (escanea todo el vault); con el caché el segundo scan baja ~69% (medido: 427→132 ms con 500 notas en RPi4). LRU acotado a 2000 entradas. El frontmatter devuelto es siempre una copia fresca para que mutaciones del caller no corrompan el caché. Métricas (`entries`, `hit_ratio`) expuestas en `/status`.

- **Escrituras atómicas al vault (`_atomic_write_sync` en `vault_writer.py`):** toda escritura de `.md` (`create_note`, `append_to_note`, `set_property`, `update_wikilinks`, limpieza de wikilinks) usa temp en el mismo directorio + `fsync` + `os.replace`. Un crash a mitad de escritura (OOM en RPi4, `docker stop`) nunca deja la nota truncada. Regla de oro: sin pérdida de datos. El temporal usa sufijo `.tmp` (no `.md`): además de ser hidden (`.adso-tmp-*`), el sufijo distinto de `.md` hace que el filtro del `VaultWatcher` lo saltee aunque el `_is_hidden` fallara, y evita que un `git add -A` concurrente lo commitee.

- **Sanitización de path (`_safe_component` en `vault_writer.py`):** `project`/`area`/`section` del frontmatter (LLM) y `name`/`project` de operaciones de gestión (`manage.py`) se sanitizan contra path traversal (`..`, separadores, dots iniciales) antes de concatenarse al path del vault. Valor inválido → se descarta (cae a Inbox / se rechaza la operación). Además `create_note` verifica `dest_dir.resolve().is_relative_to(vault_path)` como defensa en profundidad. Complementa el `Path(...).name` que ya protegía `save_resource`.

- **Neutralización de tags en el prompt (`classify` en `llm_client.py`):** el contenido externo se inserta en `<input>` tras neutralizar cualquier `<input>`/`</input>`/`<system>`/`<user_context>` literal que traiga (se le inserta un espacio tras el `<`, preservando el `<` legítimo de código/matemática). Cierra el vector de escape del wrapper para PDFs/OCR/abstracts.

- **Tareas de fondo con referencia fuerte (`spawn_tracked` en `bot_utils.py`):** reemplaza `asyncio.create_task` para trabajo fire-and-forget (push a Tasks, indexado, re-embed del watcher). Guarda referencia fuerte (evita GC prematuro que cancele la tarea) y loguea excepciones. El `VaultWatcher` usa su propio set y lo drena en `stop()`.

- **Orden "crear antes de descartar" (regla de oro, sin pérdida de datos):** `_cb_confirm` lee `pending_note`/`clasificar_inbox_path` con `get` y los popea **recién después** de que `create_note` retorne — si la escritura falla (disco lleno, I/O de la SD), el estado sigue en `user_data` y un segundo `[Confirmar]` reintenta. Sin esto se perdía definitivamente el texto de audio/OCR/Vision, que no existe en ningún otro lado. El temporal del recurso adjunto también se borra recién tras la escritura (el reintento lo necesita; `save_resource` dedup por hash, así que no duplica). Por el mismo motivo `reclassify_inbox` crea la nota nueva **antes** de borrar la del Inbox (antes hacía `delete_note` → `create_note`: un fallo de creación evaporaba la nota).

- **Descubrimiento de proyectos y áreas (`_get_existing_items`):** lee los subdirectorios de `01-Projects/` y `02-Areas/` directamente (no busca por `type: area-index`). Si el subdirectorio tiene `_index.md` con campos `project:`/`area:` y `description:`, los usa; si no, usa el nombre del directorio. Esto garantiza que cualquier área o proyecto con al menos una nota en su carpeta aparece en los reportes y teclados, aunque no tenga índice. El escaneo (iterdir + parse de cada `_index.md`) corre en `asyncio.to_thread` — se ejecuta en todo flujo de clasificación antes de cada `classify()` y no debe congelar el event loop en la RPi4. `/status` también threadiza su conteo del vault (`_gather_vault_counts`: `rglob` + `parse_cached` en vez de `read_note`).

- **Embedding único por texto:** `EmbeddingsClient.compute_embedding()` expone el cálculo para reutilizar el vector. En captura, el body se embebe una vez en el preview (`_body_embedding` viaja en el payload de `pending_note`) y se reutiliza al indexar en `_cb_confirm` **solo si el body no cambió** (sin "Ver también" ni recurso adjunto — si cambió, se recomputa). En `knowledge_query.retrieve`, el reintento con umbral relajado reutiliza el embedding de la primera pasada. `query_similar` e `index_note` aceptan el vector precomputado como parámetro opcional. El cliente `genai.Client` se instancia una vez y se reutiliza (lazy) en `embeddings.py`, `llm_client._get_genai_client()` y `reporters.py`.

- **Lock compartido de jobs pesados (`_vault_heavy_lock` en `jobs.py`):** `reclassify_inbox` y `reindex_job` comparten un `asyncio.Lock` — el reindex nocturno espera el lock, la reclasificación saltea la pasada si está tomado. Evita CPU/red concurrente de ambos crons en la RPi4. El reindex además usa `vault_cache.parse_cached` (no relee notas sin cambios desde la SD).

- **Hardening Docker:** `docker-compose.yml` corre con `no-new-privileges` y `cap_drop: ALL` (el bot procesa PDFs/imágenes no confiables con pymupdf/Pillow/tesseract). El backup SSH usa key dedicada + `known_hosts` precargado con `StrictHostKeyChecking=yes` — nunca `~/.ssh` completo ni `StrictHostKeyChecking=no` (ver `docs/installation.md` §4.3).

### Post-mortems movidos a `docs/decisions-log.md`

El *porqué* detrás de código que parece innecesariamente defensivo. Leerlo antes de tocar estos módulos:

- **`vault_writer`:** Construcción del `frontmatter.Post` (`_build_post` / `load_post`); Limpieza de wikilinks acotada al bloque `## Ver también`.
- **`GitBackup`:** Git backup fuera del event loop; Flush del backup al shutdown.
- **`vault_watcher` / embeddings:** `on_moved` + drenado de `bot_written_paths`; Embedding inline al confirmar.
- **Frontmatter editado a mano:** Valores no-string; YAML corrupto.
- **Medios (`callbacks.py` / `input.py`):** Render de PDFs escaneados fuera del event loop; Preview completo y copiable del texto extraído; Caption de imagen reutilizado como descripción; Límite de tamaño post-descarga.
- **Estado y errores:** Limpieza del estado de gestión (`pop_manage_state`); Error handler global de PTB.
