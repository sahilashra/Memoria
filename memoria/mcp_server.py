"""
memoria MCP server — exposes Memory Banks and SKILL.md files via the
Model Context Protocol (stdio transport, JSON-RPC 2.0).

Configure in Claude Desktop (~/.config/claude/claude_desktop_config.json):

  {
    "mcpServers": {
      "memoria": {
        "command": "memoria",
        "args": ["serve", "--mcp"],
        "env": {}
      }
    }
  }

Or in Cursor (.cursor/mcp.json):

  {
    "mcpServers": {
      "memoria": {
        "command": "memoria",
        "args": ["serve", "--mcp"]
      }
    }
  }

Tools exposed:
  - list_projects      — list all available Memory Bank projects
  - get_memory_bank    — retrieve the full Memory Bank for a project
  - get_skill_file     — get or generate the SKILL.md for a project
  - search             — semantic search across all Memory Banks
  - ask                — natural language Q&A over all Memory Banks
"""

from __future__ import annotations

import json
import sys
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("memoria.mcp")


# ── MCP message helpers ───────────────────────────────────────────────────────

def _send(msg: dict) -> None:
    """Write a JSON-RPC message to stdout (newline-delimited)."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _ok(req_id, result: Any) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _err(req_id, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


# ── Tool definitions ──────────────────────────────────────────────────────────

_TOOLS = [
    {
        "name": "list_projects",
        "description": (
            "List all available Memory Bank projects in Memoria. "
            "Returns project names, sizes, and last-updated timestamps."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_memory_bank",
        "description": (
            "Retrieve the full Memory Bank for a specific project. "
            "A Memory Bank is a comprehensive reference document covering architecture, "
            "workflows, key concepts, and gotchas. Pass this to your LLM context to "
            "answer questions about the project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "The project name (use list_projects to see available names)",
                }
            },
            "required": ["project"],
        },
    },
    {
        "name": "get_skill_file",
        "description": (
            "Get the SKILL.md for a project — a structured list of AI-agent capabilities "
            "for that project. If no SKILL.md exists yet, generates one on-the-fly from "
            "the Memory Bank. Use this to understand what actions you can take on a project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "The project name",
                },
                "format": {
                    "type": "string",
                    "enum": ["raw", "langchain", "crewai", "autogen", "n8n"],
                    "default": "raw",
                    "description": "Output format (default: raw SKILL.md)",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "search",
        "description": (
            "Semantic search across all Memory Banks. Returns the most relevant excerpts "
            "from across all projects for a given query."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for",
                },
                "limit": {
                    "type": "integer",
                    "default": 5,
                    "description": "Max number of results to return",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask",
        "description": (
            "Ask a natural language question and get an answer grounded in Memory Banks. "
            "Specify a project for a focused answer, or omit for a cross-project search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Your question",
                },
                "project": {
                    "type": "string",
                    "description": "Restrict answer to a specific project (optional)",
                },
            },
            "required": ["question"],
        },
    },
]


# ── Tool handlers ─────────────────────────────────────────────────────────────

def _handle_list_projects(books_dir: str, **_) -> dict:
    books_path = Path(books_dir)
    if not books_path.exists():
        return {"projects": [], "message": "No books directory found."}
    projects = []
    for f in sorted(books_path.glob("*_memory_bank.md")):
        stat = f.stat()
        from datetime import datetime
        projects.append({
            "name":     f.stem.replace("_memory_bank", "").replace("_", " "),
            "size_kb":  round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "path":     str(f.resolve()),
        })
    return {
        "projects": projects,
        "count":    len(projects),
        "books_dir": str(books_path.resolve()),
    }


def _handle_get_memory_bank(books_dir: str, project: str, **_) -> dict:
    books_path = Path(books_dir)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project)
    book_file = books_path / f"{safe}_memory_bank.md"

    if not book_file.exists():
        # Fuzzy match
        matches = list(books_path.glob(f"*{safe.lower()}*_memory_bank.md"))
        if not matches:
            # Try case-insensitive partial match
            all_books = list(books_path.glob("*_memory_bank.md"))
            matches = [b for b in all_books if project.lower() in b.stem.lower()]
        if not matches:
            return {
                "error": f"No memory bank found for '{project}'.",
                "available": [f.stem.replace("_memory_bank", "").replace("_", " ")
                              for f in sorted(books_path.glob("*_memory_bank.md"))],
            }
        book_file = matches[0]

    content = book_file.read_text(encoding="utf-8")
    return {
        "project": project,
        "content": content,
        "size_chars": len(content),
        "path": str(book_file.resolve()),
    }


def _handle_get_skill_file(books_dir: str, config_path: str, project: str,
                            fmt: str = "raw", **_) -> dict:
    books_path = Path(books_dir)
    skills_dir = books_path / "skills"

    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project)
    suffix = "" if fmt == "raw" else f"_{fmt}"
    ext_map = {"raw": "md", "langchain": "py", "crewai": "py", "autogen": "py", "n8n": "json"}
    filename = f"{safe}_SKILL.md" if fmt == "raw" else f"{safe}_skill{suffix}.{ext_map.get(fmt, 'md')}"
    skill_file = skills_dir / filename

    # Use cached file if it exists and is newer than the memory bank
    book_file = books_path / f"{safe}_memory_bank.md"
    if skill_file.exists() and book_file.exists():
        if skill_file.stat().st_mtime >= book_file.stat().st_mtime:
            content = skill_file.read_text(encoding="utf-8")
            return {"project": project, "format": fmt, "content": content,
                    "cached": True, "path": str(skill_file.resolve())}

    # Need to generate
    book_result = _handle_get_memory_bank(books_dir, project)
    if "error" in book_result:
        return book_result

    from .skills import generate_skill
    try:
        out_path = generate_skill(
            project_name=project,
            book_content=book_result["content"],
            output_dir=str(skills_dir),
            fmt=fmt,
            config_path=config_path,
        )
        content = Path(out_path).read_text(encoding="utf-8")
        return {"project": project, "format": fmt, "content": content,
                "cached": False, "path": str(Path(out_path).resolve())}
    except Exception as e:
        return {"error": f"Skill generation failed: {e}"}


def _handle_search(books_dir: str, query: str, limit: int = 5, **_) -> dict:
    try:
        from .search import query_books
        results = query_books(query, books_dir=books_dir, n=limit)
        return {"query": query, "results": results, "count": len(results)}
    except Exception as e:
        return {"error": f"Search failed: {e}"}


def _handle_ask(books_dir: str, config_path: str, question: str,
                project: str | None = None, **_) -> dict:
    try:
        from .models import ModelProvider
        model = ModelProvider(config_path)

        # Gather context
        context_parts = []
        books_path = Path(books_dir)

        if project:
            result = _handle_get_memory_bank(books_dir, project)
            if "content" in result:
                context_parts.append(f"## Memory Bank: {project}\n{result['content']}")
        else:
            # Semantic search to find relevant sections
            search_result = _handle_search(books_dir, question, limit=3)
            if "results" in search_result and search_result["results"]:
                for r in search_result["results"]:
                    context_parts.append(
                        f"## From {r.get('project', 'Unknown')}:\n{r.get('content', '')}"
                    )

        context = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant context found."

        answer = model.complete(
            "You are a helpful assistant answering questions about software projects "
            "and processes using Memory Banks as context. Be specific and cite the source.",
            f"Context:\n{context}\n\nQuestion: {question}\n\nAnswer:",
        )
        return {"question": question, "answer": answer, "project": project}
    except Exception as e:
        return {"error": f"Ask failed: {e}"}


# ── Main server loop ──────────────────────────────────────────────────────────

_TOOL_HANDLERS = {
    "list_projects":  _handle_list_projects,
    "get_memory_bank": _handle_get_memory_bank,
    "get_skill_file": _handle_get_skill_file,
    "search":         _handle_search,
    "ask":            _handle_ask,
}


def run_mcp_server(books_dir: str, config_path: str) -> None:
    """
    Start the stdio MCP server. Reads JSON-RPC messages from stdin,
    writes responses to stdout. Runs until EOF.
    """
    # Redirect all logging to stderr so stdout stays clean for MCP messages
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        # ── initialize ──
        if method == "initialize":
            _ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name":    "memoria",
                    "version": "1.0.0",
                },
            })

        elif method == "notifications/initialized":
            pass  # no response for notifications

        # ── tools/list ──
        elif method == "tools/list":
            _ok(req_id, {"tools": _TOOLS})

        # ── tools/call ──
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments  = params.get("arguments", {})
            handler = _TOOL_HANDLERS.get(tool_name)
            if not handler:
                _err(req_id, -32601, f"Unknown tool: {tool_name}")
                continue
            try:
                result = handler(
                    books_dir=books_dir,
                    config_path=config_path,
                    **arguments,
                )
                _ok(req_id, {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
                })
            except Exception as e:
                _err(req_id, -32000, str(e))

        # ── resources/list (not implemented but spec-required) ──
        elif method == "resources/list":
            _ok(req_id, {"resources": []})

        # ── ping ──
        elif method == "ping":
            _ok(req_id, {})

        else:
            if req_id is not None:
                _err(req_id, -32601, f"Method not found: {method}")
