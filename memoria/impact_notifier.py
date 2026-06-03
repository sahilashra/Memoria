"""
Change Impact Notifications — when a commit lands on a tracked repo, compute
its blast radius against the knowledge graph and push alerts to downstream
project owners before they find out the hard way.

──────────────────────────────────────────────────────────────────────────────
Flow
──────────────────────────────────────────────────────────────────────────────

  git commit detected by tracker.py
          │
          ▼
  repo path → project name   (via meta.py + heuristic fallback)
          │
          ▼
  impact_analysis(project, graph)   (graph.py — BFS over depends_on edges)
          │
          ├── no dependents → silent (no noise for leaf-node changes)
          │
          └── dependents found →
                  ├── desktop notification  (notifier.py)
                  ├── Slack webhook         (optional, config.yaml)
                  └── activity log entry   (tracker.py record_event)

──────────────────────────────────────────────────────────────────────────────
Config (config.yaml)
──────────────────────────────────────────────────────────────────────────────

  impact_notifications:
    enabled:          true
    min_depth:        1        # only notify if dependents exist
    slack_webhook:    "https://hooks.slack.com/..."
    desktop:          true     # OS notification via notifier.py
    ignored_projects: []       # projects whose commits should not trigger alerts
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional


# ─── Constants ────────────────────────────────────────────────────────────────

MEMORIA_DIR   = Path.home() / ".memoria"
_NOTIF_LOG    = MEMORIA_DIR / "impact_notifications.jsonl"


# ─── Config ───────────────────────────────────────────────────────────────────

def _load_notif_config() -> dict:
    cfg_path = MEMORIA_DIR / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        import yaml
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return raw.get("impact_notifications", {})
    except Exception:
        return {}


def is_enabled() -> bool:
    cfg = _load_notif_config()
    # Default to True if the key exists at all; False if not configured
    return cfg.get("enabled", False)


# ─── Project name resolution ──────────────────────────────────────────────────

def _repo_path_to_project(repo_path: str) -> Optional[str]:
    """
    Map a repo directory path to its Memory Bank project name.

    Strategy (in order):
      1. Look up meta.py store (exact repo_path match)
      2. Fuzzy: match by folder name against all known project names
      3. Return None if no match found
    """
    from .meta import all_projects

    resolved = str(Path(repo_path).resolve())
    meta = all_projects()

    # 1. Exact match
    for proj_name, info in meta.items():
        if str(Path(info.get("repo_path", "")).resolve()) == resolved:
            return proj_name

    # 2. Folder-name fuzzy match
    folder = Path(repo_path).name.lower()
    for proj_name in meta:
        if proj_name.lower().replace(" ", "_").replace("-", "_") == folder.replace("-", "_"):
            return proj_name

    # 3. Last resort: use folder name directly (may not match a graph node)
    return Path(repo_path).name


# ─── Graph impact lookup ──────────────────────────────────────────────────────

def _get_impact(project_name: str, books_dir: str) -> list[dict]:
    """
    Run impact_analysis for project_name against the stored graph.
    Returns [] if graph doesn't exist or project isn't in it.
    """
    try:
        from .graph import get_graph, impact_analysis
        graph = get_graph(books_dir)
        if not graph.get("nodes"):
            return []
        return impact_analysis(project_name, graph)
    except Exception:
        return []


# ─── Notification formatting ──────────────────────────────────────────────────

def _format_desktop(project: str, commit_msg: str, dependents: list[dict]) -> tuple[str, str]:
    """Return (title, body) for a desktop notification."""
    n = len(dependents)
    title = f"Memoria — {project} changed"
    dep_names = ", ".join(d["project"] for d in dependents[:3])
    if n > 3:
        dep_names += f" +{n - 3} more"
    body = f"{commit_msg[:60]}\n⚠ {n} downstream: {dep_names}"
    return title, body


def _format_slack(project: str, commit_msg: str, dependents: list[dict]) -> str:
    """Return a Slack message string."""
    lines = [
        f"*:warning: Impact Alert — `{project}` changed*",
        f"> {commit_msg[:120]}",
        f"",
        f"*Downstream projects affected ({len(dependents)}):*",
    ]
    for d in dependents[:8]:
        depth_label = "direct" if d["depth"] == 1 else f"depth {d['depth']}"
        lines.append(f"  • *{d['project']}* ({depth_label})  `{d['path']}`")
    if len(dependents) > 8:
        lines.append(f"  _…and {len(dependents) - 8} more_")
    lines.append(f"\n_Run `memoria graph impact {project}` for full analysis._")
    return "\n".join(lines)


# ─── Delivery ─────────────────────────────────────────────────────────────────

def _send_desktop(title: str, body: str) -> None:
    try:
        from .notifier import send_notification
        send_notification(title=title, body=body)
    except Exception:
        pass


def _send_slack(message: str, webhook_url: str) -> None:
    try:
        payload = json.dumps({"text": message}).encode()
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def _log_notification(entry: dict) -> None:
    """Append a notification record to the JSONL log."""
    try:
        MEMORIA_DIR.mkdir(parents=True, exist_ok=True)
        with _NOTIF_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def get_recent_notifications(limit: int = 50) -> list[dict]:
    """Return the most recent impact notifications (newest first)."""
    if not _NOTIF_LOG.exists():
        return []
    try:
        lines = _NOTIF_LOG.read_text(encoding="utf-8").splitlines()
        records = []
        for line in reversed(lines):
            try:
                records.append(json.loads(line))
                if len(records) >= limit:
                    break
            except Exception:
                pass
        return records
    except Exception:
        return []


# ─── Main entry point (called by tracker.py on every git commit) ──────────────

def on_commit(
    repo_path:   str,
    commit_msg:  str,
    books_dir:   Optional[str] = None,
) -> None:
    """
    Called by the tracker daemon whenever a new git commit is detected.
    Does nothing if impact notifications are disabled or no dependents found.
    """
    cfg = _load_notif_config()

    if not cfg.get("enabled", False):
        return

    ignored = [p.lower() for p in cfg.get("ignored_projects", [])]

    # Resolve books_dir
    if not books_dir:
        books_dir = str(MEMORIA_DIR / "books")

    # Resolve project name
    project = _repo_path_to_project(repo_path)
    if not project:
        return

    if project.lower() in ignored:
        return

    # Compute blast radius
    dependents = _get_impact(project, books_dir)
    min_depth  = cfg.get("min_depth", 1)
    dependents = [d for d in dependents if d["depth"] >= min_depth]

    if not dependents:
        return   # leaf-node commit — no noise

    # Build notification content
    title, desktop_body = _format_desktop(project, commit_msg, dependents)
    slack_msg = _format_slack(project, commit_msg, dependents)

    # Desktop notification
    if cfg.get("desktop", True):
        _send_desktop(title, desktop_body)

    # Slack
    webhook = cfg.get("slack_webhook", "")
    if webhook:
        _send_slack(slack_msg, webhook)

    # Log
    _log_notification({
        "ts":          datetime.now().isoformat(),
        "project":     project,
        "repo_path":   repo_path,
        "commit_msg":  commit_msg,
        "dependents":  [d["project"] for d in dependents],
        "depth_max":   max(d["depth"] for d in dependents),
        "notified_via": (
            ["desktop"] * int(cfg.get("desktop", True))
            + (["slack"] if webhook else [])
        ),
    })
