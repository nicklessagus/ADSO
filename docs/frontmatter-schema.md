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
date_created: 2025-01-15T14:30:00     # ISO 8601, generado por el bot — sin comillas para tipo Date & time en Obsidian
date_modified: 2025-01-15T14:30:00    # ISO 8601, actualizado en cada edición — sin comillas
type: reference                         # Ver tipos válidos abajo
tags: [tag1, tag2]                     # Generados por LLM, kebab-case
source: telegram                       # "telegram" para notas de usuario, "system" para auto-generadas (ej: _index.md)
media_type: text                       # text | audio | image | link | document — origen del contenido, seteado automáticamente
status: active                         # Text enum — valores dependen del type — ver tabla abajo
source_file: "[[archivo.pdf]]"        # opcional — wikilink al archivo en 03-Resources/, clickeable en Properties de Obsidian
source_url: "https://..."             # opcional — URL original cuando el input es un link
read_status: unread                    # Text enum — unread | reading | read (ver sección read_status abajo)
---
```

`source_file` y `source_url` son mutuamente opcionales y pueden coexistir (ej: un paper del que se tiene el PDF y el link).

---

## Tipado nativo de Obsidian Properties

Reglas de serialización para máxima compatibilidad con la UI de Properties de Obsidian.

- **Fechas sin comillas (`Date & time` / `Date`):** Los campos `date_created`, `date_modified`, `due_date` y `scheduled` se escriben **sin comillas** en el YAML. Sin comillas, Obsidian los reconoce como tipo `Date & time` o `Date` y habilita el widget de calendario. Con comillas los trata como `Text` plano. Esto es **crítico** — el bot siempre genera estos campos sin comillas.
  ```yaml
  # Correcto — Obsidian reconoce como Date & time / Date
  date_created: 2025-01-15T14:30:00
  due_date: 2025-02-01

  # Incorrecto — Obsidian lo trata como Text
  date_created: "2025-01-15T14:30:00"
  ```

- **`source_file` como wikilink:** El valor usa formato `"[[nombre.pdf]]"` (con comillas dobles porque contiene `[[`). Esto permite que Obsidian lo muestre como link clickeable en Properties, apuntando al archivo en `03-Resources/`.
  ```yaml
  source_file: "[[paper.pdf]]"
  ```

- **Links en listas — siempre entre comillas dobles:** En campos `related`, los wikilinks dentro de arrays YAML **deben ir entre comillas dobles**. Sin ellas, el `[[` rompe el parseo de YAML y Obsidian muestra error en el frontmatter.
  ```yaml
  # Correcto
  related: ["[[otra-nota]]", "[[paper-similar]]"]

  # Incorrecto — error de parseo YAML
  related: [[[otra-nota]], [[paper-similar]]]
  ```

- **`project` y `area` como `Text` plano:** Contienen el nombre de la carpeta (ej: `tesis`, `investigacion`) y deben mantenerse como texto plano, **no** como wikilinks. Esto permite consultas y agrupaciones limpias en Dataview (`WHERE project = "tesis"`, `GROUP BY area`).

- **`read_status`, `priority` y `status` como `Text` (enum):** Mapean al tipo `Text` nativo de Obsidian, no a `Checkbox`. Son enums de múltiples estados y Obsidian ofrece autocompletado para sus valores en la UI de Properties cuando el tipo es `Text`.

---

## Valores de `status` por tipo

Cada tipo tiene su propio ciclo de vida. `status: archived` solo aplica a `project-index` — archivar un proyecto mueve la carpeta a `05-Archive/` y setea `status: archived` en el `_index.md`. Los demás tipos no usan este valor. El status refleja el estado dentro del ciclo de vida del tipo.

`pending-classification` es el único valor compartido: cualquier tipo puede tenerlo si el LLM no respondió (modo degradado) o si el LLM no pudo asignar destino. Las notas con este status son candidatas para reclasificación automática (cron) o manual (`/clasificar`).

| Tipo | Valores de `status` | Default |
|---|---|---|
| `reference` | `active`, `pending-classification` | `active` |
| `task` | `pending`, `in-progress`, `done`, `pending-classification` | `pending` |
| `idea` | `raw`, `implemented`, `discarded`, `pending-classification` | `raw` |
| `project-index` | `active`, `on-hold`, `completed`, `archived` | `active` |
| `area-index` | — (sin ciclo de vida) | — |

`pending-classification` está disponible para todos los tipos: indica que el LLM no respondió (modo degradado). El bot intentará reclasificar automáticamente.

---

## Tipos de nota (`type`)

| Valor | Carpeta destino | Descripción |
|---|---|---|
| `reference` | `01-Projects/{proyecto}/{seccion}/` si tiene proyecto, `02-Areas/{area}/` si tiene área, o el bot pregunta destino si no tiene ninguno | Nota de contenido general (incluye papers y cualquier material de referencia) |
| `task` | `01-Projects/{proyecto}/` si tiene proyecto, `02-Areas/{area}/` si tiene área, o `00-Inbox/` si no tiene ninguno | Tarea (proyecto > área > Inbox; con `due_date`/`scheduled` opcionales → Google Calendar) |
| `idea` | `01-Projects/{proyecto}/{seccion}/` si tiene proyecto, `02-Areas/{area}/` si tiene área, o el bot pregunta destino | Idea exploratoria — se promueve a proyecto o se descarta |

| `project-index` | `01-Projects/{proyecto}/` | Nota índice de proyecto — auto-generada, no clasificada por el LLM |
| `area-index` | `02-Areas/{area}/` | Nota índice de área — auto-generada, no clasificada por el LLM |

---

## Campos adicionales por tipo

### `reference`
```yaml
---
type: reference
project: tesis                          # Text plano — nombre del proyecto (carpeta) — opcional
section: experimentos                   # Text plano — sección dentro del proyecto — opcional, solo si tiene proyecto
area: docencia                          # Text plano — opcional, solo si no tiene proyecto. Determina carpeta destino (02-Areas/{area}/)
summary: "Resumen generado por LLM"    # para notas largas
related: ["[[otra-nota]]"]             # links siempre entre comillas dobles dentro del array
---
```
> Routing: con proyecto → `01-Projects/{proyecto}/{seccion}/`. Con área (sin proyecto) → `02-Areas/{area}/`. Sin proyecto ni área → el bot pregunta con botones: `[Elegir área]` `[Elegir proyecto]` `[Inbox]`. El PDF/binario siempre va a `03-Resources/` independientemente del destino de la nota.

### Campos opcionales para contenido académico

Cuando el pipeline de extracción detecta contenido académico (papers, artículos, preprints), estos campos se agregan al frontmatter de la nota. No definen un tipo separado — un paper es una `reference` con tag `#paper` y estos campos poblados.

Los papers se identifican por la presencia del tag `#paper` y/o la presencia de estos campos en el frontmatter.

```yaml
---
type: reference
tags: [paper, cosmology, machine-learning]        # generados por LLM, kebab-case, siempre en inglés. El LLM reutiliza tags existentes del vault (excluyendo 00-Inbox) antes de crear nuevos
read_status: unread                               # unread | reading | read — seteado por el usuario con [Ya lo leí] / [Lo quiero leer]
authors: ["Apellido, N.", "Apellido, N."]
year: 2024
url: "https://arxiv.org/abs/XXXX.XXXXX"
doi: "10.XXXX/..."                                # extraído localmente del PDF
keywords: ["time-series", "transformer", "self-supervised"]  # palabras clave del paper, idioma original
relevance: "Para qué sirve este paper"            # provisto por el usuario o inferido por LLM
context: "Contexto adicional de uso"              # opcional, ej: "comparar con modelo actual"
priority: medium                                  # Text enum — low | medium | high — inferido o explícito
# Extraídos localmente del PDF + estructurados por LLM:
contribution: "Qué aporta — nuevo modelo, benchmark, survey, etc."
methods: ["transformer", "contrastive-learning"]  # métodos/técnicas usadas
dataset: ["ImageNet", "COCO"]                     # opcional
conclusions: "Principales hallazgos y limitaciones reconocidas por los autores"
related: ["[[otra-nota]]", "[[paper-similar]]"]   # links siempre entre comillas dobles dentro del array
---
```

**Body de una nota de paper** — estructura fija generada por el LLM:

```markdown
> [!summary] AI Summary
> Síntesis en español más amplia que el abstract: qué hace el paper, cómo y qué concluye.
> Cada línea del summary generado por la IA empieza con "> ".

## Abstract
[Texto original del abstract, en el idioma del paper]

## Methods
[Sección de métodos extraída del paper, en el idioma original. Bloques de fórmulas ilegibles reemplazados por `> [mathematical content — see PDF]`]

## Conclusions
[Sección de conclusiones extraída del paper, en el idioma original]

## Personal Notes
```

**Regla de dos voces:** el callout `[!summary]` marca la voz del bot (contenido generado o sintetizado por la IA). Abstract, Methods y Conclusions van en Markdown estándar para preservar la voz del autor y diferenciarla visualmente. Las secciones en idioma original no se traducen para no introducir ruido en los embeddings multilingüe de ChromaDB.

**Modo degradado:** si el LLM no responde, el contenido crudo queda en `00-Inbox/` envuelto en un callout colapsable:

```markdown
> [!warning]- Modo degradado: Clasificación pendiente
> El LLM no respondió. Contenido original sin procesar:
> {texto original del usuario}
```

El cron de reclasificación extrae el contenido original del callout antes de enviarlo al LLM.

### `task`
```yaml
---
type: task
status: pending                         # Text enum — pending | in-progress | done
priority: medium                        # Text enum — low | medium | high — inferido o explícito
project: tesis                          # Text plano — opcional. Si presente, la tarea va a 01-Projects/{project}/ (gana sobre area)
area: investigacion                     # Text plano — carpeta destino si no hay proyecto (02-Areas/{area}/)
due_date: 2025-02-01                    # Date — sin comillas, ISO 8601 (solo fecha)
scheduled: 2025-01-28T10:00:00         # Date & time — sin comillas, ISO 8601
related: ["[[otra-nota]]"]             # links siempre entre comillas dobles dentro del array
---
```
> Routing de tasks: `project` gana sobre `area`. Con proyecto → `01-Projects/{proyecto}/`. Con área (sin proyecto) → `02-Areas/{area}/`. Sin ninguno → `00-Inbox/`.
> Todas las tasks se sincronizan a la lista única `ADSO` en Google Tasks.
> Si la tarea tiene fecha/hora explícita, va además a Google Calendar.
> Si no tiene fecha, va solo a Google Tasks.

### `idea`
```yaml
---
type: idea
status: raw                             # Text enum — raw | implemented | discarded
project: tesis                          # Text plano — opcional, determina carpeta destino (01-Projects/{proyecto}/)
section: brainstorm                     # Text plano — sección dentro del proyecto — opcional, solo si tiene proyecto
area: investigacion                     # Text plano — opcional, solo si no tiene proyecto. Determina carpeta destino (02-Areas/{area}/)
priority: low                           # Text enum — low | medium | high — inferido o explícito
related: ["[[nota-relacionada]]"]       # links siempre entre comillas dobles dentro del array
---
```

### `_index.md` (nota índice de proyecto)

Cada proyecto tiene un `_index.md` en su raíz. Es la única nota que no se crea por captura de mensaje — se genera automáticamente al crear un proyecto via `/gestión` o cuando el LLM clasifica una nota en un proyecto nuevo. El usuario puede editarlo después via el bot (modo edición por Telegram).

```yaml
---
type: project-index
title: "Tesis doctoral"
date_created: 2025-01-01               # Date — sin comillas
date_modified: 2025-01-15              # Date — sin comillas
status: active                          # Text enum — active | on-hold | completed
description: "Papers de doctorado, experimentos de ML, escritura académica."  # scope de clasificación — requerido
sections: [introduccion, experimentos, trabajos-futuros, papers]
tags: [tesis, doctorado]
source: system                          # auto-generado por el bot, no desde un mensaje de Telegram
---
```

El body del `_index.md` es Markdown libre. ADSO genera un template inicial con:

```markdown
# {title}

## Descripción
{descripción provista al crear el proyecto}

## Secciones
- [[introduccion/]] — {descripción breve}
- [[experimentos/]] — {descripción breve}
- [[papers/]] — {descripción breve}

## Estado
- Creado: {date_created}
- Notas: {count} (actualizado por el reporte semanal)
```

El usuario puede agregar lo que quiera al body (objetivos, notas, links, etc.). ADSO solo modifica el frontmatter (`date_modified`, `status`, `sections` si se agregan nuevas).

---

### `_index.md` (nota índice de área)

Cada área tiene un `_index.md` en su raíz. Se genera automáticamente al crear un área. La `description` es requerida — el bot la pide al crear y no permite omitirla.

```yaml
---
type: area-index
title: "Docencia"
date_created: 2025-01-01               # Date — sin comillas
date_modified: 2025-01-15              # Date — sin comillas
description: "Preparación de clases, guías de ejercicios, consultas de alumnos, material didáctico."  # requerido — usado por el LLM para clasificar
source: system
---
```

> Las áreas usan el mismo campo `description` — no hay diferencia estructural entre el `_index.md` de proyecto y de área, excepto que los proyectos tienen `status` y `sections`.

---

## `read_status`

Campo opcional que indica si el contenido fue leído/revisado. Aplica únicamente a **PDFs y links** (web genérico, arXiv, NASA ADS) — los tipos que representan contenido externo que el usuario puede o no haber consumido.

No aplica a: texto libre, audio, imágenes (se mandan para guardar algo, no como contenido a leer).

| Valor | Significado |
|---|---|
| `unread` | Guardado pero no leído todavía |
| `reading` | En proceso de lectura (principalmente para papers y documentos largos) |
| `read` | Leído / revisado |

**Cuándo se setea:**
- Al recibir un PDF o link, el bot pregunta `[Ya lo leí]` `[Lo quiero leer]`
- `[Ya lo leí]` → `read_status: read` — el usuario lo agrega al vault para que se relacione con el resto
- `[Lo quiero leer]` → `read_status: unread`
- Es siempre una decisión explícita del usuario — nunca automático

**Cómo se actualiza:**
- El usuario dice "ya leí el paper X" → bot actualiza `read_status: read`
- El usuario dice "estoy leyendo X" → `reading`
- Desde el bot al listar inbox: botón `[Marcar como leído]` junto a cada ítem

---

## `## Personal Notes` — sección en el body

Toda nota de paper incluye en su body una sección vacía al crear:

```markdown
## Personal Notes

```

El usuario la completa después de leer/revisar el contenido — es su interpretación propia, distinta de los campos auto-generados:

| Campo/sección | Quién lo llena | Qué es |
|---|---|---|
| `contribution`, `methods`, `conclusions` | LLM (del paper) | Lo que dice el autor |
| `relevance`, `context` | Usuario/LLM al guardar | Por qué lo guardaste |
| `## Personal Notes` | Usuario después de leer | Tu interpretación — cómo te sirve, con qué linkear, qué aplicar |

El bot puede ayudar a redactar esta sección: si el usuario manda "este paper me sirve para el capítulo 3, linkear con [[baseline-cnn]]", el bot formatea y escribe en esa sección.

---

## Notas de implementación

- El LLM recibe el contenido crudo y devuelve el frontmatter completo + cuerpo de la nota en JSON estructurado
- El bot parsea el JSON y escribe el archivo `.md` con el YAML correspondiente
- Los `tags` se generan en kebab-case, siempre en inglés. El LLM reutiliza tags existentes del vault (excluyendo 00-Inbox, top 100 por frecuencia) antes de crear nuevos
- El bot actualiza `date_modified` al editar notas existentes
- `relevance` y `context` en papers pueden ser provistos por el usuario o inferidos por el LLM del lenguaje del mensaje
- **Prioridad:** el LLM infiere `priority` del lenguaje del mensaje. Si no hay señal clara, usa `medium`. La prioridad aparece en el preview y el usuario puede corregirla por texto libre antes de confirmar. Solo aplica a tipos accionables: `task`, `idea`.
- **Extracción de papers:** `document_extractor.py` detecta heurísticamente si un PDF es un paper académico (≥ 2 señales: abstract, DOI, references, etc.) y extrae localmente secciones clave (abstract, keywords, methods, conclusions). Solo ese extracto compacto (~3000 chars) se envía al LLM. El título se extrae del metadata del PDF; si está vacío (común en arXiv), se infiere de las primeras líneas del texto. Fórmulas matemáticas: bloques detectados por número de ecuación `(1)`, `(2)` y reemplazados por `> [mathematical content — see PDF]`. `tags` y `keywords` son distintos: `keywords` = palabras clave del paper en idioma original; `tags` = etiquetas ADSO en inglés.
- **Embeddings de papers:** ChromaDB indexa el body completo generado por el LLM (AI Summary + Abstract + Methods + Conclusions). Gemini Embedding API es multilingüe y maneja búsqueda cross-lingual.

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
