"""Tests unitarios para adso.document_extractor — PDF + texto."""

from __future__ import annotations

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from adso.document_extractor import (
    extract_pdf,
    extract_text_file,
    is_text_file,
    is_pdf,
)


class TestIsTextFile:

    def test_known_extensions(self) -> None:
        for ext in [".md", ".txt", ".py", ".csv", ".json", ".yaml", ".yml",
                    ".toml", ".html", ".sql", ".tex"]:
            assert is_text_file(f"file{ext}"), f"{ext} should be text"

    def test_unknown_extensions(self) -> None:
        for ext in [".pdf", ".docx", ".xlsx", ".zip", ".exe", ".jpg", ".png"]:
            assert not is_text_file(f"file{ext}"), f"{ext} should not be text"

    def test_case_insensitive(self) -> None:
        assert is_text_file("README.TXT")
        assert is_text_file("script.PY")

    def test_no_extension(self) -> None:
        assert not is_text_file("Makefile")


class TestIsPdf:

    def test_pdf(self) -> None:
        assert is_pdf("paper.pdf")
        assert is_pdf("paper.PDF")
        assert is_pdf("paper.Pdf")

    def test_not_pdf(self) -> None:
        assert not is_pdf("paper.doc")
        assert not is_pdf("paper.txt")


class TestExtractTextFile:

    @pytest.mark.asyncio
    async def test_reads_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("Contenido de prueba", encoding="utf-8")

        result = await extract_text_file(f)
        assert result == "Contenido de prueba"

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await extract_text_file(tmp_path / "no.txt")

    @pytest.mark.asyncio
    async def test_max_chars_truncates(self, tmp_path: Path) -> None:
        f = tmp_path / "long.txt"
        f.write_text("A" * 1000, encoding="utf-8")

        result = await extract_text_file(f, max_chars=100)
        assert len(result) == 100

    @pytest.mark.asyncio
    async def test_max_chars_no_truncate_when_short(self, tmp_path: Path) -> None:
        f = tmp_path / "short.txt"
        f.write_text("corto", encoding="utf-8")

        result = await extract_text_file(f, max_chars=1000)
        assert result == "corto"

    @pytest.mark.asyncio
    async def test_handles_encoding_errors(self, tmp_path: Path) -> None:
        f = tmp_path / "binary.txt"
        f.write_bytes(b"\xff\xfe\x00\x01 texto")

        result = await extract_text_file(f)
        assert "texto" in result


class TestExtractPdf:

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await extract_pdf(tmp_path / "no.pdf")

    @pytest.mark.asyncio
    async def test_extract_returns_text_and_metadata(self, tmp_path: Path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        # Mock pymupdf at sys.modules level so the import inside to_thread works
        mock_pymupdf = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {
            "title": "Test Paper",
            "author": "Author Name",
            "subject": "Testing",
        }
        mock_doc.page_count = 3

        page1 = MagicMock()
        page1.get_text.return_value = "Página uno."
        page2 = MagicMock()
        page2.get_text.return_value = "Página dos."
        page3 = MagicMock()
        page3.get_text.return_value = ""

        mock_doc.__iter__ = lambda self: iter([page1, page2, page3])
        mock_pymupdf.open.return_value = mock_doc

        with patch.dict(sys.modules, {"pymupdf": mock_pymupdf}):
            text, meta = await extract_pdf(pdf)

        assert "Página uno." in text
        assert "Página dos." in text
        assert meta["title"] == "Test Paper"
        assert meta["author"] == "Author Name"
        assert meta["pages"] == 3

    @pytest.mark.asyncio
    async def test_extract_empty_pdf(self, tmp_path: Path) -> None:
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        mock_pymupdf = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {}
        mock_doc.page_count = 1

        page = MagicMock()
        page.get_text.return_value = ""
        mock_doc.__iter__ = lambda self: iter([page])
        mock_pymupdf.open.return_value = mock_doc

        with patch.dict(sys.modules, {"pymupdf": mock_pymupdf}):
            text, meta = await extract_pdf(pdf)

        assert text == ""
        assert meta["title"] == ""
        assert meta["pages"] == 1

    @pytest.mark.asyncio
    async def test_extract_open_failure(self, tmp_path: Path) -> None:
        pdf = tmp_path / "bad.pdf"
        pdf.write_bytes(b"not a pdf")

        mock_pymupdf = MagicMock()
        mock_pymupdf.open.side_effect = RuntimeError("Invalid PDF")

        with patch.dict(sys.modules, {"pymupdf": mock_pymupdf}):
            with pytest.raises(RuntimeError, match="No se pudo abrir"):
                await extract_pdf(pdf)
