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

import logging
import re

logger = logging.getLogger(__name__)

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
VALID_READ_STATUS = {"read", "unread"}
# Claves legítimas del frontmatter según docs/frontmatter-schema.md. Cualquier
# otra clave que devuelva el LLM se descarta en `_validate_capture_payload`.
# Motivo de seguridad además de higiene: claves como `handler` o `content`
# rompían la escritura del archivo cuando se pasaban como kwargs a
# `frontmatter.Post` (ver `_build_post` en vault_writer.py); el whitelisteo cierra
# el vector en origen, tanto para el fallback de Groq (sin schema constrained)
# como para una prompt injection en un PDF/OCR.
ALLOWED_FRONTMATTER_KEYS = frozenset({
    # Base
    "title", "date_created", "date_modified", "type", "tags", "source",
    "media_type", "status", "source_file", "source_url", "read_status",
    # Destino
    "project", "section", "area",
    # Contenido / relaciones
    "summary", "related", "priority", "relevance", "context",
    # Tareas
    "due_date", "scheduled",
    # Académicos (pipeline de extracción + LLM)
    "authors", "year", "journal", "doi", "keywords",
    "contribution", "methods", "dataset", "conclusions",
    # Índices de proyecto/área (auto-generados, no del LLM, pero legítimos)
    "description", "sections",
})

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
                # `params` DEBE declarar sus properties: el constrained decoding
                # de Gemini solo puede emitir claves presentes en el schema, así
                # que un OBJECT vacío devolvía siempre `{}` — con el nombre del
                # proyecto visible en el input — y `_validate_manage_payload`
                # tiraba LLMResponseError, mandando todo el modo manage por texto
                # libre a modo degradado. Detectado por scripts/llm_regression.py.
                "params": {
                    "type": "OBJECT",
                    "nullable": True,
                    "properties": {
                        "name": {"type": "STRING", "nullable": True},
                        "description": {"type": "STRING", "nullable": True},
                        "project": {"type": "STRING", "nullable": True},
                        "old_name": {"type": "STRING", "nullable": True},
                        "new_name": {"type": "STRING", "nullable": True},
                        # convert_idea_to_project
                        "note": {"type": "STRING", "nullable": True},
                        "project_name": {"type": "STRING", "nullable": True},
                    },
                },
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

    # Coerce confidence to a float in [0,1]. Small models (o el fallback de Groq
    # sin schema) a veces devuelven "high" o un string; sin esto, la comparación
    # `confidence < threshold` aguas abajo lanzaría TypeError y quemaría un retry.
    conf = response_json.get("confidence", 0.5)
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = 0.5
    response_json["confidence"] = max(0.0, min(1.0, conf))

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


def _clean_title(raw: object) -> str:
    """Coacciona el título a string y le quita headings y prefijos de label.

    Aplica `_TITLE_CLEANUP_RE` en bucle porque ambas alternativas del patrón
    están ancladas en `^`: `"## Tarea: X"` necesita dos pasadas (heading y
    después label) y `re.sub` no reintenta sobre el resultado.

    Args:
        raw: Valor crudo del LLM (puede ser `None` o un tipo inesperado).

    Returns:
        El título limpio, o `""` si no había contenido utilizable.
    """
    title = str(raw or "").strip()
    while True:
        cleaned = _TITLE_CLEANUP_RE.sub("", title).strip()
        if cleaned == title:
            return cleaned
        title = cleaned


def _norm_enum(value: object) -> str:
    """Normaliza un valor de enum del LLM a minúsculas kebab-case.

    Acepta cualquier tipo (incluidos dict/list no hasheables que el fallback de
    Groq puede devolver) sin lanzar: se stringifica antes de normalizar.

    Args:
        value: Valor crudo del LLM.

    Returns:
        El valor normalizado; `""` si era `None`.
    """
    if value is None:
        return ""
    # Los espacios internos se colapsan a guión: un modelo chico devuelve
    # "In Progress" tan seguido como "in-progress", y sin esto el primero no
    # matchea el enum y tira TODA la respuesta a modo degradado.
    return re.sub(r"\s+", "-", str(value).strip().lower())


# `media_type` donde el `type` del LLM se descarta: en texto y audio lo elige el
# usuario con los botones [Tarea]/[Nota] y `capture.py` lo pisa con `forced_type`.
BUTTON_CHOSEN_TYPE_MEDIA = frozenset({"text", "audio"})

