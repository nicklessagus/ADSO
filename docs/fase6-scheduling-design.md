# Fase 6 — Scheduling con Calendar y Tasks: Diseño

Documento de análisis previo a la implementación. Cubre el caso de uso de agendar trabajo desde lenguaje natural usando el vault como fuente de verdad.

---

## Caso de uso central

El usuario puede pedir cosas como:
- `"Reservame espacios de lectura para el proyecto tesis"`
- `"Agendame tiempo para revisar la metodología"`
- `"Bloques de trabajo para el área docencia esta semana"`

El bot tiene que interpretar el intent, encontrar los ítems relevantes del vault y crear eventos en Google Calendar (calendario ADSO dedicado).

---

## Capacidades disponibles

**Vault (búsqueda determinista):**
- `find_tasks(project, area, status)` — tareas por frontmatter + checkboxes inline
- `find_by_property(read_status=unread, project=X)` — papers pendientes de lectura
- Campos `due_date` y `scheduled` en frontmatter — ya capturados en clasificación
- `title`, `status`, `priority` — suficiente para describir un evento

**LLM:**
- `classify()` con `user_context` — extrae intent, proyecto, duración, urgencia
- Interpreta expresiones temporales ("esta semana", "el jueves", "los próximos 3 días")
- Puede rankear candidatos semánticamente dado un query

**Pendiente (Fase 6):**
- `calendar_client.py` — Google Calendar API
- `tasks_client.py` — Google Tasks API

---

## Dos sub-casos con confianza distinta

### Caso A — Scheduling por proyecto/área (confianza alta)

**Ejemplo:** `"Reservame bloques de lectura para tesis"`

Flujo casi determinista:
1. LLM extrae `{intent: schedule_work, work_type: reading, project: "tesis"}`
2. `find_by_property(read_status=unread, project="tesis")` → lista de papers pendientes
3. Bot muestra cuántos hay y propone bloque: `"Encontré 5 papers pendientes. ¿Cuánto tiempo reservamos?"`
4. Usuario confirma duración y día
5. Se crea evento con título `"Lectura: Tesis"` + links `obsidian://` a los papers en la descripción

**Riesgo:** bajo. El LLM solo interpreta lenguaje natural; la búsqueda es determinista.

---

### Caso B — Scheduling de tarea específica (confianza media)

**Ejemplo:** `"Agendame tiempo para revisar la metodología"`

El LLM tiene que resolver *qué tarea* corresponde al texto. El riesgo es que existan candidatos similares y elija mal sin avisarte.

**Regla:** el LLM nunca elige una tarea solo. Siempre muestra candidatos al usuario.

Flujo propuesto:
1. LLM extrae `{intent: schedule_task, query: "revisar metodología", week: "current"}`
2. `find_tasks(status=pending)` → todas las tareas pendientes del vault
3. LLM rankea los candidatos contra el query (no elige, rankea)
4. Bot muestra top 2–3 con botones inline: `[Revisar metodología estadística]` `[Revisar cap. 3 — metodología]` `[Otra]`
5. Usuario selecciona
6. Bot pide duración, propone día (o usa `due_date` si existe en el frontmatter)
7. Crea evento con link `obsidian://` a la nota de la tarea

**El LLM como rankeador semántico:** trabaja bien sobre candidatos ya encontrados por vault_search. Lo que se evita es que invente o suponga el nombre de la tarea.

---

## Tabla de confianza

| Responsabilidad | ¿Confiar en el LLM? |
|---|---|
| Extraer intent (lectura / trabajo / planning) | Sí |
| Extraer proyecto o área del lenguaje natural | Sí |
| Extraer duración, frecuencia, urgencia | Sí |
| Interpretar expresiones temporales | Sí |
| **Elegir una tarea específica sin mostrarte candidatos** | No |
| Rankear candidatos encontrados por vault_search | Sí |
| Generar descripción del evento con links obsidian | Sí |

---

## Nuevo intent en classify()

Agregar `mode: schedule` al schema del LLM, con payload:

```json
{
  "work_type": "reading | task | planning",
  "project": "nombre | null",
  "area": "nombre | null",
  "query": "texto libre para buscar tarea específica | null",
  "duration_minutes": 90,
  "frequency": "once | daily | weekly",
  "target_date": "2026-03-28 | null",
  "target_week": "current | next | null"
}
```

---

## calendar_client.py mínimo requerido

- Crear eventos en el calendario ADSO (solo escritura en ADSO, lectura en todos)
- Leer free/busy para proponer slots (opcional, agrega complejidad — evaluar si vale)
- Detectar duplicados: no crear dos eventos para la misma tarea en la misma semana
- Descripción del evento: título de la nota + links `obsidian://` + proyecto/área

---

## Plan de implementación sugerido

### Fase 6a — Scheduling por proyecto (más fácil, más útil ya)

Scope:
- Casos tipo A: lectura de papers pendientes, bloques de trabajo por proyecto/área
- Solo necesita vault_search + Calendar API básico
- Confirmación siempre: bot muestra qué incluye el bloque y propone slot → usuario confirma
- No requiere Tasks API

### Fase 6b — Scheduling de tarea específica + Tasks API

Scope:
- Casos tipo B: tarea específica por nombre en lenguaje natural
- Flujo de disambiguación con candidatos inline
- Tasks API para sincronizar `due_date` con Google Tasks
- Reconciliación bidireccional (ver decisiones en CLAUDE.md)

---

## Preguntas abiertas

- ¿Leer free/busy del calendario para proponer slots automáticamente, o siempre pedir al usuario que elija el día?
- ¿Frecuencia recurrente (ej: "1h de lectura todos los martes") o siempre eventos únicos?
- ¿Cuántos candidatos mostrar en el Caso B? (propuesta: máximo 3)
- ¿Qué pasa si no hay tareas pendientes para el proyecto pedido? ¿Proponer crear una?
