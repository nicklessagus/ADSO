"""Constantes de callback data para inline keyboards de Telegram.

Este módulo no tiene imports locales — es la raíz del grafo de dependencias.
"""

# ---------------------------------------------------------------------------
# Callback data constants
# ---------------------------------------------------------------------------

CB_CONFIRM = "confirm"
CB_CANCEL = "cancel"
CB_CORRECT = "correct"
CB_DEST_INBOX = "dest:inbox"
CB_DISAMBIG_CAPTURE = "disambig:capture"
CB_DISAMBIG_QUERY = "disambig:query"
CB_QUERY_REPORT = "query:report"
CB_MANAGE_CONFIRM = "manage:confirm"
CB_MANAGE_CANCEL = "manage:cancel"
CB_INTENT_SAVE = "intent:save"
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

# Corrección de nota (tarea)
CB_NOTE_CORRECT = "note:correct"

# Prefijos
CB_DEST_AREA_PREFIX = "dest:area:"
CB_DEST_PROJECT_PREFIX = "dest:project:"
CB_CHOOSE_AREA = "choose:area"
CB_CHOOSE_PROJECT = "choose:project"
CB_BACK = "back"

# ---------------------------------------------------------------------------
# Keywords de gestión para detección sin LLM
# ---------------------------------------------------------------------------

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
