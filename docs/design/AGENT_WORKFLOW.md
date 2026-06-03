# Agent Workflow Design — Open-Ended SDLC Agent

> **Updated 2026-05-27** — the old rigid pipeline (generate → approve → generate → approve) has been replaced with an open-ended chat architecture. This doc reflects the current implementation.

---

## Architecture Summary

The agent has two distinct parts:

1. **Context assembly** (`analyze_context → execute_actions`) — runs once when a work item opens. Assembles Memory Banks, detects capability gaps (as suggestions), proposes write-back actions.

2. **Work item chat** (`/api/workflow/chat`) — the actual agent loop. User drives all SDLC tasks: write code, tests, analysis, PR descriptions, planning. No forced order.

---

## LangGraph Graph

```
analyze_context → execute_actions → END
```

**`analyze_context`** — assembles:
- Relevant sub-Memory Banks (via hybrid retrieval: ChromaDB + graph traversal + RRF)
- Live MCP data (Jira ticket details, linked Figma frame, recent PRs)
- Capability gaps: `["needs_implementation", "needs_tests", ...]` — surfaced as chat suggestions, NOT mandatory pipeline steps

**`execute_actions`** — proposes write-back actions:
- Jira comment (session summary)
- VCS branch creation (GitHub / GitLab)
- PR / MR creation
- Emits `actions_available` SSE event; user confirms via the actions bar

### WorkState

```python
class WorkState(TypedDict):
    thread_id: str
    ticket_text: str
    project_name: str
    books_dir: str
    config_path: str

    # Populated by analyze_context
    capability_gaps: list[str]
    work_context_summary: str
    relevant_modules: list[str]
    external_blocks: list[dict]
    skip_external_fetch: bool

    # Outcome
    actions_proposed: list[dict]
    actions_taken: list[str]
    error: Optional[str]
```

No artifact fields. No approval tracking. The chat handles generation; the state only tracks context and actions.

---

## SSE Event Types

### Workflow stream (`GET /api/workflow/stream/{thread_id}`)

```
{"type": "context_analyzing",  "message": "Assembling context…"}
{"type": "context_analyzed",   "capability_gaps": [...], "summary": "...", "relevant_modules": [...], "context_chunks": [...]}
{"type": "actions_available",  "actions": [{id, label, description, detail, source, tool, args}, ...]}
{"type": "action_done",        "action_id": "...", "label": "...", "result": "...", "ok": true}
{"type": "done",               "message": "Context ready"}
{"type": "error",              "message": "..."}
{"type": "warning",            "message": "..."}
{"type": "ping"}               // keepalive every 30s
```

### Chat stream (`POST /api/workflow/chat` → SSE response)

```
": keepalive\n\n"
{"type": "chunk",  "text": "..."}   // one or many
{"type": "done"}
{"type": "error",  "message": "..."}
```

---

## Chat Endpoint — Context Injection

`/api/workflow/chat` reads the assembled state from the LangGraph checkpointer and injects it as the system prompt:

```
System:
  You are an expert software engineer AI assistant helping a developer work through a ticket.
  [ticket text]
  [work_context_summary from Memory Banks]
  [capability_gaps as suggestions]

User: [message]
```

The user can ask for anything — implementation, tests, analysis, PR descriptions, impact assessment. The agent responds in markdown with code blocks where appropriate.

Chat history (last 10 turns) is passed per request. Sessions are ephemeral — no server-side chat history stored.

---

## Actions Flow

```
1. analyze_context + execute_actions run → "actions_available" SSE event
2. UI shows checkbox cards per action
3. User selects actions → POST /api/workflow/actions/confirm
4. run_confirmed_actions() calls each MCP tool, streams action_done events
5. Session log written to books/_work_sessions/{ticket_id}.md
```

Write-back actions are always user-confirmed. Never executed silently.

---

## Phase 1 Constraint: Single Worker

`_event_queues` is process-level. `POST /api/workflow/actions/confirm` must hit the same Uvicorn worker that started the workflow. Run with `--workers 1`.

Phase 2: Redis pub/sub, one channel per `thread_id`.

---

## Session Auto-Save

After `run_confirmed_actions` completes, a human-readable log is written to:
```
books/_work_sessions/{ticket_id}.md
```

Contents: ticket text, capability gaps, actions taken, timestamp.
