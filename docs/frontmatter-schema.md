# Schema de Frontmatter YAML

Define la estructura de metadatos que el bot genera para cada nota creada en Obsidian.

---

## Schema base (todos los tipos de nota)

```yaml
---
title: "Título descriptivo de la nota"
date_created: "2025-01-15T14:30:00"   # ISO 8601, generado por el bot
date_modified: "2025-01-15T14:30:00"  # ISO 8601, actualizado en cada edición
type: resource                         # Ver tipos válidos abajo
tags: [tag1, tag2]                     # Generados por el bot via LLM
source: telegram                       # Canal de origen
status: active                         # active | archived
---
```

---

## Tipos de nota (`type`)

| Valor | Carpeta destino | Descripción |
|---|---|---|
| `inbox` | `00-Inbox/` | Sin clasificar, requiere revisión |
| `project` | `01-Projects/` | Proyecto activo |
| `area` | `02-Areas/` | Área de responsabilidad |
| `resource` | `03-Resources/` | Conocimiento de referencia |
| `task` | `02-Areas/Tasks/` | Tarea accionable |
| `idea` | `03-Resources/Ideas/` | Idea para desarrollar |
| `daily` | `02-Areas/Daily/` | Nota diaria |

---

## Campos adicionales por tipo

### `project`
```yaml
---
type: project
goal: "Descripción del objetivo"
deadline: "2025-03-01"           # opcional
status: active                   # active | on-hold | completed
related: ["[[nota-relacionada]]"]
---
```

### `task`
```yaml
---
type: task
status: pending                  # pending | in-progress | done
priority: medium                 # low | medium | high
project: "[[proyecto-asociado]]" # opcional
due: "2025-02-01"                # opcional
---
```

### `resource`
```yaml
---
type: resource
url: "https://..."               # si proviene de un link
media_type: text                 # text | image | audio | link
summary: "Resumen generado por LLM"
---
```

### `idea`
```yaml
---
type: idea
status: raw                      # raw | developing | mature
related: []
---
```

---

## Notas de implementación

- Todos los campos son generados automáticamente por el bot. El usuario no escribe YAML manualmente.
- El LLM recibe el contenido crudo y devuelve el frontmatter completo + cuerpo de la nota.
- Los `tags` se generan en minúsculas, sin espacios (usar guiones), en español o inglés según el contenido.
- El bot puede actualizar `date_modified` y `status` en notas existentes.

---

## Consultas Dataview de ejemplo

Todas las tareas pendientes:
```dataview
TABLE date_created, priority, project
FROM "02-Areas/Tasks"
WHERE status = "pending"
SORT priority DESC
```

Ideas sin desarrollar:
```dataview
LIST
FROM "03-Resources/Ideas"
WHERE status = "raw"
```

Proyectos activos:
```dataview
TABLE goal, deadline, status
FROM "01-Projects"
WHERE status = "active"
SORT deadline ASC
```
