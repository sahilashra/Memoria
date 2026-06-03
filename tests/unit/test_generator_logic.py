"""
Unit tests for generator.py logic that doesn't require AI calls:
- _enforce_header()
- _format_files()
- _save_book() archiving behaviour
- Token tracking on ModelProvider
"""

import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock


class TestEnforceHeader:
    """_enforce_header() ensures the book starts with the correct # Title header."""

    def _get_generator(self, tmp_config):
        from memoria.generator import BookGenerator
        with patch("litellm.completion"):
            gen = BookGenerator(str(tmp_config))
        return gen

    def test_correct_header_unchanged(self, tmp_config):
        gen = self._get_generator(tmp_config)
        content = "# My Project — Memory Bank\n> Last updated: 2024-01-01\n\n## TL;DR\nContent."
        result = gen._enforce_header(content, "My Project")
        assert result.startswith("# My Project — Memory Bank")

    def test_wrong_header_replaced(self, tmp_config):
        gen = self._get_generator(tmp_config)
        content = "# Wrong Title\n> Last updated: 2024-01-01\n\n## TL;DR\nContent."
        result = gen._enforce_header(content, "My Project")
        assert result.startswith("# My Project — Memory Bank")

    def test_missing_header_prepended(self, tmp_config):
        gen = self._get_generator(tmp_config)
        content = "## TL;DR\nJust content without a header."
        result = gen._enforce_header(content, "My Project")
        assert "# My Project — Memory Bank" in result

    def test_preserves_rest_of_content(self, tmp_config):
        gen = self._get_generator(tmp_config)
        content = "# Bad Title\n\n## TL;DR\n- Point one\n- Point two"
        result = gen._enforce_header(content, "My Project")
        assert "Point one" in result
        assert "Point two" in result


class TestFormatFiles:
    """_format_files() builds the text block fed to the model."""

    def _get_generator(self, tmp_config):
        from memoria.generator import BookGenerator
        with patch("litellm.completion"):
            gen = BookGenerator(str(tmp_config))
        return gen

    def test_basic_formatting(self, tmp_config):
        gen = self._get_generator(tmp_config)
        files = {
            "main.py": {"content": "print('hello')", "extension": ".py"},
            "utils.py": {"content": "def greet(): pass", "extension": ".py"},
        }
        result = gen._format_files(files)
        assert "main.py" in result
        assert "print('hello')" in result
        assert "utils.py" in result
        assert "def greet" in result

    def test_empty_files_returns_string(self, tmp_config):
        gen = self._get_generator(tmp_config)
        result = gen._format_files({})
        assert isinstance(result, str)

    def test_file_with_content_key(self, tmp_config):
        gen = self._get_generator(tmp_config)
        files = {"README.md": {"content": "# Hello World", "extension": ".md"}}
        result = gen._format_files(files)
        assert "README.md" in result
        assert "Hello World" in result


class TestSaveBook:
    """_save_book() creates files and archives previous versions."""

    def _get_generator(self, tmp_config):
        from memoria.generator import BookGenerator
        with patch("litellm.completion"):
            gen = BookGenerator(str(tmp_config))
        return gen

    def test_creates_book_file(self, tmp_config, tmp_path):
        gen = self._get_generator(tmp_config)
        books_dir = tmp_path / "books"
        # Include a --- divider so _enforce_header correctly identifies where body starts
        content = "# Test Project — Memory Bank\n> Last updated: 2024-01-01\n\n---\n\n## TL;DR\nContent."
        path = gen._save_book("Test Project", content, str(books_dir))
        assert Path(path).exists()
        saved = Path(path).read_text(encoding="utf-8")
        # _enforce_header always rewrites the date line — check structure not exact text
        assert "# Test Project" in saved
        assert "Memory Bank" in saved
        assert "## TL;DR" in saved
        assert "Content." in saved

    def test_filename_sanitised(self, tmp_config, tmp_path):
        gen = self._get_generator(tmp_config)
        books_dir = tmp_path / "books"
        content = "# My Cool Project — Memory Bank\n> Last updated: 2024-01-01\n\nContent."
        path = gen._save_book("My Cool Project!", content, str(books_dir))
        # Special chars should be replaced with underscores
        assert "My_Cool_Project_" in Path(path).name or "My_Cool_Project" in Path(path).name

    def test_previous_version_archived(self, tmp_config, tmp_path):
        gen = self._get_generator(tmp_config)
        books_dir = tmp_path / "books"
        content1 = "# My Project — Memory Bank\n> Last updated: 2024-01-01\n\nVersion 1."
        content2 = "# My Project — Memory Bank\n> Last updated: 2024-01-02\n\nVersion 2."

        gen._save_book("My Project", content1, str(books_dir))
        gen._save_book("My Project", content2, str(books_dir))

        # Current book should be version 2
        book_path = books_dir / "My_Project_memory_bank.md"
        assert "Version 2" in book_path.read_text(encoding="utf-8")

        # Version 1 should be in .archive/
        archive = books_dir / ".archive"
        archived = list(archive.glob("My_Project_*.md"))
        assert len(archived) >= 1

    def test_output_dir_created_if_missing(self, tmp_config, tmp_path):
        gen = self._get_generator(tmp_config)
        books_dir = tmp_path / "deep" / "nested" / "books"
        content = "# Project — Memory Bank\n> Last updated: 2024-01-01\n\nContent."
        path = gen._save_book("Project", content, str(books_dir))
        assert Path(path).exists()


class TestTokenTracking:
    """ModelProvider accumulates token counts across calls."""

    def test_tokens_start_at_zero(self, tmp_config):
        from memoria.models import ModelProvider
        mp = ModelProvider(str(tmp_config))
        assert mp.tokens_in == 0
        assert mp.tokens_out == 0
        assert mp.tokens_total == 0

    def test_tokens_accumulate(self, tmp_config):
        from memoria.models import ModelProvider

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Some response"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 150
        mock_response.usage.completion_tokens = 75

        with patch("litellm.completion", return_value=mock_response):
            mp = ModelProvider(str(tmp_config))
            mp.complete("system prompt", "user prompt")
            mp.complete("system prompt 2", "user prompt 2")

        assert mp.tokens_in == 300    # 150 × 2
        assert mp.tokens_out == 150   # 75 × 2
        assert mp.tokens_total == 450

    def test_usage_summary_empty_when_no_calls(self, tmp_config):
        from memoria.models import ModelProvider
        mp = ModelProvider(str(tmp_config))
        assert mp.usage_summary() == ""

    def test_usage_summary_non_empty_after_call(self, tmp_config):
        from memoria.models import ModelProvider

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "response"
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50

        with patch("litellm.completion", return_value=mock_response):
            mp = ModelProvider(str(tmp_config))
            mp.complete("sys", "user")

        summary = mp.usage_summary()
        assert "150" in summary   # total tokens
        assert "100" in summary   # tokens in
        assert "50" in summary    # tokens out

    def test_provider_name_gemini(self, tmp_config):
        from memoria.models import ModelProvider
        mp = ModelProvider(str(tmp_config))
        assert "Gemini" in mp.provider_name

    def test_provider_name_claude(self, tmp_path):
        from memoria.models import ModelProvider
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: claude-sonnet-4-5\n", encoding="utf-8")
        mp = ModelProvider(str(cfg))
        assert "Claude" in mp.provider_name

    def test_provider_name_openai(self, tmp_path):
        from memoria.models import ModelProvider
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: gpt-4o\n", encoding="utf-8")
        mp = ModelProvider(str(cfg))
        assert "OpenAI" in mp.provider_name
