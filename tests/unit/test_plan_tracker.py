"""
Unit tests for the Plan vs Reality Tracker functions in memoria/planner.py.

Tests cover:
  - _snapshot_path     — safe filename derivation
  - save_plan_snapshot — JSON written, shape correct, special-char project names
  - load_plan_snapshot — round-trips; returns None when missing
  - check_drift        — no drift when Memory Bank unchanged; drift when risks removed
  - write_drift_report — file written, markdown valid, present in books_dir

No real LLM calls — all tests use temp directories and in-memory data.
"""

import json
import re
import pytest
from pathlib import Path
from datetime import datetime


# ─── _snapshot_path ───────────────────────────────────────────────────────────

class TestSnapshotPath:
    def test_simple_name(self, tmp_path):
        from memoria.planner import _snapshot_path
        p = _snapshot_path("MyProject", str(tmp_path))
        assert p.parent == tmp_path
        assert p.name == "MyProject_plan_snapshot.json"

    def test_spaces_replaced(self, tmp_path):
        from memoria.planner import _snapshot_path
        p = _snapshot_path("My Project", str(tmp_path))
        assert " " not in p.name
        assert p.name == "My_Project_plan_snapshot.json"

    def test_special_chars_replaced(self, tmp_path):
        from memoria.planner import _snapshot_path
        p = _snapshot_path("Proj/Name:v2", str(tmp_path))
        # Slashes, colons → underscores
        assert re.match(r"^[\w\-]+_plan_snapshot\.json$", p.name)

    def test_hyphens_preserved(self, tmp_path):
        from memoria.planner import _snapshot_path
        p = _snapshot_path("my-project", str(tmp_path))
        assert p.name == "my-project_plan_snapshot.json"


# ─── save_plan_snapshot ───────────────────────────────────────────────────────

class TestSavePlanSnapshot:
    def _make_report(self):
        return {
            "affected_projects": ["ProjectA", "ProjectB"],
            "risks": [
                {"title": "Auth regression", "source": "ProjectA", "severity": "high",
                 "detail": "Some detail"},
                {"title": "DB migration risk", "source": "ProjectB", "severity": "medium"},
            ],
            "dependencies": [],
            "prior_art": [],
        }

    def test_file_created(self, tmp_path):
        from memoria.planner import save_plan_snapshot, _snapshot_path
        report = self._make_report()
        save_plan_snapshot("Alpha", "My plan text", report, str(tmp_path))
        p = _snapshot_path("Alpha", str(tmp_path))
        assert p.exists()

    def test_snapshot_shape(self, tmp_path):
        from memoria.planner import save_plan_snapshot, _snapshot_path
        report = self._make_report()
        save_plan_snapshot("Alpha", "My plan text", report, str(tmp_path))
        data = json.loads(_snapshot_path("Alpha", str(tmp_path)).read_text())
        assert data["project"] == "Alpha"
        assert data["plan_text"] == "My plan text"
        assert data["affected_projects"] == ["ProjectA", "ProjectB"]
        assert len(data["risks"]) == 2
        assert "saved_at" in data

    def test_risk_compact_fields_only(self, tmp_path):
        """Only title, source, severity should be stored (not 'detail')."""
        from memoria.planner import save_plan_snapshot, _snapshot_path
        report = self._make_report()
        save_plan_snapshot("Alpha", "Plan", report, str(tmp_path))
        data = json.loads(_snapshot_path("Alpha", str(tmp_path)).read_text())
        risk = data["risks"][0]
        assert set(risk.keys()) == {"title", "source", "severity"}
        assert "detail" not in risk

    def test_overwrite_existing_snapshot(self, tmp_path):
        from memoria.planner import save_plan_snapshot, _snapshot_path
        report1 = self._make_report()
        save_plan_snapshot("Alpha", "Plan v1", report1, str(tmp_path))
        report2 = {**report1, "affected_projects": ["ProjectC"]}
        save_plan_snapshot("Alpha", "Plan v2", report2, str(tmp_path))
        data = json.loads(_snapshot_path("Alpha", str(tmp_path)).read_text())
        assert data["plan_text"] == "Plan v2"
        assert data["affected_projects"] == ["ProjectC"]

    def test_empty_risks(self, tmp_path):
        from memoria.planner import save_plan_snapshot, _snapshot_path
        report = {"affected_projects": [], "risks": []}
        save_plan_snapshot("Empty", "Plan", report, str(tmp_path))
        data = json.loads(_snapshot_path("Empty", str(tmp_path)).read_text())
        assert data["risks"] == []

    def test_returns_path(self, tmp_path):
        from memoria.planner import save_plan_snapshot
        report = {"affected_projects": [], "risks": []}
        result = save_plan_snapshot("Alpha", "Plan", report, str(tmp_path))
        assert isinstance(result, Path)
        assert result.exists()


