"""
Pull state — tracks last-pull timestamps for incremental and scheduled MCP pulls.

State is stored at ~/.memoria/pull_state.json.
It is updated after every successful pull and read by:
  - Incremental pulls  (mcp_sources.py) — to inject  `since`  into tool args
  - The scheduler      (scheduler.py)   — to decide if a source is due to run
  - The CLI            (cli.py)         — to display last pull time to the user

File format
───────────
{
  "confluence": {
    "last_pull": "2026-04-30T09:00:00.123456",
    "last_book":  "books/confluence_memory_bank.md"
  },
  "slack": {
    "last_pull": "2026-04-29T18:30:00.000000"
  }
}
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

STATE_PATH = Path.home() / ".memoria" / "pull_state.json"


# ─── Private helpers ──────────────────────────────────────────────────────────

def _load() -> dict:
    """Read the state file; return empty dict on any error."""
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(state: dict) -> None:
    """Atomically write the state file, creating parent dirs if needed."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ─── Public API ───────────────────────────────────────────────────────────────

def get_last_pull(source_name: str) -> Optional[datetime]:
    """
    Return the datetime of the last successful pull for this source.
    Returns None if the source has never been pulled.
    """
    ts = _load().get(source_name, {}).get("last_pull")
    if ts:
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None
    return None


def set_last_pull(
    source_name: str,
    dt: Optional[datetime] = None,
    book_path: Optional[str] = None,
) -> None:
    """
    Record a successful pull for this source.
    dt defaults to now. book_path is optional — stored for display only.
    """
    state = _load()
    entry = state.setdefault(source_name, {})
    entry["last_pull"] = (dt or datetime.now()).isoformat()
    if book_path:
        entry["last_book"] = book_path
    _save(state)


def get_all_states() -> dict:
    """
    Return the full state dict.
    Keys are source names; values are dicts with 'last_pull' and optionally 'last_book'.
    Used by  `memoria schedule list`  to display status for all sources.
    """
    return _load()


def clear_state(source_name: str) -> None:
    """
    Remove all pull history for a source.
    Next pull will be treated as a first-ever pull (no `since` injection,
    scheduler considers it immediately due).
    """
    state = _load()
    if source_name in state:
        del state[source_name]
        _save(state)
