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
date_modified: 2025-01-15T14:30:00    # ISO 8601, actualizado por el bot en cada edición hecha vía ADSO — sin comillas
type: reference                         # Ver tipos válidos abajo
tags: [tag1, tag2]                     # Generados por LLM, kebab-case
source: telegram                       # "telegram" para notas de usuario, "system" para auto-generadas (ej: _index.md)
media_type: text                       # text | audio | image | link | document — origen del contenido, seteado automáticamente
status: active                         # Text enum — valores dependen del type — ver tabla abajo
source_file: "[[archivo.pdf]]"        # opcional — wikilink al archivo en 03-Resources/, clickeable en Properties de Obsidian
source_url: "https://..."             # opcional — URL original cuando el input es un link
read_status: unread                    # Text enum — unread | read (ver sección read_status abajo)
---
```

`source_file` y `source_url` son mutuamente opcionales y pueden coexistir (ej: un paper del que se tiene el PDF y el link).

> **`date_modified` solo lo mantiene el bot.** Se actualiza en las escrituras que hace ADSO (`create_note`, `append_to_note`, `set_property`, `update_wikilinks`). Una edición externa desde Obsidian no lo toca: el `VaultWatcher` detecta el cambio y re-embede la nota, pero no reescribe el frontmatter. Consecuencia práctica: el `mtime` del archivo puede ser bastante más nuevo que `date_modified` (en el vault real hay 18 notas con esa divergencia, hasta 98 días). Para "última modificación real", usar `file.mtime` de Dataview, no `date_modified`.

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

- **Links en listas — siempre entre comillas dobles:** En campos de tipo lista de wikilinks (como el reservado `related`), los wikilinks dentro de arrays YAML **deben ir entre comillas dobles**. Sin ellas, el `[[` rompe el parseo de YAML y Obsidian muestra error en el frontmatter.
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

Cada tipo tiene su propio ciclo de vida. `status: archived` solo aplica a `project-index`; los demás tipos no usan este valor. El status refleja el estado dentro del ciclo de vida del tipo.

> **Archivar es manual.** El diseño es mover la carpeta del proyecto a `05-Archive/` y setear `status: archived` en su `_index.md`, pero **ningún código lo hace**: `archive_project` está en `VALID_OPERATIONS` y en el prompt, y `_cb_manage_confirm` responde `"Operación 'archive_project' todavía no está disponible."` (ver `docs/security.md` §9b). Hoy el usuario mueve la carpeta desde Obsidian y edita el `status` a mano. Lo único que ADSO archiva por su cuenta son los adjuntos huérfanos de `03-Resources/`, que van a `05-Archive/03-Resources/` y no tienen frontmatter.

`pending-classification` es el único valor compartido: cualquier tipo puede tenerlo si el LLM no respondió (modo degradado) o si el LLM no pudo asignar destino. Las notas con este status son candidatas para reclasificación automática (cron) o manual (`/clasificar`).

| Tipo | Valores de `status` | Default |
|---|---|---|
| `reference` | `active`, `pending-classification` | `active` |
| `task` | `pending`, `in-progress`, `done`, `pending-classification` | `pending` |
| `idea` | `raw`, `implemented`, `discarded`, `pending-classification` | `raw` |
| `project-index` | `active`, `on-hold`, `completed`, `archived` | `active` |
| `area-index` | — (sin ciclo de vida) | — |

`pending-classification` está disponible para todos los tipos: indica que el LLM no respondió (modo degradado). El bot intentará reclasificar automáticamente.

> **Normalización de enums (`_norm_enum` en `llm_schema.py`).** Antes de validar `type`/`status`/`priority` contra su enum, el valor se stringifica, se hace `strip`, se pasa a minúsculas **y se colapsan los espacios internos a guión**: `"In Progress"` → `in-progress`. Un modelo chico devuelve la forma con espacio tan seguido como la canónica, y sin esa normalización tiraba *toda* la respuesta a modo degradado. Acepta cualquier tipo sin lanzar (incluidos `dict`/`list` no hasheables del fallback de Groq).
>
> **Aliases de status:** si el valor normalizado sigue sin ser canónico, `STATUS_ALIASES` lo mapea antes de rechazar: `todo`/`open`/`new` → `pending`, `draft` → `raw`, `published` → `active`.
>
> **Enum vacío = sin valor:** un `status` o `priority` que llega como string vacío (`""`) se **descarta** (queda `None`) en vez de hacer fallar la validación. `""` es "sin valor", igual que `None` —que ya se aceptaba—, y el default aguas abajo completa el campo; antes un campo opcional vacío mandaba la captura entera a modo degradado.
>
> **Último recurso en el writer:** `create_note()` (`vault_writer.py`) revalida `type`/`status` contra sus enums y **coacciona** lo que no pasa — `type` inválido → `idea` + `pending-classification`; `status` inválido para su type → el fallback del type. Cubre a los escritores que no pasan por el validador del LLM (índices de `manage.py`, callers directos). Ver `docs/vault-interface.md` § `create_note()`.

---

## Tipos de nota (`type`)

| Valor | Carpeta destino | Descripción |
|---|---|---|
| `reference` | `01-Projects/{proyecto}/{seccion}/` si tiene proyecto, `02-Areas/{area}/` si tiene área, o `00-Inbox/` si no tiene ninguno | Nota de contenido general (incluye papers y cualquier material de referencia) |
| `task` | `01-Projects/{proyecto}/` si tiene proyecto, `02-Areas/{area}/` si tiene área, o `00-Inbox/` si no tiene ninguno | Tarea (proyecto > área > Inbox; con `due_date`/`scheduled` opcionales → Google Calendar) |
| `idea` | `01-Projects/{proyecto}/{seccion}/` si tiene proyecto, `02-Areas/{area}/` si tiene área, o `00-Inbox/` si no tiene ninguno | Idea exploratoria — se promueve a proyecto o se descarta |
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
summary: "Resumen generado por LLM"    # RESERVADO — no implementado (ver nota abajo)
related: ["[[otra-nota]]"]             # RESERVADO — no implementado (ver nota abajo)
---
```

> Routing: con proyecto → `01-Projects/{proyecto}/{seccion}/`. Con área (sin proyecto) → `02-Areas/{area}/`. Sin proyecto ni área → el preview muestra `00-Inbox` como destino; el bot **no** dispara ningún selector proactivamente. Para cambiarlo, el usuario aprieta `[Reubicar]` en el preview y elige `[Elegir área]` `[Elegir proyecto]` `[Inbox]`. El PDF/binario siempre va a `03-Resources/` independientemente del destino de la nota.

> **`summary` y `related` son campos reservados — ningún código los escribe.** Están en `ALLOWED_FRONTMATTER_KEYS` (`llm_schema.py`), así que sobreviven a la sanitización si aparecen, pero **no** están declaradas en el `frontmatter` de `_GEMINI_RESPONSE_SCHEMA`: el constrained decoding de Gemini no puede emitirlas, solo podría hacerlo el fallback de Groq (que responde sin schema). Ningún flujo del bot las popula y ninguna nota del vault real las usa. En particular, los links por similitud **no** van a `related`: se escriben como wikilinks en el bloque `## Ver también` del body. El campo `summary` del LLM que sí se usa es el del payload (nivel superior, no frontmatter), y solo en el flujo de arXiv para el callout `> [!summary] AI Summary` del body.

### Campos opcionales para contenido académico

Cuando el pipeline de extracción detecta contenido académico (papers, artículos, preprints), estos campos se agregan al frontmatter de la nota. No definen un tipo separado — un paper es una `reference` con estos campos poblados.

**Criterio de identificación — campos académicos, no el tag.** Un paper se identifica por la presencia de campos académicos en el frontmatter (`authors`, `year`, `doi`, `keywords`). El tag `#paper` **no es confiable como criterio**: `_TYPE_TAGS` (`llm_schema.py`) filtra `paper` de los tags que propone el LLM, por duplicar el `type`. El único flujo que lo escribe es el de arXiv, que lo antepone explícitamente después de la sanitización (`capture.py`); un paper subido como PDF queda sin el tag. En el vault real esto ya se nota: 6 notas con campos académicos y solo 5 con el tag. Filtrar por `contains(tags, "paper")` deja papers afuera — filtrar por `authors` (o `doi`) no.

```yaml
---
type: reference
tags: [paper, cosmology, machine-learning]        # generados por LLM, kebab-case, siempre en inglés. El LLM reutiliza tags existentes del vault (excluyendo 00-Inbox) antes de crear nuevos
read_status: unread                               # unread | read — seteado por el usuario con [Ya lo leí] / [Lo quiero leer]
authors: ["Apellido, N.", "Apellido, N."]
year: 2024
journal: "Nombre de la revista o venue"           # opcional — parte del schema del LLM
source_url: "https://arxiv.org/abs/XXXX.XXXXX"   # URL canónica — "source_url", no "url"
doi: "10.XXXX/..."                                # extraído localmente del PDF
keywords: ["time-series", "transformer", "self-supervised"]  # palabras clave del paper, idioma original
priority: medium                                  # Text enum — low | medium | high — inferido o explícito
# RESERVADOS — ningún código los escribe hoy (ver nota abajo):
relevance: "Para qué sirve este paper"            # campo libre
context: "Contexto adicional de uso"              # ej: "comparar con modelo actual"
contribution: "Qué aporta — nuevo modelo, benchmark, survey, etc."
methods: ["transformer", "contrastive-learning"]  # métodos/técnicas usadas
dataset: ["ImageNet", "COCO"]
conclusions: "Principales hallazgos y limitaciones reconocidas por los autores"
related: ["[[otra-nota]]", "[[paper-similar]]"]   # los links van al bloque `## Ver también` del body
---
```

> **Qué popula cada campo, verificado contra el código.** De este bloque, lo único que algún flujo escribe es:
> - **arXiv** (`_classify_and_preview_arxiv`, `capture.py`): `authors`, `year`, `doi`, `keywords` y `source_url` literales de la API, más `read_status: unread` por defecto y el tag `paper` antepuesto.
> - **PDF** (`_frontmatter_from_pdf_metadata`, `capture.py`): `title` y `authors` de la metadata del archivo (el `author` viene como un solo string y se parte por `,`/`;`), más el `read_status` que eligió el usuario con los botones.
> - **LLM de clasificación:** `journal`, y `authors`/`year`/`doi`/`keywords` cuando no vinieron de una fuente literal. Junto con `read_status`, son las únicas claves académicas declaradas en `_GEMINI_RESPONSE_SCHEMA` — las demás el constrained decoding no las puede emitir.
>
> `relevance`, `context`, `contribution`, `dataset` y `related` están en `ALLOWED_FRONTMATTER_KEYS` pero **no** en el schema del LLM y ningún flujo los escribe: son reservados, igual que `summary`. `methods` y `conclusions` sí los extrae `document_extractor.py`, pero **no como frontmatter**: van al contenido que se manda al LLM y terminan como las secciones `## Methods` / `## Conclusions` del body.

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

> **`user_context` — clave transitoria, solo en notas degradadas.** Si el usuario mandó un caption junto al archivo y la clasificación cayó a modo degradado, `_classify_and_preview` guarda ese texto en el frontmatter como `user_context`. No es parte del schema: es el único rastro de la señal de destino que dio el usuario, y `reclassify_inbox` la lee (`orig_fm.get("user_context")`) para pasársela al LLM en el reintento y la **borra** de la nota reclasificada (`new_fm.pop`). Está deliberadamente **fuera** de `ALLOWED_FRONTMATTER_KEYS`: la escribe el bot, y dejarla en la whitelist le abriría al LLM la posibilidad de proponerla.

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
related: ["[[nota-relacionada]]"]       # RESERVADO — no implementado; los links van al bloque `## Ver también` del body
---
```

### `_index.md` (nota índice de proyecto)

Cada proyecto tiene un `_index.md` en su raíz. Es la única nota que no se crea por captura de mensaje — se genera automáticamente al crear un proyecto por el flujo de gestión del bot (lenguaje natural + botones; **no existe un comando `/gestión`**) o por la siembra inicial desde `config.yaml` (`seed_vault`). El LLM clasificando una nota en un proyecto nuevo **no** dispara la creación del índice. Para editarlo después, se edita el archivo en Obsidian: el modo edición por Telegram no está implementado (ver Fase 7).

```yaml
---
type: project-index
title: "Tesis doctoral"
date_created: 2025-01-01               # Date — sin comillas
date_modified: 2025-01-15              # Date — sin comillas
status: active                          # Text enum — active | on-hold | completed | archived
description: "Papers de doctorado, experimentos de ML, escritura académica."  # scope de clasificación — requerido
sections: [introduccion, experimentos, trabajos-futuros, papers]
tags: [tesis, doctorado]
source: system                          # auto-generado por el bot, no desde un mensaje de Telegram
---
```

> **Lo que el bot escribe realmente al crear el proyecto** (`manage.py` y `seed_vault` en `vault_writer.py`): `title` (el nombre con guiones a espacios y capitalizado), `type`, `status: active`, `description`, `sections: []` **vacío**, `tags` (`["system", "<nombre>"]` desde el bot; solo `["<nombre>"]` desde la siembra de `config.yaml`), `source: system` y `project: <nombre>`. Ese `project:` no es decorativo: es uno de los campos que `_get_existing_items` lee del `_index.md` para armar la lista de destinos que ve el LLM.
>
> **`sections` nace vacío y ADSO no lo actualiza.** `create_section` solo hace `mkdir` del directorio dentro del proyecto — no toca el `_index.md`. Mantener la lista al día es manual.

El body del `_index.md` es Markdown libre. ADSO genera un template inicial con:

```markdown
# {title}

## Descripción
{descripción provista al crear el proyecto}

## Secciones

## Estado
- Creado: {date_created}
```

El usuario puede agregar lo que quiera al body (objetivos, notas, links, secciones, etc.). ADSO solo modifica el frontmatter (`date_modified`, `status`).

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

> Las áreas usan el mismo campo `description` — no hay diferencia estructural entre el `_index.md` de proyecto y de área, excepto que los proyectos tienen `status` y `sections`. El bot escribe además `tags` y `area: <nombre>` (el equivalente del `project:` del índice de proyecto, y lo que `_get_existing_items` lee).

> **`description` se valida por contenido, no por presencia.** `_validate_manage_payload` (`llm_schema.py`) rechaza con `LLMResponseError` una operación de crear proyecto o área cuya `description` sea `""`, solo espacios o `null` — antes solo se chequeaba que la clave existiera, así que un `_index.md` podía nacer con la descripción vacía. No es cosmético: la `description` es lo que el LLM lee para decidir en qué proyecto o área clasificar cada nota nueva. Un índice sin ella deja al destino invisible para la clasificación.

---

## `read_status`

Campo opcional que indica si el contenido fue leído/revisado. Aplica a los inputs que representan contenido externo que el usuario puede o no haber consumido: **PDFs** y **links de arXiv**.

No aplica a: texto libre, audio, imágenes, documentos de texto (`.md`/`.txt`) ni links genéricos (web no-arXiv) — ninguno de esos flujos setea el campo.

| Valor | Significado |
|---|---|
| `unread` | Guardado pero no leído todavía |
| `read` | Leído / revisado |

Los valores válidos son solo estos dos (`VALID_READ_STATUS` en `llm_schema.py`) — cualquier otro valor que devuelva el LLM se descarta.

**Cuándo se setea:**
- **PDF** (`input.py`): al recibir el archivo el bot pregunta con botones `[Ya lo leí]` `[Lo quiero leer]` antes de procesarlo. `[Ya lo leí]` → `read_status: read` (el usuario lo agrega al vault para que se relacione con el resto); `[Lo quiero leer]` → `read_status: unread`. Es decisión explícita del usuario. La elección se arrastra por todos los caminos del PDF, incluido el de PDF escaneado (Vision).
- **Link de arXiv** (`capture.py`): el bot **no** pregunta — el flujo aplica `read_status: unread` por defecto. Se corrige editando la nota en Obsidian.
- **Cualquier otro input:** no se setea el campo. Si el LLM lo devuelve igual, `_validate_capture_payload` lo valida contra `VALID_READ_STATUS` y descarta cualquier valor fuera de `{read, unread}`.

**Cómo se actualiza:** editando la nota en Obsidian. La actualización via bot ("ya leí el paper X" → `read`, botón `[Marcar como leído]`) es funcionalidad planificada del modo edición (Fase 7) — no está implementada.

---

## `## Personal Notes` — sección en el body

Toda nota de paper incluye en su body una sección vacía al crear:

```markdown
## Personal Notes

```

El usuario la completa después de leer/revisar el contenido — es su interpretación propia, distinta de los campos auto-generados:

| Campo/sección | Quién lo llena | Qué es |
|---|---|---|
| `## Abstract`, `## Methods`, `## Conclusions` (body) | Pipeline de extracción + LLM (del paper) | Lo que dice el autor |
| `contribution`, `relevance`, `context` (frontmatter) | — reservados, nadie los escribe todavía | Por qué lo guardaste |
| `## Personal Notes` | Usuario después de leer | Tu interpretación — cómo te sirve, con qué linkear, qué aplicar |

*(Planificado — Fase 7)* El bot podría ayudar a redactar esta sección: si el usuario manda "este paper me sirve para el capítulo 3, linkear con [[baseline-cnn]]", el bot formatea y escribe ahí. Requiere el modo edición, que no está implementado — hoy la sección se completa en Obsidian.

---

## Notas de implementación

- El LLM recibe el contenido crudo y devuelve el frontmatter completo + cuerpo de la nota en JSON estructurado
- El bot parsea el JSON y escribe el archivo `.md` con el YAML correspondiente
- Los `tags` se generan en kebab-case, siempre en inglés. El LLM reutiliza tags existentes del vault (los 100 más frecuentes, excluyendo `00-Inbox`, `05-Archive`, `.obsidian` y `.trash`) antes de crear nuevos
- **Sanitización de `tags` (`_validate_capture_payload`):** un string suelto (`"python, ml"`, típico de Groq sin schema) se parte por comas; cualquier otro tipo inesperado cae a `[]`. Cada tag pasa por `_to_kebab` —minúsculas, transliteración de acentos y `ñ` (`mañana` → `manana`, no `maana`), espacios y guiones bajos a `-`, se descarta el resto de los caracteres—, y después se filtran los que duplican el `type` (`_TYPE_TAGS`: task, tarea, note, nota, idea, reference, paper, document, audio, image, link) y las expresiones temporales (`_TEMPORAL_TAGS`: días de la semana en ES/EN, hoy, manana, today, tomorrow, proxima-semana, next-week). Un `None` suelto dentro de la lista se descarta **antes** de stringificar: sin ese guard, `str(None)` producía el tag literal `none`. El dedup corre **después** de normalizar —`"Machine Learning"` y `"machine-learning"` colapsan en uno— y preserva el orden de primera aparición (un `set()` lo barajaría)
- El bot actualiza `date_modified` al editar notas existentes
- **Qué no llega al YAML (`_clean_frontmatter` en `vault_writer.py`):** los campos con valor `None` se omiten del archivo (no se escriben como `campo: null`), y las claves con prefijo `_` se descartan por convención — son estado interno del bot (por ejemplo el `_body_embedding` que viaja en el payload de `pending_note`) y nunca se persisten. Los campos de `DATE_FIELDS` (`date_created`, `date_modified`, `due_date`, `scheduled`) se convierten de string ISO 8601 a objeto `date`/`datetime` para que PyYAML los serialice sin comillas
- `relevance` y `context` son **reservados**: el diseño es que los provea el usuario o los infiera el LLM del lenguaje del mensaje, pero no están en el schema del LLM y ningún flujo los escribe hoy (ver la nota del bloque académico)
- **Prioridad:** el LLM infiere `priority` del lenguaje del mensaje. Si no hay señal clara, usa `medium`. La prioridad aparece en el preview y el usuario puede corregirla por texto libre antes de confirmar. Solo aplica a tipos accionables: `task`, `idea`.
- **Extracción de papers:** `document_extractor.py` detecta heurísticamente si un PDF es un paper académico (≥ 2 señales: abstract, DOI, references, etc.) y extrae localmente secciones clave (abstract, keywords, methods, conclusions). Solo ese extracto compacto (~3000 chars) se envía al LLM. El título se extrae del metadata del PDF; si está vacío (común en arXiv), se infiere de las primeras líneas del texto. Fórmulas matemáticas: bloques detectados por número de ecuación `(1)`, `(2)` y reemplazados por `> [mathematical content — see PDF]`. `tags` y `keywords` son distintos: `keywords` = palabras clave del paper en idioma original; `tags` = etiquetas ADSO en inglés.
- **Embeddings de papers:** ChromaDB indexa el body completo generado por el LLM (AI Summary + Abstract + Methods + Conclusions). Gemini Embedding API es multilingüe y maneja búsqueda cross-lingual.
- **`due_date` / `scheduled` se coaccionan a `str`, no solo se validan.** Groq (sin schema constrained) devuelve a veces `due_date: 20260101` como **int**: `datetime.fromisoformat(str(val))` lo acepta, pero después rompía el slice `due_date[:10]` de `tasks_client` al pushear la tarea. `_validate_capture_payload` reescribe el campo con su forma string tras validarlo; lo que no parsea como ISO 8601 se descarta (`None`).
- **`summary` no-string se descarta.** El `summary` del payload (nivel superior, el que usa el flujo de arXiv para el callout `> [!summary] AI Summary`) puede venir como dict o lista del fallback de Groq. Se coacciona a `None` con log a `warning`, para que el `(payload.get("summary") or "").strip()` del flujo de arXiv no lance `AttributeError`.
- **`type` inválido en `text`/`audio` no degrada la captura** (`coerce_discarded_type` en `llm_schema.py`, aplicado a los `media_type` de `BUTTON_CHOSEN_TYPE_MEDIA` = `{text, audio}`). En esos dos medios el usuario ya acotó el tipo con los botones `[Tarea]`/`[Nota]` antes de clasificar, así que hacer fallar la validación por ese campo quemaba los 3 reintentos y mandaba a modo degradado una nota por un valor que el flujo iba a acotar igual. La coerción mapea un puñado de aliases (`note`/`nota`/`referencia` → `reference`; `tarea`/`todo`/`recordatorio` → `task`) y cualquier otro valor cae a `idea`; si el `status` que venía atado al type descartado no aplica al nuevo, se descarta también (el default aguas abajo lo completa). **En `document`/`image`/`link` no se aplica:** ahí el `type` sí lo decide el LLM y un valor inválido debe seguir cayendo a modo degradado.
  - Cuánto se descarta después depende del botón: `[Tarea]` pasa `forced_type="task"` y **pisa** el type del LLM (más `status: pending`); `[Nota]` **no** fuerza type — pasa `prevent_task=True`, que solo convierte un `task` en `reference` + `status: active` y deja en pie la elección del LLM entre `reference` e `idea` (`_classify_and_preview`, `capture.py`). Es decir: con `[Nota]`, el `idea` de la coerción sí llega al preview.

---

## Consultas Dataview de ejemplo

**Papers sin leer por prioridad:**
```dataview
TABLE authors, year, priority, relevance
FROM "01-Projects" OR "03-Resources"
WHERE authors AND read_status = "unread"
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
WHERE authors
SORT choice(priority, "high", 1, "medium", 2, "low", 3) ASC, year DESC
```

**Papers de la tesis:**
```dataview
TABLE authors, year, relevance
FROM "01-Projects/tesis"
WHERE authors
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
