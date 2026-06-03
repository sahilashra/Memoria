# Pillar 3 — Workflow Understanding & AI Modeling

> **The "Brain"** — converts raw clicks and keystrokes into abstract,
> understandable human tasks and detects repeating patterns.
> Coverage today: **30%** | Status: 🔶 Partial

---

## Goal

Apply AI to the cleaned session data from Pillar 2 to:
1. Identify what the user is doing in natural language
2. Detect repeating multi-step workflow patterns across sessions
3. Produce structured workflow descriptions that Pillar 4 can automate

---

## What Already Exists

### `memoria/learner.py`
- Watches git commits and correlates: commit message, changed files, linked
  tickets, recent file-change events from `tracker.py`
- Calls the LLM once per commit: "What semantic work unit was just completed?
  Is it significant enough to update the Memory Bank? Confidence 0–1."
- If `confidence >= threshold` (default 0.65), queues a targeted Memory Bank
  draft update
- Stores last 50 inference records in `~/.memoria/learn_history.json`

### `memoria/tracker.py`
- SQLite activity log provides the raw event stream that `learner.py` reads
- Feeds `repo`, `event_type`, `detail` per event

---

## What Is Missing

| Gap | Description | Effort |
|-----|-------------|--------|
| Visual Interface Parsing | Pass screenshots through a VLM (llava/UI-TARS/Claude) to identify semantic UI elements: "Submit button in Salesforce Opportunity form", "Column B in Excel sheet 'Q3 Budget'" | Large |
| Cross-session sequence mining | Apply PrefixSpan / SPADE or hierarchical clustering across all sessions to find patterns that repeat ≥ N times | Large |
| Multi-app workflow detection | Recognise a task that spans multiple apps ("Copy from Excel → paste into Chrome form → click Submit") as a single logical workflow | Large |
| Temporal pattern detection | Detect time-based patterns: "This workflow happens every Monday morning between 9–10am" | Medium |
| Workflow schema output | Produce a structured JSON workflow description consumable by Pillar 4's RPA generator | Medium |

---

## Architecture Decisions (Resolved ✅)

> **D5 →** Accessibility API primary (AXUIElement / UI Automation) — free, fast, no DLP risk. Fallback to `llava:7b-v1.6-Q4_K_M` via Ollama for apps with poor a11y support (e.g. JetBrains IDEs). No cloud VLM calls.

## Architecture Decision

**Accessibility API first for UI context (not VLM by default). Memory Bank context injected into every LLM label call. SQL rule patterns before ML clustering.**

The most expensive missing piece — VLM screenshot parsing — is largely avoidable. The OS accessibility tree gives structured semantic UI data for free and without DLP risk. Reserve VLM calls for the rare apps that block accessibility access.

For workflow pattern detection: 5 high-value developer patterns are detectable with pure SQL. ML clustering (sentence-transformers + HDBSCAN) handles novel pattern discovery without GPU or cloud.

## Proposed VLM Integration

**Primary: Accessibility API (AXUIElement on macOS / UI Automation on Windows) — free, fast, structured.**

For VS Code, terminal, Chrome, Firefox: the accessibility tree returns element data directly (focused file path, function under cursor, terminal last line, browser URL). No model call needed.

**Fallback: `llava:7b-v1.6-Q4_K_M` via Ollama — for poor-a11y apps only.**

```python
POOR_A11Y_APPS = {"idea64.exe", "webstorm64.exe", "slack"}

def get_screen_context(app: str, pid: int) -> dict:
    if app.lower() not in POOR_A11Y_APPS:
        ctx = read_accessibility_tree(app, pid)
        if ctx.get("elements"):
            return ctx   # structured, no ML cost, no DLP risk
    # Fallback: local VLM — cropped to active window only
    screenshot = capture_window(pid)
    return query_vlm_local(screenshot)

def query_vlm_local(image_bytes: bytes) -> dict:
    """Local Ollama call — screenshot never leaves the machine."""
    return litellm.completion(
        model="ollama/llava:7b-v1.6-Q4_K_M",
        messages=[{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64(image_bytes)}"}},
            {"type": "text",
             "text": "What UI elements are visible? What action is the user taking? "
                     "JSON: {app, screen, action, elements[]}"}
        ]}]
    )
```

## Proposed Sequence Mining Approach

### Stage 1: Memory Bank-Augmented Session Labeling

Before calling the LLM to label a session, run a ChromaDB semantic search against existing Memory Banks using the session's git commit message + file paths as the query:

```python
LABEL_PROMPT = """
You are labeling a developer work session for: {project_name}.

## Relevant project knowledge (from Memory Banks):
{chroma_results}

## Session signals:
- Files changed: {file_changes}
- Git commit: "{git_commit}"
- Active window: "{active_window}"
- Duration: {duration_minutes} min
- Test runs: {test_results}

## Task:
1. Label this session with ONE activity type:
   [bug-fix, feature-addition, refactor, test-writing, investigation,
    dependency-update, config-change, code-review, documentation]
2. Write a one-sentence description grounded in the Memory Bank context above.
   BAD: "Developer fixed a bug in authentication"
   GOOD: "Developer patched the known JWT expiry gap in auth/jwt.py"
3. List 1-3 codebase concepts touched (use Memory Bank names, not generic terms).
4. Confidence: 0.0–1.0

JSON: {"activity_type": "...", "description": "...", "concepts": [...], "confidence": 0.0}
"""
```

Query ChromaDB with `" ".join(file_changes) + " " + git_commit` before building the prompt. Limit injected context to ≤400 tokens.

