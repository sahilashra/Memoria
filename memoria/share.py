"""
Consultant Mode — read-only share tokens for scoped external access.

Tokens are stored at ~/.memoria/share_tokens.json.

Schema:
{
  "tok_<hex>": {
    "token":      "tok_<hex>",
    "label":      "Acme Inc consultant",
    "projects":   ["ProjectA", "ProjectB"],   # [] means all projects
    "expires_at": "2024-01-15T10:00:00" | null,
    "created_at": "2024-01-08T10:00:00"
  }
}

Access model:
- GET requests: always allowed for valid tokens (filtered by projects if scoped)
- POST /api/ask, /api/global-ask, /api/search, /api/plan/stream, /api/plan/chat: allowed
- All other POST/PUT/DELETE: blocked with 403
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


_TOKENS_PATH = Path.home() / ".memoria" / "share_tokens.json"

# POST endpoints that are allowed in read-only share mode (AI queries, no writes)
SHARE_ALLOWED_POSTS = {
    "/api/ask",
    "/api/global-ask",
    "/api/search",
    "/api/plan/stream",
    "/api/plan/chat",
}


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _load() -> dict:
    if _TOKENS_PATH.exists():
        try:
            return json.loads(_TOKENS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    _TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKENS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _parse_expires(expires_str: Optional[str]) -> Optional[str]:
    """
    Parse human-readable expiry like '7d', '30d', '24h', '2h' into an ISO timestamp.
    Returns None if no expiry specified.
    """
    if not expires_str:
        return None
    s = expires_str.strip().lower()
    try:
        if s.endswith("d"):
            delta = timedelta(days=int(s[:-1]))
        elif s.endswith("h"):
            delta = timedelta(hours=int(s[:-1]))
        else:
            raise ValueError()
    except (ValueError, IndexError):
        raise ValueError(
            f"Unknown expiry format: {expires_str!r}. "
            "Use days like '7d' or hours like '24h'."
        )
    return (datetime.now() + delta).isoformat()


# ─── Public API ───────────────────────────────────────────────────────────────

def generate_token(
    projects: list[str],
    label: str = "",
    expires: Optional[str] = None,
) -> dict:
    """
    Create a new share token, persist it, and return the full record.

    Args:
        projects: List of project names to scope access to. Pass [] for all projects.
        label:    Human-readable label (e.g. "Acme Inc consultant").
        expires:  Expiry string like '7d' or '24h'. None means no expiry.

    Returns:
        Token record dict including the ``token`` field.
    """
    token_str  = "tok_" + secrets.token_hex(16)
    expires_at = _parse_expires(expires)
    record: dict = {
        "token":      token_str,
        "label":      label,
        "projects":   list(projects),
        "expires_at": expires_at,
        "created_at": datetime.now().isoformat(),
    }
    data = _load()
    data[token_str] = record
    _save(data)
    return record


def revoke_token(token_str: str) -> bool:
    """Revoke a specific token. Returns True if it existed, False if not found."""
    data = _load()
    if token_str in data:
        del data[token_str]
        _save(data)
        return True
    return False


def revoke_all() -> int:
    """Revoke all tokens. Returns the count removed."""
    data = _load()
    count = len(data)
    _save({})
    return count


def list_tokens() -> list[dict]:
    """Return all tokens (expired ones are included but flagged with is_expired=True)."""
    data = _load()
    now = datetime.now()
    result = []
    for record in data.values():
        exp = record.get("expires_at")
        r = dict(record)
        r["is_expired"] = bool(exp and datetime.fromisoformat(exp) < now)
        result.append(r)
    # Most recently created first
    result.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return result


def validate_token(token_str: str) -> Optional[dict]:
    """
    Validate a token string.

    Returns the token record if valid, or None if not found / expired.
    Lazily removes expired tokens from the store on access.
    """
    if not token_str:
        return None
    data = _load()
    record = data.get(token_str)
    if record is None:
        return None
    exp = record.get("expires_at")
    if exp and datetime.fromisoformat(exp) < datetime.now():
        # Lazily purge expired token
        del data[token_str]
        _save(data)
        return None
    return record


def is_project_allowed(token_record: dict, project: str) -> bool:
    """
    Return True if the project is accessible under the given token.
    An empty projects list means all projects are allowed.
    """
    allowed = token_record.get("projects") or []
    return not allowed or project in allowed
