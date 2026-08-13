"""Tests unitarios para adso.document_extractor — PDF + texto."""

from __future__ import annotations

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from adso.document_extractor import (
    _clean_formula_blocks,
    _extract_title_from_text,
    _find_section_boundaries,
    build_classify_content,
    detect_paper,
    extract_paper_sections,
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


# ---------------------------------------------------------------------------
# I4 de docs/audit-2026-07-31.md — este módulo parsea el input MENOS confiable
# del sistema (PDFs de terceros) y era el peor cubierto del repo (32%). Lo que
# sigue cubre el pipeline de papers: detección, límites de sección, extracción
# y armado del contenido que termina dentro de <input> en el prompt del LLM.
# ---------------------------------------------------------------------------


class TestDetectPaper:

    def test_dos_senales_es_paper(self) -> None:
        assert detect_paper("Abstract\n\nWe introduce...\n\nReferences", {}) is True

    def test_una_senal_no_alcanza(self) -> None:
        assert detect_paper("Introduction to my shopping list", {}) is False

    def test_texto_sin_senales(self) -> None:
        assert detect_paper("Comprar leche y pan. Llamar al dentista.", {}) is False

    def test_doi_cuenta_como_senal(self) -> None:
        assert detect_paper("10.1234/abcd.2024 — Introduction", {}) is True

    def test_solo_mira_los_primeros_5000_chars(self) -> None:
        """Señales enterradas después del sample no cuentan.

        Es deliberado (costo), pero fija el contrato: un PDF cuyo abstract
        aparece en la página 3 no se detecta como paper.
        """
        texto = ("x" * 5000) + "\nAbstract\nReferences\nIntroduction"
        assert detect_paper(texto, {}) is False


class TestExtractTitleFromText:

    def test_salta_headers_de_journal_y_toma_el_titulo(self) -> None:
        lines = [
            "Monthly Notices of the Royal Astronomical Society",
            "Accepted 2024 March 15",
            "A Deep Learning Approach to Variable Star Classification",
            "John Doe, Jane Roe",
        ]
        assert _extract_title_from_text(lines) == (
            "A Deep Learning Approach to Variable Star Classification"
        )

    def test_acronimo_corto_se_une_con_la_linea_siguiente(self) -> None:
        lines = ["ASTROMER", "A Transformer Based Embedding for Light Curves"]
        assert _extract_title_from_text(lines) == (
            "ASTROMER: A Transformer Based Embedding for Light Curves"
        )

    def test_sin_candidatos_devuelve_vacio(self) -> None:
        assert _extract_title_from_text(["", "  ", "corto"]) == ""

    def test_salta_lineas_con_anio(self) -> None:
        """Un año de 4 dígitos marca header de journal o fecha, no título."""
        lines = ["Astrophysical Journal 2023", "Neural Networks for Photometry"]
        assert _extract_title_from_text(lines) == "Neural Networks for Photometry"

    def test_respeta_max_lines(self) -> None:
        lines = ["©"] * 10 + ["Un Titulo Perfectamente Valido Y Largo"]
        assert _extract_title_from_text(lines, max_lines=5) == ""

    def test_descarta_lineas_demasiado_largas(self) -> None:
        assert _extract_title_from_text(["z" * 300]) == ""


class TestCleanFormulaBlocks:

    def test_texto_normal_no_se_toca(self) -> None:
        texto = "Este es un párrafo normal de un paper.\nSigue en otra línea."
        assert _clean_formula_blocks(texto) == texto

    def test_bloque_con_numero_de_ecuacion_se_reemplaza(self) -> None:
        texto = "Definimos la pérdida:\nL = ∑ xi\n(1)\nDonde xi es la entrada."
        out = _clean_formula_blocks(texto)
        assert "> [mathematical content — see PDF]" in out
        assert "Donde xi es la entrada." in out

    def test_run_de_simbolos_sin_ancla_se_reemplaza(self) -> None:
        texto = "Intro\nα ≈ β\n∑ ∂x\n∇ · F\nTexto final que sobrevive."
        out = _clean_formula_blocks(texto)
        assert "> [mathematical content — see PDF]" in out
        assert "Texto final que sobrevive." in out

    def test_run_corto_sin_ancla_no_se_reemplaza(self) -> None:
        """Menos de 3 líneas seguidas no es un bloque de fórmula."""
        texto = "Intro\nα ≈ β\nTexto normal que sigue."
        assert "mathematical content" not in _clean_formula_blocks(texto)

    def test_bloque_contiguo_produce_un_solo_placeholder(self) -> None:
        texto = "Intro\n∑ a\n∂ b\n∇ c\n∫ d\nFin."
        assert _clean_formula_blocks(texto).count("mathematical content") == 1


class TestFindSectionBoundaries:

    def test_headers_simples(self) -> None:
        lines = ["Abstract", "bla", "Introduction", "bla", "References"]
        b = _find_section_boundaries(lines)
        assert b["abstract"] == 0
        assert b["introduction"] == 2
        assert b["references"] == 4

    def test_headers_numerados_y_romanos(self) -> None:
        lines = ["2. Methods", "bla", "IV. Conclusions"]
        b = _find_section_boundaries(lines)
        assert b["methods"] == 0
        assert b["conclusions"] == 2

    def test_ignora_lineas_largas(self) -> None:
        """Un header real es corto; un párrafo que empieza igual, no."""
        lines = ["Methods " + "x" * 90]
        assert _find_section_boundaries(lines) == {}

    def test_se_queda_con_la_primera_aparicion(self) -> None:
        lines = ["Abstract", "bla", "Abstract"]
        assert _find_section_boundaries(lines)["abstract"] == 0

    def test_variantes_de_keywords(self) -> None:
        assert "keywords" in _find_section_boundaries(["Index Terms"])
        assert "keywords" in _find_section_boundaries(["Palabras clave"])


class TestExtractPaperSections:

    def _paper(self) -> str:
        return "\n".join([
            "Abstract",
            "Presentamos un método nuevo para clasificar curvas de luz.",
            "Keywords",
            "machine learning, astronomy, time series",
            "1. Introduction",
            "El problema es viejo.",
            "2. Methods",
            "Usamos un transformer sobre la serie temporal.",
            "5. Conclusions",
            "El método funciona mejor que la línea de base.",
            "References",
            "[1] Alguien 2020",
        ])

    def test_extrae_todas_las_secciones(self) -> None:
        s = extract_paper_sections(self._paper(), {})
        assert "curvas de luz" in s["abstract"]
        assert "machine learning" in s["keywords"]
        assert "transformer" in s["methods"]
        assert "línea de base" in s["conclusions"]

    def test_seccion_termina_en_el_header_siguiente(self) -> None:
        """El abstract no debe arrastrar el contenido de Methods."""
        s = extract_paper_sections(self._paper(), {})
        assert "transformer" not in s["abstract"]
        assert "Keywords" not in s["abstract"]

    def test_referencias_no_se_cuelan_en_conclusions(self) -> None:
        s = extract_paper_sections(self._paper(), {})
        assert "Alguien 2020" not in s["conclusions"]

    def test_titulo_y_autores_de_metadata(self) -> None:
        s = extract_paper_sections(
            self._paper(), {"title": "  Mi Paper  ", "author": " Nieto, L. "}
        )
        assert s["title"] == "Mi Paper"
        assert s["authors"] == "Nieto, L."

    def test_titulo_cae_al_texto_si_metadata_esta_vacia(self) -> None:
        """Caso típico de arXiv: el PDF no trae título en metadata."""
        texto = "Un Titulo Inferido Desde El Cuerpo\n\nAbstract\nbla\nReferences\nx"
        assert extract_paper_sections(texto, {"title": ""})["title"] == (
            "Un Titulo Inferido Desde El Cuerpo"
        )

    def test_doi_de_metadata_gana_sobre_el_texto(self) -> None:
        s = extract_paper_sections("10.9999/delTexto", {"doi": "10.1111/deMetadata"})
        assert s["doi"] == "10.1111/deMetadata"

    def test_doi_se_busca_en_el_texto_si_falta(self) -> None:
        s = extract_paper_sections("DOI: 10.1234/paper.2024.", {})
        assert s["doi"] == "10.1234/paper.2024"

    def test_abstract_inline_como_fallback(self) -> None:
        """Sin header propio: 'Abstract— texto' en una sola línea."""
        s = extract_paper_sections("Abstract— Un resumen corto del trabajo.", {})
        assert "Un resumen corto del trabajo." in s["abstract"]

    def test_keywords_inline_como_fallback(self) -> None:
        s = extract_paper_sections("Key words: galaxias, fotometría", {})
        assert "galaxias" in s["keywords"]

    def test_secciones_ausentes_son_string_vacio(self) -> None:
        s = extract_paper_sections("Un texto cualquiera sin estructura.", {})
        assert s["abstract"] == ""
        assert s["methods"] == ""
        assert s["conclusions"] == ""

    def test_seccion_larga_se_trunca_al_limite(self) -> None:
        largo = "\n".join(["Abstract"] + ["palabra " * 20] * 40 + ["References", "x"])
        abstract = extract_paper_sections(largo, {})["abstract"]
        assert len(abstract) <= 1500 + 3
        assert abstract.endswith("...")


class TestBuildClassifyContent:

    def test_paper_arma_bloques_etiquetados(self) -> None:
        texto = "\n".join([
            "Abstract", "Un resumen.", "Keywords", "ml, astro",
            "2. Methods", "Un método.", "5. Conclusions", "Una conclusión.",
        ])
        out = build_classify_content(texto, {"title": "T"}, is_paper=True)
        assert "TÍTULO: T" in out
        assert "ABSTRACT:" in out and "Un resumen." in out
        assert "KEYWORDS: ml, astro" in out
        assert "METHODS:" in out and "CONCLUSIONS:" in out

    def test_paper_omite_secciones_vacias(self) -> None:
        out = build_classify_content("Abstract\nSolo esto.", {"title": ""}, is_paper=True)
        assert "KEYWORDS" not in out
        assert "METHODS" not in out

    def test_generico_corto_pasa_entero(self) -> None:
        texto = "Un documento corto cualquiera."
        assert build_classify_content(texto, {}, is_paper=False) == texto

    def test_generico_largo_trunca_inicio_y_fin(self) -> None:
        texto = "A" * 3000 + "B" * 3000
        out = build_classify_content(texto, {}, is_paper=False)
        assert "[...]" in out
        assert out.startswith("A" * 100)
        assert out.endswith("B" * 100)
        assert len(out) < len(texto)

    def test_generico_en_el_limite_no_trunca(self) -> None:
        texto = "x" * 3500
        assert build_classify_content(texto, {}, is_paper=False) == texto


class TestExtractPdfPathsDeError:
    """PDFs que fallan a mitad de la extracción (cifrados, corruptos).

    `pymupdf.open()` acepta un PDF cifrado sin chistar; lo que explota es el
    primer `page.get_text()`. Ese path no estaba cubierto y tenía dos
    problemas (F9 de docs/audit-2026-07-31.md): el `doc.close()` quedaba
    fuera del alcance de la excepción —un `Document` filtrado por cada PDF
    cifrado que entra al bot— y la excepción cruda de pymupdf salía tal cual
    hacia el handler en vez del `RuntimeError` que documenta la firma.
    """

    def _mock_pymupdf(self, error: Exception) -> tuple[MagicMock, MagicMock]:
        mock_pymupdf = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {}
        mock_doc.page_count = 1

        page = MagicMock()
        page.get_text.side_effect = error
        mock_doc.__iter__ = lambda self: iter([page])
        mock_pymupdf.open.return_value = mock_doc
        return mock_pymupdf, mock_doc

    @pytest.mark.asyncio
    async def test_pdf_cifrado_da_runtime_error(self, tmp_path: Path) -> None:
        pdf = tmp_path / "cifrado.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        mock_pymupdf, _ = self._mock_pymupdf(ValueError("document closed or encrypted"))

        with patch.dict(sys.modules, {"pymupdf": mock_pymupdf}):
            with pytest.raises(RuntimeError, match="No se pudo leer"):
                await extract_pdf(pdf)

    @pytest.mark.asyncio
    async def test_documento_se_cierra_aunque_falle_la_extraccion(
        self, tmp_path: Path
    ) -> None:
        """Sin esto, cada PDF cifrado deja un Document abierto en la RPi4."""
        pdf = tmp_path / "cifrado.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        mock_pymupdf, mock_doc = self._mock_pymupdf(ValueError("encrypted"))

        with patch.dict(sys.modules, {"pymupdf": mock_pymupdf}):
            with pytest.raises(RuntimeError):
                await extract_pdf(pdf)

        mock_doc.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_documento_se_cierra_en_el_camino_feliz(self, tmp_path: Path) -> None:
        pdf = tmp_path / "ok.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        mock_pymupdf = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {}
        mock_doc.page_count = 1
        page = MagicMock()
        page.get_text.return_value = "texto"
        mock_doc.__iter__ = lambda self: iter([page])
        mock_pymupdf.open.return_value = mock_doc

        with patch.dict(sys.modules, {"pymupdf": mock_pymupdf}):
            await extract_pdf(pdf)

        mock_doc.close.assert_called_once()
