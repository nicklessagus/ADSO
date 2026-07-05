# Auditoría general 2026-07-05 — propuestas de mejora

Resultado de la revisión profunda post-release 1.1.0: tres pasadas de análisis
(captura/UX, LLM/embeddings/jobs, vault/backup/tasks) sobre todo el código.
Este documento deja cada propuesta con el detalle suficiente para implementarla
—o descartarla— más adelante, sin re-ejecutar el análisis.

**Estados de verificación:**

- **Confirmado** — verificado contra el código el 2026-07-05.
- **Plausible** — reportado por la revisión; re-verificar contra el código antes de implementar.

Cada ítem tiene una línea `Decisión:` para marcar `implementar` / `descartar` / `pendiente`.

Orden sugerido de bloques (mayor a menor ratio impacto/costo): 1 → 2 → 3 → 4 → 5 → backlog.

---

## Bloque 1 — Quick wins: pérdida de datos y drift de docs (~1 sesión)

### 1.1 `GitBackup.flush()` al shutdown

- **Estado:** confirmado · **Impacto:** alto · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Problema:** el backup con debounce (`vault_writer.py`, `GitBackup`) programa el commit con
  `call_later` y no existe `stop()`/`flush()`. `_post_shutdown` (`bot.py:123-127`) solo detiene el
  watcher. Una nota escrita dentro de la ventana de `backup.debounce_seconds` (default 30s) antes
  de un shutdown queda sin commit/push hasta la *próxima* escritura. Contradice la regla de oro
  "sin pérdida de datos".
- **Propuesta:** agregar `GitBackup.flush()` — cancelar `_timer` bajo lock y ejecutar `_do_backup()`
  inmediatamente si hay trabajo pendiente — y `await`earlo en `_post_shutdown` antes de retornar.
- **Archivos:** `adso/vault_writer.py`, `adso/bot.py`.

### 1.2 Watcher: manejar `on_moved`

- **Estado:** confirmado (no existe el método en `_VaultEventHandler`) · **Impacto:** alto · **Esfuerzo:** bajo-medio · **Decisión:** pendiente
- **Problema:** `vault_watcher.py:44-93` implementa `on_created`/`on_modified`/`on_deleted` pero no
  `on_moved`. Inotify reporta renames como `FileMovedEvent`. Consecuencias:
  1. **Syncthing aplica cambios remotos escribiendo un temporal y renombrando** → las ediciones
     sincronizadas desde otros dispositivos no disparan re-embed (quedan esperando el reindex
     nocturno). Es exactamente el caso de uso central del watcher.
  2. Editores externos con guardado atómico (vim, etc.) tampoco disparan.
  3. Una nota renombrada externamente deja su embedding viejo huérfano en ChromaDB y el path
     nuevo sin indexar.
- **Propuesta:** implementar `on_moved(event)`: emitir un delete para `src_path` y un change para
  `dest_path`, respetando los filtros existentes (`.md`, regex de conflictos, dedup 2s). Consultar
  y limpiar `bot_written_paths` en este camino (ver 1.3).
- **Archivos:** `adso/vault_watcher.py`. Tests: simular move con `FileMovedEvent`.

### 1.3 `bot_written_paths` nunca se drena (leak + guard inefectivo)

- **Estado:** confirmado (consecuencia directa de 1.2) · **Impacto:** medio-alto · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Problema:** cada `create_note` agrega el path al set (`capture.py`, `jobs.py`); el único consumidor
  que hace `discard()` es `_reindex_external_note` (`bot.py:62-65`), que corre en `on_modified`/
  `on_created`. Pero el bot escribe con `os.replace` → el evento real es un move (no manejado) →
  la entrada no se remueve nunca. El set crece sin límite en uptime largo y el guard anti-doble-embed
  no actúa.
- **Propuesta:** resolver junto con 1.2 (consumir el set en `on_moved`). Defensa extra: TTL o tope
  de tamaño en el set (p. ej. dict path→timestamp, purga en cada inserción).
- **Archivos:** `adso/bot.py`, `adso/vault_watcher.py`.

### 1.4 Temporales de escritura atómica con sufijo `.md`