# Lo que devuelve un modelo chico cuando el prompt le habla de "notas". Solo se
# aplican donde el type se descarta igual (ver `coerce_discarded_type`): en
# document/image/link el type sí lo decide el LLM y un valor inválido debe
# seguir cayendo a modo degradado.
_TYPE_ALIASES: dict[str, str] = {
    "note": "reference",
    "nota": "reference",
    "referencia": "reference",
    "tarea": "task",
    "todo": "task",
    "recordatorio": "task",
}


def coerce_discarded_type(response_json: object, media_type: str) -> None:
    """Rescata una captura de texto/audio con un `type` inválido.

    `_validate_capture_payload` lanza si el `type` no está en `VALID_TYPES`, y
    `classify()` quema los 3 reintentos y cae a modo degradado. Para
    `media_type` text/audio eso es puro desperdicio: el tipo lo eligió el
    usuario con los botones y el bot lo sobreescribe después, así que la nota
    se degradaba por un campo que iba a descartar igual.

    Args:
        response_json: Respuesta cruda del LLM (se muta in-place).
        media_type: Tipo de medio de la captura.

    Behavior on error: no lanza nunca; si el payload no tiene la forma esperada
    no toca nada y la validación posterior decide.
    """
    if media_type not in BUTTON_CHOSEN_TYPE_MEDIA:
        return
    if not isinstance(response_json, dict) or response_json.get("mode") != "capture":
        return
    payload = response_json.get("payload")
    if not isinstance(payload, dict):
        return
    fm = payload.get("frontmatter")
    if not isinstance(fm, dict):
        return

    note_type = _norm_enum(fm.get("type"))
    if note_type in VALID_TYPES:
        return

    nuevo = _TYPE_ALIASES.get(note_type, "idea")
    logger.warning(
        "type %r inválido para media_type=%r — se usa %r (lo pisan los botones)",
        fm.get("type"), media_type, nuevo,
    )
    fm["type"] = nuevo

    # El `status` venía atado al type descartado: si no aplica al nuevo se
    # descarta (el default aguas abajo lo completa) en vez de hacer fallar la
    # validación por la coerción que acaba de rescatar la respuesta.
    status = _norm_enum(fm.get("status"))
    status = STATUS_ALIASES.get(status, status)
    if status and status not in VALID_STATUS.get(nuevo, set()):
        fm["status"] = None


