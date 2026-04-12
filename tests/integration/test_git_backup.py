"""Tests de integración: git backup con debounce."""

from __future__ import annotations

import asyncio
import pytest
from pathlib import Path

from adso.vault_writer import GitBackup


@pytest.fixture
def git_vault(tmp_path: Path) -> Path:
    """Vault temporal inicializado como repo git."""
    import git
    repo = git.Repo.init(str(tmp_path))
    # Crear un commit inicial para que el repo sea válido
    readme = tmp_path / "README.md"
    readme.write_text("# Vault\n")
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")
    return tmp_path


class TestGitBackup:

    @pytest.mark.asyncio
    async def test_single_note_commits(self, git_vault: Path) -> None:
        """Una nota → commit después del debounce."""
        import git
        backup = GitBackup(git_vault, debounce_seconds=0)

        # Crear un archivo en el vault
        (git_vault / "test.md").write_text("---\ntitle: Test\n---\nBody\n")

        await backup.notify("Test note")
        # Esperar a que el debounce dispare el backup
        await asyncio.sleep(0.2)

        repo = git.Repo(str(git_vault))
        last_msg = repo.head.commit.message
        assert "Add note: Test note" in last_msg

    @pytest.mark.asyncio
    async def test_multiple_notes_consolidated(self, git_vault: Path) -> None:
        """Varias notas rápidas → un solo commit."""
        import git
        backup = GitBackup(git_vault, debounce_seconds=0)

        (git_vault / "note1.md").write_text("nota 1")
        (git_vault / "note2.md").write_text("nota 2")

        await backup.notify("Nota 1")
        await backup.notify("Nota 2")
        await asyncio.sleep(0.2)

        repo = git.Repo(str(git_vault))
        last_msg = repo.head.commit.message
        assert "2 notes" in last_msg
        assert "Nota 1" in last_msg
        assert "Nota 2" in last_msg

    @pytest.mark.asyncio
    async def test_push_failure_does_not_crash(self, git_vault: Path) -> None:
        """Push falla → no crashea, nota segura en disco."""
        backup = GitBackup(git_vault, debounce_seconds=0)

        (git_vault / "test.md").write_text("safe content")
        await backup.notify("Safe note")
        await asyncio.sleep(0.2)

        # El push falla porque no hay remote → solo log warning
        assert (git_vault / "test.md").exists()

    @pytest.mark.asyncio
    async def test_no_changes_no_commit(self, git_vault: Path) -> None:
        """Sin cambios → no crea commit."""
        import git
        backup = GitBackup(git_vault, debounce_seconds=0)

        repo = git.Repo(str(git_vault))
        initial_count = len(list(repo.iter_commits()))

        await backup.notify("Ghost note")
        await asyncio.sleep(0.2)

        # No debería haber commit nuevo porque no hay archivos nuevos staged
        # (notify no crea archivos, solo registra el título)
        # En este caso _do_backup hace git add -A pero no hay cambios
        final_count = len(list(repo.iter_commits()))
        assert final_count == initial_count
