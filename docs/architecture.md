# Arquitectura del Sistema Adso

## Visión general

Adso es un bot orquestador de Telegram que captura información no estructurada, la procesa con LLMs y la persiste como notas Markdown estructuradas en un vault de Obsidian.

---

## Diagrama de flujo

```
Usuario (Telegram)
       │
       ▼
┌─────────────────┐
│   Bot Python    │  python-telegram-bot, async
│   (RPi4)        │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
 Texto     Audio
 Imagen    Link
    │         │
    │    Whisper (local)
    │    transcripción
    │         │
    └────┬────┘
         │ texto unificado
         ▼
┌─────────────────┐
│   LLM API       │  Gemini API (clasificación + YAML)
│                 │  Claude API (consultas complejas, opcional)
└────────┬────────┘
         │ Markdown + Frontmatter YAML
         ▼
┌─────────────────┐
│  Obsidian       │  via Local REST API plugin
│  Local REST API │  HTTP → vault local
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Vault local    │  filesystem en RPi4
│  (Markdown)     │
└────────┬────────┘
         │
    Syncthing
         │
         ▼
  Dispositivos con
  Obsidian instalado
```

---

## Componentes

### Bot Python (`bot.py`)
- Framework: `python-telegram-bot` (async)
- Handlers: texto, foto, audio, documento, URL
- Responsabilidad: recibir input, orquestar el pipeline, escribir resultado

### Módulo de transcripción (`transcriber.py`)
- Modelo: Whisper cuantizado (ej. `whisper.cpp` o `faster-whisper`)
- Input: archivo de audio descargado desde Telegram
- Output: texto transcripto

### Módulo LLM (`llm_client.py`)
- Proveedor primario: Gemini API (Google AI Studio, free tier)
- Proveedor secundario: Anthropic API (Claude, opcional)
- Responsabilidad: clasificar contenido, generar frontmatter YAML, redactar nota

### Módulo Obsidian (`obsidian_writer.py`)
- Interfaz: Local REST API plugin (HTTP)
- Responsabilidad: crear y actualizar notas en el vault
- Alternativa: escritura directa al filesystem via volumen Docker

### Módulo RAG (`knowledge_query.py`) — Fase 2
- Índice vectorial: ChromaDB o FAISS (liviano, corre en RPi4)
- Embeddings: Gemini Embedding API o modelo local
- Responsabilidad: responder consultas del usuario con contexto del vault

---

## Infraestructura

```yaml
# docker-compose.yml (esquema)
services:
  adso-bot:
    build: .
    environment:
      - TELEGRAM_TOKEN
      - GEMINI_API_KEY
      - ANTHROPIC_API_KEY  # opcional
      - OBSIDIAN_API_URL
      - OBSIDIAN_API_KEY
    volumes:
      - ./vault:/vault        # vault de Obsidian
      - ./data:/app/data      # índice vectorial, caché
    restart: always
```

### Restricciones RPi4 (4GB RAM)
- Whisper: usar modelo `tiny` o `base` cuantizado (< 200MB RAM)
- ChromaDB/FAISS: índice local, sin servidor externo
- Llamadas a API externas (Gemini/Claude): no consumen RAM local significativa
- Evitar modelos LLM locales grandes (llama, etc.) por RAM insuficiente

---

## Fases de desarrollo

| Fase | Funcionalidad | Estado |
|---|---|---|
| 1 | Captura de texto y clasificación básica | Pendiente |
| 2 | Soporte de audio (transcripción Whisper) | Pendiente |
| 3 | Soporte de imágenes (descripción via LLM) | Pendiente |
| 4 | RAG — consultas sobre el vault | Pendiente |
| 5 | Gestión de tareas (recordatorios) | Pendiente |

---

## Decisiones de diseño

| Decisión | Elección | Alternativa descartada | Razón |
|---|---|---|---|
| Sync del vault | Syncthing | GitHub, Obsidian Sync | Ya configurado, tiempo real, P2P |
| LLM primario | Gemini API | Claude API | Free tier disponible para prototipo |
| Interfaz Obsidian | Local REST API | Escritura directa al FS | Découplé, no requiere montar volumen compartido |
| Transcripción | Whisper local | APIs externas | Privacidad, sin costo por uso |
| Vector DB | ChromaDB | Pinecone, Weaviate | Embebido, corre en RPi4, sin servidor |
