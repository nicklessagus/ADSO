# Changelog

All notable changes to ADSO are documented here.
Format: [Conventional Commits](https://www.conventionalcommits.org/). Dates are UTC.

---

## [Unreleased]

---

## [1.5.0] — 2026-08-26

Auditoría funcional completa: **40 bugs encontrados, 39 arreglados**, cada uno con un test que primero falló reproduciéndolo. El que queda abierto es #53 (la dedup no cubre archivos subidos), que necesita una decisión de diseño y va como issue en vez de un fix apurado. Siete pasadas de revisión (captura, medios, LLM/config, vault, embeddings, comandos/reportes/jobs, y docs), más una auditoría del **vault real de producción** que destapó cinco defectos que leyendo código no se veían.

La regla que ordenó todo el trabajo: ningún bug se reporta sin un test que lo reproduzca, verificado con `--runxfail` para que un mock mal armado no se confunda con un defecto real. Salió **un falso positivo de 40 candidatos**, y lo atrapó la propia suite (ver más abajo).

Detalle completo en `docs/audit-2026-08-26.md`. Suite: 770 → 897 tests. Cobertura: 76% → 84%.

### Fixed

**Pérdida de datos** — la regla de oro del proyecto, rota en cuatro lugares:
- **El error al guardar borraba el teclado** (#6): el `except` de `_cb_confirm` editaba el mensaje sin `reply_markup`, así que el reintento que el fix A2 prometía era **inalcanzable** — no quedaba ningún `[Confirmar]` que apretar. El estado seguía en `user_data`, pero la única salida era `/reset`, que descarta el texto de audio/OCR. A2 creía haber cerrado exactamente este agujero
- **Los confirmadores descartaban el estado antes de usarlo** (#8): `_cb_transcript_ok`/`_cb_extraction_ok` hacían `pop` al entrar y recién después editaban el mensaje y clasificaban; un `TimedOut` en el medio evaporaba el texto de OCR/extracción y dejaba el temporal huérfano. Ahora siguen el patrón "crear antes de descartar" de `_cb_confirm`
- **OCR → Vision con error dejaba el bot muerto** (#9): el `except` limpiaba `pending_fallback_pdf`, que en ese camino no existe, y borraba el temporal mientras `pending_transcript` seguía vivo → todo input rechazado sin un solo botón en pantalla. Ahora conserva el texto de OCR (que ya está pago) y repone su teclado
- **`mode=manage` descartaba contenido ya confirmado** (#10): los cuatro flujos donde el usuario ya apretó guardar no pasaban `force_capture=True`

**El bot se quedaba trabado:**
- **Editar un mensaje crasheaba los cuatro handlers** (#16): los `MessageHandler` matcheaban también `edited_message`, donde `update.message` es `None`. Corregir un typo en el propio mensaje —gesto rutinario de Telegram— tiraba `AttributeError`
- **Estado seteado antes del reply** (#17): en cinco sitios, si el envío fallaba el estado quedaba colgado y `_has_pending_keyboard` rechazaba todo sin que hubiera botones. E9 ya lo había arreglado para audio; faltaban los otros cinco
- **Doble tap destruía el preview vigente** (#12): la rama "no hay estado" editaba el mensaje que para entonces ya mostraba el preview con su teclado
- **Destino borrado dejaba la captura sin botones** (#15) y el guard G14 estaba **inerte** porque ningún render guardaba el `msg_id` que compara (#7)

**Corrupción del vault:**
- **Mover una nota borraba wikilinks válidos** (#3): los wikilinks resuelven por *stem*, así que mover no rompe nada — pero `on_moved` emite un delete del origen y la limpieza corría sin verificar si el stem seguía vivo. Se perdían links curados, en silencio
- **La limpieza destripaba bloques de código** (#5): un `## Ver también` dentro de un fence se trataba como bloque real
- **`create_note` no validaba nada** (#54) y en `set_property` un `type` inválido **desactivaba en silencio** la validación de `status` (el set vacío es falsy). Ahora `create_note` coacciona en vez de rechazar: el usuario ya confirmó y su texto no existe en ningún otro lado
- **`status` inválido para el tipo** (#11) al corregir el tipo o reubicar: una task terminaba en `raw`

**Retrieval:**
- **Una nota con `title: 2024` tumbaba `/buscar` entero** (#25): YAML lo parsea como int y `_esc` hace `.replace()`. Misma clase C4 que ya se había arreglado en `vault_search`/`reporters`, pero no en el camino semántico
- **El watcher indexaba `05-Archive` y `_index.md`** (#26) y el reindex nocturno los borraba como huérfanos: ciclo diario de embed + delete, y resultados de búsqueda que cambiaban según la hora del día
- **`import chromadb` congelaba el event loop 4,4 s** (#29, medido en la RPi4) en el primer mensaje tras cada arranque. Ahora hay warm-up en `_post_init`, donde el freeze no molesta a nadie
- Nota vaciada que conservaba su embedding viejo para siempre (#27), carrera del sweep de huérfanos (#28), botón de informe de una consulta vieja (#30) y `InaccessibleMessage.chat_id` en mensajes de más de 48 h (#31)

**Silenciosos** — los que nadie iba a reportar:
- **Notas atascadas en el Inbox quemando quota para siempre** (#23): una nota degradada cuyo contenido parece pregunta hacía que el LLM devolviera `mode=query`, y el cron la salteaba en **cada** pasada — ~48 llamadas a Gemini por día, por nota, sin síntoma visible
- **La detección de "reporte vacío" era código muerto** (#33): el umbral era 400 bytes y el header solo pesa 655, así que la rama nunca ejecutaba y el usuario recibía un `.md` lleno de "_Sin referencias activas._"
- **La notificación del cron rompía la garantía "una por ciclo"** (#35): el `return` estaba después del `send_message`, así que un fallo de notificación encadenaba `classify()` contra un free tier de 15 RPM
- Un `type` inválido degradaba capturas de texto/audio **cuyo tipo ya había elegido el usuario con los botones** (#18), y un `status`/`priority` vacío tiraba toda la respuesta a degradado (#19)

**Encontrados solo auditando el vault real** (90 notas, cinco meses de datos):
- **Un temporal `.adso-tmp-*` fosilizado como wikilink** (#59) en una nota de `00-Inbox`: se indexó antes del `os.replace` y `_suggest_links` lo propagó. La fuga ya estaba tapada; ahora hay un guard para que una futura no vuelva a escribirse en una nota
- **El adjunto se escribía *dentro* de `## Ver también`** (#52) — 6 de las 8 notas con `source_file` lo tienen así
- **Los 7 `_index.md` colapsaban en una sola entrada** en `get_note_index` (#55): el dict keyeaba por stem y ganaba el último de `rglob`
- **`description: ""` se aceptaba al crear proyecto/área** (#56) porque se chequeaba la presencia de la clave, no su contenido. No es cosmético: 3 de 7 índices del vault están vacíos, y ese campo es el contexto que el LLM usa para elegir destino — explica parte de las misclasificaciones
- **El mismo PDF creó dos notas** (#53): la dedup mira `source_url` y `doi`, y un archivo subido no tiene ninguno — aunque `save_resource` ya calculó su SHA-256 y detectó que era el mismo. **Queda abierto**: falta decidir sobre qué clave deduplicar y cómo se combina con la búsqueda existente

### Added

- **`docs/audit-2026-08-26.md`** — índice completo de la auditoría: método, los 39 bugs con su issue y su test, lo que encontró el vault y lo que se verificó sano
- **~100 tests** en `tests/unit/test_audit_2026_08_*.py` (siete archivos). Todos empezaron reproduciendo su bug y hoy son guards de regresión
- **Convención de reproductores con `xfail(strict=True)`** en `CLAUDE.md`: un bug que no se arregla en el momento se documenta con un test que especifica el comportamiento correcto y lleva la marca, de modo que el día que se arregle el test pase a XPASS y `strict` obligue a sacarla en el mismo commit. Con dos reglas que lo hacen funcionar: verificar con `--runxfail` que falla por el mecanismo real, y acompañarlo de contra-casos, porque casi todos estos fixes son guards de una línea fáciles de aplicar de más
- **Regla de idioma** en `CLAUDE.md`: desde 2026-08-26 todo lo **nuevo** (símbolos, docstrings, docs nuevas, issues) se escribe en inglés. Lo existente no se traduce hacia atrás, y un archivo en español se corrige en español — un archivo bilingüe es peor que uno consistente en el idioma equivocado

### Changed

- **48 puntos de drift de documentación corregidos** y **14 funcionalidades documentadas por primera vez**. `architecture.md` concentraba lo peor: describía un flujo de extracción web genérica que **no existe**, daba el sync bidireccional de Google Tasks y el modo edición como implementados, y tenía teclados que no coincidían con `keyboards.py`. También se corrigieron contradicciones internas de `security.md` (el snippet del schema omitía las `properties` de `params`, y copiarlo reintroducía un bug crítico), `frontmatter-schema.md` (identificaba papers por un tag que el sanitizador filtra) y `obsidian-vault-structure.md` (presentaba como soportadas operaciones de gestión que responden "todavía no está disponible")
- `docs/testing.md`: cifras remedidas (623 → 897 tests, 76% → 84%) y secciones nuevas sobre el harness de regresión de modelo y la convención de reproductores

### Known

- **#53 sigue abierto** — es el único bug confirmado de la auditoría que no se arregló en esta tanda (ver arriba)
- **#62 — `tipo` sobre una tarea** (encontrado en la pasada de verificación post-fixes, ya arreglado): `_apply_task_corrections` no tenía rama de `tipo`, así que `"tipo nota"` sobre una tarea caía al fallback de título y **la tarea pasaba a llamarse "tipo nota"**, sin cambiar de tipo y sin aviso. Era la única vía para sacar una nota de `task`

- **#61 — `find_tasks`**: se reportó como bug de duplicación, se escribió el fix, y **rompió un test existente** cuyo fixture y comentario documentan que la duplicación es deliberada (los checkboxes de una tarea son sus subtareas). Se revirtió y quedó como pregunta de diseño, con tests de caracterización que fijan el comportamiento actual. La inconsistencia real es que las dos fuentes de `find_tasks` filtran distinto
- Quedan abiertas **19 mejoras** (#36–#51, #57, #58, #60) con propuesta de implementación y tests propuestos, incluida la limpieza de datos del vault y las decisiones de taxonomía que la auditoría dejó a la vista

---

## [1.4.0] — 2026-08-13

Cierre de la auditoría 2026-07-31: bloques **E, F y G completos** (36 ítems) más el bloque **I** nuevo, salido de una pasada de código muerto. Con esto la auditoría queda en **0 pendientes**.

Dos cambios estructurales acompañan a los fixes: la cobertura pasa a medir el código real (`adso/handlers/*` estaba excluido, o sea el 40% del proyecto) y el deploy deja de depender de una copia del compose mantenida a mano.

Verificado en producción el mismo día: G10, G12, F3/F4, E1 y el dedup de recursos por hash. Quedan sin probar contra Telegram real E4, F5, F2 y el flujo de audio.

### Added
- **`document_extractor.py` pasa de 32% a 97% de cobertura** (I4 de la auditoría): era el módulo peor cubierto del repo y el que parsea el input menos confiable del sistema, PDFs de terceros. 36 tests sobre el pipeline de papers — detección heurística (incluida la ventana de 5000 chars), inferencia de título cuando el PDF no trae metadata (caso típico de arXiv), limpieza de bloques de fórmulas, fronteras de sección, fallbacks inline de abstract/keywords, DOI de metadata vs texto, y las dos ramas de `build_classify_content`. Cobertura global: 74% → 76%
- **Markers de test asignados por directorio** (`tests/conftest.py`): un hook de `pytest_collection_modifyitems` marca `integration`/`e2e` según dónde vive el archivo, con `tests/unit/test_suite_hygiene.py` como guard. Un directorio nuevo bajo `tests/` hace fallar el guard hasta que se le decida un marker — obliga a decidir en vez de heredar un default silencioso
- **Regla test-first obligatoria** (`CLAUDE.md` § Validación de código): ninguna funcionalidad ni fix entra sin test que lo cubra, y el test se escribe **antes** que el código — planificar, escribir el test, implementar, verificar. Escribir el test después produce tests que confirman lo implementado en vez de especificar el comportamiento buscado

### Removed
- **Sección `content_extraction` de `config.yaml`** (I1 de la auditoría, decisión tomada 2026-08-13): era la única config declarada sin fase asociada, y su validación podía **abortar el arranque** comparando `engine` contra `{gemini, trafilatura}` — donde `trafilatura` no es dependencia del proyecto ni se importa en ningún lado. Gemini lee las URLs directamente y no hay motor alternativo previsto: no era un contrato, era código muerto. El resto de la config sin consumir (`weekly_report.*`, `sync.interval_minutes`, `llm.max_*_tokens`, `rag.max_expansion_depth`) se mantiene deliberadamente como contrato de fases con diseño escrito, documentado en `docs/configuration.md` con la fase que consumirá cada campo
- **Limpieza de código muerto** (parte del Bloque H de `docs/audit-2026-07-31.md`): `_scope_match` (`reporters.py`, cero callers — lo reemplazó el filtrado por `scope` de `scan_notes`) y `_deserialize_tags` (`embeddings.py`, inverso de `_serialize_metadata` que ningún path de lectura usaba; 5 líneas triviales de reescribir si Fase 7 necesita leer tags desde ChromaDB en vez del disco), con sus 3 tests
- **`vault` era un gitlink huérfano** (modo `160000` en el índice, commiteado en `e995dd4`) **sin `.gitmodules`**: un clone fresco recibía un directorio vacío y `git submodule update` fallaba. Se saca del índice y se agrega `vault/` a `.gitignore` — el repo es público y un vault local tiene notas personales
- Fixtures muertas: `tests/fixtures/sample_notes/` entero (7 archivos, cero referencias en la suite) y `llm_responses/empty_response.json` (el test homónimo construye `{}` inline)

### Fixed
- **Bloque G de la auditoría 2026-07-31 — completo (14/14)**:
  - **G1** — ventana TOCTOU entre elegir el nombre del archivo y escribirlo: una captura y `reclassify_inbox` creando notas con el mismo título el mismo día elegían el mismo candidato y la segunda **sobrescribía a la primera en silencio**. La reserva del nombre pasa a hacerse con `O_EXCL` (atómico a nivel kernel) en el mismo thread que la escritura
  - **G7** — `TELEGRAM_ALLOWED_USER_ID` con un valor no numérico (`"12a"`) dejaba el set de IDs vacío **sin error**: lockout total y silencioso, sin nada en los logs. Ahora falla al arrancar con mensaje explícito. Y `"123,456"` mataba el arranque con `ValueError` crudo desde `config.py`, que hacía `int()` sobre el valor completo mientras `security.py` sí parseaba la lista
  - **G10** — `[Confirmar]` por botón creaba proyecto/área con `description: ""`, violando la regla de CLAUDE.md. `_cb_manage_confirm` la re-valida y la pide. Al arreglarlo apareció que `_handle_manage_missing_fields` asumía que faltaban **ambos** campos: con solo la descripción faltante, tomaba el texto como nombre y pisaba el resuelto por regex
  - **G14** — un `[Confirmar]` de un preview viejo (scrolleando hacia arriba) escribía la nota **nueva** editando el mensaje **viejo**, dejando el preview vigente con botones sin estado. El callback ahora se valida contra el `msg_id` del preview
  - **G2** — el caché del vault devolvía copia *shallow*: las listas (`tags`, `authors`) eran las mismas del caché, así que un `append` de cualquier caller lo envenenaba para todos los scans siguientes. El miss path tenía el mismo problema que el hit
  - **G8** — `/buscar` no respetaba el lock de corrección ni el teclado pendiente, a diferencia del resto de los comandos
  - **G9** — `reindex.time: "3am"` mataba el arranque con traceback crudo; ahora es `ConfigError` con mensaje (también para `weekly_report.time`)
  - **G4** — toda nota escrita por el bot quedaba `0600` (herencia de `mkstemp`) en vez de `0644`; si el archivo ya existía se preserva su modo. **Completado tras verlo en el deploy:** el fix cubría solo `_atomic_write_sync`, o sea las notas `.md`; los adjuntos van por `save_resource` → `shutil.copy2`, que preserva el modo del origen (el temporal de la descarga, 0600), así que todo PDF e imagen de `03-Resources/` quedaba ilegible para cualquier otro usuario o proceso — Syncthing corriendo como otro UID, por ejemplo
  - **G3** — `GitBackup` nunca cerraba el `Repo`: GitPython retiene mmaps y procesos `git cat-file` que solo libera `close()`, y en la RPi hay un backup por captura
  - **G5** — `.replace(".md", "")` sobre el path relativo corrompía `note_id` y los links `obsidian://` si algún directorio contenía ".md" en el nombre (4 sitios)
  - **G6** — `authors` como string en una nota editada a mano hacía que `authors[:2]` devolviera dos **caracteres**: el reporte imprimía "S, m"
  - **G12** — los `_index.md` de gestión no registraban `mark_bot_written` ni notificaban al backup: dependían de que el watcher tratara la escritura propia como cambio externo
  - **G13** — gestión volcaba la excepción cruda (con paths internos) al chat
  - **G11** — el prompt de `create_section` decía "Para crear el **área** hacen falta: nombre de la sección…"
- **Bloque F de la auditoría 2026-07-31 — completo (10/10 pendientes; F9 ya estaba)**:
  - **F2** — el dedup de 2s del watcher **descartaba el último evento** en vez de debouncear: Obsidian autosalva dos veces en <2s y después dejás de editar, así que el re-embed corría con el contenido **intermedio** y el save final se perdía hasta el reindex nocturno — lo contrario del objetivo del watcher. Ahora una ráfaga colapsa a dos llamadas (una inmediata, una al final de la ventana) y `stop()` dispara los pendientes en vez de cancelarlos
  - **F3/F4** — el `callback_data` llevaba el nombre del proyecto/área: sin truncar en los selectores de destino (un directorio de ~27 chars acentuados supera los 64 **bytes** de Telegram → `BadRequest` al abrir `[Elegir área]`) y truncado a 32 chars en reportes (`scope_report` armaba un path inexistente → reporte vacío engañoso, sin error). Ahora viaja un token hash de 10 chars que se resuelve contra el vault; un teclado con el formato viejo sigue funcionando
  - **F5** — los selectores de `[Reubicar]` buscaban por `type: area-index`, así que un área sin `_index.md` era invisible al reubicar aunque apareciera en `/reporte`. Migrados a `_get_existing_items`, como manda CLAUDE.md
  - **F1** — `mark_bot_written` estaba dentro del `if git_backup:`: con `backup.enabled: false` cada nota se indexaba inline **y además** el watcher la re-embebía como cambio externo (llamada redundante a Gemini)
  - **F7** — ante un ID de arXiv inexistente pero bien formado, la API devuelve un feed **con** un entry de error; el chequeo `if not entries` no lo atrapaba y el usuario veía el preview de una "nota" titulada Error, con `source_url` roto contra el que se comparaban duplicados
  - **F8** — los IDs viejos con subclase (`math.GT/0309136`, `cond-mat.str-el/…`) no matcheaban el regex y caían en silencio al flujo de link genérico
  - **F11** — `remove_broken_wikilinks` normalizaba el newline final **siempre**, así que una nota que menciona el link fuera de `## Ver también` se reescribía sin cambio real → mtime bump → re-embed espurio + churn del backup por cada borrado externo. Además su `rglob` corría bloqueante en el event loop
  - **F6** — `/reset` y `[Cancelar]` no borraban el temporal del adjunto por un mismatch de key (`_resource_file` vs `resource_file`): en la RPi4 `/tmp` es tmpfs, o sea RAM filtrada hasta el reinicio
  - **F10** — el `rglob` del reindex nocturno corría síncrono en el event loop
- **Bloque E de la auditoría 2026-07-31 — completo (12/12)** (`docs/audit-2026-07-31.md`), casi todos del tipo "el bot queda inutilizable hasta `/reset`" o "se pierde algo que el usuario escribió":
  - **E8** — una nota de Inbox sin body **bloqueaba la cola de `/clasificar` para siempre**: el código decía "saltando" pero hacía `return`, y `caso_b[0]` elegía siempre la misma nota vacía, dejando el resto inalcanzable. Ahora busca la primera con contenido y nombra las que salteó
  - **E7** — `/clasificar` via el botón `[Clasificar inbox]` crasheaba con `AttributeError`: dos guards usaban `update.message`, que es `None` en un callback. `reply` se resuelve antes de ambos
  - **E4** — una corrección `tag <algo>` metía HTML crudo en el preview y rompía el parse de Telegram: el `edit` fallaba, el fallback también, y con `awaiting_correction` ya en False el preview quedaba sin teclado y sin forma de re-renderizarse. Los tags del usuario ahora pasan por `_to_kebab`, igual que los del LLM
  - **E3** — un error de OCR/Vision dejaba `pending_fallback_pdf` colgado: todo input posterior recibía "Hay una acción pendiente" sin que hubiera botones. Se limpia el estado y se invita a reenviar la imagen
  - **E9** — si fallaba el reply de la transcripción, `pending_transcript` quedaba apuntando a un temporal que el propio `except` borraba
  - **E1** — el caption escrito junto a un PDF (`user_context`) **nunca llegaba al LLM**: se guardaba en `pending_read_status` y no se copiaba ni a `pending_extraction` ni al fallback de PDFs escaneados
  - **E12** — un `on_retry` que lanzaba (hace `edit_message_text`, plausible con la red caída justo cuando Gemini falla) abortaba `classify()` **salteándose el modo degradado**; como los `_cb_intent_*` ya habían popeado `pending_raw_content`, el texto se perdía
  - **E11** — un fallo de `save_resource` era silencioso: "Nota guardada" sin mencionar que el adjunto no se copió, y el temporal quedaba sin borrar
  - **E5** — corrección `titulo` con salto de línea y sin espacios daba `IndexError` (el regex acepta `\n`, la asignación re-spliteaba por espacio literal)
  - **E6** — mandar un segundo archivo mientras uno esperaba descripción sobreescribía el estado y perdía el primero sin aviso
  - **E2** — el `read_status` elegido con `[Ya lo leí]`/`[Lo quiero leer]` se perdía cuando el PDF resultaba escaneado: ni OCR, ni Vision, ni `[Describir]` con caption lo propagaban, así que el paper quedaba sin `read_status` pese a la elección explícita
  - **E10** — si el edit final fallaba por red **después** de que la nota ya estaba escrita, el usuario recibía "Error al guardar" (falso: nota, push a Tasks e indexado ya habían corrido) y el bot intentaba otro edit por la misma red caída
- **`extract_pdf` filtraba el `Document` y propagaba la excepción cruda de mupdf ante PDFs cifrados o corruptos** (F9 de la auditoría): `pymupdf.open()` acepta un PDF cifrado sin chistar — lo que explota es el primer `get_text()`, que quedaba fuera del `try` y con el `doc.close()` inalcanzable. Ahora el bloque entero va en `try/except/finally`: `RuntimeError("No se pudo leer el PDF: …")` como documenta la firma, y `close()` siempre. Salió de la pasada de cobertura de I4 — el test del path cifrado lo dejó en rojo
- **Claves desconocidas de `config.yaml` se descartaban en silencio** (I2 de la auditoría): el config desplegado declaraba `weekly_report.include:` mientras el loader lee `weekly_report.sections:`. Ahora `load_settings` acumula lo ignorado en `Settings.unknown_keys` y lo loguea a `WARNING` con la ruta exacta — sin abortar el arranque, porque un typo en el YAML no puede dejar al usuario sin path de captura. Los tests cargan **los dos** YAML del repo y fallan si vuelven a driftear. Una sección que no sea un mapa (ej: una lista) sí da `ConfigError`. De paso: `_build_weekly_report` tenía un shim para aceptar `sections` como lista cuyo comentario decía que el example usaba `include:` — las dos mitades mal, y el shim leía la clave que ningún archivo tenía. Los nombres de sección del config vivo tampoco coincidían con el schema, así que ni arreglando la clave hubiera matcheado el contenido
- **Los markers `integration`/`e2e` estaban declarados pero aplicados en cero tests**, así que el `-m "not integration and not e2e"` de `.github/workflows/ci.yml` no excluía nada — CI corría los 618. G15 de la auditoría 2026-07-31 lo había reportado al revés ("CI no ejecuta los tests de integración") leyendo el flag en vez de correrlo. El riesgo real era el inverso y peor: aplicar los markers a mano, que es lo natural al leer `docs/testing.md`, habría sacado 193 tests de CI **en silencio**, sin que nada fallara. Ahora los markers se asignan por directorio y CI corre la suite completa en un solo step
- **La cobertura reportada excluía el 40% del código:** `adso/handlers/*` estaba en el `omit` de `pyproject.toml` como "e2e territory", pero los e2e sí lo ejercitan. El 82% se calculaba sobre 2852 statements cuando el código real son 4634 — y un test nuevo sobre un handler no movía el gate, justo donde la regla test-first más hace falta. Cobertura real medida y publicada: **74%**. `bot.py`/`__main__.py` siguen fuera (bootstrap sin lógica propia)

### Changed
- **El `docker-compose.yml` del repo pasa a ser la base del deploy**, en vez de una copia adaptada a mano en el directorio de despliegue. Nada sincronizaba esa copia: `make deploy` solo copia `config.yaml`, así que **los cambios al compose no llegaban nunca a producción**. Se descubrió en el deploy de esta versión — el fix del healthcheck (B4) estuvo semanas commiteado, testeado y documentado como implementado mientras producción corría el roto, sin nada que lo detectara. Ahora lo específico de la máquina vive en `<deploy>/local.yml` (fuera de git) y el Makefile combina ambos con `--project-directory` (para que `.env`, `config.yaml` y `credentials` sigan resolviendo contra el deploy — hay un `.env` distinto en el repo) y `-p adso` (para que el volumen siga siendo `adso_adso-data`, donde viven ChromaDB y whisper). Verificado: la configuración resuelta es byte-idéntica a la anterior, y `docker compose up` no recreó el contenedor. `docs/installation.md` actualizado, porque la instrucción vieja era justamente la que generaba el drift
- **La sugerencia de wikilinks se centraliza en `_suggest_links()`** (I5 de la auditoría, cierra el último pendiente): la secuencia "computar o reusar el embedding → `query_similar` → mapear a links" vivía copiada en tres flujos de captura con variaciones sutiles entre copias. Es justo el invariante que CLAUDE.md marca como delicado —qué texto se embebe y qué vector puede reutilizarse al indexar—, así que tenerlo triplicado era una invitación a que el cuarto flujo copiara la variante equivocada y la nota terminara indexada con un embedding que no corresponde a su texto. Refactor puro, sin cambio de comportamiento: 10 tests de caracterización escritos antes de mover una línea, verdes antes y después. De paso queda explícito que el flujo de arXiv busca por el **abstract** y descarta el vector a propósito, algo que antes solo se deducía de que ese sitio no guardaba nada
- **`CLAUDE.md` reorganizado:** `## Decisiones clave` había crecido a 21KB (el 40% del archivo) mezclando políticas que restringen trabajo futuro con post-mortems de fixes puntuales. Los 14 post-mortems se movieron **verbatim** a `docs/decisions-log.md`, agrupados por módulo y con punteros desde CLAUDE.md; quedan los 21 bullets de taxonomía, invariantes y políticas. La sección baja a 13KB y el archivo de 53KB a 45KB. El contenido movido ya vivía además como comentario en el propio código y en el CHANGELOG — la duplicación costaba contexto en cada sesión sin agregar nada
- **Gemini Vision usa su propio modelo** (`GEMINI_VISION_MODEL = "gemini-3.6-flash"`, overridable con `ADSO_GEMINI_VISION_MODEL`): la quota del free tier de Google es **por modelo**, así que rasterizar un PDF escaneado de 20 páginas ya no consume RPD del mismo bucket que la clasificación de notas, que es el flujo de todos los días. El split no lo motiva la calidad — el resultado de Vision se muestra en el preview y lo valida el usuario antes de confirmar. `/status` muestra ambos modelos y el harness acepta `--vision-model` para evaluar candidatos por separado

---

## [1.3.0] — 2026-08-13

Auditoría 2026-07-31 (bloques A-D, `docs/audit-2026-07-31.md`), harness de regresión de modelo y mantenimiento de CI.

### Added
- **Harness de regresión de modelo LLM** (`scripts/llm_regression.py` + `tests/llm_regression/`): golden set que verifica contra la API real el contrato estructural que el bot asume del LLM, para decidir si actualizar `GEMINI_MODEL` rompe algo. 14 reglas estructurales, no de calidad — la calidad la valida el usuario en el preview antes de confirmar cada nota; el harness mide lo que el usuario *no* ve, sobre todo `validate_llm_response` lanzando (manda toda captura a modo degradado) y la resistencia a prompt injection. Deliberadamente fuera de pytest: pega contra la API y quema quota, así que ni un `pytest` local ni un cambio en CI lo disparan. `make llm-baseline` / `make llm-check MODEL=... BASE=...`. Con `--compare` el exit code refleja regresiones contra la baseline, no fallas absolutas. Baseline de `gemini-3.5-flash-lite`: 34/34, p50 1.5s
- `ADSO_GEMINI_MODEL` overridea `GEMINI_MODEL` sin tocar código (para apuntar el harness a un candidato; en producción sin setear)
- `build_user_message()` extraído de `classify()`, para que el harness construya el mensaje con la misma neutralización de tags que el bot

### Fixed
- **Modo manage por texto libre caía siempre a modo degradado:** el constrained decoding de Gemini solo emite claves declaradas en el schema, y `params` estaba como `OBJECT` sin `properties` → volvía siempre `{}`, incluso con el nombre del proyecto visible en el input. `_validate_manage_payload` lanzaba `LLMResponseError` y el fallback de `_cb_manage_create` proponía el texto crudo del usuario como nombre del proyecto tras gastar 3 reintentos. Detectado por el harness contra el modelo en producción; guard de regresión en `test_manage_params_declares_properties`
- CI / Lint roto por drift de ruff: el job instalaba `ruff` sin pinear y la 0.16.0 (liberada ~2026-07-26) cambió las reglas default (isort etc.) → 312 hallazgos nuevos en un push que no tocó Python. Se pinea `ruff~=0.15.10` en el CI y en las dev deps de `pyproject.toml` (local y CI corren lo mismo). Adoptar la 0.16 con sus fixes queda como tarea aparte

### Data safety
- `frontmatter.Post(body, **fm)` interpretaba una clave `handler` del frontmatter como handler de serialización: `dumps()` escribía ese string como contenido total del archivo (body y frontmatter perdidos en silencio), y una clave `content` lanzaba `TypeError`. Los 4 sitios de escritura usan `_build_post`, que asigna `post.metadata`. Se agrega `load_post()` porque `frontmatter.loads()` tiene el mismo choque de kwargs al *leer* una nota editada externamente (rompía `read_note` y los scans de `vault_cache`). Además `_validate_capture_payload` whitelistea las claves contra `docs/frontmatter-schema.md` (`ALLOWED_FRONTMATTER_KEYS`), cerrando el vector en origen para el fallback de Groq y para prompt injection en PDF/OCR
- `_cb_confirm` popeaba `pending_note`/`clasificar_inbox_path` antes de `create_note`: un fallo de I/O perdía la captura para siempre (crítico para audio, OCR y Vision, cuyo texto no vive en ningún otro lado). Ahora se descartan recién tras la escritura, y el temporal del recurso adjunto también
- `reclassify_inbox` borraba la nota del Inbox antes de crear la nueva; si `create_note` fallaba, el contenido solo vivía en memoria. Orden invertido
- `GitBackup`: se guarda la task del backup en vuelo (`_running`). `_do_backup` la espera antes de correr (nunca dos git en paralelo → sin colisión de `index.lock`) y `flush()` la espera antes de mirar `_pending_titles`, que el backup en vuelo ya drenó — sin esto el shutdown seguía con el push a medio hacer. `notify()` no espera: bloquearía la confirmación del usuario
- El except genérico de `_do_backup` re-encola los títulos drenados al frente de la cola y notifica por Telegram — un fallo de `add`/`commit` dejaba el vault sin backup indefinidamente y en silencio

### Security
- El redirect de `mode=query`/`edit` a `capture` no re-validaba el payload (`validate_llm_response` saltea `_validate_capture_payload` para esos modos), así que un frontmatter crudo de Groq llegaba al vault sin sanitizar y un `frontmatter: null` (legal en el schema de Gemini) mataba el flujo arXiv con `TypeError`. Nuevo `_redirect_unimplemented_mode()` en `capture.py`, que valida y cae a degradado si no se puede sanear
- `@authorized` en `handle_status` — era el único handler registrado sin la segunda barrera de autenticación
- CodeQL bloquea en serio: se removió el `continue-on-error: true` del job `codeql` en `security.yml`. Estaba puesto porque el repo privado no tenía GitHub Advanced Security para subir SARIF; el repo es público desde 2026-07-25 y code scanning es gratis

### Changed
- Confirmar o cancelar una operación de gestión dejaba `manage_missing_fields` residual en `user_data` — como está en `_PENDING_FLOW_KEYS`, cada pasada de `reclassify_inbox` se posponía para siempre y el inbox nunca se drenaba. Nuevo `pop_manage_state()` que popea ambas keys, también en las salidas tempranas de `_cb_manage_confirm`
- Healthcheck de docker-compose: `find` sale 0 aunque no matchee nada, así que un heartbeat congelado nunca marcaba unhealthy. Ahora `CMD-SHELL test -n "$(find /tmp/adso_heartbeat -mmin -2)"`
- Normalización defensiva del frontmatter del LLM: `body: null` → `""` (antes el preview reventaba con `AttributeError` y la captura se perdía); `tags` como string se parte por comas; nuevos `_clean_title()` (regex en bucle: `"## Tarea: X"` → `"X"`) y `_norm_enum()` (`type`/`status`/`priority` a minúsculas antes de validar, así una respuesta correcta de Groq no cae entera a degradado por capitalización)
- Valores no-string del frontmatter (nota editada a mano) ya no tiran abajo la búsqueda ni los reportes: filtros de `vault_search`, `_note_ref_from_data`, `_extract_tags_from_note`, keys de agrupamiento de `reporters` y `_priority_key` coaccionan con `str(... or "")`. Nuevo `_to_naive()` en reporters (`scope_report` mezclaba datetimes aware y naive) y `_parse_date_value` envuelve `fromisoformat` en `try/except` (`2026-02-30` pasa el regex y reventaba la escritura *después* de la confirmación)
- Bump de GitHub Actions por deprecaciones: `actions/checkout` v4→v5 y `actions/setup-python` v5→v6 (Node 20 deprecado en los runners), `github/codeql-action` v3→v4 (v3 se deprecaba en diciembre 2026). `codecov-action@v4` y trufflehog (pineado a SHA) quedan como estaban

---

## [1.2.1] — 2026-07-22

### Changed
- LLM primario migrado de `gemini-3.1-flash-lite` a `gemini-3.5-flash-lite`: sucesor directo en la misma familia flash-lite (mismo free tier holgado ~15 RPM, mismo soporte de schema-constrained JSON), más capaz. Swap 1:1 en `config.GEMINI_MODEL` — sin cambios de código en los call sites. Los tiers "flash" (`gemini-3.6-flash`, `gemini-3.5-flash`) se descartaron: más lentos, más caros en tokens y con free tier más ajustado (~10 RPM), overkill para clasificación estructurada. Nota de rate limits: Google ya no publica los números del free tier en la doc pública; se consultan por proyecto en AI Studio

---

## [1.2.0] — 2026-07-08

Bloque 1 de la auditoría 2026-07 (`docs/improvements-2026-07.md` §1): quick wins de pérdida de datos y drift, más un bugfix de regresión en `/status`.

### Fixed
- `/status` volvía a responder "Ocurrió un error inesperado": el helper síncrono `_gather_vault_counts` (extraído para correr en `asyncio.to_thread`) quedó decorado con `@authorized`, que lo convertía en un coroutine que espera `(update, context)` — el unpacking de la tupla fallaba y el handler caía en el error genérico. El decorador no corresponde en un helper interno; se removió. Regresión introducida en el bloque 1 (ítem 1.6)

### Data safety
- `GitBackup.flush()` se awaitea en `_post_shutdown`: una nota escrita dentro de la ventana de debounce ya no se pierde ante un `docker stop`
- `VaultWatcher.on_moved`: inotify reporta renames como `FileMovedEvent`. Cubre Syncthing (temp+rename) y editores atómicos → re-embed inmediato. La escritura del propio bot (`os.replace`) ahora drena `bot_written_paths`, que antes crecía sin límite (leak) y volvía inefectivo el guard anti-doble-embed. Nuevo helper `mark_bot_written` con cap (512)
- Los temporales de escritura atómica usan sufijo `.tmp` (no `.md`): defensa extra sobre `_is_hidden` y evita que `git add -A` los commitee

### Performance (RPi4)
- `/status` cuenta el vault en `asyncio.to_thread` + `parse_cached` (antes `rglob` bloqueante en el event loop)
- `_get_existing_items` corre en `asyncio.to_thread` (antes síncrono en cada captura)

### Removed
- Código muerto: `_handle_capture`/`_handle_degraded` (~76 líneas), params fantasma de `build_capture_keyboard`, `_has_destination`, `vault_path` de `_cb_correct`
- `docs/gemini-gem-instructions.md` — la gema de Gemini quedó fuera de uso; el desarrollo es 100% Claude

### Docs
- CLAUDE.md: reporte semanal y Tasks bidireccional marcados como diseño no implementado

---

## [1.1.1] — 2026-07-05

Bugfix release after a live incident (Telegram network timeouts mid-capture, 2026-07-05).

### Fixed
- `VaultWatcher` no longer treats the atomic-write temp files (`.adso-tmp-*.md`) as external changes — they were being indexed into ChromaDB as phantom notes and polluting backup commit messages; any hidden dotfile is now ignored
- Global PTB error handler registered (previously "No error handlers are registered"): benign `BadRequest`s ("message is not modified", "query is too old") are ignored, network errors are logged without attempting to notify over the same dead connection, and any other unhandled error notifies the user with a clear message suggesting `/reset`
- A stale `query.answer()` ("query is too old" after network lag) no longer aborts inline-button processing, and "message is not modified" on confirm is treated as silent success — the note was already saved

---

## [1.1.0] — 2026-07-04

Performance and hardening release, driven by a post-release audit (performance / security / docs).

### Performance (RPi4)
- Scanned-PDF rendering (`_render_pdf_pages`) now runs in a worker thread — rasterizing at 200 DPI no longer freezes the event loop for seconds; pages render to in-memory PNGs (no temp files)
- One embedding per capture: the preview's body embedding is reused when confirming (if the body didn't change), and `/buscar` reuses the query embedding on the relaxed-threshold retry — fewer Gemini API calls and lower latency
- Nightly reindex uses the vault parse cache (unchanged notes are not re-read from the SD card)
- Heavy vault jobs (`reclassify_inbox`, `reindex_job`) share a lock so they never overlap
- Whisper transcription with `beam_size=1` (greedy) — 3-5x faster on ARM int8 with marginal quality loss for short voice notes
- `genai.Client` instantiated lazily once per module instead of per request

### Security
- Per-page pixel cap (16 MP) when rasterizing PDFs — a small PDF declaring huge page dimensions can no longer exhaust the RPi4's RAM
- File size limit now also enforced after download when Telegram omits `file_size` (previously the pre-check was skipped for `None`)
- Docker hardening: `no-new-privileges` + `cap_drop: ALL`
- Vault backup SSH: dedicated deploy key + pinned `known_hosts` with `StrictHostKeyChecking=yes`; the install guide no longer suggests mounting `~/.ssh` or disabling host verification
- CI: `trufflehog` action pinned to a commit SHA (was floating on `@main`)
- Search query text no longer logged at INFO level

### Docs
- Bot messages aligned with the impersonal-infinitive style guide; third documentation audit applied (phase 7.0 status, real fixtures, minor drift)

---

## [1.0.0] — 2026-07-04

First public release.

### Added
- Phase 7.0 — semantic retrieval over the vault with `/buscar` and the `[🔎 Buscar en el vault]` button (ChromaDB, no LLM synthesis)
- Verbatim body for text files (`.md`, `.txt`): the LLM only generates frontmatter; the note body is the original content
- Timezone-aware relative date parsing (`ADSO_TIMEZONE` / `TZ` + `tzdata`)
- Vault parse cache keyed by `(mtime, size)` — repeated scans ~69% faster on RPi4; metrics in `/status`
- `/status` shows the running version
- Version is now single-sourced from `adso.__version__` (pyproject reads it dynamically)

### Changed
- Primary LLM migrated to `gemini-3.1-flash-lite` (stable since May 2026); model ID centralized in `config.GEMINI_MODEL`
- `llm_client` split: schema, validation and sanitization moved to `llm_schema.py` (re-exported for compatibility)
- Atomic writes for every vault `.md` (temp + fsync + `os.replace`) — a crash never leaves a truncated note
- Git backup runs fully off the event loop (`asyncio.to_thread`)
- Floating dependencies capped with upper bounds for reproducible builds

### Security
- Fix path traversal vulnerability in `save_resource()` — filename components are now stripped before composing the destination path
- Path sanitization (`_safe_component`) for LLM-provided `project`/`area`/`section` and manage operations
- Expand prompt injection detection to include Spanish-language variants (`ignora las instrucciones`, `ahora eres`, etc.) and common bypasses
- Apply injection check to `user_context` parameter before LLM call
- Neutralize literal `<input>`/`<system>` tags in external content before prompt wrapping
- Global auth gate (`TypeHandler`, `group=-1`) in addition to the per-handler `@authorized` decorator
- Injection warning prepended to previews of externally-extracted content (PDF/OCR/Vision/arXiv)
- Dockerfile: replace `chmod -R 777 /app/data` with explicit `chown` to avoid world-writable data directory
- `config.yaml` untracked (template: `config.yaml.example`); `.dockerignore` added
- Pre-publication audit (July 2026): clean git history verified, docs scrubbed of personal paths

### Docs
- Installation guide reproducible from a fresh clone; test env vars documented; module tree, phase statuses, coverage gate and Python requirement synced with reality

---

## [0.5.0] — 2026-04-09

### Fixed
- `_get_existing_items` reads subdirectories of `01-Projects/` and `02-Areas/` directly (not by `type:area-index`), ensuring all projects/areas with notes appear in reports and keyboards
- Filter out `area-index` notes without an `area:` field in `_get_existing_items`
- Remove `obsidian://` links from Google Tasks `notes` field (links don't work outside Obsidian)
- Deduplicate inotify events in `VaultWatcher` (CREATE + MODIFY on same path within 2s)
- Fix `OAUTHLIB_INSECURE_TRANSPORT` for Google Tasks OAuth fetch-token flow on headless RPi
- Make `auth_google_tasks.py` fully headless (no browser required on RPi)

### Added
- Telegram notifications on Google Tasks push failures with `tasks.debug` config flag for push-success notifications

### Docs
- Complete `installation.md` with vault `.gitignore` and SSH volume setup
- Update CLAUDE.md: Google Tasks, VaultWatcher dedup, tasks.debug

---

## [0.4.0] — 2026-04-08

### Added
- Git backup triggered on external vault changes (Obsidian edits via Syncthing)
- Real-time indexing of notes created externally from Obsidian
- Telegram notification when broken wikilinks are cleaned after external deletion
- Broken wikilink cleanup when a note is deleted externally

### Fixed
- Add `openssh-client` to Docker image for SSH git push
- Create `adso` user with UID 1000 in container (required for SSH to work)

---

## [0.3.0] — 2026-04-03

### Added
- `VaultWatcher`: detects Syncthing conflicts and re-embeds externally modified notes
- Reactive embedding cleanup when notes are deleted externally

### Changed
- Separate deploy repository from development repository

---

## [0.2.0] — 2026-03-29

### Added
- Google Tasks integration (Phase 6 partial): automatic push on task confirmation
- `[Tarea]`/`[Nota]` choice in audio post-transcription flow
- `[Corregir]` button for tasks with date correction in natural language
- `Ver también` section with bullets, short names, and titles from ChromaDB
- `backup.enabled` flag in `config.yaml` to disable git backup
- `user_context` passed to LLM with the task/note choice of the user
- Explicit git author in Docker commit (avoids "unknown author" errors)

### Fixed
- Tag normalization: transliterate accents, filter type-duplicating tags and temporal expressions
- Title sanitization: strip markdown headings and label prefixes (`Tarea:`, `Task:`, etc.)
- Due date resolution: local date parser overrides LLM for relative Spanish expressions
- Prevent type=task when user explicitly chose "nota"
- `/reset` command, correction mode safeguards, test suite
- Timestamps use local timezone (not UTC)
- Three production bugs: empty title, invalid date, `Ver también` in Tasks notes
- Remove `type: draft` — `idea` is now the default for unclassified content

### Changed
- Unified capture keyboard (same layout for notes and tasks)
- Replace voseo with impersonal infinitive in all bot messages

---

## [0.1.0] — 2026-03-28 (initial release)

### Added
- Phase 1: Text capture, LLM classification, confirmation flow, vault write, structural search (backlinks, tags, frontmatter)
- Phase 2: Vault indexing + automatic links (ChromaDB + Gemini embeddings)
- Phase 3: Audio transcription (faster-whisper), PDF extraction (pymupdf), text documents
- Phase 4: Image capture (OCR via pytesseract + Gemini Vision)
- Phase 5: arXiv integration via official Atom API — metadata extraction without scraping
- Phase 8 (partial): Vault reports on demand (`/reporte`, `/reporte_full`): project/area/inbox scope, ideas, reading queue, vault health
- Degraded mode: inbox fallback when LLM unavailable, cron reclassification
- Git backup of vault with debounce (configurable `backup.debounce_seconds`)
- Docker deployment targeting Raspberry Pi 4 (ARM64)
- Syncthing bidirectional sync support
- Duplicate detection for arXiv papers (by `source_url` and `doi`)
- Security: injection detection, constrained JSON output, user ID authentication, confirmation before write
