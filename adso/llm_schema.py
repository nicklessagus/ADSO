"""Schema, validación y sanitización de respuestas del LLM.

Separado de `llm_client.py` (que se ocupa de las llamadas a la API, retries y
modo degradado) para mantener la lógica de contrato/validación aislada de la
de transporte. Prepara la Fase 7 (RAG) sin mezclar ambos planos.

Contiene:
- Constantes de validación (modos, tipos, estados, prioridades, operaciones).
- Patrones de detección de prompt injection (ES + EN).
- El schema de salida restringida de Gemini.
- Validación de la respuesta del LLM y sanitización del frontmatter (tags,
  títulos, fechas).

Referencia: docs/security.md (JSON schema)
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

VALID_MODES = {"capture", "query", "edit", "manage"}
VALID_TYPES = {"reference", "task", "idea"}  # LLM proposes only these 3
VALID_STATUS = {
    "reference": {"active", "pending-classification"},
    "task": {"pending", "in-progress", "done", "pending-classification"},
    "idea": {"raw", "implemented", "discarded", "pending-classification"},
}
# Aliases the LLM may return → canonical value
STATUS_ALIASES: dict[str, str] = {
    "todo": "pending",
    "open": "pending",
    "new": "pending",
    "draft": "raw",
    "published": "active",
}
VALID_PRIORITY = {"low", "medium", "high"}
VALID_OPERATIONS = {
    "create_project", "create_area", "archive_project", "unarchive_project",
    "delete_project", "delete_area", "rename_project", "rename_area",
    "create_section", "convert_idea_to_project", "reclassify_inbox",
}

# Prompt injection patterns — English and Spanish variants.
# Checks are case-insensitive (re.IGNORECASE in check_injection_risk).
INJECTION_PATTERNS = [
    # English
    r"ignore (previous|all|your|the) instructions",
    r"disregard (previous|all|your|the) instructions",
    r"forget (what|everything|all)",
    r"you are now (a|an|the)",
    r"new instructions\s*:",
    r"system prompt",
    r"act as (a|an|the)",
    r"from now on",
    # XML/tag injection — breaking out of <input> wrapper
    r"</?(input|system|instructions?|user_context|prompt)>",
    # Spanish variants
    r"ignora (las|tus|todas las|las anteriores|tus anteriores) instrucciones",
    r"olvida (las instrucciones|todo|el contexto|lo anterior|tus instrucciones)",
    r"ahora (eres|actúa como|actua como|sos)",
    r"actúa como (un|una|el|la)",
    r"actua como (un|una|el|la)",
    r"nuevas instrucciones\s*:",
    r"a partir de ahora",
    r"eres (un|una|ahora)",
    r"pretende (ser|que eres)",
]


# ---------------------------------------------------------------------------
# Gemini constrained output schema
# ---------------------------------------------------------------------------

_GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "required": ["mode", "confidence", "payload"],
    "properties": {
        "mode": {"type": "STRING"},
        "confidence": {"type": "NUMBER"},
        "payload": {
            "type": "OBJECT",
            "properties": {
                # Capture mode
                "frontmatter": {
                    "type": "OBJECT",
                    "nullable": True,
                    "properties": {
                        "title": {"type": "STRING"},
                        "type": {"type": "STRING"},
                        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "status": {"type": "STRING"},
                        "project": {"type": "STRING", "nullable": True},
                        "section": {"type": "STRING", "nullable": True},
                        "area": {"type": "STRING", "nullable": True},
                        "priority": {"type": "STRING", "nullable": True},
                        "due_date": {"type": "STRING", "nullable": True},
                        "scheduled": {"type": "STRING", "nullable": True},
                        # Academic fields
                        "authors": {
                            "type": "ARRAY",
                            "nullable": True,
                            "items": {"type": "STRING"},
                        },
                        "year": {"type": "INTEGER", "nullable": True},
                        "journal": {"type": "STRING", "nullable": True},
                        "doi": {"type": "STRING", "nullable": True},
                        "keywords": {
                            "type": "ARRAY",
                            "nullable": True,
                            "items": {"type": "STRING"},
                        },
                        "read_status": {"type": "STRING", "nullable": True},
                    },
                },
                "body": {"type": "STRING", "nullable": True},
                "summary": {"type": "STRING", "nullable": True},
                # Manage mode
                "operation": {"type": "STRING", "nullable": True},
                "params": {"type": "OBJECT", "nullable": True},
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt injection detection
# ---------------------------------------------------------------------------


def check_injection_risk(content: str) -> bool:
    """Detect common prompt injection patterns in content.

    Args:
        content: Text to analyze.

    Returns:
        True if a suspicious pattern is detected.
    """
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return True
    return False


# ---------------------------------------------------------------------------
# LLM response validation
# ---------------------------------------------------------------------------


class LLMResponseError(Exception):
    """Invalid or unparseable LLM response."""


def validate_llm_response(response_json: dict) -> dict:
    """Validate the LLM JSON response against the expected schema.

    Args:
        response_json: Parsed dict from the LLM response.

    Returns:
        The same dict if valid.

    Raises:
        LLMResponseError: If the response does not meet the schema.
    """
    if not isinstance(response_json, dict):
        raise LLMResponseError("LLM response is not a JSON object")

    mode = response_json.get("mode")
    if not mode:
        raise LLMResponseError("Missing 'mode' field in response")
    if mode not in VALID_MODES:
        raise LLMResponseError(f"Invalid mode: {mode!r}")

    if "confidence" not in response_json:
        # Default to 0.5 if omitted
        response_json["confidence"] = 0.5

    payload = response_json.get("payload")
    if not isinstance(payload, dict):
        raise LLMResponseError("Missing 'payload' field or not an object")

    if mode == "capture":
        _validate_capture_payload(payload)
    elif mode == "manage":
        _validate_manage_payload(payload)
    # query and edit validated in future phases

    return response_json


# ---------------------------------------------------------------------------
# Frontmatter sanitization (tags, titles, dates)
# ---------------------------------------------------------------------------


_ACCENT_MAP = str.maketrans(
    "áàäâéèëêíìïîóòöôúùüûñçÁÀÄÂÉÈËÊÍÌÏÎÓÒÖÔÚÙÜÛÑÇ",
    "aaaaeeeeiiiioooouuuuncAAAAEEEEIIIIOOOOUUUUNC",
)

# Tags that duplicate frontmatter fields — filtered out regardless of model
_TYPE_TAGS = frozenset({
    "task", "tarea", "note", "nota", "idea", "reference",
    "paper", "document", "audio", "image", "link",
})

# Tags that are temporal expressions — not useful as long-term labels
_TEMPORAL_TAGS = frozenset({
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "hoy", "manana", "today", "tomorrow", "proxima-semana", "next-week",
})

# Regex for stripping markdown heading markers and common label prefixes from titles
_TITLE_CLEANUP_RE = re.compile(
    r'^#+\s*'                           # markdown headings: ##, ###, etc.
    r'|^(tarea|task|nota|note|recordar|reminder|idea)\s*:\s*',
    re.IGNORECASE,
)


def _to_kebab(tag: str) -> str:
    """Normalize a tag to kebab-case (lowercase, spaces→hyphens, strip invalid chars).

    Transliterates accented/Spanish characters before stripping so that
    e.g. 'mañana' → 'manana' instead of 'maana'.
    """
    tag = tag.lower().strip()
    tag = tag.translate(_ACCENT_MAP)            # ñ→n, á→a, etc.
    tag = re.sub(r"[\s_]+", "-", tag)          # spaces and underscores → hyphens
    tag = re.sub(r"[^a-z0-9\-]", "", tag)      # remove anything else
    tag = re.sub(r"-{2,}", "-", tag)            # collapse consecutive hyphens
    return tag.strip("-")


def _validate_capture_payload(payload: dict) -> None:
    """Validate the capture mode payload."""
    fm = payload.get("frontmatter")
    if not isinstance(fm, dict):
        raise LLMResponseError("capture.payload.frontmatter missing or not an object")

    title = fm.get("title", "")
    # Strip markdown heading markers and label prefixes (e.g. "# Tarea: foo" → "foo")
    title = _TITLE_CLEANUP_RE.sub("", title).strip()
    if not title or title == "Sin título":
        fm["title"] = ""  # will be filled with content fallback in classify()
    else:
        fm["title"] = title

    note_type = fm.get("type")
    if note_type not in VALID_TYPES:
        raise LLMResponseError(f"Invalid type: {note_type!r}")

    status = fm.get("status")
    if status is not None:
        valid = VALID_STATUS.get(note_type, set())
        if valid and status not in valid:
            normalized = STATUS_ALIASES.get(status)
            if normalized and normalized in valid:
                fm["status"] = normalized
            else:
                raise LLMResponseError(
                    f"Invalid status '{status}' for type '{note_type}'"
                )

    priority = fm.get("priority")
    if priority is not None and priority not in VALID_PRIORITY:
        raise LLMResponseError(f"Invalid priority: {priority!r}")

    # Normalize tags to kebab-case; remove type-duplicating and temporal tags
    tags = fm.get("tags")
    if isinstance(tags, list):
        fm["tags"] = [
            t for t in (_to_kebab(str(tag)) for tag in tags)
            if t and t not in _TYPE_TAGS and t not in _TEMPORAL_TAGS
        ]
    elif tags is None:
        fm["tags"] = []

    # Sanitize due_date and scheduled: must be valid ISO 8601, else discard
    for date_field in ("due_date", "scheduled"):
        val = fm.get(date_field)
        if val is not None:
            try:
                from datetime import datetime as _dt
                _dt.fromisoformat(str(val))
            except (ValueError, TypeError):
                fm[date_field] = None

    if "body" not in payload:
        payload["body"] = ""  # small models occasionally omit the body


def _validate_manage_payload(payload: dict) -> None:
    """Validate the manage mode payload."""
    operation = payload.get("operation")
    if operation not in VALID_OPERATIONS:
        raise LLMResponseError(f"Invalid operation: {operation!r}")

    params = payload.get("params")
    if not isinstance(params, dict):
        raise LLMResponseError("manage.payload.params missing or not an object")

    # Validate required params per operation
    if operation in ("create_project", "create_area"):
        if "name" not in params:
            raise LLMResponseError(f"{operation} requires 'name'")
        if "description" not in params:
            raise LLMResponseError(f"{operation} requires 'description'")

    if operation == "create_section":
        if "project" not in params:
            raise LLMResponseError("create_section requires 'project'")
        if "name" not in params:
            raise LLMResponseError("create_section requires 'name'")

    if operation in ("rename_project", "rename_area"):
        if "old_name" not in params or "new_name" not in params:
            raise LLMResponseError(f"{operation} requires 'old_name' and 'new_name'")
