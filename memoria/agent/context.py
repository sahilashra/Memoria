"""
Context Assembler — given a ticket, build a WorkContext with capability gaps.

From docs/design/CAPABILITY_DETECTION.md:
  1. Extract work-type signals from ticket text (labels, keywords, file paths)
  2. Check asset existence via MCP (Figma linked? branch exists? test files exist?)
  3. Map relevant sub-Memory Banks via hybrid retrieval
  4. Traverse file graph to include connected modules
  5. Build capability gap list: needs_implementation, needs_tests, needs_design

Works with ticket text only — no MCP required for the first pass.
MCP data enriches the context if available (live Jira, GitHub, Figma checks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
import asyncio


# ─── External reference types ────────────────────────────────────────────────

class ExternalFetchError(Exception):
    """Raised when external URL fetch was required but all attempts failed."""


@dataclass
class ExternalReference:
    ref_type: str      # "github_url" | "jira_id"
    raw: str           # original matched string
    owner: str = ""
    repo: str = ""
    subpath: str = ""  # e.g. "/blob/main/AGENTS.md"
    ticket_id: str = ""


_GITHUB_URL_RE = re.compile(
    r'https?://(?:www\.)?github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)'
    r'(/(?:blob|tree|raw|pulls|issues)/[^\s"\'<>]*)?'
)
_TICKET_ID_RE = re.compile(r'\b([A-Z]{2,10}-\d+)\b')


def extract_external_references(text: str) -> list[ExternalReference]:
    """Extract structured external references (GitHub URLs, ticket IDs) from text."""
    refs: list[ExternalReference] = []
    seen: set[str] = set()

    for m in _GITHUB_URL_RE.finditer(text):
        url = m.group(0)
        if url not in seen:
            seen.add(url)
            refs.append(ExternalReference(
                ref_type="github_url",
                raw=url,
                owner=m.group(1),
                repo=m.group(2),
                subpath=m.group(3) or "",
            ))

    for m in _TICKET_ID_RE.finditer(text):
        tid = m.group(1)
        if tid not in seen:
            seen.add(tid)
            refs.append(ExternalReference(ref_type="jira_id", raw=tid, ticket_id=tid))

    return refs


def _format_github_issue(raw: str, fallback_number: int) -> str:
    """Parse a GitHub issue MCP response and return readable ticket text."""
    import json as _json
    try:
        data = _json.loads(raw)
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            return raw
        number = data.get("number", fallback_number)
        title = data.get("title", "")
        body = data.get("body") or ""
        state = data.get("state", "")
        labels = data.get("labels", [])
        label_names = [
            l.get("name", str(l)) if isinstance(l, dict) else str(l)
            for l in labels
        ]
        lines = [f"# Issue #{number}: {title}", ""]
        if state:
            lines.append(f"**State:** {state}")
        if label_names:
            lines.append(f"**Labels:** {', '.join(label_names)}")
        if state or label_names:
            lines.append("")
        if body:
            lines.append(body)
        return "\n".join(lines)
    except Exception:
        return raw


async def _fetch_github_ref(
    ref: ExternalReference,
    config_path: str,
    timeout: float = 8.0,
    retries: int = 1,
) -> tuple[str, str | None, str]:
    """
    Fetch content for a github_url reference via MCP.
    - /issues/N  → get_issue (formatted as readable ticket text)
    - /blob/…    → get_file_contents (derives file path)
    - bare repo  → get_file_contents for README.md
    Returns (label, content, error_reason).
    error_reason is "" on success; one of timeout|auth_required|access_denied|not_found|unknown on failure.
    """
    from ..mcp_sources import call_tool_async

    # Detect issue URL: subpath like /issues/5 or /issues/5/...
    issue_match = re.match(r'^/issues/(\d+)', ref.subpath or '')

    if issue_match:
        issue_number = int(issue_match.group(1))
        label = f"GitHub Issue — {ref.owner}/{ref.repo}#{issue_number}"
        tool = "get_issue"
        args = {"owner": ref.owner, "repo": ref.repo, "issue_number": issue_number}
    else:
        label = f"GitHub — {ref.owner}/{ref.repo}{ref.subpath or ''}"
        tool = "get_file_contents"
        if ref.subpath and '/' in ref.subpath:
            parts = ref.subpath.strip('/').split('/', 2)
            file_path = parts[2] if len(parts) >= 3 else "README.md"
        else:
            file_path = "README.md"
        args = {"owner": ref.owner, "repo": ref.repo, "path": file_path}

    for attempt in range(retries + 1):
        try:
            result = await asyncio.wait_for(
                call_tool_async(
                    server="github", tool=tool,
                    args=args, config_path=config_path,
                ),
                timeout=timeout,
            )
            if issue_match and result:
                result = _format_github_issue(result, issue_number)
            return label, result, ""
        except asyncio.TimeoutError:
            if attempt < retries:
                await asyncio.sleep(0.5)
            else:
                return label, None, "timeout"
        except Exception as exc:
            err = str(exc).lower()
            if any(x in err for x in ("401", "unauthorized", "authentication", "bad credentials")):
                return label, None, "auth_required"
            if any(x in err for x in ("403", "forbidden", "rate limit")):
                return label, None, "access_denied"
            if any(x in err for x in ("404", "not found", "does not exist", "no such file")):
                return label, None, "not_found"
            return label, None, "unknown"

    return label, None, "unknown"


_FETCH_ERROR_MSGS: dict[str, str] = {
    "timeout":       "MCP server timed out — try again or check server status",
    "auth_required": "authentication failed — check your GitHub token in MCP settings",
    "access_denied": "access denied — repository may be private or token lacks repo scope",
    "not_found":     "file not found — try linking directly to a file (e.g. /blob/main/README.md)",
    "unknown":       "unexpected error — check MCP server logs",
}


# ─── Signal sets (from CAPABILITY_DETECTION.md) ───────────────────────────────

IMPLEMENTATION_SIGNALS = frozenset({
    "implement", "build", "create", "add", "develop", "write", "code",
    "feature", "endpoint", "function", "component", "api", "integration",
    "scaffold", "stub", "boilerplate",
})

FRONTEND_SIGNALS = frozenset({
    "ui", "ux", "frontend", "front-end", "component", "page", "screen",
    "button", "form", "modal", "layout", "style", "css", "react", "vue",
    "svelte", "design", "figma", "wireframe", "mockup", "prototype",
    "responsive", "accessibility", "a11y",
})

TEST_SIGNALS = frozenset({
    "test", "tests", "testing", "spec", "coverage", "unit test",
    "integration test", "e2e", "end-to-end", "regression", "assert",
    "mock", "stub", "fixture", "jest", "pytest", "mocha", "vitest",
})

BACKEND_SIGNALS = frozenset({
    "api", "endpoint", "route", "controller", "service", "database",
    "migration", "query", "schema", "model", "backend", "server",
    "auth", "authentication", "authorization", "cron", "worker",
    "queue", "event", "webhook", "microservice",
})


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens, stripping punctuation."""
    return set(re.findall(r"[a-z][a-z0-9\-_]*", text.lower()))


