# ADSO — Autonomous Data Structuring Orchestrator

Bot orquestador de Telegram que actúa como escriba, observador y clasificador del conocimiento: captura información no estructurada, la clasifica mediante LLMs, la persiste como notas Markdown en un vault de Obsidian y permite recuperarla mediante consultas en lenguaje natural.

## ¿Qué hace?

- **Captura** texto, links, imágenes y audios enviados via Telegram
- **Procesa** el contenido con Gemini API (y opcionalmente Claude)
- **Transcribe** audios localmente con `faster-whisper`
- **Genera** archivos Markdown con Frontmatter YAML clasificados
- **Escribe** las notas al vault de Obsidian directamente al filesystem via volumen Docker
- **Agenda** con Google Calendar y Google Tasks
- **Consulta** la base de conocimiento via RAG

## Stack

| Componente | Tecnología |
|---|---|
| Bot | Python 3.11+, `python-telegram-bot` (async) |
| LLM primario | Gemini API (Google AI Studio) |
| LLM secundario | Anthropic API / Claude (opcional) |
| Transcripción | `faster-whisper` (local) |
| Knowledge base | Obsidian vault (Markdown + YAML Frontmatter) |
| Vector DB | ChromaDB (local) |
| Infraestructura | Docker + docker-compose en Raspberry Pi 4 |
| Calendar | Google Calendar API v3 + Google Tasks API |
| Backup | Git (repo privado en GitHub) |
| Sync | Syncthing (sync en vivo) + Git (backup/DR) |

## Documentación

- [`docs/architecture.md`](docs/architecture.md) — Diagrama de flujo, componentes y fases de desarrollo
- [`docs/obsidian-vault-structure.md`](docs/obsidian-vault-structure.md) — Estructura PARA del vault y plugins
- [`docs/frontmatter-schema.md`](docs/frontmatter-schema.md) — Schema YAML por tipo de nota y queries Dataview
- [`docs/configuration.md`](docs/configuration.md) — Referencia de configuración (`config.yaml`)
- [`docs/testing.md`](docs/testing.md) — Estrategia de testing: niveles, cobertura y fixtures
- [`docs/security.md`](docs/security.md) — Modelo de amenaza, mitigaciones y checklist de deploy

## Estado

En fase de diseño y documentación. Ver fases de desarrollo en [`docs/architecture.md`](docs/architecture.md).

## Requisitos previos

- Raspberry Pi 4 (4GB RAM)
- Docker y docker-compose
- Token de bot de Telegram
- API key de Gemini (Google AI Studio — free tier)
- Vault de Obsidian accesible como directorio local (montado via Docker)
