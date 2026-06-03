"""
Integration tests for the full analyze pipeline.
AI calls are mocked — tests run without API keys and complete in seconds.
Tests verify: routing, file saving, archiving, content-type detection, and
that the right prompt template is selected for each content type.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, call, MagicMock

from tests.conftest import make_mock_completion, MOCK_BOOK_CONTENT, MOCK_DOCUMENT_CONTENT


class TestAnalyzeCodeProject:

    def test_generates_book_file(self, sample_code_project, tmp_config, tmp_books, mock_litellm):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(
            repo_path=str(sample_code_project),
            company_context="A test Python project",
            output_dir=str(tmp_books),
        )
        assert Path(output).exists()
        assert Path(output).suffix == ".md"

    def test_book_named_after_project(self, sample_code_project, tmp_config, tmp_books, mock_litellm):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(
            repo_path=str(sample_code_project),
            company_context="Test",
            output_dir=str(tmp_books),
        )
        assert "my_project" in Path(output).name

    def test_book_has_correct_header(self, sample_code_project, tmp_config, tmp_books, mock_litellm):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(
            repo_path=str(sample_code_project),
            company_context="Test",
            output_dir=str(tmp_books),
        )
        content = Path(output).read_text(encoding="utf-8")
        assert content.startswith("# my_project — Memory Bank")

    def test_model_was_called(self, sample_code_project, tmp_config, tmp_books, mock_litellm):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        gen.generate(
            repo_path=str(sample_code_project),
            company_context="Test",
            output_dir=str(tmp_books),
        )
        assert mock_litellm.called

    def test_on_progress_callback_called(self, sample_code_project, tmp_config, tmp_books, mock_litellm):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        steps = []
        gen.generate(
            repo_path=str(sample_code_project),
            company_context="Test",
            output_dir=str(tmp_books),
            on_progress=steps.append,
        )
        assert len(steps) >= 2  # at least crawl + generate + save
        assert any("Saving" in s or "saving" in s.lower() for s in steps)

    def test_second_analyze_archives_first(self, sample_code_project, tmp_config, tmp_books, mock_litellm):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        gen.generate(str(sample_code_project), "Test v1", str(tmp_books))
        gen.generate(str(sample_code_project), "Test v2", str(tmp_books))

        archive = tmp_books / ".archive"
        archived = list(archive.glob("my_project_*.md"))
        assert len(archived) >= 1

    def test_token_usage_tracked(self, sample_code_project, tmp_config, tmp_books, mock_litellm):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        gen.generate(str(sample_code_project), "Test", str(tmp_books))
        assert gen.model.tokens_total > 0


class TestAnalyzeDocumentFolder:

    def test_generates_book_for_doc_folder(self, sample_doc_folder, tmp_config, tmp_books, mock_litellm_document):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(
            repo_path=str(sample_doc_folder),
            company_context="A document collection",
            output_dir=str(tmp_books),
        )
        assert Path(output).exists()

    def test_document_prompt_used_for_docs(self, sample_doc_folder, tmp_config, tmp_books):
        """Verify the document system prompt is used, not the code prompt."""
        from memoria.generator import BookGenerator, DOCUMENT_SYSTEM_PROMPT, SYSTEM_PROMPT

        captured_system_prompts = []

        def mock_complete(model, messages, max_tokens, **kwargs):
            captured_system_prompts.append(messages[0]["content"])
            return make_mock_completion(MOCK_DOCUMENT_CONTENT)

        with patch("litellm.completion", side_effect=mock_complete):
            gen = BookGenerator(str(tmp_config))
            gen.generate(
                repo_path=str(sample_doc_folder),
                company_context="Test docs",
                output_dir=str(tmp_books),
            )

        assert len(captured_system_prompts) >= 1
        # The system prompt used should be the document one, not the code one
        used_prompt = captured_system_prompts[0]
        assert used_prompt != SYSTEM_PROMPT  # not the code prompt


class TestAnalyzeSingleFile:

    def test_single_python_file(self, tmp_path, tmp_config, tmp_books, mock_litellm):
        f = tmp_path / "script.py"
        f.write_text("def hello(): return 'world'", encoding="utf-8")

        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(str(f), "A Python script", str(tmp_books))
        assert Path(output).exists()
        assert "script" in Path(output).name

    def test_single_html_file(self, tmp_path, tmp_config, tmp_books, mock_litellm_document):
        f = tmp_path / "page.html"
        f.write_text("<html><body><h1>Test Page</h1><p>Content here.</p></body></html>", encoding="utf-8")

        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(str(f), "A web page", str(tmp_books))
        assert Path(output).exists()

    def test_single_ipynb_file(self, tmp_path, tmp_config, tmp_books, mock_litellm):
        nb = {
            "nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python"}},
            "cells": [
                {"cell_type": "markdown", "source": ["# Analysis\nData exploration."]},
                {"cell_type": "code", "source": ["import pandas as pd\ndf = pd.DataFrame()"], "outputs": []},
            ]
        }
        f = tmp_path / "analysis.ipynb"
        f.write_text(json.dumps(nb), encoding="utf-8")

        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(str(f), "A Jupyter analysis", str(tmp_books))
        assert Path(output).exists()

    def test_book_name_uses_stem_not_extension(self, tmp_path, tmp_config, tmp_books, mock_litellm):
        """Book for 'recording.m4a' should be 'recording_memory_bank.md', not 'recording.m4a_memory_bank.md'"""
        # We simulate by testing the name_base logic with a .html file
        f = tmp_path / "standup.html"
        f.write_text("<html><body><p>Standup notes</p></body></html>", encoding="utf-8")

        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        output = gen.generate(str(f), "Standup notes", str(tmp_books))
        assert ".html" not in Path(output).name
        assert "standup" in Path(output).name


class TestGenerateFromText:
    """Tests for BookGenerator.generate_from_text() — used by memoria pull."""

    def test_generates_book_from_raw_text(self, tmp_config, tmp_books, mock_litellm_document):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        content = "This is pulled content from Confluence. It covers architecture decisions made in Q1."
        output = gen.generate_from_text(
            source_name="confluence",
            company_context="Engineering wiki",
            content=content,
            output_dir=str(tmp_books),
        )
        assert Path(output).exists()
        assert "confluence" in Path(output).name

    def test_large_content_chunked(self, tmp_config, tmp_books):
        """Content above CHUNK_THRESHOLD should be split and processed in chunks."""
        from memoria.generator import BookGenerator

        call_count = [0]
        def mock_complete(model, messages, max_tokens, **kwargs):
            call_count[0] += 1
            return make_mock_completion(MOCK_DOCUMENT_CONTENT)

        with patch("litellm.completion", side_effect=mock_complete):
            gen = BookGenerator(str(tmp_config))
            # Generate content larger than CHUNK_THRESHOLD (60_000 chars)
            large_content = "This is a paragraph of pulled content. " * 2000  # ~78K chars
            gen.generate_from_text(
                source_name="large_source",
                company_context="Big data source",
                content=large_content,
                output_dir=str(tmp_books),
            )

        # Should have called the model more than once (chunk summaries + synthesis)
        assert call_count[0] >= 2

    def test_progress_callback_fires(self, tmp_config, tmp_books, mock_litellm_document):
        from memoria.generator import BookGenerator
        gen = BookGenerator(str(tmp_config))
        steps = []
        gen.generate_from_text(
            source_name="notion",
            company_context="Wiki",
            content="Some pulled content from Notion.",
            output_dir=str(tmp_books),
            on_progress=steps.append,
        )
        assert len(steps) >= 1
        assert any("Saving" in s or "saving" in s.lower() or "Generating" in s for s in steps)