def _signal_score(tokens: set[str], signals: frozenset) -> int:
    return len(tokens & signals)


# ─── Asset inventory (from MCP checks) ────────────────────────────────────────

@dataclass
class AssetInventory:
    figma_linked: bool = False      # Figma frame attached to ticket
    branch_exists: bool = False     # GitHub branch for this ticket exists
    pr_open: bool = False           # PR already open
    test_files_exist: bool = False  # test files already exist for affected module
    mcp_available: bool = False     # whether any MCP check succeeded


# ─── Capability gap detection ─────────────────────────────────────────────────

@dataclass
class CapabilityGaps:
    needs_implementation: bool = False
    needs_tests: bool = False
    needs_design: bool = False

    def as_list(self) -> list[str]:
        out = []
        if self.needs_implementation:
            out.append("needs_implementation")
        if self.needs_tests:
            out.append("needs_tests")
        if self.needs_design:
            out.append("needs_design")
        return out

    def __bool__(self) -> bool:
        return any([self.needs_implementation, self.needs_tests, self.needs_design])


def detect_capability_gaps(
    ticket_text: str,
    assets: AssetInventory,
    threshold: int = 1,
) -> CapabilityGaps:
    """
    Determine which capability gaps exist for a given ticket.

    Parameters
    ----------
    ticket_text : str
        Full ticket description including title, body, labels.
    assets : AssetInventory
        Results from MCP asset-existence checks (or defaults if no MCP).
    threshold : int
        Minimum signal score to trigger a gap (default 1 — any match).
    """
    tokens = _tokenize(ticket_text)
    gaps = CapabilityGaps()

    impl_score = _signal_score(tokens, IMPLEMENTATION_SIGNALS)
    test_score = _signal_score(tokens, TEST_SIGNALS)
    fe_score = _signal_score(tokens, FRONTEND_SIGNALS)
    be_score = _signal_score(tokens, BACKEND_SIGNALS)

    # ── Implementation gap ────────────────────────────────────────────────────
    # Needs implementation if ticket implies building something AND
    # no PR is already open for it.
    if impl_score >= threshold or (fe_score >= threshold or be_score >= threshold):
        if not assets.pr_open:
            gaps.needs_implementation = True

    # If ticket text is empty / no signals at all, default to needs_implementation
    if not tokens or (impl_score + test_score + fe_score + be_score) == 0:
        gaps.needs_implementation = True

    # ── Test gap ──────────────────────────────────────────────────────────────
    # Needs tests if ticket doesn't purely say "write tests" AND
    # no test files already exist for the module.
    # "Needs tests" is added alongside implementation, not instead of it.
    if not assets.test_files_exist and gaps.needs_implementation:
        gaps.needs_tests = True
    elif test_score >= threshold and not assets.test_files_exist:
        gaps.needs_tests = True

    # ── Design gap ────────────────────────────────────────────────────────────
    # Needs design if:
    # - Frontend signals present AND no Figma frame linked
    # Phase 2 only: detected by design_skeleton.py; Phase 1 flag only surfaced in UI
    if fe_score >= threshold and not assets.figma_linked:
        gaps.needs_design = True

    return gaps