- **Estado:** confirmado (`vault_writer.py:101` — `prefix=".adso-tmp-", suffix=path.suffix`) · **Impacto:** medio · **Esfuerzo:** trivial · **Decisión:** pendiente
- **Problema:** el temp `.adso-tmp-XXXX.md` está en un directorio observado → dispara `on_created`
  espurio; `_reindex_external_note` procesa un archivo ya renombrado (trabajo perdido, notificación
  espuria en modo debug). También puede colarse en un `git add -A` concurrente.
- **Propuesta:** usar sufijo no-`.md` (p. ej. `.tmp`) para que el filtro `path.suffix != ".md"`
  del handler lo saltee. Verificar que ninguna lógica dependa del sufijo original.
- **Archivos:** `adso/vault_writer.py:101`.

### 1.5 `_get_existing_items` bloquea el event loop en cada captura

- **Estado:** confirmado (`bot_utils.py:193-224`) · **Impacto:** medio-alto en RPi4 · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Problema:** es `async def` pero hace `iterdir()` + `parse_cached()` de cada `_index.md`
  síncronamente, sin `asyncio.to_thread`. Es el único escaneo del vault que no usa `to_thread`
  (todo `vault_search.py` sí lo hace). Corre en **todo** flujo de clasificación, antes de cada
  `classify()`. Con caché frío o SD lenta congela el bot decenas/cientos de ms.
- **Propuesta:** envolver el cuerpo en `await asyncio.to_thread(...)`. Opcional: cachear el
  resultado con invalidación por mtime de `01-Projects/`/`02-Areas/` (cambian poco).
- **Archivos:** `adso/bot_utils.py`.

### 1.6 `/status` hace `rglob` completo bloqueante y saltea el caché

- **Estado:** plausible (`commands.py:108,117`) · **Impacto:** medio · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Problema:** `len(list(vault_path.rglob("*.md")))` corre en el event loop; el loop del inbox usa
  `read_note` por archivo en vez de `parse_cached`.
- **Propuesta:** mover el conteo a `to_thread` y reutilizar `_scan_vault`/`parse_cached`.
- **Archivos:** `adso/handlers/commands.py`.

### 1.7 Eliminar código muerto y parámetros fantasma

- **Estado:** confirmado · **Impacto:** bajo (funcional) / medio (mantenibilidad) · **Esfuerzo:** trivial · **Decisión:** pendiente
- **Problema y propuesta:**
  - `_handle_capture` y `_handle_degraded` (`capture.py:266-341`): ~76 líneas sin ningún caller
    (verificado con grep). Borrarlas.
  - `build_capture_keyboard(frontmatter, has_destination)` (`keyboards.py:119-141`): ignora ambos
    parámetros — el teclado es 100% estático. Decidir: o el teclado realmente varía con
    `has_destination` (intención original aparente), o se eliminan los parámetros, se convierte en
    constante de módulo y se borran todos los `_has_destination(fm)` que solo lo alimentan
    (`capture.py:247,305,670,1169`; `callbacks.py:151`).
  - `_cb_correct(query, context, vault_path)` (`capture.py:892`): `vault_path` no se usa. Quitar.
- **Archivos:** `adso/handlers/capture.py`, `adso/keyboards.py`, `adso/handlers/callbacks.py`.

### 1.8 Corregir drift de CLAUDE.md y docs

- **Estado:** confirmado · **Impacto:** medio (confianza en docs) · **Esfuerzo:** trivial · **Decisión:** pendiente
- **Problema:** dos afirmaciones del CLAUDE.md no reflejan el código:
  1. `handlers/jobs.py` listado con cron de "reporte semanal" — no existe tal job (ver 2.2).
  2. "Google Calendar y Tasks: sync cada 30 min... bidireccional (gana el último cambio)" — solo
     existe `create_task` unidireccional; el `task_id` se descarta (ver bloque 5).
- **Propuesta:** al decidir 2.2 y el bloque 5, actualizar CLAUDE.md en consecuencia (implementar la
  feature o degradar la doc a lo real). Mientras tanto, marcar ambos como "diseño, no implementado".
- **Archivos:** `CLAUDE.md`, `ROADMAP.md`.

---

## Bloque 2 — Informe breve inline + reporte semanal (~1 sesión)

### 2.1 Renderer breve inline para reportes (idea del usuario, recalibrada)

