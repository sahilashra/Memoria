"""
Cross-Source Intelligence: Ticket Detection & Resolution

Detects issue/ticket IDs embedded in Memory Banks and resolves them
to provide enriched context during ask queries.

Supported patterns:
  - Jira:       PROJ-123, ABC-4567
  - Linear:     LIN-123, TEAM-456
  - GitHub:     #123, org/repo#456
  - Azure DevOps: AB#789

Resolution sources (checked in order):
  1. Local ticket cache (~/.memoria/tickets.json) — fast, offline
  2. MCP sources (if Jira/Linear/GitHub connector configured)
  3. Graceful fallback — returns just the ID with no metadata
"""

import re
import json
import time
from pathlib import Path
from typing import List, Dict, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Ticket patterns
# ─────────────────────────────────────────────────────────────────────────────

# Jira / Linear / generic "PROJ-123" style
_JIRA_PATTERN = re.compile(
    r'\b([A-Z][A-Z0-9]{1,9})-(\d{1,6})\b'
)

# GitHub-style: #123 or org/repo#123
_GITHUB_PATTERN = re.compile(
    r'(?:([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+))?#(\d{1,6})\b'
)

# Azure DevOps: AB#789
_AZDO_PATTERN = re.compile(
    r'\b([A-Z]{2})#(\d{1,6})\b'
)

# Common project prefixes that are NOT tickets (skip false positives)
_SKIP_PREFIXES = {
    "UTF", "ISO", "RFC", "HTTP", "HTML", "JSON", "YAML", "TOML",
    "API", "SDK", "CLI", "SQL", "CSS", "SSE", "GPT", "LLM", "MCP",
    "AWS", "GCP", "CPU", "GPU", "RAM", "SSD", "DNS", "TCP", "UDP",
    "SSH", "TLS", "SSL", "URL", "URI", "FTP", "PDF", "PNG", "JPG",
    "SVG", "WAV", "MP3", "MP4", "MOV", "GIF", "BMP", "ZIP", "TAR",
    "GIT", "NPM", "PIP", "NPX", "ENV", "CWD", "BOM", "EOF", "NUL",
}


# ─────────────────────────────────────────────────────────────────────────────
# Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_tickets(text: str) -> List[Dict]:
    """
    Detect all ticket/issue references in text.
    Returns list of {"id": "PROJ-123", "type": "jira|github|azdo", "project": "PROJ", "number": 123}
    """
    seen = set()
    tickets = []

    # Jira / Linear style
    for match in _JIRA_PATTERN.finditer(text):
        prefix = match.group(1)
        number = match.group(2)

        # Skip false positives
        if prefix in _SKIP_PREFIXES:
            continue
        if len(prefix) > 6:  # too long to be a project key
            continue

        ticket_id = f"{prefix}-{number}"
        if ticket_id not in seen:
            seen.add(ticket_id)
            tickets.append({
                "id": ticket_id,
                "type": "jira",
                "project": prefix,
                "number": int(number),
            })

    # GitHub style
    for match in _GITHUB_PATTERN.finditer(text):
        repo = match.group(1)  # could be None for bare #123
        number = match.group(2)

        # Skip bare #123 if it's likely a heading anchor or small number
        if not repo and int(number) < 10:
            continue

        ticket_id = f"{repo}#{number}" if repo else f"#{number}"
        if ticket_id not in seen:
            seen.add(ticket_id)
            tickets.append({
                "id": ticket_id,
                "type": "github",
                "project": repo or "",
                "number": int(number),
            })

    # Azure DevOps
    for match in _AZDO_PATTERN.finditer(text):
        prefix = match.group(1)
        number = match.group(2)

        if prefix in _SKIP_PREFIXES:
            continue

        ticket_id = f"{prefix}#{number}"
        if ticket_id not in seen:
            seen.add(ticket_id)
            tickets.append({
                "id": ticket_id,
                "type": "azdo",
                "project": prefix,
                "number": int(number),
            })

    return tickets


# ─────────────────────────────────────────────────────────────────────────────
# Ticket Cache — local JSON store for resolved ticket metadata
# ─────────────────────────────────────────────────────────────────────────────

_CACHE_FILE = Path.home() / ".memoria" / "tickets.json"
_CACHE_TTL = 86400 * 7  # 7 days before re-resolving


def _load_cache() -> Dict:
    """Load ticket cache from disk."""
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(cache: Dict):
    """Save ticket cache to disk."""
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def cache_ticket(ticket_id: str, metadata: Dict):
    """Add or update a ticket in the local cache."""
    cache = _load_cache()
    cache[ticket_id] = {
        **metadata,
        "_cached_at": int(time.time()),
    }
    _save_cache(cache)


def get_cached_ticket(ticket_id: str) -> Optional[Dict]:
    """Get a ticket from cache if it exists and isn't expired."""
    cache = _load_cache()
    entry = cache.get(ticket_id)
    if not entry:
        return None
    if time.time() - entry.get("_cached_at", 0) > _CACHE_TTL:
        return None  # expired
    return entry


# ─────────────────────────────────────────────────────────────────────────────
# Resolution — enrich ticket IDs with metadata
# ─────────────────────────────────────────────────────────────────────────────

