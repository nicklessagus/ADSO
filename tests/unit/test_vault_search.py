"""Tests unitarios para vault_search — parsing de wikilinks, tags, frontmatter."""

from __future__ import annotations

from pathlib import Path

from adso.vault_search import (
    _extract_tags_from_note,
    _strip_code_blocks,
    _WIKILINK_RE,
)
from adso.vault_writer import NoteData


class TestWikilinkExtraction:

    def test_simple_wikilink(self) -> None:
        text = "Ver [[baseline-cnn]] para más info."
        matches = _WIKILINK_RE.findall(text)
        assert "baseline-cnn" in matches

    def test_wikilink_with_alias(self) -> None:
        text = "Ver [[baseline-cnn|Resultados CNN]] para más info."
        matches = _WIKILINK_RE.findall(text)
        assert "baseline-cnn" in matches

    def test_wikilink_with_heading(self) -> None:
        text = "Ver [[baseline-cnn#Métodos]] para más info."
        matches = _WIKILINK_RE.findall(text)
        assert "baseline-cnn" in matches

    def test_wikilink_with_heading_and_alias(self) -> None:
        text = "Ver [[baseline-cnn#Métodos|ver métodos]] para más info."
        matches = _WIKILINK_RE.findall(text)
        assert "baseline-cnn" in matches

    def test_multiple_wikilinks(self) -> None:
        text = "Links: [[nota-a]], [[nota-b]], [[nota-c]]"
        matches = _WIKILINK_RE.findall(text)
        assert set(matches) == {"nota-a", "nota-b", "nota-c"}

    def test_wikilinks_in_code_block_ignored(self) -> None:
        """Wikilinks dentro de code blocks se ignoran."""
        text = "Normal [[nota-real]]\n\n```\n[[nota-falsa]]\n```\n"
        clean = _strip_code_blocks(text)
        matches = _WIKILINK_RE.findall(clean)
        assert "nota-real" in matches
        assert "nota-falsa" not in matches

    def test_wikilinks_in_inline_code_ignored(self) -> None:
        text = "Normal [[nota-real]] y `[[nota-falsa]]` inline."
        clean = _strip_code_blocks(text)
        matches = _WIKILINK_RE.findall(clean)
        assert "nota-real" in matches
        assert "nota-falsa" not in matches

    def test_wikilinks_in_obsidian_comments_ignored(self) -> None:
        text = "Normal [[nota-real]]\n%%\n[[nota-comentario]]\n%%\n"
        clean = _strip_code_blocks(text)
        matches = _WIKILINK_RE.findall(clean)
        assert "nota-real" in matches
        assert "nota-comentario" not in matches


class TestTagExtraction:

    def _make_note(
        self, tags: list[str] | None = None, body: str = ""
    ) -> NoteData:
        fm: dict = {"title": "Test", "type": "note"}
        if tags is not None:
            fm["tags"] = tags
        return NoteData(path=Path("/fake.md"), frontmatter=fm, body=body)

    def test_tags_from_frontmatter(self) -> None:
        note = self._make_note(tags=["paper", "ml", "cnn"])
        tags = _extract_tags_from_note(note)
        assert tags == {"paper", "ml", "cnn"}

    def test_inline_tags(self) -> None:
        note = self._make_note(body="Texto con #deep-learning y #metodo/cnn")
        tags = _extract_tags_from_note(note)
        assert "deep-learning" in tags
        assert "metodo/cnn" in tags

    def test_combined_frontmatter_and_inline(self) -> None:
        note = self._make_note(tags=["paper"], body="Inline #ml")
        tags = _extract_tags_from_note(note)
        assert "paper" in tags
        assert "ml" in tags

    def test_tags_normalized_lowercase(self) -> None:
        note = self._make_note(tags=["Paper", "ML"])
        tags = _extract_tags_from_note(note)
        assert "paper" in tags
        assert "ml" in tags

    def test_tags_with_hash_stripped(self) -> None:
        """Tags con # en frontmatter se normalizan."""
        note = self._make_note(tags=["#paper"])
        tags = _extract_tags_from_note(note)
        assert "paper" in tags
        assert "#paper" not in tags

    def test_hierarchical_tags(self) -> None:
        note = self._make_note(body="Usa #metodo/cnn y #metodo/transformer")
        tags = _extract_tags_from_note(note)
        assert "metodo/cnn" in tags
        assert "metodo/transformer" in tags

    def test_inline_tags_in_code_blocks_ignored(self) -> None:
        note = self._make_note(body="Real #tag\n```\n#falso\n```\n")
        tags = _extract_tags_from_note(note)
        assert "tag" in tags
        assert "falso" not in tags

    def test_no_tags(self) -> None:
        note = self._make_note(tags=[], body="Sin tags.")
        tags = _extract_tags_from_note(note)
        assert len(tags) == 0


class TestStripCodeBlocks:

    def test_fenced_code_block(self) -> None:
        text = "before\n```python\ncode\n```\nafter"
        clean = _strip_code_blocks(text)
        assert "code" not in clean
        assert "before" in clean
        assert "after" in clean

    def test_inline_code(self) -> None:
        text = "text `code` more"
        clean = _strip_code_blocks(text)
        assert "code" not in clean

    def test_obsidian_comments(self) -> None:
        text = "visible %%hidden%% visible"
        clean = _strip_code_blocks(text)
        assert "hidden" not in clean
        assert "visible" in clean
