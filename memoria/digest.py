"""
Executive Summary Layer — weekly/monthly AI digest for managers and PMs.

Distinct from the Morning Brief (which is a daily developer-focused briefing):
the Executive Digest is a higher-level, longer-horizon summary intended for
non-technical stakeholders. It answers:

  - What changed across all projects in the past week/month?
  - What was shipped or completed?
  - What is blocked or at risk?
  - How has technical health trended? (more debt? new risks?)
  - What should leadership pay attention to?

──────────────────────────────────────────────────────────────────────────────
Output
──────────────────────────────────────────────────────────────────────────────

  terminal  — rich Markdown in the terminal
  markdown  — raw Markdown to stdout
  json      — structured JSON
  slack     — POST to a Slack webhook URL
  email     — send via SMTP (configure in config.yaml)

──────────────────────────────────────────────────────────────────────────────
Config (config.yaml)
──────────────────────────────────────────────────────────────────────────────

  digest:
    schedule: "0 9 * * 1"      # Monday 9 AM
    window:   "7d"             # look back N days/hours
    delivery: terminal         # terminal | markdown | json | slack | email
    slack_webhook: "https://hooks.slack.com/..."
    email:
      smtp_host: smtp.gmail.com
      smtp_port: 587
      from:  you@company.com
      to:    [team@company.com]
      subject: "Weekly Memoria Digest"
"""

from __future__ import annotations

import json
import smtplib
import textwrap
import urllib.request
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional


# ─── Constants ────────────────────────────────────────────────────────────────

MEMORIA_DIR     = Path.home() / ".memoria"
_STATE_PATH     = MEMORIA_DIR / "digest_state.json"
DEFAULT_WINDOW  = "7d"


# ─── State ────────────────────────────────────────────────────────────────────

def _get_last_digest_at() -> Optional[datetime]:
    if _STATE_PATH.exists():
        try:
            data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            ts = data.get("last_digest_at")
            return datetime.fromisoformat(ts) if ts else None
        except Exception:
            return None
    return None


def _set_last_digest_at() -> None:
    MEMORIA_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if _STATE_PATH.exists():
        try:
            existing = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing["last_digest_at"] = datetime.now().isoformat()
    _STATE_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")


# ─── Data collection ──────────────────────────────────────────────────────────

def _parse_window(window: str) -> timedelta:
    """Parse '7d' / '30d' / '24h' into a timedelta."""
    import re
    m = re.match(r"^(\d+)(d|h)$", window.strip().lower())
    if not m:
        raise ValueError(f"Invalid window format: '{window}'. Use e.g. '7d', '30d', '24h'.")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(days=n) if unit == "d" else timedelta(hours=n)


def _collect_book_changes(books_dir: str, since: datetime) -> list[dict]:
    """
    Find Memory Banks that were modified since `since`.
    Returns a list of {name, path, mtime, size_kb} sorted newest first.
    """
    changed = []
    for book in Path(books_dir).glob("*_memory_bank.md"):
        if book.name.endswith("_draft.md"):
            continue
        mtime = datetime.fromtimestamp(book.stat().st_mtime)
        if mtime >= since:
            name = book.stem.replace("_memory_bank", "").replace("_", " ").strip()
            changed.append({
                "name":    name,
                "path":    str(book),
                "mtime":   mtime.isoformat(),
                "size_kb": round(book.stat().st_size / 1024, 1),
            })
    return sorted(changed, key=lambda x: x["mtime"], reverse=True)


def _collect_pending_drafts(books_dir: str) -> list[str]:
    """Return names of projects that have pending draft updates."""
    return [
        p.stem.replace("_draft", "").replace("_", " ").strip()
        for p in Path(books_dir).glob("*_draft.md")
    ]


def _collect_drift_reports(books_dir: str) -> list[dict]:
    """Return any drift report files present in the books directory."""
    reports = []
    for p in Path(books_dir).glob("*_drift.md"):
        name = p.stem.replace("_drift", "").replace("_", " ").strip()
        reports.append({"name": name, "path": str(p)})
    return reports


def _collect_book_excerpts(books_dir: str, changed: list[dict], max_chars: int = 1200) -> str:
    """Build a concise excerpt block from the changed books."""
    lines = []
    for entry in changed[:8]:   # cap at 8 for token budget
        try:
            text = Path(entry["path"]).read_text(encoding="utf-8")
            # Extract TL;DR section
            import re
            m = re.search(
                r"##\s+TL;DR\s*\n(.*?)(?=##|\Z)",
                text, re.DOTALL | re.IGNORECASE
            )
            excerpt = m.group(1).strip()[:600] if m else text[:400]
        except Exception:
            excerpt = "(could not read)"
        lines.append(f"### {entry['name']}  _(updated {entry['mtime'][:10]}, {entry['size_kb']} KB)_\n{excerpt}")
    return "\n\n---\n\n".join(lines)


# ─── LLM synthesis ────────────────────────────────────────────────────────────

_DIGEST_SYSTEM = """\
You are writing an executive briefing for a software organisation's leadership
team. You have access to a summary of recent Memory Bank changes, pending
reviews, plan drift reports, and activity tracker data.

Write clearly for a non-technical audience. Be specific about project names and
dates. Be honest about risks. Avoid jargon. Do not invent facts — if something
is unclear, say "unclear from available data."
"""

