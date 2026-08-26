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

# Estructura del Vault de Obsidian

## Metodología: PARA adaptado

Se adopta el método PARA (Tiago Forte) como estructura base del vault, adaptado para ingesta automatizada via bot con soporte de proyectos y secciones dinámicas.


```
vault/
├── 00-Inbox/                    # Notas sin clasificar (baja confianza del bot)
├── 01-Projects/                 # Proyectos activos (tienen inicio y fin)
│   ├── tesis/
│   │   ├── _index.md            # Nota índice del proyecto
│   │   ├── introduccion/        # Sección
│   │   ├── experimentos/        # Sección
│   │   └── papers/              # Sección
│   └── adso/
│       ├── _index.md
│       └── diseno/              # Sección
├── 02-Areas/                    # Dominios de responsabilidad continua (sin fin) — concepto PARA
│   ├── docencia/
│   │   └── _index.md            # Nota índice del área (con description requerida)
│   ├── investigacion/
│   │   └── _index.md
│   └── personal/                # (ilustrativo — las áreas reales se crean según necesidad)
│       └── _index.md
├── 03-Resources/                # Material de referencia permanente + archivos adjuntos (PDFs, imágenes, .txt, .py, etc.)
│                                # No tiene ciclo de vida — los proyectos linkean a esto, no lo mueven
└── 05-Archive/                  # Proyectos archivados — excluidos del índice de ChromaDB
```

> **Archivos adjuntos (PDFs, imágenes, .txt, .py, etc.):** siempre se guardan en `03-Resources/`, independientemente de dónde se clasifique la nota `.md` asociada. La nota referencia al archivo con un embed `![[archivo]]`, que Obsidian resuelve automáticamente. Esto centraliza los archivos crudos en un solo lugar y mantiene las carpetas de proyectos y áreas limpias de binarios.

```
```

---

## Taxonomía: Proyecto → Sección → Nota

### Proyecto
Tiene un tema, un inicio y un fin. Agrupa todo el trabajo relacionado con un objetivo concreto. Tiene una nota índice `_index.md`.

Ejemplos: `tesis`, `adso`, `curso-python`, proyectos del trabajo

### Área
Dominio de responsabilidad continua sin fecha de cierre — el concepto PARA original. Las áreas agrupan tareas, notas e ideas que no pertenecen a ningún proyecto activo. Son estables y se crean via el bot o desde `vault_seed` en `config.yaml` (ej: `docencia`, `investigacion`, `personal`). Cada área tiene un `_index.md` con `description` requerida.

Ejemplos de tareas en `docencia/`: "preparar guía de ejercicios de X materia". Ejemplos en `personal/`: "renovar credencial", "llamar al banco".

### Sección
Subdivisión temática dentro de un proyecto. No es un proyecto — es una categoría organizativa. Se crea dinámicamente cuando aparece contenido que no encaja en secciones existentes.

Ejemplos dentro de `tesis`: `introduccion`, `experimentos`, `trabajos-futuros`, `papers`

---

## Ciclo de vida de proyectos e ideas

```
Nota con type:idea en 02-Areas/{area}/
        │
        │  "convertir en proyecto" → nota se mueve, no queda copia
        ▼
Proyecto activo (01-Projects/)    ←── también puede crearse desde cero
        │
        │  se completa o abandona
        ▼
Archivo (05-Archive/)
        │
        │  borrar (doble confirmación)
        ▼
     eliminado
```

Los resources no tienen ciclo de vida — son referencia permanente. Los proyectos los linkean con `[[wikilinks]]` pero nunca los mueven.

Las áreas no tienen ciclo de vida — existen indefinidamente.

---

## Operaciones de gestión

Solo las tres primeras están implementadas. `_cb_manage_confirm` (`adso/handlers/manage.py`) resuelve `create_project`, `create_area` y `create_section`; cualquier otra operación cae al `else` y responde *"Operación 'X' todavía no está disponible"*. Las filas marcadas como diseño se conservan porque describen el comportamiento previsto, no el actual.