### Stage 2: SQL Rule Patterns (No ML Required)

Five high-value developer patterns detectable with pure SQL:

| Pattern | Detection Signal | Threshold |
|---------|-----------------|-----------|
| Ticket-to-code pipeline | `ticket_refs` populated → files in matching service edited within 30 min | ≥5 occurrences |
| TDD micro-loop | Median file saves between consecutive test runs = 2–4 saves | ≥8 occurrences |
| Debugging spiral | `investigation` session → `bug-fix` session on same files within 4 hrs | ≥3 occurrences |
| Pre-deploy checklist | App sequence `editor → pytest → docker → browser(staging)` within 20 min | ≥3 occurrences |
| Context-switch cost | Sessions <8 min, no commit, different file cluster immediately after | ≥5 occurrences |

### Stage 3: ML Clustering (Novel Pattern Discovery)

For ambiguous sessions and novel workflow discovery beyond the rule patterns:

```python
from sentence_transformers import SentenceTransformer
import hdbscan

model = SentenceTransformer("all-MiniLM-L6-v2")  # 22MB, CPU-only, loads in ~1s

def cluster_sessions(sessions: list[dict]) -> dict[int, list[str]]:
    texts = [
        f"{s['commit_message'] or ''} | "
        f"{' '.join(f['path'] for f in s['files_touched'])} | "
        f"{s['app_focus']}"
        for s in sessions
    ]
    embeddings = model.encode(texts, batch_size=32)   # ~50ms per session on CPU

    labels = hdbscan.HDBSCAN(
        min_cluster_size=3, min_samples=1
    ).fit_predict(embeddings)

    clusters: dict[int, list[str]] = {}
    for label, session in zip(labels, sessions):
        if label >= 0:   # -1 = noise
            clusters.setdefault(label, []).append(session["session_id"])
    return clusters
```

Pass centroid sessions from each cluster back to the LLM (with Memory Bank context) to generate a human-readable workflow name.

### Stage 4: Feedback Loop → Memory Banks

When a workflow is confirmed (≥3 occurrences + `memoria confirm <workflow_id>`), Pillar 3 generates a structured Memory Bank patch and routes it through the existing `learner.py` draft queue:

```markdown
## § Common Workflows
<!-- auto-detected by Pillar 3, confirmed 2024-04-10 -->
### OAuth Client Onboarding (~2 hrs, biweekly)
**Trigger:** Jira ticket matching "oauth.*client" opened
**Steps:** Read ticket → edit `auth/oauth_clients.py` + `auth/jwt.py` → pytest → commit
**Friction point:** JWT expiry edge case re-surfaces on each new client (see § Known Issues).
**Automation candidate:** Step 2 edits are templated — Pillar 4 has a generated script.
```

Confidence gates: 0.65–0.85 → `learner.py` draft queue (user reviews); >0.85 + user confirmation → auto-apply.

---

## Proposed Workflow Modeling Pipeline

```
Cleaned sessions (from Pillar 2)
          ↓
  [Step 1: Per-session labeling]
  LLM prompt: "Here are the UI events from this 8-minute session.
  What task was the user performing? Output JSON:
  { task: str, app: str, steps: [str], confidence: float }"
          ↓
  [Step 2: Cross-session clustering]
  Embed task descriptions (sentence-transformers / local)
  Cluster with HDBSCAN or hierarchical clustering
  Flag clusters with ≥ N occurrences as "candidate workflows"
          ↓
  [Step 3: Workflow schema generation]
  For each candidate workflow cluster, LLM synthesises:
  { name, trigger, steps[], apps[], estimated_duration, frequency, confidence }
          ↓
  Workflow registry (~/.memoria/workflows.json)
          ↓
  Pillar 4 reads registry → generates automation suggestions
```

---

## Proposed Workflow Schema (JSON)

```json
{
  "id": "wf_copy_excel_to_crm",
  "name": "Weekly report migration to CRM",
  "detected_at": "2026-05-15T09:00:00",
  "occurrences": 12,
  "frequency": "weekly",
  "typical_time": "Monday 09:00–09:45",
  "confidence": 0.87,
  "trigger": "Open Excel file matching 'Q*_report.xlsx'",
  "steps": [
    "Open Excel, navigate to Sheet 'Summary'",
    "Copy cells A1:F20",
    "Switch to Chrome, navigate to CRM opportunity form",
    "Paste into 'Revenue Forecast' field",
    "Click Submit"
  ],
  "apps": ["excel", "chrome"],
  "automatable": true,
  "automation_type": "playwright"
}
```

---

## Candidate Libraries

| Library | Purpose | Size | Local? |
|---------|---------|------|--------|
| `sentence-transformers` | Embed task descriptions for clustering | ~100MB | ✅ |
| `hdbscan` | Density-based clustering — handles noise well | Small | ✅ |
| `prefixspan` | Sequential pattern mining | Small | ✅ |
| `llava` via Ollama | Local VLM for screenshot parsing | 4–7GB | ✅ |
| `Claude claude-3-5-sonnet` via LiteLLM | Cloud VLM for screenshot parsing | API | ❌ local |
| `UI-TARS` (Byte Dance) | Specialised UI understanding model | 7–72B | ✅ |

---

## Effort Estimate

| Component | Days |
|-----------|------|
| VLM screenshot parsing integration | 4–5 |
| Per-session LLM labeling | 2 |
| Embedding + clustering pipeline | 3–4 |
| Temporal pattern detection | 2 |
| Workflow schema generator | 2 |
| Workflow registry storage | 1 |
| **Total** | **14–16 days** |
