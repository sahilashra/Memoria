"""
Morning Intelligence Brief — Jarvis-style proactive briefing.

Collects data from every live source (draft queue, graph, Memory Banks, MCP),
applies RBAC filtering, synthesises with LLM into a plain-English morning
summary, and optionally delivers it via terminal, desktop notification, or Slack.

Priority order
──────────────
  1  URGENT     — pending review drafts waiting for a human decision
  2  IMPORTANT  — graph alerts (a project you depend on just changed)
  3  INFO       — Memory Banks updated since last brief
  4  INFO       — MCP sources that are stale / overdue for their schedule

Usage
─────
  from memoria.brief import run_brief, get_last_brief_at

  # Generate and print to terminal
  result = run_brief(config_path="~/.memoria/config.yaml", books_dir="~/.memoria/books")

  # Full call with options
  result = run_brief(
      config_path="~/.memoria/config.yaml",
      books_dir="~/.memoria/books",
      days_back=1,
      delivery="terminal",   # terminal | notify | slack | silent
      max_items=10,
  )

Config shape (config.yaml)
──────────────────────────
  brief:
    schedule: "0 8 * * 1-5"   # weekdays at 8 AM
    delivery: terminal          # terminal | notify | slack
    days_back: 1                # look back N days
    max_items: 10               # cap displayed items
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─── Brief state ──────────────────────────────────────────────────────────────

_STATE_PATH = Path.home() / ".memoria" / "brief_state.json"


def get_last_brief_at() -> Optional[datetime]:
    """Return the datetime of the last successful brief run, or None."""
    if _STATE_PATH.exists():
        try:
            data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            ts = data.get("last_brief_at")
            if ts:
                return datetime.fromisoformat(ts)
        except Exception:
            pass
    return None


def set_last_brief_at(dt: Optional[datetime] = None) -> None:
    """Record a successful brief run."""
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if _STATE_PATH.exists():
        try:
            existing = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing["last_brief_at"] = (dt or datetime.now()).isoformat()
    _STATE_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def get_last_brief_content() -> Optional[str]:
    """Return the markdown text from the last brief, or None."""
    if _STATE_PATH.exists():
        try:
            data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            return data.get("last_content")
        except Exception:
            pass
    return None


def _save_brief_content(content: str) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if _STATE_PATH.exists():
        try:
            existing = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing["last_content"] = content
    existing["last_brief_at"] = datetime.now().isoformat()
    _STATE_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


# ─── Monday mode ──────────────────────────────────────────────────────────────

def _effective_days_back(days_back: int) -> int:
    """
    On Mondays, extend days_back to cover the weekend (min 3).
    This ensures Friday/Saturday/Sunday changes surface in the brief.
    """
    if datetime.now().weekday() == 0:  # 0 = Monday
        return max(days_back, 3)
    return days_back


# ─── Item collection ──────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _collect_drafts(books_dir: str) -> List[dict]:
    """Priority 1 — pending review drafts."""
    items = []
    books_path = Path(books_dir)
    if not books_path.exists():
        return items

    for draft in sorted(books_path.glob("*_draft.md")):
        project = draft.stem.replace("_draft", "")
        age_h = (datetime.now() - datetime.fromtimestamp(draft.stat().st_mtime)).total_seconds() / 3600
        age_str = f"{int(age_h)}h ago" if age_h < 48 else f"{int(age_h / 24)}d ago"
        try:
            n_lines = len(draft.read_text(encoding="utf-8").splitlines())
        except Exception:
            n_lines = 0
        items.append({
            "priority": 1,
            "category": "draft_pending",
            "project": project,
            "summary": f"Draft waiting approval — {n_lines} lines, created {age_str}",
            "action": f"memoria review --project {project}",
            "detail": str(draft),
        })
    return items


def _collect_updated_books(books_dir: str, since: datetime) -> List[dict]:
    """Priority 3 — Memory Banks that changed after `since`."""
    items = []
    books_path = Path(books_dir)
    if not books_path.exists():
        return items

    for book in sorted(books_path.glob("*_memory_bank.md")):
        mtime = datetime.fromtimestamp(book.stat().st_mtime)
        if mtime < since:
            continue
        project = book.stem.replace("_memory_bank", "")
        age_h = (datetime.now() - mtime).total_seconds() / 3600
        age_str = f"{int(age_h)}h ago" if age_h < 48 else f"{int(age_h / 24)}d ago"
        size_kb = round(book.stat().st_size / 1024, 1)
        items.append({
            "priority": 3,
            "category": "book_updated",
            "project": project,
            "summary": f"Memory Bank updated {age_str} ({size_kb} KB)",
            "action": f"memoria ask --project {project}",
            "detail": str(book),
        })
    return items


def _collect_graph_alerts(books_dir: str, since: datetime) -> List[dict]:
    """
    Priority 2 — graph impact alerts.
    Any project that changed since `since` → find who depends on it → alert.
    """
    items = []
    try:
        from .graph import get_graph, impact_analysis
        graph = get_graph(books_dir)
        if not graph.get("nodes"):
            return items

        books_path = Path(books_dir)
        changed_projects = set()
        for book in books_path.glob("*_memory_bank.md"):
            if datetime.fromtimestamp(book.stat().st_mtime) >= since:
                changed_projects.add(book.stem.replace("_memory_bank", ""))

        for changed in changed_projects:
            dependents = impact_analysis(changed, graph)
            if not dependents:
                continue
            dep_names = [d["project"] for d in dependents[:3]]
            dep_str = ", ".join(dep_names)
            if len(dependents) > 3:
                dep_str += f" +{len(dependents) - 3} more"
            items.append({
                "priority": 2,
                "category": "graph_alert",
                "project": changed,
                "summary": f"{changed} updated — affects: {dep_str}",
                "action": f"memoria graph impact {changed}",
                "detail": json.dumps([d["project"] for d in dependents]),
            })
    except Exception:
        pass
    return items


def _collect_mcp_stale(config_path: str, since: datetime) -> List[dict]:
    """Priority 3 — MCP sources overdue for their scheduled pull."""
    items = []
    try:
        from .mcp_sources import load_sources
        from .scheduler import is_due
        from .pull_state import get_last_pull

        sources = load_sources(config_path)
        for src in sources:
            if not src.get("schedule"):
                continue
            last = get_last_pull(src["name"])
            if last is None:
                items.append({
                    "priority": 3,
                    "category": "mcp_stale",
                    "project": src["name"],
                    "summary": f"MCP source '{src['name']}' has never been pulled",
                    "action": f"memoria pull --source {src['name']}",
                    "detail": "",
                })
            elif is_due(src):
                age_h = (datetime.now() - last).total_seconds() / 3600
                age_str = f"{int(age_h)}h ago" if age_h < 48 else f"{int(age_h / 24)}d ago"
                items.append({
                    "priority": 3,
                    "category": "mcp_stale",
                    "project": src["name"],
                    "summary": f"MCP source '{src['name']}' overdue (last pulled {age_str})",
                    "action": f"memoria pull --source {src['name']}",
                    "detail": "",
                })
    except Exception:
        pass
    return items


def _apply_rbac_filter(items: List[dict], config_path: str) -> List[dict]:
    """
    Filter items based on the current user's RBAC role.
      viewer      — only book_updated and mcp info items (read-only)
      contributor — adds mcp_stale prompts (can pull)
      reviewer+   — adds draft_pending items (can approve)
      admin       — all items
    """
    try:
        from .rbac import get_role, Role
        role = get_role(config_path)

        if role == Role.VIEWER:
            return [i for i in items if i["category"] in ("book_updated", "graph_alert")]
        if role == Role.CONTRIBUTOR:
            return [i for i in items if i["category"] != "draft_pending"]
        # reviewer and admin see everything
        return items
    except Exception:
        return items  # no RBAC → show all


# ─── LLM synthesis ────────────────────────────────────────────────────────────

_BRIEF_SYSTEM_PROMPT = """\
You are Memoria's morning briefing assistant — think Jarvis, not a status bot.

