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
# ─── RAG — Consultas en lenguaje natural ────────────────────────────────────
rag:
  similarity_threshold: 0.75  # umbral mínimo para incluir una nota en el contexto
  max_results: 10             # máximo de notas a pasar al LLM como contexto
  max_expansion_depth: 2      # profundidad máxima en expansión desde nodo (1 = solo directas, 2 = un grado más, etc.)

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
  model_dir: /app/data/whisper     # directorio de descarga/caché del modelo (debe ser escribible por el proceso)
  language: es                     # idioma fijo para transcripción (evita detección automática, mejora velocidad)
                                   # null para auto-detect (útil si se mezclan idiomas)

# ─── Re-indexado nocturno ──────────────────────────────────────────────────
reindex:
  enabled: true
  time: "03:00"                    # hora local del servidor (formato HH:MM)

# ─── Sync (Calendar + Tasks) ──────────────────────────────────────────────
sync:
  interval_minutes: 30           # intervalo del cron que reconcilia Calendar y Tasks con el vault

# ─── Backup (Git) ─────────────────────────────────────────────────────────
backup:
  enabled: true                  # false para deshabilitar — útil si el vault no tiene repo git con remote
  debounce_seconds: 30           # esperar N segundos sin nuevas escrituras antes de commit+push

# ─── Documentos adjuntos ──────────────────────────────────────────────────
documents:
  max_size_mb: 20             # archivos más grandes se rechazan con mensaje al usuario

# ─── LLM ────────────────────────────────────────────────────────────────────
llm:
  degraded_retry_minutes: 30  # intervalo del cron que reintenta clasificar inbox pendiente
  disambiguation_threshold: 0.7  # confidence del LLM por debajo de este valor → bot pregunta con botones en vez de asumir

# ─── Reporte semanal ─────────────────────────────────────────────────────────
weekly_report:
  enabled: true
  day: friday                  # monday | tuesday | wednesday | thursday | friday | saturday | sunday
  time: "12:00"                # hora local del servidor (formato HH:MM)
  sections:
    notes_summary: true        # notas creadas durante la semana, desglose por tipo
    most_active_project: true  # proyecto con más actividad
    papers_queue: true         # papers con read_status: unread, ordenados por prioridad
    inbox_suggestion: true     # ítem del inbox más relevante según actividad reciente de la semana
    tasks_summary: true        # tasks ADSO completadas vs pendientes de la semana
    stale_ideas: true          # ideas con status: raw hace más de stale_idea_days
    paper_suggestion: true     # sugerencia de paper a leer basada en similitud con actividad reciente
  stale_idea_days: 60          # días sin actividad para considerar una idea estancada

# ─── Google Tasks ────────────────────────────────────────────────────────────
tasks:
  debug: false                # true: notifica por Telegram también en push exitoso a Google Tasks
                              # útil para verificar que la integración funciona durante testing
                              # en producción dejar en false (solo notifica en fallos)

# ─── Watcher de vault ────────────────────────────────────────────────────────
watcher:
  debug: false                # true: notifica por Telegram cada cambio externo detectado en el vault
                              # útil para verificar que el watcher funciona durante testing
                              # en producción dejar en false (los cambios se reindexan silenciosamente)
