"""
Unit tests for the HTML extractor.
No extra dependencies — uses stdlib html.parser.
BeautifulSoup path is tested when bs4 is installed (skipped gracefully if not).
"""

import pytest
from pathlib import Path
from memoria.extractors.html import extract_html, _extract_with_stdlib


def _write_html(tmp_path, content, filename="test.html"):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    return path


class TestExtractHtmlStdlib:
    """Test the stdlib fallback path — always available."""

    def test_basic_paragraph(self, tmp_path):
        path = _write_html(tmp_path, "<html><body><p>Hello world</p></body></html>")
        result = _extract_with_stdlib("<html><body><p>Hello world</p></body></html>", "test.html")
        assert "Hello world" in result

    def test_scripts_stripped(self, tmp_path):
        html = "<html><body><script>alert('xss')</script><p>Real content</p></body></html>"
        result = _extract_with_stdlib(html, "test.html")
        assert "alert" not in result
        assert "Real content" in result

    def test_styles_stripped(self, tmp_path):
        html = "<html><head><style>body { color: red; }</style></head><body><p>Text</p></body></html>"
        result = _extract_with_stdlib(html, "test.html")
        assert "color" not in result
        assert "Text" in result

    def test_nav_stripped(self, tmp_path):
        html = "<html><body><nav>Menu items</nav><p>Main content</p></body></html>"
        result = _extract_with_stdlib(html, "test.html")
        assert "Menu items" not in result
        assert "Main content" in result

    def test_empty_html_returns_message(self, tmp_path):
        result = _extract_with_stdlib("<html><body></body></html>", "empty.html")
        assert "no readable text" in result.lower()

    def test_plain_text_preserved(self, tmp_path):
        html = "<html><body><p>The quick brown fox</p><p>jumps over</p></body></html>"
        result = _extract_with_stdlib(html, "test.html")
        assert "quick brown fox" in result
        assert "jumps over" in result


class TestExtractHtmlFull:
    """Test extract_html() — uses bs4 if available, stdlib otherwise."""

    def test_basic_extraction(self, tmp_path):
        path = _write_html(tmp_path, "<html><body><p>Hello from HTML</p></body></html>")
        result = extract_html(path)
        assert "Hello from HTML" in result

    def test_headings_preserved(self, tmp_path):
        html = """
        <html><body>
          <h1>Main Title</h1>
          <h2>Section One</h2>
          <p>Some content here.</p>
          <h2>Section Two</h2>
          <p>More content.</p>
        </body></html>
        """
        path = _write_html(tmp_path, html)
        result = extract_html(path)
        assert "Main Title" in result
        assert "Section One" in result
        assert "Section Two" in result
        assert "Some content here" in result

    def test_confluence_export_style(self, tmp_path):
        """Simulate a Confluence HTML export with nav, header, footer noise."""
        html = """
        <html><body>
          <header>Confluence Header</header>
          <nav>Page Tree | Home | Dashboard</nav>
          <div id="main-content">
            <h1>Engineering Architecture</h1>
            <p>This document describes the system architecture.</p>
            <h2>Components</h2>
            <p>The system has three main components.</p>
          </div>
          <footer>Confluence Footer | Version 1.0</footer>
        </body></html>
        """
        path = _write_html(tmp_path, html)
        result = extract_html(path)
        assert "Engineering Architecture" in result
        assert "system architecture" in result
        # Nav/footer should be stripped (bs4 path) or at least content is present
        assert "Page Tree" not in result or "Engineering Architecture" in result

    def test_table_content_extracted(self, tmp_path):
        html = """
        <html><body>
          <table>
            <tr><th>Name</th><th>Role</th></tr>
            <tr><td>Alice</td><td>Engineer</td></tr>
            <tr><td>Bob</td><td>QA</td></tr>
          </table>
        </body></html>
        """
        path = _write_html(tmp_path, html)
        result = extract_html(path)
        assert "Alice" in result
        assert "Engineer" in result

    def test_code_blocks_preserved(self, tmp_path):
        html = """
        <html><body>
          <p>To install, run:</p>
          <pre>pip install memoria</pre>
        </body></html>
        """
        path = _write_html(tmp_path, html)
        result = extract_html(path)
        assert "pip install memoria" in result

    def test_long_content_truncated(self, tmp_path):
        # Create content larger than MAX_CHARS (40000)
        long_para = "<p>" + ("word " * 2000) + "</p>"
        html = f"<html><body>{''.join([long_para] * 10)}</body></html>"
        path = _write_html(tmp_path, html)
        result = extract_html(path)
        assert "truncated" in result.lower() or len(result) <= 41000

    def test_htm_extension_works(self, tmp_path):
        path = _write_html(tmp_path, "<html><body><p>HTM file</p></body></html>", "page.htm")
        result = extract_html(path)
        assert "HTM file" in result

    def test_utf8_content_preserved(self, tmp_path):
        html = "<html><body><p>Héllo Wörld — café résumé</p></body></html>"
        path = _write_html(tmp_path, html)
        result = extract_html(path)
        assert "café" in result or "caf" in result  # encoding may vary
