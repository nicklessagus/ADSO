# Log de decisiones de implementación

Post-mortems de fixes puntuales: el *porqué* detrás de una línea de código que
parece arbitraria. Se movieron acá desde `CLAUDE.md` para que ese archivo —que se
carga entero en contexto en cada sesión— quede con lo que restringe trabajo
futuro (taxonomía, invariantes, políticas) y no con el historial de cada bug.

Nada se perdió: el texto de cada entrada es verbatim el que estaba en CLAUDE.md.
La mayoría también vive como comentario en el propio código, y el detalle
cronológico está en `CHANGELOG.md`.

**Cuándo leer esto:** antes de tocar `vault_writer`, `vault_watcher`, `GitBackup`,
el flujo de confirmación de `capture.py`/`callbacks.py` o el manejo de errores de
PTB — si algo parece innecesariamente defensivo, probablemente esté explicado acá.

---

## Limpieza del estado de gestión (`pop_manage_state` en `manage.py`)

confirmar o cancelar una operación de gestión popea `pending_operation` **y** `manage_missing_fields` juntas. Ambas están en `_PENDING_FLOW_KEYS`; dejar la segunda colgada hacía que `reclassify_inbox` pospusiera cada pasada indefinidamente (el inbox nunca se drenaba) hasta un `/reset`. El helper cubre también las salidas tempranas de `_cb_manage_confirm` (nombre o proyecto inválido, elemento ya existente).

## Construcción del `frontmatter.Post` (`_build_post` / `load_post` en `vault_writer.py`)

nunca se usa `frontmatter.Post(body, **fm)`. La firma real es `Post(content, handler=None, **metadata)`, así que una clave `handler` en el frontmatter se interpretaba como handler de serialización y `frontmatter.dumps()` escribía ese string como **contenido total del archivo** (body y frontmatter perdidos en silencio), y una clave `content` lanzaba `TypeError`. Los cuatro sitios de escritura (`create_note`, `append_to_note`, `set_property`, `update_wikilinks`) usan `_build_post`, que asigna `post.metadata`. Del lado de lectura, `frontmatter.loads()` tiene el mismo choque de kwargs: `load_post()` lo envuelve y cae a `frontmatter.parse()` si lanza `TypeError`, para que una nota editada externamente con esas claves no rompa `read_note` ni los scans de `vault_cache`.

## Limpieza de wikilinks acotada al bloque (`_strip_broken_links_in_ver_tambien`)

`remove_broken_wikilinks` solo borra items `- [[stem]]` que están **dentro** del bloque `## Ver también` (recorrido por líneas con estado de bloque), nunca en prosa u otras listas del usuario. Antes un regex global podía borrar líneas del usuario que contuvieran el wikilink.

## Git backup fuera del event loop (`GitBackup._sync_backup`)

`Repo`/`add`/`is_dirty`/`commit`/`push` corren en `asyncio.to_thread` (antes solo el `push`). En la RPi4 con SD lenta esto evita congelar el bot durante el backup. `_do_backup` limpia `_timer` bajo lock y las notificaciones a Telegram quedan en el event loop según el status devuelto.

## Flush del backup al shutdown (`GitBackup.flush`)

`_post_shutdown` (en `bot.py`) awaitea `git_backup.flush()` tras detener el watcher. Sin esto, una nota escrita dentro de la ventana de debounce (`backup.debounce_seconds`, default 30s) justo antes de un `docker stop` quedaba sin commit/push hasta la *próxima* escritura — potencial pérdida de datos si el contenedor no volvía a arrancar. `flush()` cancela el `_timer` bajo lock, **espera el backup en vuelo** (`_await_running`) y recién después evalúa `_pending_titles` — si el debounce ya había disparado, la cola está drenada y sin esa espera el shutdown continuaba con el push a medio hacer. `_do_backup` se serializa consigo mismo por la misma referencia (`_running`): dos backups nunca corren git en paralelo (colisión de `index.lock`). `notify()` no espera el backup en vuelo — bloquearía la confirmación del usuario durante el push; la serialización ya la garantiza `_do_backup`. Ante un fallo de `add`/`commit` (disco lleno, `index.lock` de un git manual, repo corrupto) los títulos drenados se **re-encolan** al frente de `_pending_titles` y se notifica por Telegram, igual que en `push_failed`: antes el error solo se logueaba y el vault podía quedar sin backup indefinidamente y en silencio.

