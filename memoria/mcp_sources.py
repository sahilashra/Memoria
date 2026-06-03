"""
MCP Source Connector — pull content from any MCP server into Memoria's pipeline.

How it works
────────────
1. Reads  mcp_sources:  from config.yaml
2. For each source, spawns the MCP server as a subprocess (via the official `mcp` SDK)
3. Calls the tools listed under  pull:  and collects the text responses
4. Returns raw text that the generator turns into a Memory Bank

Config shape (add to config.yaml)
──────────────────────────────────
    mcp_sources:
      - name: "confluence"
        server: "npx @atlassian/mcp-confluence"
        env:
          CONFLUENCE_URL: "${CONFLUENCE_URL}"
          CONFLUENCE_TOKEN: "${CONFLUENCE_TOKEN}"
        pull:
          - tool: "confluence_search"
            args: {query: "engineering architecture", limit: 20}

      - name: "teams"
        server: "npx @microsoft/mcp-teams"
        env:
          TEAMS_TOKEN: "${TEAMS_TOKEN}"
        pull:
          - tool: "teams_get_messages"
            args: {channel: "dev-general", days: 7}

      - name: "slack"
        server: "npx @slack/mcp"
        env:
          SLACK_BOT_TOKEN: "${SLACK_BOT_TOKEN}"
        pull:
          - tool: "slack_get_messages"
            args: {channel: "eng-decisions", limit: 100}

      - name: "notion"
        server: "npx @notionhq/mcp"
        env:
          NOTION_API_KEY: "${NOTION_API_KEY}"
        pull:
          - tool: "notion_query_database"
            args: {database_id: "abc123"}

Auth
────
Store tokens in ~/.memoria/.env (created by `memoria init`) or your project .env:
    CONFLUENCE_TOKEN=your-token-here
    SLACK_BOT_TOKEN=xoxb-...

Requires
────────
    pip install mcp
The MCP servers themselves are separate installs — most are npm packages.
"""

import asyncio
import os
import re
import shlex
import yaml
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path
from typing import Optional


# ─── Config loading ───────────────────────────────────────────────────────────