def _validate_capture_payload(payload: dict) -> None:
    """Validate the capture mode payload."""
    fm = payload.get("frontmatter")
    if not isinstance(fm, dict):
        raise LLMResponseError("capture.payload.frontmatter missing or not an object")

    # Whitelist de claves: cualquier clave fuera del schema documentado se
    # descarta. Sin esto, el fallback de Groq (sin schema constrained) o una
    # prompt injection en un PDF/OCR podían meter claves arbitrarias en el
    # frontmatter que terminaban serializadas en la nota — y `handler`/`content`
    # llegaban a corromper el archivo entero al escribirlo.
    unknown = [k for k in fm if k not in ALLOWED_FRONTMATTER_KEYS]
    for key in unknown:
        logger.warning("Clave de frontmatter fuera del schema, descartada: %r", key)
        del fm[key]

    # El título puede venir null o no-string (Groq sin schema constrained);
    # coaccionar antes de limpiarlo evita un TypeError en el `re.sub`.
    title = _clean_title(fm.get("title"))
    if not title or title == "Sin título":
        fm["title"] = ""  # will be filled with content fallback in classify()
    else:
        fm["title"] = title

    # Enums: normalizar case/espacios antes de validar. Groq devuelve
    # habitualmente "Task"/"Pending"/"High" y una respuesta semánticamente
    # correcta no debe tirar todo el fallback a modo degradado.
    note_type = _norm_enum(fm.get("type"))
    if note_type not in VALID_TYPES:
        raise LLMResponseError(f"Invalid type: {fm.get('type')!r}")
    fm["type"] = note_type

    # Un enum vacío ("" del LLM) es "sin valor", igual que None —que ya se
    # acepta—: se descarta el campo. Antes tiraba toda la respuesta a modo
    # degradado por un campo opcional que el default aguas abajo completa.
    for enum_field in ("status", "priority"):
        val = fm.get(enum_field)
        if isinstance(val, str) and not val.strip():
            fm[enum_field] = None

    status = fm.get("status")
    if status is not None:
        norm_status = _norm_enum(status)
        valid = VALID_STATUS.get(note_type, set())
        if not valid or norm_status in valid:
            fm["status"] = norm_status
        else:
            normalized = STATUS_ALIASES.get(norm_status)
            if normalized and normalized in valid:
                fm["status"] = normalized
            else:
                raise LLMResponseError(
                    f"Invalid status '{status}' for type '{note_type}'"
                )

    priority = fm.get("priority")
    if priority is not None:
        norm_priority = _norm_enum(priority)
        if norm_priority not in VALID_PRIORITY:
            raise LLMResponseError(f"Invalid priority: {priority!r}")
        fm["priority"] = norm_priority

    # Normalize tags to kebab-case; remove type-duplicating and temporal tags
    tags = fm.get("tags")
    if isinstance(tags, str):
        # Groq (sin schema) devuelve a veces "python, ml" como string suelto.
        tags = tags.split(",")
    if isinstance(tags, list):
        fm["tags"] = [
            t for t in (_to_kebab(str(tag)) for tag in tags)
            if t and t not in _TYPE_TAGS and t not in _TEMPORAL_TAGS
        ]
    else:
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
            else:
                # Coaccionar además de validar: Groq (sin schema constrained)
                # devuelve `due_date: 20260101` como int, que `fromisoformat`
                # acepta vía `str(val)` pero después rompe el slice
                # `due_date[:10]` de tasks_client al pushear la tarea.
                fm[date_field] = str(val)

    # Campos académicos: forzar tipos y descartar si no se puede (nunca crashear
    # aguas abajo por un tipo inesperado del LLM, sobre todo del fallback de Groq).
    year = fm.get("year")
    if year is not None:
        try:
            fm["year"] = int(year)
        except (TypeError, ValueError):
            fm["year"] = None

    # authors/keywords deben ser listas de strings. Un string suelto se parte por
    # comas; cualquier otro tipo se descarta.
    for list_field in ("authors", "keywords"):
        val = fm.get(list_field)
        if val is None:
            continue
        if isinstance(val, list):
            fm[list_field] = [str(x).strip() for x in val if str(x).strip()]
        elif isinstance(val, str):
            fm[list_field] = [p.strip() for p in val.split(",") if p.strip()]
        else:
            fm[list_field] = None

    # read_status: enum {read, unread}, descartar si no coincide
    read_status = fm.get("read_status")
    if read_status is not None:
        rs = str(read_status).strip().lower()
        fm["read_status"] = rs if rs in VALID_READ_STATUS else None

    # `summary` (lo usa el flujo de arXiv) puede venir no-string del fallback de
    # Groq, que no tiene schema constrained. Un dict o una lista son truthy, así
    # que el `(payload.get("summary") or "").strip()` de
    # `_classify_and_preview_arxiv` lanzaba AttributeError y mataba la captura
    # del paper. C9 de la auditoría 2026-08.
    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        logger.warning("`summary` no-string descartado: %r", type(summary).__name__)
        payload["summary"] = None

    # `body` puede venir ausente (modelos chicos lo omiten) o explícitamente
    # null — el schema de Gemini lo permite (`nullable: True`). Ambos casos se
    # normalizan a "" para que el preview no reviente con AttributeError.
    if not payload.get("body"):
        payload["body"] = ""


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
        # Se valida el CONTENIDO, no la presencia de la clave: `description: ""`
        # (y `null`, que el schema declara legal) pasaban el chequeo anterior y
        # llegaban al `_index.md`. No es cosmético — `description` es lo que
        # `_get_existing_items` le pasa al prompt como scope de cada destino, así
        # que un proyecto sin descripción se le presenta al LLM sin contexto y
        # degrada el routing de todas las capturas. B8 de la auditoría 2026-08.
        if not str(params.get("description") or "").strip():
            raise LLMResponseError(f"{operation} requires a non-empty 'description'")

    if operation == "create_section":
        if "project" not in params:
            raise LLMResponseError("create_section requires 'project'")
        if "name" not in params:
            raise LLMResponseError("create_section requires 'name'")

    if operation in ("rename_project", "rename_area"):
        if "old_name" not in params or "new_name" not in params:
            raise LLMResponseError(f"{operation} requires 'old_name' and 'new_name'")
