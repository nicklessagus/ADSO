# CLAUDE.md — ADSO

Instrucciones para Claude Code al trabajar en este repositorio.

---

## Proyecto

**ADSO** (*Autonomous Data Structuring Orchestrator*) es un bot de Telegram personal escrito en Python que actúa como escriba, observador y clasificador del conocimiento: captura información no estructurada, la clasifica mediante LLMs, la persiste como notas Markdown en un vault de Obsidian y permite recuperarla mediante consultas en lenguaje natural.

Documentación completa en `docs/`.

---

## Infraestructura de despliegue

- **Hardware:** Raspberry Pi 4, 4GB RAM, ARM64
- **Entorno:** Docker + docker-compose
- **Lenguaje:** Python 3.11+, implementación asíncrona
- **Vault:** Markdown en filesystem local (Syncthing para sync en vivo + Git para backup/DR — ver `docs/architecture.md`)

Toda propuesta de implementación debe evaluarse contra las restricciones de CPU y RAM de la RPi4. Mencionar explícitamente el impacto estimado en recursos.

---

## Stack

| Componente | Tecnología |
|---|---|
| Bot | `python-telegram-bot` (async) |
| LLM primario | Gemini API (Google AI Studio) |
| LLM secundario | Anthropic API / Claude (opcional) |
| Embeddings | Gemini Embedding API (remoto, no local) |
| Vector DB | ChromaDB embebido |
| Transcripción | `faster-whisper` (modelo `tiny` o `base`) |
| Calendar | Google Calendar API v3 — lectura de todos los calendarios, escritura y borrado solo en calendario `ADSO` dedicado |
| Tasks | Google Tasks API — lista `ADSO` dedicada (escritura/borrado) + lectura de listas externas |
| Vault | Markdown + YAML Frontmatter en filesystem |
| Backup vault | Repo git privado en GitHub — push automático tras cada nota confirmada |

---

## Estructura de módulos

```
adso/
├── bot.py                  # Orquestador principal, handlers de Telegram
├── context.py              # Gestión del contexto activo (proyecto/sección)
├── transcriber.py          # Transcripción de audio con faster-whisper
├── llm_client.py           # Cliente Gemini/Claude, clasificación y generación (usa Obsidian Skills como referencia)
├── vault_writer.py         # Escritura de .md al filesystem
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
02-Areas/tareas/nota.md                        # sin fin, continuo
03-Resources/                                  # material de referencia permanente (papers sueltos, etc.)
04-Ideas/nota.md                               # intenciones sin proyecto; pueden promoverse a proyecto
00-Inbox/nota.md                               # sin clasificar
05-Archive/                                    # proyectos inactivos o completados
```

### Ciclo de vida
Idea → Proyecto activo → Archivo → (borrado con doble confirmación)
Las áreas no tienen ciclo de vida.

### Frontmatter mínimo requerido
```yaml
---
title: ""
date_created: ""   # ISO 8601
date_modified: ""  # ISO 8601
type: ""           # project-note | paper | task | idea | inbox
tags: []
source: telegram
media_type: ""     # text | audio | image | link — automático
status: active     # active | archived | pending-classification
---
```

Schema completo en `docs/frontmatter-schema.md`.

### Regla de confirmación
Ninguna nota se escribe al vault sin confirmación explícita del usuario. El bot siempre muestra un preview del frontmatter y los links sugeridos antes de persistir.

### Prioridad inferida
El LLM infiere `priority` del lenguaje del mensaje para tipos accionables (task, paper, idea). La prioridad explícita del usuario siempre gana. Si no hay señal clara, sugiere `medium` y pregunta.

---

## Contexto activo

El bot mantiene un contexto activo persistente en disco:

- **Default:** raíz (vault completo)
- **Cambio:** `/contexto {proyecto}` o `/contexto {proyecto} {seccion}`
- **Volver a raíz:** `/contexto raiz`

Con contexto activo, todo el input se asume destino en ese proyecto/sección. Las consultas buscan primero ahí, luego en el vault completo si no encuentra. El bot muestra el contexto activo en cada respuesta.

Si el input claramente no pertenece al contexto activo, el bot lo detecta y pregunta antes de asumir destino.

---

## Modos de operación

El LLM clasifica cada mensaje en uno de estos modos antes de procesarlo:

| Modo | Ejemplos |
|---|---|
| **Captura** | Texto, audio, link, imagen con contenido a guardar |
| **Consulta** | "qué tengo sobre X", "mostrá relaciones", "todo pendiente" |
| **Agenda** | Input con fecha/hora explícita |
| **Edición** | "actualizá la nota X", "agregale esto a..." |
| **Gestión** | Crear proyecto, archivar, cambiar contexto |

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

---

## Fases de desarrollo

| Fase | Funcionalidad |
|---|---|
| 1 | Captura de texto, clasificación, confirmación, escritura al vault |
| 2 | Indexado del vault + links automáticos (embeddings + ChromaDB) |
| 3 | Audio (faster-whisper) |
| 4 | Imágenes y capturas |
| 5 | Integraciones externas (arXiv, NASA ADS) |
| 6 | Google Calendar + Google Tasks |
| 7 | Consultas RAG en lenguaje natural |
| 8 | Análisis del vault: reporte semanal, scoring de papers, detección de gaps |

Implementar en orden. No saltar fases.

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
CONTEXT_FILE               # default: /app/data/context.json
```

---

## Decisiones clave

- **Modo degradado:** si Gemini no responde, el input se guarda en `00-Inbox/` con `status: pending-classification`. Un cron reclasifica cuando la API vuelve.
- **Google Calendar y Tasks:** vault es fuente de verdad. Sync cada 30 min (configurable). Si hay conflicto entre cambios en Google y en el vault, gana el vault.
- **Google Tasks:** lista `ADSO` dedicada (escritura/borrado), lectura de listas externas. Modelo semanal: planificación + revisión via reporte.
- **Syncthing read-only en clientes:** ADSO es el único escritor del vault. Obsidian en clientes es solo lectura. Syncthing en modo send-only desde la RPi4.
- **Conflictos Syncthing:** ADSO no resuelve, solo notifica. El usuario resuelve manualmente.