## Watcher `on_moved` + drenado de `bot_written_paths` (`vault_watcher.py`, `bot_utils.mark_bot_written`)

inotify reporta renames como `FileMovedEvent`. El handler ahora implementa `on_moved`: emite un delete para el origen y un change (o conflicto) para el destino. Cubre (1) Syncthing aplicando cambios remotos via temp+rename → re-embed inmediato en vez de esperar el reindex nocturno; (2) editores con guardado atómico. La propia escritura atómica del bot (temp `.adso-tmp-*.tmp` → nota) también dispara un move: el origen es hidden y no-`.md` (se saltea) y el destino cae en `bot_written_paths`, que `_reindex_external_note` consume y descarta — sin doble embed. Antes `on_moved` no existía, así que la escritura del bot (que es un `os.replace` = move) nunca drenaba el set → leak de memoria y guard anti-doble-embed inefectivo. Los tres sitios que registran escrituras propias (`_cb_confirm`, `reclassify_inbox`, y el flujo degradado) usan `mark_bot_written`, que además acota el set a `_BOT_WRITTEN_CAP` (512) como red de seguridad ante eventos perdidos.

## Embedding inline al confirmar (`_cb_confirm`)

la nota se indexa en ChromaDB inline con `spawn_tracked(_index_note_safe(...))`, igual que `jobs.reclassify_inbox`. El path se registra en `bot_written_paths` para que el `VaultWatcher` saltee el evento inotify de esa escritura (sin doble embed). Antes se delegaba al watcher, que justamente saltea esos paths → la nota quedaba sin embedding hasta el reindex nocturno.

## Frontmatter no-string en notas editadas a mano

YAML parsea `title: 2024` como `int` y `project:` vacío como `None` (el default de `.get()` no aplica). Todos los filtros de `vault_search.py` (`type`/`status`/`project`/`area`/`title`), `_note_ref_from_data`, `_extract_tags_from_note` (acepta `tags` como string), las keys de agrupamiento de `reporters.py` (`sorted()` sobre keys mixtas str/int lanzaba `TypeError`) y `_priority_key` coaccionan con `str(... or "")`. Antes **una sola nota editada a mano tiraba abajo la búsqueda o el reporte entero**. En la misma línea, `_to_naive()` (reporters) normaliza los datetimes antes de compararlos: `_parse_fm_date` devuelve aware para fechas con offset (plugins de Obsidian) y naive para las que escribe ADSO. Y `_parse_date_value` (`vault_writer.py`) envuelve `fromisoformat` en `try/except`: una fecha sintácticamente válida pero imposible (`2026-02-30`) ya no revienta la escritura *después* de la confirmación — se deja como string.

## Frontmatter YAML corrupto (`vault_cache.parse_cached`)

una nota con YAML inválido (edición externa a mano) se omite de los scans pero se loguea a `warning` con el path (antes: `debug` silencioso). Los errores de I/O (`OSError`) siguen a `debug` por ser transitorios.

## Render de PDFs escaneados fuera del event loop (`_render_pdf_pages` en `callbacks.py`)

función síncrona que se llama siempre via `asyncio.to_thread` (rasterizar a 200 DPI tarda segundos en la RPi4 y antes congelaba el bot entero). Devuelve los PNG en memoria (`pix.tobytes`), sin archivos temporales. El DPI efectivo se reduce si la página declara dimensiones enormes (cap `_MAX_RENDER_PIXELS` = 16MP por página — protege contra OOM por PDFs maliciosos/malformados). `_pdf_page_count` es el helper threadizado para contar páginas.

