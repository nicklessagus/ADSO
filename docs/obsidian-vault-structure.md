# Estructura del Vault de Obsidian para ADSO

## Metodología: PARA adaptado

Se adopta el método PARA (Tiago Forte) como estructura base del vault, adaptado para ingesta automatizada via bot con soporte de proyectos, secciones dinámicas y subproyectos.

```
vault/
├── 00-Inbox/                    # Notas sin clasificar (baja confianza del bot)
├── 01-Projects/                 # Proyectos activos (tienen inicio y fin)
│   ├── tesis/
│   │   ├── _index.md            # Nota índice del proyecto
│   │   ├── introduccion/
│   │   ├── experimentos/
│   │   ├── trabajos-futuros/
│   │   └── papers/
│   ├── trabajo/
│   │   ├── _index.md
│   │   ├── proyecto-x/          # Subproyecto
│   │   │   ├── _index.md
│   │   │   └── ...
│   │   └── proyecto-y/
│   └── adso/
│       ├── _index.md
│       └── ...
├── 02-Areas/                    # Responsabilidades continuas (sin fin)
│   └── tareas/                  # Tareas sin proyecto ni fecha
├── 03-Resources/                # Conocimiento atemporal
│   └── ideas/                   # Ideas sin proyecto definido
├── 04-Archive/                  # Proyectos completados, pausados o eliminados
└── _assets/                     # Imágenes y adjuntos
```

---

## Taxonomía: Proyecto → Sección → Nota

### Proyecto
Tiene un tema, un inicio y un fin. Agrupa todo el trabajo relacionado con un objetivo concreto. Tiene una nota índice `_index.md`.

Ejemplos: `tesis`, `adso`, `curso-python`, proyectos del trabajo

### Área
No tiene fin. Es una responsabilidad o dominio que se mantiene indefinidamente mientras sea relevante.

Ejemplos: `tareas` (pendientes del día a día)

### Sección
Subdivisión temática dentro de un proyecto. No es un proyecto — es una categoría organizativa. Se crea dinámicamente cuando aparece contenido que no encaja en secciones existentes.

Ejemplos dentro de `tesis`: `introduccion`, `experimentos`, `trabajos-futuros`, `papers`

### Subproyecto
Proyecto anidado dentro de otro que tiene objetivo y ciclo de vida propios. Se modela como carpeta con su propio `_index.md` dentro de un proyecto padre.

Ejemplo: una herramienta desarrollada durante la tesis que luego tiene vida propia.

### Ideas
Conocimiento o iniciativas sin proyecto asignado. Pueden convertirse en proyectos mediante una operación explícita del bot.

---

## Ciclo de vida de proyectos e ideas

```
Idea (03-Resources/ideas/)
        │
        │  "convertir en proyecto"
        ▼
Proyecto activo (01-Projects/)
        │
        │  se completa o abandona
        ▼
Archivo (04-Archive/)
        │
        │  borrar (doble confirmación)
        ▼
     eliminado
```

Las áreas no tienen ciclo de vida — existen indefinidamente.

---

## Operaciones de gestión soportadas por el bot

| Acción | Confirmación | Reversible |
|---|---|---|
| Crear proyecto / subproyecto | Sí | — |
| Crear sección | Sí | — |
| Convertir idea en proyecto | Sí | La idea se mueve, no se borra |
| Archivar proyecto | Sí | Sí — se puede desarchivar |
| Borrar proyecto | Doble confirmación | No |
| Borrar nota | Sí | No |

El borrado de proyecto requiere doble confirmación: primero "¿seguro?" y luego confirmación explícita del nombre del proyecto.

---

## Comportamiento del bot ante la taxonomía

1. Analiza el input e identifica proyecto y sección destino según el contexto activo
2. Si el proyecto **existe** → propone la sección más apropiada entre las existentes
3. Si el proyecto **no existe** → sugiere crearlo y pide confirmación
4. Si la sección **no existe** → sugiere el nombre y pide confirmación
5. Siempre muestra un preview antes de escribir — nada se persiste sin confirmación

---

## Tipos de nota y destino

| Tipo | Destino | Descripción |
|---|---|---|
| `project-note` | `01-Projects/{proyecto}/{seccion}/` | Nota dentro de un proyecto |
| `paper` | `01-Projects/{proyecto}/papers/` | Paper académico con metadatos |
| `task` | `02-Areas/tareas/` + Google Tasks | Tarea sin fecha (con fecha → también Google Calendar) |
| `idea` | `03-Resources/ideas/` | Idea sin proyecto definido, con ciclo de vida propio |
| `inbox` | `00-Inbox/` | Bot no pudo clasificar con confianza |

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
- Estrategia de sync pendiente de decisión (ver opciones en `docs/architecture.md`)
- El vault es Markdown plano — legible y editable sin Obsidian si fuera necesario

### Conflictos de Syncthing

Syncthing genera archivos `.sync-conflict-*` cuando un archivo se modifica simultáneamente en dos dispositivos (ej: el usuario edita en Obsidian mientras ADSO actualiza la misma nota).

Política:
- ADSO **nunca resuelve conflictos automáticamente** — solo notifica al usuario por Telegram
- Un cron periódico escanea el vault buscando archivos `.sync-conflict-*`
- Si encuentra alguno, envía un mensaje: "Hay N conflictos de sync pendientes: [lista de archivos]"
- El usuario resuelve manualmente y borra el archivo de conflicto

---

## Referencias

- [PARA Method - Tiago Forte](https://www.dsebastien.net/2022-04-26-para/)
- [Obsidian Starter Kit](https://github.com/shuvangkardas/obsidian-starter-kit)
- [kepano vault template](https://github.com/kepano/kepano-obsidian)
