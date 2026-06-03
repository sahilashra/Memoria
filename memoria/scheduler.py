"""
Scheduled MCP pulls — runs sources on a defined interval.

Schedule formats
────────────────
  hourly          every 60 minutes
  daily           every 24 hours
  weekly          every 7 days
  30m             every 30 minutes
  2h              every 2 hours
  6h / 12h        every 6 or 12 hours
  1d / 7d         every 1 or 7 days

Raw cron expressions (e.g. "0 9 * * 1-5") are not yet supported — use simple
intervals for now; cron support will be added in a future release.

Usage (via CLI)
────────────────
  memoria schedule list             show all scheduled sources + last pull time
  memoria schedule run              run all due sources right now (one-shot)
  memoria schedule start            blocking daemon (runs until Ctrl+C)
  memoria schedule reset <source>   clear pull history so next run is a full pull

Config shape (in config.yaml)
──────────────────────────────
  mcp_sources:
    - name: "confluence"
      server: "npx @atlassian/mcp-confluence"
      schedule: "daily"          # ← runs once every 24 hours
      incremental: true          # ← only fetch content newer than last pull
      pull:
        - tool: "confluence_search"
          args: {query: "engineering", limit: 50}
"""

import re
import time
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from .pull_state import get_last_pull, set_last_pull
from .mcp_sources import load_sources, pull_source

# Regex that recognises the shape of a cron expression (5 or 6 fields)
_CRON_RE = re.compile(r"^[\d\*/,\-]+ [\d\*/,\-]+ [\d\*/,\-]+ [\d\*/,\-]+ [\d\*/,\-]+( [\d\*/,\-]+)?$")


# ─── Schedule parsing ─────────────────────────────────────────────────────────

_NAMED_INTERVALS = {
    "hourly": timedelta(hours=1),
    "daily":  timedelta(days=1),
    "weekly": timedelta(weeks=1),
}

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


