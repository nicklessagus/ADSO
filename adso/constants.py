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
CB_MANAGE_CONFIRM = "manage:confirm"
CB_MANAGE_CANCEL = "manage:cancel"
CB_INTENT_SAVE = "intent:save"
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
CB_DESCRIBE = "describe"
CB_OCR = "ocr"
CB_VISION = "vision"

# Prefijos
CB_DEST_AREA_PREFIX = "dest:area:"
CB_DEST_PROJECT_PREFIX = "dest:project:"
CB_CHOOSE_AREA = "choose:area"
CB_CHOOSE_PROJECT = "choose:project"
CB_BACK = "back"

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
