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

[![CI](https://github.com/nicklessagus/ADSO/actions/workflows/ci.yml/badge.svg)](https://github.com/nicklessagus/ADSO/actions/workflows/ci.yml)
[![Security](https://github.com/nicklessagus/ADSO/actions/workflows/security.yml/badge.svg)](https://github.com/nicklessagus/ADSO/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)

Bot orquestador de Telegram que actúa como escriba, observador y clasificador del conocimiento: captura información no estructurada, la clasifica mediante LLMs, la persiste como notas Markdown en un vault de Obsidian y permite recuperarla mediante consultas en lenguaje natural.

## ¿Qué hace?

- **Captura** texto, links, imágenes, audios y documentos enviados via Telegram
- **Procesa** el contenido con Gemini API (con fallback a Groq) para clasificar, titular y enrutar cada nota
- **Transcribe** audios localmente con `faster-whisper`
- **Extrae texto** de imágenes con OCR local (pytesseract) o Gemini Vision — el usuario elige en el momento
- **Extrae metadata** de PDFs con `pymupdf`: texto, abstract, keywords, autores, DOI; detecta papers académicos automáticamente
- **Preserva formato** de archivos `.md` y `.txt`: el LLM solo genera el frontmatter (título, tags, destino), el body de la nota es el contenido original sin modificar
- **Integra arXiv**: al recibir un link de arxiv.org recupera metadata completa via API oficial (sin scraping) y genera una nota con abstract, autores y summary en español
- **Genera** archivos Markdown con Frontmatter YAML clasificados, con links automáticos por similitud semántica
- **Escribe** las notas al vault de Obsidian directamente al filesystem via volumen Docker
- **Busca** en el vault con retrieval semántico: `/buscar` o el botón `[🔎 Buscar en el vault]` (ChromaDB + Gemini Embeddings). La búsqueda estructural (backlinks, tags, frontmatter) existe como librería (`vault_search.py`) y la usan flujos internos — `/clasificar`, dedup de papers, reportes, crons —, pero todavía no hay comando ni flujo de usuario que la exponga
- **Reporta** el estado del vault a pedido: scope por proyecto/área/inbox, ideas, cola de lectura, salud del vault (`/reporte`, `/reporte_full`)

## Stack

| Componente | Tecnología |
|---|---|
| Bot | Python 3.11+, `python-telegram-bot` (async) |
| LLM primario | Gemini API — `gemini-3.5-flash-lite` |
| LLM de Vision | Gemini API — `gemini-3.6-flash` (`GEMINI_VISION_MODEL`, constante separada de `GEMINI_MODEL`: la quota del free tier es **por modelo**, así que rasterizar un PDF escaneado no consume RPD del bucket de la captura diaria) |
| LLM fallback | Groq — `llama-3.1-8b-instant` (entra en dos casos: cuando Gemini agota la cuota diaria, y cuando Gemini devuelve dos respuestas inservibles seguidas — JSON o schema inválido. Un error de red o timeout agota los 3 reintentos y cae a modo degradado sin pasar por Groq) |
| Transcripción | `faster-whisper` (local, modelo `tiny`/`base`) |
| OCR / Visión | `pytesseract` (local) o Gemini Vision (remoto) — el usuario elige en el momento |
| PDF | `pymupdf` — extracción de texto, metadata académica y detección de papers |
| Extracción web | arXiv API (papers). **No hay extracción de web genérica:** un link que no sea de arxiv.org sigue el flujo de texto y la URL queda en el body |
| Knowledge base | Obsidian vault (Markdown + YAML Frontmatter) |
| Embeddings | Gemini Embedding API (remoto) |
| Búsqueda semántica | ChromaDB (local) |
| Búsqueda estructural | Parser propio de wikilinks, tags y frontmatter (`vault_search.py`) |
| Infraestructura | Docker + docker-compose en Raspberry Pi 4 (4GB RAM) |
| Calendar / Tasks | Google Tasks API (implementado) + Google Calendar API v3 *(diferido)* |
| Backup | Git (repo privado en GitHub) |
| Sync | Syncthing bidireccional + Git (backup/DR). `VaultWatcher` re-embeds cambios externos automáticamente, con el mismo filtro que el reindex nocturno (`should_index`: fuera `05-Archive`, los `_index.md` y las carpetas de `vault.exclude_dirs`) |

## Documentación

- [`docs/installation.md`](docs/installation.md) — Instalación y puesta en marcha paso a paso
- [`docs/architecture.md`](docs/architecture.md) — Diagrama de flujo, componentes y fases de desarrollo
- [`docs/obsidian-vault-structure.md`](docs/obsidian-vault-structure.md) — Estructura PARA del vault y plugins
- [`docs/frontmatter-schema.md`](docs/frontmatter-schema.md) — Schema YAML por tipo de nota y queries Dataview
- [`docs/configuration.md`](docs/configuration.md) — Referencia de configuración (`config.yaml`)
- [`docs/testing.md`](docs/testing.md) — Estrategia de testing: niveles, cobertura y fixtures
- [`docs/security.md`](docs/security.md) — Modelo de amenaza, mitigaciones, JSON schema del LLM y checklist de deploy
- [`docs/vault-interface.md`](docs/vault-interface.md) — Firmas de funciones de vault_writer, vault_search y embeddings
- [`docs/decisions-log.md`](docs/decisions-log.md) — Post-mortems: el porqué detrás de código que parece innecesariamente defensivo

## Estado

Fases 1–5 implementadas y funcionando: captura de texto, audio, documentos, imágenes y links de arXiv; clasificación con Gemini; embeddings en ChromaDB; escritura al vault con confirmación; retrieval semántico con `/buscar` (Fase 7 parcial); reportes a pedido (Fase 8 parcial). Fase 6 parcial: Google Tasks implementado, Google Calendar diferido. Ver el estado de las fases en [`ROADMAP.md`](ROADMAP.md).

## Requisitos previos

- Docker y docker-compose-v2 (`sudo apt install docker-compose-v2`)
- Token de bot de Telegram (via @BotFather)
- API key de Gemini (Google AI Studio — free tier, sin tarjeta de crédito)
- API key de Groq (free tier — fallback LLM cuando Gemini agota la cuota diaria o devuelve respuestas inservibles)
- Google OAuth credentials (opcional — para la integración con Google Tasks)
- Vault de Obsidian accesible como directorio local (montado via Docker)

Ver instrucciones detalladas en [`docs/installation.md`](docs/installation.md).
