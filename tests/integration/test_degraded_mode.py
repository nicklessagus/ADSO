"""Tests de integración: modo degradado (LLM falla → Inbox)."""

from __future__ import annotations

import pytest
from pathlib import Path

from adso.vault_writer import create_note, read_note


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for d in ["00-Inbox", "01-Projects", "02-Areas", "03-Resources", "05-Archive"]:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


class TestDegradedMode:

    @pytest.mark.asyncio
    async def test_degraded_goes_to_inbox(self, vault: Path) -> None:
        """En modo degradado, la nota va a 00-Inbox/ con pending-classification."""
        fm = {
            "title": "Nota sin clasificar",
            "type": "idea",
            "status": "pending-classification",
            "media_type": "text",
        }
        original_body = "Contenido original que debe preservarse íntegro sin perder nada."

        path = await create_note(fm, original_body, vault)

        assert path.exists()
        assert "00-Inbox" in str(path)

        note = await read_note(path)
        assert note.frontmatter["type"] == "idea"
        assert note.frontmatter["status"] == "pending-classification"
        assert note.body.strip() == original_body

    @pytest.mark.asyncio
    async def test_degraded_preserves_media_type(self, vault: Path) -> None:
        """media_type se preserva aunque no haya clasificación."""
        fm = {
            "title": "Audio sin clasificar",
            "type": "idea",
            "status": "pending-classification",
            "media_type": "audio",
        }
        path = await create_note(fm, "Transcripción del audio.", vault)
        note = await read_note(path)
        assert note.frontmatter["media_type"] == "audio"

    @pytest.mark.asyncio
    async def test_degraded_body_integrity(self, vault: Path) -> None:
        """El body se preserva completo con unicode, wikilinks, etc."""
        body = (
            "## Contenido\n\n"
            "Texto con [[wikilink]] y #tag\n"
            "Caracteres: café, niño, über\n"
            "Emoji: intentamos preservar todo"
        )
        fm = {
            "title": "Integridad",
            "type": "idea",
            "status": "pending-classification",
        }
        path = await create_note(fm, body, vault)
        note = await read_note(path)
        assert "café" in note.body
        assert "[[wikilink]]" in note.body
        assert "#tag" in note.body