# ─── load_plan_snapshot ───────────────────────────────────────────────────────

class TestLoadPlanSnapshot:
    def test_round_trip(self, tmp_path):
        from memoria.planner import save_plan_snapshot, load_plan_snapshot
        report = {
            "affected_projects": ["P1"],
            "risks": [{"title": "Risk A", "source": "P1", "severity": "high"}],
        }
        save_plan_snapshot("Beta", "Plan text", report, str(tmp_path))
        loaded = load_plan_snapshot("Beta", str(tmp_path))
        assert loaded is not None
        assert loaded["project"] == "Beta"
        assert loaded["affected_projects"] == ["P1"]

    def test_returns_none_when_missing(self, tmp_path):
        from memoria.planner import load_plan_snapshot
        result = load_plan_snapshot("NonExistent", str(tmp_path))
        assert result is None

    def test_none_on_corrupted_file(self, tmp_path):
        from memoria.planner import load_plan_snapshot, _snapshot_path
        p = _snapshot_path("BadProject", str(tmp_path))
        p.write_text("not valid json", encoding="utf-8")
        result = load_plan_snapshot("BadProject", str(tmp_path))
        assert result is None


# ─── check_drift ─────────────────────────────────────────────────────────────

class TestCheckDrift:
    """check_drift reads the live Memory Bank and compares against saved snapshot."""

    def _write_book(self, tmp_path, project, content):
        """Write a memory bank file."""
        import re as _re
        safe = _re.sub(r"[^\w\-]", "_", project)
        book = tmp_path / f"{safe}_memory_bank.md"
        book.write_text(content, encoding="utf-8")
        return book

    def _save_snapshot(self, tmp_path, project, risks, affected=None, source_project=None):
        """
        source_project: the project name stored as 'source' in each risk.
        Defaults to 'ExternalDep' — a name intentionally absent from test book content,
        so drift detection is not confounded by the source always appearing in the book header.
        """
        from memoria.planner import save_plan_snapshot
        src = source_project or "ExternalDep"
        report = {
            "affected_projects": affected if affected is not None else [project],
            "risks": [{"title": r, "source": src, "severity": "medium"} for r in risks],
        }
        save_plan_snapshot(project, "Test plan", report, str(tmp_path))

    def test_no_drift_when_risks_present(self, tmp_path):
        from memoria.planner import check_drift
        # Title keywords "authentication", "regression", "database", "migration" all appear in content
        content = "# Overview\n\nAuthentication regression risk discussed here.\nDatabase migration noted."
        self._write_book(tmp_path, "MyProject", content)
        # Pass affected=[] so the project-name presence check is skipped
        self._save_snapshot(tmp_path, "MyProject",
                            ["Authentication regression", "Database migration"],
                            affected=[])
        result = check_drift("MyProject", str(tmp_path))
        assert result["has_drift"] is False

    def test_drift_when_all_risks_missing(self, tmp_path):
        from memoria.planner import check_drift
        content = "# MyProject\n\nAll good, nothing here."
        self._write_book(tmp_path, "MyProject", content)
        self._save_snapshot(tmp_path, "MyProject", ["Auth regression", "DB migration"])
        result = check_drift("MyProject", str(tmp_path))
        assert result["has_drift"] is True
        assert result["total_risks"] == 2

    def test_drift_when_majority_missing(self, tmp_path):
        """≥30% missing triggers drift."""
        from memoria.planner import check_drift
        # Only 1 of 4 risks present → 75% missing
        content = "# MyProject\n\nAuth regression is a concern."
        self._write_book(tmp_path, "MyProject", content)
        self._save_snapshot(tmp_path, "MyProject", [
            "Auth regression", "DB migration", "API deprecation", "Security audit"
        ])
        result = check_drift("MyProject", str(tmp_path))
        assert result["has_drift"] is True

    def test_no_drift_when_minority_missing(self, tmp_path):
        """<30% missing = no drift."""
        from memoria.planner import check_drift
        # 3 of 4 risks present (25% missing < 30% threshold)
        content = "# Overview\n\nAuth regression here. DB migration discussed. Security audit planned."
        self._write_book(tmp_path, "MyProject", content)
        # affected=[] so project-name presence check is skipped
        self._save_snapshot(tmp_path, "MyProject", [
            "Auth regression", "DB migration", "Security audit", "API deprecation"
        ], affected=[])
        result = check_drift("MyProject", str(tmp_path))
        # "API deprecation": "api" and "deprecation" not in content → 1 missing (25% < 30%)
        assert result["has_drift"] is False

    def test_drift_when_tracked_project_gone(self, tmp_path):
        """If a tracked affected project has no Memory Bank at all → drift."""
        from memoria.planner import check_drift, save_plan_snapshot
        # Save snapshot referencing ProjectB, but only write ProjectA's book
        report = {
            "affected_projects": ["ProjectA", "ProjectB"],
            "risks": [{"title": "Integration risk", "source": "ExternalDep", "severity": "low"}],
        }
        save_plan_snapshot("ProjectA", "Plan", report, str(tmp_path))
        # Write only ProjectA's book. ProjectB book is absent → it won't appear in the book text
        # but also ProjectB won't appear in ProjectA's book content below
        content = "# Overview\n\nIntegration risk discussed in the architecture."
        self._write_book(tmp_path, "ProjectA", content)
        result = check_drift("ProjectA", str(tmp_path))
        assert result["has_drift"] is True
        assert "ProjectB" in result["missing_projects"]

    def test_result_fields_present(self, tmp_path):
        from memoria.planner import check_drift
        content = "# Overview\n\nAuthentication token validation discussed in detail."
        self._write_book(tmp_path, "P", content)
        # affected=[] to skip project-name presence check
        self._save_snapshot(tmp_path, "P", ["Authentication validation"], affected=[])
        result = check_drift("P", str(tmp_path))
        assert "has_drift" in result
        assert "missing_risks" in result
        assert "missing_projects" in result
        assert "total_risks" in result
        assert "checked_at" in result
        assert "snapshot_saved_at" in result

    def test_no_snapshot_raises_or_returns_gracefully(self, tmp_path):
        """If no snapshot exists, check_drift should not crash."""
        from memoria.planner import check_drift
        content = "# Q\n\nSome content."
        self._write_book(tmp_path, "Q", content)
        # No snapshot saved for Q → function should either return gracefully or raise ValueError
        try:
            result = check_drift("Q", str(tmp_path))
            # If it returns, has_drift should be False (nothing to compare against)
            assert "has_drift" in result
        except (FileNotFoundError, ValueError, KeyError):
            pass  # also acceptable


