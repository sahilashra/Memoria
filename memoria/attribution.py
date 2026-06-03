"""
Attribution Chain — traces causality across sources.

Connects the chain:
  Business Outcome → Ticket/Issue → Pull Request → Memory Bank Section

This enables queries like:
  "What code changes were driven by the latency improvement goal?"
  "Why was this module rewritten?"
  "Which business outcomes did PR #45 contribute to?"

Data flow:
  1. Tickets module detects issue IDs in Memory Banks
  2. GitHub source captures PR data with linked issues
  3. This module builds the connecting graph and surfaces it in Ask responses

The chain is stored in ~/.memoria/attribution.json and rebuilt on demand.
"""

import json
import re
import time
from pathlib import Path
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, asdict


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChainLink:
    """One node in the attribution chain."""
    type: str           # "outcome", "ticket", "pr", "section"
    id: str             # unique identifier
    label: str          # human-readable label
    project: str        # which Memory Bank this belongs to
    metadata: dict      # type-specific extra data


@dataclass
class Chain:
    """A complete attribution chain from outcome to code."""
    outcome: Optional[ChainLink]
    ticket: Optional[ChainLink]
    pr: Optional[ChainLink]
    section: Optional[ChainLink]
    confidence: float   # 0.0 to 1.0 — how certain is this chain


# ─────────────────────────────────────────────────────────────────────────────
# Chain store
# ─────────────────────────────────────────────────────────────────────────────

_CHAIN_FILE = Path.home() / ".memoria" / "attribution.json"


