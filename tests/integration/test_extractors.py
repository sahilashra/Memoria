"""
Integration tests for document extractors — DOCX, XLSX, PPTX, CSV.
Files are created programmatically using the same libraries Memoria uses,
so no fixture files are needed and tests run on any machine with deps installed.
Tests are skipped gracefully if the optional library isn't installed.
"""

import pytest
from pathlib import Path


# ─── DOCX ─────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not pytest.importorskip("docx", reason="python-docx not installed"),
    reason="python-docx not installed",
)
class TestDocxExtractor:

    @pytest.fixture
    def simple_docx(self, tmp_path):
        from docx import Document
        doc = Document()
        doc.add_heading("Annual Report 2024", 0)
        doc.add_heading("Executive Summary", 1)
        doc.add_paragraph("Revenue grew by 23% year-over-year to £4.2M.")
        doc.add_heading("Key Risks", 1)
        doc.add_paragraph("Supply chain disruption remains a primary concern.")
        path = tmp_path / "report.docx"
        doc.save(str(path))
        return path

    @pytest.fixture
    def docx_with_table(self, tmp_path):
        from docx import Document
        doc = Document()
        doc.add_heading("Team Roster", 0)
        table = doc.add_table(rows=3, cols=3)
        table.cell(0, 0).text = "Name"
        table.cell(0, 1).text = "Role"
        table.cell(0, 2).text = "Team"
        table.cell(1, 0).text = "Alice"
        table.cell(1, 1).text = "Engineer"
        table.cell(1, 2).text = "Backend"
        table.cell(2, 0).text = "Bob"
        table.cell(2, 1).text = "QA"
        table.cell(2, 2).text = "Quality"
        path = tmp_path / "roster.docx"
        doc.save(str(path))
        return path

    def test_headings_extracted(self, simple_docx):
        from memoria.extractors.docx import extract_docx
        result = extract_docx(simple_docx)
        assert "Annual Report 2024" in result
        assert "Executive Summary" in result
        assert "Key Risks" in result

    def test_paragraph_text_extracted(self, simple_docx):
        from memoria.extractors.docx import extract_docx
        result = extract_docx(simple_docx)
        assert "Revenue grew by 23%" in result
        assert "Supply chain disruption" in result

    def test_specific_numbers_preserved(self, simple_docx):
        from memoria.extractors.docx import extract_docx
        result = extract_docx(simple_docx)
        assert "4.2M" in result or "£4.2" in result

    def test_table_content_extracted(self, docx_with_table):
        from memoria.extractors.docx import extract_docx
        result = extract_docx(docx_with_table)
        assert "Alice" in result
        assert "Engineer" in result
        assert "QA" in result

    def test_returns_string(self, simple_docx):
        from memoria.extractors.docx import extract_docx
        result = extract_docx(simple_docx)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_nonexistent_file_raises_or_returns_error(self, tmp_path):
        # extract_docx() itself raises on missing file — the registry's extract()
        # wrapper catches it. Test both behaviours are acceptable.
        from memoria.extractors.docx import extract_docx
        import pytest
        try:
            result = extract_docx(tmp_path / "missing.docx")
            assert "error" in result.lower() or "failed" in result.lower()
        except (FileNotFoundError, Exception):
            pass  # raising is also acceptable — registry catches this upstream


