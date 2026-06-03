"""
Regression tests — one test per bug that was found and fixed.
Each test is named after the bug it guards against.
If any of these fail, a previously-fixed bug has come back.

Bug history
───────────
BUG-001  WinError 267 — analyzing a single audio/image file crashed the crawler
BUG-002  Wrong model used — running from a parent directory silently picked the wrong config
BUG-003  Arabic transcript — Whisper auto-detected wrong language on English audio
BUG-004  Fake architecture hallucinated — code prompt used for audio files invented repos
BUG-005  Book name had file extension — "recording.m4a" produced "recording.m4a_memory_bank.md"
BUG-006  "Software project" for Certifications — all directories got the code context question
BUG-007  "Crawling repository..." for document folders — progress message not adapted
BUG-008  No "Cancelled" message when user types n at Proceed prompt
BUG-009  Config panel showed "config.yaml" even when resolved from ~/.memoria/config.yaml
BUG-010  Whisper re-downloaded 139MB every run — no explicit cache dir set
BUG-011  Transcribed audio content missing from book — extractor result not passed to generator
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from tests.conftest import make_mock_completion, MOCK_BOOK_CONTENT, MOCK_DOCUMENT_CONTENT


# ─── BUG-001 — WinError 267: single file mode ─────────────────────────────────

class TestBug001SingleFileMode:
    """
    Symptom: memoria analyze --repo recording.m4a → [WinError 267] directory name is invalid
    Root cause: crawler called iterdir() on a file path
    Fix: _crawl_single_file() method swaps repo_path to parent before iterating
    """

    def test_single_py_file_does_not_raise(self, tmp_path, tmp_config):
        f = tmp_path / "script.py"
        f.write_text("x = 1", encoding="utf-8")
        from memoria.crawler import RepoCrawler
        # Must NOT raise WinError 267 or any directory-related error
        crawler = RepoCrawler(str(f), str(tmp_config))
        data = crawler.crawl()
        assert data["stats"]["total_files"] == 1

    def test_single_html_file_does_not_raise(self, tmp_path, tmp_config):
        f = tmp_path / "page.html"
        f.write_text("<html><body><p>Hi</p></body></html>", encoding="utf-8")
        from memoria.crawler import RepoCrawler
        crawler = RepoCrawler(str(f), str(tmp_config))
        data = crawler.crawl()
        assert data["stats"]["total_files"] == 1

    def test_single_ipynb_file_does_not_raise(self, tmp_path, tmp_config):
        nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
            {"cell_type": "code", "source": ["x = 1"], "outputs": []}
        ]}
        f = tmp_path / "notebook.ipynb"
        f.write_text(json.dumps(nb), encoding="utf-8")
        from memoria.crawler import RepoCrawler
        crawler = RepoCrawler(str(f), str(tmp_config))
        data = crawler.crawl()
        assert data["stats"]["total_files"] == 1

    def test_single_file_name_is_stem_not_path(self, tmp_path, tmp_config):
        """data['name'] for a single file should be the filename stem, not '.'"""
        f = tmp_path / "my_analysis.ipynb"
        nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": []}
        f.write_text(json.dumps(nb), encoding="utf-8")
        from memoria.crawler import RepoCrawler
        crawler = RepoCrawler(str(f), str(tmp_config))
        data = crawler.crawl()
        assert data["name"] == "my_analysis"
        assert data["name"] != "."


# ─── BUG-002 — Wrong model from wrong config ──────────────────────────────────

class TestBug002ConfigDiscovery:
    """
    Symptom: Running from a parent directory silently used the wrong model
    Root cause: No config found → hardcoded fallback to claude-opus-4-5
    Fix: ValueError raised if no model configured, 3-level config discovery
    """

    def test_raises_if_no_model_configured(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("books_dir: ./books\n", encoding="utf-8")
        from memoria.models import ModelProvider
        with pytest.raises(ValueError, match="No model configured"):
            ModelProvider(str(cfg))

    def test_no_silent_fallback_to_hardcoded_model(self, tmp_path):
        """ModelProvider must never silently use a hardcoded default model."""
        with patch("pathlib.Path.cwd", return_value=tmp_path), \
             patch("pathlib.Path.home", return_value=tmp_path):
            from memoria.models import ModelProvider
            with pytest.raises((ValueError, Exception)):
                ModelProvider("nonexistent_config.yaml")

    def test_direct_config_path_always_wins(self, tmp_path):
        cfg = tmp_path / "myconfig.yaml"
        cfg.write_text("model: gpt-4o\n", encoding="utf-8")
        from memoria.models import ModelProvider
        mp = ModelProvider(str(cfg))
        assert mp.model == "gpt-4o"


# ─── BUG-004 — Fake architecture hallucinated for audio ───────────────────────

class TestBug004AudioPromptRouting:
    """
    Symptom: Audio file analysis produced a book with invented code architecture
    Root cause: _generate_single() always used the code prompt template
    Fix: _detect_content_type() routes audio to AUDIO_PROMPT_TEMPLATE
    """

    def test_audio_content_type_detected(self):
        from memoria.generator import _detect_content_type
        files = {"standup.m4a": {}}
        assert _detect_content_type(files) == "audio"

    def test_audio_uses_different_system_prompt(self, tmp_path, tmp_config, tmp_books):
        """The system prompt for audio must NOT be the code SYSTEM_PROMPT."""
        from memoria.generator import BookGenerator, SYSTEM_PROMPT

        captured = []
        def capture_complete(model, messages, max_tokens, **kwargs):
            captured.append(messages[0]["content"])
            return make_mock_completion(MOCK_DOCUMENT_CONTENT)

        # Create a fake audio "file" — crawler will see .m4a extension
        audio_file = tmp_path / "meeting.m4a"
        audio_file.write_bytes(b"\x00" * 10)

        # Mock the audio extractor too so no Whisper needed
        with patch("litellm.completion", side_effect=capture_complete), \
             patch("memoria.extractors.audio.extract_audio", return_value="Hello team, let's get started."):
            gen = BookGenerator(str(tmp_config))
            gen.generate(str(audio_file), "A meeting recording", str(tmp_books))

        assert len(captured) >= 1
        # Must NOT use the code-oriented system prompt
        assert captured[0] != SYSTEM_PROMPT, \
            "BUG-004 regression: code prompt used for audio content"

    def test_document_content_type_detected(self):
        from memoria.generator import _detect_content_type
        files = {"report.pdf": {}, "data.xlsx": {}}
        assert _detect_content_type(files) == "document"

    def test_document_uses_different_system_prompt(self, tmp_path, tmp_config, tmp_books, sample_doc_folder):
        """Document folders must not use the code SYSTEM_PROMPT."""
        from memoria.generator import BookGenerator, SYSTEM_PROMPT

        captured = []
        def capture_complete(model, messages, max_tokens, **kwargs):
            captured.append(messages[0]["content"])
            return make_mock_completion(MOCK_DOCUMENT_CONTENT)

        with patch("litellm.completion", side_effect=capture_complete):
            gen = BookGenerator(str(tmp_config))
            gen.generate(str(sample_doc_folder), "A document collection", str(tmp_books))

        assert captured[0] != SYSTEM_PROMPT, \
            "BUG-004 regression: code prompt used for document content"


# ─── BUG-005 — Book name had file extension ───────────────────────────────────

class TestBug005BookNameHasNoExtension:
    """
    Symptom: analyzing recording.m4a produced recording.m4a_memory_bank.md
    Root cause: repo_path.name used instead of repo_path.stem for single files
    Fix: name_base = repo_path.stem if repo_path.is_file() else repo_path.name
    """

    def test_single_file_book_has_no_extension_in_name(self, tmp_path, tmp_config, tmp_books):
        f = tmp_path / "standup.html"
        f.write_text("<html><body><p>Notes</p></body></html>", encoding="utf-8")

        with patch("litellm.completion", return_value=make_mock_completion()):
            from memoria.generator import BookGenerator
            gen = BookGenerator(str(tmp_config))
            output = gen.generate(str(f), "Meeting notes", str(tmp_books))

        book_name = Path(output).name
        assert ".html" not in book_name, \
            f"BUG-005 regression: book name contains file extension: {book_name}"
        assert "standup" in book_name

    def test_directory_book_uses_dirname(self, sample_code_project, tmp_config, tmp_books):
        """Directories should use the folder name, not stem (same thing, but verify)."""
        with patch("litellm.completion", return_value=make_mock_completion()):
            from memoria.generator import BookGenerator
            gen = BookGenerator(str(tmp_config))
            output = gen.generate(str(sample_code_project), "A project", str(tmp_books))

        assert "my_project" in Path(output).name


# ─── BUG-006 — Wrong context question for document folders ────────────────────

class TestBug006ContextQuestionAdaptsToDirectory:
    """
    Symptom: Analyzing Certifications/ asked "What does this project do?" defaulting to "A software project"
    Root cause: _peek_dir_type() not called for directory paths
    Fix: elif not is_file: branch calls _peek_dir_type() and adapts question/default
    """

    def test_document_folder_classified_correctly(self, tmp_path):
        from memoria.cli import _peek_dir_type
        (tmp_path / "certificate.pdf").touch()
        (tmp_path / "diploma.pdf").touch()
        assert _peek_dir_type(tmp_path) == "document", \
            "BUG-006 regression: PDF folder not classified as 'document'"

    def test_code_folder_classified_correctly(self, tmp_path):
        from memoria.cli import _peek_dir_type
        (tmp_path / "main.py").touch()
        (tmp_path / "requirements.txt").touch()
        assert _peek_dir_type(tmp_path) == "code"

    def test_certifications_like_folder_is_document(self, tmp_path):
        """A folder with only PDFs and no code signals should be 'document'."""
        from memoria.cli import _peek_dir_type
        for name in ["AWS_cert.pdf", "Python_cert.pdf", "Agile_cert.pdf"]:
            (tmp_path / name).touch()
        result = _peek_dir_type(tmp_path)
        assert result == "document", \
            f"BUG-006 regression: certifications folder returned '{result}' not 'document'"


# ─── BUG-007 — Progress messages not adapted ──────────────────────────────────

class TestBug007ProgressMessages:
    """
    Symptom: Document folders showed "Crawling repository..." in progress
    Root cause: generate() always emitted the same progress string
    Fix: pre-crawl label checks for code signals; post-crawl uses _detect_content_type
    """

    def test_code_project_says_crawling(self, sample_code_project, tmp_config, tmp_books):
        steps = []
        with patch("litellm.completion", return_value=make_mock_completion()):
            from memoria.generator import BookGenerator
            gen = BookGenerator(str(tmp_config))
            gen.generate(str(sample_code_project), "code", str(tmp_books), on_progress=steps.append)

        first_step = steps[0].lower()
        assert "crawling" in first_step, \
            f"BUG-007 regression: code project first step was '{steps[0]}'"

    def test_document_folder_does_not_say_crawling(self, sample_doc_folder, tmp_config, tmp_books):
        steps = []
        with patch("litellm.completion", return_value=make_mock_completion(MOCK_DOCUMENT_CONTENT)):
            from memoria.generator import BookGenerator
            gen = BookGenerator(str(tmp_config))
            gen.generate(str(sample_doc_folder), "docs", str(tmp_books), on_progress=steps.append)

        first_step = steps[0].lower()
        assert "crawling" not in first_step, \
            f"BUG-007 regression: document folder said 'Crawling': '{steps[0]}'"

    def test_document_analyze_step_says_extracting(self, sample_doc_folder, tmp_config, tmp_books):
        steps = []
        with patch("litellm.completion", return_value=make_mock_completion(MOCK_DOCUMENT_CONTENT)):
            from memoria.generator import BookGenerator
            gen = BookGenerator(str(tmp_config))
            gen.generate(str(sample_doc_folder), "docs", str(tmp_books), on_progress=steps.append)

        # Second step should mention extracting, not "analysing" code
        second_step = steps[1].lower() if len(steps) > 1 else ""
        assert any(w in second_step for w in ("extract", "document", "file")), \
            f"BUG-007 regression: document analyze step was '{steps[1] if len(steps) > 1 else '(missing)'}'"


# ─── BUG-008 — Archive versioning: second save always archives first ──────────

class TestBug008ArchiveOnOverwrite:
    """
    Symptom: Re-analyzing a project silently overwrote the book with no archive
    Root cause: Archive logic was missing
    Fix: _save_book() moves existing file to .archive/ before writing new one
    """

    def test_first_save_no_archive(self, tmp_config, tmp_path):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        books_dir = tmp_path / "books"
        content = "# P — Memory Bank\n> Last updated: 2024-01-01\n\n---\n\n## TL;DR\nv1"
        gen._save_book("P", content, str(books_dir))
        archive = books_dir / ".archive"
        assert not list(archive.glob("P_*.md"))  # nothing archived yet

    def test_second_save_archives_first(self, tmp_config, tmp_path):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        books_dir = tmp_path / "books"
        content1 = "# P — Memory Bank\n> Last updated: 2024-01-01\n\n---\n\n## TL;DR\nv1"
        content2 = "# P — Memory Bank\n> Last updated: 2024-01-02\n\n---\n\n## TL;DR\nv2"
        gen._save_book("P", content1, str(books_dir))
        gen._save_book("P", content2, str(books_dir))
        archived = list((books_dir / ".archive").glob("P_*.md"))
        assert len(archived) == 1
        assert "v1" in archived[0].read_text(encoding="utf-8")

    def test_current_book_is_latest(self, tmp_config, tmp_path):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        books_dir = tmp_path / "books"
        for i in range(1, 4):
            content = f"# P — Memory Bank\n> Last updated: 2024-01-0{i}\n\n---\n\n## TL;DR\nv{i}"
            gen._save_book("P", content, str(books_dir))
        current = (books_dir / "P_memory_bank.md").read_text(encoding="utf-8")
        assert "v3" in current
        assert len(list((books_dir / ".archive").glob("P_*.md"))) == 2


# ─── BUG-011 — Extractor result passed correctly to generator ─────────────────

class TestBug011ExtractorContentReachesGenerator:
    """
    Symptom: Audio book had no transcribed content — book was empty/generic
    Root cause: Crawler stored extraction result but generator received empty files dict
    Fix: _crawl_single_file() stores content in self.files; generator reads it
    """

    def test_html_content_reaches_model(self, tmp_path, tmp_config, tmp_books):
        """Content from the HTML extractor must appear in the prompt sent to the model."""
        f = tmp_path / "notes.html"
        f.write_text(
            "<html><body><h1>Q1 Review</h1><p>Revenue increased 23%.</p></body></html>",
            encoding="utf-8"
        )
        prompts_sent = []

        def capture(model, messages, max_tokens, **kwargs):
            prompts_sent.append(messages[1]["content"])  # user message
            return make_mock_completion()

        with patch("litellm.completion", side_effect=capture):
            from memoria.generator import BookGenerator
            gen = BookGenerator(str(tmp_config))
            gen.generate(str(f), "A web page", str(tmp_books))

        assert len(prompts_sent) >= 1
        combined_prompt = " ".join(prompts_sent)
        assert "Q1 Review" in combined_prompt or "Revenue" in combined_prompt, \
            "BUG-011 regression: extracted HTML content did not reach the model prompt"

    def test_ipynb_content_reaches_model(self, tmp_path, tmp_config, tmp_books):
        """Notebook cell content must appear in the prompt sent to the model."""
        nb = {
            "nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python"}},
            "cells": [
                {"cell_type": "markdown", "source": ["# Revenue Analysis"], "outputs": []},
                {"cell_type": "code", "source": ["total_revenue = 1_245_000"], "outputs": []},
            ]
        }
        f = tmp_path / "analysis.ipynb"
        f.write_text(json.dumps(nb), encoding="utf-8")
        prompts_sent = []

        def capture(model, messages, max_tokens, **kwargs):
            prompts_sent.append(messages[1]["content"])
            return make_mock_completion()

        with patch("litellm.completion", side_effect=capture):
            from memoria.generator import BookGenerator
            gen = BookGenerator(str(tmp_config))
            gen.generate(str(f), "A notebook", str(tmp_books))

        combined = " ".join(prompts_sent)
        assert "Revenue Analysis" in combined or "total_revenue" in combined, \
            "BUG-011 regression: notebook content did not reach the model prompt"


# ─── BUG-012 — Rate-limit retry loops too long ────────────────────────────────

class TestBug012RateLimitAbortsFast:
    """
    Symptom: Persistent rate limits caused 6-minute waits (60s+120s+180s per chunk).
    Root cause: Retry logic waited 60*(attempt+1) seconds with 3 attempts = 360s per chunk.
    Fix: Reduced to 15s/30s max; consecutive-rl budget of 3 aborts the entire job.

    These tests mock time.sleep so the suite stays fast.
    """

    def test_call_with_retry_waits_at_most_15s(self, tmp_config, tmp_path):
        """_call_with_retry must use at most a 15s wait on first retry."""
        from unittest.mock import patch, MagicMock
        from memoria.generator import BookGenerator

        gen = BookGenerator(str(tmp_config))
        sleep_calls = []

        rl_error = RuntimeError("429 Resource exhausted: rate limit exceeded")
        ok_response = make_mock_completion()

        with patch("litellm.completion", side_effect=[rl_error, ok_response]):
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                gen._call_with_retry("sys", "prompt")

        assert sleep_calls, "Expected at least one sleep on rate limit"
        assert max(sleep_calls) <= 15, \
            f"BUG-012 regression: _call_with_retry slept {max(sleep_calls)}s, should be ≤15s"

    def test_consecutive_rate_limits_abort_generate_large(
        self, sample_code_project, tmp_config, tmp_books
    ):
        """3 consecutive rate-limit errors on chunks must raise RuntimeError, not loop forever."""
        from unittest.mock import patch
        from memoria.generator import BookGenerator

        gen = BookGenerator(str(tmp_config))
        rl_error = RuntimeError("429 rate limit exceeded")

        # Make every call fail with a rate-limit error
        with patch("litellm.completion", side_effect=rl_error):
            with patch("time.sleep"):   # skip actual sleeps
                with pytest.raises(RuntimeError) as exc_info:
                    gen.generate(
                        str(sample_code_project), "test", str(tmp_books)
                    )

        err = str(exc_info.value).lower()
        assert "rate limit" in err or "quota" in err, \
            f"BUG-012 regression: expected rate-limit error, got: {exc_info.value}"

    def test_rate_limit_error_message_is_actionable(self, tmp_config, tmp_path):
        """The final error message must tell the user what to do, not just 'error'."""
        from unittest.mock import patch
        from memoria.generator import BookGenerator

        gen = BookGenerator(str(tmp_config))
        rl_error = RuntimeError("429 rate limit exceeded")

        with patch("litellm.completion", side_effect=rl_error):
            with patch("time.sleep"):
                with pytest.raises(RuntimeError) as exc_info:
                    gen._call_with_retry("sys", "prompt")

        msg = str(exc_info.value)
        assert any(phrase in msg.lower() for phrase in ["quota", "rate limit", "model", "wait"]), \
            f"BUG-012 regression: error message not actionable: '{msg}'"