- **Estado:** diseño nuevo · **Impacto:** medio-alto (UX) · **Esfuerzo:** medio · **Decisión:** pendiente
- **Motivación real:** los links `obsidian://` no son clickeables en Telegram (la Bot API solo
  permite http/https/tg en links inline) y el usuario no tiene Obsidian configurado en el celular.
  Un resumen breve inline es el único formato de reporte que **no depende de Obsidian**. El `.md`
  completo queda como formato "de escritorio".
- **Dato clave de costo:** cada reporter ya computa `summary_parts` (conteos por sección) y una
  síntesis LLM de 2-3 oraciones (`_llm_synthesis`) que hoy quedan enterrados dentro del archivo.
  La versión breve es un render alternativo de datos existentes: **cero llamadas extra a Gemini,
  cero re-scans**.
- **Diseño:**
  1. Refactorizar `reporters.py`: cada reporter arma una estructura intermedia en vez de retornar
     `bytes` directo — p. ej. `@dataclass ReportData: title, synthesis, sections:
     list[Section(name, count, notes, extras)]`.
  2. Dos renderers puros: `render_md(data) -> bytes` (idéntico al output actual, con header ASCII)
     y `render_brief(data) -> str` (HTML de Telegram: síntesis + conteos por sección + top 3-5
     ítems más urgentes — tareas vencidas primero, luego papers high — como texto plano sin links,
     límite duro 4096 chars con truncado elegante).
  3. Flujo: al elegir el scope, el bot manda el brief inline con teclado
     `[Generar .md completo]` `[Cerrar]`. El botón genera el archivo **reutilizando la
     `ReportData` cacheada en `user_data`** (sin re-scan ni segunda síntesis LLM); con TTL corto o
     invalidación al salir del flujo para no servir datos viejos.
  4. `/reporte_full` mantiene su semántica actual (afecta solo al render `.md`).
- **Riesgo asumido por el usuario:** puede no verse bien — el diseño limita el brief a síntesis +
  conteos + top ítems justamente porque las listas largas de notas en un mensaje de Telegram
  quedan ruidosas. Testear con el vault real antes de dar por buena la UX.
- **Archivos:** `adso/reporters.py` (refactor mayor), `adso/handlers/reports.py`,
  `adso/keyboards.py`, `adso/constants.py` (callbacks nuevos). Tests de ambos renderers sobre la
  misma `ReportData`.

### 2.2 Implementar el `weekly_report_job` (hoy: config sin feature)

- **Estado:** confirmado (config completa en `config.py:116-165,211-233,262`; ningún job en
  `bot.py:203-217`) · **Impacto:** alto (feature prometida ausente) · **Esfuerzo:** bajo-medio (sobre 2.1) · **Decisión:** pendiente
- **Problema:** `WeeklyReportConfig` (`enabled=True` por default, `day`, `time`, `sections`,
  `stale_idea_days`) se carga y valida — hasta tira `ConfigError` con día inválido — pero nada lo
  ejecuta. El usuario puede creer que recibe un reporte semanal que nunca llega.
- **Propuesta:** implementar `weekly_report_job` en `jobs.py` reusando los reporters (secciones
  según `weekly_report.sections`, umbral `stale_idea_days`) y registrarlo en `bot.py` con
  `app.job_queue.run_daily(..., days=(dia,))` cuando `weekly_report.enabled`. **Formato natural:
  el brief inline de 2.1** + documento `.md` adjunto (o botón para generarlo). Respetar
  `_PENDING_FLOW_KEYS` para no interrumpir un flujo interactivo (mismo criterio que
  `reclassify_inbox`).
  - *Alternativa si se descarta:* eliminar `WeeklyReportConfig` para que la config no mienta.
- **Archivos:** `adso/handlers/jobs.py`, `adso/bot.py`, `adso/reporters.py`, CLAUDE.md (1.8).

---

## Bloque 3 — Resiliencia LLM / embeddings (fusiona el ítem "degraded mode" del plan 2026-05)

### 3.1 Timeout explícito en llamadas a Gemini

- **Estado:** plausible (`llm_client.py:548-557,615-619`) · **Impacto:** alto (robustez) · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Problema:** `generate_content` corre en `to_thread` sin timeout. Un cuelgue de red deja el
  thread bloqueado indefinidamente; en `reclassify_inbox`/`reindex` retiene `_vault_heavy_lock` y
  frena todos los jobs pesados.
- **Propuesta:** `types.HttpOptions(timeout=...)` (30-60s) al construir el cliente, o
  `asyncio.wait_for` alrededor del `to_thread`; ante timeout, contar como retry transitorio.