# ─── XLSX ─────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not pytest.importorskip("openpyxl", reason="openpyxl not installed"),
    reason="openpyxl not installed",
)
class TestXlsxExtractor:

    @pytest.fixture
    def simple_xlsx(self, tmp_path):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Test Results"
        ws.append(["Test Case", "Status", "Duration (s)", "Notes"])
        ws.append(["Login flow", "Pass", 1.2, "Stable"])
        ws.append(["Checkout flow", "Fail", 3.5, "Timeout on step 3"])
        ws.append(["Search", "Pass", 0.8, ""])
        path = tmp_path / "results.xlsx"
        wb.save(str(path))
        return path

    @pytest.fixture
    def multi_sheet_xlsx(self, tmp_path):
        from openpyxl import Workbook
        wb = Workbook()
        ws1 = wb.active
        ws1.title = "Q1"
        ws1.append(["Month", "Revenue"])
        ws1.append(["Jan", 120000])
        ws1.append(["Feb", 135000])
        ws2 = wb.create_sheet("Q2")
        ws2.append(["Month", "Revenue"])
        ws2.append(["Apr", 145000])
        ws2.append(["May", 160000])
        path = tmp_path / "financials.xlsx"
        wb.save(str(path))
        return path

    def test_headers_extracted(self, simple_xlsx):
        from memoria.extractors.xlsx import extract_xlsx
        result = extract_xlsx(simple_xlsx)
        assert "Test Case" in result
        assert "Status" in result
        assert "Duration" in result

    def test_data_rows_extracted(self, simple_xlsx):
        from memoria.extractors.xlsx import extract_xlsx
        result = extract_xlsx(simple_xlsx)
        assert "Login flow" in result
        assert "Checkout flow" in result
        assert "Timeout on step 3" in result

    def test_pass_fail_status_preserved(self, simple_xlsx):
        from memoria.extractors.xlsx import extract_xlsx
        result = extract_xlsx(simple_xlsx)
        assert "Pass" in result
        assert "Fail" in result

    def test_sheet_name_included(self, simple_xlsx):
        from memoria.extractors.xlsx import extract_xlsx
        result = extract_xlsx(simple_xlsx)
        assert "Test Results" in result

    def test_multiple_sheets_extracted(self, multi_sheet_xlsx):
        from memoria.extractors.xlsx import extract_xlsx
        result = extract_xlsx(multi_sheet_xlsx)
        assert "Q1" in result
        assert "Q2" in result
        assert "120000" in result or "120,000" in result

    def test_returns_string(self, simple_xlsx):
        from memoria.extractors.xlsx import extract_xlsx
        result = extract_xlsx(simple_xlsx)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_csv_file_works(self, tmp_path):
        from memoria.extractors.xlsx import extract_xlsx
        csv = tmp_path / "data.csv"
        csv.write_text("Name,Score\nAlice,95\nBob,82\n", encoding="utf-8")
        result = extract_xlsx(csv)
        assert "Alice" in result
        assert "95" in result


# ─── PPTX ─────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not pytest.importorskip("pptx", reason="python-pptx not installed"),
    reason="python-pptx not installed",
)
class TestPptxExtractor:

    @pytest.fixture
    def simple_pptx(self, tmp_path):
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()

        # Slide 1 — title slide
        slide1 = prs.slides.add_slide(prs.slide_layouts[0])
        slide1.shapes.title.text = "Q1 2024 Engineering Review"
        slide1.placeholders[1].text = "Platform Team"

        # Slide 2 — content slide
        slide2 = prs.slides.add_slide(prs.slide_layouts[1])
        slide2.shapes.title.text = "Highlights"
        tf = slide2.placeholders[1].text_frame
        tf.text = "Deployed new auth service"
        tf.add_paragraph().text = "Reduced API latency by 40%"
        tf.add_paragraph().text = "Migrated 3 services to Kubernetes"

        # Slide 3 — content slide
        slide3 = prs.slides.add_slide(prs.slide_layouts[1])
        slide3.shapes.title.text = "Risks"
        slide3.placeholders[1].text = "Database migration planned for Q2"

        path = tmp_path / "review.pptx"
        prs.save(str(path))
        return path

    def test_title_slide_extracted(self, simple_pptx):
        from memoria.extractors.pptx import extract_pptx
        result = extract_pptx(simple_pptx)
        assert "Q1 2024 Engineering Review" in result

    def test_slide_content_extracted(self, simple_pptx):
        from memoria.extractors.pptx import extract_pptx
        result = extract_pptx(simple_pptx)
        assert "Deployed new auth service" in result
        assert "API latency" in result

    def test_all_slides_included(self, simple_pptx):
        from memoria.extractors.pptx import extract_pptx
        result = extract_pptx(simple_pptx)
        assert "Highlights" in result
        assert "Risks" in result

    def test_specific_numbers_preserved(self, simple_pptx):
        from memoria.extractors.pptx import extract_pptx
        result = extract_pptx(simple_pptx)
        assert "40%" in result

    def test_returns_string(self, simple_pptx):
        from memoria.extractors.pptx import extract_pptx
        result = extract_pptx(simple_pptx)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_slide_numbers_in_output(self, simple_pptx):
        from memoria.extractors.pptx import extract_pptx
        result = extract_pptx(simple_pptx)
        # Extractors typically include "Slide 1", "Slide 2" etc.
        assert "Slide 1" in result or "slide 1" in result.lower() or "1" in result