| Operación | Estado | Confirmación | Qué hace |
|---|---|---|---|
| Crear proyecto | ✅ implementado | Simple | Crea `01-Projects/{nombre}/` + `_index.md` con description requerida |
| Crear área | ✅ implementado | Simple | Crea `02-Areas/{nombre}/` + `_index.md` con description requerida |
| Crear sección | ✅ implementado | Simple | Crea subcarpeta dentro de un proyecto |
| Convertir idea en proyecto | ❌ *(diseño — no implementado)* | Simple | Mueve la nota de `02-Areas/` a `01-Projects/`, no queda copia |
| Archivar proyecto | ❌ *(diseño — no implementado)* | Simple | Mueve carpeta a `05-Archive/`, actualiza `status: archived` en `_index.md` y en metadata de ChromaDB |
| Desarchivar proyecto | ❌ *(diseño — no implementado)* | Simple | Mueve de `05-Archive/` a `01-Projects/`, actualiza `status: active` en `_index.md` y en metadata de ChromaDB |
| Borrar proyecto | ❌ *(diseño — no implementado)* | Doble + resolución de backlinks | Ver reglas abajo |
| Borrar área | ❌ *(diseño — no implementado)* | Simple (muestra cuántas notas se mueven) | Mueve notas internas a `00-Inbox/`, borra carpeta, actualiza ChromaDB |
| Renombrar proyecto/área | ❌ *(diseño — no implementado)* | Simple | Renombra carpeta, actualiza ChromaDB y `_index.md` |
| Mover nota | ❌ fuera de scope | — | **Fuera de scope como comando del bot** — existe la primitiva `move_note()` en `vault_writer.py` (la usan otros flujos), pero no hay flujo de usuario para mover una nota suelta |
| Borrar nota | ❌ *(diseño — no implementado)* | Simple o con aviso de backlinks | Ver reglas abajo |

Mientras tanto, archivar, renombrar, mover y borrar se hacen a mano en el filesystem o en Obsidian; el `VaultWatcher` reconcilia los embeddings, y el reindex nocturno limpia los huérfanos.

### Reglas de borrado de nota *(diseño — no implementado)*

Antes de borrar, el bot busca notas que referencian la nota a borrar con `[[wikilink]]`:

- **0 backlinks** → confirmación simple y borra. Elimina archivo y embeddings.
- **1+ backlinks** → muestra la lista de notas que apuntan a ella y avisa que quedarán links rotos. El usuario decide si confirma el borrado o cancela.

El bot nunca modifica automáticamente las notas que apuntan a la nota borrada — eso queda a criterio del usuario.

### Reglas de borrado de proyecto *(diseño — no implementado)*

Antes de borrar, el bot resuelve backlinks automáticamente según cuántas notas externas apuntan al proyecto:

- **0 backlinks** → doble confirmación (nombre del proyecto) y borra
- **1 backlink** → mueve las notas del proyecto al área/proyecto que contiene ese backlink, luego doble confirmación y borra
- **2+ backlinks** → mueve las notas al área/proyecto más frecuente entre los backlinks, luego doble confirmación y borra

En todos los casos: filesystem, ChromaDB y wikilinks quedan consistentes — no quedan links rotos.

> **Futuro:** mover notas entre proyectos/áreas desde el bot.

> **Nota:** ADSO es el **escritor principal** del vault (toda creación de notas pasa por Telegram), pero no el único: los clientes de Obsidian editan notas existentes y el `VaultWatcher` re-embede el cambio automáticamente (ver "Consideraciones de uso" más abajo). La afirmación de que los clientes son read-only quedó obsoleta con el sync bidireccional.

### Notas sobre archivar

- `05-Archive` está en `vault.exclude_dirs` (`config.yaml`), así que **queda fuera del índice de embeddings**. El reindex nocturno (`reindex_vault`) borra como huérfano todo ID que ya no aparezca en el scan: archivar una nota elimina su embedding, y desarchivarla lo recalcula desde cero. No se conserva metadata `status: archived` en ChromaDB.
- Por lo mismo, las búsquedas semánticas **no pueden** incluir archivados: no hay vectores que consultar ni opción de "buscar también en archivados".
- Los wikilinks que apunten a notas archivadas siguen funcionando (Obsidian resuelve por nombre de archivo, no por ruta)
- Archivar es reversible; borrar no lo es — pero en ambos casos los embeddings se eliminan

---

## Comportamiento del bot ante la taxonomía