### 3.2 No reintentar errores permanentes en `classify()`

- **Estado:** plausible (`llm_client.py:391-423`) · **Impacto:** medio · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Problema:** el `except Exception` genérico reintenta 3× (con sleeps 1+2s) errores que nunca van
  a mejorar: 400 `INVALID_ARGUMENT`, 401/403 (key mala), `LLMResponseError` de schema.
- **Propuesta:** clasificar la excepción: permanentes → cortar directo a fallback/degraded;
  fallo de validación → como máximo 1 reintento con hint "return valid JSON"; transitorios (5xx,
  red, empty) → retries actuales.

### 3.3 Groq como fallback ante cualquier fallo terminal de Gemini (no solo cuota diaria)

- **Estado:** plausible (`llm_client.py:395-404`) · **Impacto:** alto · **Esfuerzo:** bajo-medio · **Decisión:** pendiente
- **Problema:** el fallback a Groq se dispara solo con 429 `PerDay`. Con 500/503, timeouts o
  respuestas vacías repetidas se agotan los retries y se cae a degraded aunque Groq esté sano.
- **Propuesta:** tras agotar los retries por errores transitorios, intentar Groq antes de degradar.
  Complemento: 1-2 retries con backoff corto dentro de `_try_groq_fallback` (hoy es un solo
  intento y Groq free tier también tiene RPM estricto).

### 3.4 `describe_image_with_vision`: retry, rate-limit y tope de páginas

- **Estado:** plausible (`llm_client.py:589-624`) · **Impacto:** alto (caso más caro y más frágil) · **Esfuerzo:** medio · **Decisión:** pendiente
- **Problema:** Vision no tiene retries ni parsing de 429 ni fallback. Para PDFs escaneados se envía
  un `Part` por página sin cap: 40 páginas = 40 imágenes en un request (tokens enormes,
  rechazo/timeout probable en free tier).
- **Propuesta:** misma lógica de retry/backoff que `_call_gemini`; cap de páginas por request
  (10-15) con troceo en varias llamadas si excede; timeout (3.1). Avisar en el preview si se
  procesó parcialmente.

### 3.5 Drenado por lotes en `reclassify_inbox`

- **Estado:** plausible (`jobs.py:179` — `return` tras 1 nota) · **Impacto:** medio-alto · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Problema:** 1 nota por ciclo × `degraded_retry_minutes=30` → 10 notas degradadas tardan 5 horas
  en drenarse con la API ya sana.
- **Propuesta:** lote de 3-5 por ciclo con `asyncio.sleep` entre notas (respetar RPM), re-chequeando
  `_PENDING_FLOW_KEYS` antes de cada una; o re-encolar un ciclo corto mientras queden notas y la
  API responda. Complementos del plan viejo que siguen vigentes: persistir `classify_attempt_count`
  / `first_failed_at` en el frontmatter degraded y notificar si una nota lleva >60 min sin
  reclasificar.

### 3.6 Manejo de cuota en el pipeline de embeddings

- **Estado:** plausible (`embeddings.py:159-177`) · **Impacto:** alto durante reindex · **Esfuerzo:** bajo-medio · **Decisión:** pendiente
- **Problema:** `_compute_embedding` reintenta 3× cualquier excepción con backoff fijo; no detecta
  429 `PerDay` ni respeta `retryDelay`. Si el reindex topa la cuota diaria, cada nota restante
  reintenta 3× condenada (cientos de llamadas inútiles).
- **Propuesta:** reutilizar `_parse_rate_limit_error` de `llm_client`; ante `PerDay` abortar el
  reindex completo (y notificar); ante RPM, dormir `retryDelay`.

### 3.7 Reindex por lotes / concurrente

- **Estado:** plausible (`embeddings.py:401-457`) · **Impacto:** alto en reindex inicial · **Esfuerzo:** medio · **Decisión:** pendiente
- **Problema:** el loop hace `await index_note()` uno por uno + `sleep(0.2)`; el
  `_embed_semaphore(4)` existente nunca se aprovecha y no se usa batch embedding (la API acepta
  varios `contents` por request).
- **Propuesta:** batch de 20-100 notas por request de embedding, o N tareas concurrentes con el
  semáforo. Reduce requests (clave para RPM/RPD) y tiempo total en órdenes de magnitud.

