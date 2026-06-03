# Pillar 2 — Activity Segmentation & Cleaning

> **The "Data Processor"** — raw interaction data is noisy. This pillar
> structures it into clean, meaningful task sessions.
> Coverage today: **25%** | Status: 🔴 Mostly missing

---

## Goal

Transform the raw event stream from Pillar 1 into structured, clean task
sessions — grouping related actions, removing dead time, and permanently
scrubbing sensitive data before anything is stored or analyzed.

---

## What Already Exists

### `memoria/tracker.py`
- Records a `session_start` event when the daemon starts — a basic session
  boundary marker, not a true idle-based sessionizer
- 5-second debounce on file changes (reduces noise at event level)
- Extension-based filtering — only tracks code/config files, not all FS events

### `memoria/companion.py`
- 3-second debounce on context changes before triggering a search
- Keyword extraction (`_extract_keywords`) strips stop-words and short tokens

---

## What Is Missing

| Gap | Description | Effort |
|-----|-------------|--------|
| Idle-time sessionization | Group events into discrete task sessions based on inactivity threshold (e.g. 5 min with no input = end of session) | Small |
| PII / data masking | Regex + lightweight local NER to scrub passwords, credit card numbers, email addresses, SSNs from captured text **before** storage | Medium |
| Noise reduction | Filter accidental single clicks, erratic mouse micro-movements, rapid context switches < 2s (checking a notification mid-task) | Medium |
| Session labeling | After sessionization, attach a human-readable label to each session ("Editing auth service login flow", "Filling Salesforce opportunity form") | Medium (needs Pillar 3) |
| Privacy audit log | Record what was masked and why, so users can review what the system chose to scrub | Small |

---

## Architecture Decisions (Resolved ✅)

> **D2 →** Local forever. `sync_allowed: false` hard-coded in v1. No cloud path exists. Raw events, PII-masked text, and session data never leave `~/.memoria/`.

## Architecture Decision

**Composite boundary signals over simple idle timeout. Developer-specific PII regex required before any `INSERT`. `workflow_hash` field enables cross-session pattern detection in Pillar 3.**

Session boundaries for developers are better detected by intent shifts, not time gaps alone.

**Boundary signals, in priority order:**
1. **Repo switch** — any event where `repo` field changes. Hard boundary.
2. **Git commit** — treat a commit as a natural session close; new session for subsequent events.
3. **Ticket reference change** — if `JIRA-123` or `#402` in window titles/commits changes, context has switched.
4. **Cold return** — same repo event after >25-minute gap. New session even if no other change.
5. **Sustained non-dev switch** — IDE → Slack/email for >8 minutes with no return.

## Proposed Implementation

### 1. Composite Boundary Algorithm

```python
import re
from datetime import datetime

BOUNDARY_SIGNALS = {
    "repo_change":   lambda prev, cur: prev.get("repo") != cur.get("repo"),
    "commit_close":  lambda prev, cur: prev.get("event_type") == "git_commit",
    "cold_return":   lambda prev, cur: (
        datetime.fromisoformat(cur["ts"]) - datetime.fromisoformat(prev["ts"])
    ).total_seconds() > 1500 and cur.get("repo") == prev.get("repo"),
    "ticket_change": lambda prev, cur: (
        _extract_ticket(prev) != _extract_ticket(cur)
        and _extract_ticket(cur) is not None
    ),
}

def _extract_ticket(event: dict) -> str | None:
    text = event.get("context", "") or event.get("detail", "") or ""
    m = re.search(r'([A-Z]+-\d+|#\d+)', text)
    return m.group(1) if m else None

def should_split_session(prev: dict, cur: dict) -> bool:
    return any(fn(prev, cur) for fn in BOUNDARY_SIGNALS.values())
```

Ambiguous case: `IDE → browser (stackoverflow.com) → IDE` within 12 min stays in the same session — browser URL is annotated as `lookup_url`, not a boundary trigger.

### 2. Developer-Specific PII Patterns

```python
import re

PII_PATTERNS = [
    (r'\bAKIA[0-9A-Z]{16}\b',                                        '[REDACTED_AWS_KEY_ID]'),
    (r'\bghp_[A-Za-z0-9]{36,}\b',                                    '[REDACTED_GH_PAT]'),
    (r'\bgithub_pat_[A-Za-z0-9_]{82,}\b',                            '[REDACTED_GH_PAT]'),
    (r'(?i)bearer\s+[A-Za-z0-9\-_\.]{20,}',                          'Bearer [REDACTED_TOKEN]'),
    (r'(?i)(postgres|mysql|mongodb|redis)://[^\s\'"]+:[^\s\'"@]+@',   '[REDACTED_DB_CONNSTR]://'),
    (r'\beyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\b',   '[REDACTED_JWT]'),
    (r'(?i)(SECRET|PASSWORD|TOKEN|API_KEY)\s*=\s*["\']?([^\s"\']{8,})["\']?',
     r'\1=[REDACTED_SECRET]'),
    (r'-----BEGIN [^\-]+PRIVATE KEY-----[\s\S]+?-----END [^\-]+PRIVATE KEY-----',
     '[REDACTED_PRIVATE_KEY]'),
]

def scrub_pii(text: str) -> tuple[str, int]:
    """Returns (scrubbed_text, hit_count). Applied before any INSERT."""
    count = 0
    for pattern, replacement in PII_PATTERNS:
        new_text, n = re.subn(pattern, replacement, text)
        text = new_text
        count += n
    return text, count
```

