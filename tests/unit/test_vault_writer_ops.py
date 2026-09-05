"""Tests para operaciones de vault_writer: append, set_property, delete, move, wikilinks."""

from __future__ import annotations

import pytest
from pathlib import Path

from adso.vault_writer import (
    _safe_component,
    append_to_note,
    create_note,
    delete_note,
    ensure_vault_structure,
    move_note,
    read_note,
    remove_broken_wikilinks,
    seed_vault,
    set_property,
    VAULT_DIRS,
)


@pytest.fixture
async def vault(tmp_path: Path) -> Path:
    for d in VAULT_DIRS:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


@pytest.fixture
async def sample_note(vault: Path) -> Path:
    fm = {"title": "Test Note", "type": "reference", "tags": ["ml"], "status": "active",
          "project": "tesis"}
    return await create_note(fm, "Body original.", vault)


class TestAppendToNote:

    @pytest.mark.asyncio
    async def test_append_adds_content(self, sample_note: Path) -> None:
        await append_to_note(sample_note, "Contenido nuevo.")
        note = await read_note(sample_note)
        assert "Body original." in note.body
        assert "Contenido nuevo." in note.body
        assert "---" in note.body  # separator

    @pytest.mark.asyncio
    async def test_append_updates_date_modified(self, sample_note: Path) -> None:
        before = (await read_note(sample_note)).frontmatter["date_modified"]
        await append_to_note(sample_note, "Más contenido.")
        after = (await read_note(sample_note)).frontmatter["date_modified"]
        assert after >= before

    @pytest.mark.asyncio
    async def test_append_not_found(self, vault: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await append_to_note(vault / "nope.md", "x")


class TestSetProperty:

    @pytest.mark.asyncio
    async def test_set_status(self, sample_note: Path) -> None:
        await set_property(sample_note, "status", "pending-classification")
        note = await read_note(sample_note)
        assert note.frontmatter["status"] == "pending-classification"

    @pytest.mark.asyncio
    async def test_set_invalid_type(self, sample_note: Path) -> None:
        with pytest.raises(ValueError, match="type inválido"):
            await set_property(sample_note, "type", "paper")

    @pytest.mark.asyncio
    async def test_set_invalid_status_for_type(self, sample_note: Path) -> None:
        with pytest.raises(ValueError, match="status inválido"):
            await set_property(sample_note, "status", "done")

    @pytest.mark.asyncio
    async def test_set_invalid_priority(self, sample_note: Path) -> None:
        with pytest.raises(ValueError, match="priority inválido"):
            await set_property(sample_note, "priority", "urgent")

    @pytest.mark.asyncio
    async def test_set_invalid_media_type(self, sample_note: Path) -> None:
        with pytest.raises(ValueError, match="media_type inválido"):
            await set_property(sample_note, "media_type", "video")

    @pytest.mark.asyncio
    async def test_set_invalid_source(self, sample_note: Path) -> None:
        with pytest.raises(ValueError, match="source inválido"):
            await set_property(sample_note, "source", "web")

    @pytest.mark.asyncio
    async def test_set_invalid_date(self, sample_note: Path) -> None:
        with pytest.raises(ValueError, match="ISO 8601"):
            await set_property(sample_note, "date_created", "not-a-date")

    @pytest.mark.asyncio
    async def test_set_tags_must_be_list(self, sample_note: Path) -> None:
        with pytest.raises(ValueError, match="lista"):
            await set_property(sample_note, "tags", "single-tag")

    @pytest.mark.asyncio
    async def test_set_tags_valid(self, sample_note: Path) -> None:
        await set_property(sample_note, "tags", ["ml", "cnn"])
        note = await read_note(sample_note)
        assert note.frontmatter["tags"] == ["ml", "cnn"]

    @pytest.mark.asyncio
    async def test_set_updates_date_modified(self, sample_note: Path) -> None:
        await set_property(sample_note, "tags", ["new"])
        note = await read_note(sample_note)
        assert "date_modified" in note.frontmatter

    @pytest.mark.asyncio
    async def test_set_valid_date(self, sample_note: Path) -> None:
        await set_property(sample_note, "due_date", "2025-06-15T10:00:00")
        note = await read_note(sample_note)
        # python-frontmatter puede devolver datetime o string según el parser YAML
        from datetime import datetime as dt
        val = note.frontmatter["due_date"]
        if isinstance(val, dt):
            assert val == dt(2025, 6, 15, 10, 0)
        else:
            assert val == "2025-06-15T10:00:00"


class TestDeleteNote:

    @pytest.mark.asyncio
    async def test_delete_removes_file(self, sample_note: Path) -> None:
        assert sample_note.exists()
        await delete_note(sample_note)
        assert not sample_note.exists()

    @pytest.mark.asyncio
    async def test_delete_not_found(self, vault: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await delete_note(vault / "nope.md")


class TestMoveNote:

    @pytest.mark.asyncio
    async def test_move_note(self, sample_note: Path, vault: Path) -> None:
        dest = vault / "02-Areas" / "investigacion"
        new_path = await move_note(sample_note, dest)
        assert new_path.exists()
        assert not sample_note.exists()
        assert "02-Areas" in str(new_path)

    @pytest.mark.asyncio
    async def test_move_creates_dest_dir(self, sample_note: Path, vault: Path) -> None:
        dest = vault / "02-Areas" / "nueva-area"
        new_path = await move_note(sample_note, dest)
        assert dest.is_dir()
        assert new_path.exists()

    @pytest.mark.asyncio
    async def test_move_not_found(self, vault: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await move_note(vault / "nope.md", vault / "00-Inbox")
class TestEnsureVaultStructure:

    @pytest.mark.asyncio
    async def test_creates_dirs(self, tmp_path: Path) -> None:
        await ensure_vault_structure(tmp_path)
        for d in VAULT_DIRS:
            assert (tmp_path / d).is_dir()

    @pytest.mark.asyncio
    async def test_idempotent(self, tmp_path: Path) -> None:
        await ensure_vault_structure(tmp_path)
        await ensure_vault_structure(tmp_path)
        for d in VAULT_DIRS:
            assert (tmp_path / d).is_dir()


class TestSeedVault:

    @pytest.mark.asyncio
    async def test_seeds_projects_and_areas(self, vault: Path) -> None:
        from adso.config import VaultSeedItem, VaultSeedConfig
        seed = VaultSeedConfig(
            projects=[VaultSeedItem(name="test-proj", description="A test project.")],
            areas=[VaultSeedItem(name="test-area", description="A test area.")],
        )
        await seed_vault(vault, seed)

        proj_index = vault / "01-Projects" / "test-proj" / "_index.md"
        assert proj_index.exists()
        note = await read_note(proj_index)
        assert note.frontmatter["type"] == "project-index"

        area_index = vault / "02-Areas" / "test-area" / "_index.md"
        assert area_index.exists()
        note = await read_note(area_index)
        assert note.frontmatter["type"] == "area-index"

    @pytest.mark.asyncio
    async def test_seed_idempotent(self, vault: Path) -> None:
        from adso.config import VaultSeedItem, VaultSeedConfig
        seed = VaultSeedConfig(
            projects=[VaultSeedItem(name="proj", description="Desc.")],
            areas=[],
        )
        await seed_vault(vault, seed)
        await seed_vault(vault, seed)  # Should not duplicate
        assert (vault / "01-Projects" / "proj" / "_index.md").exists()


class TestCreateNoteEdgeCases:

    @pytest.mark.asyncio
    async def test_missing_title_raises(self, vault: Path) -> None:
        with pytest.raises(ValueError, match="title"):
            await create_note({"type": "note"}, "body", vault)

    @pytest.mark.asyncio
    async def test_empty_title_raises(self, vault: Path) -> None:
        with pytest.raises(ValueError, match="title"):
            await create_note({"title": "", "type": "note"}, "body", vault)

    @pytest.mark.asyncio
    async def test_missing_type_raises(self, vault: Path) -> None:
        with pytest.raises(ValueError, match="type"):
            await create_note({"title": "Test"}, "body", vault)

    @pytest.mark.asyncio
    async def test_note_without_project_or_area_goes_to_inbox(self, vault: Path) -> None:
        path = await create_note(
            {"title": "Orphan", "type": "note"}, "body", vault
        )
        assert "00-Inbox" in str(path)

    @pytest.mark.asyncio
    async def test_task_without_area_goes_to_inbox(self, vault: Path) -> None:
        path = await create_note(
            {"title": "Task", "type": "task", "status": "pending"}, "body", vault
        )
        assert "00-Inbox" in str(path)

    @pytest.mark.asyncio
    async def test_idea_without_area_goes_to_inbox(self, vault: Path) -> None:
        path = await create_note(
            {"title": "Idea", "type": "idea", "status": "raw"}, "body", vault
        )
        assert "00-Inbox" in str(path)

    @pytest.mark.asyncio
    async def test_project_index_without_project_goes_to_inbox(self, vault: Path) -> None:
        path = await create_note(
            {"title": "Index", "type": "project-index", "description": "d"},
            "body", vault
        )
        assert "00-Inbox" in str(path)

    @pytest.mark.asyncio
    async def test_area_index_without_area_goes_to_inbox(self, vault: Path) -> None:
        path = await create_note(
            {"title": "Index", "type": "area-index", "description": "d"},
            "body", vault
        )
        assert "00-Inbox" in str(path)

    @pytest.mark.asyncio
    async def test_unknown_type_goes_to_inbox(self, vault: Path) -> None:
        path = await create_note(
            {"title": "X", "type": "weird"}, "body", vault
        )
        assert "00-Inbox" in str(path)

    @pytest.mark.asyncio
    async def test_read_nonexistent_note(self, vault: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await read_note(vault / "nope.md")

    @pytest.mark.asyncio
    async def test_read_note_without_frontmatter(self, vault: Path) -> None:
        p = vault / "no-fm.md"
        p.write_text("Just text, no frontmatter.", encoding="utf-8")
        with pytest.raises(ValueError, match="frontmatter"):
            await read_note(p)


class TestSafeComponent:
    """Sanitización de componentes de path contra traversal."""

    @pytest.mark.parametrize("value", ["tesis", "mi-proyecto", "Área 51", "a_b"])
    def test_valid_names_pass_through(self, value: str) -> None:
        assert _safe_component(value) == value.strip()

    @pytest.mark.parametrize(
        "value",
        ["../etc", "../../secret", "a/b", "a\\b", "..", ".", ".hidden", "", "   ", "x\x00y"],
    )
    def test_traversal_and_junk_rejected(self, value: str) -> None:
        assert _safe_component(value) is None

    @pytest.mark.parametrize("value", [None, 123, ["x"], {"a": 1}])
    def test_non_string_rejected(self, value) -> None:
        assert _safe_component(value) is None


class TestCreateNotePathTraversal:
    """create_note nunca debe escribir fuera del vault."""

    @pytest.mark.asyncio
    async def test_project_traversal_redirects_to_inbox(self, vault: Path) -> None:
        path = await create_note(
            {"title": "Evil", "type": "reference", "status": "active",
             "project": "../../../etc"},
            "body", vault,
        )
        assert path.resolve().is_relative_to(vault.resolve())
        assert "00-Inbox" in str(path)

    @pytest.mark.asyncio
    async def test_section_traversal_redirects_to_inbox(self, vault: Path) -> None:
        path = await create_note(
            {"title": "Evil2", "type": "reference", "status": "active",
             "project": "tesis", "section": "../../.obsidian"},
            "body", vault,
        )
        assert path.resolve().is_relative_to(vault.resolve())
        # project válido pero section con traversal → section se descarta, cae en el proyecto
        assert "01-Projects/tesis" in str(path)


class TestAtomicWrite:
    """Las escrituras deben ser atómicas y no dejar temporales."""

    @pytest.mark.asyncio
    async def test_no_temp_files_left_behind(self, vault: Path) -> None:
        await create_note(
            {"title": "Atomic", "type": "reference", "status": "active", "project": "tesis"},
            "body", vault,
        )
        temps = list((vault / "01-Projects" / "tesis").glob(".adso-tmp-*"))
        assert temps == []

    @pytest.mark.asyncio
    async def test_content_intact_after_append(self, sample_note: Path) -> None:
        await append_to_note(sample_note, "Nuevo contenido")
        note = await read_note(sample_note)
        assert "Body original." in note.body
        assert "Nuevo contenido" in note.body


class TestRemoveBrokenWikilinksScoped:
    """remove_broken_wikilinks solo toca el bloque '## Ver también'."""

    @pytest.mark.asyncio
    async def test_removes_link_in_ver_tambien(self, vault: Path) -> None:
        note = await create_note(
            {"title": "Src", "type": "reference", "status": "active", "project": "tesis"},
            "Cuerpo.\n\n## Ver también\n\n- [[nota-vieja]] — Nota vieja\n",
            vault,
        )
        deleted = vault / "01-Projects" / "tesis" / "nota-vieja.md"
        count = await remove_broken_wikilinks(vault, deleted)
        assert count == 1
        body = (await read_note(note)).body
        assert "[[nota-vieja]]" not in body

    @pytest.mark.asyncio
    async def test_preserves_user_text_outside_block(self, vault: Path) -> None:
        # Un wikilink usado en prosa/otra lista fuera de "Ver también" NO se toca.
        note = await create_note(
            {"title": "Src2", "type": "reference", "status": "active", "project": "tesis"},
            "- [[nota-vieja]] dato importante del usuario\n\n"
            "## Ver también\n\n- [[nota-vieja]] — link\n",
            vault,
        )
        deleted = vault / "01-Projects" / "tesis" / "nota-vieja.md"
        await remove_broken_wikilinks(vault, deleted)
        body = (await read_note(note)).body
        # El item del bloque Ver también se borró, pero la línea del usuario queda
        assert "dato importante del usuario" in body


# ---------------------------------------------------------------------------
# backup_label — commit messages for the vault backup
# ---------------------------------------------------------------------------
#
# New code, written in English per the repo-wide decision of 2026-08-26.


class TestBackupLabel:
    """The vault backup labels a change by note stem, which is useless for indexes.

    Every project and area index is named `_index.md`, so the watcher produced
    commits reading `Add note: _index` — no way to tell which project changed,
    and seven files share that stem.
    """

    def test_project_index_is_labelled_by_its_project(self) -> None:
        from adso.vault_writer import backup_label

        assert backup_label(Path("/vault/01-Projects/ADSO/_index.md")) == "ADSO (index)"

    def test_area_index_is_labelled_by_its_area(self) -> None:
        from adso.vault_writer import backup_label

        assert backup_label(Path("/vault/02-Areas/ROCKY/_index.md")) == "ROCKY (index)"

    def test_regular_note_keeps_its_stem(self) -> None:
        """Counter-case: ordinary notes must be labelled exactly as before."""
        from adso.vault_writer import backup_label

        p = Path("/vault/00-Inbox/2026-08-26-comprar-filamento.md")
        assert backup_label(p) == "2026-08-26-comprar-filamento"

    def test_nested_index_uses_its_immediate_parent(self) -> None:
        """A section index is labelled by the section, not the project.

        The label answers "which folder changed", so the nearest parent is the
        right one — the full path would make the commit subject unreadable.
        """
        from adso.vault_writer import backup_label

        p = Path("/vault/01-Projects/Tesis/capitulo-5/_index.md")
        assert backup_label(p) == "capitulo-5 (index)"
