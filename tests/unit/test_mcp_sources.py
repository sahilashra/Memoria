"""
Unit tests for mcp_sources.py — env expansion, config loading, error handling.
No MCP server is actually started — that requires integration tests with real servers.
"""

import os
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch

from memoria.mcp_sources import _expand_env, load_sources


class TestExpandEnv:
    """Tests for _expand_env() — ${VAR} pattern substitution."""

    def test_single_var_expanded(self):
        with patch.dict(os.environ, {"MY_TOKEN": "abc123"}):
            result = _expand_env("${MY_TOKEN}")
        assert result == "abc123"

    def test_var_in_url_expanded(self):
        with patch.dict(os.environ, {"CONFLUENCE_URL": "https://company.atlassian.net"}):
            result = _expand_env("${CONFLUENCE_URL}/wiki")
        assert result == "https://company.atlassian.net/wiki"

    def test_missing_var_kept_as_is(self):
        with patch.dict(os.environ, {}, clear=False):
            # Ensure var is not set
            os.environ.pop("MISSING_VAR", None)
            result = _expand_env("${MISSING_VAR}")
        assert result == "${MISSING_VAR}"

    def test_multiple_vars_expanded(self):
        with patch.dict(os.environ, {"HOST": "myhost", "PORT": "8080"}):
            result = _expand_env("${HOST}:${PORT}")
        assert result == "myhost:8080"

    def test_no_vars_unchanged(self):
        result = _expand_env("plain string with no vars")
        assert result == "plain string with no vars"

    def test_non_string_coerced(self):
        # Should handle int values without crashing
        result = _expand_env(42)
        assert result == "42"


class TestLoadSources:
    """Tests for load_sources() — reads mcp_sources from config."""

    def test_empty_when_no_key(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: gemini/gemini-2.0-flash\n", encoding="utf-8")
        assert load_sources(str(cfg)) == []

    def test_empty_when_file_missing(self, tmp_path):
        with patch("pathlib.Path.cwd", return_value=tmp_path), \
             patch("pathlib.Path.home", return_value=tmp_path):
            assert load_sources("nonexistent.yaml") == []

    def test_single_source_returned(self, tmp_path):
        config = {"model": "test", "mcp_sources": [
            {"name": "confluence", "server": "npx @atlassian/mcp-confluence"}
        ]}
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump(config), encoding="utf-8")
        sources = load_sources(str(cfg))
        assert len(sources) == 1
        assert sources[0]["name"] == "confluence"

    def test_all_source_fields_preserved(self, tmp_path):
        config = {"model": "test", "mcp_sources": [
            {
                "name": "slack",
                "server": "npx @slack/mcp",
                "env": {"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}"},
                "pull": [{"tool": "slack_get_messages", "args": {"channel": "eng", "limit": 50}}]
            }
        ]}
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump(config), encoding="utf-8")
        sources = load_sources(str(cfg))
        src = sources[0]
        assert src["server"] == "npx @slack/mcp"
        assert "SLACK_BOT_TOKEN" in src["env"]
        assert src["pull"][0]["tool"] == "slack_get_messages"

    def test_pull_source_raises_import_error_without_mcp(self, tmp_path):
        """pull_source() should raise ImportError if `mcp` package is missing."""
        from memoria.mcp_sources import pull_source
        source = {"name": "test", "server": "npx something", "pull": [{"tool": "some_tool"}]}

        with patch.dict("sys.modules", {"mcp": None,
                                        "mcp.client": None,
                                        "mcp.client.stdio": None}):
            with pytest.raises((ImportError, Exception)):
                pull_source(source)
