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
type: note                              # Ver tipos válidos abajo
tags: [tag1, tag2]                     # Generados por LLM, kebab-case
source: telegram                       # "telegram" para notas de usuario, "system" para auto-generadas (ej: _index.md)
media_type: text                       # text | audio | image | link | document — origen del contenido, seteado automáticamente
status: active                         # valores dependen del type — ver tabla abajo
source_file: "archivo.pdf"            # opcional — nombre del archivo original cuando el input es un documento adjunto
source_url: "https://..."             # opcional — URL original cuando el input es un link
read_status: unread                    # opcional — unread | reading | read (ver sección read_status abajo)
---
```

`source_file` y `source_url` son mutuamente opcionales y pueden coexistir (ej: un paper del que se tiene el PDF y el link).

---

## Valores de `status` por tipo

Cada tipo tiene su propio ciclo de vida. No existe `status: archived` — archivar es mover el archivo a `05-Archive/`. El status refleja el estado dentro del ciclo de vida del tipo, no la ubicación en el vault.

`pending-classification` es el único valor compartido: cualquier tipo puede tenerlo si el LLM no respondió (modo degradado).

| Tipo | Valores de `status` | Default |
|---|---|---|
| `note` | `active`, `pending-classification` | `active` |
| `task` | `pending`, `in-progress`, `done`, `pending-classification` | `pending` |
| `idea` | `raw`, `developing`, `mature`, `pending-classification` | `raw` |
| `inbox` | `pending-classification` | `pending-classification` |
| `project-index` | `active`, `on-hold`, `completed` | `active` |

---

## Tipos de nota (`type`)

| Valor | Carpeta destino | Descripción |
|---|---|---|
| `note` | `01-Projects/{proyecto}/{seccion}/` si tiene proyecto, `03-Resources/` si es referencia suelta | Nota de contenido general (incluye papers y cualquier material de referencia) |
| `task` | `02-Areas/{area}/` | Tarea (el área determina la carpeta destino; con `due_date`/`scheduled` opcionales → Google Calendar) |
| `idea` | `02-Areas/{area}/` | Idea sin proyecto definido — se promueve a proyecto o se descarta |
| `inbox` | `00-Inbox/` | Sin clasificar, requiere revisión |
| `project-index` | `01-Projects/{proyecto}/` | Nota índice de proyecto — auto-generada, no clasificada por el LLM |

---

## Campos adicionales por tipo

### `note`
```yaml
---
type: note
project: "tesis"                        # nombre del proyecto (carpeta) — opcional, sin proyecto va a 03-Resources/
section: "experimentos"                 # sección dentro del proyecto — opcional, solo si tiene proyecto
summary: "Resumen generado por LLM"    # para notas largas
related: ["[[otra-nota]]"]             # sugeridos por ChromaDB, elegidos por el usuario
---
```

### Campos opcionales para contenido académico

Cuando el pipeline de extracción detecta contenido académico (papers, artículos, preprints), estos campos se agregan al frontmatter de la nota. No definen un tipo separado — un paper es una `note` con tag `#paper` y estos campos poblados.

Los papers se identifican por la presencia del tag `#paper` y/o la presencia de estos campos en el frontmatter.

```yaml
---
type: note
tags: [paper, cosmologia, machine-learning]
read_status: unread                     # unread | reading | read — solo si el usuario eligió "guardar para después"
authors: ["Apellido, N.", "Apellido, N."]
year: 2024
url: "https://arxiv.org/abs/XXXX.XXXXX"
doi: "10.XXXX/..."                      # opcional
relevance: "Para qué sirve este paper" # provisto por el usuario o inferido por LLM
context: "Contexto adicional de uso"   # opcional, ej: "comparar con modelo actual"
priority: medium                        # low | medium | high — inferido o explícito
# Extraídos por Gemini del PDF completo:
contribution: "Qué aporta — nuevo modelo, benchmark, survey, etc."
methods: ["transformer", "contrastive-learning"]   # métodos/técnicas usadas
dataset: ["ImageNet", "COCO"]                      # opcional
conclusions: "Principales hallazgos y limitaciones reconocidas por los autores"
related: ["[[otra-nota]]", "[[paper-similar]]"]    # sugeridos por ChromaDB, elegidos por el usuario
---
```

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
description: "Papers de doctorado, experimentos de ML, escritura académica."  # scope de clasificación — requerido
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

### `_index.md` (nota índice de área)

Cada área tiene un `_index.md` en su raíz. Se genera automáticamente al crear un área. La `description` es requerida — el bot la pide al crear y no permite omitirla.

