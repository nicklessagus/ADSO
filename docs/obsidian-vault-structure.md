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
├── 02-Areas/                    # Dominios de responsabilidad continua (sin fin) — concepto PARA
│   ├── docencia/                # Tareas, notas e ideas de docencia sin proyecto asignado
│   ├── investigacion/           # Tareas, notas e ideas de investigación sin proyecto asignado
│   └── personal/                # (ilustrativo — las áreas reales se crean según necesidad)
├── 03-Resources/                # Material de referencia permanente (papers sueltos, artículos, herramientas)
│                                # No tiene ciclo de vida — los proyectos linkean a esto, no lo mueven
├── 05-Archive/                  # Proyectos completados, pausados o eliminados
└── _assets/                     # Imágenes (fotos enviadas al bot)
```

> **Archivos adjuntos (PDFs, .txt, .py, etc.):** se guardan en la misma carpeta que su nota `.md`, no en `_assets/`. Esto permite que el embed `![[archivo]]` funcione naturalmente y mantiene la relación archivo-nota visible en el filesystem. `_assets/` se reserva para imágenes.

```
```

---

## Taxonomía: Proyecto → Sección → Nota

### Proyecto
Tiene un tema, un inicio y un fin. Agrupa todo el trabajo relacionado con un objetivo concreto. Tiene una nota índice `_index.md`.

Ejemplos: `tesis`, `adso`, `curso-python`, proyectos del trabajo

### Área
Dominio de responsabilidad continua sin fecha de cierre — el concepto PARA original. Las áreas agrupan tareas, notas e ideas que no pertenecen a ningún proyecto activo. Son estables y se crean manualmente según la estructura real del usuario (ej: `docencia`, `investigacion`, `personal`).

Ejemplos de tareas en `docencia/`: "preparar guía de ejercicios de X materia". Ejemplos en `personal/`: "renovar credencial", "llamar al banco".

### Sección
Subdivisión temática dentro de un proyecto. No es un proyecto — es una categoría organizativa. Se crea dinámicamente cuando aparece contenido que no encaja en secciones existentes.

Ejemplos dentro de `tesis`: `introduccion`, `experimentos`, `trabajos-futuros`, `papers`

### Subproyecto
Proyecto anidado dentro de otro que tiene objetivo y ciclo de vida propios. Se modela como carpeta con su propio `_index.md` dentro de un proyecto padre.

Ejemplo: una herramienta desarrollada durante la tesis que luego tiene vida propia.

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

## Operaciones de gestión soportadas por el bot

| Acción | Confirmación | Reversible |
|---|---|---|
| Crear proyecto / subproyecto | Sí | — |
| Crear sección | Sí | — |
| Convertir idea en proyecto | Sí | La nota se mueve de su área a Projects, no se borra |
| Archivar proyecto | Sí | Sí — se puede desarchivar |
| Borrar proyecto | Doble confirmación | No |
| Borrar nota | Sí | No |

El borrado de proyecto requiere doble confirmación: primero "¿seguro?" y luego confirmación explícita del nombre del proyecto.

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
| `note` | `01-Projects/{proyecto}/{seccion}/` o `03-Resources/` (si es referencia suelta, sin proyecto) | Nota general. Incluye papers académicos (con campos opcionales: authors, year, doi, methods, etc.) |
| `task` | `02-Areas/{area}/` + Google Tasks | Tarea sin proyecto activo — el área determina la carpeta destino |
| `idea` | `02-Areas/{area}/` | Intención sin proyecto definido — se promueve a proyecto o se descarta |
| `inbox` | `00-Inbox/` | Bot no pudo clasificar con confianza |
| `project-index` | `01-Projects/{proyecto}/` | Nota índice de proyecto — auto-generada, no clasificada por el LLM |

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
- Syncthing en modo send-only desde la RPi4 — los clientes reciben cambios pero no los envían (ver `docs/architecture.md`)
- El vault es Markdown plano — legible y editable sin Obsidian si fuera necesario

### Conflictos de Syncthing

Syncthing genera archivos `.sync-conflict-*` cuando un archivo se modifica simultáneamente en dos dispositivos (ej: el usuario edita en Obsidian mientras ADSO actualiza la misma nota).

Política:
- ADSO **nunca resuelve conflictos automáticamente** — solo notifica al usuario por Telegram
- Un watcher de filesystem (`watchdog`) corre como tarea async en background y detecta archivos `.sync-conflict-*` en tiempo real
- Al detectar uno, envía un mensaje por Telegram indicando el archivo y la carpeta afectada
- El usuario resuelve manualmente y borra el archivo de conflicto

---

## Referencias

- [PARA Method - Tiago Forte](https://www.dsebastien.net/2022-04-26-para/)
- [Obsidian Starter Kit](https://github.com/shuvangkardas/obsidian-starter-kit)
- [kepano vault template](https://github.com/kepano/kepano-obsidian)
