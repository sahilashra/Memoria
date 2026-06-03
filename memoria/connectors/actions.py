"""
Transactional MCP write-back action executor.

Handles dependency ordering, per-action result tracking, and best-effort
rollback so callers get a consistent partial-state report instead of silent
half-applied changes.

Usage::

    from memoria.connectors.actions import execute_actions_transactional

    results = await execute_actions_transactional(
        actions=proposed_actions,   # list[dict] from agent graph
        config_path="config.yaml",
        rollback_on_failure=True,
        on_progress=lambda r: queue.put_nowait(r.to_sse_event()),
    )
    # results.ok, results.actions_taken, results.errors, results.records
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ─── Dependency graph ─────────────────────────────────────────────────────────
# Maps action id → list of action ids that must succeed first.
# Matches the logical ordering imposed by the VCS (branch before PR/MR).

_DEPENDENCIES: dict[str, list[str]] = {
    "vcs_pr": ["vcs_branch"],
    "vcs_mr": ["vcs_branch"],
}

# ─── Rollback tool patterns ───────────────────────────────────────────────────
# For each action type, what tool names (in priority order) can undo it.
# Looked up in the same MCP source that executed the action.

_ROLLBACK_PATTERNS: dict[str, list[str]] = {
    "vcs_branch":   ["delete_branch", "delete_ref", "remove_branch"],
    "vcs_pr":       ["close_pull_request", "close_pr", "update_pull_request"],
    "vcs_mr":       ["close_merge_request", "close_mr", "update_merge_request"],
    "jira_comment": ["delete_comment", "remove_comment"],
}

# ─── Result model ─────────────────────────────────────────────────────────────

@dataclass
class ActionResult:
    action_id: str
    label: str
    status: str          # "ok" | "failed" | "skipped" | "rolled_back"
    result: str = ""     # raw response or error message (truncated)
    rolled_back: bool = False

    def to_sse_event(self) -> dict:
        return {
            "type": "action_done",
            "action_id": self.action_id,
            "label": self.label,
            "result": self.result,
            "ok": self.status == "ok",
            "rolled_back": self.rolled_back,
        }


@dataclass
class ExecutionReport:
    ok: bool
    records: list[ActionResult] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rolled_back: list[str] = field(default_factory=list)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _topo_sort(action_ids: list[str]) -> list[str]:
    """
    Return action_ids sorted so that each action's dependencies come first.
    Unknown dependencies (not in action_ids) are ignored — they were not
    selected by the user.
    """
    present = set(action_ids)
    visited: set[str] = set()
    order: list[str] = []

    def visit(aid: str):
        if aid in visited:
            return
        visited.add(aid)
        for dep in _DEPENDENCIES.get(aid, []):
            if dep in present:
                visit(dep)
        order.append(aid)

    for aid in action_ids:
        visit(aid)
    return order


async def _find_rollback_tool(
    source: str,
    action_id: str,
    config_path: str,
) -> Optional[str]:
    """Return the name of a rollback tool available on `source`, or None."""
    patterns = _ROLLBACK_PATTERNS.get(action_id, [])
    if not patterns:
        return None
    try:
        from ..mcp_sources import list_tools_async
        tools = await asyncio.wait_for(
            list_tools_async(server=source, config_path=config_path),
            timeout=6.0,
        )
        available = {t["name"].lower(): t["name"] for t in tools}
        for p in patterns:
            if p.lower() in available:
                return available[p.lower()]
        for p in patterns:
            for name_l, name_orig in available.items():
                if p.lower() in name_l:
                    return name_orig
    except Exception:
        pass
    return None


def _rollback_args(action: dict, rollback_tool: str) -> dict:
    """Build minimal args for the rollback call from the original action args."""
    original_args = action.get("args", {})
    aid = action.get("id", "")

    if aid == "vcs_branch":
        return {
            k: original_args[k]
            for k in ("branch", "repository")
            if k in original_args
        }
    if aid in ("vcs_pr", "vcs_mr"):
        # close_pull_request typically needs a PR number we don't have;
        # best-effort: pass head/repository if that's what the rollback tool accepts
        return {k: original_args[k] for k in ("repository",) if k in original_args}
    if aid == "jira_comment":
        # delete_comment needs comment_id which we don't have from create response
        return {}
    return {}


# ─── Core executor ────────────────────────────────────────────────────────────

async def execute_actions_transactional(
    actions: list[dict],
    config_path: str,
    rollback_on_failure: bool = True,
    on_progress: Optional[Callable[[ActionResult], Any]] = None,
) -> ExecutionReport:
    """
    Execute write-back actions with dependency ordering and optional rollback.

    Parameters
    ----------
    actions:
        List of action dicts as produced by agent/graph.py ``_execute_actions_node``.
        Each must have: id, label, source, tool, args.
    config_path:
        Path to config.yaml for MCP source resolution.
    rollback_on_failure:
        When True, attempt to undo already-completed actions if a later action
        fails. Rollback is best-effort; its own failure is logged but ignored.
    on_progress:
        Optional async/sync callback invoked after each action completes.
        Receives an ``ActionResult`` — use ``.to_sse_event()`` to get an SSE dict.
    """
    from ..mcp_sources import call_tool_async as _cta

    actions_by_id = {a["id"]: a for a in actions}
    ordered_ids = _topo_sort(list(actions_by_id.keys()))

    succeeded: list[str] = []   # action ids that completed ok
    records: list[ActionResult] = []
    failed = False

    async def _notify(result: ActionResult):
        if on_progress is None:
            return
        ret = on_progress(result)
        if asyncio.iscoroutine(ret):
            await ret

    for aid in ordered_ids:
        action = actions_by_id[aid]

        # Skip if any required dependency failed
        unmet = [dep for dep in _DEPENDENCIES.get(aid, []) if dep in actions_by_id and dep not in succeeded]
        if unmet:
            rec = ActionResult(
                action_id=aid,
                label=action["label"],
                status="skipped",
                result=f"Skipped: dependency '{unmet[0]}' did not succeed.",
            )
            records.append(rec)
            await _notify(rec)
            failed = True
            continue

        try:
            raw = await _cta(
                server=action["source"],
                tool=action["tool"],
                args=action["args"],
                config_path=config_path,
            )
            rec = ActionResult(
                action_id=aid,
                label=action["label"],
                status="ok",
                result=(raw or "").strip()[:300],
            )
            succeeded.append(aid)
        except Exception as exc:
            rec = ActionResult(
                action_id=aid,
                label=action["label"],
                status="failed",
                result=str(exc)[:300],
            )
            failed = True

        records.append(rec)
        await _notify(rec)

    # ── Rollback ───────────────────────────────────────────────────────────────
    rolled_back: list[str] = []
    if failed and rollback_on_failure and succeeded:
        for aid in reversed(succeeded):
            action = actions_by_id[aid]
            rb_tool = await _find_rollback_tool(action["source"], aid, config_path)
            if not rb_tool:
                continue
            rb_args = _rollback_args(action, rb_tool)
            try:
                await _cta(
                    server=action["source"],
                    tool=rb_tool,
                    args=rb_args,
                    config_path=config_path,
                )
                rolled_back.append(action["label"])
                # Update the original record to reflect rollback
                for r in records:
                    if r.action_id == aid:
                        r.status = "rolled_back"
                        r.rolled_back = True
                        # Re-notify with updated status
                        await _notify(r)
                        break
            except Exception:
                pass  # rollback failure is best-effort

    actions_taken = [r.label for r in records if r.status == "ok"]
    errors = [f"{r.label}: {r.result}" for r in records if r.status == "failed"]

    return ExecutionReport(
        ok=not failed,
        records=records,
        actions_taken=actions_taken,
        errors=errors,
        rolled_back=rolled_back,
    )