# ─── Work context ─────────────────────────────────────────────────────────────

@dataclass
class WorkContext:
    ticket_text: str
    project_name: str
    capability_gaps: CapabilityGaps
    retrieval_results: list = field(default_factory=list)   # RetrievalResult list
    assets: AssetInventory = field(default_factory=AssetInventory)
    mcp_enrichment: dict = field(default_factory=dict)      # raw MCP data if available

    external_blocks: list = field(default_factory=list)        # fetched external content [{label, content, owner, repo}]
    external_fetch_errors: list = field(default_factory=list)   # non-fatal fetch error messages

    @property
    def gap_list(self) -> list[str]:
        return self.capability_gaps.as_list()

    @property
    def relevant_projects(self) -> list[str]:
        return list(dict.fromkeys(r.project for r in self.retrieval_results))

    def summary(self) -> str:
        gaps = self.gap_list or ["none detected"]
        projects = self.relevant_projects or ["none"]
        return (
            f"Gaps: {', '.join(gaps)} | "
            f"Relevant modules: {', '.join(projects[:5])}"
        )


# ─── MCP asset check ──────────────────────────────────────────────────────────

async def _check_assets_via_mcp(
    ticket_text: str,
    project_name: str,
    mcp_config: Optional[dict],
    config_path: str,
) -> tuple[AssetInventory, dict]:
    """
    Optionally enrich AssetInventory by querying connected MCP sources.
    Returns (AssetInventory, raw_mcp_data).
    Silently degrades — never raises.
    """
    from ..mcp_sources import call_tool_async

    assets = AssetInventory()
    enrichment: dict = {}

    if not mcp_config:
        return assets, enrichment

    # Extract a potential ticket ID from text (e.g. PROJ-123, #456, GH-789)
    ticket_id_match = re.search(r"\b([A-Z]{2,10}-\d+|#\d+)\b", ticket_text)
    ticket_id = ticket_id_match.group(1) if ticket_id_match else None

    # ── GitHub: check for existing branch ────────────────────────────────────
    if "github" in mcp_config:
        try:
            branch_query = f"{ticket_id or project_name}".lower().replace(" ", "-")
            result = await call_tool_async(
                server="github",
                tool="list_branches",
                args={"query": branch_query},
                config_path=config_path,
            )
            if result and branch_query in result.lower():
                assets.branch_exists = True
            enrichment["github_branches"] = result
            assets.mcp_available = True
        except Exception:
            pass

    # ── Figma: check for linked design frames ─────────────────────────────────
    if "figma" in mcp_config:
        try:
            result = await call_tool_async(
                server="figma",
                tool="get_file",
                args={"query": ticket_id or ticket_text[:100]},
                config_path=config_path,
            )
            if result and len(result.strip()) > 50:
                assets.figma_linked = True
            enrichment["figma"] = result
            assets.mcp_available = True
        except Exception:
            pass

    return assets, enrichment


