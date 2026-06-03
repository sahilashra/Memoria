"""
Unit tests for pull_state.py — timestamp tracking for incremental/scheduled pulls.
All tests use a tmp_path-based state file so they never touch ~/.memoria/pull_state.json.
"""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


def _patch_state(tmp_path):
    """Return a context manager that redirects STATE_PATH to a temp file."""
    return patch("memoria.pull_state.STATE_PATH", tmp_path / "pull_state.json")


class TestGetSetLastPull:
    """Basic read/write round-trips."""

    def test_returns_none_for_unknown_source(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import get_last_pull
            assert get_last_pull("nonexistent") is None

    def test_set_and_get_roundtrip(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import get_last_pull, set_last_pull
            dt = datetime(2026, 4, 30, 9, 0, 0)
            set_last_pull("confluence", dt)
            result = get_last_pull("confluence")
            assert result == dt

    def test_defaults_to_now(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import get_last_pull, set_last_pull
            before = datetime.now()
            set_last_pull("slack")
            after = datetime.now()
            result = get_last_pull("slack")
            assert before <= result <= after

    def test_multiple_sources_independent(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import get_last_pull, set_last_pull
            t1 = datetime(2026, 1, 1)
            t2 = datetime(2026, 6, 1)
            set_last_pull("confluence", t1)
            set_last_pull("notion", t2)
            assert get_last_pull("confluence") == t1
            assert get_last_pull("notion") == t2

    def test_overwrite_updates_timestamp(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import get_last_pull, set_last_pull
            t1 = datetime(2026, 1, 1)
            t2 = datetime(2026, 6, 1)
            set_last_pull("confluence", t1)
            set_last_pull("confluence", t2)
            assert get_last_pull("confluence") == t2

    def test_book_path_stored(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import set_last_pull, get_all_states
            set_last_pull("confluence", book_path="books/confluence_memory_bank.md")
            state = get_all_states()
            assert state["confluence"]["last_book"] == "books/confluence_memory_bank.md"


class TestClearState:

    def test_clear_removes_source(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import get_last_pull, set_last_pull, clear_state
            set_last_pull("confluence", datetime(2026, 1, 1))
            clear_state("confluence")
            assert get_last_pull("confluence") is None

    def test_clear_nonexistent_is_safe(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import clear_state
            clear_state("never_existed")   # must not raise

    def test_clear_one_leaves_others(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import get_last_pull, set_last_pull, clear_state
            set_last_pull("confluence", datetime(2026, 1, 1))
            set_last_pull("slack", datetime(2026, 2, 1))
            clear_state("confluence")
            assert get_last_pull("confluence") is None
            assert get_last_pull("slack") is not None


class TestGetAllStates:

    def test_empty_when_no_state_file(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import get_all_states
            assert get_all_states() == {}

    def test_returns_all_sources(self, tmp_path):
        with _patch_state(tmp_path):
            from memoria.pull_state import set_last_pull, get_all_states
            set_last_pull("confluence", datetime(2026, 1, 1))
            set_last_pull("notion", datetime(2026, 2, 1))
            states = get_all_states()
            assert "confluence" in states
            assert "notion" in states

    def test_corrupt_file_returns_empty(self, tmp_path):
        state_file = tmp_path / "pull_state.json"
        state_file.write_text("{{NOT VALID JSON", encoding="utf-8")
        with _patch_state(tmp_path):
            from memoria.pull_state import get_all_states
            assert get_all_states() == {}
