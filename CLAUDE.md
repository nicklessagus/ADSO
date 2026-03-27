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
pytest
```

Requiere Python ≥ 3.9. No hay dependencias nativas — venv estándar alcanza, no necesita conda.

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
- **Lenguaje:** Python 3.9+ (dev), 3.11 (Docker), implementación asíncrona
- **Vault:** Markdown en filesystem local (Syncthing para sync en vivo + Git para backup/DR — ver `docs/architecture.md`)
- **Health check:** `heartbeat_job` toca `/tmp/adso_heartbeat` cada 60s. Docker verifica que el archivo tenga menos de 2 minutos (`find -mmin -2`); 3 fallos consecutivos → `unhealthy`. `start_period: 30s` para absorber el arranque.

Toda propuesta de implementación debe evaluarse contra las restricciones de CPU y RAM de la RPi4. Mencionar explícitamente el impacto estimado en recursos.

---

## Stack

| Componente | Tecnología |
|---|---|
| Bot | `python-telegram-bot[job-queue]` v21+ (async) |
| LLM primario | Gemini API — modelo `gemini-2.5-flash-lite` (free tier preview: ~20 RPD observado) |
| LLM secundario | Anthropic API / Claude (opcional) |
| Embeddings | Gemini Embedding API (remoto, no local) |
| Vector DB | ChromaDB embebido |
| Transcripción | `faster-whisper` (modelo `tiny` o `base`) |
| Extracción web | Gemini nativo (producción) / `trafilatura` (desarrollo) |
| Extracción PDF | `pymupdf` (texto + metadata) — detección heurística de papers + extracción local de secciones (abstract, keywords, métodos, conclusiones); preview muestra título + abstract + keywords para papers, texto crudo para genéricos |
| Calendar | Google Calendar API v3 — lectura de todos los calendarios, escritura y borrado solo en calendario `ADSO` dedicado |
| Tasks | Google Tasks API — lista `ADSO` dedicada (escritura/borrado) + lectura de listas externas |
| Vault | Markdown + YAML Frontmatter en filesystem |
| Backup vault | Repo git privado en GitHub — push automático con debounce configurable (`backup.debounce_seconds`) |

---

## Estructura de módulos

```
adso/
├── bot.py                  # Orquestador principal, handlers de Telegram, inline keyboards
├── transcriber.py          # Transcripción de audio con faster-whisper
├── llm_client.py           # Cliente Gemini/Claude, clasificación y generación (usa Obsidian Skills como referencia)
├── vault_writer.py         # Escritura de .md al filesystem + git backup con debounce
├── vault_search.py         # Búsqueda estructural: backlinks ([[wikilinks]]), tags, filtros por frontmatter
├── embeddings.py           # Pipeline de embeddings y ChromaDB
├── knowledge_query.py      # Retrieval semántico — busca notas por similitud vectorial en ChromaDB (no llama al LLM)
├── calendar_client.py      # Google Calendar API
├── tasks_client.py         # Google Tasks API
├── security.py             # Middleware de autenticación
└── config.py               # Variables de entorno y constantes
```

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

- Todo contenido externo (URLs, PDFs, imágenes) se pasa al LLM dentro de etiquetas `<input>` con instrucción explícita de no seguir instrucciones internas.
- El LLM siempre responde en JSON estructurado con schema fijo.
- Autenticación por `TELEGRAM_ALLOWED_USER_ID` en todo handler. Usar el middleware de `security.py`.
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
type: ""           # reference | task | idea | draft | project-index | area-index
tags: []           # siempre en inglés, kebab-case; el LLM reutiliza tags existentes del vault (excluyendo 00-Inbox) antes de crear nuevos
source: telegram   # "telegram" para notas de usuario, "system" para auto-generadas
media_type: ""     # text | audio | image | link | document — automático
status: active     # valores dependen del type — ver docs/frontmatter-schema.md
---
```

Los tipos `project-index` y `area-index` se generan automáticamente al crear proyecto/área (no por clasificación del LLM). Ambos requieren `description` — el bot la pide obligatoriamente en la creación. Schema completo en `docs/frontmatter-schema.md`.

### Regla de confirmación
Ninguna nota se escribe al vault sin confirmación explícita del usuario. El bot muestra un preview del frontmatter y los links sugeridos, y el usuario confirma con inline keyboard (`[Confirmar]` `[Reubicar]` `[Cancelar]`).

`[Reubicar]` cambia únicamente el destino (`[Elegir área]` `[Elegir proyecto]` `[Inbox]`). Para corregir cualquier otro campo (título, tags, tipo, prioridad), el usuario manda texto libre antes de confirmar — el bot actualiza el frontmatter y regenera el preview.

### Prioridad inferida
El LLM infiere `priority` del lenguaje del mensaje para tipos accionables (task, idea). Si no hay señal clara, usa `medium`. La prioridad aparece en el preview y el usuario puede corregirla por texto libre antes de confirmar, como cualquier otro campo.

---

## Modelo de interacción

El bot funciona en un único chat de Telegram. No hay estado de contexto persistente. Toda la interacción se basa en **lenguaje natural + inline keyboards**.

### Estado default: captura
El usuario manda contenido (texto, audio, link, imagen, documento). El LLM infiere tipo, proyecto y sección del contenido mismo. El bot propone clasificación y el usuario confirma, edita o cancela con inline keyboards.

### Estado transiente: consulta
El usuario pregunta algo sobre el vault. El bot resuelve la consulta, devuelve el resultado (inline o como archivo `.md` con links `obsidian://`) y vuelve al estado default.

### Inline keyboards
Los botones son el mecanismo principal de interacción después del lenguaje natural:

