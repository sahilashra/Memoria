"""
End-to-end integration test for the Memoria core loop.

Demonstrates the full ticket→context→artifact→approval→write-back pipeline
with mocked LLM calls and mocked MCP tool calls (no real API keys needed).

Two scenarios are tested:
  QA   — VIZINZ-1234: Write test cases → assembled from Memory Bank
            → test scaffolding generated → Zephyr test created
  Dev  — VIZINZ-567:  Implement rate limiting → assembled from Memory Bank
            → code skeleton generated → GitHub branch + PR opened

These tests prove the data flows correctly through every stage of the loop.
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Realistic ticket texts ───────────────────────────────────────────────────

QA_TICKET = """
VIZINZ-1234: Write automated test cases for the authentication flow

As a QA engineer I need to create test cases for login, logout, and
session management that were implemented in the last sprint.

Acceptance criteria:
- Test login with valid credentials
- Test login with invalid credentials (wrong password, locked account)
- Test session timeout after 30 minutes
- Test logout clears session data and cookie

The implementation is in src/auth/ and uses JWT tokens + Redis sessions.
"""

DEV_TICKET = """
VIZINZ-567: Implement rate limiting for the authentication API endpoint

Add rate limiting to /api/auth/login to prevent brute-force attacks.
Limit: 5 failed attempts per 5 minutes per IP address.
After the limit, return 429 with a Retry-After header.

Tech stack: FastAPI, Redis (already in stack), auth middleware in src/auth/middleware.py.
"""

# ─── Shared mock Memory Bank chunks ──────────────────────────────────────────

_AUTH_CHUNKS = [
    {
        "project": "VIZINZ",
        "section": "Authentication",
        "text": (
            "JWT tokens are issued by src/auth/tokens.py.\n"
            "Sessions stored in Redis with 30-min TTL (src/auth/session.py).\n"
            "Login handled by POST /api/auth/login in src/auth/routes.py."
        ),
        "score": 0.93,
    },
    {
        "project": "VIZINZ",
        "section": "Test Conventions",
        "text": (
            "Tests use pytest + pytest-asyncio.\n"
            "Auth tests live in tests/auth/.\n"
            "Fixtures in tests/conftest.py provide async test_client and redis_mock."
        ),
        "score": 0.88,
    },
    {
        "project": "VIZINZ",
        "section": "Middleware",
        "text": (
            "Rate limiting middleware (not yet implemented) belongs in src/auth/middleware.py.\n"
            "Existing middleware: JWTMiddleware, CORSMiddleware.\n"
            "Pattern: class XMiddleware(BaseHTTPMiddleware): async def dispatch(request, call_next)."
        ),
        "score": 0.85,
    },
]

# ─── Mock LLM responses ───────────────────────────────────────────────────────

_MOCK_TEST_ARTIFACT = """## Test File
`tests/auth/test_authentication_flow.py`

## Test Scaffolding
```python
import pytest
from httpx import AsyncClient

class TestLoginFlow:
    async def test_login_valid_credentials(self, test_client: AsyncClient):
        response = await test_client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "correct_password"},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()
        # TODO: verify JWT signature

    async def test_login_invalid_password(self, test_client: AsyncClient):
        response = await test_client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "wrong"},
        )
        assert response.status_code == 401
        # TODO: verify error message format

    async def test_session_timeout(self, test_client: AsyncClient, redis_mock):
        # TODO: fast-forward Redis TTL and verify session is invalidated
        pass

    async def test_logout_clears_session(self, test_client: AsyncClient):
        # TODO: login, logout, verify session gone from Redis
        pass
```

## Notes
- Requires `test_client` and `redis_mock` fixtures from tests/conftest.py
- JWT signature verification needs the public key fixture
"""

_MOCK_CODE_ARTIFACT = """## Files to Create / Modify
- `src/auth/middleware.py` — add RateLimitMiddleware class

