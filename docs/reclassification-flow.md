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

# Flujo de reclasificación de notas degradadas

Especificación del flujo de captura en modo degradado (LLM no disponible) y
reclasificación posterior. Incluye cambios al cron existente, nuevo comando
`/clasificar` e integración con `/status`.

---

## Contexto

Cuando el LLM no responde al capturar una nota, el bot la guarda en `00-Inbox/`
con `status: pending-classification` y el contenido crudo envuelto en un callout
colapsable de advertencia (ver `docs/frontmatter-schema.md`). La nota queda
pendiente hasta ser reclasificada.

Una nota degradada necesita dos cosas para estar completa:

1. **Destino** (`project` o `area` en frontmatter) — lo decide el usuario
2. **Tags, summary y body limpio** — los genera el LLM cuando vuelve a estar disponible

Estas dos cosas son independientes y pueden resolverse en distinto orden.

### Pérdida de contexto del usuario en modo degradado

Cuando el usuario manda un documento (PDF, link) acompañado de texto
("quiero leer esto esta semana", "esto es urgente"), ese texto es la señal
que el LLM usa para inferir `priority`, `relevance` y `context`. En modo
degradado, ese mensaje se pierde — solo se guarda el contenido extraído del
documento.

**Solución:** guardar el mensaje del usuario en el campo `user_context` del
frontmatter al caer en modo degradado. Cuando el LLM reclasifique, ese campo
se incluye en el prompt junto con el contenido del documento.

```yaml
user_context: "quiero leer esto esta semana"  # opcional — mensaje original del usuario
```

- Solo se guarda si el usuario mandó texto junto con el archivo
- Se incluye en el prompt de reclasificación como contexto adicional
- No se muestra en el preview al usuario (es metadata interna)
- Se elimina del frontmatter una vez que la nota es clasificada (los campos
  inferidos de él — `priority`, `relevance` — quedan en el frontmatter final)

---

## Estados posibles de una nota degradada

| `status` | `project`/`area` | Significado |
|---|---|---|
| `pending-classification` | vacío | Recién caída en degradado — falta todo |
| `pending-classification` | seteado | Usuario asignó destino — falta LLM |
| `active` (u otro) | seteado | Completamente clasificada — ya no es degradada |

---

## Cómo se asigna el destino

El usuario puede asignar `project`/`area` de dos formas:

### Desde el bot (flujo principal)
Al capturar en modo degradado, el bot muestra el preview con los botones:
`[Elegir área]` `[Elegir proyecto]` `[Inbox]`

Si el usuario elige área o proyecto, el frontmatter se actualiza con ese campo.
El `status` sigue siendo `pending-classification` — la nota no está completa
hasta que el LLM genere los tags y el body limpio.

### Desde Obsidian (flujo manual)
El usuario edita el frontmatter directamente en Obsidian, agrega `project` o
`area` y deja `status: pending-classification`. El cron lo detectará en el
próximo ciclo.

---

## Comportamiento del cron (`reclassify_inbox`)

El cron corre en background según `llm.degraded_retry_minutes` en `config.yaml`.
**No manda popups al usuario.** Su única función es completar la clasificación
silenciosamente cuando el LLM vuelve a estar disponible.

### Caso A — nota con destino ya asignado

Condición: `status: pending-classification` AND (`project` OR `area`) seteado.

1. Extrae el contenido original del callout de warning con `extract_original_from_degraded()`
2. Si existe `user_context` en el frontmatter, lo agrega al prompt de clasificación
3. Llama al LLM para clasificar
4. Si el LLM responde:
   - Preserva el `project`/`area` que el usuario asignó — no lo sobreescribe
   - Toma del resultado del LLM: `tags`, `summary`, `title` (si está vacío), `priority`, `relevance`, campos académicos si aplica
   - Genera el body limpio (con `[!summary]` callout para papers, Markdown libre para el resto)
   - Actualiza el frontmatter: setea `status` al valor correcto para el tipo (`active`, `pending`, `raw`)
   - Elimina `user_context` del frontmatter (ya fue consumido por el LLM)
   - Mueve la nota al destino (`01-Projects/{project}/` o `02-Areas/{area}/`)
   - Envía notificación breve al usuario: `"✓ Nota clasificada: {título} → {destino}"`
4. Si el LLM falla de nuevo: deja la nota como está, reintenta en el próximo ciclo

### Caso B — nota sin destino asignado

Condición: `status: pending-classification` AND sin `project` ni `area`.

El cron **no hace nada**. Estas notas esperan la intervención explícita del
usuario via `/clasificar`.

---

## Comando `/clasificar`

Procesa las notas de `00-Inbox/` con `status: pending-classification` que
**no tienen destino asignado**. Las notas del Caso A ya fueron resueltas por
el cron.

### Flujo

1. Busca notas con `status: pending-classification` sin `project` ni `area`
2. Si no hay ninguna: responde `"No hay notas pendientes de clasificar."`
3. Si hay: toma la primera, llama al LLM, muestra preview con keyboard completo:
   - `[Confirmar]` `[Corregir]` `[Cancelar]`
   - Si el LLM no sugirió destino: `[Elegir área]` `[Elegir proyecto]` `[Inbox]`
4. El usuario confirma → nota al destino, mismo flujo que captura normal
5. Si hay más pendientes: responde `"Quedan N notas más. Mandá /clasificar para continuar."`
   — de a una por comando, para no saturar

### Comportamiento si el LLM falla durante `/clasificar`

Notifica al usuario: `"El LLM no está disponible. La nota quedó en Inbox."`
No mueve ni modifica nada.

---

## Cambios a `/status`

Agrega al resumen existente:

```
📊 Estado del vault
─────────────────
Notas totales: 47
...
Inbox pendiente: 3 ⚠️   ← nuevo
```

Si `inbox_pendiente > 0`:
- Desglosa: `"Con destino asignado: N (el bot las procesa automáticamente)"`
- `"Sin destino: N"` + botón inline `[Clasificar inbox]` que dispara `/clasificar`

Si `inbox_pendiente == 0`: no muestra la sección de inbox.

---

## Archivos a modificar

| Archivo | Cambio |
|---|---|
| `adso/bot.py` | Refactorizar `reclassify_inbox` según Caso A/B; guardar `user_context` en modo degradado; agregar comando `/clasificar`; actualizar `_handle_status` con desglose de inbox |
| `adso/llm_client.py` | `build_system_prompt` acepta `user_context` opcional e lo inyecta en el prompt; `classify()` acepta parámetro `user_context` |
| `adso/vault_writer.py` | Sin cambios — `set_property`, `move_note` y `create_note` ya cubren todo |

---

## Invariantes a respetar

- **El destino asignado por el usuario nunca se sobreescribe.** Si el LLM propone
  un project/area distinto, se ignora — se usan los del frontmatter existente.
- **Nada se mueve sin que el LLM haya completado la clasificación.** Una nota
  con destino pero sin tags sigue en `pending-classification` en Inbox.
- **El cron no manda popups.** Solo notificaciones breves de confirmación cuando
  completa exitosamente.
- **`/clasificar` procesa de a una.** Evita saturar al usuario con múltiples
  previews seguidos.
- **El callout de warning se elimina** al generar el body limpio. La nota
  clasificada no tiene rastro del modo degradado salvo en el log.
