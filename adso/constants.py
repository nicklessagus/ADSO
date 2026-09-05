"""Constantes compartidas: taxonomía del vault y callback data de los teclados.

Este módulo no tiene imports locales — es la raíz del grafo de dependencias.
"""

# ---------------------------------------------------------------------------
# Taxonomía del vault
# ---------------------------------------------------------------------------
#
# Única fuente de verdad para los enums del frontmatter. `llm_schema.py` (lo
# que el LLM puede proponer) y `vault_writer.py` (lo que se puede persistir)
# derivan sus sets de acá en vez de repetirlos: antes cada uno tenía su copia y
# `STATUS_ON_CONFIRM` vivía dos veces con dos nombres (capture.py y jobs.py).

# Tipos persistibles. `project-index` y `area-index` los genera el bot, no el LLM.
NOTE_TYPES = frozenset({"reference", "task", "idea", "project-index", "area-index"})

# Tipos que el LLM puede proponer en una captura.
LLM_NOTE_TYPES = frozenset({"reference", "task", "idea"})

# `status` válidos por tipo. `area-index` no tiene ciclo de vida (set vacío).
STATUS_BY_TYPE: dict[str, frozenset[str]] = {
    "reference": frozenset({"active", "pending-classification"}),
    "task": frozenset({"pending", "in-progress", "done", "pending-classification"}),
    "idea": frozenset({"raw", "implemented", "discarded", "pending-classification"}),
    "project-index": frozenset({"active", "on-hold", "completed", "archived"}),
    "area-index": frozenset(),
}

VALID_PRIORITY = frozenset({"low", "medium", "high"})

# Status que corresponde a cada type cuando una nota deja de estar en
# `pending-classification` (el usuario la confirma, la reubica o el cron la
# reclasifica). Los sets de `STATUS_BY_TYPE` son disjuntos por tipo: una task
# nunca puede quedar en `active` ni en `raw`, o los filtros y reportes por
# `status` dejan de verla.
STATUS_ON_CONFIRM: dict[str, str] = {
    "reference": "active",
    "task": "pending",
    "idea": "raw",
}

# Medios cuyo body es siempre el texto original del usuario, nunca la
# reescritura del LLM. Lo aplican la captura interactiva y el cron de
# reclasificación (#64); para document/image/link el LLM sí genera el body.
VERBATIM_BODY_MEDIA = frozenset({"text", "audio"})

# Carpetas que ningún scan ni índice debe mirar por defecto. Es el mismo valor
# que el default de `vault.exclude_dirs` en config.yaml.
DEFAULT_EXCLUDE_DIRS = ("05-Archive", ".obsidian", ".trash")

# ---------------------------------------------------------------------------
# Callback data constants
# ---------------------------------------------------------------------------

CB_CONFIRM = "confirm"
CB_CANCEL = "cancel"
CB_CORRECT = "correct"
CB_DEST_INBOX = "dest:inbox"
CB_DISAMBIG_QUERY = "disambig:query"
CB_QUERY_REPORT = "query:report"
CB_MANAGE_CONFIRM = "manage:confirm"
CB_MANAGE_CANCEL = "manage:cancel"
CB_INTENT_TASK = "intent:task"
CB_INTENT_NOTE = "intent:note"
CB_INTENT_CREATE_PROJECT = "intent:project"
CB_INTENT_CREATE_AREA = "intent:area"
CB_CLASIFICAR_INBOX = "clasificar:inbox"

# Audio / documento
CB_TRANSCRIPT_OK = "transcript:ok"
CB_TRANSCRIPT_CANCEL = "transcript:cancel"
CB_TRANSCRIPT_CORRECT = "transcript:correct"
CB_READ_STATUS_READ = "read:read"
CB_READ_STATUS_UNREAD = "read:unread"
CB_EXTRACTION_OK = "extraction:ok"
CB_EXTRACTION_CANCEL = "extraction:cancel"
CB_EXTRACTION_CORRECT = "extraction:correct"
CB_DESCRIBE = "describe"
CB_OCR = "ocr"
CB_VISION = "vision"

# arXiv
CB_ARXIV_CREATE_ANYWAY = "arxiv:create_anyway"

# Documento subido que ya está en el vault (mismo contenido en 03-Resources/).
# Callback propio y no el de arXiv porque el estado a retomar es otro.
CB_DOC_CREATE_ANYWAY = "doc:create_anyway"

# Corrección de nota (tarea)
CB_NOTE_CORRECT = "note:correct"

# Prefijos
CB_DEST_AREA_PREFIX = "dest:area:"
CB_DEST_PROJECT_PREFIX = "dest:project:"
CB_CHOOSE_AREA = "choose:area"
CB_CHOOSE_PROJECT = "choose:project"
CB_BACK = "back"

# ---------------------------------------------------------------------------
# Report callback constants
# ---------------------------------------------------------------------------

CB_REPORT_MENU = "rpt:menu"
CB_REPORT_SCOPE = "rpt:scope"
CB_REPORT_IDEAS = "rpt:ideas"
CB_REPORT_HEALTH = "rpt:health"
CB_REPORT_READING = "rpt:reading"
CB_REPORT_SCOPE_PREFIX = "rpt:s:"    # + "p:name" | "a:name" | "inbox"
CB_REPORT_IDEAS_PREFIX = "rpt:i:"   # + "p:name" | "a:name" | "all"
CB_REPORT_READING_PREFIX = "rpt:r:" # + "p:name" | "a:name" | "all"
# Paso intermedio: muestra lista de proyectos o áreas según tipo de reporte
CB_REPORT_SCOPE_SHOW_P = "rpt:sp"   # → lista de proyectos para scope
CB_REPORT_SCOPE_SHOW_A = "rpt:sa"   # → lista de áreas para scope
CB_REPORT_IDEAS_SHOW_P = "rpt:ip"   # → lista de proyectos para ideas
CB_REPORT_IDEAS_SHOW_A = "rpt:ia"   # → lista de áreas para ideas
CB_REPORT_READING_SHOW_P = "rpt:rp" # → lista de proyectos para lectura
CB_REPORT_READING_SHOW_A = "rpt:ra" # → lista de áreas para lectura

# ---------------------------------------------------------------------------
# Keywords de gestión para detección sin LLM
# ---------------------------------------------------------------------------

MANAGE_KEYWORDS: dict[str, set[str]] = {
    "project": {"proyecto", "project"},
    "area":    {"área", "area"},
    "archive": {"archivar", "archive"},
    "delete":  {"borrar", "eliminar", "delete"},
    "rename":  {"renombrar", "rename"},
}
