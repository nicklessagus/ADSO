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

- **Captura** texto, links, imágenes, audios y documentos enviados via Telegram
- **Procesa** el contenido con Gemini API (y opcionalmente Claude) para clasificar, titular y enrutar cada nota
- **Transcribe** audios localmente con `faster-whisper`
- **Extrae texto** de imágenes con OCR local (pytesseract) o Gemini Vision — el usuario elige en el momento
- **Extrae metadata** de PDFs con `pymupdf`: texto, abstract, keywords, autores, DOI; detecta papers académicos automáticamente
- **Integra arXiv**: al recibir un link de arxiv.org recupera metadata completa via API oficial (sin scraping) y genera una nota con abstract, autores y summary en español
- **Genera** archivos Markdown con Frontmatter YAML clasificados, con links automáticos por similitud semántica
- **Escribe** las notas al vault de Obsidian directamente al filesystem via volumen Docker
- **Busca** en el vault con dos motores complementarios: semántico (ChromaDB + Gemini Embeddings) y estructural (backlinks, tags, frontmatter)
- **Reporta** el estado del vault a pedido: scope por proyecto/área/inbox, ideas, cola de lectura, salud del vault (`/reporte`, `/reporte_full`)

## Stack

| Componente | Tecnología |
|---|---|
| Bot | Python 3.9+ (dev) / 3.11 (Docker), `python-telegram-bot` (async) |
| LLM primario | Gemini API — `gemini-2.5-flash-lite` |
| LLM secundario | Anthropic API / Claude (opcional) |
| Transcripción | `faster-whisper` (local, modelo `tiny`/`base`) |
| OCR / Visión | `pytesseract` (local) o Gemini Vision (remoto) — el usuario elige en el momento |
| PDF | `pymupdf` — extracción de texto, metadata académica y detección de papers |
| Extracción web | arXiv API (papers) / Gemini nativo (producción) / `trafilatura` (desarrollo) |
| Knowledge base | Obsidian vault (Markdown + YAML Frontmatter) |
| Embeddings | Gemini Embedding API (remoto) |
| Búsqueda semántica | ChromaDB (local) |
| Búsqueda estructural | Parser propio de wikilinks, tags y frontmatter (`vault_search.py`) |
| Infraestructura | Docker + docker-compose en Raspberry Pi 4 (4GB RAM) |
| Calendar / Tasks | Google Calendar API v3 + Google Tasks API *(Fase 6 — diferida)* |
| Backup | Git (repo privado en GitHub) |
| Sync | Syncthing send-only desde RPi4 (clientes read-only) + Git (backup/DR) |

## Documentación

- [`docs/installation.md`](docs/installation.md) — Instalación y puesta en marcha paso a paso
- [`docs/architecture.md`](docs/architecture.md) — Diagrama de flujo, componentes y fases de desarrollo
- [`docs/obsidian-vault-structure.md`](docs/obsidian-vault-structure.md) — Estructura PARA del vault y plugins
- [`docs/frontmatter-schema.md`](docs/frontmatter-schema.md) — Schema YAML por tipo de nota y queries Dataview
- [`docs/configuration.md`](docs/configuration.md) — Referencia de configuración (`config.yaml`)
- [`docs/testing.md`](docs/testing.md) — Estrategia de testing: niveles, cobertura y fixtures
- [`docs/security.md`](docs/security.md) — Modelo de amenaza, mitigaciones, JSON schema del LLM y checklist de deploy
- [`docs/vault-interface.md`](docs/vault-interface.md) — Firmas de funciones de vault_writer, vault_search y embeddings
- [`docs/gemini-gem-instructions.md`](docs/gemini-gem-instructions.md) — Instrucciones de referencia para el LLM (Gemini)

## Estado

Fases 1–5 implementadas y funcionando: captura de texto, audio, documentos, imágenes y links de arXiv; clasificación con Gemini; embeddings en ChromaDB; escritura al vault con confirmación; reportes a pedido (Fase 8 parcial). Fase 6 (Google Calendar + Tasks) diferida. Ver fases de desarrollo en [`docs/architecture.md`](docs/architecture.md).

## Requisitos previos

- Docker y docker-compose-v2 (`sudo apt install docker-compose-v2`)
- Token de bot de Telegram (via @BotFather)
- API key de Gemini (Google AI Studio — free tier, sin tarjeta de crédito)
- Google OAuth credentials (para Calendar + Tasks — Fase 6)
- Vault de Obsidian accesible como directorio local (montado via Docker)

Ver instrucciones detalladas en [`docs/installation.md`](docs/installation.md).
