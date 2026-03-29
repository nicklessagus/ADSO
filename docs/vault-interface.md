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

# Interfaz del Vault — Especificación detallada

Este documento especifica en detalle las funciones de `vault_writer.py` y `vault_search.py`, los dos módulos que encapsulan toda interacción directa con el filesystem del vault.

**Contexto:** el Obsidian CLI (disponible desde v1.12.4) expone estas mismas operaciones como comandos de shell, pero no es viable en RPi4 porque requiere que Obsidian (Electron) esté corriendo (ver `docs/architecture.md` — sección "Alternativa futura: Obsidian CLI"). Estos módulos implementan el mismo comportamiento operando directamente sobre el filesystem. Cuando el CLI sea viable en entornos headless, se reemplazarán por adaptadores que deleguen al CLI sin cambiar las firmas.

---

## Mapa CLI → implementación

| Comando CLI | Módulo | Función |
|---|---|---|
| `obsidian create` | `vault_writer.py` | `create_note()` |
| `obsidian read` | `vault_writer.py` | `read_note()` |
| `obsidian append` | `vault_writer.py` | `append_to_note()` |
| `obsidian property:set` | `vault_writer.py` | `set_property()` |
| `obsidian backlinks` | `vault_search.py` | `get_backlinks()` |
| `obsidian search` | `vault_search.py` | `search()` |
| `obsidian tags` | `vault_search.py` | `get_all_tags()` |
| `obsidian tasks` | `vault_search.py` | `find_tasks()` |
| — (no tiene equiv. CLI) | `vault_writer.py` | `delete_note()`, `move_note()`, `update_wikilinks()` |
| — (no tiene equiv. CLI) | `vault_search.py` | `get_wikilinks()`, `find_by_property()`, `find_by_tag()` |
| `obsidian daily` | — | Fuera de scope |
| `obsidian eval` | — | No replicable sin Electron |

---

## Dependencias

```
python-frontmatter   # parse/serialización segura de YAML frontmatter
pathlib              # filesystem
asyncio              # wrappers async
python-slugify       # generación de nombres de archivo en kebab-case
```

`python-frontmatter` es crítico: maneja el bloque `---` de forma atómica (parse + serialize sin tocar el body), preserva el orden de campos existentes y evita corrupción. No se usa PyYAML directamente para escritura de frontmatter.

---

## Tipos de datos

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class NoteRef:
    """Referencia ligera a una nota. Se usa en resultados de búsqueda."""
    path: Path           # path absoluto al archivo .md
    title: str           # campo title del frontmatter
    note_type: str       # campo type del frontmatter
    status: str          # campo status del frontmatter
    snippet: str | None  # fragmento del cuerpo con contexto alrededor del match (None si no aplica)

@dataclass
class NoteData:
    """Contenido completo de una nota."""
    path: Path
    frontmatter: dict    # todos los campos YAML como dict Python
    body: str            # cuerpo de la nota (sin los delimitadores ---)