_DIGEST_PROMPT = """\
Today: {today}
Period: {window_label}

## Memory Banks updated this period ({n_changed} projects)
{changed_list}

## Project excerpts (what changed)
{excerpts}

## Pending review queue ({n_drafts} draft(s) awaiting approval)
{draft_names}

## Plan drift alerts ({n_drift} report(s))
{drift_names}

Write the Executive Digest using EXACTLY this structure:

# Executive Digest — {today}
> Covering: {window_label} | {n_changed} project(s) updated

---

## What Happened This {period_label}
[3-5 bullet points. What was built, changed, or shipped?
 Be specific: "The auth-service Memory Bank was updated, showing a new OAuth2 flow was added."]

## Status by Project
[For each updated project: one sentence on its current state.
 Format: **ProjectName** — [what's happening / what changed]]

## What Needs Attention
[Pending reviews, plan drift, stale projects that haven't changed in >30 days.
 Any project showing signs of scope creep or rising complexity?]

## Risks This {period_label}
[Severity-tagged. Only list real risks visible in the data.
 If no new risks: "No new risks surfaced this period."]

## Recommended Actions
[1-3 concrete actions leadership or the team should take this week.
 Format: → [Action] — [why it matters]]

---
"""


def build_digest(
    books_dir:   str,
    window:      str  = DEFAULT_WINDOW,
    config_path: Optional[str] = None,
    on_progress=None,
) -> dict:
    """
    Collect data and synthesise the executive digest.

    Returns a dict:
      {
        "markdown":   str,   # full Markdown text
        "generated_at": str,
        "window":     str,
        "changed_projects": list[dict],
        "pending_drafts": list[str],
        "drift_reports":  list[dict],
      }
    """
    from .models import ModelProvider
    import litellm

    def progress(msg: str):
        if on_progress:
            on_progress(msg)

    # ── Resolve paths ─────────────────────────────────────────────────────────
    if not config_path:
        cfg = MEMORIA_DIR / "config.yaml"
        config_path = str(cfg) if cfg.exists() else ""

    # ── Collect data ──────────────────────────────────────────────────────────
    delta = _parse_window(window)
    since = datetime.now() - delta

    progress("Collecting Memory Bank changes…")
    changed   = _collect_book_changes(books_dir, since)
    drafts    = _collect_pending_drafts(books_dir)
    drift     = _collect_drift_reports(books_dir)
    excerpts  = _collect_book_excerpts(books_dir, changed)

    today  = datetime.now().strftime("%Y-%m-%d")
    n_days = int(delta.total_seconds() // 86400)
    period_label  = f"{n_days} days" if n_days > 1 else "24 hours"
    window_label  = f"Last {period_label}"
    changed_list  = "\n".join(f"- {c['name']} ({c['mtime'][:10]})" for c in changed) or "- No updates this period."
    draft_names   = "\n".join(f"- {d}" for d in drafts) or "- None"
    drift_names   = "\n".join(f"- {d['name']}" for d in drift) or "- None"

    prompt = _DIGEST_PROMPT.format(
        today        = today,
        window_label = window_label,
        period_label = period_label,
        n_changed    = len(changed),
        changed_list = changed_list,
        excerpts     = excerpts or "(No project excerpts available)",
        n_drafts     = len(drafts),
        draft_names  = draft_names,
        n_drift      = len(drift),
        drift_names  = drift_names,
    )

    progress(f"Synthesising digest ({len(changed)} project(s)) via LLM…")
    model = ModelProvider(config_path)

    _RETRIES = 3
    content  = None
    last_err = None
    for attempt in range(_RETRIES):
        try:
            resp = litellm.completion(
                model      = model.model,
                messages   = [
                    {"role": "system", "content": _DIGEST_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                max_tokens = 3000,
                timeout    = 120,
            )
            content = resp.choices[0].message.content.strip()
            break
        except Exception as exc:
            last_err = exc
            import time
            err_lower = str(exc).lower()
            if "rate" in err_lower or "429" in err_lower:
                wait = 15 * (attempt + 1)
                progress(f"Rate-limited, waiting {wait}s…")
                time.sleep(wait)
            elif attempt < _RETRIES - 1:
                time.sleep(5)

    if content is None:
        raise RuntimeError(f"LLM synthesis failed after {_RETRIES} attempts: {last_err}")

    # Ensure header
    if not content.lstrip().startswith("# Executive Digest"):
        content = f"# Executive Digest — {today}\n> {window_label}\n\n---\n\n" + content

    _set_last_digest_at()

    return {
        "markdown":          content,
        "generated_at":      datetime.now().isoformat(),
        "window":            window,
        "changed_projects":  changed,
        "pending_drafts":    drafts,
        "drift_reports":     drift,
    }


# ─── Delivery ─────────────────────────────────────────────────────────────────

def deliver_slack(markdown: str, webhook_url: str) -> None:
    """POST digest to a Slack incoming webhook (plain text blocks)."""
    # Slack can render Markdown-ish, but we convert headers to bold for clarity
    import re
    slack_text = re.sub(r"^# (.+)$", r"*\1*", markdown, flags=re.MULTILINE)
    slack_text = re.sub(r"^## (.+)$", r"\n*\1*", slack_text, flags=re.MULTILINE)
    slack_text = re.sub(r"^### (.+)$", r"*\1*", slack_text, flags=re.MULTILINE)

    payload = json.dumps({"text": slack_text[:3900]}).encode()  # Slack 4K limit
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"Slack webhook returned {resp.status}")


def deliver_email(
    markdown: str,
    smtp_host: str,
    smtp_port: int,
    from_addr: str,
    to_addrs:  list[str],
    subject:   str,
    username:  Optional[str] = None,
    password:  Optional[str] = None,
) -> None:
    """Send the digest as an HTML email via SMTP."""
    try:
        import markdown as md_lib  # type: ignore
        html_body = md_lib.markdown(markdown)
    except ImportError:
        # Fallback: plain text email
        html_body = f"<pre>{markdown}</pre>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(to_addrs)

    msg.attach(MIMEText(markdown, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        if username and password:
            server.login(username, password)
        server.sendmail(from_addr, to_addrs, msg.as_string())