1. Analiza el input e identifica proyecto y sección destino a partir del contenido
2. Si el proyecto **existe** → propone la sección más apropiada entre las existentes
3. Si el proyecto **no existe** → sugiere crearlo y pide confirmación
4. Si la sección **no existe** → sugiere el nombre y pide confirmación
5. Siempre muestra un preview antes de escribir — nada se persiste sin confirmación

---

## Tipos de nota y destino

| Tipo | Destino | Descripción |
|---|---|---|
| `reference` | `01-Projects/{proyecto}/{seccion}/`, `02-Areas/{area}/`, o `00-Inbox/` si no tiene proyecto ni área (el preview lo muestra como destino; se cambia con `[Reubicar]`) | Contenido de referencia. Incluye papers académicos (con campos opcionales: authors, year, doi, methods, etc.) |
| `task` | `01-Projects/{proyecto}/` si tiene proyecto, `02-Areas/{area}/` si tiene área, o `00-Inbox/` si no tiene ninguno — más Google Tasks | Tarea. Mismo orden que el resto de los tipos: **project gana sobre area** (`_resolve_dest_dir` en `vault_writer.py`), una task con `project` va al proyecto aunque tenga `area` seteada |
| `idea` | `01-Projects/{proyecto}/{seccion}/`, `02-Areas/{area}/`, o `00-Inbox/` si no tiene proyecto ni área (se cambia con `[Reubicar]`) | Idea exploratoria — se promueve a proyecto o se descarta |
| `idea` (sin destino) | `00-Inbox/` | Bot no pudo clasificar con confianza — `status: pending-classification` |
| `project-index` | `01-Projects/{proyecto}/` | Nota índice de proyecto — auto-generada, no clasificada por el LLM |
| `area-index` | `02-Areas/{area}/` | Nota índice de área — auto-generada, no clasificada por el LLM |

---

## Convenciones de nomenclatura

- **Archivos:** `YYYY-MM-DD-titulo-en-kebab-case.md`
- **Carpetas de proyecto/sección:** lowercase, sin espacios, con guiones
- **Nota índice de proyecto:** `_index.md` (prefijo `_` para que aparezca primero)
- Sin caracteres especiales en nombres de archivo

---

## Plugins recomendados

| Plugin | Propósito |
|---|---|
| **Dataview** | Queries avanzadas sobre el frontmatter (esencial) |
| **Bases** (core) | Vistas tipo spreadsheet, edición inline de propiedades |
| **Graph Analysis** | Co-citaciones, detección de comunidades, predicción de links |
| **Strange New Worlds** | Contador de referencias inline — identifica conceptos hub |
| **Charts View** | Gráficos temporales de actividad, métodos, temas |
| **Canvas** | Mapas visuales de literatura y planificación de investigación |

> El plugin **Local REST API** no se utiliza. El bot escribe directamente al filesystem via volumen Docker.

---

## Consideraciones de uso

- Obsidian **no necesita estar abierto** para que el bot funcione
- El cliente visual de Obsidian se usa opcionalmente desde otras computadoras
- Syncthing bidireccional — los clientes pueden editar notas existentes; `VaultWatcher` detecta los cambios y actualiza los embeddings automáticamente (ver `docs/architecture.md`)
- El vault es Markdown plano — legible y editable sin Obsidian si fuera necesario

### Conflictos de Syncthing

Syncthing genera archivos `.sync-conflict-*` cuando un archivo se modifica simultáneamente en dos dispositivos (ej: el usuario edita en Obsidian mientras ADSO actualiza la misma nota).

Política:
- ADSO **nunca resuelve conflictos automáticamente** — solo notifica al usuario por Telegram
- `VaultWatcher` (`watchdog`) corre en background y detecta archivos `.sync-conflict-*` en tiempo real via `inotify`
- Al detectar uno, envía un mensaje por Telegram indicando el archivo y la carpeta afectada
- El usuario resuelve manualmente y borra el archivo de conflicto
- Los cambios externos normales (sin conflicto) se re-embeds automáticamente sin notificación (salvo `watcher.debug: true`)

---

## Referencias

- [PARA Method - Tiago Forte](https://www.dsebastien.net/2022-04-26-para/)
- [Obsidian Starter Kit](https://github.com/shuvangkardas/obsidian-starter-kit)
- [kepano vault template](https://github.com/kepano/kepano-obsidian)
