"""Tests de integración para vault_search contra vault temporal."""

from __future__ import annotations

import pytest
from pathlib import Path

from adso.vault_writer import create_note
from adso.vault_search import (
    get_backlinks,
    search,
    find_by_tag,
    find_by_property,
    get_wikilinks,
    get_all_tags,
    get_note_index,
)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for d in ["00-Inbox", "01-Projects/tesis/experimentos",
              "01-Projects/tesis/papers", "02-Areas/investigacion",
              "02-Areas/docencia", "03-Resources", "05-Archive"]:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


async def _create_notes(vault: Path) -> dict[str, Path]:
    """Crea un set de notas para testing y retorna {name: path}."""
    notes = {}

    notes["nota-a"] = await create_note(
        {"title": "Nota A", "type": "reference", "tags": ["ml", "cnn"],
         "project": "tesis", "section": "experimentos", "status": "active"},
        "Contenido de A. Ver [[nota-b]] y [[nota-c]].",
        vault,
    )

    notes["nota-b"] = await create_note(
        {"title": "Nota B", "type": "reference", "tags": ["ml"],
         "project": "tesis", "section": "papers", "status": "active"},
        "Contenido de B. Referencia a [[nota-a]].",
        vault,
    )

    notes["nota-c"] = await create_note(
        {"title": "Nota C", "type": "reference", "tags": ["paper", "metodo/transformer"],
         "project": "tesis", "status": "active"},
        "Contenido de C. Linkea a [[nota-a|Resultado A]] y al #deep-learning.",
        vault,
    )

    notes["task-1"] = await create_note(
        {"title": "Task pendiente", "type": "task", "tags": ["revision"],
         "area": "investigacion", "project": "tesis",
         "status": "pending", "priority": "high"},
        "## Tarea\n\nRevisar algo.\n\n- [ ] Subtarea inline\n- [x] Subtarea hecha",
        vault,
    )

    notes["idea-1"] = await create_note(
        {"title": "Idea nueva", "type": "idea", "tags": ["mejora"],
         "area": "investigacion", "status": "raw", "priority": "low"},
        "Se me ocurre mejorar el pipeline.",
        vault,
    )

    notes["inbox-1"] = await create_note(
        {"title": "Sin clasificar", "type": "idea",
         "status": "pending-classification"},
        "Algo que no se clasificó.",
        vault,
    )

    return notes


class TestBacklinks:

    @pytest.mark.asyncio
    async def test_backlinks_found(self, vault: Path) -> None:
        await _create_notes(vault)
        # nota-b y nota-c referencian [[nota-a]], no el stem del archivo
        results = await get_backlinks("nota-a", vault, exclude_dirs=[])
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_backlinks_with_alias(self, vault: Path) -> None:
        await _create_notes(vault)
        # nota-c linkea a [[nota-a|Resultado A]]
        results = await get_backlinks("nota-a", vault, exclude_dirs=[])
        # Debe encontrar tanto el link simple como el alias
        paths = {r.path.stem for r in results}
        assert len(paths) == 2

    @pytest.mark.asyncio
    async def test_no_backlinks(self, vault: Path) -> None:
        await _create_notes(vault)
        results = await get_backlinks("nota-inexistente", vault, exclude_dirs=[])
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_vault(self, tmp_path: Path) -> None:
        (tmp_path / "00-Inbox").mkdir()
        results = await get_backlinks("algo", tmp_path, exclude_dirs=[])
        assert results == []


class TestSearch:

    @pytest.mark.asyncio
    async def test_text_search_in_title(self, vault: Path) -> None:
        await _create_notes(vault)
        results = await search("Nota A", vault, exclude_dirs=[])
        assert any("Nota A" in r.title for r in results)

    @pytest.mark.asyncio
    async def test_search_with_type_filter(self, vault: Path) -> None:
        await _create_notes(vault)
        results = await search("type:task", vault, exclude_dirs=[])
        assert all(r.note_type == "task" for r in results)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_with_combined_filters(self, vault: Path) -> None:
        await _create_notes(vault)
        results = await search("type:reference project:tesis", vault, exclude_dirs=[])
        assert len(results) >= 2
        assert all(r.note_type == "reference" for r in results)

    @pytest.mark.asyncio
    async def test_search_with_status_filter(self, vault: Path) -> None:
        await _create_notes(vault)
        results = await search("type:task status:pending", vault, exclude_dirs=[])
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_body_search(self, vault: Path) -> None:
        await _create_notes(vault)
        results = await search("pipeline", vault, exclude_dirs=[])
        assert len(results) >= 1  # "mejorar el pipeline" en la idea


