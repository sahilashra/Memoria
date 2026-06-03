"""
Queryable Org Chart — maps people, teams, and domains to the projects they own.

Answers questions like:
  - "Who owns the checkout service?"
  - "What does the Backend team own?"
  - "Which projects have no owner?"
  - "Who do I ping about auth-service?"

──────────────────────────────────────────────────────────────────────────────
Data store  (~/.memoria/org_chart.yaml)
──────────────────────────────────────────────────────────────────────────────

  projects:
    auth-service:
      owner:  "Jane Smith"
      team:   "Backend"
      email:  "jane@company.com"
      slack:  "@janesmith"
      domain: "authentication"
      notes:  "Primary contact for OAuth and JWT questions"

    checkout:
      owner:  "Bob Lee"
      team:   "Payments"
      email:  "bob@company.com"
      domain: "payments"

  teams:
    Backend:
      lead:   "Jane Smith"
      slack:  "#backend-eng"
    Payments:
      lead:   "Bob Lee"
      slack:  "#payments-team"

──────────────────────────────────────────────────────────────────────────────
Public API
──────────────────────────────────────────────────────────────────────────────

  set_owner(project, owner, team, email, slack, domain, notes)
  get_owner(project)           → dict | None
  list_by_team(team)           → list[dict]
  list_unowned(books_dir)      → list[str]  (projects with no entry)
  search(query)                → list[dict]  (fuzzy name/team/domain match)
  set_team(team, lead, slack)
  get_team(team)               → dict | None
  list_teams()                 → list[dict]
  all_entries()                → dict
  inject_into_answer(question, answer) → str  (append ownership context)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# ─── Constants ────────────────────────────────────────────────────────────────

MEMORIA_DIR    = Path.home() / ".memoria"
CHART_PATH     = MEMORIA_DIR / "org_chart.yaml"


# ─── I/O ──────────────────────────────────────────────────────────────────────

def _load() -> dict:
    if not CHART_PATH.exists():
        return {"projects": {}, "teams": {}}
    try:
        import yaml
        data = yaml.safe_load(CHART_PATH.read_text(encoding="utf-8")) or {}
        data.setdefault("projects", {})
        data.setdefault("teams", {})
        return data
    except Exception:
        return {"projects": {}, "teams": {}}


def _save(data: dict) -> None:
    import yaml
    MEMORIA_DIR.mkdir(parents=True, exist_ok=True)
    CHART_PATH.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


# ─── Project ownership ────────────────────────────────────────────────────────

def set_owner(
    project: str,
    owner:   Optional[str] = None,
    team:    Optional[str] = None,
    email:   Optional[str] = None,
    slack:   Optional[str] = None,
    domain:  Optional[str] = None,
    notes:   Optional[str] = None,
) -> dict:
    """Set or update ownership for a project. Returns the updated entry."""
    data = _load()
    existing = data["projects"].get(project, {})
    entry = {
        "owner":  owner  or existing.get("owner",  ""),
        "team":   team   or existing.get("team",   ""),
        "email":  email  or existing.get("email",  ""),
        "slack":  slack  or existing.get("slack",  ""),
        "domain": domain or existing.get("domain", ""),
        "notes":  notes  or existing.get("notes",  ""),
    }
    # Strip empty strings to keep the YAML clean
    entry = {k: v for k, v in entry.items() if v}
    data["projects"][project] = entry
    _save(data)
    return entry


def get_owner(project: str) -> Optional[dict]:
    """Return the ownership entry for a project, or None if not registered."""
    return _load()["projects"].get(project)


def remove_project(project: str) -> bool:
    """Remove a project from the org chart. Returns True if it existed."""
    data = _load()
    if project not in data["projects"]:
        return False
    del data["projects"][project]
    _save(data)
    return True


# ─── Team operations ──────────────────────────────────────────────────────────

def set_team(team: str, lead: Optional[str] = None, slack: Optional[str] = None) -> dict:
    data = _load()
    existing = data["teams"].get(team, {})
    entry = {
        "lead":  lead  or existing.get("lead",  ""),
        "slack": slack or existing.get("slack", ""),
    }
    entry = {k: v for k, v in entry.items() if v}
    data["teams"][team] = entry
    _save(data)
    return entry


def get_team(team: str) -> Optional[dict]:
    return _load()["teams"].get(team)


def list_teams() -> list[dict]:
    data = _load()
    result = []
    for team_name, info in data["teams"].items():
        # Count projects owned by this team
        owned = [p for p, e in data["projects"].items() if e.get("team") == team_name]
        result.append({"team": team_name, "projects": owned, **info})
    return sorted(result, key=lambda x: x["team"])


# ─── Queries ──────────────────────────────────────────────────────────────────

def list_by_team(team: str) -> list[dict]:
    """Return all projects owned by a team."""
    data = _load()
    return [
        {"project": p, **e}
        for p, e in data["projects"].items()
        if e.get("team", "").lower() == team.lower()
    ]


def list_unowned(books_dir: Optional[str] = None) -> list[str]:
    """Return project names (from Memory Banks) that have no org chart entry."""
    data = _load()
    registered = set(data["projects"].keys())

    if books_dir:
        from pathlib import Path as _P
        books = [
            p.stem.replace("_memory_bank", "")
            for p in _P(books_dir).glob("*_memory_bank.md")
            if not p.name.endswith("_draft.md")
        ]
    else:
        # Fall back to meta.py
        try:
            from .meta import all_projects
            books = list(all_projects().keys())
        except Exception:
            books = []

    return sorted(p for p in books if p not in registered)


def search(query: str) -> list[dict]:
    """
    Search projects by name, owner, team, domain, or notes.
    Returns a list of matching project entries with the project name included.
    """
    q = query.lower().strip()
    data = _load()
    results = []
    for proj_name, entry in data["projects"].items():
        haystack = " ".join([
            proj_name,
            entry.get("owner", ""),
            entry.get("team", ""),
            entry.get("domain", ""),
            entry.get("notes", ""),
        ]).lower()
        if q in haystack:
            results.append({"project": proj_name, **entry})
    return results


def all_entries() -> dict:
    return _load()


# ─── AI answer injection ───────────────────────────────────────────────────────

# Phrases that suggest the user is asking about ownership
_OWNERSHIP_PATTERNS = re.compile(
    r"\b(who owns?|who maintains?|who is (responsible|in charge)|contact for|"
    r"point of contact|owner of|maintainer of|who should i (ask|ping|contact))\b",
    re.IGNORECASE,
)


def should_inject(question: str) -> bool:
    """Return True if the question is likely asking about project ownership."""
    return bool(_OWNERSHIP_PATTERNS.search(question))


def ownership_context_for(question: str) -> str:
    """
    Build a short ownership context block to append to AI answers when
    the question is about who owns something.

    Tries to extract a project name from the question and look it up.
    Falls back to a search across all fields.
    """
    data = _load()
    if not data["projects"]:
        return ""

    # Extract candidate project names from the question
    # Simple approach: look for quoted terms or capitalised words
    candidates = re.findall(r'"([^"]+)"|\'([^\']+)\'|([A-Z][a-zA-Z0-9_\-]+)', question)
    flat = [c for group in candidates for c in group if c]

    matched_entries = []
    for candidate in flat:
        entry = data["projects"].get(candidate)
        if entry:
            matched_entries.append({"project": candidate, **entry})

    # If no exact match, do a keyword search
    if not matched_entries:
        words = re.findall(r"[a-zA-Z0-9_\-]{3,}", question)
        for w in words:
            hits = search(w)
            for h in hits:
                if h not in matched_entries:
                    matched_entries.append(h)

    if not matched_entries:
        return ""

    lines = ["\n\n---\n**Ownership (from org chart):**"]
    for e in matched_entries[:4]:
        parts = [f"**{e['project']}**"]
        if e.get("owner"):  parts.append(f"Owner: {e['owner']}")
        if e.get("team"):   parts.append(f"Team: {e['team']}")
        if e.get("email"):  parts.append(f"Email: {e['email']}")
        if e.get("slack"):  parts.append(f"Slack: {e['slack']}")
        if e.get("notes"):  parts.append(f"Note: {e['notes']}")
        lines.append(" · ".join(parts))
    return "\n".join(lines)