### 3.8 Metadata stale en ChromaDB ante cambios solo-de-frontmatter

- **Estado:** plausible (`embeddings.py:436-439`) · **Impacto:** alto para fiabilidad de `/buscar` · **Esfuerzo:** bajo-medio · **Decisión:** pendiente
- **Problema:** el skip del reindex usa `md5(body)`. Si cambia solo el frontmatter (status→done,
  cambio de proyecto, tags) el hash coincide y la metadata en ChromaDB queda vieja — y el filtrado
  por scope y las etiquetas mostradas dependen de ella.
- **Propuesta:** comparar también un hash de la metadata relevante; si difiere solo la metadata,
  `update_metadata` (barato, sin re-embed) en vez de skip.

### 3.9 Truncado / chunking del body antes de embeder

- **Estado:** plausible (`embeddings.py:144-167,453`) · **Impacto:** alto para papers (caso de uso central) · **Esfuerzo:** medio · **Decisión:** pendiente
- **Problema:** se pasa el body completo a `embed_content`; papers largos exceden el límite de
  entrada del modelo (error o truncado silencioso del lado servidor) y un único vector para una
  nota larga degrada el recall.
- **Propuesta:** mínimo, truncar defensivamente a un presupuesto de tokens. Mejor: chunkear notas
  largas (varios embeddings con sufijo en el `note_id`) e indexar por chunk.

### 3.10 `task_type` asimétrico en embeddings

- **Estado:** plausible (`embeddings.py:162-166,304`) · **Impacto:** medio-alto (calidad de `/buscar`, gratis) · **Esfuerzo:** bajo + reindex completo · **Decisión:** pendiente
- **Propuesta:** `EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")` al indexar y
  `"RETRIEVAL_QUERY"` al consultar (recomendación de Gemini). Requiere reindex para coherencia —
  conviene hacerlo junto con 3.11.

### 3.11 `output_dimensionality` reducido (default 3072 → 768)

- **Estado:** plausible (`embeddings.py:20,162-166`) · **Impacto:** medio-alto en RPi4 (RAM/latencia del índice HNSW) · **Esfuerzo:** bajo + reindex completo · **Decisión:** pendiente
- **Propuesta:** fijar `output_dimensionality=768` (el modelo soporta MRL) y normalizar el vector.
  ~4× menos memoria y disco por nota con pérdida mínima. Coordinar con 3.10 (un solo reindex).

### 3.12 `finish_reason=MAX_TOKENS` → no reintentar idéntico

- **Estado:** plausible (`llm_client.py:559-562,627-654`) · **Impacto:** medio para papers largos · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Problema:** si Gemini corta por límite de output, el JSON queda truncado, `json.loads` falla y
  se reintenta 3× el mismo prompt con el mismo resultado.
- **Propuesta:** inspeccionar `finish_reason`; ante `MAX_TOKENS` no reintentar idéntico — reducir
  el contenido o ir directo a Groq/degraded. Evaluar subir `max_output_tokens`.

### 3.13 Snippet de `/buscar` = primeros 200 chars, no el pasaje relevante

- **Estado:** plausible (`embeddings.py:348`, `knowledge_query.py:61`) · **Impacto:** medio (UX) · **Esfuerzo:** bajo-medio · **Decisión:** pendiente
- **Propuesta:** snippet alrededor del match léxico de la consulta en el body; como mínimo, saltear
  el callout `[!summary]` inicial.

### 3.14 Retrieval híbrido (vectorial + léxico)

- **Estado:** propuesta de mejora (no bug) · **Impacto:** medio (recall con nombres propios/DOIs) · **Esfuerzo:** medio · **Decisión:** pendiente
- **Propuesta:** cuando el vectorial devuelve poco o con baja confianza, complementar con búsqueda
  léxica sobre `vault_search` y fusionar con RRF simple. Sin llamadas extra a la API. Encaja con
  Fase 7.1+ (ver `docs/fase7-rag-design.md`).

### 3.15 Higiene de prompt de clasificación

- **Estado:** plausible · **Impacto:** medio (tokens y precisión) · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Propuestas:**
  - Capear `existing_tags` a top-N por frecuencia (~50-80) — ya vienen ordenados, falta el slice
    (`llm_client.py:197,215-216`). En vault maduro son cientos de tokens por request.
  - `media_type` se recibe en `classify()` pero no se usa en el prompt (`llm_client.py:321-330`):
    inyectarlo como hint (transcripción de audio, OCR, etc.) o eliminar el parámetro.
  - Evaluar Gemini context caching (`cachedContent`) para la porción estática del system prompt
    (~200 líneas); beneficio en free tier: latencia/TPM, no dinero.