Your job: synthesise a ranked list of data points into a concise, actionable
morning briefing for a software professional. Write as if speaking directly to
the person: confident, calm, and clear. No fluff.

Rules:
1. Lead with what needs a decision (draft approvals) — most urgent first.
2. Graph alerts come next — flag when a dependency changed.
3. Informational updates (new Memory Banks, stale sources) come last.
4. Every item MUST end with the exact CLI command to act on it.
5. Monday briefs acknowledge the weekend gap in one sentence.
6. If there is nothing notable, say so in one sentence.
7. Use markdown: **bold** project names, `code` for CLI commands.
8. Keep the total brief under 300 words — scannable, not exhaustive.
9. Synthesise, don't list: connect the dots where relevant
   ("payments depends on auth — auth changed — you should check X").
"""


def synthesize_brief(
    items: List[dict],
    config_path: str,
    max_items: int = 10,
    days_back: int = 1,
) -> str:
    """
    Ask the LLM to turn a list of raw brief items into a plain-English morning
    briefing. Returns markdown text.

    Falls back to a simple formatted list if the LLM call fails.
    """
    truncated = items[:max_items]

    if not truncated:
        day = "Monday" if datetime.now().weekday() == 0 else datetime.now().strftime("%A")
        hour = datetime.now().hour
        if hour < 12:
            greeting = "Good morning."
        elif hour < 17:
            greeting = "Good afternoon."
        else:
            greeting = "Good evening."
        return f"**{greeting}** Nothing notable since {days_back}d ago — all quiet.\n\nUse the Search tab to explore your Memory Banks, or analyze a new project from the Banks tab."

    # Build a compact JSON summary for the LLM
    data_str = json.dumps(
        [{"priority": i["priority"], "category": i["category"],
          "project": i["project"], "summary": i["summary"], "action": i["action"]}
         for i in truncated],
        indent=2,
    )

    monday_note = ""
    if datetime.now().weekday() == 0:
        monday_note = "\n(Today is Monday — cover the weekend in one sentence at the top.)"

    user_msg = (
        f"Generate a morning brief from these {len(truncated)} items "
        f"(looking back {days_back} day{'s' if days_back != 1 else ''}):{monday_note}\n\n"
        f"```json\n{data_str}\n```"
    )

    try:
        from .models import ModelProvider
        import litellm
        provider = ModelProvider(config_path)
        response = litellm.completion(
            model=provider.model,
            messages=[
                {"role": "system", "content": _BRIEF_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=600,
            timeout=30,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        # Graceful fallback — plain list
        lines = [f"**Morning Brief** — {datetime.now().strftime('%A, %b %d')}  \n"]
        priority_labels = {1: "🔴 Urgent", 2: "🟡 Important", 3: "ℹ️ Info"}
        for item in truncated:
            label = priority_labels.get(item["priority"], "ℹ️")
            lines.append(f"**{label}** — **{item['project']}**: {item['summary']}")
            lines.append(f"  → `{item['action']}`")
            lines.append("")
        lines.append(f"\n*LLM synthesis unavailable: {exc}*")
        return "\n".join(lines)


# ─── Delivery ─────────────────────────────────────────────────────────────────

def _deliver_terminal(brief_text: str, items: List[dict]) -> None:
    """Print the brief as a rich-formatted panel to the terminal."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown

    console = Console()
    day = datetime.now().strftime("%A, %B %d")
    n_urgent = sum(1 for i in items if i["priority"] == 1)
    n_important = sum(1 for i in items if i["priority"] == 2)

    subtitle = ""
    if n_urgent:
        subtitle += f"[red]{n_urgent} urgent[/red]  "
    if n_important:
        subtitle += f"[yellow]{n_important} important[/yellow]  "
    subtitle += f"[dim]{len(items)} items total[/dim]"

    console.print()
    console.print(Panel(
        Markdown(brief_text),
        title=f"[bold cyan]☀  Morning Brief — {day}[/bold cyan]",
        subtitle=subtitle.strip(),
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()


def _deliver_notify(brief_text: str, items: List[dict]) -> None:
    """Send a desktop notification with a 2-line summary."""
    try:
        from .notifier import send_notification
        n_urgent = sum(1 for i in items if i["priority"] == 1)
        first_item = items[0]["summary"] if items else "All quiet."
        title = f"Memoria Brief — {datetime.now().strftime('%a %b %d')}"
        body = (f"{n_urgent} item(s) need attention. " if n_urgent else "") + first_item
        # Truncate to 120 chars for notification systems
        body = (body[:117] + "...") if len(body) > 120 else body
        send_notification(title=title, body=body)
    except Exception:
        pass  # notification failure is non-fatal


def _deliver_slack(brief_text: str, config_path: str) -> None:
    """Post the brief to a Slack channel via MCP."""
    try:
        from .mcp_sources import load_sources, pull_source
        import yaml

        cfg = yaml.safe_load(Path(config_path).expanduser().read_text(encoding="utf-8")) or {}
        slack_cfg = cfg.get("brief", {}).get("slack", {})
        channel = slack_cfg.get("channel", "#general")
        source_name = slack_cfg.get("source", "slack")

        sources = load_sources(config_path)
        slack_source = next((s for s in sources if s["name"] == source_name), None)
        if not slack_source:
            return

        # Post via the Slack MCP post_message tool
        slack_source_with_post = dict(slack_source)
        slack_source_with_post["pull"] = [{
            "tool": "slack_post_message",
            "args": {"channel": channel, "text": brief_text},
        }]
        pull_source(slack_source_with_post, config_path)
    except Exception:
        pass


# ─── Top-level entry point ────────────────────────────────────────────────────

def collect_items(
    books_dir: str,
    config_path: str,
    days_back: int = 1,
) -> List[dict]:
    """
    Gather all brief items from every source.
    Returns a list sorted by priority (1 first).
    """
    days_back = _effective_days_back(days_back)
    since = datetime.now() - timedelta(days=days_back)

    items: List[dict] = []
    items.extend(_collect_drafts(books_dir))
    items.extend(_collect_graph_alerts(books_dir, since))
    items.extend(_collect_updated_books(books_dir, since))
    items.extend(_collect_mcp_stale(config_path, since))

    items = _apply_rbac_filter(items, config_path)
    items.sort(key=lambda x: (x["priority"], x["project"]))
    return items


def run_brief(
    config_path: str,
    books_dir: str,
    days_back: int = 1,
    delivery: str = "terminal",
    max_items: int = 10,
) -> dict:
    """
    Generate and deliver the morning brief.

    Returns a dict with:
      {
        "items": [...],           # raw brief items (before truncation)
        "brief_text": "...",      # synthesised markdown
        "days_back": int,         # effective lookback (3 on Mondays)
        "generated_at": "...",    # ISO timestamp
      }

    Delivery modes:
      "terminal" — rich-formatted output to stdout
      "notify"   — desktop OS notification (2-line summary)
      "slack"    — post to Slack channel via MCP
      "silent"   — generate and return, no output (for programmatic use)
    """
    effective_days = _effective_days_back(days_back)
    items = collect_items(books_dir, config_path, days_back)
    brief_text = synthesize_brief(items, config_path, max_items=max_items, days_back=effective_days)

    if delivery == "terminal":
        _deliver_terminal(brief_text, items)
    elif delivery == "notify":
        _deliver_terminal(brief_text, items)  # also print to terminal
        _deliver_notify(brief_text, items)
    elif delivery == "slack":
        _deliver_slack(brief_text, config_path)
        _deliver_terminal(brief_text, items)  # always print locally too
    # "silent" → do nothing

    _save_brief_content(brief_text)

    return {
        "items": items,
        "brief_text": brief_text,
        "days_back": effective_days,
        "generated_at": datetime.now().isoformat(),
    }