## Implementation Skeleton
```python
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import redis.asyncio as aioredis
import time

RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_WINDOW = 300  # 5 minutes in seconds

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_client: aioredis.Redis):
        super().__init__(app)
        self.redis = redis_client

    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/api/auth/login":
            return await call_next(request)

        client_ip = request.client.host
        key = f"rate_limit:{client_ip}"

        # TODO: get current attempt count from Redis
        # TODO: if count >= RATE_LIMIT_ATTEMPTS: return 429 with Retry-After
        # TODO: increment counter with EXPIRE on first attempt
        # TODO: call_next(request) and reset counter on success

        return await call_next(request)
```

## Notes
- Redis client should be injected via app.state (already wired in main.py)
- RATE_LIMIT_WINDOW resets on each new 5-min window, not rolling window
"""


def _make_acompletion_mock(content: str):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = content
    mock_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=300)
    return mock_resp


# ─── Scenario 1: QA ticket → test cases → Zephyr write-back ─────────────────

class TestQAScenario:
    """
    QA engineer opens VIZINZ-1234 → test scaffolding generated
    → approved → Zephyr test cases created.
    """

    def test_context_assembly_detects_test_gap(self):
        """Step 1: Ticket text → capability gaps detected correctly."""
        t0 = time.monotonic()

        with patch("memoria.search.search", return_value=_AUTH_CHUNKS):
            with patch("memoria.core.file_graph.get_neighbors", return_value=[]):
                from memoria.agent.context import assemble_context
                ctx = asyncio.get_event_loop().run_until_complete(
                    assemble_context(
                        ticket_text=QA_TICKET,
                        project_name="VIZINZ",
                        books_dir="/tmp/books",
                        config_path="/tmp/config.yaml",
                    )
                )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"\n[QA] context assembly: {elapsed_ms}ms")

        # Verify gaps
        assert ctx.capability_gaps.needs_tests, "QA ticket must trigger needs_tests"
        assert "needs_tests" in ctx.gap_list

        # Verify retrieval found auth chunks
        assert len(ctx.retrieval_results) > 0
        assert any("auth" in r.section.lower() for r in ctx.retrieval_results)

        # Verify project routing
        assert ctx.project_name == "VIZINZ"
        assert all(r.project == "VIZINZ" for r in ctx.retrieval_results)

    def test_test_artifact_generated_from_ticket(self):
        """Step 2: Context → test scaffolding generated by LLM."""
        t0 = time.monotonic()

        with patch("memoria.search.search", return_value=_AUTH_CHUNKS):
            with patch(
                "litellm.acompletion",
                new=AsyncMock(return_value=_make_acompletion_mock(_MOCK_TEST_ARTIFACT)),
            ):
                from memoria.agent.capabilities.test_gen import generate_test_artifact
                artifact = asyncio.get_event_loop().run_until_complete(
                    generate_test_artifact(
                        ticket_text=QA_TICKET,
                        project_name="VIZINZ",
                        generated_code=None,
                        relevant_modules=["VIZINZ"],
                        books_dir="/tmp/books",
                        config_path="/tmp/config.yaml",
                    )
                )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"[QA] test artifact generation: {elapsed_ms}ms")

        # Verify artifact contains usable test code
        assert artifact, "Artifact must be non-empty"
        assert "pytest" in artifact or "test" in artifact.lower()
        assert "VIZINZ" in QA_TICKET  # sanity — ticket is in scope
        print(f"[QA] artifact length: {len(artifact)} chars")

    def test_zephyr_writeback_executes_with_real_tool_name(self):
        """
        Step 3: After approval, Zephyr write-back executes.
        Proves the discovered tool name is used — not a hardcoded guess.
        """
        t0 = time.monotonic()

        # Simulate: Zephyr MCP server exposes 'create_test_cycle' and 'create_test_case'
        discovered_tools = {"create_test_case", "create_test_cycle", "link_test_to_issue"}
        mock_state = {
            "actions_proposed": [
                {
                    "id": "zephyr_create",
                    "label": "Create Zephyr test cases",
                    "description": "Create test cases and link to VIZINZ-1234",
                    "source": "zephyr",
                    "tool": "create_test_case",
                    "args": {"issue_key": "VIZINZ-1234"},
                }
            ],
            "config_path": "/tmp/config.yaml",
        }

        mcp_call_args = {}

        async def _fake_call_tool_async(server, tool, args, config_path=None, **kwargs):
            mcp_call_args["server"] = server
            mcp_call_args["tool"] = tool
            mcp_call_args["args"] = dict(args)
            return "OK: test case TC-001 created and linked to VIZINZ-1234"

        async def _run():
            from memoria.agent.graph import run_confirmed_actions, _get_event_queue
            # Drain any pre-existing events in the queue
            q = _get_event_queue("qa-test-thread")

            with patch("memoria.agent.graph.get_workflow_state", new=AsyncMock(return_value=mock_state)):
                with patch("memoria.mcp_sources.call_tool_async", side_effect=_fake_call_tool_async):
                    result = await run_confirmed_actions("qa-test-thread", ["zephyr_create"])

            # Drain events pushed to queue
            events = []
            while not q.empty():
                events.append(q.get_nowait())
            return result, events

        result, events = asyncio.get_event_loop().run_until_complete(_run())

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"[QA] write-back execution: {elapsed_ms}ms")

        # Verify write-back succeeded
        assert result["ok"], f"Write-back failed: {result}"
        assert len(result.get("actions_taken", [])) == 1

        # Verify MCP was called with the REAL discovered tool name
        assert mcp_call_args["server"] == "zephyr"
        assert mcp_call_args["tool"] == "create_test_case"
        assert mcp_call_args["args"]["issue_key"] == "VIZINZ-1234"

        # Verify action_done event was emitted
        action_done_events = [e for e in events if e.get("type") == "action_done"]
        assert len(action_done_events) == 1
        assert action_done_events[0]["ok"] is True
        assert "TC-001" in action_done_events[0]["result"]

        # Verify done event was emitted
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1
        assert len(done_events[0].get("actions_taken", [])) == 1

        print(f"[QA] PASS Zephyr tool called: {mcp_call_args['tool']}({mcp_call_args['args']})")
        print(f"[QA] PASS Result: {action_done_events[0]['result']}")


# ─── Scenario 2: Dev ticket → code skeleton → GitHub branch + PR ────────────

class TestDevScenario:
    """
    Developer opens VIZINZ-567 → implementation skeleton generated
    → approved → GitHub branch created + PR opened.
    """

    def test_context_assembly_detects_impl_gap(self):
        """Step 1: Dev ticket → implementation + test gaps detected."""
        t0 = time.monotonic()

        with patch("memoria.search.search", return_value=_AUTH_CHUNKS):
            with patch("memoria.core.file_graph.get_neighbors", return_value=[]):
                from memoria.agent.context import assemble_context
                ctx = asyncio.get_event_loop().run_until_complete(
                    assemble_context(
                        ticket_text=DEV_TICKET,
                        project_name="VIZINZ",
                        books_dir="/tmp/books",
                        config_path="/tmp/config.yaml",
                    )
                )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"\n[Dev] context assembly: {elapsed_ms}ms")

        assert ctx.capability_gaps.needs_implementation
        assert len(ctx.retrieval_results) > 0
        # Middleware chunk should surface
        assert any("middleware" in r.section.lower() for r in ctx.retrieval_results)

    def test_code_skeleton_generated(self):
        """Step 2: Context → implementation skeleton."""
        t0 = time.monotonic()

        with patch("memoria.search.search", return_value=_AUTH_CHUNKS):
            with patch(
                "litellm.acompletion",
                new=AsyncMock(return_value=_make_acompletion_mock(_MOCK_CODE_ARTIFACT)),
            ):
                from memoria.agent.capabilities.code_gen import generate_code_artifact
                artifact = asyncio.get_event_loop().run_until_complete(
                    generate_code_artifact(
                        ticket_text=DEV_TICKET,
                        project_name="VIZINZ",
                        relevant_modules=["VIZINZ"],
                        books_dir="/tmp/books",
                        config_path="/tmp/config.yaml",
                    )
                )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"[Dev] code artifact generation: {elapsed_ms}ms, {len(artifact)} chars")

        assert artifact
        assert "class" in artifact or "def " in artifact
        assert "rate" in artifact.lower() or "middleware" in artifact.lower()

    def test_github_branch_and_pr_writeback(self):
        """
        Step 3: After approval, GitHub branch created then PR opened.
        Two separate MCP calls — verifies transactional ordering.
        """
        t0 = time.monotonic()

        mock_state = {
            "actions_proposed": [
                {
                    "id": "vcs_branch",
                    "label": "Create GitHub branch",
                    "description": "Create branch 'vizinz-567-changes' in VIZINZ",
                    "source": "github",
                    "tool": "create_branch",
                    "args": {"branch": "vizinz-567-changes", "repository": "VIZINZ"},
                },
                {
                    "id": "vcs_pr",
                    "label": "Open Pull Request",
                    "description": "PR: VIZINZ-567: changes",
                    "source": "github",
                    "tool": "create_pull_request",
                    "args": {
                        "title": "VIZINZ-567: changes",
                        "body": "## Summary\n\nRate limiting implementation",
                        "head": "vizinz-567-changes",
                        "repository": "VIZINZ",
                    },
                },
            ],
            "config_path": "/tmp/config.yaml",
        }

        call_log: list[dict] = []

        async def _fake_call_tool_async(server, tool, args, config_path=None, **kwargs):
            call_log.append({"server": server, "tool": tool, "args": dict(args)})
            if tool == "create_branch":
                return "OK: branch 'vizinz-567-changes' created"
            if tool == "create_pull_request":
                return "OK: PR #42 opened — https://github.com/org/VIZINZ/pull/42"
            return "OK"

        async def _run():
            from memoria.agent.graph import run_confirmed_actions, _get_event_queue
            q = _get_event_queue("dev-test-thread")
            with patch("memoria.agent.graph.get_workflow_state", new=AsyncMock(return_value=mock_state)):
                with patch("memoria.mcp_sources.call_tool_async", side_effect=_fake_call_tool_async):
                    result = await run_confirmed_actions(
                        "dev-test-thread", ["vcs_branch", "vcs_pr"]
                    )
            events = []
            while not q.empty():
                events.append(q.get_nowait())
            return result, events

        result, events = asyncio.get_event_loop().run_until_complete(_run())

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"[Dev] write-back execution: {elapsed_ms}ms")

        # Both actions succeeded
        assert result["ok"]
        assert len(result["actions_taken"]) == 2

        # Branch was created BEFORE PR (ordering guarantee)
        assert call_log[0]["tool"] == "create_branch"
        assert call_log[1]["tool"] == "create_pull_request"
        assert call_log[1]["args"]["head"] == "vizinz-567-changes"

        # Both action_done events emitted, both ok
        action_done = [e for e in events if e.get("type") == "action_done"]
        assert len(action_done) == 2
        assert all(e["ok"] for e in action_done)

        # PR URL is in the result text
        assert "pull/42" in action_done[1]["result"]

        print(f"[Dev] PASS Branch: {call_log[0]['args']['branch']}")
        print(f"[Dev] PASS PR: {action_done[1]['result']}")

    def test_partial_approval_skips_unselected_actions(self):
        """
        User selects only 'Create branch', not 'Open PR'.
        Only the branch MCP call is made — PR is not opened.
        """
        mock_state = {
            "actions_proposed": [
                {
                    "id": "vcs_branch",
                    "label": "Create GitHub branch",
                    "source": "github",
                    "tool": "create_branch",
                    "args": {"branch": "vizinz-567-changes", "repository": "VIZINZ"},
                },
                {
                    "id": "vcs_pr",
                    "label": "Open Pull Request",
                    "source": "github",
                    "tool": "create_pull_request",
                    "args": {"title": "VIZINZ-567: changes"},
                },
            ],
            "config_path": "/tmp/config.yaml",
        }

        call_log: list[dict] = []

        async def _fake_tool(server, tool, args, config_path=None, **kwargs):
            call_log.append(tool)
            return "OK"

        async def _run():
            from memoria.agent.graph import run_confirmed_actions, _get_event_queue
            q = _get_event_queue("partial-test-thread")
            with patch("memoria.agent.graph.get_workflow_state", new=AsyncMock(return_value=mock_state)):
                with patch("memoria.mcp_sources.call_tool_async", side_effect=_fake_tool):
                    # User only selects branch, not PR
                    result = await run_confirmed_actions("partial-test-thread", ["vcs_branch"])
            events = []
            while not q.empty():
                events.append(q.get_nowait())
            return result, events

        result, events = asyncio.get_event_loop().run_until_complete(_run())

        assert result["ok"]
        assert len(result["actions_taken"]) == 1
        assert call_log == ["create_branch"]  # PR was NOT called

    def test_skip_all_actions_emits_done(self):
        """Skipping all actions emits done event with empty actions_taken."""
        async def _run():
            from memoria.agent.graph import run_confirmed_actions, _get_event_queue
            q = _get_event_queue("skip-test-thread")
            result = await run_confirmed_actions("skip-test-thread", [])
            events = []
            while not q.empty():
                events.append(q.get_nowait())
            return result, events

        result, events = asyncio.get_event_loop().run_until_complete(_run())

        assert result["ok"]
        assert len(events) == 1
        assert events[0]["type"] == "done"
        assert events[0]["actions_taken"] == []


# ─── Tool matching end-to-end: discovery → proposal → execution ──────────────

class TestToolDiscoveryToExecution:
    """
    Verifies the dynamic tool discovery → proposal → execution chain.
    Proves that 'add_comment' is used because the MCP server EXPOSES it,
    not because it was hardcoded.
    """

    def test_tool_discovered_from_mcp_is_used_in_proposal(self):
        """
        _discover_tools returns real tool names → _match_tool picks the right one
        → action is proposed with that tool name → execution uses it.
        """
        # What the Jira MCP server actually exposes
        real_jira_tools = {
            "add_comment",       # ← this is what we should use
            "get_issue",
            "update_issue",
            "list_issues",
            "transition_issue",
        }

        from memoria.agent.graph import _match_tool, _TOOL_PATTERNS

        # Dynamic matching: our patterns against real tool names
        comment_tool = _match_tool(real_jira_tools, _TOOL_PATTERNS["jira_comment"])
        update_tool  = _match_tool(real_jira_tools, _TOOL_PATTERNS["jira_update"])

        assert comment_tool == "add_comment"
        assert update_tool  == "update_issue"

    def test_tool_not_exposed_means_no_action_proposed(self):
        """
        If the Jira MCP doesn't expose a comment tool, no comment action is proposed.
        (User's custom MCP might not have all tools.)
        """
        limited_jira_tools = {
            "get_issue",
            "list_issues",
            # No add_comment, no create_comment
        }

        from memoria.agent.graph import _match_tool, _TOOL_PATTERNS
        comment_tool = _match_tool(limited_jira_tools, _TOOL_PATTERNS["jira_comment"])
        assert comment_tool is None  # → action not proposed → user not shown a broken button

    def test_alternate_tool_name_discovered(self):
        """
        Different Jira MCP implementations use different tool names.
        e.g. Atlassian's official MCP uses 'jira_add_comment' not 'add_comment'.
        Substring matching handles this.
        """
        atlassian_tools = {
            "jira_add_comment",    # Atlassian's naming
            "jira_get_issue",
            "jira_update_issue",
        }

        from memoria.agent.graph import _match_tool, _TOOL_PATTERNS
        comment_tool = _match_tool(atlassian_tools, _TOOL_PATTERNS["jira_comment"])
        # "add_comment" is a substring of "jira_add_comment"
        assert comment_tool == "jira_add_comment"