## Preview completo y copiable del texto extraído (`_build_extract_preview` en `callbacks.py`)

el resultado de OCR y de Gemini Vision se muestra íntegro dentro de un bloque `<code>` (copiable de un toque en Telegram), no truncado a 500 chars como antes. El helper ajusta el cuerpo para no pasar el límite de ~4096 chars de un mensaje (`_PREVIEW_LIMIT = 3900`, con margen para el escape HTML); solo si el texto excede lo que entra en un mensaje se trunca el **preview** con un aviso, pero el texto íntegro sigue en `pending_transcript["text"]` y es lo que se guarda al confirmar. Usado por `_cb_ocr` y `_cb_vision`, y también por el modo corrección (`CB_TRANSCRIPT_CORRECT` para OCR/Vision/audio y `CB_EXTRACTION_CORRECT` para texto de PDF/documento) via el parámetro `footer`, que agrega la instrucción "Texto corregido…" después del bloque copiable contándola en el presupuesto — así al corregir se ve/copia el texto actual entero, no un recorte de 500 chars.

## Caption de imagen reutilizado como descripción (`user_context`)

cuando el usuario manda una imagen con caption, ese texto viaja como `user_context` en `pending_fallback_pdf` y ahora se propaga por todo el flujo — `_cb_ocr`/`_cb_vision` lo copian a `pending_transcript`, y `_cb_transcript_ok` lo pasa a `_classify_and_preview` (influye en la clasificación). Además, si el usuario elige `[Describir]` y la imagen ya trae caption, el bot **no vuelve a pedir la descripción**: usa el caption directo como body (`preserve_body=True`) y clasifica. Sin caption, mantiene el prompt "Describir el contenido…".

## Límite de tamaño post-descarga (`_exceeds_size_after_download` en `input.py`)

si Telegram no informa `file_size` (None), el pre-check se saltea — el límite se aplica sobre el archivo ya descargado (se borra el temporal si excede). Transcripción con `beam_size=1` (greedy): en CPU ARM int8 el beam de 5 era 3-5x más lento con ganancia marginal para notas de voz.

## Error handler global de PTB (`_global_error_handler` en `bot.py`)

registrado con `add_error_handler`. Los `BadRequest` benignos (`message is not modified`, `query is too old` — típicos tras timeouts de red a mitad de flujo) se ignoran con log a `info`. Los errores de red (`NetworkError`/`TimedOut`) solo se loguean — notificar por la misma red caída fallaría. El resto se loguea y notifica al usuario con mensaje genérico + `/reset`. Complementos en `handle_callback`: `query.answer()` vencido no aborta el procesamiento del tap, y `_cb_confirm` trata "message is not modified" como éxito silencioso (la confirmación ya se había aplicado).

## El mensaje de una excepción decidía el control de flujo del retry loop (`classify`, lote 3 / #43)

`classify` clasificaba el error con `"429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)`. El problema no es la fragilidad teórica del substring: es que `_validate_capture_payload` **embebe valores controlados por el modelo en el mensaje de la excepción** (`Invalid status '<valor>'…`), y el modelo copia texto del input del usuario a esos campos. Un usuario que manda la captura de pantalla de un error de cuota de Gemini produce, en cadena: un `status` con `"429 … GenerateRequestsPerDayPerProject"` adentro → `LLMResponseError` con ese texto en el mensaje → el loop lo lee como cuota diaria agotada → abandona Gemini tras **un** intento y salta a Groq. Contenido del usuario eligiendo la rama de reintentos.

Colateral del mismo mecanismo: un JSON truncado cuyo `JSONDecodeError` diga `column 429` era indistinguible de un rate limit.