def _load_config(config_path: str) -> dict:
    """
    Same 3-level discovery as models.py:
      1. Given path
      2. Walk up CWD (like git finds .git)
      3. ~/.memoria/config.yaml
    """
    path = Path(config_path)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / "config.yaml"
        if candidate.exists():
            try:
                with open(candidate, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if "model" in data or "mcp_sources" in data or "books_dir" in data:
                    return data
            except Exception:
                pass

    user_config = Path.home() / ".memoria" / "config.yaml"
    if user_config.exists():
        with open(user_config, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    return {}


def load_sources(config_path: str = "config.yaml") -> list:
    """Return the mcp_sources list from config.yaml, or []."""
    return _load_config(config_path).get("mcp_sources", [])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _expand_env(value: str) -> str:
    """Expand ${VAR_NAME} patterns from environment variables."""
    return re.sub(
        r"\$\{([^}]+)\}",
        lambda m: os.environ.get(m.group(1), m.group(0)),
        str(value),
    )


def _text_from_content_block(block) -> str:
    """Extract a string from an MCP content block (text or json/data)."""
    if hasattr(block, "text") and block.text:
        return block.text.strip()
    if hasattr(block, "data"):
        import json as _json
        try:
            return _json.dumps(block.data, indent=2, ensure_ascii=False)
        except Exception:
            return str(block.data)
    return ""


# ─── Incremental pull helpers ─────────────────────────────────────────────────

# Parameter names commonly used by MCP servers to accept a "since this time" filter.
# We check the tool's JSON Schema; if any of these keys appear in its properties,
# we inject the last-pull timestamp automatically (when incremental: true).
_SINCE_PARAM_NAMES = frozenset({
    "since", "after", "from", "updated_after", "modified_after",
    "from_date", "start_date", "newer_than",
})


def _inject_since(
    tool_args: dict,
    tool_schema: Optional[dict],
    since: Optional[datetime],
) -> dict:
    """
    If `since` is set and the tool's JSON Schema contains a temporal-filter param,
    inject the ISO timestamp into tool_args (without overwriting user-supplied values).
    Returns tool_args (possibly updated — a copy is returned, original is not mutated).
    """
    if not since or not tool_schema:
        return tool_args
    properties = tool_schema.get("properties", {}) if isinstance(tool_schema, dict) else {}
    matching = _SINCE_PARAM_NAMES & set(properties.keys())
    if not matching:
        return tool_args
    param = next(iter(sorted(matching)))   # deterministic: pick alphabetically first
    args = dict(tool_args)   # copy — never mutate caller's dict
    args.setdefault(param, since.isoformat())
    return args


# ─── Session pool ─────────────────────────────────────────────────────────────
#
# call_tool_async / list_tools_async reuse long-lived ClientSession objects so
# the MCP subprocess (or HTTP connection) is only spawned once per server
# configuration.  _pull_async (used by the bulk pull pipeline) still opens its
# own temporary session because it needs to run outside a live event loop.

class _PoolEntry:
    __slots__ = ("session", "lock", "_stack")

    def __init__(self) -> None:
        self.session = None
        self.lock = asyncio.Lock()
        self._stack: AsyncExitStack | None = None

    async def ensure_open(self, source: dict) -> None:
        """Open a new session if one isn't already alive. Call under self.lock."""
        if self.session is not None:
            return
        stack = AsyncExitStack()
        try:
            ctx = await _make_client_session(source)
            session = await stack.enter_async_context(ctx)
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self.session = session

    async def close(self) -> None:
        if self._stack:
            try:
                await self._stack.aclose()
            except Exception:
                pass
            self._stack = None
        self.session = None


_pool: dict[str, _PoolEntry] = {}
_pool_lock: asyncio.Lock | None = None


def _get_pool_lock() -> asyncio.Lock:
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


def _pool_key(source: dict) -> str:
    server = source.get("server", "")
    env_sig = ",".join(f"{k}={v}" for k, v in sorted((source.get("env") or {}).items()))
    return f"{server}||{env_sig}"


async def _acquire_entry(source: dict) -> tuple["_PoolEntry", str]:
    """Return (entry, key); creates a new entry if none exists for this source."""
    key = _pool_key(source)
    async with _get_pool_lock():
        entry = _pool.get(key)
        if entry is None:
            entry = _PoolEntry()
            _pool[key] = entry
    return entry, key


async def _evict(key: str, entry: "_PoolEntry") -> None:
    """Remove a broken entry from the pool and close its session."""
    async with _get_pool_lock():
        if _pool.get(key) is entry:
            del _pool[key]
    await entry.close()


async def shutdown_pool() -> None:
    """Close every pooled session. Call on app shutdown."""
    async with _get_pool_lock():
        entries = list(_pool.values())
        _pool.clear()
    for entry in entries:
        async with entry.lock:
            await entry.close()


# ─── Core async pull ──────────────────────────────────────────────────────────

def _is_http_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


async def _make_client_session(source: dict):
    """
    Context manager factory — returns the right client for stdio vs HTTP transport.
    Usage:  async with _make_client_session(source) as session: ...
    """
    from contextlib import asynccontextmanager

    server_str = source.get("server", "").strip()
    if not server_str:
        raise ValueError(
            f"Source '{source.get('name', '?')}' has no 'server' configured."
        )

    if _is_http_url(server_str):
        # HTTP/SSE transport (e.g. GitHub Copilot MCP, remote servers)
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError:
            raise ImportError(
                "mcp package not installed or too old for HTTP transport.\n"
                "Run: pip install 'mcp>=1.3'"
            )
        raw_headers = source.get("headers") or {}
        headers = {str(k): str(v) for k, v in raw_headers.items()}
        # Expand env vars in header values
        headers = {k: _expand_env(v) for k, v in headers.items()}

        @asynccontextmanager
        async def _http_ctx():
            async with streamablehttp_client(server_str, headers=headers or None) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

        return _http_ctx()
    else:
        # Stdio transport (subprocess)
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            raise ImportError(
                "mcp package not installed.\n"
                "Run:  pip install mcp\n"
                "Then install the MCP server binary (e.g. npm i -g @atlassian/mcp-confluence)"
            )
        parts = shlex.split(server_str)
        cmd, args = parts[0], parts[1:]
        env = {**os.environ}
        for k, v in source.get("env", {}).items():
            env[k] = _expand_env(v)

        # Docker containers don't inherit the host process environment, so
        # inject -e KEY for each source env var before the image name.
        if cmd == "docker" and source.get("env"):
            extra_flags: list[str] = []
            for k in source["env"]:
                extra_flags += ["-e", k]
            # Insert flags just before the image name (after "run" and its options).
            # Find the first arg that doesn't start with "-" and isn't a known
            # value-bearing flag — that's the image name position.
            _VALUE_FLAGS = {
                "--add-host", "--attach", "-a", "--blkio-weight-device",
                "--cap-add", "--cap-drop", "--cgroup-parent", "--cgroupns",
                "--cidfile", "--cpu-period", "--cpu-quota", "--cpu-rt-period",
                "--cpu-rt-runtime", "--cpu-shares", "-c", "--cpus", "--cpuset-cpus",
                "--cpuset-mems", "--device", "--device-cgroup-rule",
                "--device-read-bps", "--device-read-iops", "--device-write-bps",
                "--device-write-iops", "--dns", "--dns-option", "--dns-search",
                "--domainname", "--entrypoint", "--env", "-e", "--env-file",
                "--expose", "--gpus", "--group-add", "--health-cmd",
                "--health-interval", "--health-retries", "--health-start-period",
                "--health-timeout", "--hostname", "-h", "--init", "--ip", "--ip6",
                "--ipc", "--isolation", "--kernel-memory", "--label", "-l",
                "--label-file", "--link", "--link-local-ip", "--log-driver",
                "--log-opt", "--mac-address", "--memory", "-m", "--memory-reservation",
                "--memory-swap", "--memory-swappiness", "--mount", "--name",
                "--net", "--network", "--network-alias", "--no-healthcheck",
                "--oom-kill-disable", "--oom-score-adj", "--pid", "--pids-limit",
                "--platform", "--privileged", "--publish", "-p", "--publish-all",
                "--pull", "--read-only", "--restart", "--rm", "--runtime",
                "--security-opt", "--shm-size", "--sig-proxy", "--stop-signal",
                "--stop-timeout", "--storage-opt", "--sysctl", "--tmpfs",
                "--tty", "-t", "--ulimit", "--user", "-u", "--userns",
                "--uts", "--volume", "-v", "--volume-driver", "--volumes-from",
                "--workdir", "-w",
            }
            insert_at = len(args)
            skip_next = False
            for i, arg in enumerate(args):
                if skip_next:
                    skip_next = False
                    continue
                if arg in _VALUE_FLAGS:
                    skip_next = True
                    continue
                if arg.startswith("-"):
                    continue
                # First positional arg after "run" is the image name
                insert_at = i
                break
            args = args[:insert_at] + extra_flags + args[insert_at:]

        params = StdioServerParameters(command=cmd, args=args, env=env)

        @asynccontextmanager
        async def _stdio_ctx():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

        return _stdio_ctx()


async def _pull_async(source: dict, since: Optional[datetime] = None) -> str:
    """
    Connect to an MCP server (stdio or HTTP), call configured tools, return combined text.

    Parameters
    ----------
    source : dict
        One entry from  mcp_sources:  in config.yaml.
    since : datetime | None
        When set (and source has  incremental: true), inject the timestamp into
        any tool call whose schema exposes a temporal-filter parameter.

    Raises ImportError if the `mcp` package isn't installed.
    """
    incremental = bool(source.get("incremental")) and since is not None
    chunks: list[str] = []

    async with await _make_client_session(source) as session:
        pull_list = source.get("pull", [])

        # No pull config → list available tools so the user knows what to add
        if not pull_list:
            tools_result = await session.list_tools()
            tool_names = [t.name for t in (tools_result.tools or [])]
            lines = [
                f"# Available tools on '{source.get('name', 'source')}'",
                "",
                "Add these to your config.yaml `pull:` list to start pulling content.",
                "",
            ]
            lines += [f"- `{name}`" for name in tool_names]
            return "\n".join(lines)

        # Build a schema map for incremental injection (list tools once)
        schema_by_name: dict = {}
        if incremental:
            try:
                tools_result = await session.list_tools()
                schema_by_name = {
                    t.name: (t.inputSchema if hasattr(t, "inputSchema") else None)
                    for t in (tools_result.tools or [])
                }
            except Exception:
                pass  # Can't list tools? Skip incremental injection gracefully

        for item in pull_list:
            tool_name = item.get("tool")
            # Support both flat {tool: x, key: val} and nested {tool: x, args: {...}}
            if "args" in item and isinstance(item["args"], dict):
                tool_args = dict(item["args"])
            else:
                tool_args = {k: v for k, v in item.items() if k != "tool"}

            if not tool_name:
                continue

            # Inject since-timestamp for incremental pulls
            if incremental:
                tool_args = _inject_since(
                    tool_args, schema_by_name.get(tool_name), since
                )

            result = await session.call_tool(tool_name, arguments=tool_args)

            piece = "\n\n".join(
                t for b in result.content
                if (t := _text_from_content_block(b))
            )
            if piece:
                chunks.append(piece)

    return "\n\n---\n\n".join(chunks) if chunks else "[No content returned by this source]"


# ─── Public sync API ──────────────────────────────────────────────────────────

def pull_source(source: dict, since: Optional[datetime] = None) -> str:
    """
    Pull from a single MCP source. Blocking — runs async internally.
    Returns the combined text from all tool calls.

    Parameters
    ----------
    source : dict
        One entry from  mcp_sources:  in config.yaml.
    since : datetime | None
        When set (and source has  incremental: true), inject timestamp into
        tool calls that support temporal filtering. Pass the value from
        pull_state.get_last_pull(source['name']).
    """
    return asyncio.run(_pull_async(source, since=since))


def pull_all(config_path: str = "config.yaml") -> dict:
    """
    Pull from every configured MCP source.
    Returns  {source_name: content_text}.
    Safe: errors on individual sources are caught and returned as error strings,
    so one broken source never stops the others.
    Does NOT use incremental mode — use pull_source() directly for that.
    """
    sources = load_sources(config_path)
    results = {}
    for src in sources:
        name = src.get("name", "unknown")
        try:
            results[name] = asyncio.run(_pull_async(src))
        except Exception as e:
            results[name] = f"[ERROR pulling from '{name}': {e}]"
    return results


# ─── Async entry point for FastAPI / agent use ────────────────────────────────

async def list_tools_async(
    server: str,
    config_path: str = "config.yaml",
) -> list[dict]:
    """
    List available tools from an MCP source (stdio or HTTP transport).
    Returns [{name, description, inputSchema}].
    Reuses a pooled session if one already exists for this server.
    """
    sources = load_sources(config_path)
    source_spec = next((s for s in sources if s.get("name") == server), None)
    source = dict(source_spec) if source_spec else {"server": server, "env": {}}

    entry, key = await _acquire_entry(source)

    async with entry.lock:
        try:
            await entry.ensure_open(source)
        except Exception:
            await _evict(key, entry)
            raise

        try:
            result = await entry.session.list_tools()
        except Exception:
            await _evict(key, entry)
            raise

    return [
        {
            "name": t.name,
            "description": getattr(t, "description", "") or "",
            "inputSchema": getattr(t, "inputSchema", {}) or {},
        }
        for t in (result.tools or [])
    ]


async def call_tool_async(
    server: str,
    tool: str,
    args: dict,
    env: dict | None = None,
    config_path: str | None = None,
) -> str:
    """
    Call a single MCP tool and return the text result.
    Safe to await inside FastAPI — never calls asyncio.run().
    Reuses a pooled ClientSession; the subprocess/HTTP connection is kept alive
    between calls to avoid the 2-4 s spawn cost on every invocation.

    Parameters
    ----------
    server : str
        Either a source name from config.yaml (e.g. "github") or a direct
        command string (e.g. "npx @modelcontextprotocol/server-github").
    tool : str
        The MCP tool name to call.
    args : dict
        Arguments for the tool call.
    env : dict | None
        Extra environment variables. Merged on top of any config-defined env
        when the source is looked up by name; used as the sole env otherwise.
    config_path : str | None
        Path to config.yaml. Defaults to standard config discovery if None.
    """
    cfg_path = config_path or "config.yaml"
    sources = load_sources(cfg_path)
    source_spec = next((s for s in sources if s.get("name") == server), None)

    if source_spec is not None:
        source = dict(source_spec)
        if env:
            source["env"] = {**source.get("env", {}), **env}
    else:
        source = {"server": server, "env": env or {}}

    entry, key = await _acquire_entry(source)

    async with entry.lock:
        try:
            await entry.ensure_open(source)
        except Exception:
            await _evict(key, entry)
            raise

        try:
            result = await entry.session.call_tool(tool, arguments=args)
        except Exception:
            await _evict(key, entry)
            raise

    piece = "\n\n".join(
        t for b in result.content
        if (t := _text_from_content_block(b))
    )
    return piece or "[No content returned]"
