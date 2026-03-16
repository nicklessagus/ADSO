# CLAUDE.md — Adso

Instrucciones para Claude Code al trabajar en este repositorio.

---

## Proyecto

**Adso** es un bot de Telegram personal escrito en Python que actúa como escriba, observador y clasificador del conocimiento: captura información no estructurada, la clasifica mediante LLMs, la persiste como notas Markdown en un vault de Obsidian y permite recuperarla mediante consultas en lenguaje natural.

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
| Calendar | Google Calendar API v3 |
| Vault | Markdown + YAML Frontmatter en filesystem |

---

## Estructura de módulos

```
adso/
├── bot.py                  # Orquestador principal, handlers de Telegram
├── transcriber.py          # Transcripción de audio con faster-whisper
├── llm_client.py           # Cliente Gemini/Claude, clasificación y generación
├── vault_writer.py         # Escritura de .md al filesystem
├── embeddings.py           # Pipeline de embeddings y ChromaDB
├── knowledge_query.py      # RAG — consultas sobre el vault
├── calendar_client.py      # Google Calendar API
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
01-Projects/{proyecto}/{seccion}/nota.md
02-Areas/tareas/nota.md
03-Resources/ideas/nota.md
00-Inbox/nota.md
```

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
| 4 | Google Calendar |
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
GOOGLE_CALENDAR_CREDS      # path al JSON OAuth
LINK_SIMILARITY_THRESHOLD  # default: 0.82
VAULT_PATH                 # default: /vault
```
