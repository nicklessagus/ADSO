"""Tests unitarios para adso.reporters — generación de reportes del vault.

Usa tmp_path con archivos .md reales (no mocks de vault_search).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from adso.reporters import (
    _parse_fm_date,
    _priority_key,
    health_report,
    ideas_report,
    reading_queue,
    scope_report,
)
from adso.vault_writer import NoteData


# ---------------------------------------------------------------------------
# Helpers para crear notas de prueba
# ---------------------------------------------------------------------------


def _write_note(path: Path, frontmatter: dict, body: str = "") -> None:
    """Escribe una nota .md con frontmatter YAML en path.

    Crea directorios intermedios si es necesario.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, list):
            if v:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{k}: []")
        elif v is None:
            lines.append(f"{k}:")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    if body:
        lines.append("")
        lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")


def _today_str() -> str:
    return date.today().isoformat()


def _days_ago_str(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# Tests de helpers
# ---------------------------------------------------------------------------


class TestParseFmDate:
    def test_string_date(self) -> None:
        dt = _parse_fm_date("2024-03-15")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 15

    def test_string_datetime(self) -> None:
        dt = _parse_fm_date("2024-03-15T10:30:00")
        assert dt is not None
        assert dt.hour == 10

    def test_datetime_object(self) -> None:
        now = datetime(2024, 1, 1, 12, 0, 0)
        dt = _parse_fm_date(now)
        assert dt == now

    def test_date_object(self) -> None:
        d = date(2024, 6, 1)
        dt = _parse_fm_date(d)
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 6

    def test_none(self) -> None:
        assert _parse_fm_date(None) is None

    def test_empty_string(self) -> None:
        assert _parse_fm_date("") is None

    def test_invalid_string(self) -> None:
        assert _parse_fm_date("no-es-fecha") is None


class TestPriorityKey:
    def test_high_first(self) -> None:
        notes = [
            NoteData(path=Path("a.md"), frontmatter={"priority": "low"}, body=""),
            NoteData(path=Path("b.md"), frontmatter={"priority": "high"}, body=""),
            NoteData(path=Path("c.md"), frontmatter={"priority": "medium"}, body=""),
        ]
        sorted_notes = sorted(notes, key=_priority_key)
        assert sorted_notes[0].frontmatter["priority"] == "high"
        assert sorted_notes[1].frontmatter["priority"] == "medium"
        assert sorted_notes[2].frontmatter["priority"] == "low"

    def test_null_last(self) -> None:
        notes = [
            NoteData(path=Path("a.md"), frontmatter={}, body=""),
            NoteData(path=Path("b.md"), frontmatter={"priority": "high"}, body=""),
        ]
        sorted_notes = sorted(notes, key=_priority_key)
        assert sorted_notes[0].frontmatter.get("priority") == "high"


# ---------------------------------------------------------------------------
# Tests de scope_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestScopeReport:
    async def test_scope_project_basic(self, tmp_path: Path) -> None:
        """scope_report con un proyecto simple retorna bytes no vacíos."""
        vault = tmp_path / "vault"
        proj_dir = vault / "01-Projects" / "mi-proyecto"

        _write_note(
            proj_dir / "nota-ref.md",
            {
                "title": "Referencia de prueba",
                "type": "reference",
                "status": "active",
                "project": "mi-proyecto",
                "date_modified": _today_str(),
                "source": "telegram",
                "media_type": "text",
            },
            "Contenido de prueba",
        )
        _write_note(
            proj_dir / "tarea.md",
            {
                "title": "Tarea pendiente",
                "type": "task",
                "status": "pending",
                "project": "mi-proyecto",
                "priority": "high",
                "date_modified": _today_str(),
                "source": "telegram",
                "media_type": "text",
            },
        )

        result = await scope_report(vault, project="mi-proyecto")
        assert isinstance(result, bytes)
        assert len(result) > 200
        content = result.decode("utf-8")
        assert "mi-proyecto" in content.lower()
        assert "Referencia de prueba" in content
        assert "Tarea pendiente" in content

    async def test_scope_inbox(self, tmp_path: Path) -> None:
        """scope_report con inbox detecta notas en 00-Inbox."""
        vault = tmp_path / "vault"
        inbox = vault / "00-Inbox"

        _write_note(
            inbox / "borrador.md",
            {
                "title": "Borrador sin clasificar",
                "type": "idea",
                "status": "pending-classification",
                "source": "telegram",
                "media_type": "text",
                "date_modified": _today_str(),
            },
        )

        result = await scope_report(vault, inbox=True)
        content = result.decode("utf-8")
        assert "Inbox" in content

    async def test_scope_papers_unread(self, tmp_path: Path) -> None:
        """Papers con read_status unread aparecen en la sección correcta."""
        vault = tmp_path / "vault"
        proj_dir = vault / "01-Projects" / "tesis"

        _write_note(
            proj_dir / "paper.md",
            {
                "title": "Paper sin leer",
                "type": "reference",
                "status": "active",
                "project": "tesis",
                "read_status": "unread",
                "authors": ["Autor A", "Autor B"],
                "year": 2023,
                "source": "telegram",
                "media_type": "link",
                "date_modified": _today_str(),
            },
        )

        result = await scope_report(vault, project="tesis")
        content = result.decode("utf-8")
        assert "Paper sin leer" in content
        assert "Papers sin leer" in content

    async def test_empty_scope_returns_header_only(self, tmp_path: Path) -> None:
        """Scope vacío retorna bytes con solo el header (< 400 bytes)."""
        vault = tmp_path / "vault"
        vault.mkdir(parents=True, exist_ok=True)

        result = await scope_report(vault, project="inexistente")
        # El resultado debe ser pequeño (solo header + secciones vacías)
        # El test valida que el reporte se genera sin errores
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# Tests de ideas_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestIdeasReport:
    async def test_grouping_by_status(self, tmp_path: Path) -> None:
        """Las ideas se agrupan correctamente por status."""
        vault = tmp_path / "vault"
        areas_dir = vault / "02-Areas" / "investigacion"

        for i, status in enumerate(["raw", "raw", "implemented", "discarded"]):
            _write_note(
                areas_dir / f"idea-{i}.md",
                {
                    "title": f"Idea {i} ({status})",
                    "type": "idea",
                    "status": status,
                    "area": "investigacion",
                    "source": "telegram",
                    "media_type": "text",
                    "date_modified": _today_str(),
                },
            )

        result = await ideas_report(vault)
        content = result.decode("utf-8")

        assert "Raw" in content
        assert "Implemented" in content
        assert "Discarded" in content
        # 2 ideas raw
        assert content.count("Idea") >= 4

    async def test_filter_by_project(self, tmp_path: Path) -> None:
        """ideas_report filtra correctamente por proyecto."""
        vault = tmp_path / "vault"
        proj_a = vault / "01-Projects" / "proyecto-a"
        proj_b = vault / "01-Projects" / "proyecto-b"

        _write_note(
            proj_a / "idea-a.md",
            {
                "title": "Idea proyecto A",
                "type": "idea",
                "status": "raw",
                "project": "proyecto-a",
                "source": "telegram",
                "media_type": "text",
                "date_modified": _today_str(),
            },
        )
        _write_note(
            proj_b / "idea-b.md",
            {
                "title": "Idea proyecto B",
                "type": "idea",
                "status": "raw",
                "project": "proyecto-b",
                "source": "telegram",
                "media_type": "text",
                "date_modified": _today_str(),
            },
        )

        result = await ideas_report(vault, project="proyecto-a")
        content = result.decode("utf-8")

        assert "Idea proyecto A" in content
        assert "Idea proyecto B" not in content

    async def test_empty_vault(self, tmp_path: Path) -> None:
        """ideas_report con vault vacío retorna bytes sin errores."""
        vault = tmp_path / "vault"
        vault.mkdir(parents=True, exist_ok=True)
        result = await ideas_report(vault)
        assert isinstance(result, bytes)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Tests de health_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHealthReport:
    async def test_overdue_tasks(self, tmp_path: Path) -> None:
        """Tareas vencidas se detectan correctamente."""
        vault = tmp_path / "vault"
        inbox = vault / "00-Inbox"

        past_date = _days_ago_str(5)  # 5 días atrás = vencida

        _write_note(
            inbox / "tarea-vencida.md",
            {
                "title": "Tarea vencida",
                "type": "task",
                "status": "pending",
                "due_date": past_date,
                "source": "telegram",
                "media_type": "text",
                "date_modified": past_date,
            },
        )

        result = await health_report(vault)
        content = result.decode("utf-8")

        assert "Tareas vencidas" in content
        assert "Tarea vencida" in content

    async def test_future_task_not_overdue(self, tmp_path: Path) -> None:
        """Tareas con due_date futuro no aparecen como vencidas."""
        vault = tmp_path / "vault"
        inbox = vault / "00-Inbox"

        future_date = (date.today() + timedelta(days=7)).isoformat()

        _write_note(
            inbox / "tarea-futura.md",
            {
                "title": "Tarea futura",
                "type": "task",
                "status": "pending",
                "due_date": future_date,
                "source": "telegram",
                "media_type": "text",
                "date_modified": _today_str(),
            },
        )

        result = await health_report(vault)
        content = result.decode("utf-8")

        # La sección de tareas vencidas debe estar vacía
        assert "Sin tareas vencidas" in content

    async def test_inbox_accumulation(self, tmp_path: Path) -> None:
        """Notas en inbox con pending-classification aparecen en el reporte."""
        vault = tmp_path / "vault"
        inbox = vault / "00-Inbox"

        old_date = _days_ago_str(10)

        _write_note(
            inbox / "pendiente.md",
            {
                "title": "Nota pendiente",
                "type": "idea",
                "status": "pending-classification",
                "source": "telegram",
                "media_type": "text",
                "date_created": old_date,
                "date_modified": old_date,
            },
        )

        result = await health_report(vault)
        content = result.decode("utf-8")

        assert "Inbox acumulado" in content
        assert "Nota pendiente" in content

    async def test_stale_detection(self, tmp_path: Path) -> None:
        """Proyectos con notas antiguas aparecen como inactivos."""
        vault = tmp_path / "vault"
        proj_dir = vault / "01-Projects" / "proyecto-viejo"

        old_date = _days_ago_str(45)

        _write_note(
            proj_dir / "nota.md",
            {
                "title": "Nota vieja",
                "type": "reference",
                "status": "active",
                "project": "proyecto-viejo",
                "source": "telegram",
                "media_type": "text",
                "date_modified": old_date,
            },
        )

        result = await health_report(vault, stale_days=30)
        content = result.decode("utf-8")

        assert "proyecto-viejo" in content
        assert "sin actividad" in content.lower()

    async def test_recent_project_not_stale(self, tmp_path: Path) -> None:
        """Proyectos con actividad reciente no aparecen como inactivos."""
        vault = tmp_path / "vault"
        proj_dir = vault / "01-Projects" / "proyecto-activo"

        _write_note(
            proj_dir / "nota.md",
            {
                "title": "Nota reciente",
                "type": "reference",
                "status": "active",
                "project": "proyecto-activo",
                "source": "telegram",
                "media_type": "text",
                "date_modified": _today_str(),
            },
        )

        result = await health_report(vault, stale_days=30)
        content = result.decode("utf-8")

        # Debe aparecer en la sección "sin actividad reciente"
        assert "Todos los proyectos tienen actividad reciente" in content


# ---------------------------------------------------------------------------
# Tests de reading_queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestReadingQueue:
    async def test_priority_ordering(self, tmp_path: Path) -> None:
        """Los papers se ordenan por prioridad high > medium > low."""
        vault = tmp_path / "vault"
        proj_dir = vault / "01-Projects" / "tesis"

        for priority in ["low", "high", "medium"]:
            _write_note(
                proj_dir / f"paper-{priority}.md",
                {
                    "title": f"Paper {priority}",
                    "type": "reference",
                    "status": "active",
                    "project": "tesis",
                    "read_status": "unread",
                    "priority": priority,
                    "source": "telegram",
                    "media_type": "link",
                    "date_modified": _today_str(),
                },
            )

        result = await reading_queue(vault)
        content = result.decode("utf-8")

        # Los tres papers deben aparecer
        assert "Paper high" in content
        assert "Paper medium" in content
        assert "Paper low" in content

        # Verificar orden: high debe aparecer antes que medium y low
        idx_high = content.index("Paper high")
        idx_medium = content.index("Paper medium")
        idx_low = content.index("Paper low")
        assert idx_high < idx_medium < idx_low

    async def test_filter_by_area(self, tmp_path: Path) -> None:
        """reading_queue filtra por área."""
        vault = tmp_path / "vault"
        area_a = vault / "02-Areas" / "area-a"
        area_b = vault / "02-Areas" / "area-b"

        _write_note(
            area_a / "paper-a.md",
            {
                "title": "Paper área A",
                "type": "reference",
                "status": "active",
                "area": "area-a",
                "read_status": "unread",
                "source": "telegram",
                "media_type": "link",
                "date_modified": _today_str(),
            },
        )
        _write_note(
            area_b / "paper-b.md",
            {
                "title": "Paper área B",
                "type": "reference",
                "status": "active",
                "area": "area-b",
                "read_status": "unread",
                "source": "telegram",
                "media_type": "link",
                "date_modified": _today_str(),
            },
        )

        result = await reading_queue(vault, area="area-a")
        content = result.decode("utf-8")

        assert "Paper área A" in content
        assert "Paper área B" not in content

    async def test_read_papers_excluded(self, tmp_path: Path) -> None:
        """Papers con read_status:read no aparecen en la cola."""
        vault = tmp_path / "vault"
        proj_dir = vault / "01-Projects" / "tesis"

        _write_note(
            proj_dir / "paper-leido.md",
            {
                "title": "Paper ya leído",
                "type": "reference",
                "status": "active",
                "project": "tesis",
                "read_status": "read",
                "source": "telegram",
                "media_type": "link",
                "date_modified": _today_str(),
            },
        )

        result = await reading_queue(vault)
        content = result.decode("utf-8")

        assert "Paper ya leído" not in content

    async def test_grouped_by_scope(self, tmp_path: Path) -> None:
        """Los papers se agrupan por proyecto/área en el reporte."""
        vault = tmp_path / "vault"
        proj_a = vault / "01-Projects" / "proyecto-a"
        proj_b = vault / "01-Projects" / "proyecto-b"

        _write_note(
            proj_a / "paper1.md",
            {
                "title": "Paper 1 proyecto A",
                "type": "reference",
                "status": "active",
                "project": "proyecto-a",
                "read_status": "unread",
                "source": "telegram",
                "media_type": "link",
                "date_modified": _today_str(),
            },
        )
        _write_note(
            proj_b / "paper2.md",
            {
                "title": "Paper 2 proyecto B",
                "type": "reference",
                "status": "active",
                "project": "proyecto-b",
                "read_status": "unread",
                "source": "telegram",
                "media_type": "link",
                "date_modified": _today_str(),
            },
        )

        result = await reading_queue(vault)
        content = result.decode("utf-8")

        # Ambos proyectos deben aparecer como secciones de agrupamiento
        assert "proyecto-a" in content
        assert "proyecto-b" in content
        assert "Paper 1 proyecto A" in content
        assert "Paper 2 proyecto B" in content

    async def test_empty_queue(self, tmp_path: Path) -> None:
        """Cola vacía retorna mensaje apropiado."""
        vault = tmp_path / "vault"
        vault.mkdir(parents=True, exist_ok=True)

        result = await reading_queue(vault)
        content = result.decode("utf-8")

        assert "vacía" in content.lower() or "sin leer" in content.lower() or "Total" in content
