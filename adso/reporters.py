"""Generación de reportes del vault a pedido.

Cada función retorna bytes de un archivo .md listo para enviar como documento
en Telegram via send_document + BytesIO.

Los reportes incluyen un header estándar ASCII, síntesis LLM opcional y
secciones con notas y links obsidian://.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from adso import __version__ as ADSO_VERSION
from adso.config import GEMINI_MODEL
from adso.vault_search import scan_notes
from adso.vault_writer import NoteData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Header ASCII estándar
# ---------------------------------------------------------------------------

_ASCII_HEADER = """\
      ,
     /|
    / |   \u2588\u2588\u2588\u2588\u2588     \u2588\u2588\u2588\u2588\u2588\u2588      \u2588\u2588\u2588\u2588\u2588     \u2588\u2588\u2588\u2588\u2588
   / /   \u2588\u2588   \u2588\u2588    \u2588\u2588   \u2588\u2588    \u2588\u2588        \u2588\u2588   \u2588\u2588
  | /    \u2588\u2588   \u2588\u2588    \u2588\u2588   \u2588\u2588     \u2588\u2588\u2588\u2588     \u2588\u2588   \u2588\u2588
  |/     \u2588\u2588\u2588\u2588\u2588\u2588\u2588    \u2588\u2588   \u2588\u2588        \u2588\u2588    \u2588\u2588   \u2588\u2588
  |      \u2588\u2588   \u2588\u2588    \u2588\u2588\u2588\u2588\u2588\u2588     \u2588\u2588\u2588\u2588\u2588      \u2588\u2588\u2588\u2588\u2588
 _|_
