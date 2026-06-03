"""
HTML Extractor — pulls readable text from .html / .htm files.
Designed for Confluence exports, Notion exports, internal wikis, docs sites.
Uses Python's stdlib html.parser — no extra dependencies.
Falls back to BeautifulSoup if available for better quality.
"""

from pathlib import Path
import re

MAX_CHARS = 40000


def extract_html(file_path: Path) -> str:
    """Extract clean readable text from an HTML file."""
    raw = file_path.read_text(encoding="utf-8", errors="replace")

    # Try BeautifulSoup first — better at preserving structure
    try:
        from bs4 import BeautifulSoup
        return _extract_with_bs4(raw, file_path.name)
    except ImportError:
        return _extract_with_stdlib(raw, file_path.name)


def _extract_with_bs4(raw: str, filename: str) -> str:
    """High-quality extraction using BeautifulSoup."""
    from bs4 import BeautifulSoup, Comment

    soup = BeautifulSoup(raw, "html.parser")

    # Remove noise: scripts, styles, nav, footer, ads
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "noscript", "iframe", "form"]):
        tag.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    lines = []

    # Walk the tree preserving heading structure
    for el in soup.find_all(True):
        tag = el.name
        text = el.get_text(separator=" ", strip=True)
        if not text:
            continue

        if tag == "h1":
            lines.append(f"\n# {text}")
        elif tag == "h2":
            lines.append(f"\n## {text}")
        elif tag == "h3":
            lines.append(f"\n### {text}")
        elif tag in ("h4", "h5", "h6"):
            lines.append(f"\n#### {text}")
        elif tag == "p":
            lines.append(text)
        elif tag in ("li",):
            lines.append(f"- {text}")
        elif tag in ("th", "td"):
            pass  # handled at table level below
        elif tag == "tr":
            cells = [td.get_text(strip=True) for td in el.find_all(["td", "th"])]
            if cells:
                lines.append(" | ".join(cells))
        elif tag == "pre":
            lines.append(f"```\n{text}\n```")

    # Deduplicate consecutive blank lines
    text = "\n".join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    if not text:
        return f"[{filename}: no readable text found]"

    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[... truncated ...]"

    return text


def _extract_with_stdlib(raw: str, filename: str) -> str:
    """Fallback: stdlib html.parser — no extra dependencies."""
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        SKIP_TAGS = {"script", "style", "nav", "footer", "header",
                     "aside", "noscript", "iframe"}

        def __init__(self):
            super().__init__()
            self.chunks = []
            self._skip_depth = 0
            self._current_tag = ""

        def handle_starttag(self, tag, attrs):
            self._current_tag = tag
            if tag in self.SKIP_TAGS:
                self._skip_depth += 1

        def handle_endtag(self, tag):
            if tag in self.SKIP_TAGS and self._skip_depth > 0:
                self._skip_depth -= 1

        def handle_data(self, data):
            if self._skip_depth > 0:
                return
            text = data.strip()
            if text:
                self.chunks.append(text)

    parser = _TextExtractor()
    parser.feed(raw)

    # Strip leftover HTML entities
    text = " ".join(parser.chunks)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()

    if not text:
        return f"[{filename}: no readable text found]"

    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[... truncated ...]"

    return text
