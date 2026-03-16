# CLAUDE.md — Adso

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
- **Vault:** Markdown en filesystem local, sincronizado via Syncthing

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
| Tasks | Google Tasks API |
| Vault | Markdown + YAML Frontmatter en filesystem |

---

## Estructura de módulos

```
adso/
├── bot.py                  # Orquestador principal, handlers de Telegram
├── context.py              # Gestión del contexto activo (proyecto/sección)
├── transcriber.py          # Transcripción de audio con faster-whisper
├── llm_client.py           # Cliente Gemini/Claude, clasificación y generación
├── vault_writer.py         # Escritura de .md al filesystem
├── embeddings.py           # Pipeline de embeddings y ChromaDB
├── knowledge_query.py      # RAG — retrieval de notas por consulta
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
03-Resources/ideas/nota.md                     # pueden convertirse en proyectos
00-Inbox/nota.md                               # sin clasificar
04-Archive/                                    # proyectos inactivos o completados
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
status: active
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
| **Gestión** | Crear proyecto, archivar, cambiar contexto |

**El bot es un sistema de retrieval, no de razonamiento.** En modo consulta, recupera y presenta notas relevantes del vault. No agrega conocimiento propio ni opina sobre el contenido.

Acciones destructivas (archivar, borrar, renombrar) siempre requieren confirmación explícita.

---

## Embeddings

- Se calculan via **Gemini Embedding API** (nunca localmente).
- Se almacenan en **ChromaDB** en `/app/data/chroma/`.
- Se generan de forma asíncrona inmediatamente después de confirmar una nota.
- Umbral de similitud para sugerir links: `LINK_SIMILARITY_THRESHOLD` (configurable).
- Carpetas excluidas del índice: `VAULT_EXCLUDE_DIRS` en `config.py`.

---

## Fases de desarrollo

| Fase | Funcionalidad |
|---|---|
| 1 | Captura de texto, clasificación, confirmación, escritura al vault |
| 2 | Indexado del vault + links automáticos (embeddings + ChromaDB) |
| 3 | Audio (faster-whisper) |
| 4 | Google Calendar + Google Tasks |
| 5 | Imágenes y capturas |
| 6 | Consultas RAG en lenguaje natural |
| 7 | Integraciones externas (arXiv, NASA ADS) |

Implementar en orden. No saltar fases.

---

## Validación de código

Todo el código generado es validado con **OpenAI Codex** antes de incorporarse al repositorio.

---

## Variables de entorno requeridas

```bash
TELEGRAM_TOKEN
TELEGRAM_ALLOWED_USER_ID
GEMINI_API_KEY
ANTHROPIC_API_KEY          # opcional
GOOGLE_CALENDAR_CREDS      # path al JSON OAuth (Calendar + Tasks)
LINK_SIMILARITY_THRESHOLD  # default: 0.82
VAULT_PATH                 # default: /vault
CONTEXT_FILE               # default: /app/data/context.json
```