/   \\    Autonomous Data Structuring Orchestrator
|>_ |
\\___/    𝘴𝘤𝘳𝘪𝘱𝘵𝘰𝘳𝘪𝘶𝘮 𝘥𝘪𝘨𝘪𝘵𝘢𝘭𝘦"""


# Priority ordering for sort
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, None: 3, "": 3}


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _to_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Devuelve el datetime sin tzinfo para poder compararlo con otros naive.

    Las notas que escribe ADSO usan fechas naive, pero un plugin de Obsidian (o
    una edición a mano) puede dejar un offset. Mezclar aware y naive en una
    comparación lanza `TypeError` y tira abajo el reporte entero.

    Args:
        dt: Datetime aware o naive, o None.

    Returns:
        El mismo datetime sin tzinfo, o None si la entrada era None.
    """
    if dt is not None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _parse_fm_date(val) -> Optional[datetime]:
    """Parsea un valor de fecha del frontmatter a datetime.

    Soporta: datetime nativo, date nativo, string ISO 8601 (fecha o fecha+hora).

    Args:
        val: Valor crudo del frontmatter (datetime, date, str, o None).

    Returns:
        datetime en UTC o naive, o None si no se puede parsear.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day)
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        # Intentar varios formatos
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
    return None


def _normalize_authors(authors) -> list[str]:
    """Normaliza el campo `authors` del frontmatter a lista de strings.

    `authors[:2]` sobre un string devuelve dos **caracteres**, así que una nota
    con `authors: "Smith, J."` —editada a mano o creada por otro cliente—
    imprimía "S, m" en el reporte. El coercer de `llm_schema` solo cubre el
    payload del LLM, no las notas releídas del vault. G6 de
    docs/audit-2026-07-31.md.

    Args:
        authors: Valor crudo del frontmatter (lista, string, None u otro).

    Returns:
        Lista de strings, vacía si no había nada.
    """
    if isinstance(authors, list):
        return [str(a) for a in authors]
    if authors:
        return [str(authors)]
    return []


def _obsidian_link(vault_path: Path, note_path: Path) -> str:
    """Genera un link obsidian:// para abrir una nota directamente.

    Args:
        vault_path: Raíz del vault.
        note_path: Path absoluto a la nota.

    Returns:
        String con el link obsidian://.
    """
    vault_name = urllib.parse.quote(vault_path.name)
    rel = str(note_path.relative_to(vault_path).with_suffix(""))
    file_encoded = urllib.parse.quote(rel, safe="/")
    return f"obsidian://open?vault={vault_name}&file={file_encoded}"


def _report_header(title: str, today: Optional[date] = None, full: bool = False) -> str:
    """Genera el header estándar del reporte.

    Args:
        title: Título del reporte.
        today: Fecha del reporte. Default: hoy.
        full: True si es un reporte full (incluye cuerpo completo de cada nota).

    Returns:
        String con el header formateado.
    """
    if today is None:
        today = date.today()
    date_str = today.strftime("%d/%m/%Y")
    full_badge = "  |  **reporte full**" if full else ""
    return f"```\n{_ASCII_HEADER}\n```\n\n---\n\n# {title}\n\n**Fecha:** {date_str}  |  **ADSO** v{ADSO_VERSION}{full_badge}\n\n"


def _priority_key(note: NoteData) -> int:
    """Clave de ordenamiento por prioridad (high < medium < low < null)."""
    p = note.frontmatter.get("priority")
    if p is not None and not isinstance(p, str):
        # Valor no-string (o no hasheable) de una nota editada a mano.
        p = str(p)
    return _PRIORITY_ORDER.get(p.lower() if isinstance(p, str) else p, 3)


async def _llm_synthesis(report_summary: str) -> Optional[str]:
    """Genera una síntesis breve del reporte usando Gemini (texto libre).

    Si la API falla, retorna None sin bloquear el reporte.

    Args:
        report_summary: Resumen compacto del contenido del reporte.

    Returns:
        Síntesis en español (2-3 oraciones) o None.
    """
    try:
        from google.genai import types

        from adso.llm_client import _get_genai_client

        client = _get_genai_client()
        prompt = (
            "Sos un asistente que genera síntesis ejecutivas de reportes de vault personal.\n"
            "Generá una síntesis en español de 2-3 oraciones que describa el estado general "
            "del vault según la información del reporte. Sé conciso y útil.\n\n"
            f"Reporte:\n{report_summary}"
        )

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="text/plain",
            ),
        )

        text = response.text
        return text.strip() if text else None

    except Exception as e:
        logger.warning("_llm_synthesis falló (no bloqueante): %s", e)
        return None


def _note_line(vault_path: Path, note: NoteData, extra: str = "") -> str:
    """Genera una línea de referencia a una nota con link obsidian://.

    Args:
        vault_path: Raíz del vault.
        note: NoteData de la nota.
        extra: Información adicional (status, priority, etc.).

    Returns:
        Línea Markdown formateada.
    """
    title = note.frontmatter.get("title") or note.path.stem
    link = _obsidian_link(vault_path, note.path)
    base = f"- [{title}]({link})"
    if extra:
        base += f" — {extra}"
    return base


def _note_block(vault_path: Path, note: NoteData, extra: str = "") -> str:
    """Genera un bloque completo de una nota con título, link, metadata y cuerpo.

    Usado en reportes full para mostrar el contenido completo de cada nota.

    Args:
        vault_path: Raíz del vault.
        note: NoteData de la nota.
        extra: Información adicional (status, priority, etc.).

    Returns:
        Bloque Markdown con título como heading, metadata y cuerpo de la nota.
    """
    title = note.frontmatter.get("title") or note.path.stem
    link = _obsidian_link(vault_path, note.path)
    parts = [f"#### [{title}]({link})"]
    if extra:
        parts.append(f"_{extra}_")
    body = (note.body or "").strip()
    if body:
        parts.append("")
        parts.append(body)
    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Reporte por scope (proyecto / área / inbox)
# ---------------------------------------------------------------------------


async def scope_report(
    vault_path: Path,
    project: Optional[str] = None,
    area: Optional[str] = None,
    inbox: bool = False,
    full: bool = False,
) -> bytes:
    """Genera un reporte de scope para un proyecto, área o el inbox.

    Incluye: referencias activas, tareas por estado, ideas por estado,
    papers sin leer, y última actividad.

    Args:
        vault_path: Raíz del vault.
        project: Nombre del proyecto (o None).
        area: Nombre del área (o None).
        inbox: True si el scope es el inbox (00-Inbox/).
        full: True para incluir el cuerpo completo de cada nota.

    Returns:
        Bytes del .md generado.
    """
    exclude = ["05-Archive", ".obsidian", ".trash"]

    if inbox:
        scope_path = "00-Inbox"
    elif project:
        scope_path = f"01-Projects/{project}"
    elif area:
        scope_path = f"02-Areas/{area}"
    else:
        scope_path = None

    all_notes = await scan_notes(vault_path, scope=scope_path, exclude_dirs=exclude)

    # Separar por tipo
    references = [n for n in all_notes if n.frontmatter.get("type") == "reference"]
    tasks = [n for n in all_notes if n.frontmatter.get("type") == "task"]
    ideas = [n for n in all_notes if n.frontmatter.get("type") == "idea"]
    papers_unread = [
        n for n in references
        if str(n.frontmatter.get("read_status", "")).lower() == "unread"
    ]

    # Última actividad
    last_modified: Optional[datetime] = None
    for note in all_notes:
        dt = _to_naive(_parse_fm_date(note.frontmatter.get("date_modified")))
        if dt and (last_modified is None or dt > last_modified):
            last_modified = dt

    # Título del scope
    if inbox:
        scope_label = "Inbox"
    elif project:
        scope_label = f"Proyecto: {project}"
    elif area:
        scope_label = f"Área: {area}"
    else:
        scope_label = "Vault completo"

    title = f"Reporte de scope — {scope_label}"

    # Construir resumen para síntesis LLM
    summary_parts = [
        f"Scope: {scope_label}",
        f"Referencias activas: {len(references)}",
        f"Tareas: {len(tasks)} total",
        f"Ideas: {len(ideas)} total",
        f"Papers sin leer: {len(papers_unread)}",
    ]
    if last_modified:
        summary_parts.append(f"Última actividad: {last_modified.strftime('%Y-%m-%d')}")

    synthesis = await _llm_synthesis("\n".join(summary_parts))

    # --- Construir documento ---
    _render = _note_block if full else _note_line
    lines: list[str] = [_report_header(title, full=full)]

    if synthesis:
        lines.append(f"> {synthesis}\n")

    # --- Referencias activas ---
    active_refs = [n for n in references if n.frontmatter.get("status") != "pending-classification"]
    lines.append(f"## Referencias activas ({len(active_refs)})\n")
    if active_refs:
        for n in sorted(active_refs, key=lambda x: str(x.frontmatter.get("title") or "")):
            lines.append(_render(vault_path, n))
    else:
        lines.append("_Sin referencias activas._")
    lines.append("")

    # --- Tareas por estado ---
    task_statuses = ["pending", "in-progress", "done"]
    lines.append("## Tareas\n")
    has_tasks = False
    for st in task_statuses:
        group = [n for n in tasks if str(n.frontmatter.get("status", "")).lower() == st]
        if group:
            has_tasks = True
            lines.append(f"### {st.capitalize()} ({len(group)})\n")
            for n in sorted(group, key=_priority_key):
                priority = n.frontmatter.get("priority") or ""
                due = n.frontmatter.get("due_date") or ""
                extra = " | ".join(x for x in [priority, str(due)] if x)
                lines.append(_render(vault_path, n, extra))
            lines.append("")
    if not has_tasks:
        lines.append("_Sin tareas._\n")

    # --- Ideas por estado ---
    idea_statuses = ["raw", "implemented", "discarded"]
    lines.append("## Ideas\n")
    has_ideas = False
    for st in idea_statuses:
        group = [n for n in ideas if str(n.frontmatter.get("status", "")).lower() == st]
        if group:
            has_ideas = True
            lines.append(f"### {st.capitalize()} ({len(group)})\n")
            for n in sorted(group, key=lambda x: str(x.frontmatter.get("title") or "")):
                lines.append(_render(vault_path, n))
            lines.append("")
    if not has_ideas:
        lines.append("_Sin ideas._\n")

    # --- Papers sin leer ---
    lines.append(f"## Papers sin leer ({len(papers_unread)})\n")
    if papers_unread:
        for n in sorted(papers_unread, key=_priority_key):
            authors = _normalize_authors(n.frontmatter.get("authors"))
            year = n.frontmatter.get("year") or ""
            extra = ", ".join(str(a) for a in authors[:2])
            if year:
                extra = f"{extra} ({year})" if extra else str(year)
            lines.append(_render(vault_path, n, extra))
    else:
        lines.append("_Sin papers pendientes._")
    lines.append("")

    # --- Última actividad ---
    if last_modified:
        lines.append(f"## Última actividad\n\n{last_modified.strftime('%Y-%m-%d %H:%M')}\n")

    content = "\n".join(lines)
    return content.encode("utf-8")


# ---------------------------------------------------------------------------
# Reporte de ideas
# ---------------------------------------------------------------------------


async def ideas_report(
    vault_path: Path,
    project: Optional[str] = None,
    area: Optional[str] = None,
    full: bool = False,
) -> bytes:
    """Genera un reporte de todas las ideas, opcionalmente filtradas por proyecto/área.

    Agrupa por status: raw / implemented / discarded.

    Args:
        vault_path: Raíz del vault.
        project: Filtrar por proyecto (o None para todo el vault).
        area: Filtrar por área (o None).
        full: True para incluir el cuerpo completo de cada nota.

    Returns:
        Bytes del .md generado.
    """
    exclude = ["05-Archive", ".obsidian", ".trash"]
    all_notes = await scan_notes(vault_path, exclude_dirs=exclude, filters={"type": "idea"})

    # Filtrar por scope
    if project:
        all_notes = [n for n in all_notes if str(n.frontmatter.get("project", "")).lower() == project.lower()]
    elif area:
        all_notes = [n for n in all_notes if str(n.frontmatter.get("area", "")).lower() == area.lower()]

    # Scope label
    if project:
        scope_label = f"Proyecto: {project}"
    elif area:
        scope_label = f"Área: {area}"
    else:
        scope_label = "Vault completo"

    title = f"Reporte de ideas — {scope_label}"

    idea_statuses = ["raw", "implemented", "discarded", "pending-classification"]

    # Síntesis LLM
    summary_parts = [f"Ideas en {scope_label}: {len(all_notes)} total"]
    for st in idea_statuses:
        count = sum(1 for n in all_notes if str(n.frontmatter.get("status", "")).lower() == st)
        if count:
            summary_parts.append(f"  {st}: {count}")
    synthesis = await _llm_synthesis("\n".join(summary_parts))

    _render = _note_block if full else _note_line
    lines: list[str] = [_report_header(title, full=full)]

    if synthesis:
        lines.append(f"> {synthesis}\n")

    lines.append(f"**Total:** {len(all_notes)} ideas\n")

    for st in idea_statuses:
        group = [n for n in all_notes if str(n.frontmatter.get("status", "")).lower() == st]
        if not group:
            continue
        lines.append(f"## {st.capitalize()} ({len(group)})\n")
        for n in sorted(group, key=lambda x: str(x.frontmatter.get("title") or "")):
            proj = n.frontmatter.get("project") or ""
            ar = n.frontmatter.get("area") or ""
            loc = proj or ar
            extra = f"_{loc}_" if loc else ""
            lines.append(_render(vault_path, n, extra))
        lines.append("")

    # Ideas sin status conocido
    known = set(idea_statuses)
    orphan = [
        n for n in all_notes
        if str(n.frontmatter.get("status", "")).lower() not in known
    ]
    if orphan:
        lines.append(f"## Sin status ({len(orphan)})\n")
        for n in orphan:
            lines.append(_render(vault_path, n))
        lines.append("")

    content = "\n".join(lines)
    return content.encode("utf-8")


# ---------------------------------------------------------------------------
# Reporte de salud del vault
# ---------------------------------------------------------------------------


async def health_report(vault_path: Path, stale_days: int = 30, full: bool = False) -> bytes:
    """Genera un reporte de salud del vault.

    Detecta:
    - Proyectos/áreas sin actividad en N días.
    - Tareas vencidas (type:task + status:pending/in-progress + due_date < hoy).
    - Ideas raw por proyecto/área (visibilidad, sin alarma).
    - Inbox acumulado (notas en 00-Inbox con status:pending-classification + antigüedad).

    Args:
        vault_path: Raíz del vault.
        stale_days: Umbral de inactividad en días (default 30).
        full: True para incluir el cuerpo completo de cada nota.

    Returns:
        Bytes del .md generado.
    """
    exclude = ["05-Archive", ".obsidian", ".trash"]
    today = date.today()
    today_dt = datetime(today.year, today.month, today.day)

    all_notes = await scan_notes(vault_path, exclude_dirs=exclude)

    # --- Tareas vencidas ---
    overdue: list[NoteData] = []
    for note in all_notes:
        fm = note.frontmatter
        if fm.get("type") != "task":
            continue
        status = str(fm.get("status", "")).lower()
        if status not in ("pending", "in-progress"):
            continue
        due_dt = _parse_fm_date(fm.get("due_date"))
        if due_dt is None:
            continue
        # Normalizar a naive para comparar
        if due_dt.tzinfo is not None:
            due_dt = due_dt.replace(tzinfo=None)
        if due_dt < today_dt:
            overdue.append(note)

    # --- Inbox acumulado ---
    inbox_pending: list[tuple[NoteData, Optional[int]]] = []  # (note, days_old)
    inbox_notes = await scan_notes(
        vault_path,
        scope="00-Inbox",
        exclude_dirs=exclude,
        filters={"status": "pending-classification"},
    )
    for note in inbox_notes:
        created_dt = _parse_fm_date(note.frontmatter.get("date_created"))
        days_old: Optional[int] = None
        if created_dt:
            if created_dt.tzinfo is not None:
                created_dt = created_dt.replace(tzinfo=None)
            days_old = (today_dt - created_dt).days
        inbox_pending.append((note, days_old))

    # --- Proyectos y áreas sin actividad ---
    # Agrupar última actividad por proyecto/área
    project_activity: dict[str, datetime] = {}
    area_activity: dict[str, datetime] = {}

    for note in all_notes:
        fm = note.frontmatter
        dt = _parse_fm_date(fm.get("date_modified"))
        if dt is None:
            dt = _parse_fm_date(fm.get("date_created"))
        if dt is None:
            continue
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)

        proj = str(fm.get("project") or "")
        if proj:
            if proj not in project_activity or dt > project_activity[proj]:
                project_activity[proj] = dt
        ar = str(fm.get("area") or "")
        if ar:
            if ar not in area_activity or dt > area_activity[ar]:
                area_activity[ar] = dt

    stale_projects: list[tuple[str, int]] = []  # (name, days_inactive)
    for proj, last_dt in project_activity.items():
        days = (today_dt - last_dt).days
        if days >= stale_days:
            stale_projects.append((proj, days))
    stale_projects.sort(key=lambda x: -x[1])

    stale_areas: list[tuple[str, int]] = []
    for ar, last_dt in area_activity.items():
        days = (today_dt - last_dt).days
        if days >= stale_days:
            stale_areas.append((ar, days))
    stale_areas.sort(key=lambda x: -x[1])

    # --- Ideas raw por proyecto/área ---
    raw_ideas_by_scope: dict[str, list[NoteData]] = {}
    for note in all_notes:
        fm = note.frontmatter
        if fm.get("type") != "idea":
            continue
        if str(fm.get("status", "")).lower() != "raw":
            continue
        # str(): una nota editada a mano puede traer `project: 2024` (int) y
        # `sorted()` sobre keys mixtas str/int lanza TypeError.
        scope_key = str(fm.get("project") or fm.get("area") or "Sin scope")
        raw_ideas_by_scope.setdefault(scope_key, []).append(note)

    # Síntesis LLM
    summary_parts = [
        f"Salud del vault — umbral de inactividad: {stale_days} días",
        f"Tareas vencidas: {len(overdue)}",
        f"Notas en inbox pendientes: {len(inbox_pending)}",
        f"Proyectos sin actividad: {len(stale_projects)}",
        f"Áreas sin actividad: {len(stale_areas)}",
        f"Scopes con ideas raw: {len(raw_ideas_by_scope)}",
    ]
    synthesis = await _llm_synthesis("\n".join(summary_parts))

    _render = _note_block if full else _note_line
    title = f"Salud del vault (umbral: {stale_days} días)"
    lines: list[str] = [_report_header(title, full=full)]

    if synthesis:
        lines.append(f"> {synthesis}\n")

    # --- Tareas vencidas ---
    lines.append(f"## Tareas vencidas ({len(overdue)})\n")
    if overdue:
        for n in sorted(overdue, key=_priority_key):
            fm = n.frontmatter
            due_str = str(fm.get("due_date") or "")
            priority = fm.get("priority") or ""
            extra = " | ".join(x for x in [due_str, priority] if x)
            lines.append(_render(vault_path, n, extra))
    else:
        lines.append("_Sin tareas vencidas._")
    lines.append("")

    # --- Inbox acumulado ---
    lines.append(f"## Inbox acumulado ({len(inbox_pending)} notas pendientes)\n")
    if inbox_pending:
        # Ordenar por antigüedad descendente
        inbox_pending_sorted = sorted(inbox_pending, key=lambda x: -(x[1] or 0))
        for note, days_old in inbox_pending_sorted:
            age_str = f"{days_old}d" if days_old is not None else "?"
            lines.append(_render(vault_path, note, f"en inbox hace {age_str}"))
    else:
        lines.append("_Inbox sin acumulación pendiente._")
    lines.append("")

    # --- Proyectos sin actividad ---
    lines.append(f"## Proyectos sin actividad ≥ {stale_days} días ({len(stale_projects)})\n")
    if stale_projects:
        for proj, days in stale_projects:
            lines.append(f"- **{proj}** — sin actividad hace {days} días")
    else:
        lines.append("_Todos los proyectos tienen actividad reciente._")
    lines.append("")

    # --- Áreas sin actividad ---
    lines.append(f"## Áreas sin actividad ≥ {stale_days} días ({len(stale_areas)})\n")
    if stale_areas:
        for ar, days in stale_areas:
            lines.append(f"- **{ar}** — sin actividad hace {days} días")
    else:
        lines.append("_Todas las áreas tienen actividad reciente._")
    lines.append("")

    # --- Ideas raw por scope ---
    total_raw = sum(len(v) for v in raw_ideas_by_scope.values())
    lines.append(f"## Ideas raw por scope ({total_raw} total)\n")
    if raw_ideas_by_scope:
        for scope_key in sorted(raw_ideas_by_scope):
            group = raw_ideas_by_scope[scope_key]
            lines.append(f"### {scope_key} ({len(group)})\n")
            for n in sorted(group, key=lambda x: str(x.frontmatter.get("title") or "")):
                lines.append(_render(vault_path, n))
            lines.append("")
    else:
        lines.append("_Sin ideas raw en el vault._\n")

    content = "\n".join(lines)
    return content.encode("utf-8")


# ---------------------------------------------------------------------------
# Cola de lectura
# ---------------------------------------------------------------------------


async def reading_queue(
    vault_path: Path,
    project: Optional[str] = None,
    area: Optional[str] = None,
    full: bool = False,
) -> bytes:
    """Genera un reporte de la cola de lectura (papers con read_status: unread).

    Ordena por prioridad (high > medium > low > null) y agrupa por proyecto/área.

    Args:
        vault_path: Raíz del vault.
        project: Filtrar por proyecto (o None para todo el vault).
        area: Filtrar por área (o None).
        full: True para incluir el cuerpo completo de cada nota.

    Returns:
        Bytes del .md generado.
    """
    exclude = ["05-Archive", ".obsidian", ".trash"]
    all_notes = await scan_notes(
        vault_path,
        exclude_dirs=exclude,
        filters={"read_status": "unread"},
    )

    # Filtrar por scope
    if project:
        all_notes = [n for n in all_notes if str(n.frontmatter.get("project", "")).lower() == project.lower()]
    elif area:
        all_notes = [n for n in all_notes if str(n.frontmatter.get("area", "")).lower() == area.lower()]

    # Scope label
    if project:
        scope_label = f"Proyecto: {project}"
    elif area:
        scope_label = f"Área: {area}"
    else:
        scope_label = "Vault completo"

    title = f"Cola de lectura — {scope_label}"

    # Síntesis LLM
    high = sum(1 for n in all_notes if str(n.frontmatter.get("priority", "")).lower() == "high")
    medium = sum(1 for n in all_notes if str(n.frontmatter.get("priority", "")).lower() == "medium")
    low = sum(1 for n in all_notes if str(n.frontmatter.get("priority", "")).lower() not in ("high", "medium"))
    summary_parts = [
        f"Cola de lectura en {scope_label}: {len(all_notes)} papers sin leer",
        f"  high: {high}, medium: {medium}, low/sin prioridad: {low}",
    ]
    synthesis = await _llm_synthesis("\n".join(summary_parts))

    _render = _note_block if full else _note_line
    lines: list[str] = [_report_header(title, full=full)]

    if synthesis:
        lines.append(f"> {synthesis}\n")

    lines.append(f"**Total:** {len(all_notes)} papers sin leer\n")

    if not all_notes:
        lines.append("_La cola de lectura está vacía._\n")
        return "\n".join(lines).encode("utf-8")

    # Agrupar por proyecto/área
    groups: dict[str, list[NoteData]] = {}
    for note in all_notes:
        fm = note.frontmatter
        # str() por el mismo motivo que en `health_report`: keys mixtas
        # str/int rompen el `sorted(groups)` de abajo.
        key = str(fm.get("project") or fm.get("area") or "Sin scope")
        groups.setdefault(key, []).append(note)

    for scope_key in sorted(groups):
        group = groups[scope_key]
        # Ordenar por prioridad dentro del grupo
        group_sorted = sorted(group, key=_priority_key)
        lines.append(f"## {scope_key} ({len(group)})\n")
        for n in group_sorted:
            fm = n.frontmatter
            priority = fm.get("priority") or ""
            authors = _normalize_authors(fm.get("authors"))
            year = fm.get("year") or ""
            author_str = ", ".join(str(a) for a in authors[:2])
            if year:
                author_str = f"{author_str} ({year})" if author_str else str(year)
            extra = " | ".join(x for x in [priority, author_str] if x)
            lines.append(_render(vault_path, n, extra))
        lines.append("")

    content = "\n".join(lines)
    return content.encode("utf-8")
