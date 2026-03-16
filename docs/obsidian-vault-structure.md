# Estructura del Vault de Obsidian para Adso

## Metodología: PARA adaptado

Se adopta el método PARA (Tiago Forte) como estructura base del vault, adaptado para ingesta automatizada via bot con soporte de proyectos, secciones dinámicas y subproyectos.

```
vault/
├── 00-Inbox/                    # Notas sin clasificar (baja confianza del bot)
├── 01-Projects/                 # Proyectos activos
│   ├── tesis/                   # Ejemplo: proyecto "Tesis"
│   │   ├── _index.md            # Nota índice del proyecto
│   │   ├── introduccion/        # Sección (creada dinámicamente)
│   │   ├── experimentos/        # Sección
│   │   ├── trabajos-futuros/    # Sección
│   │   └── papers/              # Sección
│   └── adso/                    # Ejemplo: proyecto "Adso"
│       ├── _index.md
│       └── ...
├── 02-Areas/                    # Responsabilidades continuas
│   └── tareas/                  # Tareas sin proyecto ni fecha
├── 03-Resources/                # Conocimiento atemporal
│   └── ideas/                   # Ideas sin proyecto definido
├── 04-Archive/                  # Proyectos completados o inactivos
└── _assets/                     # Imágenes y adjuntos
```

---

## Taxonomía: Proyecto → Sección → Nota

### Proyecto
Agrupa todo el trabajo relacionado con un objetivo. Tiene una nota índice `_index.md` con metadatos del proyecto.

Ejemplos: `tesis`, `adso`, `curso-python`

### Sección
Subdivisión temática dentro de un proyecto. No tiene objetivo propio — es una categoría organizativa. Se crea dinámicamente cuando aparece contenido que no encaja en secciones existentes.

Ejemplos dentro de `tesis`: `introduccion`, `experimentos`, `trabajos-futuros`, `papers`

### Subproyecto
Proyecto anidado que tiene vida propia y podría existir independientemente. Se modela como un proyecto dentro de otro.

Ejemplo: una herramienta desarrollada durante la tesis que luego se publica por separado.

---

## Comportamiento del bot ante la taxonomía

1. El bot analiza el input e identifica proyecto y sección destino
2. Si el proyecto **existe** → propone la sección más apropiada
3. Si el proyecto **no existe** → sugiere crearlo y pide confirmación
4. Si la sección **no existe** → sugiere el nombre y pide confirmación
5. Siempre muestra un preview antes de escribir — nada se persiste sin confirmación

---

## Tipos de nota y destino

| Tipo | Destino | Descripción |
|---|---|---|
| `project-note` | `01-Projects/{proyecto}/{seccion}/` | Nota dentro de un proyecto |
| `paper` | `01-Projects/{proyecto}/papers/` | Paper académico con metadatos |
| `task` | `02-Areas/tareas/` | Tarea sin proyecto ni fecha (con fecha → Google Calendar) |
| `idea` | `03-Resources/ideas/` | Idea sin proyecto definido |
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
| **Dataview** | Queries dinámicas sobre el vault |
| **Tasks** | Gestión de tareas con sintaxis extendida |
| **Smart Connections** | Búsqueda semántica entre notas |
| **Templater** | Templates con lógica dinámica |
| **Calendar** | Vista de notas en formato calendario |

> El plugin **Local REST API** no se utiliza. El bot escribe directamente al filesystem via volumen Docker.

---

## Consideraciones de uso

- Obsidian **no necesita estar abierto** para que el bot funcione
- El cliente visual de Obsidian se usa opcionalmente desde otras computadoras
- Syncthing corre en el host de la RPi4 y sincroniza el vault a los dispositivos del usuario
- El vault es Markdown plano — legible y editable sin Obsidian si fuera necesario

---

## Referencias

- [PARA Method - Tiago Forte](https://www.dsebastien.net/2022-04-26-para/)
- [Obsidian Starter Kit](https://github.com/shuvangkardas/obsidian-starter-kit)
- [kepano vault template](https://github.com/kepano/kepano-obsidian)
