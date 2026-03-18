# Configuración de ADSO

Parámetros que el usuario puede ajustar sin tocar el código, editando `config.yaml` en la raíz del proyecto. El bot carga este archivo al iniciar.

Separación de responsabilidades:
- **`.env`** — secretos y credenciales (tokens, API keys, paths). Nunca se comparte.
- **`config.yaml`** — preferencias de comportamiento del bot. Puede versionarse.

---

## `config.yaml` — referencia completa

```yaml
# ─── Reporte semanal ────────────────────────────────────────────────────────
weekly_report:
  enabled: true
  day: friday          # lunes=monday ... domingo=sunday
  time: "18:00"        # hora local del servidor (formato HH:MM)
  include:
    - notes_created     # notas creadas en la semana (desglose por tipo)
    - active_project    # proyecto más activo
    - new_methods       # métodos que aparecen por primera vez en papers
    - paper_queue       # papers pendientes ordenados por prioridad
    - stale_ideas       # ideas en status:raw más de N días (ver stale_idea_days)
    - tasks_review      # tasks ADSO: completadas vs pendientes de la semana

stale_idea_days: 60     # días sin actividad para marcar una idea como estancada

# ─── Google Calendar ────────────────────────────────────────────────────────
calendar:
  sync_interval_minutes: 30  # intervalo del cron que reconcilia Calendar → vault

# ─── RAG — Consultas en lenguaje natural ────────────────────────────────────
rag:
  similarity_threshold: 0.75  # umbral mínimo para incluir una nota en el contexto
  max_results: 10             # máximo de notas a pasar al LLM como contexto

# ─── Links automáticos ──────────────────────────────────────────────────────
links:
  similarity_threshold: 0.82   # umbral mínimo para sugerir un [[wikilink]]
  max_suggestions: 5           # máximo de links sugeridos por nota nueva

# ─── Vault ──────────────────────────────────────────────────────────────────
vault:
  exclude_dirs:               # carpetas excluidas del índice de embeddings
    - "04-Archive"
    - ".obsidian"
    - ".trash"

# ─── LLM ────────────────────────────────────────────────────────────────────
llm:
  max_web_tokens: 8000        # truncado de contenido web antes de enviar al LLM
  max_paper_tokens: 128000    # truncado de PDFs académicos
  degraded_retry_minutes: 30  # intervalo del cron que reintenta clasificar inbox pendiente
```

---

## Notas

- Si `config.yaml` no existe, el bot usa los valores por defecto documentados arriba.
- Cambios en `config.yaml` requieren reiniciar el bot (`docker compose restart adso-bot`).
- Los valores de `.env` tienen precedencia sobre `config.yaml` para los parámetros que aparezcan en ambos (compatibilidad con despliegues que ya usan solo `.env`).
