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

# Schema de Frontmatter YAML

Define la estructura de metadatos que el bot genera automáticamente para cada nota creada en Obsidian. El usuario nunca escribe YAML manualmente.

---

## Schema base (todos los tipos de nota)

```yaml
---
title: "Título descriptivo de la nota"
date_created: "2025-01-15T14:30:00"   # ISO 8601, generado por el bot
date_modified: "2025-01-15T14:30:00"  # ISO 8601, actualizado en cada edición
type: project-note                     # Ver tipos válidos abajo
tags: [tag1, tag2]                     # Generados por LLM, kebab-case
source: telegram                       # "telegram" para notas de usuario, "system" para auto-generadas (ej: _index.md)
media_type: text                       # text | audio | image | link | document — origen del contenido, seteado automáticamente
status: active                         # valores dependen del type — ver tabla abajo
source_file: "archivo.pdf"            # opcional — nombre del archivo original cuando el input es un documento adjunto
source_url: "https://..."             # opcional — URL original cuando el input es un link
---
```

`source_file` y `source_url` son mutuamente opcionales y pueden coexistir (ej: un paper del que se tiene el PDF y el link).

---

## Valores de `status` por tipo

Cada tipo tiene su propio ciclo de vida. No existe `status: archived` — archivar es mover el archivo a `05-Archive/`. El status refleja el estado dentro del ciclo de vida del tipo, no la ubicación en el vault.

`pending-classification` es el único valor compartido: cualquier tipo puede tenerlo si el LLM no respondió (modo degradado).

| Tipo | Valores de `status` | Default |
|---|---|---|
| `project-note` | `active`, `pending-classification` | `active` |
| `paper` | `unread`, `reading`, `read`, `pending-classification` | `unread` |
| `task` | `pending`, `in-progress`, `done`, `pending-classification` | `pending` |
| `idea` | `raw`, `developing`, `mature`, `pending-classification` | `raw` |
| `inbox` | `pending-classification` | `pending-classification` |
| `project-index` | `active`, `on-hold`, `completed` | `active` |

---

## Tipos de nota (`type`)

| Valor | Carpeta destino | Descripción |
|---|---|---|
| `project-note` | `01-Projects/{proyecto}/{seccion}/` | Nota dentro de un proyecto |
| `paper` | `01-Projects/{proyecto}/papers/` o `03-Resources/` | Paper académico (en Resources si no tiene proyecto asociado) |
| `task` | `02-Areas/{area}/` | Tarea (el área determina la carpeta destino; con `due_date`/`scheduled` opcionales → Google Calendar) |
| `idea` | `02-Areas/{area}/` | Idea sin proyecto definido — se promueve a proyecto o se descarta |
| `inbox` | `00-Inbox/` | Sin clasificar, requiere revisión |
| `project-index` | `01-Projects/{proyecto}/` | Nota índice de proyecto — auto-generada, no clasificada por el LLM |

---

## Campos adicionales por tipo

### `project-note`
```yaml
---
type: project-note
project: "tesis"                        # nombre del proyecto (carpeta)
section: "experimentos"                 # sección dentro del proyecto
summary: "Resumen generado por LLM"    # para notas largas
related: ["[[otra-nota]]"]             # sugeridos por ChromaDB, elegidos por el usuario
---
```

### `paper`
```yaml
---
type: paper
status: unread                          # unread | reading | read
project: "tesis"                        # opcional — sin proyecto va a 03-Resources/
section: "papers"                       # opcional — solo si tiene proyecto (default: "papers")
authors: ["Apellido, N.", "Apellido, N."]
year: 2024
url: "https://arxiv.org/abs/XXXX.XXXXX"
doi: "10.XXXX/..."                      # opcional
relevance: "Para qué sirve este paper" # provisto por el usuario o inferido por LLM
context: "Contexto adicional de uso"   # opcional, ej: "comparar con modelo actual"
priority: medium                        # low | medium | high — inferido o explícito
tags: [cosmologia, machine-learning]
# Extraídos por Gemini del PDF completo:
contribution: "Qué aporta — nuevo modelo, benchmark, survey, etc."
methods: ["transformer", "contrastive-learning"]   # métodos/técnicas usadas
dataset: ["ImageNet", "COCO"]                      # opcional
conclusions: "Principales hallazgos y limitaciones reconocidas por los autores"
related: ["[[otra-nota]]", "[[paper-similar]]"]    # sugeridos por ChromaDB, elegidos por el usuario
---
```
> Paper con proyecto → `01-Projects/{proyecto}/papers/`
> Paper sin proyecto → `03-Resources/` (referencia suelta, no asociado a ningún proyecto)