Clipboard events with ≥3 pattern hits are **dropped entirely**, not stored scrubbed.

### 3. Noise vs. Signal Classification

| Raw Event | Action | Reason |
|-----------|--------|--------|
| `file_save` ×8 in 3 min, same file | Collapse to 1 | Auto-save artifact; dedup within 60s window |
| `window: VS Code → Slack (2 min) → VS Code` | Keep, annotate `lookup` | Likely async unblock, same session |
| `window: VS Code → Slack (15 min)` | Boundary candidate | Sustained non-dev switch |
| `window: Chrome — stackoverflow.com` | Keep, annotate `lookup_url` | Reference lookup, same session |
| `shell: ls` / `pwd` / `git status` | **Drop** | Navigational, no work-content signal |
| `shell: pytest tests/test_auth.py` | **Keep** | Test run scoped to active work area |
| `git_commit` | **Anchor, never drop** | Session label seed + boundary marker |

### 4. Updated `sessions` Table Schema

```sql
CREATE TABLE sessions (
    session_id       TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    ended_at         TEXT NOT NULL,
    duration_secs    INTEGER,
    primary_repo     TEXT,
    related_repos    TEXT,         -- JSON array: ["payments-service", ...]
    files_touched    TEXT,         -- JSON array: [{path, changes}]
    anchor_commit    TEXT,
    commit_message   TEXT,
    ticket_refs      TEXT,         -- JSON array: ["JIRA-401", "#402"]
    test_runs        TEXT,         -- JSON array: [{cmd, exit_code, ts}]
    lookup_urls      TEXT,         -- JSON array: SO/docs URLs visited
    session_phases   TEXT,         -- JSON array: [{phase, duration_secs}]
    boundary_signal  TEXT,         -- "commit"|"repo_change"|"idle"|"ticket_change"
    event_count      INTEGER,
    noise_filtered   INTEGER,
    pii_scrubbed     BOOLEAN DEFAULT FALSE,
    confidence       REAL,
    workflow_hash    TEXT          -- hash(frozenset(files_touched paths))
);
```

`workflow_hash` is the key field for Pillar 3 to answer "I've seen this before":

```python
import hashlib

def compute_workflow_hash(files_touched: list[dict]) -> str:
    paths = frozenset(f["path"] for f in files_touched)
    return hashlib.sha256(str(paths).encode()).hexdigest()[:16]
```

---

## Proposed Sessionization Logic

```python
SESSION_IDLE_THRESHOLD = 300  # 5 minutes

def sessionize(events: list[dict]) -> list[Session]:
    sessions = []
    current = []
    for i, event in enumerate(events):
        if current:
            gap = parse_ts(event['ts']) - parse_ts(current[-1]['ts'])
            if gap.total_seconds() > SESSION_IDLE_THRESHOLD:
                sessions.append(Session(events=current))
                current = []
        current.append(event)
    if current:
        sessions.append(Session(events=current))
    return sessions
```

---

## Proposed PII Masking Pipeline

```
Raw event text
      ↓
  [Regex pass]  — passwords (pattern: field "password" adjacent), credit cards
      ↓
  [NER pass]    — spaCy en_core_web_sm (local, no cloud) — PERSON, ORG, GPE, CARDINAL
      ↓
  [Hash/redact] — replace with [PII_PERSON], [PII_CARD], etc.
      ↓
  Masked event stored in DB  (original never written to disk)
```

Candidate libraries:
- `spaCy` with `en_core_web_sm` (~12MB, fully local)
- `presidio-analyzer` (Microsoft, local NER, supports 50+ entity types)
- Custom regex bank for structured PII (card numbers, SSNs, API keys)

---

## Data Schema (proposed `sessions` table)

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    app_focus   TEXT,           -- dominant app during session
    label       TEXT,           -- AI-generated label (filled by Pillar 3)
    event_count INTEGER,
    duration_s  INTEGER,
    masked_count INTEGER DEFAULT 0
);
```

---

## Effort Estimate

| Component | Days |
|-----------|------|
| Idle-time sessionizer | 1 |
| Regex PII scrubber | 1 |
| Local NER integration (spaCy / presidio) | 2 |
| Noise filter (micro-event pruning) | 1–2 |
| Session labeling hook (calls Pillar 3) | 1 |
| Privacy audit log | 1 |
| **Total** | **7–8 days** |
