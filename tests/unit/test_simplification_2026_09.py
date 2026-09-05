"""Contracts pinned by the 2026-09 simplification pass.

The refactor collapsed copies of the same logic into shared helpers. These tests
specify what each helper must do so the next edit cannot drift a caller away
from the others, plus two `xfail(strict=True)` reproducers for bugs found while
reading the code (not fixed here — see the issues named in each `reason`).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.helpers import write_note

from adso.bot_utils import _has_pending_keyboard, count_unclassified_inbox, reply_blocked
from adso.constants import (
    DEFAULT_EXCLUDE_DIRS,
    LLM_NOTE_TYPES,
    NOTE_TYPES,
    STATUS_BY_TYPE,
    STATUS_ON_CONFIRM,
)
from adso.embeddings import build_note_metadata
from adso.handlers.capture import inherit_inbox_frontmatter
from adso.keyboards import build_fallback_pdf_keyboard, build_intent_keyboard
from adso.reporters import _authors_year, _filter_scope, _scope_label
from adso.vault_writer import (
    _reserve_name_sync,
    _resolve_dest_dir,
    build_index_note,
    create_note,
    read_note,
    seed_vault,
)


# ---------------------------------------------------------------------------
# Taxonomy lives in one place
# ---------------------------------------------------------------------------


class TestTaxonomyIsSingleSourced:
    def test_llm_types_are_a_subset_of_persistable_types(self) -> None:
        assert LLM_NOTE_TYPES < NOTE_TYPES
        assert set(STATUS_BY_TYPE) == NOTE_TYPES

    def test_llm_schema_and_vault_writer_derive_from_constants(self) -> None:
        from adso import llm_schema, vault_writer

        assert llm_schema.VALID_TYPES == LLM_NOTE_TYPES
        assert vault_writer.VALID_TYPES == NOTE_TYPES
        for note_type in LLM_NOTE_TYPES:
            assert llm_schema.VALID_STATUS[note_type] == STATUS_BY_TYPE[note_type]
            assert vault_writer.VALID_STATUS[note_type] == STATUS_BY_TYPE[note_type]

    def test_status_on_confirm_is_valid_for_its_type(self) -> None:
        for note_type, status in STATUS_ON_CONFIRM.items():
            assert status in STATUS_BY_TYPE[note_type]
            assert status != "pending-classification"

    def test_default_exclude_dirs_match_config_default(self) -> None:
        from adso.config import VaultConfig
        from adso.vault_search import _DEFAULT_EXCLUDE

        assert VaultConfig().exclude_dirs == list(DEFAULT_EXCLUDE_DIRS)
        assert _DEFAULT_EXCLUDE == list(DEFAULT_EXCLUDE_DIRS)


# ---------------------------------------------------------------------------
# vault_writer helpers
# ---------------------------------------------------------------------------


class TestResolveDestDir:
    """The three LLM types share one routing rule; only the fallback differs."""

    @pytest.mark.parametrize("note_type", ["reference", "task", "idea"])
    def test_project_beats_area(self, note_type: str, tmp_path: Path) -> None:
        fm = {"type": note_type, "project": "p", "area": "a", "section": "s"}
        assert _resolve_dest_dir(fm, tmp_path) == tmp_path / "01-Projects" / "p" / "s"

    @pytest.mark.parametrize("note_type", ["reference", "task", "idea"])
    def test_area_without_project(self, note_type: str, tmp_path: Path) -> None:
        fm = {"type": note_type, "area": "a"}
        assert _resolve_dest_dir(fm, tmp_path) == tmp_path / "02-Areas" / "a"

    def test_fallback_differs_only_for_task(self, tmp_path: Path) -> None:
        assert _resolve_dest_dir({"type": "task"}, tmp_path) == tmp_path / "00-Inbox"
        assert _resolve_dest_dir({"type": "reference"}, tmp_path) is None
        assert _resolve_dest_dir({"type": "idea"}, tmp_path) is None

    def test_unknown_type_goes_to_inbox(self, tmp_path: Path) -> None:
        assert _resolve_dest_dir({"type": "weird", "project": "p"}, tmp_path) == tmp_path / "00-Inbox"


class TestReserveNameSync:
    """One O_EXCL loop for notes (`stem-2`), attachments and orphans (`stem_1`)."""

    def test_free_name_is_reserved_as_is(self, tmp_path: Path) -> None:
        path, reserved = _reserve_name_sync(tmp_path, "a.md", sep="-", start=2)
        assert path == tmp_path / "a.md" and reserved
        assert path.exists() and path.stat().st_size == 0

    def test_note_convention_starts_at_dash_two(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("x")
        path, _ = _reserve_name_sync(tmp_path, "a.md", sep="-", start=2)
        assert path.name == "a-2.md"
        path, _ = _reserve_name_sync(tmp_path, "a.md", sep="-", start=2)
        assert path.name == "a-3.md"

    def test_attachment_convention_starts_at_underscore_one(self, tmp_path: Path) -> None:
        (tmp_path / "doc.pdf").write_bytes(b"x")
        path, _ = _reserve_name_sync(tmp_path, "doc.pdf", sep="_", start=1)
        assert path.name == "doc_1.pdf"

    def test_reuse_predicate_short_circuits_without_reserving(self, tmp_path: Path) -> None:
        existing = tmp_path / "doc.pdf"
        existing.write_bytes(b"same")
        path, reserved = _reserve_name_sync(
            tmp_path, "doc.pdf", sep="_", start=1, reuse_if=lambda p: p.read_bytes() == b"same"
        )
        assert path == existing and not reserved
        assert not (tmp_path / "doc_1.pdf").exists()

    def test_reuse_predicate_rejecting_moves_to_next_name(self, tmp_path: Path) -> None:
        (tmp_path / "doc.pdf").write_bytes(b"other")
        path, reserved = _reserve_name_sync(
            tmp_path, "doc.pdf", sep="_", start=1, reuse_if=lambda p: False
        )
        assert path.name == "doc_1.pdf" and reserved


class TestBuildIndexNote:
    """`manage.py` and `seed_vault` must write the same index."""

    def test_project_index_shape(self) -> None:
        fm, body = build_index_note("project", "Mi Proyecto", "Scope.")
        assert fm["type"] == "project-index"
        assert fm["project"] == "Mi Proyecto"           # raw: addresses the folder
        assert fm["tags"] == ["system", "mi-proyecto"]  # kebab: shared tag vocabulary (#58)
        assert fm["status"] == "active" and fm["sections"] == []
        assert fm["description"] == "Scope."
        assert body.startswith("# Mi Proyecto\n\n## Descripción\nScope.\n")
        assert "## Secciones" in body and "## Estado" in body

    def test_area_index_has_no_lifecycle_fields(self) -> None:
        fm, body = build_index_note("area", "docencia", "Clases.")
        assert fm["type"] == "area-index" and fm["area"] == "docencia"
        assert "status" not in fm and "sections" not in fm
        assert fm["tags"] == ["system", "docencia"]
        assert "## Secciones" not in body

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValueError):
            build_index_note("section", "x", "y")

    @pytest.mark.asyncio
    async def test_seed_writes_the_same_tags_as_the_bot(self, vault_path: Path) -> None:
        """The seed used to write the raw name as its only tag — no `system`
        marker and, for `ROCKY`, a tag split from the `rocky` every other note
        gets. Both paths now go through `build_index_note`."""
        from adso.config import VaultSeedConfig, VaultSeedItem

        seed = VaultSeedConfig(
            projects=[VaultSeedItem(name="ROCKY", description="Rocket.")],
            areas=[VaultSeedItem(name="Vida Personal", description="Trámites.")],
        )
        await seed_vault(vault_path, seed)

        proj = await read_note(vault_path / "01-Projects" / "ROCKY" / "_index.md")
        area = await read_note(vault_path / "02-Areas" / "Vida Personal" / "_index.md")
        assert proj.frontmatter["tags"] == ["system", "rocky"]
        assert proj.frontmatter["project"] == "ROCKY"
        assert area.frontmatter["tags"] == ["system", "vida-personal"]
        assert area.frontmatter["area"] == "Vida Personal"


# ---------------------------------------------------------------------------
# Shared handler helpers
# ---------------------------------------------------------------------------


class TestInheritInboxFrontmatter:
    def test_carries_creation_date_and_medium_and_drops_user_context(self) -> None:
        orig = {
            "date_created": "2026-08-01T10:00:00",
            "media_type": "audio",
            "user_context": "caption",
        }
        new = {"title": "T", "user_context": "leaked", "media_type": "text"}
        inherit_inbox_frontmatter(new, orig)
        assert new["date_created"] == "2026-08-01T10:00:00"
        assert new["media_type"] == "audio"
        assert new["source"] == "telegram"
        assert "user_context" not in new

    def test_defaults_when_the_original_lacks_fields(self) -> None:
        new: dict = {}
        inherit_inbox_frontmatter(new, {})
        assert new == {"date_created": "", "source": "telegram", "media_type": "text"}


class TestCountUnclassifiedInbox:
    @pytest.mark.asyncio
    async def test_counts_only_pending_notes_without_destination(self, vault_path: Path) -> None:
        inbox = vault_path / "00-Inbox"
        write_note(inbox / "a.md", "x", status="pending-classification")
        write_note(inbox / "b.md", "x", status="pending-classification", project="p")
        write_note(inbox / "c.md", "x", status="pending-classification", area="a")
        write_note(inbox / "d.md", "x", status="active")
        write_note(vault_path / "01-Projects" / "p" / "e.md", "x", status="pending-classification")
        assert await count_unclassified_inbox(vault_path) == 1

    @pytest.mark.asyncio
    async def test_empty_inbox_is_zero(self, vault_path: Path) -> None:
        assert await count_unclassified_inbox(vault_path) == 0


class TestReplyBlocked:
    @pytest.mark.asyncio
    async def test_records_both_message_ids_for_later_cleanup(self) -> None:
        context = SimpleNamespace(user_data={})
        reply = AsyncMock(return_value=SimpleNamespace(message_id=99))
        await reply_blocked(context, reply, SimpleNamespace(message_id=7))
        assert context.user_data["block_msg_ids"] == [7, 99]
        assert "acción pendiente" in reply.await_args.args[0]

    @pytest.mark.asyncio
    async def test_without_user_message_records_only_the_reply(self) -> None:
        context = SimpleNamespace(user_data={"block_msg_ids": [1]})
        reply = AsyncMock(return_value=SimpleNamespace(message_id=2))
        await reply_blocked(context, reply, None)
        assert context.user_data["block_msg_ids"] == [1, 2]


class TestHasPendingKeyboardTable:
    """Same semantics as the old if-chain, now table-driven."""

    @pytest.mark.parametrize("key", [
        "pending_note", "pending_raw_content", "pending_fallback_pdf", "pending_report",
        "pending_read_status", "pending_arxiv", "pending_duplicate_doc", "pending_operation",
    ])
    def test_each_keyboard_state_blocks(self, key: str) -> None:
        context = SimpleNamespace(user_data={key: {"x": 1}})
        assert _has_pending_keyboard(context)

    @pytest.mark.parametrize("key", ["pending_transcript", "pending_extraction"])
    def test_text_correction_states_do_not_block(self, key: str) -> None:
        assert _has_pending_keyboard(SimpleNamespace(user_data={key: {"text": "t"}}))
        assert not _has_pending_keyboard(
            SimpleNamespace(user_data={key: {"text": "t", "awaiting_correction": True}})
        )

    def test_empty_state_does_not_block(self) -> None:
        assert not _has_pending_keyboard(SimpleNamespace(user_data={}))
        assert not _has_pending_keyboard(SimpleNamespace(user_data={"pending_note": None}))


class TestKeyboardsSharedBuilders:
    def test_fallback_keyboard_without_ocr_drops_only_the_ocr_button(self) -> None:
        full = [b.text for row in build_fallback_pdf_keyboard().inline_keyboard for b in row]
        without = [
            b.text for row in build_fallback_pdf_keyboard(with_ocr=False).inline_keyboard for b in row
        ]
        assert "OCR" in full and "OCR" not in without
        assert set(full) - {"OCR"} == set(without)

    def test_intent_keyboard_without_intents_is_the_injection_warning_row(self) -> None:
        kb = build_intent_keyboard([])
        assert [[b.text for b in row] for row in kb.inline_keyboard] == [["Cancelar", "Tarea", "Nota"]]


class TestBuildNoteMetadata:
    def test_shape_and_hash(self) -> None:
        meta = build_note_metadata(Path("01-Projects/p/n.md"), {"title": "T", "tags": ["a"]}, "body")
        assert meta["path"] == "01-Projects/p/n.md"
        assert meta["title"] == "T" and meta["tags"] == ["a"]
        assert meta["type"] == "" and meta["project"] == ""
        import hashlib

        assert meta["content_hash"] == hashlib.md5(b"body").hexdigest()


class TestReportersHelpers:
    def test_scope_label_precedence(self) -> None:
        assert _scope_label("p", "a", inbox=True) == "Inbox"
        assert _scope_label("p", "a") == "Proyecto: p"
        assert _scope_label(None, "a") == "Área: a"
        assert _scope_label(None, None) == "Vault completo"

    def test_filter_scope_is_case_insensitive_and_project_first(self) -> None:
        notes = [
            SimpleNamespace(frontmatter={"project": "Tesis"}),
            SimpleNamespace(frontmatter={"area": "Docencia"}),
        ]
        assert _filter_scope(notes, "tesis", None) == [notes[0]]
        assert _filter_scope(notes, None, "docencia") == [notes[1]]
        assert _filter_scope(notes, None, None) == notes

    def test_authors_year_formats(self) -> None:
        assert _authors_year({"authors": ["A", "B", "C"], "year": 2024}) == "A, B (2024)"
        assert _authors_year({"authors": "Smith, J."}) == "Smith, J."
        assert _authors_year({"year": 2020}) == "2020"
        assert _authors_year({}) == ""


# ---------------------------------------------------------------------------
# Bugs found during the review — reproducers, not fixes
# ---------------------------------------------------------------------------


class TestReclassifyKeepsVerbatimBody:
    """Interactive capture keeps the user's text as the body for `text`/`audio`
    (and for documents captured verbatim); `/clasificar` does the same. The
    cron `reclassify_inbox` only does it for `audio` — a degraded text note
    comes back with the LLM's rewrite as its body."""

    @staticmethod
    def _context(vault: Path) -> SimpleNamespace:
        settings = SimpleNamespace(
            vault_path=vault,
            telegram_allowed_user_id=12345,
            tasks=SimpleNamespace(debug=False),
            llm=SimpleNamespace(disambiguation_threshold=0.7),
        )
        return SimpleNamespace(
            user_data={},
            bot_data={"settings": settings},
            bot=SimpleNamespace(send_message=AsyncMock()),
            application=SimpleNamespace(user_data={}),
        )

    @staticmethod
    def _llm_result() -> dict:
        return {
            "mode": "capture",
            "confidence": 0.9,
            "payload": {
                "frontmatter": {"title": "Clasificada", "type": "reference", "status": "active", "tags": []},
                "body": "Reescritura del LLM.",
            },
        }

    async def _run(self, vault_path: Path, media_type: str) -> str:
        from adso.handlers import jobs

        inbox = write_note(
            vault_path / "00-Inbox" / "2026-08-01-nota.md",
            "Texto original del usuario.",
            type="idea",
            status="pending-classification",
            project="p",
            media_type=media_type,
        )
        context = self._context(vault_path)
        with (
            patch.object(jobs, "find_by_property", AsyncMock(return_value=[SimpleNamespace(path=inbox)])),
            patch.object(jobs, "_get_existing_items", AsyncMock(return_value=([], []))),
            patch.object(jobs, "_get_existing_tags", AsyncMock(return_value=[])),
            patch.object(jobs, "classify", AsyncMock(return_value=self._llm_result())),
        ):
            await jobs._reclassify_inbox_impl(context)

        (new_path,) = (vault_path / "01-Projects" / "p").glob("*.md")
        return (await read_note(new_path)).body.strip()

    @pytest.mark.asyncio
    async def test_audio_keeps_the_original_body(self, vault_path: Path) -> None:
        """Counter-case: the audio branch already does the right thing."""
        assert await self._run(vault_path, "audio") == "Texto original del usuario."

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="BUG #64: reclassify_inbox replaces the verbatim body of a text note with the LLM rewrite",
    )
    async def test_text_keeps_the_original_body(self, vault_path: Path) -> None:
        assert await self._run(vault_path, "text") == "Texto original del usuario."


class TestScopeReportEmptyProjectCountsNoItems:
    """A project that only has its `_index.md` is empty for the user, but
    `scan_notes` returns the index and `item_count` counts it, so the
    "no hay nada" notice of `_send_report` never fires for it."""

    @pytest.mark.asyncio
    async def test_project_with_notes_counts_them(self, vault_path: Path) -> None:
        """Counter-case: real notes must keep counting."""
        from adso.reporters import scope_report

        fm, body = build_index_note("project", "p", "Scope.")
        await create_note(fm, body, vault_path)
        write_note(vault_path / "01-Projects" / "p" / "n.md", "x", project="p")
        with patch("adso.reporters._llm_synthesis", AsyncMock(return_value=None)):
            result = await scope_report(vault_path, project="p")
        assert result.item_count >= 1

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="BUG #65: scope_report counts the project's own _index.md as an item",
    )
    async def test_project_with_only_its_index_is_empty(self, vault_path: Path) -> None:
        from adso.reporters import scope_report

        fm, body = build_index_note("project", "p", "Scope.")
        await create_note(fm, body, vault_path)
        with patch("adso.reporters._llm_synthesis", AsyncMock(return_value=None)):
            result = await scope_report(vault_path, project="p")
        assert result.item_count == 0
