"""
GitHub Source — lightweight context from recent PRs and commit messages.

Unlike MCP sources (external servers), this is a built-in source that uses:
  1. Local git log (always available)
  2. GitHub API via `gh` CLI (optional, for PR details)

The output is stored as a sidecar file alongside the Memory Bank:
  books/{project}_github_context.md

This context is automatically injected into /api/ask when present,
giving the AI awareness of recent development activity without
requiring a full Memory Bank regeneration.
"""

import json
import subprocess
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Git log extraction (always available — just needs a git repo)
# ─────────────────────────────────────────────────────────────────────────────

def get_recent_commits(
    repo_path: str,
    days: int = 14,
    max_commits: int = 50,
) -> List[Dict]:
    """
    Get recent commits from a local git repo.
    Returns list of {hash, author, date, message, files_changed}.
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        result = subprocess.run(
            [
                "git", "log",
                f"--since={since}",
                f"-n{max_commits}",
                "--pretty=format:%H|%an|%aI|%s",
                "--stat",
            ],
            cwd=repo_path,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []

        output = result.stdout.decode("utf-8", errors="replace")
        return _parse_git_log(output)

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def _parse_git_log(output: str) -> List[Dict]:
    """Parse git log --stat output into structured data."""
    commits = []
    current = None
    files_lines = []

    for line in output.splitlines():
        # Commit header line: hash|author|date|subject
        if "|" in line and len(line.split("|")) >= 4:
            parts = line.split("|", 3)
            if len(parts[0]) == 40:  # SHA hash
                if current:
                    current["files_changed"] = _extract_files(files_lines)
                    commits.append(current)
                    files_lines = []
                current = {
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                    "files_changed": [],
                }
                continue

        # Stat lines (file changes)
        if current and line.strip() and not line.startswith(" "):
            files_lines.append(line)

    # Last commit
    if current:
        current["files_changed"] = _extract_files(files_lines)
        commits.append(current)

    return commits


def _extract_files(lines: List[str]) -> List[str]:
    """Extract file names from git stat output lines."""
    files = []
    for line in lines:
        # Typical: " src/main.py | 5 ++-"
        match = re.match(r'\s+(.+?)\s+\|\s+\d+', line)
        if match:
            files.append(match.group(1).strip())
    return files


# ─────────────────────────────────────────────────────────────────────────────
# GitHub PR extraction (optional — requires `gh` CLI)
# ─────────────────────────────────────────────────────────────────────────────

def get_recent_prs(
    repo_path: str,
    days: int = 30,
    max_prs: int = 20,
    state: str = "merged",
) -> List[Dict]:
    """
    Get recent PRs using GitHub CLI (`gh`).
    Returns list of {number, title, author, state, merged_at, body, labels, files}.
    Falls back gracefully if gh isn't installed or not authenticated.
    """
    try:
        # Check if gh is available
        subprocess.run(["gh", "--version"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        cmd = [
            "gh", "pr", "list",
            "--state", state,
            "--limit", str(max_prs),
            "--json", "number,title,author,state,mergedAt,body,labels,files",
            "--search", f"merged:>={since}" if state == "merged" else f"updated:>={since}",
        ]

        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            timeout=30,
        )

        if result.returncode != 0:
            return []

        prs = json.loads(result.stdout.decode("utf-8", errors="replace"))
        return _normalize_prs(prs)

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def _normalize_prs(raw_prs: list) -> List[Dict]:
    """Normalize PR data from gh CLI JSON output."""
    prs = []
    for pr in raw_prs:
        prs.append({
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "author": pr.get("author", {}).get("login", "unknown") if isinstance(pr.get("author"), dict) else str(pr.get("author", "")),
            "state": pr.get("state", ""),
            "merged_at": pr.get("mergedAt", ""),
            "body": (pr.get("body") or "")[:500],  # truncate long descriptions
            "labels": [l.get("name", "") for l in (pr.get("labels") or [])],
            "files": [f.get("path", "") for f in (pr.get("files") or [])][:20],
        })
    return prs


# ─────────────────────────────────────────────────────────────────────────────
# Context file generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_github_context(
    repo_path: str,
    project_name: str,
    books_dir: str = "books",
    commit_days: int = 14,
    pr_days: int = 30,
) -> str:
    """
    Generate a GitHub context sidecar file with recent activity.
    Returns the path to the generated file, or empty string on failure.
    """
    commits = get_recent_commits(repo_path, days=commit_days)
    prs = get_recent_prs(repo_path, days=pr_days)

    if not commits and not prs:
        return ""

    lines = [
        f"# GitHub Activity Context: {project_name}",
        f"_Auto-generated {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"Commits: last {commit_days}d | PRs: last {pr_days}d_",
        "",
    ]

    # ── Recent PRs section ──
    if prs:
        lines.append("## Recent Pull Requests")
        lines.append("")
        for pr in prs:
            labels_str = f" `{'` `'.join(pr['labels'])}`" if pr['labels'] else ""
            lines.append(f"### PR #{pr['number']}: {pr['title']}")
            lines.append(f"- **Author:** {pr['author']}")
            lines.append(f"- **State:** {pr['state']}")
            if pr['merged_at']:
                lines.append(f"- **Merged:** {pr['merged_at'][:10]}")
            if pr['labels']:
                lines.append(f"- **Labels:** {labels_str}")
            if pr['body']:
                # First 3 lines of body as summary
                body_lines = [l for l in pr['body'].splitlines() if l.strip()][:3]
                if body_lines:
                    lines.append(f"- **Summary:** {' '.join(body_lines)}")
            if pr['files']:
                lines.append(f"- **Files:** {', '.join(pr['files'][:8])}")
                if len(pr['files']) > 8:
                    lines.append(f"  _...and {len(pr['files']) - 8} more_")
            lines.append("")

    # ── Recent commits section ──
    if commits:
        lines.append("## Recent Commits")
        lines.append("")
        lines.append("| Date | Author | Message |")
        lines.append("|------|--------|---------|")
        for c in commits[:30]:  # cap display
            date = c['date'][:10] if c['date'] else ""
            msg = c['message'][:80]
            lines.append(f"| {date} | {c['author']} | {msg} |")
        lines.append("")

        if len(commits) > 30:
            lines.append(f"_...and {len(commits) - 30} more commits_")
            lines.append("")

    # ── Write sidecar file ──
    content = "\n".join(lines)
    safe_name = re.sub(r'[^\w\-.]', '_', project_name.lower())
    output_path = Path(books_dir) / f"{safe_name}_github_context.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    return str(output_path)


# ─────────────────────────────────────────────────────────────────────────────
# Context loading (used by ask endpoints)
# ─────────────────────────────────────────────────────────────────────────────

def load_github_context(project_name: str, books_dir: str = "books") -> str:
    """
    Load the GitHub context sidecar for a project, if it exists.
    Returns the content string, or empty string if no sidecar exists.
    """
    safe_name = re.sub(r'[^\w\-.]', '_', project_name.lower())
    ctx_path = Path(books_dir) / f"{safe_name}_github_context.md"

    if not ctx_path.exists():
        return ""

    try:
        content = ctx_path.read_text(encoding="utf-8")
        # Check if it's stale (older than 7 days)
        import os
        mtime = os.path.getmtime(ctx_path)
        age_days = (datetime.now().timestamp() - mtime) / 86400
        if age_days > 7:
            content += f"\n\n_Note: This context is {int(age_days)} days old. Run `memoria pull --source github` to refresh._"
        return content
    except Exception:
        return ""


def get_github_context_for_prompt(project_name: str, books_dir: str = "books") -> str:
    """
    Format GitHub context for injection into an AI prompt.
    Returns a ready-to-append string, or empty if no context.
    """
    content = load_github_context(project_name, books_dir)
    if not content:
        return ""

    return (
        "\n\n---\n\n"
        "RECENT DEVELOPMENT ACTIVITY (from git/GitHub):\n\n"
        f"{content}\n\n"
        "Use this activity context to provide more current answers about "
        "recent changes, active work, and development direction."
    )
