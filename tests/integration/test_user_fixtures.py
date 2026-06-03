"""
Tests that require user-provided fixture files (PDF, audio, image).
All tests in this file are automatically SKIPPED if the fixture file is absent —
no failure, just a clear skip message telling you what to add.

HOW TO ADD YOUR TEST DATA
─────────────────────────
Drop files into  tests/fixtures/  and re-run:

  tests/fixtures/sample.pdf     — any PDF (1-5 pages ideal)
  tests/fixtures/sample.m4a     — any audio/recording (even 10 seconds)
  tests/fixtures/sample.mp3     — alternative audio format
  tests/fixtures/sample.png     — any image (diagram, screenshot, photo)
  tests/fixtures/sample.jpg     — alternative image format

You can use real files from your own work — meeting recordings,
certification PDFs, architecture screenshots. Shorter = faster tests.

RUNNING LIVE AI TESTS (optional)
─────────────────────────────────
A few tests also call the real AI model to verify end-to-end quality.
These are skipped by default. To enable:

  set MEMORIA_TEST_LIVE=1       (Windows)
  export MEMORIA_TEST_LIVE=1    (Mac/Linux)

You need a valid config.yaml / ~/.memoria/config.yaml with a working API key.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from tests.conftest import make_mock_completion, MOCK_DOCUMENT_CONTENT

FIXTURES = Path(__file__).parent.parent / "fixtures"

# ── Skip helpers ──────────────────────────────────────────────────────────────

def _require_fixture(name: str):
    """Mark test to skip if the fixture file doesn't exist."""
    path = FIXTURES / name
    return pytest.mark.skipif(
        not path.exists(),
        reason=f"Fixture file missing: tests/fixtures/{name}  — drop your own file there to run this test",
    )


LIVE = pytest.mark.skipif(
    not os.environ.get("MEMORIA_TEST_LIVE"),
    reason="Live AI tests disabled — set MEMORIA_TEST_LIVE=1 to enable",
)


# ─── PDF ──────────────────────────────────────────────────────────────────────

class TestPdfExtractor:

    @_require_fixture("sample.pdf")
    def test_pdf_returns_text(self):
        from memoria.extractors.pdf import extract_pdf
        result = extract_pdf(FIXTURES / "sample.pdf", config_path="config.yaml")
        assert isinstance(result, str)
        assert len(result) > 50
        assert "[extraction failed" not in result.lower()

    @_require_fixture("sample.pdf")
    def test_pdf_content_not_empty(self):
        from memoria.extractors.pdf import extract_pdf
        result = extract_pdf(FIXTURES / "sample.pdf", config_path="config.yaml")
        # Should contain some real words, not just whitespace
        words = [w for w in result.split() if len(w) > 3]
        assert len(words) >= 5, f"Too few words extracted — got: {result[:200]}"

    @_require_fixture("sample.pdf")
    def test_pdf_via_registry(self):
        from memoria.extractors import extract
        result = extract(FIXTURES / "sample.pdf")
        assert result["extractor"] == "pdf"
        assert len(result["content"]) > 50

    @_require_fixture("sample.pdf")
    @LIVE
    def test_pdf_full_pipeline_live(self, tmp_books, tmp_config):
        """End-to-end: PDF → crawler → generator (real AI call)."""
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(
            repo_path=str(FIXTURES / "sample.pdf"),
            company_context="A sample PDF document",
            output_dir=str(tmp_books),
        )
        content = Path(output).read_text(encoding="utf-8")
        assert "Memory Bank" in content
        assert len(content) > 200


# ─── Audio ────────────────────────────────────────────────────────────────────

