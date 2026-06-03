# Pillar 1 — Telemetry & Data Capture

> **The "Eyes & Ears"** — the system cannot understand what it cannot see.
> Coverage today: **40%** | Status: 🔶 Partial

---

## Goal

Build a lightweight, always-on background agent that captures OS-level user
interactions — keystrokes, mouse events, active windows, and screen content —
so higher pillars can model and automate workflows.

---

## What Already Exists

### `memoria/companion.py`
- Reads the **active window title** every N seconds using:
  - Windows: `ctypes.windll.user32.GetForegroundWindow()`
  - macOS: `AppKit.NSWorkspace.sharedWorkspace().activeApplication()`
  - Linux: `xdotool getactivewindow getwindowname`
- Reads **clipboard** via `pyperclip`
- Debounces context changes (3s stable before acting)
- Searches Memory Banks and surfaces relevant snippets

### `memoria/tracker.py`
- **File change events** via `watchdog` (extension-filtered, 5s debounce)
- **Git commit polling** every 30s via `git log --oneline -1`
- **Session start** event recorded on daemon launch
- SQLite `activity.db` at `~/.memoria/activity.db`
- PID management for daemon lifecycle

---

## What Is Missing

| Gap | Description | Effort |
|-----|-------------|--------|
| Shell plugin integration | zsh/bash `precmd`/`preexec` hooks — capture terminal commands, cwd, exit codes into `interactions` table | Small |
| Browser URL extraction | macOS AppleScript / Windows UIA — extract active tab URL on focus change (no extension needed) | Small |
| Process spawn monitoring | `psutil` event stream — detect `pytest`, `docker`, `make`, CI runs and their exit codes | Small |
| Accessibility tree reading | `AXUIElement` (macOS) / `UI Automation` (Windows) — extract focused editor file, function name, terminal last line; structured, no image processing | Medium |
| Connector ingestion pipelines | Event-driven ingest from Jira, Slack, GitHub, Notion, Confluence — pull text AND metadata (who said what, when, to whom) | Large |

> **Removed from scope:** Global keyboard hooks, mouse coordinate capture, and high-frequency screenshots. These are surveillance-level signals that add DLP risk without meaningfully improving Memory Bank quality. Accessibility tree + shell plugin + focus transitions give equivalent workflow signal at a fraction of the privacy cost.

---

## Architecture Decisions (Resolved ✅)

> **D1 →** New modules inside Memoria (`memoria intelligence start`, new FastAPI routes, companion extension). Not a separate product.
>
> **D3 →** Windows + macOS from day one. Shell plugin (cross-platform), AppleScript (macOS) + UIA (Windows) for browser URL. Linux later.

## Architecture Decision

**Minimal developer-intent signal set. Shell plugin as default terminal capture. Accessibility tree primary, screenshot fallback only.**

We do NOT need keystrokes, mouse coordinates, or scroll depth for developer workflow reconstruction. Developer workflows are defined by intent transitions:

| Signal | Capture Method | Priority |
|--------|---------------|----------|
| Terminal command + exit code | Shell plugin (`zsh`/`bash` `precmd`/`preexec`) | **Must-have** |
| Focus transition + browser URL | macOS AppleScript / Windows UIA | **Must-have** |
| Process spawn events (`pytest`, `docker`, `make`) | `psutil` event stream | High |
| Editor file open | Accessibility tree (AXUIElement / UIA) | High |
| Git events (branch + commit) | Existing `tracker.py` + branch name | Existing |
| Screenshots | Active-window crop, local OCR, fallback only | Optional |

**Keystrokes and mouse position: explicitly out of scope for v1.** High PII risk, minimal workflow-reconstruction value.

## Proposed Implementation

### 1. Shell Plugin — Default Terminal Capture

Ship a zsh/bash plugin. The Memoria installer appends one line to `~/.zshrc`:

```zsh
# ~/.memoria/shell/memoria.plugin.zsh
export MEMORIA_PIPE="${HOME}/.memoria/shell.pipe"

preexec() {
    echo "$(date -Iseconds)|$$|${PWD}|cmd|${1}" >> "${MEMORIA_PIPE}"
}

precmd() {
    local exit_code=$?
    echo "$(date -Iseconds)|$$|${PWD}|exit|${exit_code}" >> "${MEMORIA_PIPE}"
}
```

