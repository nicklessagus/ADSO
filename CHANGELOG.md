# Changelog

All notable changes to ADSO are documented here.
Format: [Conventional Commits](https://www.conventionalcommits.org/). Dates are UTC.

---

## [Unreleased]

### Security
- Fix path traversal vulnerability in `save_resource()` — filename components are now stripped before composing the destination path
- Expand prompt injection detection to include Spanish-language variants (`ignora las instrucciones`, `ahora eres`, etc.) and common bypasses
- Apply injection check to `user_context` parameter before LLM call

### Changed
- Dockerfile: replace `chmod -R 777 /app/data` with explicit `chown` to avoid world-writable data directory

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