---

## Bloque 4 — Refactor de `capture.py` (ítem 4 del plan 2026-05, ahora con contenido concreto)

`capture.py` está en 1.183 líneas. Además de mover helpers a `bot_utils.py` (plan original),
la revisión encontró qué unificar exactamente:

### 4.1 Unificar el bloque "embedding + links sugeridos" (4 copias)

- **Estado:** confirmado (una de las copias es código muerto, ver 1.7) · **Impacto:** medio · **Esfuerzo:** bajo-medio · **Decisión:** pendiente
- **Problema:** el patrón `compute_embedding(body)` → `query_similar` → armar `suggested_links`
  está en `capture.py:225-245`, `:283-302` (muerto), `:649-666` y `:1152-1166`, con variaciones
  inconsistentes: el flujo arXiv usa el abstract como query y **no guarda `_body_embedding`**, así
  que `_cb_confirm` siempre re-embebe (llamada de red redundante y links calculados sobre un texto
  distinto al indexado).
- **Propuesta:** helper único `_compute_suggested_links(embeddings, settings, body, *,
  query_text=None) -> tuple[list, vector]` usado en los tres sitios vivos; arXiv computa el
  embedding del body definitivo una vez y lo guarda en `_body_embedding` como el resto.

### 4.2 Unificar los dos parsers de corrección

- **Estado:** confirmado (divergencia real) · **Impacto:** medio (UX inconsistente) · **Esfuerzo:** medio · **Decisión:** pendiente
- **Problema:** `_apply_task_corrections` (`capture.py:460-506`, regex en cualquier posición) y la
  cadena `elif startswith` para notas (`:509-554`) interpretan los mismos prefijos distinto:
  `tipo X` funciona para notas pero **no** para tareas (cae como título si es corto); el mapeo de
  prioridad está duplicado; la rama tarea acepta fecha embebida en texto libre, la de notas no.
- **Propuesta:** un solo parser que reciba el frontmatter y aplique cada campo detectado
  independiente del `type`, validando contra `VALID_TYPES`.

### 4.3 Salida del modo corrección

- **Estado:** plausible (`capture.py:898-913`) · **Impacto:** medio (trampa de UX) · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Problema:** `[Corregir]` borra el teclado (edit sin `reply_markup`) y no hay forma de volver:
  texto largo → "Corrección no reconocida" en loop; texto corto → **pisa el título
  silenciosamente**.
- **Propuesta:** teclado con `[Volver]` durante el modo corrección que limpie `awaiting_correction`
  y re-renderice el preview sin cambios.

### 4.4 Estado `pending_raw_content` + `pending_capture_ctx` como unidad

- **Estado:** plausible (`capture.py:978-983`, `callbacks.py:177-180`, `input.py:174-281`) · **Impacto:** medio (leak de estado entre capturas) · **Esfuerzo:** bajo-medio · **Decisión:** pendiente
- **Problema:** se setean juntos pero se limpian por separado según el camino de salida; un
  `pending_capture_ctx` residual puede contaminar la próxima captura (p. ej. `resource_file` de un
  audio viejo aplicado a texto nuevo). `_has_pending_keyboard` no lo considera.
- **Propuesta:** fusionarlos en un solo dict, limpiarlo en todos los caminos de salida (incluido
  `/reset`, verificar `handle_reset`) e incluirlo en `_has_pending_keyboard`.

### 4.5 Limpiezas menores del flujo

- **Estado:** mixto · **Impacto:** bajo-medio · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Propuestas:**
  - Centralizar el armado del frontmatter base en un helper único (hoy `extra_fm` se inyecta dos
    veces en modo degradado — `capture.py:115-118,128-129`; inocuo pero frágil).
  - Helper compartido `count_unclassified_inbox()` para el recuento post-confirm
    (`capture.py:862-883` relee nota por nota secuencialmente con la regla de filtrado duplicada
    de `handle_clasificar`).
  - Borrar `block_msg_ids` también cuando el flujo se resuelve por texto (hoy solo se limpian al
    llegar un callback — `input.py:152-167` vs `callbacks.py:103-107`).
  - Decidir la asimetría audio vs imagen: el `.ogg` se borra tras transcribir
    (`capture.py:974-995`) mientras que imágenes/PDF escaneados se adjuntan como recurso.
    Adjuntar también el audio o documentar la decisión.