def _load_chains() -> List[Dict]:
    """Load stored attribution chains."""
    if _CHAIN_FILE.exists():
        try:
            return json.loads(_CHAIN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_chains(chains: List[Dict]):
    """Persist attribution chains."""
    _CHAIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CHAIN_FILE.write_text(json.dumps(chains, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Chain building — extract links from available data
# ─────────────────────────────────────────────────────────────────────────────

# PR body/title patterns that link to tickets
_PR_TICKET_PATTERNS = [
    re.compile(r'(?:closes?|fixes?|resolves?)\s+#?([A-Z][A-Z0-9]+-\d+)', re.IGNORECASE),
    re.compile(r'(?:closes?|fixes?|resolves?)\s+#(\d+)', re.IGNORECASE),
    re.compile(r'\[([A-Z][A-Z0-9]+-\d+)\]'),
    re.compile(r'(?:jira|linear|issue)[:\s]+([A-Z][A-Z0-9]+-\d+)', re.IGNORECASE),
]

# Outcome detection patterns in Memory Banks
_OUTCOME_PATTERNS = [
    re.compile(r'(?:goal|objective|outcome|kpi|okr)[:\s]+(.+?)(?:\n|$)', re.IGNORECASE),
    re.compile(r'(?:business\s+)?(?:impact|value|benefit)[:\s]+(.+?)(?:\n|$)', re.IGNORECASE),
    re.compile(r'(?:metric|target)[:\s]+(.+?)(?:\n|$)', re.IGNORECASE),
]


def build_chains(books_dir: str = "books") -> List[Dict]:
    """
    Build attribution chains by cross-referencing:
    - Memory Bank content (sections, tickets, outcomes)
    - GitHub context sidecars (PRs with linked issues)
    - Ticket cache (resolved metadata)

    Returns list of chain dicts and persists them.
    """
    books_path = Path(books_dir)
    chains = []

    # Gather all data
    book_data = _extract_book_data(books_path)
    github_data = _extract_github_data(books_path)
    ticket_data = _get_ticket_cache()

    # Build chains by linking tickets → PRs → sections
    for project, data in book_data.items():
        for ticket_id in data.get("tickets", []):
            chain = _build_chain_for_ticket(
                ticket_id, project, data, github_data, ticket_data
            )
            if chain:
                chains.append(chain)

    # Build chains from PRs that reference tickets
    for project, prs in github_data.items():
        for pr in prs:
            linked_tickets = _extract_pr_tickets(pr)
            for ticket_id in linked_tickets:
                # Check if we already have this chain
                existing = [c for c in chains if c.get("ticket", {}).get("id") == ticket_id]
                if existing:
                    # Enrich existing chain with PR data
                    for c in existing:
                        if not c.get("pr"):
                            c["pr"] = {
                                "type": "pr",
                                "id": f"#{pr.get('number', '?')}",
                                "label": pr.get("title", ""),
                                "project": project,
                                "metadata": {"author": pr.get("author", ""), "merged_at": pr.get("merged_at", "")},
                            }
                else:
                    # New chain starting from PR
                    chains.append({
                        "ticket": {
                            "type": "ticket",
                            "id": ticket_id,
                            "label": ticket_data.get(ticket_id, {}).get("title", ticket_id),
                            "project": project,
                            "metadata": ticket_data.get(ticket_id, {}),
                        },
                        "pr": {
                            "type": "pr",
                            "id": f"#{pr.get('number', '?')}",
                            "label": pr.get("title", ""),
                            "project": project,
                            "metadata": {"author": pr.get("author", ""), "merged_at": pr.get("merged_at", "")},
                        },
                        "confidence": 0.7,
                    })

    _save_chains(chains)
    return chains


def _extract_book_data(books_path: Path) -> Dict[str, Dict]:
    """Extract tickets and outcomes from all Memory Banks."""
    from .tickets import detect_tickets

    result = {}
    for book in books_path.glob("*_memory_bank.md"):
        project = book.stem.replace("_memory_bank", "")
        content = book.read_text(encoding="utf-8")

        tickets = detect_tickets(content)
        outcomes = _detect_outcomes(content)
        sections = _extract_section_map(content)

        result[project] = {
            "tickets": [t["id"] for t in tickets],
            "outcomes": outcomes,
            "sections": sections,
        }

    return result


def _extract_github_data(books_path: Path) -> Dict[str, List[Dict]]:
    """Extract PR data from GitHub context sidecars."""
    result = {}
    for ctx_file in books_path.glob("*_github_context.md"):
        project = ctx_file.stem.replace("_github_context", "")
        content = ctx_file.read_text(encoding="utf-8")
        prs = _parse_github_context_prs(content)
        if prs:
            result[project] = prs
    return result


def _get_ticket_cache() -> Dict:
    """Load the ticket resolution cache."""
    cache_file = Path.home() / ".memoria" / "tickets.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _detect_outcomes(content: str) -> List[str]:
    """Detect business outcomes mentioned in a Memory Bank."""
    outcomes = []
    for pattern in _OUTCOME_PATTERNS:
        for match in pattern.finditer(content):
            outcome = match.group(1).strip()
            if len(outcome) > 10 and len(outcome) < 200:
                outcomes.append(outcome)
    return outcomes[:10]  # cap to avoid noise


def _extract_section_map(content: str) -> Dict[str, str]:
    """Build a map of section_heading → first 200 chars of content."""
    sections = {}
    current = "overview"
    current_lines = []

    for line in content.splitlines():
        if line.startswith("## "):
            if current_lines:
                sections[current] = "\n".join(current_lines)[:200]
            current = line.lstrip("#").strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections[current] = "\n".join(current_lines)[:200]

    return sections


def _parse_github_context_prs(content: str) -> List[Dict]:
    """Parse PRs from a GitHub context markdown file."""
    prs = []
    current_pr = None

    for line in content.splitlines():
        # Match PR headers like "### PR #123: Title here"
        pr_match = re.match(r'###\s+PR\s+#(\d+):\s+(.+)', line)
        if pr_match:
            if current_pr:
                prs.append(current_pr)
            current_pr = {
                "number": int(pr_match.group(1)),
                "title": pr_match.group(2).strip(),
                "author": "",
                "merged_at": "",
                "body": "",
            }
            continue

        if current_pr:
            if line.startswith("- **Author:**"):
                current_pr["author"] = line.split(":", 1)[1].strip().strip("*")
            elif line.startswith("- **Merged:**"):
                current_pr["merged_at"] = line.split(":", 1)[1].strip().strip("*")
            elif line.startswith("- **Summary:**"):
                current_pr["body"] = line.split(":", 1)[1].strip().strip("*")

    if current_pr:
        prs.append(current_pr)

    return prs


def _extract_pr_tickets(pr: Dict) -> List[str]:
    """Extract ticket IDs referenced in a PR title or body."""
    tickets = set()
    text = f"{pr.get('title', '')} {pr.get('body', '')}"

    for pattern in _PR_TICKET_PATTERNS:
        for match in pattern.finditer(text):
            tickets.add(match.group(1))

    return list(tickets)


def _build_chain_for_ticket(
    ticket_id: str,
    project: str,
    book_data: Dict,
    github_data: Dict[str, List[Dict]],
    ticket_data: Dict,
) -> Optional[Dict]:
    """Build a chain for a specific ticket found in a Memory Bank."""
    chain = {
        "ticket": {
            "type": "ticket",
            "id": ticket_id,
            "label": ticket_data.get(ticket_id, {}).get("title", ticket_id),
            "project": project,
            "metadata": ticket_data.get(ticket_id, {}),
        },
        "confidence": 0.5,
    }

    # Find matching PRs
    for pr_project, prs in github_data.items():
        for pr in prs:
            pr_tickets = _extract_pr_tickets(pr)
            if ticket_id in pr_tickets:
                chain["pr"] = {
                    "type": "pr",
                    "id": f"#{pr['number']}",
                    "label": pr.get("title", ""),
                    "project": pr_project,
                    "metadata": {"author": pr.get("author", ""), "merged_at": pr.get("merged_at", "")},
                }
                chain["confidence"] = 0.8
                break

    # Find related outcome
    outcomes = book_data.get("outcomes", [])
    if outcomes:
        chain["outcome"] = {
            "type": "outcome",
            "id": f"outcome_{project}_{hash(outcomes[0]) % 10000}",
            "label": outcomes[0],
            "project": project,
            "metadata": {},
        }
        chain["confidence"] = min(chain["confidence"] + 0.1, 1.0)

    # Find related section
    sections = book_data.get("sections", {})
    for section_name, section_text in sections.items():
        if ticket_id in section_text:
            chain["section"] = {
                "type": "section",
                "id": f"{project}#{section_name}",
                "label": section_name,
                "project": project,
                "metadata": {"excerpt": section_text[:100]},
            }
            break

    return chain


# ─────────────────────────────────────────────────────────────────────────────
# Query-time attribution — used by Global Ask
# ─────────────────────────────────────────────────────────────────────────────

def get_attribution_context(question: str, books_dir: str = "books") -> str:
    """
    Given a user question, check if any attribution chains are relevant
    and format them for injection into the system prompt.
    Returns ready-to-append context string, or empty.
    """
    chains = _load_chains()
    if not chains:
        return ""

    # Simple relevance check: does the question mention any ticket/PR/project in our chains?
    question_lower = question.lower()
    relevant = []

    for chain in chains:
        relevance_score = 0

        # Check ticket ID
        ticket = chain.get("ticket", {})
        if ticket.get("id", "").lower() in question_lower:
            relevance_score += 3

        # Check PR number
        pr = chain.get("pr", {})
        if pr.get("id", "").lower() in question_lower:
            relevance_score += 3

        # Check project name
        for node in [ticket, pr, chain.get("section", {}), chain.get("outcome", {})]:
            proj = node.get("project", "")
            if proj and proj.lower() in question_lower:
                relevance_score += 1

        # Check outcome keywords
        outcome = chain.get("outcome", {})
        if outcome.get("label"):
            outcome_words = set(outcome["label"].lower().split())
            question_words = set(question_lower.split())
            overlap = outcome_words & question_words
            if len(overlap) >= 2:
                relevance_score += 2

        if relevance_score > 0:
            relevant.append((relevance_score, chain))

    if not relevant:
        return ""

    # Sort by relevance, take top 5
    relevant.sort(key=lambda x: x[0], reverse=True)
    top_chains = [c for _, c in relevant[:5]]

    # Format for prompt
    lines = ["\n\nATTRIBUTION CHAINS (causal traceability):"]
    lines.append("These chains connect business outcomes to implementation:")
    lines.append("")

    for chain in top_chains:
        parts = []
        if chain.get("outcome"):
            parts.append(f"Outcome: {chain['outcome']['label']}")
        if chain.get("ticket"):
            t = chain["ticket"]
            parts.append(f"Ticket: {t['id']} ({t.get('label', '')})")
        if chain.get("pr"):
            p = chain["pr"]
            parts.append(f"PR: {p['id']} — {p.get('label', '')}")
        if chain.get("section"):
            s = chain["section"]
            parts.append(f"Section: [{s['project']}] {s.get('label', '')}")

        if parts:
            lines.append(f"- {'  →  '.join(parts)}")

    lines.append("")
    lines.append("Use these chains to explain WHY changes were made, not just WHAT changed.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_chains_summary(books_dir: str = "books") -> Dict:
    """Return a summary of all stored attribution chains (for API/UI)."""
    chains = _load_chains()
    return {
        "total_chains": len(chains),
        "chains": chains[:50],  # cap for API response size
        "has_outcomes": sum(1 for c in chains if c.get("outcome")),
        "has_prs": sum(1 for c in chains if c.get("pr")),
        "has_sections": sum(1 for c in chains if c.get("section")),
    }