| Momento | Botones |
|---|---|
| **PDF recibido** | `[Ya lo leí]` `[Lo quiero leer]` — setea `read_status` en frontmatter; aplica a cualquier PDF/documento |
| **Imagen recibida** | `[OCR]` `[Gemini Vision]` `[Describir]` `[Cancelar]` |
| **Resultado OCR** | `[Cancelar]` `[Corregir]` / `[Gemini Vision]` `[Confirmar]` — dos filas; Gemini Vision descarta el OCR y reprocesa |
| **Resultado Gemini Vision** | `[Cancelar]` `[Corregir]` `[Confirmar]` |
| **Audio transcripto** | `[Cancelar]` `[Corregir]` `[Confirmar]` |
| **Captura** (destino claro) | `[Confirmar]` `[Reubicar]` `[Cancelar]` |
| **Reubicar destino** | `[Elegir área]` `[Elegir proyecto]` `[Inbox]` |
| **Captura** (sin destino) | `[Elegir área]` `[Elegir proyecto]` `[Inbox]` |
| **Consulta** (si falta scope) | `[Todo]` `[Proyecto1]` `[Proyecto2]` ... |
| **Resultado de consulta** | `[Ver referencias completas]` `[Generar informe .md]` |
| **Expansión desde nodo** | `[Solo relaciones directas]` `[Expandir un grado más]` |
| **Desambiguación** (modo incierto) | `[Guardar como nota]` `[Buscar en vault]` *(Fase 7)* |
| **Fallback OCR sin texto** | `[Gemini Vision]` / `[Cancelar]` `[Describir]` — OCR no encontró texto, sin botón OCR |

### Desambiguación de intención
Si el LLM no tiene confianza alta en el modo, el bot pregunta con botones en vez de asumir. `[Buscar en vault]` es Fase 7 — por ahora responde "disponible en próxima versión".

El LLM no usa `mode=query` ni `mode=edit` (removidos del prompt hasta Fase 7). Todo input que no sea `manage` se clasifica como `capture`. Si el LLM devuelve `query` o `edit` de todas formas, el código los redirige a `capture` automáticamente.

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
| 6 | Google Calendar + Google Tasks | ⏸ diferida — diseño pendiente |
| 7 | Consultas RAG en lenguaje natural | — |
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

---

## Validación de código

- Todo el código generado es validado con **OpenAI Codex** antes de incorporarse al repositorio.
- Estrategia de testing completa en `docs/testing.md`: unit, integration y e2e con cobertura ≥ 80%.

---

## Variables de entorno requeridas

```bash
TELEGRAM_TOKEN
TELEGRAM_ALLOWED_USER_ID
GEMINI_API_KEY
ANTHROPIC_API_KEY          # opcional
GOOGLE_CALENDAR_CREDS      # path al JSON OAuth (Calendar + Tasks) — default: /credentials/google-oauth.json
VAULT_PATH                 # default: /vault
```

---

## Decisiones clave

- **Taxonomía de `type`:** `type` refleja propósito, no formato de origen. Los tipos son: `reference`, `task`, `idea`, `draft`, `project-index`, `area-index`. `project-index` y `area-index` son auto-generados por el bot (no por el LLM) y requieren `description` obligatoria al crear — el bot la pide y no permite omitirla. No existe `type: paper` — un paper es un `reference` con campos académicos opcionales (authors, year, doi, methods, etc.) que el pipeline de extracción popula. El lifecycle de lectura de papers se maneja con tasks (`"leer paper X"`). Los papers se identifican por tag `#paper` y/o presencia de campos académicos en frontmatter.

- **Destino en preview (`build_preview`):** project → `01-Projects/...`; area → `02-Areas/...`; `type: draft` sin project ni area → `00-Inbox` (tanto cuando el usuario elige Inbox explícitamente como en modo degradado — ambos terminan en Inbox); cualquier otro tipo sin destino → "por definir" (el usuario debe elegir antes de confirmar).
- **Creación de proyecto/área desde bot:** `_extract_name_from_command()` parsea el nombre directamente con regex para patrones simples (`crear proyecto "X"`, `nuevo proyecto X`). Solo llama al LLM cuando el patrón no es reconocible (ej: "quiero un proyecto para mi tesis"). El intent ya viene confirmado por el botón, solo hace falta el nombre.
- **Modo degradado:** si Gemini no responde, el input se guarda en `00-Inbox/` con `status: pending-classification`. Un cron reclasifica cuando la API vuelve.
- **Google Calendar y Tasks:** sync cada 30 min (configurable via `sync.interval_minutes`). Calendar y Tasks se reconcilian en el mismo cron. Fuentes de verdad: contenido y estructura de la nota → vault; `scheduled`, `due_date`, `status: done` y título de tarea → bidireccional (gana el último cambio). Borrar una task en Google Tasks mueve la nota a `00-Inbox/` con `status: pending-classification`.
- **Google Tasks:** lista `ADSO` dedicada (escritura/borrado), lectura de listas externas. `due_date` va al campo de fecha límite de Google Tasks — Google Calendar lo muestra automáticamente como chip, sin crear evento separado. Modelo semanal: planificación + revisión via reporte. Las tasks son intenciones de trabajo (scope = proyecto/área), no punteros a notas individuales. El campo `notes` de Google Tasks recibe: descripción + subtareas como bullets `•` + links `obsidian://` al proyecto/área (primero) y a todas las notas relevantes encontradas en el vault. Las tasks no se editan via ADSO — cambios se hacen en Google Tasks/Calendar directamente.
- **Syncthing read-only en clientes:** ADSO es el único escritor del vault. Obsidian en clientes es solo lectura. Syncthing en modo send-only desde la RPi4.
- **Conflictos Syncthing:** ADSO no resuelve, solo notifica. El usuario resuelve manualmente.
