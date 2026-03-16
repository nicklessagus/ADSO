# Estructura del Vault de Obsidian para Adso

## Metodología: PARA

Se adopta el método PARA (Tiago Forte) como estructura base del vault, adaptado para ingesta automatizada via bot.

```
vault/
├── 00-Inbox/           # Notas sin clasificar, entrada del bot
├── 01-Projects/        # Proyectos activos con objetivo y fecha límite
├── 02-Areas/           # Responsabilidades continuas (sin fecha límite)
├── 03-Resources/       # Conocimiento de referencia, ideas, investigación
├── 04-Archive/         # Items inactivos o completados
├── 05-Templates/       # Plantillas de notas (uso interno de Obsidian)
└── _assets/            # Imágenes, adjuntos
```

### Descripción de cada carpeta

| Carpeta | Propósito | Ejemplos |
|---|---|---|
| `00-Inbox` | Landing zone del bot. Todo ingresa aquí primero | Mensajes sin clasificar de Telegram |
| `01-Projects` | Trabajo activo con deadline implícito o explícito | "Desarrollo de Adso", "Preparar presentación X" |
| `02-Areas` | Dominios de responsabilidad continua | Salud, aprendizaje, finanzas personales |
| `03-Resources` | Conocimiento atemporal consultable | Artículos guardados, ideas, conceptos técnicos |
| `04-Archive` | Proyectos terminados, notas obsoletas | Proyectos completados movidos desde Projects |
| `05-Templates` | Plantillas para el bot y uso manual | Templates de nota diaria, proyecto, idea |

---

## Estrategia de clasificación automática (bot)

El bot clasifica cada mensaje entrante en una de estas categorías:

- `inbox` → `00-Inbox/` (no pudo clasificar con confianza)
- `project` → `01-Projects/`
- `area` → `02-Areas/`
- `resource` → `03-Resources/`
- `task` → `02-Areas/Tasks/`
- `idea` → `03-Resources/Ideas/`

El umbral de confianza para clasificar fuera de Inbox se define en configuración del bot.

---

## Convenciones de nomenclatura

- Archivos: `YYYY-MM-DD titulo-en-kebab-case.md`
- Carpetas: numeradas para orden visual, lowercase con guiones
- Sin espacios en nombres de archivo

---

## Plugins recomendados

| Plugin | Propósito |
|---|---|
| **Dataview** | Queries dinámicas sobre el vault (ej: "todas las tareas pendientes") |
| **Local REST API** | Permite al bot escribir notas via HTTP sin acceso directo al filesystem |
| **Tasks** | Gestión de tareas con sintaxis extendida |
| **Smart Connections** | Búsqueda semántica y sugerencias de links entre notas |
| **Templater** | Templates con lógica dinámica |
| **Calendar** | Vista de notas diarias en formato calendario |

---

## Referencias

- [PARA Method - Tiago Forte](https://www.dsebastien.net/2022-04-26-para/)
- [Obsidian Starter Kit](https://github.com/shuvangkardas/obsidian-starter-kit)
- [kepano vault template](https://github.com/kepano/kepano-obsidian)
- [Local REST API plugin](https://coddingtonbear.github.io/obsidian-local-rest-api/)