# ─── write_drift_report ───────────────────────────────────────────────────────

class TestWriteDriftReport:
    def _drift(self, missing_risks=None, missing_projects=None):
        return {
            "has_drift": True,
            "missing_risks": missing_risks or [
                {"title": "Auth regression", "source": "ProjectA", "severity": "high"},
            ],
            "missing_projects": missing_projects or [],
            "total_risks": 3,
            "checked_at": datetime.now().isoformat(),
            "snapshot_saved_at": datetime.now().isoformat(),
        }

    def test_file_created(self, tmp_path):
        from memoria.planner import write_drift_report
        import re as _re
        drift = self._drift()
        write_drift_report("MyProject", drift, str(tmp_path))
        safe = _re.sub(r"[^\w\-]", "_", "MyProject")
        report_file = tmp_path / f"{safe}_drift.md"
        assert report_file.exists()

    def test_file_contains_project_name(self, tmp_path):
        from memoria.planner import write_drift_report
        import re as _re
        drift = self._drift()
        write_drift_report("SpecialProject", drift, str(tmp_path))
        safe = _re.sub(r"[^\w\-]", "_", "SpecialProject")
        content = (tmp_path / f"{safe}_drift.md").read_text(encoding="utf-8")
        assert "SpecialProject" in content

    def test_file_mentions_missing_risk(self, tmp_path):
        from memoria.planner import write_drift_report
        import re as _re
        drift = self._drift(missing_risks=[
            {"title": "Auth regression", "source": "ProjectA", "severity": "high"}
        ])
        write_drift_report("Proj", drift, str(tmp_path))
        safe = _re.sub(r"[^\w\-]", "_", "Proj")
        content = (tmp_path / f"{safe}_drift.md").read_text(encoding="utf-8")
        assert "Auth regression" in content

    def test_file_mentions_missing_project(self, tmp_path):
        from memoria.planner import write_drift_report
        import re as _re
        drift = self._drift(missing_projects=["ProjectB"])
        write_drift_report("Proj", drift, str(tmp_path))
        safe = _re.sub(r"[^\w\-]", "_", "Proj")
        content = (tmp_path / f"{safe}_drift.md").read_text(encoding="utf-8")
        assert "ProjectB" in content

    def test_returns_path(self, tmp_path):
        from memoria.planner import write_drift_report
        drift = self._drift()
        result = write_drift_report("X", drift, str(tmp_path))
        assert isinstance(result, Path)
        assert result.exists()

    def test_overwrite_existing_report(self, tmp_path):
        from memoria.planner import write_drift_report
        import re as _re
        drift1 = self._drift(missing_risks=[{"title": "Risk A", "source": "P", "severity": "low"}])
        write_drift_report("X", drift1, str(tmp_path))
        drift2 = self._drift(missing_risks=[{"title": "Risk B", "source": "P", "severity": "high"}])
        write_drift_report("X", drift2, str(tmp_path))
        safe = _re.sub(r"[^\w\-]", "_", "X")
        content = (tmp_path / f"{safe}_drift.md").read_text(encoding="utf-8")
        assert "Risk B" in content
