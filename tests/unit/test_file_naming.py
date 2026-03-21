"""Tests de generación de nombres de archivo."""

from __future__ import annotations

import pytest
from pathlib import Path

from adso.vault_writer import _make_filename, create_note


class TestMakeFilename:

    def test_basic_format(self) -> None:
        """Formato YYYY-MM-DD-slug.md."""
        name = _make_filename("Mi primer nota", date="2025-01-15T14:30:00")
        assert name == "2025-01-15-mi-primer-nota.md"

    def test_special_chars_removed(self) -> None:
        name = _make_filename("Nota: con (caracteres) #especiales!", date="2025-01-15")
        assert "#" not in name
        assert ":" not in name
        assert "!" not in name
        assert name.endswith(".md")

    def test_long_title_truncated(self) -> None:
        """Slug truncado a MAX_SLUG_LENGTH."""
        long_title = "a" * 200
        name = _make_filename(long_title, date="2025-01-15")
        slug_part = name[len("2025-01-15-"):-len(".md")]
        assert len(slug_part) <= 60

    def test_accents_transliterated(self) -> None:
        name = _make_filename("Café con leche y niño", date="2025-01-15")
        assert "cafe" in name
        assert "nino" in name

    def test_empty_title_fallback(self) -> None:
        """Título vacío → fallback a 'nota'."""
        name = _make_filename("", date="2025-01-15")
        assert "nota" in name
        assert name.endswith(".md")

    def test_whitespace_only_fallback(self) -> None:
        name = _make_filename("   ", date="2025-01-15")
        assert "nota" in name

    def test_no_date_uses_today(self) -> None:
        """Sin fecha → usa fecha de hoy."""
        name = _make_filename("Test")
        # Debe tener formato YYYY-MM-DD-
        parts = name.split("-")
        assert len(parts[0]) == 4  # year
        assert len(parts[1]) == 2  # month
        assert len(parts[2]) == 2  # day

    def test_kebab_case(self) -> None:
        name = _make_filename("Mi Nota Con Mayúsculas", date="2025-01-15")
        # El slug debe ser lowercase con guiones
        slug = name[len("2025-01-15-"):-len(".md")]
        assert slug == slug.lower()
        assert " " not in slug


class TestFileCollision:

    @pytest.fixture
    def vault(self, tmp_path: Path) -> Path:
        for d in ["00-Inbox", "01-Projects", "02-Areas", "03-Resources", "05-Archive"]:
            (tmp_path / d).mkdir()
        return tmp_path

    @pytest.mark.asyncio
    async def test_collision_adds_suffix(self, vault: Path) -> None:
        """Si el archivo existe, agrega -2, -3, etc."""
        fm = {"title": "Duplicada", "type": "inbox"}

        path1 = await create_note(fm, "Body 1", vault)
        path2 = await create_note(fm, "Body 2", vault)
        path3 = await create_note(fm, "Body 3", vault)

        assert path1.exists()
        assert path2.exists()
        assert path3.exists()
        assert path1 != path2 != path3

        # Verificar que los sufijos son -2, -3
        assert "-2" in path2.stem
        assert "-3" in path3.stem