```yaml
---
type: area-index
title: "Docencia"
date_created: "2025-01-01"
date_modified: "2025-01-15"
description: "Preparación de clases, guías de ejercicios, consultas de alumnos, material didáctico."  # requerido — usado por el LLM para clasificar
source: system
---
```

> `goal` no aplica a áreas — las áreas no tienen un objetivo puntual, tienen un scope continuo. Solo `description`.

---

## `read_status`

Campo opcional que indica si el contenido fue revisado/leído. Se aplica a cualquier nota con contenido externo (papers, links, archivos, imágenes). No aplica a notas de texto propio (`task`, `idea`, notas libres).

| Valor | Significado |
|---|---|
| `unread` | Guardado pero no revisado todavía |
| `reading` | En proceso de lectura (principalmente para papers y documentos largos) |
| `read` | Revisado / leído |

**Cuándo se setea:**
- Cualquier input (paper, link, archivo, imagen, texto): `unread` cuando el usuario elige "guardar para después" en el flujo de captura
- No se setea automáticamente por tipo de contenido — es siempre una decisión explícita del usuario

**Cómo se actualiza:**
- El usuario dice "marqué como leído el paper X" → bot actualiza `read_status: read`
- El usuario dice "estoy leyendo X" → `reading`
- Desde el bot al listar inbox: botón `[Marcar como leído]` junto a cada ítem

**Flujo "guardar para después":**

Para links, archivos e imágenes, el bot ofrece dos opciones al recibirlos:

```
[Procesar ahora]        → flujo normal de extracción + clasificación LLM
[Guardar para después]  → guarda en 00-Inbox/ con read_status: unread
                          extracción mínima: título/nombre + URL o archivo + fecha
                          sin clasificación LLM hasta que el usuario lo revise
```

Al revisar después, el usuario elige:
```
[Procesarlo]  → flujo normal de clasificación → mueve a destino correcto
[Borrarlo]    → confirmación + borrado
```

---

## `## Notas personales` — sección en el body

Toda nota con `read_status` incluye en su body una sección vacía al crear:

```markdown
## Notas personales

```

El usuario la completa después de leer/revisar el contenido — es su interpretación propia, distinta de los campos auto-generados:

| Campo/sección | Quién lo llena | Qué es |
|---|---|---|
| `contribution`, `methods`, `conclusions` | LLM (del paper) | Lo que dice el autor |
| `relevance`, `context` | Usuario/LLM al guardar | Por qué lo guardaste |
| `## Notas personales` | Usuario después de leer | Tu interpretación — cómo te sirve, con qué linkear, qué aplicar |

El bot puede ayudar a redactar esta sección: si el usuario manda "este paper me sirve para el capítulo 3, linkear con [[baseline-cnn]]", el bot formatea y escribe en esa sección.

---

## Notas de implementación

- El LLM recibe el contenido crudo y devuelve el frontmatter completo + cuerpo de la nota en JSON estructurado
- El bot parsea el JSON y escribe el archivo `.md` con el YAML correspondiente
- Los `tags` se generan en kebab-case, en el idioma del contenido
- El bot actualiza `date_modified` al editar notas existentes
- `relevance` y `context` en papers pueden ser provistos por el usuario o inferidos por el LLM del lenguaje del mensaje
- **Prioridad:** el LLM infiere `priority` del lenguaje del mensaje. La prioridad explícita del usuario siempre gana sobre la inferida. Si no hay señal clara, sugiere `medium` y pregunta. Solo aplica a tipos accionables: `task`, `idea`.

---

## Consultas Dataview de ejemplo

**Papers sin leer por prioridad:**
```dataview
TABLE authors, year, priority, relevance
FROM "01-Projects" OR "03-Resources"
WHERE contains(tags, "paper") AND read_status = "unread"
SORT choice(priority, "high", 1, "medium", 2, "low", 3) ASC, year DESC
```

**Todo el inbox pendiente de revisar:**
```dataview
TABLE media_type, date_created, source_url, source_file
FROM "00-Inbox"
WHERE read_status = "unread"
SORT date_created ASC
```

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
FROM "01-Projects" OR "03-Resources"
WHERE contains(tags, "paper") AND authors
SORT choice(priority, "high", 1, "medium", 2, "low", 3) ASC, year DESC
```

**Papers de la tesis:**
```dataview
TABLE authors, year, relevance
FROM "01-Projects/tesis"
WHERE contains(tags, "paper")
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