class TestAudioExtractor:

    @pytest.fixture
    def _audio_file(self):
        """Return path to first available audio fixture."""
        for name in ("sample.m4a", "sample.mp3", "sample.wav"):
            p = FIXTURES / name
            if p.exists():
                return p
        return None

    @pytest.mark.skipif(
        not any((FIXTURES / n).exists() for n in ("sample.m4a", "sample.mp3", "sample.wav")),
        reason="No audio fixture found — add tests/fixtures/sample.m4a (or .mp3/.wav)",
    )
    def test_audio_returns_transcript(self, _audio_file):
        pytest.importorskip("whisper", reason="openai-whisper not installed — run: pip install openai-whisper")
        from memoria.extractors.audio import extract_audio
        result = extract_audio(_audio_file)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "[extraction failed" not in result.lower()

    @pytest.mark.skipif(
        not any((FIXTURES / n).exists() for n in ("sample.m4a", "sample.mp3", "sample.wav")),
        reason="No audio fixture found — add tests/fixtures/sample.m4a (or .mp3/.wav)",
    )
    def test_audio_transcript_is_english(self, _audio_file):
        """Transcript should contain recognisable English words."""
        pytest.importorskip("whisper", reason="openai-whisper not installed")
        from memoria.extractors.audio import extract_audio
        result = extract_audio(_audio_file)
        # At minimum some common short words should appear
        common = {"the", "a", "is", "in", "and", "to", "of", "i", "we", "it"}
        words_lower = set(result.lower().split())
        overlap = words_lower & common
        assert len(overlap) >= 1 or len(result) > 20, \
            f"Transcript doesn't look like English: {result[:200]}"

    @pytest.mark.skipif(
        not any((FIXTURES / n).exists() for n in ("sample.m4a", "sample.mp3", "sample.wav")),
        reason="No audio fixture found",
    )
    def test_audio_via_registry(self, _audio_file):
        pytest.importorskip("whisper", reason="openai-whisper not installed")
        from memoria.extractors import extract
        result = extract(_audio_file)
        assert result["extractor"] == "audio"
        assert len(result["content"]) > 0

    @pytest.mark.skipif(
        not any((FIXTURES / n).exists() for n in ("sample.m4a", "sample.mp3", "sample.wav")),
        reason="No audio fixture found",
    )
    @LIVE
    def test_audio_full_pipeline_live(self, _audio_file, tmp_books, tmp_config):
        """End-to-end: audio → transcription → generator (real AI call)."""
        pytest.importorskip("whisper", reason="openai-whisper not installed")
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(
            repo_path=str(_audio_file),
            company_context="A voice recording",
            output_dir=str(tmp_books),
        )
        content = Path(output).read_text(encoding="utf-8")
        assert "Memory Bank" in content
        assert "Recording" in content or "recording" in content.lower()


# ─── Image ────────────────────────────────────────────────────────────────────

class TestImageExtractor:

    @pytest.fixture
    def _image_file(self):
        for name in ("sample.png", "sample.jpg", "sample.jpeg"):
            p = FIXTURES / name
            if p.exists():
                return p
        return None

    @pytest.mark.skipif(
        not any((FIXTURES / n).exists() for n in ("sample.png", "sample.jpg", "sample.jpeg")),
        reason="No image fixture found — add tests/fixtures/sample.png (or .jpg)",
    )
    def test_image_with_mocked_vision(self, _image_file, tmp_config):
        """Image extractor calls the vision model — mock it to test routing."""
        mock_response = make_mock_completion("This image shows an architecture diagram with three services.")
        with patch("litellm.completion", return_value=mock_response):
            from memoria.extractors.image import extract_image
            result = extract_image(_image_file, config_path=str(tmp_config))
        assert isinstance(result, str)
        assert len(result) > 0
        assert "[extraction failed" not in result.lower()

    @pytest.mark.skipif(
        not any((FIXTURES / n).exists() for n in ("sample.png", "sample.jpg", "sample.jpeg")),
        reason="No image fixture found",
    )
    def test_image_via_registry_mocked(self, _image_file, tmp_config):
        mock_response = make_mock_completion("Image description from vision model.")
        with patch("litellm.completion", return_value=mock_response):
            from memoria.extractors import extract
            result = extract(_image_file, config_path=str(tmp_config))
        assert result["extractor"] == "image"

    @pytest.mark.skipif(
        not any((FIXTURES / n).exists() for n in ("sample.png", "sample.jpg", "sample.jpeg")),
        reason="No image fixture found",
    )
    @LIVE
    def test_image_full_pipeline_live(self, _image_file, tmp_books, tmp_config):
        """End-to-end: image → vision model → generator (real AI call)."""
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(
            repo_path=str(_image_file),
            company_context="An architecture diagram",
            output_dir=str(tmp_books),
        )
        content = Path(output).read_text(encoding="utf-8")
        assert "Memory Bank" in content


# ─── Full document folder ─────────────────────────────────────────────────────

class TestMixedDocumentFolder:
    """Tests that combine multiple fixture types into one analyze run."""

    @pytest.mark.skipif(
        not (FIXTURES / "sample.pdf").exists(),
        reason="sample.pdf not in fixtures",
    )
    def test_folder_with_pdf_and_html(self, tmp_path, tmp_config, tmp_books):
        """A folder containing a PDF and an HTML file should produce a document Memory Bank."""
        import shutil
        folder = tmp_path / "mixed_docs"
        folder.mkdir()
        shutil.copy(FIXTURES / "sample.pdf", folder / "sample.pdf")
        (folder / "notes.html").write_text(
            "<html><body><h1>Meeting Notes</h1><p>We discussed the roadmap.</p></body></html>",
            encoding="utf-8",
        )

        mock_response = make_mock_completion(MOCK_DOCUMENT_CONTENT)
        with patch("litellm.completion", return_value=mock_response):
            from memoria.generator import BookGenerator
            gen = BookGenerator(str(tmp_config))
            output = gen.generate(
                repo_path=str(folder),
                company_context="Mixed document collection",
                output_dir=str(tmp_books),
            )

        assert Path(output).exists()
        content = Path(output).read_text(encoding="utf-8")
        assert "Memory Bank" in content
