# Capability Detection — Full Specification

> This document defines the exact logic for detecting what a work item requires (design, implementation, tests) and what assets already exist. This was the most critical missing spec identified in the BA review.

---

## What "Capability Detection" Means

When a work item (ticket) opens, the system determines:
1. What type of work this ticket represents
2. What assets already exist (Figma spec? existing branch? existing tests?)
3. What is missing (the "capability gaps")

The capability gap list drives the LangGraph workflow routing — which generation nodes run, in what order.

---

## Input Signals

### Signal A — Ticket keyword extraction

Applied to: ticket title + description + acceptance criteria (concatenated, lowercased)

```python
IMPLEMENTATION_SIGNALS = {
    "implement", "add", "create", "build", "write", "develop",
    "fix", "update", "modify", "change", "refactor", "migrate",
    "integrate", "connect", "wire", "hook up", "set up"
}

FRONTEND_SIGNALS = {
    "ui", "frontend", "front-end", "screen", "page", "view",
    "component", "modal", "form", "button", "table", "layout",
    "design", "ux", "interface", "widget", "dialog", "sidebar",
    "dropdown", "tab", "panel", "responsive", "css", "style"
}

TEST_SIGNALS = {
    "test", "tests", "unit test", "integration test", "e2e",
    "coverage", "spec", "assertion", "mock", "fixture"
}

BACKEND_SIGNALS = {
    "api", "endpoint", "service", "database", "query", "schema",
    "migration", "model", "repository", "cache", "queue",
    "backend", "server-side", "worker", "job", "cron"
}
```

A signal is "triggered" if any keyword from its set appears in the ticket text (word boundary match, not substring).

### Signal B — Ticket metadata (if Jira MCP is configured)

| Jira field | Used for |
|---|---|
| `issuetype.name` | "Bug" → `needs_implementation`, skip design. "Story" → check both. "Task" → check keywords. |
| `components` | Component names matched against sub-unit names in Memory Banks → identify affected modules |
| `labels` | "frontend", "backend", "design", "testing" as direct signals |
| `linkedIssues` | Linked Figma or design tickets → infer design exists |
| `attachments` | Figma embed or image attachment → infer design exists |

### Signal C — MCP asset existence check

Called on-demand when MCP is configured; skipped (defaults to False) when not configured.

```python
@dataclass
class AssetInventory:
    figma_linked: bool       # Figma frame ID found in Jira custom fields or description URL
    branch_exists: bool      # GitHub branch matching ticket ID exists
    pr_open: bool            # Open PR referencing this ticket
    test_files_exist: bool   # Test sub-unit in .file_graph.json has tests edge to matched module
```

**Figma detection:** scan ticket description and custom fields for URLs matching `figma.com/file/` or `figma.com/design/`. If the Figma MCP is connected, call `get_file()` on the found frame ID to confirm it's accessible. If Figma MCP is not connected but a Figma URL is present, record `figma_linked=True` based on URL alone.

**Branch detection:** GitHub MCP `list_branches()` → search for branch names containing the ticket ID (e.g., `PROJ-142`). Case-insensitive prefix match.

**Test file detection:** load `.file_graph.json` → find `tests` edges to any sub-unit that matched in the retrieval step.

---

## Capability Gap Decision Logic

```python
def detect_capability_gaps(
    ticket_text: str,
    ticket_metadata: dict | None,
    assets: AssetInventory,
    retrieval_results: list[RetrievalResult]
) -> list[str]:
    gaps = []

    # --- needs_implementation ---
    impl_keywords_found = any(kw in ticket_text.lower() for kw in IMPLEMENTATION_SIGNALS)
    is_bug = (ticket_metadata or {}).get("issuetype", {}).get("name") == "Bug"
    is_task_or_story = (ticket_metadata or {}).get("issuetype", {}).get("name") in {"Story", "Task", "Feature"}

    if impl_keywords_found or is_bug or is_task_or_story:
        if not assets.pr_open:   # PR open = implementation likely already started
            gaps.append("needs_implementation")

    # --- needs_tests ---
    test_keywords_found = any(kw in ticket_text.lower() for kw in TEST_SIGNALS)
    has_matched_modules = len(retrieval_results) > 0
    test_edge_exists = assets.test_files_exist

    # Add if: explicit test signal in ticket, OR implementation needed + no existing test coverage
    if test_keywords_found:
        gaps.append("needs_tests")
    elif "needs_implementation" in gaps and has_matched_modules and not test_edge_exists:
        gaps.append("needs_tests")

    # --- needs_design (Phase 2 only) ---
    frontend_keywords_found = any(kw in ticket_text.lower() for kw in FRONTEND_SIGNALS)
    if frontend_keywords_found and not assets.figma_linked:
        gaps.append("needs_design")  # Phase 2: only if design_skeleton capability is enabled

    return gaps
```

**Ordering:** `needs_design` is always resolved before `needs_implementation` (you need a spec before you write code). `needs_implementation` is resolved before `needs_tests` (you need code before you test it). LangGraph routing enforces this ordering.

---

## Edge Cases and Handling

### Ticket with no description (title only)
- `ticket_text` = title only (from Jira `summary` field)
- Signal extraction runs on title text — lower signal quality, not an error
- `retrieval_results` will likely be sparse (low scores)
- If below retrieval threshold (0.3): `capability_gaps = ["needs_implementation"]` by default (conservative)
- UI banner: "Ticket has no description. Context quality is limited — add acceptance criteria for better results."

### No MCP configured
- `assets` defaults to `AssetInventory(figma_linked=False, branch_exists=False, pr_open=False, test_files_exist=False)`
- Capability detection runs on keywords only
- System is more aggressive in detecting gaps (all three defaults to False → more gaps detected)
- This is intentional: better to offer help that isn't needed than to miss a genuine gap

### All retrieval scores below threshold
- `relevant_sub_banks = []`
- `file_graph_slice = []`
- Gaps still detected from keywords
- Generation nodes run with ticket context only (no Memory Bank grounding)
- UI warning: "No relevant Memory Banks found. Generated artifacts may not match your codebase patterns."
- Do NOT block the workflow — let the user decide whether to proceed

### MCP call fails during asset check
- Treat as asset not found (conservative)
- Log the error with source name and exception
- UI shows: "Could not check [source] — assuming assets are missing"
- Never block the workflow due to MCP availability

### Work item has no matched module in file graph
- `test_edge_exists = False` by default
- `needs_tests` may still be added if test keywords found in ticket

---

## Session Persistence

When the context assembler completes, its output is stored in the LangGraph `WorkState` and checkpointed to `workflow_state.db`. On session reopen (same ticket), the state is loaded from the checkpointer:

- Previous capability gaps and their resolution status are visible
- Approved artifacts are shown in the UI with "Previously approved" label
- If `needs_implementation` was resolved in a previous session (code was approved), the gap is not re-detected
- User can re-open any capability and regenerate from the Work Item Detail screen

---

## Acceptance Criteria for Context Assembly

The BA review identified the absence of measurable success criteria. Defined here:

| Criterion | Definition |
|---|---|
| Relevant sub-banks selected | At least one of the top-3 retrieved sub-banks is annotated as "related" by the developer (to be validated via "Was this context helpful?" thumbs up/down in UI) |
| Capability gap accuracy | Developer proceeds with the generated artifact without rejecting it on the first attempt (proxy for correct gap detection) |
| False positive rate | Developer does not use "Dismiss" on a capability gap banner more than 15% of sessions (tracked from gap dismissal events) |
| Retrieval latency | Context assembly completes in <3 seconds for a 10-sub-unit project |
