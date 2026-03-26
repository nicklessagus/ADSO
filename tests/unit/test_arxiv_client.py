"""Tests unitarios para adso.arxiv_client."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from adso.arxiv_client import (
    _fetch_metadata_sync,
    build_arxiv_body,
    build_arxiv_classify_content,
    extract_arxiv_id,
    fetch_arxiv_metadata,
)

# XML de respuesta Atom mínimo, con los campos relevantes
_SAMPLE_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2301.07041v2</id>
    <title>Verifiable Fully Homomorphic Encryption</title>
    <summary>  FHE enables computation over encrypted data.  </summary>
    <published>2023-01-17T18:00:00Z</published>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <arxiv:doi>10.1234/test.doi</arxiv:doi>
    <category term="cs.CR" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
    <link rel="alternate" type="text/html" href="https://arxiv.org/abs/2301.07041v2"/>
  </entry>
</feed>"""

_EMPTY_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>"""


# ---------------------------------------------------------------------------
# extract_arxiv_id
# ---------------------------------------------------------------------------

class TestExtractArxivId:

    def test_abs_url(self) -> None:
        assert extract_arxiv_id("https://arxiv.org/abs/2301.12345") == "2301.12345"

    def test_abs_url_with_version(self) -> None:
        assert extract_arxiv_id("https://arxiv.org/abs/2301.12345v3") == "2301.12345"

    def test_pdf_url(self) -> None:
        assert extract_arxiv_id("https://arxiv.org/pdf/2301.12345") == "2301.12345"

    def test_pdf_url_with_extension(self) -> None:
        assert extract_arxiv_id("https://arxiv.org/pdf/2301.12345.pdf") == "2301.12345"

    def test_old_format(self) -> None:
        assert extract_arxiv_id("https://arxiv.org/abs/hep-ph/0001234") == "hep-ph/0001234"

    def test_http_scheme(self) -> None:
        assert extract_arxiv_id("http://arxiv.org/abs/2301.12345") == "2301.12345"

    def test_url_with_extra_text(self) -> None:
        # URL embebida en texto
        assert extract_arxiv_id("mirá este paper https://arxiv.org/abs/2301.12345 interesante") == "2301.12345"

    def test_no_arxiv_url(self) -> None:
        assert extract_arxiv_id("https://google.com/foo") is None

    def test_plain_text(self) -> None:
        assert extract_arxiv_id("texto sin url") is None

    def test_empty_string(self) -> None:
        assert extract_arxiv_id("") is None


# ---------------------------------------------------------------------------
# _fetch_metadata_sync / fetch_arxiv_metadata
# ---------------------------------------------------------------------------

class TestFetchArxivMetadata:

    def test_parses_title(self) -> None:
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda *a: None
            mock_open.return_value.read.return_value = _SAMPLE_ATOM
            meta = _fetch_metadata_sync("2301.07041")
        assert meta["title"] == "Verifiable Fully Homomorphic Encryption"

    def test_parses_authors(self) -> None:
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda *a: None
            mock_open.return_value.read.return_value = _SAMPLE_ATOM
            meta = _fetch_metadata_sync("2301.07041")
        assert meta["authors"] == ["Alice Smith", "Bob Jones"]

    def test_parses_year(self) -> None:
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda *a: None
            mock_open.return_value.read.return_value = _SAMPLE_ATOM
            meta = _fetch_metadata_sync("2301.07041")
        assert meta["year"] == 2023

    def test_parses_abstract(self) -> None:
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda *a: None
            mock_open.return_value.read.return_value = _SAMPLE_ATOM
            meta = _fetch_metadata_sync("2301.07041")
        assert "FHE" in meta["abstract"]
        # El abstract debe estar limpio (sin espacios extra)
        assert not meta["abstract"].startswith(" ")

    def test_parses_doi(self) -> None:
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda *a: None
            mock_open.return_value.read.return_value = _SAMPLE_ATOM
            meta = _fetch_metadata_sync("2301.07041")
        assert meta["doi"] == "10.1234/test.doi"

    def test_parses_keywords(self) -> None:
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda *a: None
            mock_open.return_value.read.return_value = _SAMPLE_ATOM
            meta = _fetch_metadata_sync("2301.07041")
        assert "cs.CR" in meta["keywords"]
        assert "cs.LG" in meta["keywords"]

    def test_source_url_without_version(self) -> None:
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda *a: None
            mock_open.return_value.read.return_value = _SAMPLE_ATOM
            meta = _fetch_metadata_sync("2301.07041")
        # La URL canónica no debe tener versión
        assert meta["source_url"] == "https://arxiv.org/abs/2301.07041"
        assert "v2" not in meta["source_url"]

    def test_empty_feed_raises(self) -> None:
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda *a: None
            mock_open.return_value.read.return_value = _EMPTY_FEED
            with pytest.raises(ValueError, match="no devolvió resultados"):
                _fetch_metadata_sync("9999.99999")

    @pytest.mark.asyncio
    async def test_fetch_async_delegates_to_sync(self) -> None:
        with patch("adso.arxiv_client._fetch_metadata_sync", return_value={"title": "Test"}) as mock_sync:
            result = await fetch_arxiv_metadata("2301.07041")
        mock_sync.assert_called_once_with("2301.07041")
        assert result == {"title": "Test"}


# ---------------------------------------------------------------------------
# build_arxiv_classify_content
# ---------------------------------------------------------------------------

class TestBuildArxivClassifyContent:

    def _full_metadata(self) -> dict:
        return {
            "title": "Test Paper",
            "authors": ["Author A", "Author B", "Author C"],
            "year": 2024,
            "abstract": "This paper presents a method.",
            "doi": "10.1234/test",
            "keywords": ["cs.LG", "cs.AI"],
            "arxiv_id": "2401.00001",
            "source_url": "https://arxiv.org/abs/2401.00001",
        }

    def test_includes_title(self) -> None:
        content = build_arxiv_classify_content(self._full_metadata())
        assert "Test Paper" in content

    def test_includes_abstract(self) -> None:
        content = build_arxiv_classify_content(self._full_metadata())
        assert "This paper presents a method." in content

    def test_includes_authors_truncated_to_5(self) -> None:
        meta = self._full_metadata()
        meta["authors"] = [f"Author {i}" for i in range(10)]
        content = build_arxiv_classify_content(meta)
        assert "Author 0" in content
        assert "Author 5" not in content  # máx 5

    def test_includes_keywords(self) -> None:
        content = build_arxiv_classify_content(self._full_metadata())
        assert "cs.LG" in content

    def test_handles_missing_optional_fields(self) -> None:
        meta = {"title": "Solo título", "authors": [], "year": None,
                "abstract": "", "doi": None, "keywords": [],
                "arxiv_id": "", "source_url": ""}
        content = build_arxiv_classify_content(meta)
        assert "Solo título" in content
        # No debe fallar ni dejar campos vacíos visibles
        assert "None" not in content
        assert "ABSTRACT" not in content  # no agrega sección vacía


# ---------------------------------------------------------------------------
# build_arxiv_body
# ---------------------------------------------------------------------------

class TestBuildArxivBody:

    def _metadata(self) -> dict:
        return {
            "title": "Test Paper",
            "abstract": "This is the abstract text.",
            "keywords": ["cs.LG"],
        }

    def test_includes_abstract_literal(self) -> None:
        body = build_arxiv_body(self._metadata(), llm_summary=None)
        assert "## Abstract" in body
        assert "This is the abstract text." in body

    def test_includes_llm_summary_callout(self) -> None:
        body = build_arxiv_body(self._metadata(), llm_summary="Resumen del LLM.")
        assert "[!summary]" in body
        assert "Resumen del LLM." in body

    def test_no_summary_when_none(self) -> None:
        body = build_arxiv_body(self._metadata(), llm_summary=None)
        assert "[!summary]" not in body

    def test_includes_personal_notes_section(self) -> None:
        body = build_arxiv_body(self._metadata(), llm_summary=None)
        assert "## Personal Notes" in body

    def test_summary_before_abstract(self) -> None:
        body = build_arxiv_body(self._metadata(), llm_summary="Resumen.")
        assert body.index("[!summary]") < body.index("## Abstract")

    def test_abstract_before_personal_notes(self) -> None:
        body = build_arxiv_body(self._metadata(), llm_summary=None)
        assert body.index("## Abstract") < body.index("## Personal Notes")

    def test_empty_abstract(self) -> None:
        meta = {**self._metadata(), "abstract": ""}
        body = build_arxiv_body(meta, llm_summary=None)
        assert "## Personal Notes" in body  # no falla con abstract vacío