### 4.6 Errores crudos al chat

- **Estado:** plausible (`callbacks.py:117,371,446`; `input.py:358,457,476,624`) · **Impacto:** medio (fuga de detalles internos) · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Problema:** varios `edit_message_text(f"Error: {e}")` vuelcan la excepción cruda (paths, detalles
  de API) al usuario.
- **Propuesta:** mensaje genérico + `logger.exception` con el detalle; volcado de `{e}` solo bajo un
  flag de debug.

### 4.7 Fugas de archivos temporales

- **Estado:** plausible (`input.py:512-529` y transversal) · **Impacto:** medio (`/tmp` suele ser tmpfs = RAM en la Pi) · **Esfuerzo:** bajo-medio · **Decisión:** pendiente
- **Problema:** `handle_photo` no tiene el `try/finally` + flag `transferred` que sí tiene
  `handle_document`; y todos los temp paths viven en `user_data` (in-memory) — si el usuario
  abandona el flujo y el bot se reinicia, quedan huérfanos para siempre (no hay barrido al
  arranque).
- **Propuesta:** replicar el patrón `try/finally` en `handle_photo`; usar un subdirectorio propio
  para temporales y un barrido al startup que borre archivos con mtime > N horas.

---

## Bloque 5 — Google Tasks bidireccional (o degradar la doc)

**Decisión previa a todo el bloque:** ¿se quiere el sync bidireccional que CLAUDE.md describe, o
alcanza con el push unidireccional actual? Si alcanza, implementar solo 5.3-5.5 (robustez) y
corregir la doc (1.8).

### 5.1 Persistir `gtask_id` en el frontmatter

- **Estado:** confirmado (el `task_id` devuelto por `create_task` se descarta — `capture.py:735,817`) · **Impacto:** alto (prerequisito de todo sync) · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Propuesta:** guardar `gtask_id` (y opcionalmente el list id) en el frontmatter de la nota task
  al confirmar. Sin esto no hay update/complete/delete posible ni idempotencia ante retries.

### 5.2 Job de reconciliación

- **Estado:** diseño (descrito en CLAUDE.md, nunca implementado) · **Impacto:** medio-alto · **Esfuerzo:** alto (el mayor del documento) · **Decisión:** pendiente
- **Propuesta:** cron cada `sync.interval_minutes` que diffee tasks del vault ↔ lista ADSO por
  `gtask_id`: `status: done` en vault → completar en Google; completado/borrado en Google → aplicar
  al vault (borrado → mover a Inbox con `pending-classification`, como dice el diseño); reintentar
  creates perdidos (hoy un push fallido se pierde — la nota queda sin task y nada lo reintenta).

### 5.3 Robustez del cliente

- **Estado:** plausible (`tasks_client.py:33,71-127`) · **Impacto:** medio · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Propuestas:**
  - `RefreshError`/401 en runtime: hoy el `_service` cacheado nunca se reconstruye y el usuario
    recibe el mensaje genérico en vez del de re-auth. Capturar, resetear `_service` y activar el
    flag de auth-failed.
  - 404 al insertar (lista ADSO borrada en Google): limpiar `_list_id` cacheado y recrear la lista.
  - Escribir `token_tasks.json` atómicamente (reusar el patrón de `_atomic_write_sync`) — un corte
    de luz a mitad de escritura corrompe el token y fuerza re-auth manual.

### 5.4 `due` a medianoche UTC puede correr el día en UTC-3

- **Estado:** plausible (`tasks_client.py:161`) · **Impacto:** bajo · **Esfuerzo:** bajo · **Decisión:** pendiente
- **Propuesta:** anclar el `due` date-only a mediodía local, o documentar el comportamiento
  conocido de Google Tasks con fechas sin hora.

### 5.5 Condición muerta en `build_task_notes`

- **Estado:** plausible (`tasks_client.py:227`) · **Impacto:** bajo (claridad) · **Esfuerzo:** trivial · **Decisión:** pendiente
- **Problema:** `isinstance(dt, datetime) and not isinstance(dt, date)` — `datetime` es subclase de
  `date`, el primer conjunto es siempre falso; funciona de casualidad por el `hasattr` posterior.
