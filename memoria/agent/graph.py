"""
LangGraph Adaptive Work Agent — open-ended SDLC assistant.

Architecture:
  1. analyze_context  — assemble Memory Banks + live MCP data + detect gaps (as suggestions)
  2. execute_actions  — propose write-back actions (Jira comment, branch, PR)
  3. END

The work item chat (stream_work_chat) is the actual agent loop.
It uses the assembled context from analyze_context as its system prompt.
Users drive the flow — ask for code, tests, analysis, or anything SDLC-related.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import uuid
from typing import Annotated, Any, AsyncGenerator, Optional, TypedDict

# ─── Process-level SSE bridge ────────────────────────────────────────────────
# Keyed by thread_id. Each running workflow writes events here;
# the SSE endpoint in ui.py reads from it.

_event_queues: dict[str, asyncio.Queue] = {}


def _get_event_queue(thread_id: str) -> asyncio.Queue:
    if thread_id not in _event_queues:
        _event_queues[thread_id] = asyncio.Queue()
    return _event_queues[thread_id]


def _push_event(thread_id: str, event_type: str, data: dict):
    """Non-blocking push to the SSE queue for this thread."""
    q = _get_event_queue(thread_id)
    try:
        q.put_nowait({"type": event_type, **data})
    except asyncio.QueueFull:
        pass  # drop if consumer is too slow


def cleanup_thread(thread_id: str):
    """Remove queues for a completed or timed-out thread."""
    _event_queues.pop(thread_id, None)


# ─── Stop signals ─────────────────────────────────────────────────────────────
_stop_flags: dict[str, bool] = {}


def stop_workflow(thread_id: str):
    """Signal a running workflow to stop. Safe to call from any context."""
    _stop_flags[thread_id] = True
    q = _event_queues.get(thread_id)
    if q:
        try:
            q.put_nowait({"type": "stopped", "message": "Workflow stopped by user"})
        except asyncio.QueueFull:
            pass
    cleanup_thread(thread_id)


# ─── State ────────────────────────────────────────────────────────────────────

class WorkState(TypedDict):
    thread_id: str
    ticket_text: str
    project_name: str
    books_dir: str
    config_path: str

    # Populated by analyze_context
    capability_gaps: list[str]
    work_context_summary: str       # human-readable summary for SSE events
    relevant_modules: list[str]

    # External reference blocks fetched during context assembly
    external_blocks: list[dict]
    skip_external_fetch: bool

    # Outcome
    actions_proposed: list[dict]   # proposed write-back actions shown to user
    actions_taken: list[str]
    error: Optional[str]


def _default_state(
    ticket_text: str,
    project_name: str,
    books_dir: str,
    config_path: str,
    thread_id: Optional[str] = None,
    skip_external_fetch: bool = False,
) -> WorkState:
    return WorkState(
        thread_id=thread_id or str(uuid.uuid4()),
        ticket_text=ticket_text,
        project_name=project_name,
        books_dir=books_dir,
        config_path=config_path,
        capability_gaps=[],
        work_context_summary="",
        relevant_modules=[],
        external_blocks=[],
        skip_external_fetch=skip_external_fetch,
        actions_proposed=[],
        actions_taken=[],
        error=None,
    )


# ─── Tool-name patterns for write-back ────────────────────────────────────────

_TOOL_PATTERNS: dict[str, list[str]] = {
    "jira_comment":  ["add_comment", "create_comment", "post_comment", "comment_issue", "comment"],
    "jira_update":   ["update_issue", "edit_issue", "update_issue_description", "update"],
    "vcs_branch":    ["create_branch", "create_ref", "branch_create"],
    "vcs_pr":        ["create_pull_request", "create_pr", "open_pull_request"],
    "vcs_mr":        ["create_merge_request", "create_mr", "open_merge_request"],
    "zephyr_test":   ["create_test_case", "create_test", "zephyr_create_test", "test_case_create"],
}


async def _discover_tools(
    source_name: str,
    config_path: str,
    timeout: float = 8.0,
) -> tuple[set[str], bool]:
    """
    List available tools from a configured MCP source.
    Returns (tool_names, verified) where verified=False means unreachable.
    """
    from ..mcp_sources import list_tools_async
    try:
        tools = await asyncio.wait_for(
            list_tools_async(server=source_name, config_path=config_path),
            timeout=timeout,
        )
        return {t["name"] for t in tools}, True
    except Exception:
        return set(), False


def _match_tool(available: set[str], patterns: list[str]) -> Optional[str]:
    """Return the first pattern that matches an available tool name."""
    available_lower: dict[str, str] = {n.lower(): n for n in available}
    for p in patterns:
        p_l = p.lower()
        if p_l in available_lower:
            return available_lower[p_l]
    for p in patterns:
        p_l = p.lower()
        for name_l, name_orig in available_lower.items():
            if p_l in name_l:
                return name_orig
    return None


# ─── Nodes ────────────────────────────────────────────────────────────────────

async def _analyze_context_node(state: WorkState) -> WorkState:
    """Assemble work context, detect capability gaps, map relevant Memory Banks."""
    thread_id = state["thread_id"]
    _push_event(thread_id, "context_analyzing", {"message": "Assembling context…"})

    gaps = []
    summary = ""
    modules: list[str] = []
    external_blocks: list[dict] = []
    retrieval_results: list = []

    try:
        from .context import assemble_context, ExternalFetchError
    except ImportError as exc:
        _push_event(thread_id, "error", {"message": f"Context module unavailable: {exc}"})
        return {**state, "error": str(exc)}

    try:
        ctx = await assemble_context(
            ticket_text=state["ticket_text"],
            project_name=state["project_name"],
            books_dir=state["books_dir"],
            config_path=state["config_path"],
            skip_external_fetch=state.get("skip_external_fetch", False),
        )
        gaps = ctx.gap_list
        summary = ctx.summary()
        modules = ctx.relevant_projects
        external_blocks = ctx.external_blocks
        retrieval_results = ctx.retrieval_results
        if ctx.external_fetch_errors:
            _push_event(thread_id, "warning", {
                "message": "Some external references could not be fetched: "
                           + "; ".join(ctx.external_fetch_errors),
            })
    except ExternalFetchError as exc:
        _push_event(thread_id, "error", {
            "message": str(exc),
            "recoverable": True,
            "type_detail": "url_fetch_failed",
        })
        return {**state, "error": str(exc), "external_blocks": external_blocks}
    except Exception as exc:
        summary = f"Context assembly failed: {exc}"

    context_chunks = [
        {"project": r.project, "section": r.section, "text": r.text}
        for r in retrieval_results[:6]
    ]
    _push_event(thread_id, "context_analyzed", {
        "capability_gaps": gaps,
        "summary": summary,
        "relevant_modules": modules,
        "context_chunks": context_chunks,
    })

    return {
        **state,
        "capability_gaps": gaps,
        "work_context_summary": summary,
        "relevant_modules": modules,
        "external_blocks": external_blocks,
    }


async def _execute_actions_node(state: WorkState) -> WorkState:
    """Propose MCP write-back actions for user to confirm."""
    thread_id = state["thread_id"]
    ticket_text = state["ticket_text"]
    project_name = state["project_name"]

    ticket_match = re.search(r'\b([A-Z][A-Z0-9]+-\d+)\b', ticket_text)
    ticket_id: Optional[str] = ticket_match.group(1) if ticket_match else None

    try:
        from ..mcp_sources import load_sources
        sources = load_sources(state["config_path"])
    except Exception:
        sources = []

    slug = re.sub(r'[^a-z0-9]+', '-', (ticket_id or project_name).lower()).strip('-')
    branch_name = f"{slug}-changes"

    session_summary = (
        f"**Memoria session summary**\n\n"
        f"Ticket: {ticket_id or 'N/A'} | Project: {project_name}\n\n"
        f"- Assembled context from Memory Banks\n"
        f"- Capability gaps identified: {', '.join(state.get('capability_gaps') or ['none']) or 'none'}"
    )

    jira_src    = next((s for s in sources if "jira"   in s.get("name", "").lower()), None)
    gh_src      = next((s for s in sources if "github" in s.get("name", "").lower()), None)
    gl_src      = next((s for s in sources if "gitlab" in s.get("name", "").lower()), None)
    vcs_src     = gh_src or gl_src
    is_gl       = gl_src is not None and gh_src is None

    jira_tools: set[str] = set()
    vcs_tools:  set[str] = set()
    cfg = state["config_path"]

    if jira_src:
        jira_tools, _ = await _discover_tools(jira_src["name"], cfg)
    if vcs_src:
        vcs_tools, _ = await _discover_tools(vcs_src["name"], cfg)

    proposed: list[dict] = []

    # ── Jira actions ──────────────────────────────────────────────────────────
    if jira_src and ticket_id:
        comment_tool = _match_tool(jira_tools, _TOOL_PATTERNS["jira_comment"])
        if comment_tool:
            proposed.append({
                "id": "jira_comment",
                "label": "Add Jira comment",
                "description": f"Post session summary as a comment on {ticket_id}",
                "detail": session_summary,
                "source": jira_src["name"],
                "tool": comment_tool,
                "args": {"issue_key": ticket_id, "body": session_summary},
            })

    # ── VCS (GitHub / GitLab) actions ─────────────────────────────────────────
    if vcs_src:
        branch_tool = _match_tool(vcs_tools, _TOOL_PATTERNS["vcs_branch"])
        if branch_tool:
            proposed.append({
                "id": "vcs_branch",
                "label": f"Create {'GitLab' if is_gl else 'GitHub'} branch",
                "description": f"Create branch '{branch_name}' in {project_name}",
                "detail": f"Branch: {branch_name}\nRepository: {project_name}",
                "source": vcs_src["name"],
                "tool": branch_tool,
                "args": {"branch": branch_name, "repository": project_name},
            })

        pr_patterns = _TOOL_PATTERNS["vcs_mr"] if is_gl else _TOOL_PATTERNS["vcs_pr"]
        pr_tool = _match_tool(vcs_tools, pr_patterns)
        if pr_tool:
            pr_body = f"## Summary\n\n{session_summary}\n\n🤖 Generated by Memoria"
            proposed.append({
                "id": "vcs_pr",
                "label": f"Open {'Merge Request' if is_gl else 'Pull Request'}",
                "description": f"{'MR' if is_gl else 'PR'}: {ticket_id or project_name}: changes",
                "detail": pr_body[:400],
                "source": vcs_src["name"],
                "tool": pr_tool,
                "args": {
                    "title": f"{ticket_id or project_name}: changes",
                    "body": pr_body,
                    "head": branch_name,
                    "repository": project_name,
                },
            })

    if not proposed:
        _write_session_log(state, actions_taken=[])
        return {**state, "actions_proposed": [], "actions_taken": []}

    _push_event(thread_id, "actions_available", {
        "actions": proposed,
        "message": "Review proposed write-back actions",
    })
    return {**state, "actions_proposed": proposed, "actions_taken": []}


# ─── Session log ──────────────────────────────────────────────────────────────

def _write_session_log(state: WorkState, actions_taken: list[str]) -> None:
    """Write a human-readable session log to books/_work_sessions/{ticket_id}.md."""
    import datetime
    try:
        books_dir = pathlib.Path(state.get("books_dir", ""))
        project = state.get("project_name", "unknown")
        thread_id = state.get("thread_id", "unknown")
        ticket_text = (state.get("ticket_text") or "").strip()
        ticket_id = thread_id[:12]

        m = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", ticket_text)
        if m:
            ticket_id = m.group(1)

        work_sessions_dir = books_dir / "_work_sessions"
        work_sessions_dir.mkdir(parents=True, exist_ok=True)

        gaps = state.get("capability_gaps", [])

        lines = [
            f"# Session — {ticket_id}",
            f"",
            f"**Project:** {project}  ",
            f"**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
            f"**Thread:** {thread_id}",
            f"",
            f"## Ticket",
            f"",
            (ticket_text[:500] + "…") if len(ticket_text) > 500 else ticket_text,
            f"",
            f"## Capability Gaps Detected",
            f"",
        ]
        if gaps:
            lines += [f"- `{g}`" for g in gaps]
        else:
            lines.append("None detected.")

        lines += ["", "## Actions Taken", ""]
        if actions_taken:
            lines += [f"- {a}" for a in actions_taken]
        else:
            lines.append("No write-back actions executed.")

        log_path = work_sessions_dir / f"{ticket_id}.md"
        log_path.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass  # best-effort


# ─── Graph construction ───────────────────────────────────────────────────────

def build_workflow():
    """Build and compile the LangGraph StateGraph."""
    try:
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.memory import MemorySaver
    except ImportError:
        raise ImportError(
            "LangGraph is required for the adaptive work agent. "
            "Install with: pip install langgraph"
        )

    graph = StateGraph(WorkState)
    graph.add_node("analyze_context", _analyze_context_node)
    graph.add_node("execute_actions", _execute_actions_node)

    graph.set_entry_point("analyze_context")
    graph.add_edge("analyze_context", "execute_actions")
    graph.add_edge("execute_actions", END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


_workflow = None

def get_workflow():
    global _workflow
    if _workflow is None:
        _workflow = build_workflow()
    return _workflow


# ─── Public API ───────────────────────────────────────────────────────────────

async def start_workflow(
    ticket_text: str,
    project_name: str,
    books_dir: str = "books",
    config_path: str = "config.yaml",
    thread_id: Optional[str] = None,
    skip_external_fetch: bool = False,
) -> tuple[str, asyncio.Queue]:
    """
    Launch the work agent for a ticket.
    Assembles context and proposes write-back actions.
    Returns (thread_id, event_queue) — callers read from event_queue for SSE.
    """
    state = _default_state(ticket_text, project_name, books_dir, config_path, thread_id, skip_external_fetch)
    tid = state["thread_id"]
    q = _get_event_queue(tid)
    workflow = get_workflow()
    config = {"configurable": {"thread_id": tid}}

    async def _run():
        try:
            async for _chunk in workflow.astream(state, config=config):
                pass
        except Exception as exc:
            _push_event(tid, "error", {"message": str(exc)})
            return
        try:
            snapshot = await workflow.aget_state(config)
            if not snapshot or not snapshot.next:
                _push_event(tid, "done", {"message": "Context ready"})
        except Exception:
            _push_event(tid, "done", {"message": "Context ready"})

    asyncio.create_task(_run())
    return tid, q


async def get_workflow_state(thread_id: str) -> Optional[WorkState]:
    """Return the current persisted state for a thread, or None."""
    workflow = get_workflow()
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = await workflow.aget_state(config)
        return snapshot.values if snapshot else None
    except Exception:
        return None


async def stream_work_chat(
    thread_id: str,
    message: str,
    history: list[dict],
) -> AsyncGenerator[str, None]:
    """
    Stream a chat response for a work item.
    Uses the assembled context (Memory Banks, gaps, summary) from analyze_context
    as the system prompt — the user drives all SDLC tasks from here.
    """
    state = await get_workflow_state(thread_id)

    # Build system context from assembled state
    system_parts = [
        "You are an expert software engineer AI assistant helping a developer work through a ticket.",
        "You have codebase context via Memory Banks assembled when this work item was opened.",
        "Respond to whatever the developer asks — code, tests, analysis, planning, PR descriptions, anything SDLC-related.",
        "When generating code or tests, use markdown code blocks with the appropriate language.",
    ]

    if state:
        ticket = (state.get("ticket_text") or "").strip()
        if ticket:
            system_parts.append(f"\n## Ticket / Task\n{ticket[:800]}")

        summary = state.get("work_context_summary") or ""
        if summary:
            system_parts.append(f"\n## Codebase Context (from Memory Banks)\n{summary[:2000]}")

        gaps = state.get("capability_gaps") or []
        if gaps:
            formatted = "\n".join(
                f"- {g.replace('needs_', '').replace('_', ' ').title()}" for g in gaps
            )
            system_parts.append(f"\n## Suggested areas (not mandatory — user decides):\n{formatted}")

    system_prompt = "\n\n".join(system_parts)

    # Build messages list
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for h in (history or []):
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    # Load config for model name
    config_path = (state or {}).get("config_path", "config.yaml")
    import yaml
    from dotenv import load_dotenv as _ld
    _ld(override=True)
    _ld(pathlib.Path.home() / ".memoria" / ".env", override=True)

    cfg: dict = {}
    try:
        p = pathlib.Path(config_path)
        if not p.exists():
            p = pathlib.Path.home() / ".memoria" / "config.yaml"
        if p.exists():
            cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        pass

    model = cfg.get("model", "gemini/gemini-2.0-flash")
    max_tokens = cfg.get("max_tokens_output") or 4096
    timeout = 600 if "ollama" in model.lower() else 120

    import litellm
    loop = asyncio.get_event_loop()

    yield ": keepalive\n\n"
    try:
        response = await loop.run_in_executor(
            None,
            lambda: litellm.completion(
                model=model,
                messages=messages,
                stream=True,
                timeout=timeout,
                max_tokens=max_tokens,
            ),
        )
        _STOP = object()
        _iter = iter(response)
        while True:
            chunk = await loop.run_in_executor(None, lambda: next(_iter, _STOP))
            if chunk is _STOP:
                break
            delta = chunk.choices[0].delta.content
            if delta:
                yield f"data: {json.dumps({'type': 'chunk', 'text': delta})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"


async def run_confirmed_actions(thread_id: str, approved_action_ids: list[str]) -> dict:
    """
    Execute the write-back actions the user selected in the confirmation UI.

    Delegates to the transactional executor which handles dependency ordering
    and best-effort rollback. Streams action_done + done events to the SSE queue.
    """
    q = _get_event_queue(thread_id)

    if not approved_action_ids:
        try:
            q.put_nowait({"type": "done", "message": "Actions skipped.", "actions_taken": []})
        except asyncio.QueueFull:
            pass
        return {"ok": True}

    state = await get_workflow_state(thread_id)
    proposed: list[dict] = (state or {}).get("actions_proposed", [])
    config_path: str = (state or {}).get("config_path", "config.yaml")
    actions_by_id = {a["id"]: a for a in proposed}

    selected = [actions_by_id[aid] for aid in approved_action_ids if aid in actions_by_id]

    from ..connectors.actions import execute_actions_transactional

    def _on_progress(result):
        try:
            q.put_nowait(result.to_sse_event())
        except asyncio.QueueFull:
            pass

    report = await execute_actions_transactional(
        actions=selected,
        config_path=config_path,
        rollback_on_failure=True,
        on_progress=_on_progress,
    )

    done_msg = f"{len(report.actions_taken)} action(s) completed."
    if report.errors:
        done_msg += f" {len(report.errors)} failed."
    if report.rolled_back:
        done_msg += f" Rolled back: {', '.join(report.rolled_back)}."

    done_event = {
        "type": "done",
        "message": done_msg,
        "actions_taken": report.actions_taken,
    }
    try:
        q.put_nowait(done_event)
    except asyncio.QueueFull:
        pass

    if state:
        _write_session_log(state, actions_taken=report.actions_taken)

    # Audit each executed action
    try:
        from ..audit import log_action as _audit
        for rec in report.records:
            if rec.status in ("ok", "failed"):
                _audit(
                    action=f"mcp_tool_call:{rec.action_id}",
                    target=state.get("project_name", "") if state else "",
                    detail=rec.result[:200],
                    ok=(rec.status == "ok"),
                    config_path=config_path,
                )
    except Exception:
        pass

    return {
        "ok": report.ok,
        "actions_taken": report.actions_taken,
        "errors": report.errors,
        "rolled_back": report.rolled_back,
    }