def parse_interval(schedule: str) -> Optional[timedelta]:
    """
    Parse a schedule string into a timedelta.
    Returns None for unrecognised formats (e.g. raw cron — not yet supported).

    >>> parse_interval("daily")
    datetime.timedelta(days=1)
    >>> parse_interval("2h")
    datetime.timedelta(seconds=7200)
    >>> parse_interval("30m")
    datetime.timedelta(seconds=1800)
    >>> parse_interval("0 9 * * 1")  # cron — not supported yet
    None
    """
    if not schedule:
        return None
    s = str(schedule).strip().lower()
    if s in _NAMED_INTERVALS:
        return _NAMED_INTERVALS[s]
    m = re.fullmatch(r"(\d+)(m|h|d)", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return timedelta(seconds=n * _UNIT_SECONDS[unit])
    return None


def describe_interval(schedule: str) -> str:
    """
    Return a human-readable description of a schedule string.
    Used in CLI output.
    """
    iv = parse_interval(schedule)
    if iv is not None:
        total = int(iv.total_seconds())
        if total < 3600:
            return f"every {total // 60}m"
        if total < 86400:
            h = total // 3600
            return f"every {h}h"
        d = total // 86400
        return f"every {d}d"
    if _CRON_RE.match(schedule.strip()):
        return f"cron: {schedule}"
    return f"{schedule} (unrecognised)"


# ─── Cron expression support ─────────────────────────────────────────────────

def _is_cron_due(cron_expr: str, last: Optional[datetime]) -> bool:
    """
    Check if a cron expression has fired at least once since `last`.

    Requires croniter: pip install croniter  (or pip install memoria[schedule])

    Raises ImportError if croniter is not installed — caller handles gracefully.
    """
    try:
        from croniter import croniter, CroniterBadCronError  # type: ignore
    except ImportError:
        raise ImportError(
            "croniter is not installed — cron expression schedules require it.\n"
            "Run:  pip install croniter   (or: pip install memoria[schedule])"
        )

    if last is None:
        return True  # never run — always due

    now = datetime.now()
    try:
        # Get the first scheduled fire time after `last`.
        # If that time is <= now, the job is due.
        cron = croniter(cron_expr, last)
        next_fire = cron.get_next(datetime)
        return next_fire <= now
    except CroniterBadCronError:
        return False  # malformed expression — skip silently


# ─── Due-check ────────────────────────────────────────────────────────────────

def is_due(source: dict) -> bool:
    """
    Return True if this source has a schedule and enough time has passed
    since the last successful pull.

    Supports both simple interval strings ("daily", "2h") and cron expressions
    ("0 9 * * 1-5"). Cron expressions require  pip install croniter.

    Always returns True when the source has never been pulled.
    Returns False when:
      - No schedule is set on the source
      - The schedule format is unrecognised
      - croniter is not installed and a cron expression was provided
      - The source ran recently and the interval hasn't elapsed
    """
    schedule = source.get("schedule")
    if not schedule:
        return False

    s = str(schedule).strip()
    interval = parse_interval(s)

    if interval is not None:
        # Simple interval string
        last = get_last_pull(source["name"])
        if last is None:
            return True
        return datetime.now() - last >= interval

    # Try as a cron expression
    try:
        return _is_cron_due(s, get_last_pull(source["name"]))
    except ImportError:
        # croniter not installed — don't crash, just skip this source
        return False
    except Exception:
        return False


def due_sources(config_path: str) -> List[dict]:
    """Return all configured sources that are currently due to run."""
    return [s for s in load_sources(config_path) if is_due(s)]


def scheduled_sources(config_path: str) -> List[dict]:
    """Return all sources that have a schedule field (due or not)."""
    return [s for s in load_sources(config_path) if s.get("schedule")]


# ─── One-shot run ─────────────────────────────────────────────────────────────

def run_due(
    config_path: str,
    books_dir: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """
    Pull all sources that are currently due, generate Memory Banks, record timestamps.
    Returns a list of output file paths (one per successfully-generated book).

    Safe: errors on individual sources are caught and reported via on_progress,
    they never propagate — one broken source never stops the others.
    """
    from .generator import BookGenerator

    sources = due_sources(config_path)
    if not sources:
        if on_progress:
            on_progress("No sources are due to run right now.")
        return []

    gen = BookGenerator(config_path)
    results: List[str] = []

    for source in sources:
        name = source.get("name", "unknown")
        if on_progress:
            on_progress(f"Scheduled pull: {name}")
        try:
            # Pass last pull timestamp so incremental injection can use it
            last = get_last_pull(name) if source.get("incremental") else None
            content = pull_source(source, since=last)

            if not content or content.startswith("[No content"):
                if on_progress:
                    on_progress(f"  [skip] {name}: source returned no content")
                continue

            path = gen.generate_from_text(
                source_name=name,
                company_context=source.get("context", f"Content pulled from {name}"),
                content=content,
                output_dir=books_dir,
                on_progress=on_progress,
            )
            set_last_pull(name, book_path=path)
            results.append(path)

            if on_progress:
                on_progress(f"  Done: {name} → {path}")

        except Exception as e:
            if on_progress:
                on_progress(f"  [error] {name}: {e}")

    return results


# ─── Blocking daemon ──────────────────────────────────────────────────────────

def start_daemon(
    config_path: str,
    books_dir: str,
    on_progress: Optional[Callable[[str], None]] = None,
    poll_seconds: int = 60,
) -> None:
    """
    Blocking scheduler loop.
    Every poll_seconds, checks whether any source is due and pulls it.
    Runs until the user presses Ctrl+C.

    Typical usage:
      memoria schedule start          # checks every 60s
      memoria schedule start --poll 300   # checks every 5 minutes
    """
    sources = scheduled_sources(config_path)
    if not sources:
        if on_progress:
            on_progress("No scheduled sources found in config. Add a 'schedule:' field to a source.")
        return

    if on_progress:
        names = ", ".join(s["name"] for s in sources)
        on_progress(
            f"Scheduler active — monitoring {len(sources)} source(s): {names}\n"
            f"Checking every {poll_seconds}s. Press Ctrl+C to stop."
        )

    try:
        while True:
            run_due(config_path, books_dir, on_progress)
            _run_brief_if_due(config_path, books_dir, on_progress)
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        if on_progress:
            on_progress("Scheduler stopped.")


# ─── Brief schedule helpers ───────────────────────────────────────────────────

def _load_brief_config(config_path: str) -> Optional[dict]:
    """Load the brief: block from config.yaml, or None if absent."""
    try:
        import yaml
        from pathlib import Path as _Path
        cfg = yaml.safe_load(_Path(config_path).expanduser().read_text(encoding="utf-8")) or {}
        brief = cfg.get("brief")
        return brief if isinstance(brief, dict) and brief.get("schedule") else None
    except Exception:
        return None


def is_brief_due(config_path: str) -> bool:
    """
    Return True if the brief schedule has fired since the last brief was run.
    Reads the brief: schedule: field from config.yaml.
    Returns False if no schedule is set or if the brief ran recently enough.
    """
    brief_cfg = _load_brief_config(config_path)
    if not brief_cfg:
        return False

    from .brief import get_last_brief_at
    last = get_last_brief_at()
    schedule = str(brief_cfg["schedule"]).strip()

    interval = parse_interval(schedule)
    if interval is not None:
        if last is None:
            return True
        return datetime.now() - last >= interval

    # Cron expression
    try:
        return _is_cron_due(schedule, last)
    except ImportError:
        return False
    except Exception:
        return False


def _run_brief_if_due(
    config_path: str,
    books_dir: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Called by the daemon on every tick — runs a brief if the schedule says it's due."""
    try:
        if not is_brief_due(config_path):
            return

        brief_cfg = _load_brief_config(config_path) or {}
        delivery  = brief_cfg.get("delivery", "terminal")
        days_back = int(brief_cfg.get("days_back", 1))
        max_items = int(brief_cfg.get("max_items", 10))

        if on_progress:
            on_progress("Running scheduled morning brief…")

        from .brief import run_brief
        run_brief(
            config_path=config_path,
            books_dir=books_dir,
            days_back=days_back,
            delivery=delivery,
            max_items=max_items,
        )

        if on_progress:
            on_progress("Brief generated.")
    except Exception as exc:
        if on_progress:
            on_progress(f"[brief error] {exc}")