```

---

## Claves desconocidas

Una clave que el loader no reconoce **no aborta el arranque**: se ignora y se
loguea a `WARNING` con su ruta exacta (`weekly_report.include`,
`llm.typo_que_no_existe`). El bot es el path de captura del usuario, así que un
typo en el YAML no puede dejarlo sin arrancar — pero tampoco puede pasar
inadvertido.

Existe porque pasó: el `config.yaml` desplegado declaraba
`weekly_report.include:` mientras el loader lee `weekly_report.sections:`, y la
clave se descartaba en silencio (I2 de `docs/audit-2026-07-31.md`). Los tests
`TestClavesDesconocidas` en `tests/unit/test_config.py` cargan tanto
`config.yaml` como `config.yaml.example` y fallan si alguno vuelve a driftear.

Para inspeccionarlo desde código: `load_settings(...).unknown_keys`.

Desde el lote 3 (#45C) el reporte cubre también `vault_seed`, que era la única
sección que no lo hacía: `_build_vault_seed` arma su dataclass a mano (valida
`description` por ítem) en vez de delegar en `_build_section`, así que
`load_settings` nunca le pasaba la lista de claves ignoradas. Un `proyectos:` en
vez de `projects:` sembraba un vault vacío sin decir nada.

### Tipos que se validan al arrancar (#45)

El loader falla ruidosamente ante una config mal escrita. Tres casos que hasta el
lote 3 no fallaban y **cambiaban el comportamiento en silencio**:

| Clave | Formas válidas | Qué pasaba antes |
|---|---|---|
| `vault.exclude_dirs` | lista de strings | Un string suelto cargaba sin error, pero el chequeo de exclusión pasaba a ser un test de substring: dejaba de excluir lo que debía y excluía cualquier carpeta cuyo nombre fuera substring de ese string |
| `weekly_report.sections` | mapa `{nombre: bool}`, lista de nombres, o ausente | Un tipo externo (string, número) se asignaba verbatim y llegaba así a los reporters. El caso caro es el mapa con valor no-bool: `papers_queue: "false"` es un string, y un string no vacío es truthy — la sección quedaba **encendida justo cuando el usuario la apagó** |
| `vault_seed.*` | `projects`, `areas` | Ver arriba |

Todos lanzan `ConfigError` desde `load_settings`, con la clave nombrada en el
mensaje. Nada de validadores diferidos: si el bot arranca, la config es válida.

Una sección que no sea un mapa de claves (ej: una lista) sí es `ConfigError` —
ahí no hay ambigüedad sobre la intención. Eso incluye a `vault_seed`, que tiene
su propio constructor: escribirla como lista es la confusión natural (sus hijos
`projects` y `areas` **sí** son listas), y antes llegaba al `data.get()` y mataba
el arranque con un `AttributeError` crudo que no nombraba la clave. Hoy da
`ConfigError` diciendo qué se esperaba y qué se recibió.

## Las horas van entre comillas

`reindex.time` y `weekly_report.time` deben escribirse **entre comillas**:

```yaml
reindex:
  time: "03:00"     # ✅
weekly_report:
  time: "12:00"     # ✅
  # time: 12:00     # ❌ el YAML lo lee como el número 720
```

Es una trampa real del formato, no una convención de estilo. PyYAML resuelve un
escalar sin comillas que contiene dos puntos como **sexagesimal** (YAML 1.1), así
que `12:00` no llega al bot como el string `"12:00"` sino como el entero `720`
(12 × 60). Lo insidioso es que **el bug es intermitente por hora**: un cero
inicial rompe el resolver sexagesimal, así que `03:00` sin comillas funciona
igual y solo fallan las horas de `10:00` en adelante. El ejemplo de la doc
andaba y el valor del usuario no.

El loader lo rechaza —adivinar la intención de un número es peor que fallar—
pero nombrando la causa real en vez de reportar un valor que no aparece en el
archivo:

```
reindex.time: el YAML leyó la hora como el número 720.
Falta escribirla entre comillas: time: "12:00"
```

Ambas horas se validan al cargar la config, no al programar el job: así un valor
mal escrito da `ConfigError` como el resto de la config en vez de matar el
arranque con un traceback crudo de `strptime` (G9 de
`docs/audit-2026-07-31.md`).

## Campos declarados pero aún sin consumir

Se cargan y se validan, pero **ningún módulo los lee todavía** (I1 de
`docs/audit-2026-07-31.md`). Ajustarlos no cambia el comportamiento del bot:

| Campo | Espera a |
|---|---|
| `weekly_report.*` (sección entera, incl. `stale_idea_days`) | job del reporte semanal (`improvements-2026-07.md` §2.2) |
| `sync.interval_minutes` | cron de reconciliación con Google Tasks (§5.2) |
| `rag.max_expansion_depth` | expansión desde nodo (Fase 7 completa) |

Las claves `llm.max_web_tokens` y `llm.max_paper_tokens` **se eliminaron**
(2026-09): el truncado real son constantes por caracteres en
`document_extractor.py` y no había plan de hacerlo configurable. Si aparecen en
un `config.yaml` viejo se ignoran con el WARNING de clave desconocida.

La sección `content_extraction` **se eliminó** (2026-08-13, I1 de
`docs/audit-2026-07-31.md`): era la única sin fase asociada y su validación
podía abortar el arranque comparando contra `trafilatura`, que ni siquiera es
dependencia del proyecto. Gemini lee las URLs directamente y no hay motor
alternativo previsto. Si aparece en un `config.yaml` viejo se ignora con el
WARNING de clave desconocida.

## Notas

- `config.yaml` debe existir. Si falta, el bot falla con error claro al arrancar.
- Cambios en `config.yaml` requieren reiniciar el bot (`docker compose restart adso-bot`).
- Los valores de `.env` tienen precedencia sobre `config.yaml` para los parámetros que aparezcan en ambos (compatibilidad con despliegues que ya usan solo `.env`).
