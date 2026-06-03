"""
Unit tests for content type detection.
Tests _detect_content_type() and _peek_dir_type() — pure logic, no files, no API.
"""

import pytest
from pathlib import Path
from memoria.generator import _detect_content_type


class TestDetectContentType:
    """Tests for _detect_content_type(files: dict) -> str"""

    def test_python_files_returns_code(self):
        files = {"main.py": {}, "utils.py": {}, "requirements.txt": {}}
        assert _detect_content_type(files) == "code"

    def test_javascript_files_returns_code(self):
        files = {"index.js": {}, "app.ts": {}, "package.json": {}}
        assert _detect_content_type(files) == "code"

    def test_single_audio_returns_audio(self):
        files = {"standup.m4a": {}}
        assert _detect_content_type(files) == "audio"

    def test_single_mp3_returns_audio(self):
        files = {"recording.mp3": {}}
        assert _detect_content_type(files) == "audio"

    def test_multiple_audio_returns_code(self):
        # Only single audio file gets the 'audio' label
        files = {"recording1.mp3": {}, "recording2.mp3": {}}
        # Multiple audio files fall through to 'code' (treated as mixed)
        result = _detect_content_type(files)
        assert result in ("code", "document")  # not "audio"

    def test_single_image_returns_image(self):
        files = {"diagram.png": {}}
        assert _detect_content_type(files) == "image"

    def test_single_jpg_returns_image(self):
        files = {"photo.jpg": {}}
        assert _detect_content_type(files) == "image"

    def test_pdf_collection_returns_document(self):
        files = {"report.pdf": {}, "spec.pdf": {}, "notes.pdf": {}}
        assert _detect_content_type(files) == "document"

    def test_docx_collection_returns_document(self):
        files = {"report.docx": {}, "proposal.docx": {}}
        assert _detect_content_type(files) == "document"

    def test_mixed_docs_returns_document(self):
        files = {"report.pdf": {}, "slides.pptx": {}, "data.xlsx": {}}
        assert _detect_content_type(files) == "document"

    def test_notebook_returns_notebook(self):
        files = {"analysis.ipynb": {}}
        assert _detect_content_type(files) == "notebook"

    def test_notebook_with_doc_returns_notebook(self):
        # Notebook + some docs, no code
        files = {"analysis.ipynb": {}, "data.csv": {}}
        assert _detect_content_type(files) == "notebook"

    def test_code_with_docs_returns_code(self):
        # Code takes priority over docs when mixed
        files = {"main.py": {}, "report.pdf": {}}
        assert _detect_content_type(files) == "code"

    def test_empty_files_returns_code(self):
        # Default fallback
        assert _detect_content_type({}) == "code"

    def test_html_collection_returns_code(self):
        # .html is in CODE_EXTENSIONS — markup is treated as code/web content,
        # not as a document. A folder of HTML wiki exports mixed with .pdf/.docx
        # would still read as 'code' because code signals take priority.
        files = {"page.html": {}, "index.htm": {}}
        assert _detect_content_type(files) == "code"

    def test_go_files_returns_code(self):
        files = {"main.go": {}, "server.go": {}}
        assert _detect_content_type(files) == "code"

    def test_rust_files_returns_code(self):
        files = {"main.rs": {}, "lib.rs": {}}
        assert _detect_content_type(files) == "code"


class TestPeekDirType:
    """Tests for _peek_dir_type(path: Path) -> str — CLI helper"""

    def test_python_project_returns_code(self, tmp_path):
        from memoria.cli import _peek_dir_type
        (tmp_path / "main.py").touch()
        (tmp_path / "requirements.txt").touch()
        assert _peek_dir_type(tmp_path) == "code"

    def test_git_repo_returns_code(self, tmp_path):
        from memoria.cli import _peek_dir_type
        (tmp_path / ".git").mkdir()
        assert _peek_dir_type(tmp_path) == "code"

    def test_package_json_returns_code(self, tmp_path):
        from memoria.cli import _peek_dir_type
        (tmp_path / "package.json").touch()
        assert _peek_dir_type(tmp_path) == "code"

    def test_pdf_folder_returns_document(self, tmp_path):
        from memoria.cli import _peek_dir_type
        (tmp_path / "report.pdf").touch()
        (tmp_path / "notes.pdf").touch()
        assert _peek_dir_type(tmp_path) == "document"

    def test_docx_folder_returns_document(self, tmp_path):
        from memoria.cli import _peek_dir_type
        (tmp_path / "report.docx").touch()
        assert _peek_dir_type(tmp_path) == "document"

    def test_empty_folder_returns_unknown(self, tmp_path):
        from memoria.cli import _peek_dir_type
        assert _peek_dir_type(tmp_path) == "unknown"

    def test_docs_in_subdir_returns_document(self, tmp_path):
        from memoria.cli import _peek_dir_type
        sub = tmp_path / "certs"
        sub.mkdir()
        (sub / "cert.pdf").touch()
        assert _peek_dir_type(tmp_path) == "document"

    def test_nonexistent_path_returns_unknown(self, tmp_path):
        from memoria.cli import _peek_dir_type
        assert _peek_dir_type(tmp_path / "does_not_exist") == "unknown"
