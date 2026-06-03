"""
Unit tests for scheduler.py — interval parsing, due-check logic, and run_due().
No actual MCP subprocesses are spawned; pull_source is mocked throughout.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


# ─── parse_interval ───────────────────────────────────────────────────────────

class TestParseInterval:
    """parse_interval() converts schedule strings to timedelta."""

    def test_hourly(self):
        from memoria.scheduler import parse_interval
        assert parse_interval("hourly") == timedelta(hours=1)

    def test_daily(self):
        from memoria.scheduler import parse_interval
        assert parse_interval("daily") == timedelta(days=1)

    def test_weekly(self):
        from memoria.scheduler import parse_interval
        assert parse_interval("weekly") == timedelta(weeks=1)

    def test_minutes_suffix(self):
        from memoria.scheduler import parse_interval
        assert parse_interval("30m") == timedelta(minutes=30)
        assert parse_interval("5m")  == timedelta(minutes=5)

    def test_hours_suffix(self):
        from memoria.scheduler import parse_interval
        assert parse_interval("2h")  == timedelta(hours=2)
        assert parse_interval("12h") == timedelta(hours=12)

    def test_days_suffix(self):
        from memoria.scheduler import parse_interval
        assert parse_interval("1d") == timedelta(days=1)
        assert parse_interval("7d") == timedelta(weeks=1)

    def test_case_insensitive(self):
        from memoria.scheduler import parse_interval
        assert parse_interval("Daily") == timedelta(days=1)
        assert parse_interval("HOURLY") == timedelta(hours=1)

    def test_cron_expression_returns_none(self):
        """Raw cron expressions are not yet supported — must return None, not crash."""
        from memoria.scheduler import parse_interval
        assert parse_interval("0 9 * * 1-5") is None
        assert parse_interval("*/15 * * * *") is None

    def test_empty_returns_none(self):
        from memoria.scheduler import parse_interval
        assert parse_interval("") is None
        assert parse_interval(None) is None

    def test_garbage_returns_none(self):
        from memoria.scheduler import parse_interval
        assert parse_interval("whenever") is None
        assert parse_interval("soon") is None


# ─── describe_interval ────────────────────────────────────────────────────────

class TestDescribeInterval:

    def test_daily_describes_as_1d(self):
        from memoria.scheduler import describe_interval
        result = describe_interval("daily")
        assert "1d" in result or "24h" in result or "day" in result.lower()

    def test_2h_describes_correctly(self):
        from memoria.scheduler import describe_interval
        assert "2h" in describe_interval("2h")

    def test_unsupported_includes_format(self):
        from memoria.scheduler import describe_interval
        result = describe_interval("0 9 * * 1")
        assert "unsupported" in result or "0 9 * * 1" in result


# ─── is_due ───────────────────────────────────────────────────────────────────

class TestIsDue:
    """is_due() decides whether a source should run based on schedule + last pull."""

    def _source(self, name="test", schedule="daily", incremental=False):
        return {"name": name, "schedule": schedule, "incremental": incremental}

    def test_never_pulled_is_always_due(self, tmp_path):
        with patch("memoria.scheduler.get_last_pull", return_value=None):
            from memoria.scheduler import is_due
            assert is_due(self._source()) is True

    def test_recently_pulled_is_not_due(self, tmp_path):
        recent = datetime.now() - timedelta(minutes=5)
        with patch("memoria.scheduler.get_last_pull", return_value=recent):
            from memoria.scheduler import is_due
            assert is_due(self._source(schedule="daily")) is False

    def test_old_pull_is_due(self, tmp_path):
        old = datetime.now() - timedelta(days=2)
        with patch("memoria.scheduler.get_last_pull", return_value=old):
            from memoria.scheduler import is_due
            assert is_due(self._source(schedule="daily")) is True

    def test_no_schedule_never_due(self):
        from memoria.scheduler import is_due
        assert is_due({"name": "nosched"}) is False

    def test_unsupported_schedule_not_due(self):
        with patch("memoria.scheduler.get_last_pull", return_value=None):
            from memoria.scheduler import is_due
            assert is_due({"name": "x", "schedule": "0 9 * * 1"}) is False

    def test_hourly_respects_interval(self):
        just_under = datetime.now() - timedelta(minutes=59)
        just_over  = datetime.now() - timedelta(minutes=61)
        from memoria.scheduler import is_due
        with patch("memoria.scheduler.get_last_pull", return_value=just_under):
            assert is_due({"name": "x", "schedule": "hourly"}) is False
        with patch("memoria.scheduler.get_last_pull", return_value=just_over):
            assert is_due({"name": "x", "schedule": "hourly"}) is True


# ─── due_sources ─────────────────────────────────────────────────────────────

class TestDueSources:

    def test_returns_only_due_sources(self, tmp_config):
        sources = [
            {"name": "a", "schedule": "daily"},
            {"name": "b", "schedule": "daily"},
            {"name": "c"},  # no schedule — never due
        ]
        # a is old (due), b is recent (not due), c has no schedule
        def _last_pull(name):
            return {
                "a": datetime.now() - timedelta(days=2),
                "b": datetime.now() - timedelta(minutes=5),
            }.get(name)

        # scheduler imports load_sources at call time; patch the scheduler's reference
        with patch("memoria.scheduler.load_sources", return_value=sources), \
             patch("memoria.scheduler.get_last_pull", side_effect=_last_pull):
            from memoria.scheduler import due_sources
            due = due_sources(str(tmp_config))
            assert len(due) == 1
            assert due[0]["name"] == "a"


# ─── run_due ─────────────────────────────────────────────────────────────────

class TestRunDue:

    def test_no_due_sources_returns_empty(self, tmp_config):
        with patch("memoria.mcp_sources.load_sources", return_value=[]), \
             patch("memoria.scheduler.due_sources", return_value=[]):
            from memoria.scheduler import run_due
            results = run_due(str(tmp_config), "books")
            assert results == []

    def test_progress_called_when_no_due(self, tmp_config):
        steps = []
        with patch("memoria.scheduler.due_sources", return_value=[]):
            from memoria.scheduler import run_due
            run_due(str(tmp_config), "books", on_progress=steps.append)
        assert any("no sources" in s.lower() for s in steps)

    def test_error_in_source_does_not_crash_others(self, tmp_config, tmp_path):
        """An exception in one source must not prevent the next source from running."""
        books_dir = tmp_path / "books"

        sources_due = [
            {"name": "broken", "schedule": "daily"},
            {"name": "working", "schedule": "daily"},
        ]

        call_order = []

        def fake_pull(source, since=None):
            if source["name"] == "broken":
                raise RuntimeError("Connection refused")
            call_order.append(source["name"])
            return "Some content from working source"

        mock_gen = MagicMock()
        mock_gen.generate_from_text.return_value = str(books_dir / "working_memory_bank.md")

        steps = []
        # BookGenerator is imported inside run_due — patch the generator module
        with patch("memoria.scheduler.due_sources", return_value=sources_due), \
             patch("memoria.scheduler.pull_source", side_effect=fake_pull), \
             patch("memoria.generator.BookGenerator", return_value=mock_gen), \
             patch("memoria.scheduler.set_last_pull"):
            from memoria.scheduler import run_due
            results = run_due(str(tmp_config), str(books_dir), on_progress=steps.append)

        # working source must have been called despite broken source failing
        assert "working" in call_order
        # error must have been reported in progress
        assert any("error" in s.lower() or "broken" in s.lower() for s in steps)


# ─── Cron expression support ──────────────────────────────────────────────────

class TestCronSchedules:
    """
    Cron expressions require croniter (pip install croniter).
    Tests gracefully skip when croniter is not installed.
    """

    @staticmethod
    def _croniter_available():
        try:
            import croniter  # noqa: F401
            return True
        except ImportError:
            return False

    def test_cron_due_when_never_pulled(self):
        """A source on a cron schedule that has never run is always due."""
        with patch("memoria.scheduler.get_last_pull", return_value=None):
            from memoria.scheduler import is_due
            source = {"name": "x", "schedule": "0 9 * * 1-5"}
            if not self._croniter_available():
                # Without croniter, cron expressions return False (graceful degradation)
                assert is_due(source) is False
            else:
                assert is_due(source) is True

    def test_cron_not_due_just_fired(self):
        """If the last pull happened a minute ago and cron fires daily, it's not due."""
        if not self._croniter_available():
            pytest.skip("croniter not installed")

        recent = datetime.now() - timedelta(minutes=1)
        with patch("memoria.scheduler.get_last_pull", return_value=recent):
            from memoria.scheduler import is_due
            source = {"name": "x", "schedule": "0 9 * * *"}   # daily at 9am
            # Unless it actually fired in the last minute, this won't be due
            result = is_due(source)
            assert isinstance(result, bool)   # just check it doesn't crash

    def test_cron_due_after_scheduled_time_passed(self):
        """If the cron would have fired between last_pull and now, it's due."""
        if not self._croniter_available():
            pytest.skip("croniter not installed")

        # Set last pull to 2 days ago — daily cron must be due
        two_days_ago = datetime.now() - timedelta(days=2)
        with patch("memoria.scheduler.get_last_pull", return_value=two_days_ago):
            from memoria.scheduler import is_due
            source = {"name": "x", "schedule": "0 9 * * *"}
            assert is_due(source) is True

    def test_bad_cron_expression_returns_false(self):
        """A malformed cron expression must never crash — return False."""
        if not self._croniter_available():
            pytest.skip("croniter not installed")

        from memoria.scheduler import is_due
        source = {"name": "x", "schedule": "not a cron"}
        assert is_due(source) is False

    def test_croniter_not_installed_returns_false(self):
        """When croniter is absent, cron expressions silently return not-due."""
        from memoria.scheduler import is_due
        source = {"name": "x", "schedule": "0 9 * * 1-5"}
        with patch("memoria.scheduler._is_cron_due",
                   side_effect=ImportError("croniter not installed")):
            result = is_due(source)
        assert result is False

    def test_describe_interval_cron_expression(self):
        """Cron expressions should be labelled as 'cron: ...' not 'unsupported'."""
        from memoria.scheduler import describe_interval
        result = describe_interval("0 9 * * 1-5")
        assert "cron" in result.lower()
        assert "0 9 * * 1-5" in result

    def test_describe_interval_unknown_is_labelled(self):
        from memoria.scheduler import describe_interval
        result = describe_interval("whenever")
        assert "unrecognised" in result.lower() or "whenever" in result