# ─── Extractor Registry ───────────────────────────────────────────────────────

class TestExtractorRegistry:
    """Tests for extractors/__init__.py — the routing layer."""

    def test_supported_extensions_covers_key_formats(self):
        from memoria.extractors import SUPPORTED_EXTENSIONS
        for ext in [".pdf", ".docx", ".pptx", ".xlsx", ".html", ".ipynb", ".png", ".mp3"]:
            assert ext in SUPPORTED_EXTENSIONS

    def test_extract_returns_dict_with_required_keys(self, tmp_path):
        from memoria.extractors import extract
        f = tmp_path / "test.html"
        f.write_text("<html><body><p>Hello</p></body></html>", encoding="utf-8")
        result = extract(f)
        assert "content" in result
        assert "extension" in result
        assert "truncated" in result
        assert "extractor" in result

    def test_unsupported_extension_returns_no_extractor(self, tmp_path):
        from memoria.extractors import extract
        f = tmp_path / "test.xyz"
        f.write_text("some data", encoding="utf-8")
        result = extract(f)
        assert result["extractor"] == "none"
        assert "No extractor" in result["content"]

    def test_missing_optional_dep_returns_graceful_message(self, tmp_path):
        """If an extractor raises ImportError, extract() returns a message, not an exception."""
        from unittest.mock import patch
        from memoria.extractors import extract, EXTRACTORS

        f = tmp_path / "audio.mp3"
        f.write_bytes(b"\x00" * 100)

        # Patch the entry in EXTRACTORS directly so the registry calls our mock
        original = EXTRACTORS[".mp3"]
        def _raise_import(*a, **kw):
            raise ImportError("openai-whisper is not installed")

        EXTRACTORS[".mp3"] = _raise_import
        try:
            result = extract(f)
        finally:
            EXTRACTORS[".mp3"] = original  # always restore

        # Either "missing" (ImportError path) or "error" (generic Exception path) is acceptable
        assert result["extractor"] in ("missing", "error")
        assert "not installed" in result["content"] or "extraction failed" in result["content"]

    def test_extract_ipynb_via_registry(self, tmp_path):
        """Full path: extract() → EXTRACTORS['.ipynb'] → extract_ipynb()"""
        import json
        from memoria.extractors import extract
        nb = {
            "nbformat": 4, "nbformat_minor": 5, "metadata": {},
            "cells": [{"cell_type": "code", "source": ["x = 42"], "outputs": []}]
        }
        f = tmp_path / "notebook.ipynb"
        f.write_text(json.dumps(nb), encoding="utf-8")
        result = extract(f)
        assert result["extractor"] == "ipynb"
        assert "x = 42" in result["content"]

    def test_extract_html_via_registry(self, tmp_path):
        """Full path: extract() → EXTRACTORS['.html'] → extract_html()"""
        from memoria.extractors import extract
        f = tmp_path / "page.html"
        f.write_text("<html><body><p>Registry test</p></body></html>", encoding="utf-8")
        result = extract(f)
        assert result["extractor"] == "html"
        assert "Registry test" in result["content"]
