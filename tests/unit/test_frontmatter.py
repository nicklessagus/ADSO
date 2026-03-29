"""Tests de generación y validación de frontmatter YAML."""

from __future__ import annotations

import pytest
from datetime import datetime
from pathlib import Path

import frontmatter as fm_lib

from adso.vault_writer import create_note, read_note, _clean_frontmatter


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Vault temporal con estructura PARA."""
    for d in ["00-Inbox", "01-Projects", "02-Areas", "03-Resources", "05-Archive"]:
        (tmp_path / d).mkdir()
    return tmp_path


class TestFrontmatterGeneration:

    @pytest.mark.asyncio
    async def test_note_has_base_fields(self, vault: Path) -> None:
        """Nota tiene todos los campos base requeridos."""
        fm = {
            "title": "Test note",
            "type": "reference",
            "tags": ["test"],
            "status": "active",
            "project": "tesis",
        }
        path = await create_note(fm, "Body", vault)
        note = await read_note(path)

        assert note.frontmatter["title"] == "Test note"
        assert note.frontmatter["type"] == "reference"
        assert "date_created" in note.frontmatter
        assert "date_modified" in note.frontmatter
        assert note.frontmatter["tags"] == ["test"]
        assert note.frontmatter["source"] == "telegram"
        assert note.frontmatter["media_type"] == "text"
        assert note.frontmatter["status"] == "active"

    @pytest.mark.asyncio
    async def test_dates_are_iso_8601(self, vault: Path) -> None:
        fm = {"title": "ISO test", "type": "reference", "project": "tesis"}
        path = await create_note(fm, "Body", vault)
        note = await read_note(path)

        # Verificar que son parseables como ISO 8601
        # python-frontmatter parsea fechas automáticamente a objetos datetime
        for field in ("date_created", "date_modified"):
            val = note.frontmatter[field]
            if isinstance(val, str):
                datetime.fromisoformat(val)
            # Si ya es datetime, python-frontmatter lo parseó correctamente

    @pytest.mark.asyncio
    async def test_each_type_creates_valid_frontmatter(self, vault: Path) -> None:
        """Cada tipo genera frontmatter válido."""
        cases = [
            {"title": "Nota", "type": "reference", "project": "tesis"},
            {"title": "Tarea", "type": "task", "status": "pending", "area": "investigacion"},
            {"title": "Idea", "type": "idea", "status": "raw", "area": "investigacion"},
            {"title": "Inbox", "type": "idea", "status": "pending-classification"},
        ]
        for fm_input in cases:
            path = await create_note(fm_input, "Body", vault)
            note = await read_note(path)
            assert note.frontmatter["type"] == fm_input["type"]

    @pytest.mark.asyncio
    async def test_project_index_frontmatter(self, vault: Path) -> None:
        fm = {
            "title": "Tesis",
            "type": "project-index",
            "status": "active",
            "description": "Mi tesis",
            "source": "system",
            "project": "tesis",
        }
        path = await create_note(fm, "# Tesis", vault)
        note = await read_note(path)
        assert note.frontmatter["type"] == "project-index"
        assert note.frontmatter["source"] == "system"
        assert note.frontmatter["description"] == "Mi tesis"

    @pytest.mark.asyncio
    async def test_area_index_frontmatter(self, vault: Path) -> None:
        fm = {
            "title": "Docencia",
            "type": "area-index",
            "description": "Clases",
            "source": "system",
            "area": "docencia",
        }
        path = await create_note(fm, "# Docencia", vault)
        note = await read_note(path)
        assert note.frontmatter["type"] == "area-index"

    @pytest.mark.asyncio
    async def test_special_chars_in_title(self, vault: Path) -> None:
        """Caracteres especiales en title no corrompen YAML."""
        titles = [
            'Part 1: "Introduction"',
            "Nota con: dos puntos",
            "Unicode: café, niño, über",
            "Comillas 'simples' y \"dobles\"",
        ]
        for title in titles:
            fm = {"title": title, "type": "idea"}
            path = await create_note(fm, "Body", vault)
            note = await read_note(path)
            assert note.frontmatter["title"] == title

    @pytest.mark.asyncio
    async def test_yaml_is_parseable(self, vault: Path) -> None:
        """El YAML generado es parseable por python-frontmatter."""
        fm = {
            "title": "YAML test",
            "type": "reference",
            "tags": ["tag-1", "tag-2"],
            "project": "tesis",
        }
        path = await create_note(fm, "Body", vault)
        raw = path.read_text(encoding="utf-8")
        # Verificar que empieza con ---
        assert raw.startswith("---")
        # Verificar que es parseable
        post = fm_lib.loads(raw)
        assert post.metadata["title"] == "YAML test"

    @pytest.mark.asyncio
    async def test_source_telegram_vs_system(self, vault: Path) -> None:
        # Default: telegram
        path1 = await create_note(
            {"title": "User", "type": "idea"}, "Body", vault
        )
        n1 = await read_note(path1)
        assert n1.frontmatter["source"] == "telegram"

        # Explicit: system
        path2 = await create_note(
            {"title": "System", "type": "project-index", "source": "system", "project": "x"},
            "Body", vault,
        )
        n2 = await read_note(path2)
        assert n2.frontmatter["source"] == "system"


class TestCleanFrontmatter:

    def test_removes_none_values(self) -> None:
        fm = {"title": "Test", "type": "reference", "project": None, "area": None}
        clean = _clean_frontmatter(fm)
        assert "project" not in clean
        assert "area" not in clean
        assert clean["title"] == "Test"

    def test_keeps_empty_lists(self) -> None:
        fm = {"title": "Test", "tags": []}
        clean = _clean_frontmatter(fm)
        assert clean["tags"] == []