```

---

## `vault_writer.py`

Responsabilidad única: **escritura y modificación de archivos `.md` en el vault**.
No llama a LLMs ni a ChromaDB — esas responsabilidades quedan en los módulos que lo invocan.
Toda operación es `async` para no bloquear el event loop del bot.

---

### `create_note()`

**Equivalente CLI:** `obsidian create`

```python
async def create_note(
    frontmatter: dict,
    body: str,
    vault_path: Path,
    dry_run: bool = False,
) -> Path:
```

**Comportamiento:**

1. Calcula el nombre de archivo: `YYYY-MM-DD-{slug}.md` donde `slug` es el `title` del frontmatter pasado por `python-slugify` (kebab-case, sin caracteres especiales, máx. 60 chars del slug).
2. Calcula el directorio destino a partir del frontmatter según estas reglas (en orden):
   - `type: reference` con `project` → `{vault_path}/01-Projects/{project}/{section}/` (si `section` presente) o `{vault_path}/01-Projects/{project}/` (sin sección)
   - `type: reference` con `area` (sin `project`) → `{vault_path}/02-Areas/{area}/`
   - `type: reference` sin `project` ni `area` → destino resuelto por el caller (bot.py pregunta con botones: `[Elegir área]` `[Elegir proyecto]` `[Inbox]`)
   - `type: task` → `{vault_path}/02-Areas/{area}/`
   - `type: idea` con `project` → `{vault_path}/01-Projects/{project}/{section}/` (si `section` presente) o `{vault_path}/01-Projects/{project}/` (sin sección)
   - `type: idea` con `area` (sin `project`) → `{vault_path}/02-Areas/{area}/`
   - `type: idea` sin `project` ni `area` → destino resuelto por el caller (bot.py pregunta con botones)
   - `type: project-index` → `{vault_path}/01-Projects/{project}/` con nombre fijo `_index.md`
   - `type: area-index` → `{vault_path}/02-Areas/{area}/` con nombre fijo `_index.md`
3. Si el directorio no existe, lo crea (incluyendo intermedios).
4. Si ya existe un archivo con ese nombre, agrega sufijo numérico: `-2.md`, `-3.md`, etc.
5. Construye el archivo: bloque `---` con el frontmatter serializado por `python-frontmatter` + `\n\n` + body.
6. Si `dry_run=True`: retorna el path calculado sin escribir nada al disco (usado para el preview al usuario).
7. Si `dry_run=False`: escribe el archivo y retorna el path.

**Errores:**
- `PermissionError` si el vault_path no tiene permisos de escritura → propagar con mensaje claro.
- `ValueError` si el frontmatter no contiene `title` o `type` → propagar antes de intentar escribir.

---

### `read_note()`

**Equivalente CLI:** `obsidian read`

```python
async def read_note(note_path: Path) -> NoteData:
```

**Comportamiento:**

1. Lee el archivo con `python-frontmatter.load()`.
2. Retorna `NoteData(path=note_path, frontmatter=post.metadata, body=post.content)`.

**Errores:**
- `FileNotFoundError` si el archivo no existe → propagar.
- `ValueError` si el archivo no tiene bloque `---` válido → propagar con mensaje que indique el path afectado.

---

### `append_to_note()`

**Equivalente CLI:** `obsidian append`

```python
async def append_to_note(
    note_path: Path,
    content: str,
    separator: str = "\n\n---\n\n",
) -> None:
```

**Comportamiento:**

1. Lee el archivo con `read_note()`.
2. Concatena `separator + content` al final del body existente.
3. Actualiza `date_modified` en el frontmatter al timestamp actual (ISO 8601).
4. Reescribe el archivo completo con `python-frontmatter`.

**Nota:** el `separator` por defecto es una línea horizontal de Obsidian. El caller puede pasar `"\n\n"` si no quiere separador visual.

---

### `set_property()`

**Equivalente CLI:** `obsidian property:set`

```python
async def set_property(
    note_path: Path,
    key: str,
    value: Any,
    update_date_modified: bool = True,
) -> None:
```

**Comportamiento:**

1. Lee el archivo con `python-frontmatter.load()`.
2. Valida que `key` y `value` sean coherentes con el schema conocido (ver tabla de validaciones abajo). Si no lo son, lanza `ValueError` con descripción del conflicto antes de tocar el archivo.
3. Asigna `post.metadata[key] = value`.
4. Si `update_date_modified=True` y `key != "date_modified"`: actualiza también `date_modified` al timestamp actual.
5. Reescribe solo el frontmatter (body intacto) con `python-frontmatter.dump()`.

**Validaciones por campo:**

| Campo | Validación |
|---|---|
| `status` | Debe pertenecer al conjunto válido para el `type` de la nota |
| `type` | Debe ser uno de: `reference`, `task`, `idea`, `project-index`, `area-index` |
| `priority` | Debe ser: `low`, `medium`, `high` |
| `media_type` | Debe ser: `text`, `audio`, `image`, `link`, `document` |
| `source` | Debe ser: `telegram`, `system` |
| `date_created`, `date_modified`, `due_date`, `scheduled` | Debe ser ISO 8601 parseable |
| `tags` | Debe ser lista de strings |
| Otros campos | Sin validación estructural — se acepta cualquier valor |

**Errores:**
- `ValueError` si la validación falla → propagar sin modificar el archivo (fail-fast).
- `FileNotFoundError` si el archivo no existe → propagar.

---

### `delete_note()`

**Sin equivalente directo en CLI**

```python
async def delete_note(note_path: Path) -> None:
```

**Comportamiento:**

1. Elimina el archivo del filesystem.
2. No toca ChromaDB — el caller (bot.py) es responsable de llamar a `embeddings.py` para limpiar los embeddings.

**Nota de diseño:** la detección de backlinks antes del borrado es responsabilidad de `bot.py` (usando `vault_search.get_backlinks()`), no de esta función. `delete_note()` solo borra el archivo. Separar la decisión de la ejecución permite reutilizar la función en contextos donde los backlinks ya fueron verificados.

---

### `move_note()`

**Sin equivalente directo en CLI**

```python
async def move_note(source: Path, dest_dir: Path) -> Path:
```

**Comportamiento:**

1. Calcula `dest_path = dest_dir / source.name`.
2. Crea `dest_dir` si no existe.
3. Si ya existe un archivo con ese nombre en destino, agrega sufijo numérico.
4. Mueve el archivo (`pathlib.Path.rename()`).
5. Retorna el nuevo path.

**Nota:** actualizar ChromaDB metadata con el nuevo path es responsabilidad del caller.

---

### `save_resource()`

```python
async def save_resource(
    source_path: Path,
    original_filename: str,
    vault_path: Path,
) -> Path:
```

Copia un archivo a `03-Resources/` en el vault.

**Comportamiento:**

1. Verifica que `source_path` existe.
2. Calcula `dest = 03-Resources/{original_filename}`.
3. **Deduplicación:** si ya existe un archivo con el mismo nombre y el mismo tamaño, lo reutiliza y retorna el path existente sin copiar nada.
4. Si existe con el mismo nombre pero distinto tamaño (archivo diferente), agrega sufijo numérico: `paper_1.pdf`, `paper_2.pdf`, etc.
5. Copia el archivo con `shutil.copy2` (preserva metadatos).
6. Retorna el path del archivo en el vault.

**Errores:**
- `FileNotFoundError` si `source_path` no existe → propagar.

---

### `update_wikilinks()`

**Sin equivalente directo en CLI** (el CLI lo hace internamente en `rename`)

```python
async def update_wikilinks(
    note_path: Path,
    old_name: str,
    new_name: str,
) -> None:
```

**Comportamiento:**

1. Lee el archivo.
2. Aplica dos reemplazos en el body con regex:
   - `[[old_name]]` → `[[new_name]]`
   - `[[old_name|{alias}]]` → `[[new_name|{alias}]]` (preserva el alias)
3. Reescribe el archivo si hubo algún cambio.
4. Actualiza `date_modified` si el archivo fue modificado.

**Uso típico:** `bot.py` llama a `vault_search.get_backlinks(old_name)` para obtener la lista de notas afectadas, luego llama a `update_wikilinks()` en cada una.

---

## `vault_search.py`

Responsabilidad única: **lectura y búsqueda estructural sobre el vault**.
No escribe nada. No llama a APIs externas ni a ChromaDB.
Solo opera sobre archivos `.md` del filesystem.

---

### `get_backlinks()`

**Equivalente CLI:** `obsidian backlinks`

```python
async def get_backlinks(
    note_name: str,
    vault_path: Path,
    exclude_dirs: list[str] | None = None,
) -> list[NoteRef]:
```

**Comportamiento:**

1. `note_name` es el stem del archivo (sin `.md` y sin path). Obsidian resuelve wikilinks por nombre, no por path completo.
2. Construye un regex que matchea todas las formas de referenciar la nota:
   - `[[note_name]]` — link directo
   - `[[note_name|{alias}]]` — link con alias
   - `[[note_name#{heading}]]` — link a heading interno
   - `[[note_name#{heading}|{alias}]]` — combinado
3. Escanea todos los `.md` del vault (excluyendo `exclude_dirs`; si `None`, usa `vault.exclude_dirs` de config).
4. Por cada archivo que contiene al menos un match, extrae un snippet de contexto (la línea completa que contiene el match).
5. Lee el frontmatter de cada archivo que matchea para obtener `title`, `type`, `status`.
6. Retorna lista de `NoteRef` ordenada por `path`.

**Performance:** escaneo lineal de todos los `.md`. Para un vault personal (< 1000 notas) en RPi4 es aceptable (< 500ms estimado). No se mantiene índice en memoria — se escanea en cada llamada.

---

### `search()`

**Equivalente CLI:** `obsidian search`

```python
async def search(
    query: str,
    vault_path: Path,
    scope: str | None = None,
    exclude_dirs: list[str] | None = None,
) -> list[NoteRef]:
```

**Comportamiento:**

Parsea `query` buscando tokens especiales antes de hacer la búsqueda de texto libre. Los tokens se separan del texto libre por espacios. Pueden combinarse.

**Tokens soportados:**

| Token | Ejemplo | Qué filtra |
|---|---|---|
| `tag:{valor}` | `tag:paper` | Notas que tienen el tag en frontmatter `tags:` o como inline `#tag` |
| `path:{prefijo}` | `path:01-Projects/tesis` | Notas cuyo path contiene el prefijo |
| `type:{valor}` | `type:task` | Notas con ese `type` en frontmatter |
| `status:{valor}` | `status:pending` | Notas con ese `status` en frontmatter |
| `project:{valor}` | `project:tesis` | Notas con ese `project` en frontmatter |
| `area:{valor}` | `area:investigacion` | Notas con ese `area` en frontmatter |
| `file:{nombre}` | `file:baseline-cnn` | Notas cuyo nombre de archivo contiene el valor |
| texto libre | `deep learning` | Búsqueda en title + body (case-insensitive) |

**Orden de resultados:**
1. Notas donde `title` contiene la query (match exacto de frase)
2. Notas donde `title` contiene alguna de las palabras
3. Notas donde el body contiene la query
4. Resto

**`scope`:** si se provee, restringe el escaneo al subdirectorio `{vault_path}/{scope}`. Ejemplo: `scope="01-Projects/tesis"`.

---

### `find_by_tag()`

**Equivalente CLI:** subset de `obsidian search tag:{valor}`

```python
async def find_by_tag(
    tag: str,
    vault_path: Path,
    hierarchical: bool = True,
) -> list[NoteRef]:
```

**Comportamiento:**

1. Normaliza `tag`: elimina `#` inicial si está presente, convierte a lowercase.
2. Busca el tag en dos fuentes por nota:
   - Frontmatter `tags: [tag1, tag2]`
   - Tags inline `#tag` en el body (regex `(?<!\[)#([\w/-]+)`)
3. Si `hierarchical=True`: un tag `metodo` también matchea `metodo/cnn`, `metodo/transformer`, etc. (el valor buscado es prefijo del tag real).

---

### `find_by_property()`

**Equivalente CLI:** subset de `obsidian search` con filtros de property

```python
async def find_by_property(
    key: str,
    value: Any | None,
    vault_path: Path,
    scope: str | None = None,
) -> list[NoteRef]:
```

**Comportamiento:**

1. Para cada `.md` en el vault (respetando `scope`): parsea frontmatter.
2. Si `value is None`: retorna todas las notas que tienen el campo `key` con cualquier valor (incluyendo listas vacías).
3. Si `value` es un string y el campo es una lista: busca si `value` está contenido en la lista.
4. Si `value` es un string y el campo es un scalar: compara por igualdad (case-insensitive para strings).

**Uso típico:**
- `find_by_property("type", "task")` → todas las tareas
- `find_by_property("project", "tesis")` → todas las notas del proyecto tesis
- `find_by_property("doi", None)` → notas que tienen DOI

---

### `get_all_tags()`

**Equivalente CLI:** `obsidian tags`

```python
async def get_all_tags(
    vault_path: Path,
    exclude_dirs: list[str] | None = None,
) -> dict[str, int]:
```

**Comportamiento:**

1. Para cada `.md` en el vault: extrae tags de frontmatter `tags:` y tags inline `#tag` del body.
2. Normaliza: lowercase, elimina `#`.
3. Acumula frecuencias.
4. Retorna `{tag: count}` ordenado por frecuencia descendente.

**Uso en clasificación LLM:**

Al clasificar nuevo contenido, `bot.py` llama a `get_all_tags()` excluyendo `00-Inbox` (además de los directorios excluidos por defecto) y pasa la lista al system prompt de Gemini. El LLM debe preferir tags de esa lista antes de inventar nuevos. Solo se incluyen los primeros 100 tags (por frecuencia) para no inflar el prompt.

La exclusión de `00-Inbox` es intencional: los tags de notas pendientes de clasificación son tentativas y no deben propagarse como vocabulario canónico.

---

### `find_tasks()`

**Equivalente CLI:** `obsidian tasks`

```python
async def find_tasks(
    vault_path: Path,
    status: str | None = None,
    area: str | None = None,
    project: str | None = None,
    include_inline: bool = True,
) -> list[NoteRef]:
```

**Comportamiento:**

**Fuente 1 — notas `type: task`:**
- Busca todas las notas con `type: task` en frontmatter.
- Aplica filtros `status`, `area`, `project` si se proveen.

**Fuente 2 — checkboxes inline** (si `include_inline=True`):
- Busca líneas `- [ ] {texto}` o `- [x] {texto}` en el body de cualquier nota.
- Si `status="pending"`: solo `- [ ]`. Si `status="done"`: solo `- [x]`.
- Las notas con checkboxes que ya son `type: task` no se duplican.

**Retorna** lista combinada de `NoteRef`. Para los checkboxes inline, el `snippet` contiene el texto del checkbox.

---

### `get_wikilinks()`

**Sin equivalente CLI** (el CLI tiene backlinks pero no outgoing links)

```python
async def get_wikilinks(note_path: Path) -> list[str]:
```

**Comportamiento:**

1. Lee el body de la nota.
2. Extrae todos los `[[...]]` con regex `\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]`.
3. Retorna lista de stems (sin alias, sin heading, sin `.md`).
4. Elimina duplicados, preserva orden de aparición.

**Uso típico:** al crear una nueva nota, el bot verifica los outgoing links para validar que las notas referenciadas existen.

---

### `get_note_index()`

**Función interna** — no tiene equivalente CLI

```python
async def get_note_index(vault_path: Path) -> dict[str, Path]:
```

**Comportamiento:**

Recorre el vault y construye `{stem → path}` para todos los `.md`. Se usa internamente en `get_backlinks()` y en la validación de wikilinks.

```python
# Ejemplo:
{
    "2025-01-15-baseline-cnn": Path("/vault/01-Projects/tesis/experimentos/2025-01-15-baseline-cnn.md"),
    "_index": Path("/vault/01-Projects/tesis/_index.md"),
    ...
}
```

No se cachea entre llamadas. Si el vault crece y el tiempo de escaneo se convierte en problema, se puede agregar un cache invalidado por `inotify` (watchdog) sin cambiar la interfaz.

---

## Patrón de migración al CLI

Cuando el CLI de Obsidian sea viable en entorno headless, la migración es:

**1.** Crear `vault_writer_cli.py` y `vault_search_cli.py` que implementen las **mismas firmas de función**, delegando a subprocesos:

```python
# vault_search_cli.py — mismo contrato que vault_search.py
async def get_backlinks(note_name: str, vault_path: Path, ...) -> list[NoteRef]:
    result = await asyncio.create_subprocess_exec(
        "obsidian", "backlinks", f"file={note_name}", f"vault={vault_path}",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await result.communicate()
    return _parse_cli_backlinks(stdout)   # adapter de JSON del CLI a NoteRef
```

**2.** Agregar flag en `config.yaml`:

```yaml
vault_backend: filesystem   # filesystem | cli
```

**3.** En `config.py`, exportar las implementaciones según el flag:

```python
if settings.vault_backend == "cli":
    from adso.vault_writer_cli import create_note, read_note, ...
    from adso.vault_search_cli import get_backlinks, search, ...
else:
    from adso.vault_writer import create_note, read_note, ...
    from adso.vault_search import get_backlinks, search, ...
```

`bot.py` y el resto de los módulos importan desde `config.py` — nunca importan directamente de `vault_writer` o `vault_search`. El swap es un cambio de una línea en `config.yaml`.

---

## Consideraciones de performance en RPi4

| Operación | Estrategia | Estimación |
|---|---|---|
| `create_note`, `read_note`, `set_property` | I/O de un solo archivo | < 10ms |
| `get_backlinks` | Escaneo lineal de todos los `.md` | < 200ms para 500 notas |
| `search` (texto libre) | Escaneo lineal con regex | < 300ms para 500 notas |
| `get_all_tags` | Escaneo lineal | < 200ms para 500 notas |
| `find_tasks` | Escaneo lineal filtrado | < 200ms para 500 notas |

Todas las operaciones de búsqueda son `async` con `asyncio.to_thread()` para el I/O de disco — no bloquean el event loop del bot mientras escanean.

Si el vault crece y los tiempos se vuelven perceptibles, el siguiente paso es un índice JSON en memoria (`{stem → {path, frontmatter}}`) mantenido por `watchdog`, sin cambiar las firmas públicas.

---

## `embeddings.py`

Responsabilidad única: **gestión de embeddings y ChromaDB**. No escribe archivos al vault ni llama a LLMs de clasificación — solo calcula embeddings (via Gemini Embedding API) y opera sobre la colección de ChromaDB.

### Dependencias

```
chromadb              # vector store embebido (PersistentClient, sin servidor separado)
google-generativeai   # Gemini Embedding API
```

### Inicialización

```python
import chromadb

client = chromadb.PersistentClient(path="/app/data/chroma")
collection = client.get_or_create_collection(
    name="vault_notes",
    metadata={"hnsw:space": "cosine"},   # distancia coseno — estándar para embeddings de texto
)
```

La colección se crea una vez. El espacio de distancia (`cosine`) no se puede cambiar después de creada — requiere recrear la colección.

### Schema de metadata en ChromaDB

Cada documento en la colección tiene:

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | string | Stem del archivo (sin `.md`, sin path). Clave primaria. Ej: `2025-01-15-baseline-cnn` |
| `document` | string | Texto completo usado para generar el embedding (contenido extraído, no el YAML) |
| `metadata.path` | string | Path relativo al vault. Ej: `01-Projects/tesis/experimentos/2025-01-15-baseline-cnn.md` |
| `metadata.type` | string | `reference`, `task`, `idea`, `project-index`, `area-index` |
| `metadata.status` | string | Status actual de la nota |
| `metadata.project` | string | Proyecto (vacío si no tiene) |
| `metadata.area` | string | Área (vacío si no tiene) |
| `metadata.tags` | string | Tags separados por coma — ChromaDB no soporta listas, se serializa como `"paper,ml,cnn"` |
| `metadata.media_type` | string | `text`, `audio`, `image`, `link`, `document` |

> **Nota:** ChromaDB no soporta valores `None` ni listas heterogéneas en metadata. Los campos nulos se almacenan como string vacío `""`. Los tags se serializan como string separado por comas.

---

### `index_note()`

```python
async def index_note(
    note_id: str,
    content: str,
    metadata: dict,
) -> None:
```

**Comportamiento:**

1. Calcula el embedding de `content` via Gemini Embedding API (`models/text-embedding-004`).
2. Serializa `metadata` al formato ChromaDB (nulos → `""`, tags list → string separado por comas).
3. Ejecuta `collection.upsert(ids=[note_id], embeddings=[embedding], documents=[content], metadatas=[metadata])`.

`upsert` inserta si no existe, actualiza si ya existe — idempotente.

**Errores:**
- API de Gemini no responde → loguear, no propagar (el embedding se genera en el re-index nocturno).
- Rate limit 429 → lógica adaptativa: cuota diaria → desiste inmediato; RPM → espera `retryDelay` sugerido por la API (máx 70s); otros → backoff fijo (1s, 2s, 4s). Tras 3 fallos, desiste y loguea.

---

### `remove_note()`

```python
async def remove_note(note_id: str) -> None:
```

**Comportamiento:**

1. Ejecuta `collection.delete(ids=[note_id])`.
2. Si el `id` no existe, ChromaDB no falla — es un no-op silencioso.

---

### `update_metadata()`

```python
async def update_metadata(
    note_id: str,
    metadata: dict,
) -> None:
```

**Comportamiento:**

1. Serializa metadata al formato ChromaDB.
2. Ejecuta `collection.update(ids=[note_id], metadatas=[metadata])`.
3. No recalcula el embedding — solo actualiza metadata (para cambios de path, status, project, etc.).

---

### `query_similar()`

```python
async def query_similar(
    query_text: str,
    n_results: int = 10,
    threshold: float | None = None,
    where: dict | None = None,
) -> list[SimilarNote]:
```

**Tipos de retorno:**

```python
@dataclass
class SimilarNote:
    note_id: str         # stem del archivo
    path: str            # path relativo al vault
    distance: float      # distancia coseno (0 = idéntico, 2 = opuesto)
    metadata: dict       # metadata completa de ChromaDB
    snippet: str | None  # fragmento del document almacenado
```

**Comportamiento:**

1. Calcula el embedding de `query_text` via Gemini Embedding API.
2. Ejecuta `collection.query(query_embeddings=[embedding], n_results=n_results, where=where, include=["documents", "metadatas", "distances"])`.
3. Filtra resultados con `distance > threshold` (si `threshold` provisto). La distancia coseno va de 0 (idéntico) a 2 (opuesto). Para la conversión a similitud: `similitud = 1 - (distance / 2)`.
4. Excluye notas archivadas por default: inyecta `{"status": {"$ne": "archived"}}` en el filtro `where` salvo que el caller pida explícitamente incluirlas.
5. Retorna lista de `SimilarNote` ordenada por distancia ascendente (más similar primero).

**Uso para sugerir links (Fase 2):**
```python
candidates = await query_similar(
    query_text=note_content,
    n_results=config.links.max_suggestions,
    threshold=config.links.similarity_threshold,
)
suggested_links = [{"note_id": c.note_id, "title": c.metadata.get("title", "")} for c in candidates]
```

**Uso para consultas RAG (Fase 7):**
```python
context_notes = await query_similar(
    query_text=user_query,
    n_results=config.rag.max_results,
    threshold=config.rag.similarity_threshold,
    where={"project": "tesis"},  # scope del usuario
)
```

---

### `reindex_vault()`

```python
async def reindex_vault(
    vault_path: Path,
    exclude_dirs: list[str],
) -> dict:
```

**Comportamiento:**

1. Recorre todos los `.md` del vault (excluyendo `exclude_dirs`).
2. Para cada nota: calcula embedding y upsert en ChromaDB.
3. Detecta notas en ChromaDB que ya no existen en el vault → las elimina.
4. Retorna estadísticas: `{"indexed": N, "removed": M, "errors": K}`.

**Uso:** cron nocturno (`reindex.time` en `config.yaml`). Reconcilia ChromaDB con el vault ante cualquier drift.

**Rate limiting:** procesa notas en batches con delay entre llamadas a la Embedding API para respetar los límites del free tier de Gemini. El batch size y delay se ajustan dinámicamente ante respuestas 429.

---

## Compatibilidad con Obsidian — reglas para el writer

### YAML frontmatter

El writer usa `python-frontmatter` que a su vez usa PyYAML. Reglas críticas:

| Situación | Qué hacer |
|---|---|
| Strings con `:` | PyYAML los quotea automáticamente: `title: "Part 1: Introduction"` |
| Strings que parecen booleans (`yes`, `no`, `true`, `on`) | Forzar quotes. No usar como valores de campos — ADSO usa `active/pending/done` que no tienen este problema |
| Tags en frontmatter | Sin `#`: `tags: [paper, ml]`, **no** `tags: [#paper, #ml]`. Obsidian agrega `#` al renderizar |
| Wikilinks en valores YAML | Requieren quotes: `related: ["[[nota-a]]", "[[nota-b]]"]` — sin quotes el `[[` rompe el parseo |
| Listas | Ambas formas válidas: `tags: [a, b]` (inline) y bloque con `- a` |
| Campos nulos | Omitir del frontmatter en vez de escribir `campo: null` |
| `---` delimitadores | Deben ser la primera y última línea del bloque. Primera línea del archivo debe ser `---` |
| Propiedades anidadas | No usar — Obsidian no las soporta en su UI de Properties |

**Tipado nativo de Obsidian Properties — reglas críticas:**

| Campo | Tipo Obsidian | Regla |
|---|---|---|
| `date_created`, `date_modified`, `scheduled` | `Date & time` | **Sin comillas.** `_clean_frontmatter()` convierte el string ISO a objeto `datetime` antes del dump — PyYAML lo serializa como timestamp YAML sin comillas. Con comillas, Obsidian lo trata como Text y no muestra el widget de calendario. |
| `due_date` | `Date` | **Sin comillas.** `_clean_frontmatter()` convierte a objeto `date` (solo fecha). |
| `source_file` | `List of links` | Valor como wikilink: `"[[archivo.pdf]]"`. Permite navegación directa desde Properties. |
| `related` | `List of links` | Links siempre entre comillas dobles dentro del array: `["[[nota]]"]`. |
| `project`, `area` | `Text` | Texto plano (nombre de carpeta). **No** wikilinks — facilita `WHERE project = "x"` en Dataview. |
| `read_status`, `priority`, `status` | `Text` (enum) | Valores de texto, no Checkbox. Obsidian ofrece autocompletado en la UI de Properties. |

La conversión de fechas ocurre en `_clean_frontmatter()` en `vault_writer.py` y aplica a todos los paths de escritura (`create_note`, `append_to_note`, `set_property`, `update_wikilinks`).

### Nombres de archivo

Caracteres prohibidos en todas las plataformas (Obsidian cross-platform):

```
# | ^ [ ] \ / : * ? " < >
```

El patrón `YYYY-MM-DD-{slug}.md` con `python-slugify` (kebab-case, max 60 chars) evita todos estos caracteres. El slug solo produce `[a-z0-9-]`.

### Wikilinks

| Forma | Ejemplo | Cuándo usar |
|---|---|---|
| Link simple | `[[baseline-cnn]]` | Siempre — Obsidian resuelve por filename unique |
| Link con alias | `[[baseline-cnn\|Resultados CNN]]` | Cuando el slug es poco legible |
| Link a heading | `[[baseline-cnn#Métodos]]` | Para apuntar a una sección específica |
| Embed de archivo | `![[martinez_2024.pdf]]` | Para archivos en `03-Resources/` |
| Embed con página | `![[martinez_2024.pdf#page=3]]` | Para PDFs en una página específica |

El writer genera links en forma simple (`[[stem]]`) porque el patrón `YYYY-MM-DD-slug` garantiza unicidad. Los alias se agregan solo si el LLM los sugiere.

### URI scheme `obsidian://`

Para construir links clicables en Google Tasks y en informes `.md`:

```python
from urllib.parse import quote

def obsidian_uri(vault_name: str, file_path: str) -> str:
    """Construye un URI obsidian:// para abrir una nota.

    Args:
        vault_name: Nombre del vault en Obsidian.
        file_path: Path relativo al vault, sin extensión .md.
    """
    return f"obsidian://open?vault={quote(vault_name, safe='')}&file={quote(file_path, safe='')}"
```

Caracteres que requieren encoding: `/` → `%2F`, `#` → `%23`, `^` → `%5E`, espacios → `%20`.