class TestFindByTag:

    @pytest.mark.asyncio
    async def test_find_exact_tag(self, vault: Path) -> None:
        await _create_notes(vault)
        results = await find_by_tag("ml", vault)
        assert len(results) >= 2  # nota-a y nota-b

    @pytest.mark.asyncio
    async def test_hierarchical_tag(self, vault: Path) -> None:
        """metodo matchea metodo/transformer."""
        await _create_notes(vault)
        results = await find_by_tag("metodo", vault, hierarchical=True)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_non_hierarchical_tag(self, vault: Path) -> None:
        """Sin hierarchical, metodo NO matchea metodo/transformer."""
        await _create_notes(vault)
        results = await find_by_tag("metodo", vault, hierarchical=False)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_tag_with_hash(self, vault: Path) -> None:
        """Tag con # se normaliza."""
        await _create_notes(vault)
        results = await find_by_tag("#ml", vault)
        assert len(results) >= 2


class TestFindByProperty:

    @pytest.mark.asyncio
    async def test_find_by_type(self, vault: Path) -> None:
        await _create_notes(vault)
        results = await find_by_property("type", "task", vault)
        assert len(results) >= 1
        assert all(r.note_type == "task" for r in results)

    @pytest.mark.asyncio
    async def test_find_by_project(self, vault: Path) -> None:
        await _create_notes(vault)
        results = await find_by_property("project", "tesis", vault)
        assert len(results) >= 3

    @pytest.mark.asyncio
    async def test_find_any_value(self, vault: Path) -> None:
        """value=None → notas que tienen el campo."""
        await _create_notes(vault)
        results = await find_by_property("priority", None, vault)
        assert len(results) >= 2  # task y idea tienen priority

    @pytest.mark.asyncio
    async def test_case_insensitive(self, vault: Path) -> None:
        await _create_notes(vault)
        results = await find_by_property("type", "Task", vault)
        assert len(results) >= 1


class TestGetWikilinks:

    @pytest.mark.asyncio
    async def test_extract_outgoing_links(self, vault: Path) -> None:
        notes = await _create_notes(vault)
        links = await get_wikilinks(notes["nota-a"])
        assert "nota-b" in links
        assert "nota-c" in links

    @pytest.mark.asyncio
    async def test_no_duplicates(self, vault: Path) -> None:
        # Crear nota con links duplicados
        path = await create_note(
            {"title": "Dupes", "type": "idea"},
            "[[x]] y [[x]] y [[x]]",
            vault,
        )
        links = await get_wikilinks(path)
        assert links == ["x"]


class TestGetAllTags:

    @pytest.mark.asyncio
    async def test_all_tags_with_counts(self, vault: Path) -> None:
        await _create_notes(vault)
        tags = await get_all_tags(vault, exclude_dirs=[])
        assert "ml" in tags
        assert tags["ml"] >= 2

    @pytest.mark.asyncio
    async def test_sorted_by_frequency(self, vault: Path) -> None:
        await _create_notes(vault)
        tags = await get_all_tags(vault, exclude_dirs=[])
        counts = list(tags.values())
        assert counts == sorted(counts, reverse=True)


class TestGetNoteIndex:

    @pytest.mark.asyncio
    async def test_index_contains_all_notes(self, vault: Path) -> None:
        notes = await _create_notes(vault)
        index = await get_note_index(vault)
        for name, path in notes.items():
            assert path.stem in index

    @pytest.mark.asyncio
    async def test_empty_vault(self, tmp_path: Path) -> None:
        (tmp_path / "00-Inbox").mkdir()
        index = await get_note_index(tmp_path)
        assert index == {}
