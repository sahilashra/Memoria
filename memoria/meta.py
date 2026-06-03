"""
Project metadata store — lightweight JSON file tracking repo paths and
analysis history so Update from UI knows where to find source files.

Stored at ~/.memoria/project_meta.json
Schema:
{
  "ProjectName": {
    "repo_path":     "/abs/path/to/repo",
    "context":       "What this project does",
    "last_analyzed": "2026-05-02T14:22:01"
  }
}
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


_META_PATH = Path.home() / ".memoria" / "project_meta.json"


def _load() -> dict:
    if _META_PATH.exists():
        try:
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    _META_PATH.parent.mkdir(parents=True, exist_ok=True)
    _META_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def record(project_name: str, repo_path: str, context: str = "") -> None:
    """Called after every successful book generation to record the source path."""
    data = _load()
    data[project_name] = {
        "repo_path":     str(Path(repo_path).resolve()),
        "context":       context,
        "last_analyzed": datetime.now().isoformat(timespec="seconds"),
    }
    _save(data)


def get(project_name: str) -> Optional[dict]:
    """Return metadata for a project, or None if not recorded."""
    return _load().get(project_name)


def all_projects() -> dict:
    """Return all recorded project metadata."""
    return _load()
