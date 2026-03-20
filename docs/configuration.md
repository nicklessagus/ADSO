```
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
```

# Configuración

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
    - paper_suggestion  # sugerencia de paper a leer basada en similitud con actividad reciente

  stale_idea_days: 60     # días sin actividad para marcar una idea como estancada

# ─── RAG — Consultas en lenguaje natural ────────────────────────────────────
rag:
  similarity_threshold: 0.75  # umbral mínimo para incluir una nota en el contexto
  max_results: 10             # máximo de notas a pasar al LLM como contexto

# ─── Links automáticos ──────────────────────────────────────────────────────
links:
  similarity_threshold: 0.82   # umbral mínimo para sugerir un [[wikilink]]
  max_suggestions: 5           # máximo de links sugeridos por nota nueva

# ─── Siembra inicial del vault ──────────────────────────────────────────────
# Proyectos y áreas creados en el primer arranque si no existen. Opcional.
# Si se incluye un ítem sin `description`, el bot falla al iniciar con error claro.
vault_seed:
  projects:
    - name: tesis
      description: "Papers de doctorado, experimentos de ML, escritura académica y revisión bibliográfica."
    - name: adso
      description: "Desarrollo del bot ADSO: diseño, código, decisiones técnicas, tests."
  areas:
    - name: docencia
      description: "Preparación de clases, guías de ejercicios, consultas de alumnos, material didáctico."
    - name: investigacion
      description: "Tareas e ideas de investigación sin proyecto asignado. Lectura general, ideas exploratorias."
    - name: personal
      description: "Trámites, salud, finanzas, vida personal."

# ─── Vault ──────────────────────────────────────────────────────────────────
vault:
  exclude_dirs:               # carpetas excluidas del índice de embeddings
    - "05-Archive"
    - ".obsidian"
    - ".trash"

# ─── Transcripción (Fase 3) ────────────────────────────────────────────────
whisper:
  model: base                      # tiny | base — modelos recomendados para RPi4 (< 200MB RAM)

# ─── Extracción de contenido web ───────────────────────────────────────────
content_extraction:
  engine: gemini                 # gemini | trafilatura
                                 # gemini: Gemini lee la URL directamente (producción, default)
                                 # trafilatura: fetch local con trafilatura Python (desarrollo/testing)

# ─── Re-indexado nocturno ──────────────────────────────────────────────────
reindex:
  enabled: true
  time: "03:00"                    # hora local del servidor (formato HH:MM)

# ─── Sync (Calendar + Tasks) ──────────────────────────────────────────────
sync:
  interval_minutes: 30           # intervalo del cron que reconcilia Calendar y Tasks con el vault

# ─── Backup (Git) ─────────────────────────────────────────────────────────
backup:
  debounce_seconds: 30           # esperar N segundos sin nuevas escrituras antes de commit+push

# ─── Documentos adjuntos ──────────────────────────────────────────────────
documents:
  max_size_mb: 20             # archivos más grandes se rechazan con mensaje al usuario

# ─── LLM ────────────────────────────────────────────────────────────────────
llm:
  max_web_tokens: 8000        # truncado de contenido web antes de enviar al LLM
  max_paper_tokens: 128000    # truncado de PDFs académicos
  degraded_retry_minutes: 30  # intervalo del cron que reintenta clasificar inbox pendiente
```

---

## Notas

- `config.yaml` debe existir. Si falta, el bot falla con error claro al arrancar.
- Cambios en `config.yaml` requieren reiniciar el bot (`docker compose restart adso-bot`).
- Los valores de `.env` tienen precedencia sobre `config.yaml` para los parámetros que aparezcan en ambos (compatibilidad con despliegues que ya usan solo `.env`).
