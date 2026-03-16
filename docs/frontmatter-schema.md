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
source: telegram                       # Siempre "telegram"
status: active                         # active | archived
---
```

---

## Tipos de nota (`type`)

| Valor | Carpeta destino | Descripción |
|---|---|---|
| `project-note` | `01-Projects/{proyecto}/{seccion}/` | Nota dentro de un proyecto |
| `paper` | `01-Projects/{proyecto}/papers/` | Paper académico |
| `task` | `02-Areas/tareas/` | Tarea sin fecha (con fecha → Google Calendar) |
| `idea` | `03-Resources/ideas/` | Idea sin proyecto definido |
| `inbox` | `00-Inbox/` | Sin clasificar, requiere revisión |

---

## Campos adicionales por tipo

### `project-note`
```yaml
---
type: project-note
project: "tesis"                        # nombre del proyecto (carpeta)
section: "experimentos"                 # sección dentro del proyecto
media_type: text                        # text | audio | image | link
summary: "Resumen generado por LLM"    # para notas largas
---
```

### `paper`
```yaml
---
type: paper
project: "tesis"
section: "papers"
authors: ["Apellido, N.", "Apellido, N."]
year: 2024
url: "https://arxiv.org/abs/XXXX.XXXXX"
doi: "10.XXXX/..."                      # opcional
relevance: "Para qué sirve este paper" # escrito por el usuario o inferido por LLM
tags: [cosmologia, machine-learning]
---
```

### `task`
```yaml
---
type: task
status: pending                         # pending | in-progress | done
priority: medium                        # low | medium | high
project: "tesis"                        # opcional
---
```
> Si la tarea tiene fecha/hora explícita, va a Google Calendar en lugar del vault.

### `idea`
```yaml
---
type: idea
status: raw                             # raw | developing | mature
related: ["[[nota-relacionada]]"]       # opcional
---
```

### `_index.md` (nota índice de proyecto)
```yaml
---
type: project-index
title: "Tesis doctoral"
created: "2025-01-01"
status: active                          # active | on-hold | completed
goal: "Descripción del objetivo"
sections: [introduccion, experimentos, trabajos-futuros, papers]
---
```

---

## Notas de implementación

- El LLM recibe el contenido crudo y devuelve el frontmatter completo + cuerpo de la nota en JSON estructurado
- El bot parsea el JSON y escribe el archivo `.md` con el YAML correspondiente
- Los `tags` se generan en kebab-case, en el idioma del contenido
- El bot actualiza `date_modified` al editar notas existentes
- El campo `relevance` en papers puede ser provisto por el usuario al momento de enviar ("este paper me sirve para X") o inferido del contenido

---

## Consultas Dataview de ejemplo

**Tareas pendientes:**
```dataview
TABLE date_created, priority, project
FROM "02-Areas/tareas"
WHERE status = "pending"
SORT priority DESC
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
FROM "03-Resources/ideas"
WHERE status = "raw"
```

**Todo lo anotado esta semana:**
```dataview
TABLE type, project, section
FROM "01-Projects" OR "02-Areas" OR "03-Resources"
WHERE date_created >= date(today) - dur(7 days)
SORT date_created DESC
```
