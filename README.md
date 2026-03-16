# Adso

Bot orquestador de Telegram que captura información no estructurada, la clasifica mediante LLMs y la persiste como notas Markdown estructuradas en un vault de Obsidian.

## ¿Qué hace?

- **Captura** texto, links, imágenes y audios enviados via Telegram
- **Procesa** el contenido con Gemini API (y opcionalmente Claude)
- **Transcribe** audios localmente con Whisper
- **Genera** archivos Markdown con Frontmatter YAML clasificados
- **Escribe** las notas al vault de Obsidian via Local REST API
- **Consulta** la base de conocimiento via RAG (Fase 2)

## Stack

| Componente | Tecnología |
|---|---|
| Bot | Python 3.11+, `python-telegram-bot` (async) |
| LLM primario | Gemini API (Google AI Studio) |
| LLM secundario | Anthropic API / Claude (opcional) |
| Transcripción | Whisper (local, cuantizado) |
| Knowledge base | Obsidian vault (Markdown + YAML Frontmatter) |
| Vector DB | ChromaDB (local) |
| Infraestructura | Docker + docker-compose en Raspberry Pi 4 |
| Sync | Syncthing |

## Documentación

- [`docs/architecture.md`](docs/architecture.md) — Diagrama de flujo, componentes y fases de desarrollo
- [`docs/obsidian-vault-structure.md`](docs/obsidian-vault-structure.md) — Estructura PARA del vault y plugins
- [`docs/frontmatter-schema.md`](docs/frontmatter-schema.md) — Schema YAML por tipo de nota y queries Dataview

## Estado

En fase de diseño y documentación. Ver fases de desarrollo en [`docs/architecture.md`](docs/architecture.md).

## Requisitos previos

- Raspberry Pi 4 (4GB RAM)
- Docker y docker-compose
- Token de bot de Telegram
- API key de Gemini (Google AI Studio — free tier)
- Obsidian con plugin Local REST API instalado
- Syncthing configurado
