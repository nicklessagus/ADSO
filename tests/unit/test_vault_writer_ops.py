"""Tests para operaciones de vault_writer: append, set_property, delete, move, wikilinks."""

from __future__ import annotations

import pytest
from pathlib import Path

from adso.vault_writer import (
    append_to_note,
    create_note,
    delete_note,
    ensure_vault_structure,
    move_note,
    read_note,
    seed_vault,
    set_property,
    update_wikilinks,
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
        assert note.frontmatter["due_date"] == "2025-06-15T10:00:00"


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


class TestUpdateWikilinks:

    @pytest.mark.asyncio
    async def test_simple_wikilink(self, sample_note: Path) -> None:
        # Rewrite body to include a wikilink
        note = await read_note(sample_note)
        import frontmatter as fm_lib
        post = fm_lib.Post("See [[OldNote]] for details.", **note.frontmatter)
        sample_note.write_text(fm_lib.dumps(post), encoding="utf-8")

        await update_wikilinks(sample_note, "OldNote", "NewNote")
        content = sample_note.read_text(encoding="utf-8")
        assert "[[NewNote]]" in content
        assert "[[OldNote]]" not in content

    @pytest.mark.asyncio
    async def test_wikilink_with_alias(self, sample_note: Path) -> None:
        note = await read_note(sample_note)
        import frontmatter as fm_lib
        post = fm_lib.Post("See [[OldNote|my alias]].", **note.frontmatter)
        sample_note.write_text(fm_lib.dumps(post), encoding="utf-8")

        await update_wikilinks(sample_note, "OldNote", "NewNote")
        content = sample_note.read_text(encoding="utf-8")
        assert "[[NewNote|my alias]]" in content

    @pytest.mark.asyncio
    async def test_wikilink_with_heading(self, sample_note: Path) -> None:
        note = await read_note(sample_note)
        import frontmatter as fm_lib
        post = fm_lib.Post("See [[OldNote#section]].", **note.frontmatter)
        sample_note.write_text(fm_lib.dumps(post), encoding="utf-8")

        await update_wikilinks(sample_note, "OldNote", "NewNote")
        content = sample_note.read_text(encoding="utf-8")
        assert "[[NewNote#section]]" in content

    @pytest.mark.asyncio
    async def test_no_match_no_change(self, sample_note: Path) -> None:
        original = sample_note.read_text(encoding="utf-8")
        await update_wikilinks(sample_note, "NonExistent", "Other")
        assert sample_note.read_text(encoding="utf-8") == original


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
