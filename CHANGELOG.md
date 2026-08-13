# Changelog

All notable changes to ADSO are documented here.
Format: [Conventional Commits](https://www.conventionalcommits.org/). Dates are UTC.

---

## [Unreleased]

### Changed
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