El fix clasifica por tipo (`isinstance(e, APIError) and e.code == 429`) y **no deja fallback por substring** — una excepción no tipada va por el camino genérico aunque su texto mencione la cuota. Parsear el payload *después* de confirmar el tipo sigue siendo legítimo (es la API hablando, no el usuario). Reproductores en `tests/unit/test_lote3_llm_config.py::TestErrorClassificationByType`.

Efecto lateral en un test de la auditoría 2026-08-26: `test_groq_sin_titulo_cae_al_contenido` simulaba el error de cuota con una `Exception` pelada, exactamente el diseño que se reemplaza. Se reconstruyó como `APIError` tipado — andamiaje, sin tocar aserciones.

## El backoff de RPM se leía fuera del guard de reintento (`classify`, regresión de 1.7.0)

Achicar `RETRY_DELAYS` de `[1, 2, 4]` a `[1, 2]` (#43 D) dejó una lectura sin proteger: en la rama de rate limit por minuto, `wait = ... else RETRY_DELAYS[attempt - 1]` se evaluaba **antes** del `if attempt < MAX_RETRIES`, así que el tercer y último intento indexaba fuera de rango. La otra lectura, la del camino genérico, sí estaba dentro del guard — por eso pasó desapercibida.

Lo que lo vuelve grave no es el `IndexError` sino dónde ocurre: **dentro del `except`**, así que escapa del `for`, escapa de `classify()` y `make_degraded_result` nunca corre. El input del usuario no cae al Inbox: se pierde. Es exactamente la clase de fallo que el modo degradado existe para evitar.

Regla que deja: **el backoff solo se lee cuando efectivamente va a haber reintento.** Hay un delay por reintento, no uno por intento; cualquier lectura fuera del guard está mal por construcción. Reproductor en `tests/unit/test_lote3_llm_config.py::TestRpmDelayOnTheLastAttempt`, que asserta que la corrida degrada y que el texto original sobrevive en el body.

Encontrada auditando `docs/architecture.md` contra el código, no por un test — la suite estaba verde porque ningún test cubría un 429 por minuto sin `retryDelay` que agotara los tres intentos.

## El timeout de `classify` quedó por debajo del piso que impone la API (2026-08-27 → 28)

`CLASSIFY_TIMEOUT_MS = 8_000` se deployó el 2026-08-27 y **toda** captura cayó a modo degradado hasta el 2026-08-28. Gemini rechaza cualquier deadline menor a 10 s con `400 INVALID_ARGUMENT` (`Manually set deadline 8s is too short. Minimum allowed deadline is 10s.`) **sin llegar a llamar al modelo**: las tres llamadas fallaban en ~300 ms cada una, se agotaba el presupuesto de reintentos en ~6 s y el usuario recibía "No se pudo clasificar bien — guardado en Inbox como borrador" en cada nota.

Por qué la suite estaba verde: el piso vive en el servidor. `tests/unit/test_classify_timeout.py` verificaba `5_000 <= timeout <= 30_000` sobre un `MagicMock` — 8000 lo cumple, y ningún mock puede saber que la API lo rechaza. El único punto del repo que lo habría visto es `scripts/llm_regression.py`, que llama a `_call_gemini` contra la API real; no se corrió porque el harness estaba documentado como el paso previo a tocar `GEMINI_MODEL` y nada más.

Dos cosas que deja:

1. **El harness de regresión cubre la request completa, no solo el modelo.** Correrlo antes de tocar cualquier parámetro del `GenerateContentConfig`/`HttpOptions` — timeouts, schema, mime type — no solo `GEMINI_MODEL`.
2. **El guard mockeable que sí sirve es un piso duro sobre la constante**, anclado al mensaje de error que reportó el incidente (`test_the_timeout_clears_the_floor_the_api_enforces`). No sustituye al harness: fija el número conocido, no descubre el próximo límite del servidor.

Valor nuevo: `12_000`. No `10_000` clavado, para no depender de cómo redondea el borde el SDK; el costo es 2 s más de espera en un stall.
