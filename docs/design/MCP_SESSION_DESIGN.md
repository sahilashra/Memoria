# MCP Session Design

> How MCP connections are managed, the subprocess lifecycle, the critical `asyncio.run()` bug, and the Phase 1 / Phase 2 session model.

---

## The Current MCP Model

`mcp_sources.py` spawns an MCP server subprocess (typically `npx @atlassian/mcp-atlassian` or similar) using `StdioServerParameters`, runs all requested tool calls inside an `async with stdio_client(...)` context manager, then the context exits and terminates the subprocess.

```python
# Simplified from mcp_sources.py
async def _pull_async(source: dict, since: str | None = None) -> str:
    params = StdioServerParameters(command=source["command"], args=source["args"], env=...)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for tool_call in source["pull"]:
                result = await session.call_tool(tool_call["tool"], tool_call["args"])
                ...
    # subprocess terminated here
    return formatted_results
```

**This design works for batch pulls** (scheduled, once per sync interval). It does not work for:
1. Being called from inside FastAPI's async event loop (`asyncio.run()` raises)
2. Multiple tool calls on the same server in a ticket session (re-spawns Node.js each time)

---

## The Critical Bug: `asyncio.run()` in FastAPI

`pull_source()` (the public function) calls `asyncio.run(_pull_async(...))`. This pattern requires a new event loop. Inside FastAPI, an event loop is already running. Calling `asyncio.run()` from inside an async route raises:

```
RuntimeError: This event loop is already running.
```

**Fix required before Phase 1 can start** (Build Order Step 1):

```python
# New public async entry point — add to mcp_sources.py
async def call_tool_async(
    server: str,
    tool: str,
    args: dict,
    env: dict | None = None,
    config_path: str | None = None
) -> str:
    """
    Call a single MCP tool. Safe to await inside FastAPI.
    server: key in config.yaml sources, OR a direct command spec.
    """
    # Build source dict from config or inline spec
    if config_path:
        sources = load_mcp_config(config_path)
        source_spec = sources.get(server)
        if not source_spec:
            raise ValueError(f"MCP server '{server}' not found in config")
    else:
        source_spec = {"server": server, "env": env or {}}

    source = {
        **source_spec,
        "pull": [{"tool": tool, "args": args}]
    }
    return await _pull_async(source)
```

The `POST /api/mcp/fetch` endpoint wraps this:

```python
@app.post("/api/mcp/fetch")
async def mcp_fetch(request: MCPFetchRequest):
    result = await call_tool_async(
        server=request.server,
        tool=request.tool,
        args=request.args,
        config_path=config_path
    )
    return {"result": result}
```

---

## Phase 1 Subprocess Model — Per-Call

In Phase 1, each `call_tool_async()` call spawns a new subprocess. This is acceptable with known latency:

| MCP Server Type | Cold Start Time |
|---|---|
| `npx @atlassian/mcp-atlassian` | 1.5–3 seconds (Node.js + package load) |
| `npx @modelcontextprotocol/server-github` | 1–2 seconds |
| `npx figma-mcp` | 2–4 seconds |
| Python-based MCP servers | 0.3–0.8 seconds |

**In the ticket session context:** the context assembly pass calls MCP at most 3 times (Jira for ticket details, GitHub for branch existence, Figma for linked frame). With cold starts, that's 4–9 seconds of MCP overhead on ticket open. Acceptable for Phase 1.

**What is NOT acceptable at Phase 1:** calling MCP tools in a tight loop (e.g., fetching 20 tickets for the dashboard). These calls should be batched in a single `_pull_async()` invocation by building a multi-tool `pull` list, not calling `call_tool_async()` 20 times in sequence.

---

## Phase 2 Session Model — Persistent Connections

When Phase 2 adds one-click MCP actions (create branch, open PR, update ticket), multiple write calls will happen in sequence in the action execution node. Spawning a new subprocess for each is:
- ~6–12 seconds for 3 actions (branch + PR + ticket update)
- Wasteful: the MCP server is stateless between calls; all state is in the Jira/GitHub APIs

**Fix: session-scoped connection pool**

```python
# New class in mcp_sources.py (Phase 2)
class MCPSessionPool:
    """Keeps one ClientSession alive per (session_id, server_name)."""

    def __init__(self):
        self._sessions: dict[tuple[str, str], ClientSession] = {}
        self._contexts = {}

    async def get_session(
        self,
        session_id: str,
        server_name: str,
        source_spec: dict
    ) -> ClientSession:
        key = (session_id, server_name)
        if key not in self._sessions:
            params = StdioServerParameters(...)
            ctx = stdio_client(params)
            read, write = await ctx.__aenter__()
            session = ClientSession(read, write)
            await session.initialize()
            self._sessions[key] = session
            self._contexts[key] = ctx
        return self._sessions[key]

    async def close_session(self, session_id: str, server_name: str):
        key = (session_id, server_name)
        if key in self._sessions:
            await self._contexts[key].__aexit__(None, None, None)
            del self._sessions[key]
            del self._contexts[key]

    async def close_all_for_session(self, session_id: str):
        keys = [k for k in self._sessions if k[0] == session_id]
        for key in keys:
            await self.close_session(*key)

# Process-level singleton (Phase 2)
mcp_pool = MCPSessionPool()
```

Sessions are closed when the ticket session ends (`done` SSE event is emitted). This reduces action execution overhead from ~6–12 seconds to ~0.5 seconds for a 3-action sequence after the first call (which still pays the cold start cost).

---

## MCP Config Format

Users paste JSON in the same format as Claude Desktop:

```json
{
  "atlassian": {
    "command": "npx",
    "args": ["-y", "@atlassian/mcp-atlassian"],
    "env": {
      "CONFLUENCE_URL": "https://company.atlassian.net",
      "JIRA_URL": "https://company.atlassian.net",
      "ATLASSIAN_API_TOKEN": "..."
    }
  },
  "github": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": {
      "GITHUB_PERSONAL_ACCESS_TOKEN": "..."
    }
  }
}
```

This is stored in `config.yaml` under a `mcp_sources:` key. The existing `mcp_sources.py` already parses this format.

---

## Transactional Action Model (Phase 2)

Write actions (create branch, open PR, update ticket) are not atomic. A partial failure leaves real side effects.

**Required behavior:**

1. **Pre-flight check:** before executing any write action, verify the MCP token has the required scope. GitHub: check `token.scopes` includes `repo`. Jira: check `user.accountType` is not read-only. If check fails: surface "Permission denied — your [GitHub] token needs `repo` write access" before attempting.

2. **Partial failure recovery:** if `create_branch` succeeds and `create_pull_request` fails:
   - Log the partial state: `{ "branch_created": "feature/PROJ-142-...", "pr_failed": true, "error": "..." }`
   - Show in UI: "Branch created: `feature/PROJ-142-dark-mode-toggle`. PR creation failed: [error]. You can [Retry PR] or [Open GitHub] to create manually."
   - Store in session log so the user can recover in a later session

3. **No silent orphans:** never leave a created branch with no user-visible record. Even if the rest of the session crashes, the session log always includes branch names created.

4. **Idempotency on retry:** if the user retries PR creation, check if a PR already exists for the branch before creating a new one. Use GitHub MCP `list_pull_requests(head=branch_name)` as a pre-check.