- **Propuesta:** `isinstance(dt, datetime) and (dt.hour or dt.minute)`.

---

## Backlog menor (sin bloque asignado, oportunistas)

Todos **plausibles** salvo indicación; re-verificar al encarar. Decisión: pendiente en todos.

**Watcher / vault:**
- Excluir `.stversions`, `.git`, `.stfolder` tanto en el filtro del watcher como en
  `_DEFAULT_EXCLUDE` de `vault_search.py:32` — hoy las copias versionadas de Syncthing pueden
  re-embederse y aparecer en `/buscar`.
- Acotar el fan-out de re-embeds del watcher (`vault_watcher.py:184-196`): una ráfaga de Syncthing
  con N notas lanza N llamadas concurrentes a Gemini sin límite → semáforo o consumidor único.
- Fallo de `observer.start()` se traga (`vault_watcher.py:156-164`) y `/status` sigue mostrando
  "activo": propagar el estado degradado y notificar.
- `remove_broken_wikilinks` escanea el vault completo por **cada** delete externo
  (`vault_writer.py:652-701`) y también corre para deletes propios del bot (el delete de
  `reclassify_inbox` no se registra en `bot_written_paths`): registrar deletes propios, y/o
  batchear/debouncear la limpieza.
- La limpieza de wikilinks saltea `_index.md` (`vault_writer.py:675`) — justo los archivos más
  cargados de links: permitir limpiarlos.
- `_scan_vault` no poda directorios excluidos durante el recorrido (`vault_search.py:64-72`):
  `rglob` desciende a `.git`/`.obsidian`/Archive y filtra después. Cambiar a `os.walk` con poda
  in-place (medio-alto en SD lenta).

**Git backup:**
- `git add -A` (`vault_writer.py:922`) commitea conflictos Syncthing (`.sync-conflict-*`), churn de
  `.obsidian/workspace.json` y `.stversions`: agregar `.gitignore` al repo del vault o restringir
  el add a `*.md` de los directorios de contenido.
- `origin.push()` sin timeout (`vault_writer.py:930`): un push colgado en red inestable consume un
  thread del pool compartido con todo el I/O del vault → `kill_after_timeout=` de GitPython.
- Notificar fallos de push solo en transición (fallo→ok y ok→fallo), no en cada ciclo de debounce
  offline (hoy puede spamear).
- Exponer estado del backup en `/status`: `last_pushed_at`, `last_error`, commits locales sin
  pushear (complementa la idea futura "reintento de push en heartbeat" del CLAUDE.md).

**Frontmatter / jobs / arranque:**
- `set_property("type", ...)` no re-valida `status` contra el tipo nuevo (`vault_writer.py:449-459`).
- `set_property(key, None)` borra el campo silenciosamente: hacer explícito (`delete_property`).
- `reclassify_inbox` fuerza `source="telegram"` aunque la nota fuera `source: system`
  (`jobs.py:132`): preservar el original.
- `reindex_job` no notifica fallos (solo `logger.error`) y retiene `_vault_heavy_lock` durante todo
  el reindex: notificar si `errors` es alto o abortó por cuota; soltar el lock por lotes.
- `run_daily` del reindex con hora naive (`bot.py:213-217`): PTB la interpreta en su propia zona;
  adjuntar tzinfo explícito de settings.
- `_post_shutdown` no drena las tareas de `spawn_tracked` en vuelo (pushes a Tasks, indexados):
  awaitear el set como ya hace `VaultWatcher.stop()`.
- `_post_init` ante excepción deja el bot a medias con solo un log: enviar mensaje de error de
  arranque al usuario autorizado.
- `remove_note` hace `get` antes de `delete` en ChromaDB (`embeddings.py:236-247`): borrar directo.
- `concurrent_updates(True)` en PTB: **no habilitar** hasta blindar el estado compartido (4.4) —
  hoy los updates se procesan de a uno, lo cual es tolerable para un bot single-user.

**Del plan 2026-05, re-priorizado:**
- Logging estructurado + métricas de cuota Gemini (parsear `usageMetadata`, tracking RPD,
  `/status` enriquecido): **baja prioridad** — la motivación original era "~20 RPD observado" y el
  free tier real es ~1.000 RPD. Sigue valiendo por observabilidad, no por urgencia.
