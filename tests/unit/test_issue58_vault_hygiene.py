"""Issue #58 — vault hygiene: index tags and `03-Resources` in structural scans.

Both defects were found by auditing the *production* vault, not the code, and
both are the kind that never breaks anything loudly — they quietly degrade the
data the classification prompt and the search results are built from.

1. **Index tags bypass kebab-case normalization.** `manage.py` injects the
   project/area name straight into `tags`, so a project named `ROCKY` produces
   the tag `ROCKY` alongside every `rocky` the sanitizer emits elsewhere. The
   five non-kebab tags in the whole vault are exactly the five project/area
   names, and they split the tag vocabulary that `classify` reuses.

2. **`03-Resources/` is scanned as if it held notes.** The taxonomy defines it
   as attachments, but `_DEFAULT_EXCLUDE` never listed it, so any `.md` dropped
   there enters `search`, `get_all_tags` and `get_note_index`. In production
   that meant two Perplexity exports with no frontmatter at all surfacing with
   empty `type`/`status` and a "title" that was really the user's raw prompt.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import frontmatter
import pytest

from adso.handlers.manage import _cb_manage_confirm
from adso.vault_search import get_all_tags, get_note_index, search


def _pending(operation: str, name: str, description: str = "Una descripcion.") -> dict:
    """Build the `pending_operation` state a confirmed manage flow carries."""
    return {
        "payload": {
            "operation": operation,
            "params": {"name": name, "description": description},
        }
    }


def _index_tags(index_path: Path) -> list[str]:
    return frontmatter.load(index_path).metadata.get("tags", [])


class TestIndexTagsAreKebabCase:
    """The name is the directory; the *tag* derived from it must be kebab-case."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("operation", "folder"),
        [("create_project", "01-Projects"), ("create_area", "02-Areas")],
    )
    async def test_an_uppercase_name_produces_a_lowercase_tag(
        self, operation: str, folder: str, mock_context, vault_path: Path
    ) -> None:
        query = AsyncMock()
        mock_context.user_data["pending_operation"] = _pending(operation, "ROCKY")

        await _cb_manage_confirm(query, mock_context, vault_path)

        tags = _index_tags(vault_path / folder / "ROCKY" / "_index.md")
        assert "rocky" in tags, (
            f"the index tag is {tags!r}: the raw name went into `tags`, so it "
            "duplicates every `rocky` the sanitizer produces elsewhere and "
            "splits the vocabulary the classification prompt reuses"
        )
        assert "ROCKY" not in tags, "the un-normalized name must not survive as a tag"

    @pytest.mark.asyncio
    async def test_a_multiword_name_is_hyphenated(
        self, mock_context, vault_path: Path
    ) -> None:
        query = AsyncMock()
        mock_context.user_data["pending_operation"] = _pending(
            "create_project", "Presentación LLM"
        )

        await _cb_manage_confirm(query, mock_context, vault_path)

        tags = _index_tags(vault_path / "01-Projects" / "Presentación LLM" / "_index.md")
        assert "presentacion-llm" in tags, f"got {tags!r}"

    @pytest.mark.asyncio
    async def test_the_directory_and_the_project_field_keep_the_original_name(
        self, mock_context, vault_path: Path
    ) -> None:
        """Counter-case: only the *tag* is normalized.

        `project`/`area` address the folder on disk and are what `_resolve_dest_dir`
        routes on; kebab-casing them would point every future note at a directory
        that does not exist.
        """
        query = AsyncMock()
        mock_context.user_data["pending_operation"] = _pending("create_project", "ROCKY")

        await _cb_manage_confirm(query, mock_context, vault_path)

        index_path = vault_path / "01-Projects" / "ROCKY" / "_index.md"
        assert index_path.exists(), "the directory must keep the name the user chose"
        assert frontmatter.load(index_path).metadata["project"] == "ROCKY"

    @pytest.mark.asyncio
    async def test_an_already_kebab_name_is_unchanged(
        self, mock_context, vault_path: Path
    ) -> None:
        """Counter-case: normalization must be idempotent, not merely applied."""
        query = AsyncMock()
        mock_context.user_data["pending_operation"] = _pending("create_project", "adso")

        await _cb_manage_confirm(query, mock_context, vault_path)

        tags = _index_tags(vault_path / "01-Projects" / "adso" / "_index.md")
        assert "adso" in tags and tags.count("adso") == 1, f"got {tags!r}"

    @pytest.mark.asyncio
    async def test_the_system_tag_survives(self, mock_context, vault_path: Path) -> None:
        """Counter-case: normalizing must not drop the marker that flags an index."""
        query = AsyncMock()
        mock_context.user_data["pending_operation"] = _pending("create_area", "ICD")

        await _cb_manage_confirm(query, mock_context, vault_path)

        assert "system" in _index_tags(vault_path / "02-Areas" / "ICD" / "_index.md")


class TestResourcesAreNotScannedAsNotes:
    """`03-Resources/` holds attachments — a `.md` there is not a vault note."""

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @pytest.mark.asyncio
    async def test_a_markdown_file_in_resources_stays_out_of_search(
        self, vault_path: Path
    ) -> None:
        self._write(
            vault_path / "03-Resources" / "necesito material para una charla.md",
            "un export de Perplexity, sin frontmatter, cuyo nombre es el prompt crudo",
        )
        results = await search("Perplexity", vault_path)
        assert results == [], (
            f"an attachment surfaced as a note: {[r.get('path') for r in results]}"
        )

    @pytest.mark.asyncio
    async def test_a_markdown_file_in_resources_does_not_pollute_the_tag_vocabulary(
        self, vault_path: Path
    ) -> None:
        self._write(
            vault_path / "03-Resources" / "export.md",
            "---\ntags:\n- basura-de-adjunto\n---\n\ncuerpo\n",
        )
        assert "basura-de-adjunto" not in await get_all_tags(vault_path)

    @pytest.mark.asyncio
    async def test_a_markdown_file_in_resources_is_absent_from_the_note_index(
        self, vault_path: Path
    ) -> None:
        self._write(
            vault_path / "03-Resources" / "export.md",
            "---\ntitle: Export\ntype: reference\n---\n\ncuerpo\n",
        )
        index = await get_note_index(vault_path)
        assert "export" not in {p.stem for p in index.values()}

    @pytest.mark.asyncio
    async def test_notes_outside_resources_are_still_scanned(self, vault_path: Path) -> None:
        """Counter-case: the exclusion must be that folder, not the scan itself."""
        self._write(
            vault_path / "01-Projects" / "Tesis" / "nota.md",
            "---\ntitle: Nota\ntype: reference\ntags:\n- astro\n---\n\nPerplexity\n",
        )
        assert await search("Perplexity", vault_path), "a real note stopped being found"
        assert "astro" in await get_all_tags(vault_path)