# ─── Public API ───────────────────────────────────────────────────────────────

async def assemble_context(
    ticket_text: str,
    project_name: str,
    books_dir: str = "books",
    config_path: str = "config.yaml",
    mcp_config: Optional[dict] = None,
    top_k: int = 6,
    skip_external_fetch: bool = False,
) -> WorkContext:
    """
    Assemble a WorkContext for a given ticket.

    Context precedence (highest → lowest):
      1. Fetched external content (GitHub URLs found in ticket text)
      2. Memory Bank retrieval results
      3. MCP asset enrichment

    Raises ExternalFetchError if a GitHub URL was detected, GitHub MCP is configured,
    but ALL fetch attempts failed — prevents silent fallback to wrong-project context.
    Pass skip_external_fetch=True to bypass URL fetching (e.g. after a failed retry).

    Degrades gracefully for all other failures.
    """
    from .retrieval import retrieve_for_ticket

    # ── Step 0: extract and fetch external references ─────────────────────────
    refs = extract_external_references(ticket_text)
    external_blocks: list = []
    external_fetch_errors: list = []

    github_refs = [r for r in refs if r.ref_type == "github_url"]
    if github_refs and not skip_external_fetch:
        github_configured = False
        try:
            from ..mcp_sources import load_sources
            sources = load_sources(config_path)
            github_configured = any(
                s.get("name", "").lower() in ("github", "github-mcp")
                for s in sources
            )
        except Exception:
            pass

        if github_configured:
            for ref in github_refs[:3]:  # cap at 3 external fetches
                label, content, err_reason = await _fetch_github_ref(ref, config_path)
                if content:
                    external_blocks.append({
                        "label": label,
                        "content": content,
                        "owner": ref.owner,
                        "repo": ref.repo,
                    })
                else:
                    human_reason = _FETCH_ERROR_MSGS.get(err_reason, _FETCH_ERROR_MSGS["unknown"])
                    external_fetch_errors.append(
                        f"{ref.raw} — {human_reason}"
                    )
            # Block if every fetch failed (avoid silent wrong-context generation)
            if github_refs and not external_blocks:
                raise ExternalFetchError(
                    "GitHub URL(s) detected but all fetches failed: "
                    + "; ".join(external_fetch_errors)
                )
        else:
            for ref in github_refs:
                external_fetch_errors.append(
                    f"GitHub URL detected ({ref.owner}/{ref.repo}) but no GitHub MCP source configured"
                )

    # ── Step 1: MCP asset checks (optional, enriches gaps) ───────────────────
    assets, enrichment = await _check_assets_via_mcp(
        ticket_text, project_name, mcp_config, config_path
    )

    # ── Step 2: detect capability gaps ───────────────────────────────────────
    gaps = detect_capability_gaps(ticket_text, assets)

    # ── Step 3: retrieve relevant Memory Bank chunks ──────────────────────────
    try:
        results = retrieve_for_ticket(
            ticket_text,
            project_name,
            books_dir=books_dir,
            top_k=top_k,
        )
    except Exception:
        results = []

    return WorkContext(
        ticket_text=ticket_text,
        project_name=project_name,
        capability_gaps=gaps,
        retrieval_results=results,
        assets=assets,
        mcp_enrichment=enrichment,
        external_blocks=external_blocks,
        external_fetch_errors=external_fetch_errors,
    )
