"""Tests unitarios para vault_cache — caché de parsing por (mtime, size)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from adso import vault_cache
from adso.vault_cache import parse_cached


NOTE_BODY = """---
title: Nota de prueba
type: reference
tags: [alpha, beta]
---

Cuerpo de la nota con [[link]] y #tag.
"""


@pytest.fixture(autouse=True)
def _clear_cache():
    """Cada test arranca con el caché limpio."""
    vault_cache.clear()
    yield
    vault_cache.clear()


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class TestParseCached:

    def test_parses_frontmatter_and_body(self, tmp_path: Path) -> None:
        note = tmp_path / "n.md"
        _write(note, NOTE_BODY)

        data = parse_cached(note)

        assert data is not None
        assert data.frontmatter["title"] == "Nota de prueba"
        assert data.frontmatter["type"] == "reference"
        assert "Cuerpo de la nota" in data.body

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert parse_cached(tmp_path / "nope.md") is None

    def test_no_frontmatter_returns_none(self, tmp_path: Path) -> None:
        note = tmp_path / "plain.md"
        _write(note, "Solo texto, sin frontmatter.\n")
        assert parse_cached(note) is None

    def test_corrupt_yaml_returns_none_and_warns(self, tmp_path: Path, caplog) -> None:
        # YAML de frontmatter inválido (indentación rota): la nota se omite de los
        # scans pero debe quedar registrada a nivel warning para diagnóstico.
        import logging

        note = tmp_path / "broken.md"
        _write(note, "---\ntitle: x\n  bad: : indent\n\ttab\n---\nbody\n")
        with caplog.at_level(logging.WARNING, logger="adso.vault_cache"):
            result = parse_cached(note)
        assert result is None
        assert any("Frontmatter inválido" in r.message for r in caplog.records)

    def test_second_call_is_cache_hit(self, tmp_path: Path) -> None:
        note = tmp_path / "n.md"
        _write(note, NOTE_BODY)

        parse_cached(note)
        parse_cached(note)

        stats = vault_cache.stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 1
        assert stats["entries"] == 1

    def test_modification_invalidates_via_mtime(self, tmp_path: Path) -> None:
        note = tmp_path / "n.md"
        _write(note, NOTE_BODY)
        parse_cached(note)

        # Reescribir con contenido distinto y mtime avanzado.
        updated = NOTE_BODY.replace("Nota de prueba", "Nota editada")
        _write(note, updated)
        future = time.time() + 5
        os.utime(note, (future, future))

        data = parse_cached(note)

        assert data is not None
        assert data.frontmatter["title"] == "Nota editada"
        stats = vault_cache.stats()
        # Dos misses (parse inicial + re-parse tras cambio), ningún hit.
        assert stats["misses"] == 2
        assert stats["hits"] == 0

    def test_returned_frontmatter_is_isolated_copy(self, tmp_path: Path) -> None:
        note = tmp_path / "n.md"
        _write(note, NOTE_BODY)

        first = parse_cached(note)
        first.frontmatter["title"] = "MUTADO"
        first.frontmatter["nuevo"] = "campo"

        second = parse_cached(note)
        assert second.frontmatter["title"] == "Nota de prueba"
        assert "nuevo" not in second.frontmatter

    def test_invalidate_forces_reparse(self, tmp_path: Path) -> None:
        note = tmp_path / "n.md"
        _write(note, NOTE_BODY)
        parse_cached(note)
        vault_cache.invalidate(note)
        parse_cached(note)

        stats = vault_cache.stats()
        assert stats["misses"] == 2
        assert stats["hits"] == 0

    def test_deleted_file_drops_stale_entry(self, tmp_path: Path) -> None:
        note = tmp_path / "n.md"
        _write(note, NOTE_BODY)
        parse_cached(note)
        assert vault_cache.stats()["entries"] == 1

        note.unlink()
        assert parse_cached(note) is None
        assert vault_cache.stats()["entries"] == 0


class TestEviction:

    def test_lru_eviction_respects_max(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(vault_cache, "_MAX_ENTRIES", 3)
        paths = []
        for i in range(5):
            p = tmp_path / f"n{i}.md"
            _write(p, NOTE_BODY)
            parse_cached(p)
            paths.append(p)

        assert vault_cache.stats()["entries"] == 3