### `task`
```yaml
---
type: task
status: pending                         # pending | in-progress | done
priority: medium                        # low | medium | high — inferido o explícito
area: "investigacion"                   # determina la carpeta destino (02-Areas/{area}/) — inferido por LLM
project: "tesis"                        # opcional, máximo un proyecto (string, no lista). Solo metadata — no cambia la ubicación ni la lista destino
due_date: "2025-02-01"                  # opcional — fecha límite, ISO 8601 (solo fecha)
scheduled: "2025-01-28T10:00:00"        # opcional — fecha/hora agendada en Calendar, seteado automáticamente al agendar
related: ["[[otra-nota]]"]             # sugeridos por ChromaDB, elegidos por el usuario
---
```
> Las tasks se ubican en `02-Areas/{area}/`. El campo `project` es metadata, no determina la carpeta destino.
> Todas las tasks se sincronizan a la lista única `ADSO` en Google Tasks.
> Si la tarea tiene fecha/hora explícita, va además a Google Calendar.
> Si no tiene fecha, va solo a Google Tasks.

### `idea`
```yaml
---
type: idea
status: raw                             # raw | developing | mature
area: "investigacion"                   # opcional — determina la carpeta destino (02-Areas/{area}/). Si no hay área clara → 00-Inbox/
priority: low                           # low | medium | high — inferido o explícito
related: ["[[nota-relacionada]]"]       # opcional
---
```

### `_index.md` (nota índice de proyecto)

Cada proyecto tiene un `_index.md` en su raíz. Es la única nota que no se crea por captura de mensaje — se genera automáticamente al crear un proyecto via `/gestión` o cuando el LLM clasifica una nota en un proyecto nuevo. El usuario puede editarlo después via el bot (modo edición por Telegram).

```yaml
---
type: project-index
title: "Tesis doctoral"
date_created: "2025-01-01"
date_modified: "2025-01-15"
status: active                          # active | on-hold | completed
goal: "Investigar X para lograr Y"     # objetivo concreto, una línea
sections: [introduccion, experimentos, trabajos-futuros, papers]
tags: [tesis, doctorado]
source: system                          # auto-generado por el bot, no desde un mensaje de Telegram
---
```

El body del `_index.md` es Markdown libre. ADSO genera un template inicial con:

```markdown
# {title}

## Objetivo
{goal expandido — 1-2 párrafos generados por el LLM a partir del input del usuario}

## Secciones
- [[introduccion/]] — {descripción breve}
- [[experimentos/]] — {descripción breve}
- [[papers/]] — {descripción breve}

## Estado
- Creado: {date_created}
- Notas: {count} (actualizado por el reporte semanal)
```

El usuario puede agregar lo que quiera al body. ADSO solo modifica el frontmatter (`date_modified`, `status`, `sections` si se agregan nuevas).

---

## Notas de implementación

- El LLM recibe el contenido crudo y devuelve el frontmatter completo + cuerpo de la nota en JSON estructurado
- El bot parsea el JSON y escribe el archivo `.md` con el YAML correspondiente
- Los `tags` se generan en kebab-case, en el idioma del contenido
- El bot actualiza `date_modified` al editar notas existentes
- `relevance` y `context` en papers pueden ser provistos por el usuario o inferidos por el LLM del lenguaje del mensaje
- **Prioridad:** el LLM infiere `priority` del lenguaje del mensaje. La prioridad explícita del usuario siempre gana sobre la inferida. Si no hay señal clara, sugiere `medium` y pregunta. Solo aplica a tipos accionables: `task`, `paper`, `idea`.

---

## Consultas Dataview de ejemplo

**Tareas pendientes por prioridad:**
```dataview
TABLE date_created, priority, area, project
FROM "02-Areas"
WHERE type = "task" AND status = "pending"
SORT choice(priority, "high", 1, "medium", 2, "low", 3) ASC
```

**Papers pendientes de leer:**
```dataview
TABLE authors, year, priority, relevance
FROM "01-Projects"
WHERE type = "paper" AND status = "unread"
SORT choice(priority, "high", 1, "medium", 2, "low", 3) ASC, year DESC
```

**Papers de la tesis:**
```dataview
TABLE authors, year, relevance
FROM "01-Projects/tesis/papers"
SORT year DESC
```

**Ideas sin desarrollar:**
```dataview
LIST
FROM "02-Areas"
WHERE type = "idea" AND status = "raw"
```

**Todo lo anotado esta semana:**
```dataview
TABLE type, project, section
FROM "01-Projects" OR "02-Areas" OR "03-Resources"
WHERE date_created >= date(today) - dur(7 days)
SORT date_created DESC
```
