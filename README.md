<pre>
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
</pre>

Bot orquestador de Telegram que actúa como escriba, observador y clasificador del conocimiento: captura información no estructurada, la clasifica mediante LLMs, la persiste como notas Markdown en un vault de Obsidian y permite recuperarla mediante consultas en lenguaje natural.

## ¿Qué hace?

- **Captura** texto, links, imágenes y audios enviados via Telegram
- **Procesa** el contenido con Gemini API (y opcionalmente Claude), usando [Obsidian Skills](https://github.com/kepano/obsidian-skills) como referencia para generar Markdown compatible
- **Transcribe** audios localmente con `faster-whisper`
- **Extrae texto** de imágenes con Tesseract (o Gemini Vision, configurable)
- **Genera** archivos Markdown con Frontmatter YAML clasificados
- **Escribe** las notas al vault de Obsidian directamente al filesystem via volumen Docker
- **Busca** en el vault con dos motores complementarios: semántico (ChromaDB) y estructural (backlinks, tags, frontmatter)
- **Agenda** con Google Calendar y Google Tasks
- **Consulta** la base de conocimiento via RAG

## Stack

| Componente | Tecnología |
|---|---|
| Bot | Python 3.11+, `python-telegram-bot` (async) |
| LLM primario | Gemini API (Google AI Studio) |
| LLM secundario | Anthropic API / Claude (opcional) |
| Transcripción | `faster-whisper` (local) |
| OCR / Visión | Tesseract (local) o Gemini Vision (remoto) — el usuario elige en el momento |
| Knowledge base | Obsidian vault (Markdown + YAML Frontmatter) |
| Búsqueda semántica | ChromaDB (local) + Gemini Embedding API (remoto) |
| Búsqueda estructural | Parser propio de wikilinks, tags y frontmatter (`vault_search.py`) |
| Infraestructura | Docker + docker-compose en Raspberry Pi 4 (4GB RAM) |
| Calendar | Google Calendar API v3 + Google Tasks API |
| Backup | Git (repo privado en GitHub) |
| Sync | Syncthing send-only desde RPi4 (clientes read-only) + Git (backup/DR) |

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
- Google OAuth credentials (para Calendar + Tasks — Fase 6)
- Vault de Obsidian accesible como directorio local (montado via Docker)
