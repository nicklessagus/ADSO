"""Tests de integración: flujo de captura LLM mock → vault_writer → disco."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from adso.vault_writer import create_note, read_note, ensure_vault_structure


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "llm_responses"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Vault temporal con estructura PARA."""
    for d in ["00-Inbox", "01-Projects", "02-Areas", "03-Resources", "05-Archive"]:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class TestCaptureFlow:

    @pytest.mark.asyncio
    async def test_note_to_project(self, vault: Path) -> None:
        """Nota clasificada en proyecto → escrita en 01-Projects/{proyecto}/{seccion}/."""
        fixture = _load_fixture("classify_text_note.json")
        payload = fixture["payload"]
        fm = payload["frontmatter"]
        fm["source"] = "telegram"
        fm["media_type"] = "text"

        path = await create_note(fm, payload["body"], vault)

        assert path.exists()
        assert "01-Projects" in str(path)
        assert "tesis" in str(path)
        assert "experimentos" in str(path)

        note = await read_note(path)
        assert note.frontmatter["title"] == "Baseline CNN — resultados preliminares"
        assert note.frontmatter["type"] == "note"
        assert note.frontmatter["project"] == "tesis"
        assert "CNN baseline" in note.body

    @pytest.mark.asyncio
    async def test_task_to_area(self, vault: Path) -> None:
        """Task → escrita en 02-Areas/{area}/."""
        fixture = _load_fixture("classify_text_task.json")
        payload = fixture["payload"]
        fm = payload["frontmatter"]
        fm["source"] = "telegram"
        fm["media_type"] = "text"

        path = await create_note(fm, payload["body"], vault)

        assert path.exists()
        assert "02-Areas" in str(path)
        assert "investigacion" in str(path)

        note = await read_note(path)
        assert note.frontmatter["type"] == "task"
        assert note.frontmatter["status"] == "pending"
        assert note.frontmatter["priority"] == "high"

    @pytest.mark.asyncio
    async def test_idea_to_area(self, vault: Path) -> None:
        fixture = _load_fixture("classify_text_idea.json")
        payload = fixture["payload"]
        fm = payload["frontmatter"]
        fm["source"] = "telegram"
        fm["media_type"] = "text"

        path = await create_note(fm, payload["body"], vault)

        assert path.exists()
        assert "02-Areas" in str(path)
        assert note_type(path, "idea")

    @pytest.mark.asyncio
    async def test_inbox_note(self, vault: Path) -> None:
        fixture = _load_fixture("classify_text_inbox.json")
        payload = fixture["payload"]
        fm = payload["frontmatter"]
        fm["source"] = "telegram"
        fm["media_type"] = "text"

        path = await create_note(fm, payload["body"], vault)

        assert path.exists()
        assert "00-Inbox" in str(path)

    @pytest.mark.asyncio
    async def test_dirs_created_automatically(self, vault: Path) -> None:
        """Directorios intermedios se crean si no existen."""
        fm = {
            "title": "Nueva nota",
            "type": "note",
            "project": "nuevo-proyecto",
            "section": "nueva-seccion",
        }
        path = await create_note(fm, "Body", vault)
        assert path.exists()
        assert (vault / "01-Projects" / "nuevo-proyecto" / "nueva-seccion").is_dir()

    @pytest.mark.asyncio
    async def test_body_preserved_intact(self, vault: Path) -> None:
        """El body se preserva íntegro."""
        body = "Línea 1\n\n## Sección\n\nContenido con [[wikilink]] y #tag\n\n- Lista\n- Items"
        fm = {"title": "Body test", "type": "inbox"}
        path = await create_note(fm, body, vault)
        note = await read_note(path)
        assert note.body.strip() == body.strip()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write(self, vault: Path) -> None:
        """dry_run=True retorna path sin crear archivo."""
        fm = {"title": "Dry run", "type": "inbox"}
        path = await create_note(fm, "Body", vault, dry_run=True)
        assert not path.exists()
        assert str(path).endswith(".md")


def note_type(path: Path, expected: str) -> bool:
    """Helper para verificar type desde archivo."""
    import frontmatter as fm_lib
    post = fm_lib.load(str(path))
    return post.metadata.get("type") == expected