`tracker.py` tails `shell.pipe` and inserts events into `interactions`.
Captures: command text, cwd, exit code, duration. **Does NOT capture stdout/stderr** (volume + secrets risk).

PTY wrapper (`script`-style capture) is opt-in "deep capture" mode for power users who need command output.

### 2. Browser URL Capture (Focus-Triggered Only)

```python
def get_browser_url(app_name: str) -> str | None:
    """Extract active tab URL without a browser extension."""
    if sys.platform == "darwin":
        script = f'tell application "{app_name}" to get URL of active tab of front window'
        try:
            return subprocess.check_output(
                ["osascript", "-e", script], timeout=2
            ).decode().strip()
        except subprocess.CalledProcessError:
            return None
    elif sys.platform == "win32":
        return _get_url_via_uia(app_name)   # comtypes UI Automation address bar read
    return None   # Linux: window-title fallback only
```

Fire only on window focus change — not on a polling interval.

### 3. Accessibility Tree Primary, Screenshot Fallback

```python
POOR_A11Y_APPS = {"idea64.exe", "webstorm64.exe", "rider64.exe"}

def get_ui_context(app_name: str, pid: int) -> dict:
    if app_name.lower() not in POOR_A11Y_APPS:
        ctx = _read_accessibility_tree(app_name, pid)
        if ctx:
            return ctx
    # Fallback: crop active window only, OCR locally — never send to API
    return _capture_window_screenshot_local(pid)
```

A11y tree reads do not trigger corporate DLP software. Screenshot fallback uses local OCR only.

### 4. Updated `interactions` Table Schema

```sql
CREATE TABLE IF NOT EXISTS interactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    session_id   TEXT,           -- 30-min idle gap = new session_id
    event_type   TEXT NOT NULL,  -- 'focus_change'|'shell_cmd'|'file_open'
                                 --   |'test_run'|'browser_url'|'git_event'
    app          TEXT,
    context      TEXT,           -- JSON: {url, file_path, command, exit_code, branch}
    repo         TEXT,           -- matches memory bank project name
    masked       INTEGER DEFAULT 0  -- 1 = PII scrubbed by Pillar 2
);
```

`context` is a JSON blob so the schema stays stable as new event sub-types are added.

---

## Dependencies

| Library | Purpose | Platform |
|---------|---------|---------|
| `pyhook` / `pyWinhook` | Global keyboard + mouse hooks | Windows only |
| `Quartz` (pyobjc) | `CGEventTap` for low-level input | macOS only |
| `python-evdev` | Raw input device events | Linux only |
| `pillow` + `mss` | Fast screenshot capture | Cross-platform |
| `pywinauto` / `pyaccessibility` | UI Automation / AXUIElement wrapper | Win/Mac |

---

## Data Schema (proposed extension to `activity.db`)

```sql
CREATE TABLE IF NOT EXISTS interactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,           -- ISO timestamp
    event_type   TEXT NOT NULL,           -- 'keystroke' | 'click' | 'screenshot' | 'ui_element'
    app_name     TEXT,                    -- active application name
    app_pid      INTEGER,                 -- process ID
    window_title TEXT,                    -- window title
    x            REAL,                    -- mouse X (normalised 0–1)
    y            REAL,                    -- mouse Y (normalised 0–1)
    element_label TEXT,                   -- accessibility label (if available)
    screenshot_path TEXT,                 -- path to compressed screenshot (if captured)
    masked        INTEGER DEFAULT 0       -- 1 = PII scrubbed by Pillar 2
);
```

---

## Effort Estimate

| Component | Days |
|-----------|------|
| Global keyboard + mouse hooks (Win/Mac) | 3–4 |
| Screenshot capture pipeline | 2–3 |
| Accessibility API integration | 4–5 |
| Data schema + storage | 1 |
| Cross-platform abstraction layer | 2 |
| **Total** | **12–15 days** |
