# Roadmap

Development is organized in phases. Each phase extends the previous one.

## Status Overview

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Text capture, classification, vault write, structural search | Done |
| 2 | Semantic indexing (embeddings + ChromaDB) + automatic links | Done |
| 3 | Audio (faster-whisper) + PDF (pymupdf) + text documents | Done |
| 4 | Images: OCR (pytesseract) + Gemini Vision | Done |
| 5 | arXiv integration via official Atom API | Done |
| 6 | Google Calendar + Google Tasks | Partial (Tasks done, Calendar deferred) |
| 7 | Natural language RAG queries | Partial (`/buscar` retrieval done; scope/expansion/synthesis pending) |
| 8 | Vault analysis: reports, health, reading queue | Partial |

---

## Phase 6 — Google Calendar + Tasks

**Tasks (done):**
- Automatic push to dedicated `ADSO` list on task confirmation
- `due_date` maps to Google Tasks date field → appears as chip in Calendar
- Telegram notification on push failure
- `tasks.debug` config flag for verbose push logging
- External lists readable (not written)

**Calendar (deferred — design pending):**
- Write `scheduled` events to dedicated `ADSO` calendar
- Bidirectional sync: vault `status: done` ↔ Google Tasks completed
- Weekly planning report via calendar view
- Re-auth flow for expired OAuth tokens (7-day limit in Testing mode)

Design document: `docs/fase6-scheduling-design.md`

---

## Phase 7 — RAG Queries (in progress)

Natural language search over the vault using ChromaDB + LLM synthesis.

**Done (7.0):** pure semantic retrieval via `/buscar` and the `[🔎 Buscar en el vault]` button — inline results for up to 3 matches, `.md` report for larger result sets, source citations with `obsidian://` links, and a configurable similarity threshold (`rag.similarity_threshold` in `config.yaml`). No LLM synthesis yet. Design in `docs/fase7-rag-design.md`.

**Pending:**

- `mode=query` in LLM classifier (currently redirected to capture)
- Retrieval pipeline: semantic search → structural search → merge → LLM synthesis
- Scope disambiguation: bot asks `[Todo]` `[Proyecto1]` ... if not specified
- Expansion from a node: `[Solo relaciones directas]` `[Expandir un grado más]`

---

## Phase 8 — Vault Analysis (in progress)

**Done:**
- `/reporte` command: project/area/inbox scope, ideas, reading queue, vault health
- `/reporte_full`: same four report types as `/reporte`, with the full body of each note instead of a one-line summary
- Standard report header (ASCII logo + version + date)
- LLM synthesis in reports

**Planned:**
- Paper scoring: relevance, citation density, recency
- Gap detection: topics in one project not referenced in related projects
- Stale note detection: notes never retrieved in RAG results
- Weekly digest: auto-sent report on the configured day (`weekly_report.day` in `config.yaml`, default Friday) — config and reporters already exist, the scheduling job does not yet

---

## Future Ideas (post Phase 8)

These require a mature vault with sufficient notes and embeddings.

**Semantic analysis:**
- Topic clustering: UMAP + HDBSCAN over ChromaDB embeddings, LLM-labeled clusters. Viable on RPi4.
- Method transfer across projects: cross-reference `methods` fields in papers to surface applicable techniques.
- Internal citation network: `cites` field in papers, PageRank to identify foundational papers and reading gaps.
- Temporal analysis: topic evolution over time, detection of active research fronts.

**Output formats:**
- Canvas generation: `.canvas` files from embedding clusters (Obsidian graph view).
- Annotated bibliography: consolidated document per project grouped by method/topic.
- PDF export: `/reporte` and `/reporte_full` as PDF via `fpdf2` (pure Python, ARM64-native).

**External integrations:**
- NASA ADS: bulk import of paper collections or periodic sync (OAuth or API key + deduplication by DOI/arXiv ID).
- Git push retry in heartbeat: check for unpushed commits and push silently.

**Infrastructure:**
- Kubernetes or Docker Swarm deployment for multi-instance setups.
- Prometheus metrics endpoint for vault stats and bot health.
- Webhook mode (instead of polling) for reduced latency and resource usage.