def resolve_tickets(tickets: List[Dict], config_path: str = "config.yaml") -> List[Dict]:
    """
    Resolve ticket IDs to their metadata (title, status, assignee, etc.).
    Uses cache first, then MCP sources if available.
    Returns enriched ticket dicts.
    """
    resolved = []

    for ticket in tickets:
        ticket_id = ticket["id"]

        # 1. Check cache
        cached = get_cached_ticket(ticket_id)
        if cached:
            resolved.append({**ticket, **cached})
            continue

        # 2. If no cache hit, try to resolve via MCP (best-effort)
        metadata = _resolve_via_mcp(ticket, config_path)
        if metadata:
            cache_ticket(ticket_id, metadata)
            resolved.append({**ticket, **metadata})
        else:
            # 3. Fallback — just return the raw ticket info
            resolved.append(ticket)

    return resolved


def _resolve_via_mcp(ticket: Dict, config_path: str) -> Optional[Dict]:
    """
    Try to resolve a ticket using configured MCP sources.
    Returns metadata dict or None.
    """
    try:
        import yaml
        cfg_path = Path(config_path)
        if not cfg_path.exists():
            cfg_path = Path.home() / ".memoria" / "config.yaml"
        if not cfg_path.exists():
            return None

        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            cfg = yaml.safe_load(f) or {}

        sources = cfg.get("mcp_sources", [])
        if not sources:
            return None

        ticket_type = ticket.get("type", "")

        # Find a relevant MCP source for this ticket type
        for source in sources:
            name = source.get("name", "").lower()

            # Match source to ticket type
            if ticket_type == "jira" and "jira" in name:
                return _resolve_jira_ticket(ticket, source)
            elif ticket_type == "jira" and "linear" in name:
                return _resolve_linear_ticket(ticket, source)
            elif ticket_type == "github" and "github" in name:
                return _resolve_github_ticket(ticket, source)

    except Exception:
        pass

    return None


def _resolve_jira_ticket(ticket: Dict, source: Dict) -> Optional[Dict]:
    """Resolve via Jira MCP (best-effort, non-blocking)."""
    # This will be populated when the MCP pull runs — for now, return None
    # and rely on the cache being populated during pull cycles
    return None


def _resolve_linear_ticket(ticket: Dict, source: Dict) -> Optional[Dict]:
    """Resolve via Linear MCP (best-effort)."""
    return None


def _resolve_github_ticket(ticket: Dict, source: Dict) -> Optional[Dict]:
    """Resolve via GitHub MCP (best-effort)."""
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Query-time enrichment — builds extra context for /api/ask
# ─────────────────────────────────────────────────────────────────────────────

def enrich_context_with_tickets(
    book_content: str,
    question: str,
    config_path: str = "config.yaml",
) -> str:
    """
    Detect ticket IDs in the Memory Bank content and the user's question.
    If any are found and resolvable, append a "Referenced Tickets" section
    to be injected into the system prompt.

    Returns additional context string (empty if no tickets found/resolved).
    """
    # Detect in both the book and the question
    all_text = book_content + "\n" + question
    tickets = detect_tickets(all_text)

    if not tickets:
        return ""

    # Resolve them
    resolved = resolve_tickets(tickets, config_path)

    # Build the enrichment block
    enriched = [t for t in resolved if t.get("title") or t.get("status")]
    if not enriched:
        # Even unresolved tickets are useful — tell the AI what IDs exist
        ticket_list = ", ".join(t["id"] for t in tickets[:20])
        return (
            f"\n\nREFERENCED TICKETS (detected in content):\n"
            f"The following ticket/issue IDs appear in this project: {ticket_list}\n"
            f"These may refer to Jira, Linear, GitHub issues, or similar trackers.\n"
        )

    # Build enriched context
    lines = ["\n\nREFERENCED TICKETS (resolved metadata):"]
    for t in enriched[:15]:  # cap at 15 to avoid token bloat
        line = f"- **{t['id']}**"
        if t.get("title"):
            line += f": {t['title']}"
        if t.get("status"):
            line += f" [{t['status']}]"
        if t.get("assignee"):
            line += f" (assigned: {t['assignee']})"
        if t.get("priority"):
            line += f" priority={t['priority']}"
        lines.append(line)

    lines.append(
        "\nUse this ticket context to provide more informed answers when relevant."
    )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Bulk extraction — scan all books and build ticket index
# ─────────────────────────────────────────────────────────────────────────────

def scan_all_books(books_dir: str = "books") -> Dict[str, List[Dict]]:
    """
    Scan all Memory Banks and return a map of project → tickets found.
    Useful for building the ticket cache and cross-referencing.
    """
    books_path = Path(books_dir)
    result = {}

    for book in books_path.glob("*_memory_bank.md"):
        project = book.stem.replace("_memory_bank", "")
        content = book.read_text(encoding="utf-8")
        tickets = detect_tickets(content)
        if tickets:
            result[project] = tickets

    return result


def build_ticket_index(books_dir: str = "books") -> Dict[str, List[str]]:
    """
    Build an inverted index: ticket_id → [projects that reference it].
    Useful for answering "which projects are affected by PROJ-123?"
    """
    all_tickets = scan_all_books(books_dir)
    index: Dict[str, List[str]] = {}

    for project, tickets in all_tickets.items():
        for ticket in tickets:
            tid = ticket["id"]
            if tid not in index:
                index[tid] = []
            if project not in index[tid]:
                index[tid].append(project)

    return index
