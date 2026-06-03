"""
Memoria Web UI — FastAPI application for the browser-based chat interface.

Serves a single-page HTML app at /ui with the following JSON + SSE endpoints:

  GET  /ui                       → index.html
  GET  /                         → redirect to /ui
  GET  /api/projects             → list all Memory Banks
  POST /api/ask                  → SSE-streaming Q&A against a Memory Bank
  GET  /api/search?q=&top=       → semantic search across all books
  GET  /api/graph                → Knowledge Graph JSON
  GET  /api/books/{project}      → raw Memory Bank content
  GET  /api/update-stream/{project}  → SSE — re-generates book as draft, streams progress
  GET  /api/update-diff/{project}    → before/after content for diff view
  POST /api/update-apply/{project}   → promote draft to live book
  POST /api/update-reject/{project}  → discard draft
  GET  /api/drafts                   → list pending drafts (review queue)
  GET  /api/sources                  → list MCP sources with last-pull state
  GET  /api/pull-stream/{source}     → SSE — pull from MCP source, stream progress
  GET  /api/settings                 → current model, masked keys, token budgets
  POST /api/settings                 → update model, keys, token budgets
  POST /api/test-model               → quick connectivity check

Start with:
  memoria ui                     # opens http://localhost:7860/ui automatically
  memoria ui --port 8080 --no-open
"""

import json
import re
import asyncio
from pathlib import Path
from datetime import datetime
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ─── App factory ─────────────────────────────────────────────────────────────

def create_app(books_dir: str = "books", config_path: str = "config.yaml") -> FastAPI:
    """
    Create and configure the Memoria UI FastAPI application.

    books_dir   — directory where *_memory_bank.md files live
    config_path — path to config.yaml (used for model selection + API keys)
    """
    # Load .env at server start so API keys and MCP tokens (e.g.
    # GITHUB_PERSONAL_ACCESS_TOKEN) are in os.environ before any request arrives.
    try:
        from dotenv import load_dotenv as _ld
        _ld(override=True)                                     # CWD / project .env
        _ld(Path.home() / ".memoria" / ".env", override=True)  # user-level .env
    except ImportError:
        pass

    app = FastAPI(title="Memoria UI", docs_url=None, redoc_url=None)

    @app.on_event("shutdown")
    async def _shutdown_mcp_pool():
        try:
            from .mcp_sources import shutdown_pool
            await shutdown_pool()
        except Exception:
            pass

    @app.on_event("startup")
    async def _startup_cleanup():
        """
        On every server start, silently purge vector store embeddings whose
        Memory Bank files have been deleted. Fixes ghost sources from
        projects deleted via CLI, file manager, or the UI before the
        vector-aware delete was introduced.
        """
        try:
            from .search import purge_orphaned
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: purge_orphaned(books_dir))
        except Exception:
            pass  # never block startup

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Share Token Middleware (Consultant Mode) ──────────────────────────────

    from fastapi.responses import JSONResponse as _JSONResponse

    @app.middleware("http")
    async def share_token_middleware(request: Request, call_next):
        """
        Validate share tokens on every request.

        - Reads X-Share-Token header (from the UI fetch) or ?token= query param (page load).
        - Sets request.state.share_token = token record dict | None.
        - Blocks write operations that aren't AI-query POSTs.
        - Blocks ALL API calls if the token is invalid or expired.
        """
        from .share import validate_token, SHARE_ALLOWED_POSTS

        token_str = (
            request.headers.get("X-Share-Token")
            or request.query_params.get("token")
        )

        if token_str:
            token_data = validate_token(token_str)
            if token_data is None:
                # Invalid / expired — block API; allow page load so UI can show the error
                if request.url.path.startswith("/api/"):
                    return _JSONResponse(
                        {"error": "Invalid or expired share token"},
                        status_code=401,
                    )
            else:
                request.state.share_token = token_data

                # Block write operations that aren't AI queries
                if request.method in ("POST", "DELETE", "PUT", "PATCH"):
                    if request.url.path not in SHARE_ALLOWED_POSTS:
                        return _JSONResponse(
                            {"error": "Read-only access. This action is not available in shared view."},
                            status_code=403,
                        )
        else:
            request.state.share_token = None

        response = await call_next(request)
        return response

    _static = Path(__file__).parent / "static"

    # ── Serve UI ─────────────────────────────────────────────────────────────

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/ui")

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    async def serve_ui():
        html_path = _static / "index.html"
        if not html_path.exists():
            raise HTTPException(status_code=500, detail="UI static files not found.")
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    # ── Filesystem browser (localhost only — lets Analyze panel pick paths) ──

    @app.get("/api/fs/browse")
    async def fs_browse(path: str = ""):
        """
        Return the contents of a local directory for the Analyze-panel path picker.
        Only available when running on localhost (not in share mode).
        """
        import os, platform
        try:
            # Default to home directory if no path given
            if not path:
                target = Path.home()
            else:
                target = Path(path).expanduser().resolve()

            if not target.exists():
                return {"error": f"Path not found: {path}", "entries": [], "path": str(target)}

            if target.is_file():
                # If they navigated to a file, return its parent dir and mark it selected
                return {"path": str(target.parent), "selected_file": str(target), "entries": _fs_list(target.parent)}

            return {"path": str(target), "entries": _fs_list(target)}
        except PermissionError:
            return {"error": "Permission denied", "entries": [], "path": path}
        except Exception as e:
            return {"error": str(e), "entries": [], "path": path}

    def _fs_list(directory: Path) -> list:
        """List entries in a directory, sorted: folders first, then files."""
        entries = []
        try:
            for item in sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                try:
                    entries.append({
                        "name":   item.name,
                        "path":   str(item),
                        "is_dir": item.is_dir(),
                        "size":   item.stat().st_size if item.is_file() else 0,
                    })
                except (PermissionError, OSError):
                    pass
        except (PermissionError, OSError):
            pass
        # Prepend parent directory entry if not at filesystem root
        parent = directory.parent
        if parent != directory:
            entries.insert(0, {"name": "..", "path": str(parent), "is_dir": True, "size": 0})
        return entries

    # ── Projects ──────────────────────────────────────────────────────────────

    @app.get("/api/projects")
    async def list_projects(request: Request):
        """Return all Memory Banks sorted by last-modified (newest first)."""
        books_path = Path(books_dir)
        if not books_path.exists():
            return {"projects": []}

        projects = []
        for book in books_path.glob("*_memory_bank.md"):
            name = book.stem.replace("_memory_bank", "")
            stat = book.stat()
            projects.append({
                "name":    name,
                "path":    str(book),
                "updated": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "size":    stat.st_size,
            })

        projects.sort(key=lambda p: p["updated"], reverse=True)

        # Filter by share-token project scope (if consultant is viewing)
        token = getattr(request.state, "share_token", None)
        if token:
            from .share import is_project_allowed
            projects = [p for p in projects if is_project_allowed(token, p["name"])]

        return {"projects": projects}

    @app.get("/api/share-info")
    async def share_info(request: Request):
        """
        Return information about the current share token session.
        The UI calls this on load to detect consultant / read-only mode.
        """
        token = getattr(request.state, "share_token", None)
        if token:
            return {
                "is_share_mode": True,
                "label":         token.get("label", ""),
                "projects":      token.get("projects", []),
                "expires_at":    token.get("expires_at"),
            }
        return {"is_share_mode": False}

    # ── Ask (SSE streaming) ───────────────────────────────────────────────────

    class AskRequest(BaseModel):
        project:     str
        question:    str
        history:     list = []          # [{role:"user"|"assistant", content:"..."}]
        # Optional per-request model overrides from the UI gear button
        model:       Optional[str]   = None
        temperature: Optional[float] = None
        max_tokens:  Optional[int]   = None
        top_p:       Optional[float] = None

    @app.post("/api/ask")
    async def ask(req: AskRequest):
        """
        Stream an AI answer for a question about a specific project.

        If the project has a matching MCP source configured, live data is fetched
        from that source at query time and used as primary context — the answer
        reflects the current state of the external service, not a cached snapshot.

        SSE event shapes:
          data: {"type": "mcp_fetch", "sources": ["name", ...]}  — live fetch started
          data: {"type": "chunk",     "text": "..."}              — token chunk
          data: {"type": "done"}                                   — stream complete
          data: {"type": "error",     "message": "..."}           — error
        """
        books_path = Path(books_dir)
        safe_name  = _safe(req.project)
        book_path  = books_path / f"{safe_name}_memory_bank.md"

        # ── Detect live MCP sources for this project ──────────────────────────
        # Matching strategy (in priority order):
        #   1. Exact name match            github  ↔  github
        #   2. Safe-name exact match       github  ↔  github (after stripping specials)
        #   3. Prefix containment          github  ↔  github_pull  (source is prefix of project)
        #   4. Reverse containment         github_pull ↔ github  (project is prefix of source)
        # This handles the common case where the project was named after the source
        # with an added suffix (e.g. "_pull", "_mcp", "_context").
        live_sources: list = []
        try:
            from .mcp_sources import load_sources
            all_sources = load_sources(config_path)
            def _source_matches(src: dict) -> bool:
                src_safe = _safe(src.get("name", "")).lower()
                proj_safe = safe_name.lower()
                return (
                    src_safe == proj_safe                       # exact
                    or src.get("name", "") == req.project       # raw exact
                    or proj_safe.startswith(src_safe + "_")     # github_pull starts with github_
                    or src_safe.startswith(proj_safe + "_")     # reverse
                )
            live_sources = [s for s in all_sources if _source_matches(s)]
        except Exception:
            pass

        # Allow asking even before the first sync (no Memory Bank on disk yet)
        if not book_path.exists() and not live_sources:
            raise HTTPException(
                status_code=404,
                detail=f"Memory Bank not found for '{req.project}'. "
                       "Run `memoria analyze` to create one.",
            )

        book_content = book_path.read_text(encoding="utf-8") if book_path.exists() else ""

        # Cross-source intelligence: ticket context
        try:
            from .tickets import enrich_context_with_tickets
            ticket_ctx = enrich_context_with_tickets(book_content, req.question, config_path)
        except Exception:
            ticket_ctx = ""

        # Cross-source intelligence: GitHub activity sidecar
        try:
            from .github_source import get_github_context_for_prompt
            github_ctx = get_github_context_for_prompt(req.project, books_dir)
        except Exception:
            github_ctx = ""

        extra_ctx = ticket_ctx + github_ctx

        # Collect per-request model overrides (same as global-ask)
        model_overrides = {k: v for k, v in {
            "model":       req.model,
            "temperature": req.temperature,
            "max_tokens":  req.max_tokens,
            "top_p":       req.top_p,
        }.items() if v is not None}

        if live_sources:
            # Live path — fetch fresh data from MCP servers inside the stream,
            # so the cursor appears immediately while the fetch is in progress.
            return StreamingResponse(
                _stream_answer_live_mcp(
                    question       = req.question,
                    book_content   = book_content,
                    live_sources   = live_sources,
                    extra_ctx      = extra_ctx,
                    config_path    = config_path,
                    history        = req.history,
                    model_overrides= model_overrides,
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # Static path — answer from the Memory Bank snapshot
        system = _build_system_prompt(book_content) + extra_ctx
        return StreamingResponse(
            _stream_answer(
                system, req.question, config_path,
                history=req.history,
                model_overrides=model_overrides,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Global Ask (SSE streaming, cross-book) ───────────────────────────────

    class GlobalAskRequest(BaseModel):
        question: str
        top_k: int = 8   # number of chunks to retrieve across all books
        history: list = []  # [{role: "user"|"assistant", content: "..."}]
        # Optional per-request model overrides from the UI gear button
        model: Optional[str] = None
        temperature: Optional[float] = None
        max_tokens: Optional[int] = None
        top_p: Optional[float] = None
        # MCP tool approval gate:
        #   None  → first request; run tool selection; pause for consent if tools needed
        #   []    → user declined; answer from Memory Bank only
        #   [...]  → user approved these calls; execute them then answer
        approved_tools: Optional[list] = None

    @app.post("/api/global-ask")
    async def global_ask(
        req: GlobalAskRequest,
        x_memoria_user: Optional[str] = Header(default=None, alias="X-Memoria-User"),
    ):
        """
        Answer a question by searching across ALL Memory Banks.
        No project selection required.

        Optional header:
          X-Memoria-User: <username>   — apply RBAC scope filtering for this user

        SSE event shapes:
          data: {"type": "sources",  "sources": [...]}   — books consulted (fired first)
          data: {"type": "chunk",    "text": "..."}       — token chunk
          data: {"type": "done"}                          — stream complete
          data: {"type": "error",    "message": "..."}    — error
        """
        if not req.question.strip():
            raise HTTPException(status_code=400, detail="Question cannot be empty.")

        # Resolve allowed projects for this user (RBAC scope filtering)
        allowed_projects: Optional[list] = None
        try:
            from .rbac import (
                is_rbac_enabled, load_policy, get_accessible_projects,
                get_current_user,
            )
            if is_rbac_enabled(config_path):
                policy = load_policy(config_path)
                username = x_memoria_user or get_current_user(config_path)
                # Gather all project names from books dir
                all_projects = [
                    p.stem.replace("_memory_bank", "")
                    for p in Path(books_dir).glob("*_memory_bank.md")
                    if not p.name.endswith("_draft.md")
                ]
                allowed_projects = get_accessible_projects(username, policy, all_projects)
        except Exception:
            pass   # RBAC optional — never break the answer path

        model_overrides = {k: v for k, v in {
            "model":       req.model,
            "temperature": req.temperature,
            "max_tokens":  req.max_tokens,
            "top_p":       req.top_p,
        }.items() if v is not None}
        return StreamingResponse(
            _stream_global_answer(
                req.question, req.top_k, books_dir, config_path,
                allowed_projects=allowed_projects,
                history=req.history or [],
                model_overrides=model_overrides,
                approved_tools=req.approved_tools,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Search ────────────────────────────────────────────────────────────────

    @app.get("/api/search")
    async def search_books(
        q: str,
        top: int = 5,
        x_memoria_user: Optional[str] = Header(default=None, alias="X-Memoria-User"),
    ):
        """Semantic search across all Memory Banks via ChromaDB."""
        if not q.strip():
            return {"results": [], "query": q}

        try:
            from .search import search as do_search, index_all_books
            db_path = Path(books_dir) / ".chromadb"
            if not db_path.exists():
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: index_all_books(books_dir))
            results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: do_search(q, books_dir, top_k=top)
            )
            if not results:
                return {"results": [], "query": q}

            # ── Post-process results ──────────────────────────────────────────
            import re as _re

            # 1. Drop stale ChromaDB entries for projects whose book was deleted
            books_path = Path(books_dir)
            live_projects = {
                f.stem.replace("_memory_bank", "")
                for f in books_path.glob("*_memory_bank.md")
            }
            results = [r for r in results if r.get("project") in live_projects]

            # 2. Apply RBAC scope filtering
            try:
                from .rbac import (
                    is_rbac_enabled, load_policy, get_accessible_projects,
                    get_current_user,
                )
                if is_rbac_enabled(config_path):
                    policy   = load_policy(config_path)
                    username = x_memoria_user or get_current_user(config_path)
                    allowed  = get_accessible_projects(
                        username, policy, list(live_projects)
                    )
                    if allowed is not None:
                        allowed_set = set(allowed)
                        results = [r for r in results if r.get("project") in allowed_set]
            except Exception:
                pass

            # 2. Strip leftover markdown from the stored text so excerpts are
            #    clean even if the ChromaDB index predates the _clean_text fix.
            def _strip_excerpt(raw: str) -> str:
                t = raw
                t = _re.sub(r'^\[.*?\]\s*', '', t)            # [project] prefix
                t = _re.sub(r'^#{1,6}\s+', '', t, flags=_re.MULTILINE)
                t = _re.sub(r'\*\*([^*]+)\*\*', r'\1', t)
                t = _re.sub(r'\*([^*]+)\*', r'\1', t)
                t = _re.sub(r'_([^_]+)_', r'\1', t)
                t = _re.sub(r'`([^`]+)`', r'\1', t)
                t = _re.sub(r'\n{2,}', ' ', t)                # collapse newlines
                return t.strip()

            cleaned = []
            for r in results:
                # search module returns "text"; mocks / future may use "excerpt"
                raw_text = r.get("text") or r.get("excerpt") or ""
                cleaned.append({
                    "project": r.get("project", ""),
                    "section": r.get("section", ""),
                    "score":   r.get("score", 0),
                    "excerpt": _strip_excerpt(raw_text),
                })

            return {"results": cleaned, "query": q}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/fulltext")
    async def fulltext_search(q: str, top: int = 30):
        """
        Full-text (exact substring) search across all Memory Bank files.
        Case-insensitive. Returns matching lines with surrounding context,
        the section heading the match falls under, and the project name.

        Response shape:
          {
            "results": [
              {
                "project":  "memoria",
                "section":  "Architecture",
                "line":     142,
                "excerpt":  "...two lines of context...",
                "score":    1.0          # reserved for future ranking
              }, ...
            ],
            "query": "original query",
            "total": 12                  # total hits before top truncation
          }
        """
        if not q.strip():
            return {"results": [], "query": q, "total": 0}

        needle = q.lower()
        hits: list[dict] = []
        books_path = Path(books_dir)

        for book_file in sorted(books_path.glob("*_memory_bank.md")):
            project = book_file.stem.replace("_memory_bank", "")
            try:
                lines = book_file.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue

            # Build a heading index: line_number → nearest heading above it
            heading_at: list[str] = [""] * len(lines)
            current_heading = ""
            for idx, ln in enumerate(lines):
                if ln.startswith("#"):
                    current_heading = ln.lstrip("#").strip()
                heading_at[idx] = current_heading

            for idx, ln in enumerate(lines):
                if needle not in ln.lower():
                    continue

                # Excerpt: 1 line before + match + 1 line after
                ctx_start = max(0, idx - 1)
                ctx_end   = min(len(lines), idx + 2)
                excerpt   = "\n".join(lines[ctx_start:ctx_end])

                hits.append({
                    "project": project,
                    "section": heading_at[idx],
                    "line":    idx + 1,
                    "excerpt": excerpt,
                    "score":   1.0,
                })

        total = len(hits)
        return {"results": hits[:top], "query": q, "total": total}

    # ── Graph ─────────────────────────────────────────────────────────────────

    @app.get("/api/graph")
    async def get_graph():
        """Return the Knowledge Graph JSON (nodes + edges)."""
        from .graph import get_graph as load_graph
        return load_graph(books_dir)

    @app.get("/api/graph/query/{project}")
    async def graph_query(project: str):
        """Return node details + all relationships for a specific project."""
        from .graph import get_graph as load_graph, query_project
        g = load_graph(books_dir)
        nodes = g.get("nodes", {})
        if not nodes:
            raise HTTPException(status_code=404, detail="Graph not built yet. Run memoria graph build first.")
        # Case-insensitive lookup
        key = project
        if key not in nodes:
            lower = {k.lower(): k for k in nodes}
            key = lower.get(project.lower())
        if not key:
            raise HTTPException(status_code=404, detail=f"Project '{project}' not found in graph.")
        rels = query_project(key, g)
        # Normalise for the UI: rename 'other' → 'other_project'
        relationships = [
            {
                "other_project": r.get("other", ""),
                "direction":     r.get("direction", ""),
                "relation":      r.get("relation", ""),
                "confidence":    r.get("confidence", 0),
                "reason":        r.get("reason", ""),
            }
            for r in rels
        ]
        return {
            "project":       key,
            "node":          nodes[key],
            "relationships": relationships,
        }

    @app.get("/api/graph/impact/{project}")
    async def graph_impact(project: str):
        """Return transitive impact analysis — what projects depend on this one."""
        from .graph import get_graph as load_graph, impact_analysis
        g = load_graph(books_dir)
        nodes = g.get("nodes", {})
        if not nodes:
            raise HTTPException(status_code=404, detail="Graph not built yet. Run memoria graph build first.")
        key = project
        if key not in nodes:
            lower = {k.lower(): k for k in nodes}
            key = lower.get(project.lower())
        if not key:
            raise HTTPException(status_code=404, detail=f"Project '{project}' not found in graph.")
        dependents = impact_analysis(key, g)
        return {
            "project":    key,
            "description": nodes[key].get("description", ""),
            "dependents": dependents,
        }

    @app.get("/api/graph/file/{project}")
    async def get_file_graph(project: str):
        """Return the intra-project file graph (.file_graph.json)."""
        import json as _json
        safe_proj = _safe(project)
        fg_path = Path(books_dir) / safe_proj / ".file_graph.json"
        if not fg_path.exists():
            raise HTTPException(status_code=404, detail="File graph not built yet.")
        return _json.loads(fg_path.read_text(encoding="utf-8"))

    class FileGraphBuildRequest(BaseModel):
        project:   str
        repo_path: Optional[str] = None

    @app.post("/api/graph/file-build")
    async def build_file_graph_endpoint(req: FileGraphBuildRequest):
        """
        Build the intra-project file graph (.file_graph.json) for one project.

        Body: {"project": str, "repo_path": str | None}

        If repo_path is omitted, it is resolved from the project's metadata:
          1. books_dir/{project}/_meta.json  (field "repo_path" or "source_path")
          2. the project_meta.json store (~/.memoria/project_meta.json)
        """
        from fastapi.responses import JSONResponse
        try:
            repo_path = req.repo_path

            if not repo_path:
                # 1. Per-project _meta.json under the books directory
                safe_proj = _safe(req.project)
                meta_path = Path(books_dir) / safe_proj / "_meta.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        repo_path = meta.get("repo_path") or meta.get("source_path")
                    except Exception:
                        repo_path = None

            if not repo_path:
                # 2. Fall back to the central project_meta.json store
                try:
                    from .meta import get as _meta_get
                    meta = _meta_get(req.project) or {}
                    repo_path = meta.get("repo_path") or meta.get("source_path")
                except Exception:
                    repo_path = None

            if not repo_path:
                return JSONResponse(
                    status_code=500,
                    content={"ok": False, "error": f"No repo path recorded for project '{req.project}'."},
                )

            from .core.file_graph import build_file_graph
            loop = asyncio.get_event_loop()
            graph = await loop.run_in_executor(
                None,
                lambda: build_file_graph(
                    repo_path=repo_path,
                    project_name=req.project,
                    books_dir=books_dir,
                    config_path=config_path,
                ),
            )
            return {"ok": True, "edges": len(graph.get("edges", []))}
        except Exception as exc:
            return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    @app.post("/api/graph/build")
    async def build_graph_endpoint():
        """
        Trigger a full graph build from all Memory Banks.
        Runs the same logic as `memoria graph build` in a thread executor.
        Returns {nodes, edges, updated} when complete.
        """
        from .graph import build_graph as do_build
        loop = asyncio.get_event_loop()
        try:
            graph = await loop.run_in_executor(
                None,
                lambda: do_build(books_dir, config_path),
            )
            return {
                "nodes":   len(graph.get("nodes", {})),
                "edges":   len(graph.get("edges", [])),
                "updated": graph.get("updated", ""),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/graph/build-stream")
    async def build_graph_stream(
        projects: str = None,
        exclude: str = None,
        incremental: bool = False,
    ):
        """
        SSE endpoint — streams real progress while building the graph.

        Query params:
          projects    — comma-separated list of projects to rebuild
          exclude     — comma-separated list of projects to skip
          incremental — if true, only re-analyse changed books

        Event shapes:
          data: {"type": "progress", "message": "...", "step": N, "total": N}
          data: {"type": "done",     "nodes": N, "edges": N}
          data: {"type": "error",    "message": "..."}
        """
        import queue as _queue
        from .graph import build_graph as do_build
        from pathlib import Path as _Path

        project_list = [p.strip() for p in projects.split(",")] if projects else None
        exclude_list = [e.strip() for e in exclude.split(",")] if exclude else None

        # Count books so we can send a meaningful total
        books_count = len(list(_Path(books_dir).glob("*_memory_bank.md")))
        # Each book = 1 extract step; edge detection = 1 more step
        total_steps = books_count + 1

        progress_q: _queue.Queue = _queue.Queue()
        result_box: dict = {}

        def on_progress(msg: str):
            progress_q.put(msg)

        def run_build():
            try:
                graph = do_build(
                    books_dir, config_path,
                    on_progress=on_progress,
                    projects=project_list,
                    exclude=exclude_list,
                    incremental=incremental,
                )
                result_box["graph"] = graph
            except Exception as exc:
                result_box["error"] = str(exc)
            finally:
                progress_q.put(None)  # sentinel

        async def _generate():
            import threading
            loop = asyncio.get_event_loop()
            t = threading.Thread(target=run_build, daemon=True)
            t.start()
            step = 0
            while True:
                try:
                    msg = await loop.run_in_executor(
                        None, lambda: progress_q.get(timeout=0.15)
                    )
                except Exception:
                    await asyncio.sleep(0.05)
                    continue
                if msg is None:
                    break
                step += 1
                yield f"data: {json.dumps({'type': 'progress', 'message': msg, 'step': step, 'total': total_steps})}\n\n"
                await asyncio.sleep(0)
            # Wait for thread to fully finish
            await loop.run_in_executor(None, t.join)
            if "error" in result_box:
                yield f"data: {json.dumps({'type': 'error', 'message': result_box['error']})}\n\n"
            else:
                g = result_box.get("graph", {})
                yield f"data: {json.dumps({'type': 'done', 'nodes': len(g.get('nodes', {})), 'edges': len(g.get('edges', []))})}\n\n"

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Analyze (path scan + SSE streaming) ──────────────────────────────────

    @app.get("/api/scan-path")
    async def scan_path(path: str):
        """
        Check a local path and return what Memoria finds there.

        Returns:
          {type: "single",   path, name}               — one project
          {type: "multi",    projects: [{name, path, is_analyzed, group}]}
          {type: "file",     path, name, ext}           — single file
          {type: "notfound", path}
        """
        from pathlib import Path as _P
        from datetime import datetime as _dt
        import re as _re

        p = _P(path).expanduser()
        if not p.exists():
            return {"type": "notfound", "path": str(p)}

        _SIGNALS = {
            ".git", "package.json", "requirements.txt", "setup.py",
            "pyproject.toml", "Cargo.toml", "go.mod", "Makefile",
            "Dockerfile", "tsconfig.json", ".gitignore",
        }
        _SKIP = {
            ".git", "node_modules", "venv", ".venv", "__pycache__", "dist",
            "build", ".next", "coverage", ".idea", ".vscode", "target",
        }

        def _is_proj(dp: _P) -> bool:
            try:
                return bool({e.name for e in dp.iterdir()} & _SIGNALS)
            except Exception:
                return False

        def _book_info(dp: _P):
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in dp.name)
            bp = _P(books_dir) / f"{safe}_memory_bank.md"
            if bp.exists():
                ts = _dt.fromtimestamp(bp.stat().st_mtime).strftime("%Y-%m-%d")
                return True, ts
            return False, None

        if p.is_file():
            return {"type": "file", "path": str(p),
                    "name": p.stem, "ext": p.suffix.lower()}

        # Single project?
        if _is_proj(p):
            analyzed, ts = _book_info(p)
            return {"type": "single", "path": str(p), "name": p.name,
                    "is_analyzed": analyzed, "last_analyzed": ts}

        # Multi-project scan (2 levels)
        results = []
        seen = set()

        def _scan(dp: _P, depth: int, group: str):
            if str(dp) in seen or dp.name.startswith(".") or dp.name in _SKIP:
                return
            seen.add(str(dp))
            if _is_proj(dp):
                analyzed, ts = _book_info(dp)
                sub = []
                try:
                    sub = [s.name for s in dp.iterdir()
                           if s.is_dir() and not s.name.startswith(".")
                           and s.name not in _SKIP and _is_proj(s)]
                except Exception:
                    pass
                results.append({
                    "name": dp.name, "path": str(dp), "group": group,
                    "is_analyzed": analyzed, "last_analyzed": ts,
                    "sub_projects": sub,
                })
                return
            if depth >= 2:
                return
            try:
                for s in sorted(dp.iterdir()):
                    if s.is_dir():
                        _scan(s, depth + 1, dp.name if depth == 1 else group)
            except Exception:
                pass

        try:
            for s in sorted(p.iterdir()):
                if s.is_dir():
                    _scan(s, 1, s.name)
        except Exception:
            pass

        if len(results) >= 2:
            return {"type": "multi", "projects": results}
        if len(results) == 1:
            r = results[0]
            return {"type": "single", "path": r["path"], "name": r["name"],
                    "is_analyzed": r["is_analyzed"],
                    "last_analyzed": r["last_analyzed"]}
        return {"type": "single", "path": str(p), "name": p.name,
                "is_analyzed": False, "last_analyzed": None}

    @app.get("/api/analyze-stream")
    async def analyze_stream_endpoint(repo: str, context: str = ""):
        """
        SSE endpoint — runs BookGenerator on `repo` and streams progress.

        Query params:
          repo    — absolute path OR git/GitHub URL to analyze
          context — brief project description

        Events:
          {"type": "progress", "message": "..."}
          {"type": "done",     "project": "...", "path": "...", "tokens": "..."}
          {"type": "error",    "message": "..."}
        """
        import queue as _queue
        import threading
        from pathlib import Path as _P

        _is_url = repo.startswith(("http://", "https://", "git@", "git://"))

        # Fast-fail for local paths that don't exist (URLs are handled in run())
        if not _is_url:
            rp = _P(repo).expanduser()
            if not rp.exists():
                async def _err():
                    yield f"data: {json.dumps({'type':'error','message':f'Path not found: {repo}'})}\n\n"
                return StreamingResponse(_err(), media_type="text/event-stream",
                                         headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

        prog_q: _queue.Queue = _queue.Queue()
        result_box: dict = {}

        def on_progress(msg: str):
            prog_q.put(msg)

        def run():
            try:
                import subprocess
                from .generator import BookGenerator

                if _is_url:
                    url = repo
                    # Derive a clean project name from the URL
                    raw_name = url.rstrip("/").split("/")[-1]
                    if raw_name.endswith(".git"):
                        raw_name = raw_name[:-4]
                    clone_dir = _P.home() / ".memoria" / "repos" / raw_name
                    clone_dir.parent.mkdir(parents=True, exist_ok=True)

                    if not clone_dir.exists():
                        on_progress(f"Cloning {url} …")
                        res = subprocess.run(
                            ["git", "clone", "--depth=1", url, str(clone_dir)],
                            capture_output=True, text=True, timeout=180,
                        )
                        if res.returncode != 0:
                            raise RuntimeError(
                                f"git clone failed:\n{res.stderr.strip() or res.stdout.strip()}"
                            )
                        on_progress("Clone complete. Starting analysis…")
                    else:
                        on_progress("Repository already cached — pulling latest changes…")
                        subprocess.run(
                            ["git", "-C", str(clone_dir), "pull", "--ff-only"],
                            capture_output=True, text=True, timeout=60,
                        )
                        on_progress("Up to date. Starting analysis…")

                    rp = clone_dir
                    name = raw_name
                else:
                    rp = _P(repo).expanduser()
                    name = rp.stem if rp.is_file() else rp.name

                gen = BookGenerator(config_path)
                out = gen.generate(
                    repo_path=str(rp),
                    company_context=context or "A software project",
                    output_dir=books_dir,
                    on_progress=on_progress,
                )
                usage = gen.model.usage_summary()
                # Record source path so Update from UI never needs to ask again
                try:
                    from .meta import record as _meta_record
                    _meta_record(name, str(rp), context or "A software project")
                except Exception:
                    pass
                result_box.update({"path": out, "project": name, "tokens": usage or ""})
            except Exception as exc:
                result_box["error"] = str(exc)
            finally:
                prog_q.put(None)

        async def _gen():
            import time as _time
            loop = asyncio.get_event_loop()
            t = threading.Thread(target=run, daemon=True)
            t.start()
            last_heartbeat = _time.monotonic()
            while True:
                try:
                    msg = await loop.run_in_executor(
                        None, lambda: prog_q.get(timeout=0.15))
                except Exception:
                    # Send a heartbeat comment every 4 s so the browser
                    # knows the connection is alive during long AI calls.
                    now = _time.monotonic()
                    if now - last_heartbeat >= 4.0:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now
                    await asyncio.sleep(0.05)
                    continue
                if msg is None:
                    break
                last_heartbeat = _time.monotonic()
                yield f"data: {json.dumps({'type':'progress','message':msg})}\n\n"
                await asyncio.sleep(0)
            await loop.run_in_executor(None, t.join)
            if "error" in result_box:
                yield f"data: {json.dumps({'type':'error','message':result_box['error']})}\n\n"
            else:
                yield f"data: {json.dumps({'type':'done','project':result_box.get('project',''),'path':result_box.get('path',''),'tokens':result_box.get('tokens','')})}\n\n"

        return StreamingResponse(
            _gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Raw book content ──────────────────────────────────────────────────────

    @app.get("/api/books/{project}")
    async def get_book(project: str):
        """Return the raw Markdown content of a Memory Bank."""
        book_path = Path(books_dir) / f"{_safe(project)}_memory_bank.md"
        if not book_path.exists():
            raise HTTPException(status_code=404, detail="Book not found.")
        return {
            "project": project,
            "content": book_path.read_text(encoding="utf-8"),
        }

    @app.get("/api/subunits/{project}")
    async def list_subunits(project: str):
        """Return list of sub-units that have _mb.md files."""
        safe_proj = _safe(project)
        proj_dir = Path(books_dir) / safe_proj
        if not proj_dir.exists():
            return {"project": project, "subunits": []}
        subunits = []
        for mb_path in sorted(proj_dir.rglob("_mb.md")):
            rel = mb_path.parent.relative_to(proj_dir)
            subpath = str(rel).replace("\\", "/")
            name = rel.name if rel.name else "."
            subunits.append({"subpath": subpath, "name": name})
        return {"project": project, "subunits": subunits}

    @app.get("/api/books/{project}/{subpath:path}")
    async def get_subunit_book(project: str, subpath: str):
        """Return the _mb.md content for a sub-unit."""
        def _safe_seg(s: str) -> str:
            return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)
        safe_proj = _safe(project)
        segments = [s for s in subpath.replace("\\", "/").split("/") if s and s != "."]
        if not segments:
            mb_path = Path(books_dir) / safe_proj / "_mb.md"
        else:
            mb_path = Path(books_dir) / safe_proj / Path(*[_safe_seg(s) for s in segments]) / "_mb.md"
        if not mb_path.exists():
            raise HTTPException(status_code=404, detail="Sub-unit book not found.")
        return {
            "project": project,
            "subpath": subpath,
            "content": mb_path.read_text(encoding="utf-8"),
        }

    # ── Update from UI ───────────────────────────────────────────────────────

    @app.get("/api/update-stream/{project}")
    async def update_stream_endpoint(project: str, repo: str = "", mode: str = "smart"):
        """
        SSE endpoint — updates a Memory Bank as a draft, streams progress.

        Query params:
          repo  — override recorded source path (required when no meta recorded yet)
          mode  — "smart" (default): only changed files → cheaper
                  "full": full re-analyze from scratch

        Events:
          {"type": "progress",   "message": "..."}
          {"type": "no_changes", "message": "..."}   — smart mode, nothing changed
          {"type": "done",       "project": "...", "tokens": "..."}
          {"type": "error",      "message": "...", "code": "no_meta"|"not_found"|"error"}
        """
        import queue as _queue
        import threading

        # Resolve repo path: explicit override → recorded meta → auto-detect → error
        from .meta import get as _meta_get
        meta = _meta_get(project) or {}

        def _auto_detect_path(name: str):
            """Try to find the source directory automatically."""
            candidates = []
            # Common roots to search
            roots = [
                Path.home() / "Projects",
                Path.home() / "projects",
                Path.home() / "code",
                Path.home() / "dev",
                Path.home() / "workspace",
                Path("C:/Sahil/Projects"),
                Path("C:/Projects"),
                Path("D:/Projects"),
                Path.home(),
            ]
            for root in roots:
                try:
                    candidate = root / name
                    if candidate.exists() and candidate.is_dir():
                        candidates.append(candidate)
                except Exception:
                    pass
            return str(candidates[0].resolve()) if candidates else None

        if repo:
            repo_path    = str(Path(repo).expanduser().resolve())
            context      = meta.get("context", "A software project")
            last_analyzed = meta.get("last_analyzed", "")
        elif meta.get("repo_path"):
            repo_path    = meta["repo_path"]
            context      = meta.get("context", "A software project")
            last_analyzed = meta.get("last_analyzed", "")
        else:
            # Try to auto-detect before giving up
            detected = _auto_detect_path(project)
            if detected and Path(detected).exists():
                repo_path    = detected
                context      = "A software project"
                last_analyzed = ""
                # Save it immediately so we never ask again
                try:
                    from .meta import record as _meta_record
                    _meta_record(project, detected, context)
                except Exception:
                    pass
            else:
                async def _no_meta():
                    yield f"data: {json.dumps({'type':'error','message':'No source path recorded for this project.','code':'no_meta'})}\n\n"
                return StreamingResponse(_no_meta(), media_type="text/event-stream",
                                         headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

        if not Path(repo_path).exists():
            async def _not_found():
                yield f"data: {json.dumps({'type':'error','message':f'Path not found: {repo_path}','code':'not_found'})}\n\n"
            return StreamingResponse(_not_found(), media_type="text/event-stream",
                                     headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

        # If last_analyzed missing (project pre-dates meta tracking), derive it from
        # the Memory Bank file's own mtime — that's when the last analysis ran.
        if not last_analyzed:
            safe = _safe(project)
            live_path = Path(books_dir) / f"{safe}_memory_bank.md"
            if live_path.exists():
                last_analyzed = datetime.fromtimestamp(
                    live_path.stat().st_mtime
                ).isoformat(timespec="seconds")

        prog_q: _queue.Queue = _queue.Queue()
        result_box: dict = {}

        def on_progress(msg: str):
            prog_q.put(msg)

        def run():
            try:
                from .generator import BookGenerator
                gen = BookGenerator(config_path)

                if mode == "smart":
                    # Read current book for diff context
                    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project)
                    live_path = Path(books_dir) / f"{safe}_memory_bank.md"
                    current_book = live_path.read_text(encoding="utf-8") if live_path.exists() else ""

                    result = gen.smart_update(
                        repo_path=repo_path,
                        current_book=current_book,
                        last_analyzed=last_analyzed,
                        company_context=context,
                        output_dir=books_dir,
                        on_progress=on_progress,
                    )
                    if result["status"] == "no_changes":
                        result_box["no_changes"] = True
                    else:
                        usage = gen.model.usage_summary()
                        result_box.update({"project": project, "tokens": usage or ""})
                else:
                    # Full regeneration
                    gen.draft_update(
                        repo_path=repo_path,
                        company_context=context,
                        output_dir=books_dir,
                        on_progress=on_progress,
                    )
                    usage = gen.model.usage_summary()
                    result_box.update({"project": project, "tokens": usage or ""})

                # If the user supplied an override path, save it now so they
                # never have to type it again.
                if repo and not result_box.get("no_changes"):
                    try:
                        from .meta import record as _meta_record
                        _meta_record(project, repo_path, context)
                    except Exception:
                        pass

            except Exception as exc:
                result_box["error"] = str(exc)
            finally:
                prog_q.put(None)

        async def _gen():
            import time as _time
            loop = asyncio.get_event_loop()
            t = threading.Thread(target=run, daemon=True)
            t.start()
            last_heartbeat = _time.monotonic()
            while True:
                try:
                    msg = await loop.run_in_executor(
                        None, lambda: prog_q.get(timeout=0.15))
                except Exception:
                    now = _time.monotonic()
                    if now - last_heartbeat >= 4.0:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now
                    await asyncio.sleep(0.05)
                    continue
                if msg is None:
                    break
                last_heartbeat = _time.monotonic()
                yield f"data: {json.dumps({'type':'progress','message':msg})}\n\n"
                await asyncio.sleep(0)
            await loop.run_in_executor(None, t.join)
            if "error" in result_box:
                yield f"data: {json.dumps({'type':'error','message':result_box['error'],'code':'error'})}\n\n"
            elif result_box.get("no_changes"):
                yield f"data: {json.dumps({'type':'no_changes','message':'No files have changed since this Memory Bank was last generated.'})}\n\n"
            else:
                yield f"data: {json.dumps({'type':'done','project':result_box.get('project',''),'tokens':result_box.get('tokens','')})}\n\n"

        return StreamingResponse(
            _gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/update-diff/{project}")
    async def update_diff_endpoint(project: str):
        """
        Return the before/after Markdown content so the UI can show a diff.
        Returns 404 if no draft exists yet.
        """
        safe = _safe(project)
        live_path  = Path(books_dir) / f"{safe}_memory_bank.md"
        draft_path = Path(books_dir) / f"{safe}_draft.md"

        if not draft_path.exists():
            raise HTTPException(status_code=404, detail="No draft found. Run update-stream first.")

        before = live_path.read_text(encoding="utf-8") if live_path.exists() else ""
        after  = draft_path.read_text(encoding="utf-8")
        return {"project": project, "before": before, "after": after}

    @app.post("/api/update-apply/{project}")
    async def update_apply_endpoint(project: str):
        """Promote draft to live book (archives current, applies draft)."""
        safe = _safe(project)
        draft_path = Path(books_dir) / f"{safe}_draft.md"
        if not draft_path.exists():
            raise HTTPException(status_code=404, detail="No draft to apply.")
        try:
            from .generator import BookGenerator
            gen = BookGenerator(config_path)
            path = gen.apply_draft(project, books_dir)
            # Refresh last_analyzed so the next smart update compares from now
            from .meta import get as _meta_get, record as _meta_record
            meta = _meta_get(project) or {}
            if meta.get("repo_path"):
                _meta_record(project, meta["repo_path"], meta.get("context", ""))

            # ── Plan vs Reality drift check ────────────────────────────────
            # Runs after every approved update. Zero cost — pure string search.
            # If a plan snapshot exists for this project and drift is detected,
            # write a drift.md file that surfaces in the Review Queue.
            try:
                from .planner import (
                    load_plan_snapshot, check_drift, write_drift_report,
                )
                if load_plan_snapshot(project, books_dir):
                    drift = check_drift(project, books_dir)
                    if drift.get("has_drift"):
                        write_drift_report(project, drift, books_dir)
            except Exception:
                pass  # drift check failure must never break the apply

            return {"ok": True, "path": path}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/update-reject/{project}")
    async def update_reject_endpoint(project: str):
        """Discard the pending draft — live book is unchanged."""
        safe = _safe(project)
        draft_path = Path(books_dir) / f"{safe}_draft.md"
        if draft_path.exists():
            draft_path.unlink()
        return {"ok": True}

    # ── Review Queue ─────────────────────────────────────────────────────────

    @app.get("/api/drafts")
    async def list_drafts():
        """
        Return all pending drafts with metadata.
        Each draft has: project name, draft size, live-book size, timestamps.
        """
        books_path = Path(books_dir)
        if not books_path.exists():
            return {"drafts": []}

        drafts = []

        # Standard update drafts
        for draft in books_path.glob("*_draft.md"):
            project = draft.stem.replace("_draft", "")
            live = books_path / f"{project}_memory_bank.md"
            drafts.append({
                "project":       project,
                "draft_path":    str(draft),
                "draft_size":    draft.stat().st_size,
                "draft_updated": datetime.fromtimestamp(draft.stat().st_mtime).isoformat(),
                "has_live":      live.exists(),
                "live_size":     live.stat().st_size if live.exists() else 0,
                "draft_type":    "update",
            })

        # Plan drift reports  (written by check_drift → write_drift_report)
        for drift_file in books_path.glob("*_drift.md"):
            project = drift_file.stem.replace("_drift", "")
            live = books_path / f"{project}_memory_bank.md"
            drafts.append({
                "project":       project,
                "draft_path":    str(drift_file),
                "draft_size":    drift_file.stat().st_size,
                "draft_updated": datetime.fromtimestamp(drift_file.stat().st_mtime).isoformat(),
                "has_live":      live.exists(),
                "live_size":     live.stat().st_size if live.exists() else 0,
                "draft_type":    "drift",
            })

        drafts.sort(key=lambda d: d["draft_updated"], reverse=True)
        return {"drafts": drafts}

    # ── Pull / Sync from UI ──────────────────────────────────────────────────

    @app.get("/api/sources")
    async def list_sources():
        """
        Return configured MCP sources with last-pull state.
        """
        try:
            from .mcp_sources import load_sources
            from .pull_state import get_all_states
            sources = load_sources(config_path)
            states = get_all_states()

            result = []
            for src in sources:
                name = src.get("name", "unnamed")
                state = states.get(name, {})
                result.append({
                    "name":         name,
                    "server":       src.get("server", ""),
                    "schedule":     src.get("schedule", ""),
                    "incremental":  src.get("incremental", False),
                    "context":      src.get("context", ""),
                    "last_pull":    state.get("last_pull", ""),
                    "last_book":    state.get("last_book", ""),
                    "pull_count":   len(src.get("pull", [])),
                })
            return {"sources": result}
        except Exception as exc:
            return {"sources": [], "error": str(exc)}

    @app.get("/api/pull-stream/{source_name}")
    async def pull_stream_endpoint(source_name: str):
        """
        SSE endpoint — pulls from an MCP source and streams progress.

        Events:
          {"type": "progress", "message": "..."}
          {"type": "done",     "source": "...", "tokens": "...", "book": "..."}
          {"type": "error",    "message": "..."}
        """
        import queue as _queue
        import threading

        prog_q: _queue.Queue = _queue.Queue()
        result_box: dict = {}

        def on_progress(msg: str):
            prog_q.put(msg)

        def run():
            try:
                from .mcp_sources import load_sources, pull_source
                from .pull_state import get_last_pull, set_last_pull
                from .generator import BookGenerator

                sources = load_sources(config_path)
                source = next((s for s in sources if s.get("name") == source_name), None)
                if not source:
                    result_box["error"] = f"Source '{source_name}' not found in config.yaml"
                    return

                on_progress(f"Connecting to {source_name}…")

                # Determine since timestamp for incremental pulls
                since = None
                if source.get("incremental"):
                    since = get_last_pull(source_name)
                    if since:
                        on_progress(f"Incremental pull — changes since {since.strftime('%d %b %Y %H:%M')}")

                # Pull content from MCP server
                on_progress(f"Pulling data from {source_name}…")
                import asyncio as _aio
                loop = _aio.new_event_loop()
                content = loop.run_until_complete(
                    __import__('memoria.mcp_sources', fromlist=['_pull_async'])._pull_async(source, since=since)
                )
                loop.close()

                if not content or not content.strip():
                    result_box["error"] = f"No content returned from {source_name}"
                    return

                on_progress(f"Got {len(content):,} chars. Generating Memory Bank…")

                # Generate book from pulled content
                gen = BookGenerator(config_path)
                context = source.get("context", f"Content from {source_name}")

                # Write pulled content to a SYSTEM temp dir (not books_dir) and
                # name it after the source so the Memory Bank gets a sensible name.
                # Using books_dir caused temp filenames like "tmp1ees_n0d" to become
                # permanent Memory Bank names — a bug now fixed here.
                import tempfile
                safe_name = "".join(
                    c if c.isalnum() or c in "-_" else "_"
                    for c in source_name
                )
                tmp_path = Path(tempfile.gettempdir()) / f"{safe_name}_pull.md"
                tmp_path.write_text(content, encoding="utf-8")

                try:
                    out = gen.generate(
                        repo_path=str(tmp_path),
                        company_context=context,
                        output_dir=books_dir,
                        on_progress=on_progress,
                    )
                    usage = gen.model.usage_summary()
                    # Record successful pull
                    from datetime import datetime as _dt
                    set_last_pull(source_name, book_path=out)
                    result_box.update({
                        "source": source_name,
                        "tokens": usage or "",
                        "book": out,
                    })
                finally:
                    Path(tmp_path).unlink(missing_ok=True)

            except Exception as exc:
                result_box["error"] = str(exc)
            finally:
                prog_q.put(None)

        async def _gen():
            import time as _time
            loop = asyncio.get_event_loop()
            t = threading.Thread(target=run, daemon=True)
            t.start()
            last_heartbeat = _time.monotonic()
            while True:
                try:
                    msg = await loop.run_in_executor(
                        None, lambda: prog_q.get(timeout=0.15))
                except Exception:
                    now = _time.monotonic()
                    if now - last_heartbeat >= 4.0:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now
                    await asyncio.sleep(0.05)
                    continue
                if msg is None:
                    break
                last_heartbeat = _time.monotonic()
                yield f"data: {json.dumps({'type':'progress','message':msg})}\n\n"
                await asyncio.sleep(0)
            await loop.run_in_executor(None, t.join)
            if "error" in result_box:
                yield f"data: {json.dumps({'type':'error','message':result_box['error']})}\n\n"
            else:
                yield f"data: {json.dumps({'type':'done','source':result_box.get('source',''),'tokens':result_box.get('tokens',''),'book':result_box.get('book','')})}\n\n"

        return StreamingResponse(
            _gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Sources management ────────────────────────────────────────────────────

    @app.post("/api/sources/add")
    async def add_source(request: Request):
        """
        Add or update an MCP source in config.yaml mcp_sources:.
        Body: { name, server, env, pull, schedule, incremental, context }
        """
        import yaml
        data = await request.json()
        name = (data.get("name") or "").strip()
        if not name:
            from fastapi import HTTPException
            raise HTTPException(400, "name is required")

        cfg_path = _resolve_config_path(config_path)
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            cfg = {}

        sources: list = cfg.get("mcp_sources") or []

        # Build source entry
        entry: dict = {"name": name}
        if data.get("server"):
            entry["server"] = data["server"]
        if data.get("env"):
            entry["env"] = data["env"]
        if data.get("headers"):
            entry["headers"] = data["headers"]
        if data.get("pull"):
            entry["pull"] = data["pull"]
        if data.get("schedule"):
            entry["schedule"] = data["schedule"]
        if data.get("incremental"):
            entry["incremental"] = bool(data["incremental"])
        if data.get("context"):
            entry["context"] = data["context"]

        # Replace existing or append
        replaced = False
        for i, s in enumerate(sources):
            if s.get("name") == name:
                sources[i] = entry
                replaced = True
                break
        if not replaced:
            sources.append(entry)

        cfg["mcp_sources"] = sources
        cfg_path.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8")

        # Also write env vars to ~/.memoria/.env
        env_vars: dict = data.get("env_values") or {}
        if env_vars:
            env_path = Path.home() / ".memoria" / ".env"
            env_lines: list = []
            if env_path.exists():
                env_lines = env_path.read_text(encoding="utf-8").splitlines()
            for k, v in env_vars.items():
                if not v or v.startswith("…"):
                    continue
                found = False
                for i, line in enumerate(env_lines):
                    if line.startswith(f"{k}=") or line.startswith(f"{k} ="):
                        env_lines[i] = f"{k}={v}"
                        found = True
                        break
                if not found:
                    env_lines.append(f"{k}={v}")
            env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
            # Reload into os.environ immediately so tokens are available without restart
            try:
                from dotenv import load_dotenv as _ld
                _ld(env_path, override=True)
            except ImportError:
                pass

        return {"ok": True, "replaced": replaced, "name": name}

    @app.get("/api/mcp/tools")
    async def mcp_list_tools(source: str = ""):
        """
        List available tools from configured MCP sources.
        ?source=name  → one source only.
        No param      → all configured sources.
        Returns {ok, sources: [{source, tools: [{name, description, inputSchema}]}]}
        """
        from .mcp_sources import list_tools_async, load_sources as _ls
        sources_cfg = _ls(config_path)
        targets = [s for s in sources_cfg if s.get("name") == source] if source else sources_cfg
        results = []
        for s in targets:
            name = s.get("name", "")
            try:
                tools = await asyncio.wait_for(
                    list_tools_async(name, config_path=config_path),
                    timeout=12.0,
                )
                results.append({"source": name, "tools": tools})
            except Exception as exc:
                results.append({"source": name, "tools": [], "error": str(exc)})
        return {"ok": True, "sources": results}

    class MCPAgentImportRequest(BaseModel):
        sources: list[str]   # source names to query, e.g. ["github", "jira"]
        query: str           # natural-language request

    @app.post("/api/mcp/agent-import")
    async def mcp_agent_import(req: MCPAgentImportRequest):
        """
        Agentic MCP import: given a natural-language query and a list of sources,
        list tools, ask the LLM which tool(s) to call and with what args, execute them,
        and return the combined result as ticket text.
        """
        import json as _json
        from .mcp_sources import list_tools_async, call_tool_async
        from .models import ModelProvider

        if not req.sources:
            return JSONResponse(status_code=400, content={"ok": False, "error": "No sources specified"})
        if not req.query.strip():
            return JSONResponse(status_code=400, content={"ok": False, "error": "Query is empty"})

        # 1. Collect tool schemas from all requested sources
        tool_catalog: list[dict] = []
        list_errors: list[str] = []
        for source_name in req.sources:
            try:
                tools = await asyncio.wait_for(
                    list_tools_async(source_name, config_path=config_path),
                    timeout=12.0,
                )
                for t in tools:
                    tool_catalog.append({
                        "source": source_name,
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "inputSchema": t.get("inputSchema", {}),
                    })
            except Exception as exc:
                list_errors.append(f"{source_name}: {exc}")

        if not tool_catalog:
            err = "; ".join(list_errors) or "No tools available"
            return JSONResponse(status_code=502, content={"ok": False, "error": f"Could not list tools — {err}"})

        # 2. Ask LLM to select tool(s) and generate args
        tool_list_text = "\n\n".join(
            f"Source: {t['source']}\nTool: {t['name']}\nDescription: {t['description']}\n"
            f"Input schema: {_json.dumps(t['inputSchema'], indent=2)}"
            for t in tool_catalog
        )
        system = (
            "You are an MCP tool selector. Given a list of available tools and a user request, "
            "decide which tool(s) to call and what arguments to pass.\n\n"
            "Respond ONLY with a valid JSON array — no explanation, no markdown fences:\n"
            '[{"source": "<source>", "tool": "<tool_name>", "args": {<arg_key>: <value>}}]\n\n'
            "Rules:\n"
            "- Only use tools from the list provided\n"
            "- Fill required args from the user request; omit optional args you cannot determine\n"
            "- Maximum 3 tool calls\n"
            "- If the request is ambiguous, pick the most relevant single tool"
        )
        user_prompt = (
            f"Available tools:\n\n{tool_list_text}\n\n"
            f"User request: {req.query}\n\n"
            "Return the JSON array of tool calls:"
        )

        model = ModelProvider(config_path)
        try:
            llm_response = await model.async_complete(system, user_prompt)
        except Exception as exc:
            return JSONResponse(status_code=502, content={"ok": False, "error": f"LLM call failed: {exc}"})

        # Parse LLM response — strip markdown fences if present
        raw = llm_response.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            raw = raw.rstrip("`").strip()
        try:
            tool_calls: list[dict] = _json.loads(raw)
            if not isinstance(tool_calls, list):
                raise ValueError("Expected a JSON array")
        except Exception as exc:
            return JSONResponse(status_code=502, content={
                "ok": False,
                "error": f"LLM returned unexpected format: {exc}",
                "raw": llm_response[:500],
            })

        # 3. Execute each tool call
        results: list[str] = []
        exec_errors: list[str] = []
        for call in tool_calls[:3]:
            source_name = call.get("source", "")
            tool_name = call.get("tool", "")
            args = call.get("args") or {}
            if not source_name or not tool_name:
                continue
            try:
                text = await asyncio.wait_for(
                    call_tool_async(server=source_name, tool=tool_name, args=args, config_path=config_path),
                    timeout=15.0,
                )
                results.append(f"[{source_name} / {tool_name}]\n{text}")
            except Exception as exc:
                exec_errors.append(f"{source_name}/{tool_name}: {exc}")

        if not results:
            err = "; ".join(exec_errors) or "No results returned"
            return JSONResponse(status_code=502, content={"ok": False, "error": f"Tool execution failed — {err}"})

        combined = "\n\n---\n\n".join(results)
        return {
            "ok": True,
            "result": combined,
            "calls": [{"source": c.get("source"), "tool": c.get("tool")} for c in tool_calls[:3]],
            "warnings": exec_errors or None,
        }

    class MCPFetchRequest(BaseModel):
        server: str        # config name (e.g. "github") or direct command string
        tool: str          # MCP tool name
        args: dict = {}    # tool arguments

    @app.post("/api/mcp/fetch")
    async def mcp_fetch(req: MCPFetchRequest):
        """
        On-demand MCP tool call — safe to call from within FastAPI's event loop.
        Used by the context assembler and adaptive agent to query live data
        (Jira ticket details, GitHub branch status, Figma frame) per work item.
        """
        try:
            from .mcp_sources import call_tool_async
            result = await call_tool_async(
                server=req.server,
                tool=req.tool,
                args=req.args,
                config_path=config_path,
            )
            return {"ok": True, "result": result}
        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=502, content={"ok": False, "error": str(exc)})

    @app.delete("/api/sources/{source_name}")
    async def delete_source(source_name: str):
        """Remove an MCP source from config.yaml mcp_sources:."""
        import yaml
        cfg_path = _resolve_config_path(config_path)
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            cfg = {}

        sources: list = cfg.get("mcp_sources") or []
        new_sources = [s for s in sources if s.get("name") != source_name]
        if len(new_sources) == len(sources):
            from fastapi import HTTPException
            raise HTTPException(404, f"Source '{source_name}' not found")

        cfg["mcp_sources"] = new_sources
        cfg_path.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8")
        return {"ok": True, "deleted": source_name}

    @app.delete("/api/projects/{project_name}")
    async def delete_project(project_name: str):
        """
        Delete a project completely:
          1. Memory Bank .md file (+ draft if present)
          2. Vector store embeddings (ChromaDB / Pinecone / Weaviate)
          3. Knowledge graph node + edges
          4. Project metadata entry
        """
        from fastapi import HTTPException
        from .graph import get_graph, GRAPH_FILE
        import re

        safe = re.sub(r"[^\w\-]", "_", project_name)
        book  = Path(books_dir) / f"{safe}_memory_bank.md"
        draft = Path(books_dir) / f"{safe}_memory_bank_draft.md"

        if not book.exists():
            raise HTTPException(404, f"Memory Bank not found: {project_name}")

        # ── 1. Delete Memory Bank files ───────────────────────────────────────
        book.unlink()
        if draft.exists():
            draft.unlink()

        # ── 2. Purge vector store embeddings ─────────────────────────────────
        # Must happen BEFORE the file is gone so the provider can be resolved,
        # but the .md is already deleted above so we pass books_dir directly.
        try:
            from .search import _get_provider
            provider = _get_provider(books_dir)
            provider.delete_project(project_name)
        except Exception:
            pass  # non-fatal — stale entries are filtered at query time anyway

        # ── 3. Remove node from cached knowledge graph ────────────────────────
        graph_path = Path(books_dir) / GRAPH_FILE
        if graph_path.exists():
            try:
                import json as _json
                g = _json.loads(graph_path.read_text(encoding="utf-8"))
                g.get("nodes", {}).pop(project_name, None)
                g["edges"] = [
                    e for e in g.get("edges", [])
                    if e.get("source") != project_name and e.get("target") != project_name
                ]
                graph_path.write_text(_json.dumps(g, indent=2), encoding="utf-8")
            except Exception:
                pass

        # ── 4. Remove from project metadata ──────────────────────────────────
        try:
            from .meta import _load, _save
            m = _load()
            m.pop(project_name, None)
            _save(m)
        except Exception:
            pass

        return {"ok": True, "deleted": project_name}

    # ── Settings ─────────────────────────────────────────────────────────────

    @app.get("/api/settings")
    async def get_settings():
        """
        Return current config + masked env keys so the Settings panel can
        show what's configured without exposing full secrets.
        """
        import yaml
        import os
        from dotenv import load_dotenv as _ld
        _ld(override=True)
        _ld(Path.home() / ".memoria" / ".env", override=True)

        cfg = _load_config(config_path)

        def _mask(val: str) -> str:
            if not val or val.startswith("your_"):
                return ""
            if len(val) <= 8:
                return "****"
            return val[:4] + "…" + val[-4:]

        keys = {
            "GEMINI_API_KEY":       _mask(os.environ.get("GEMINI_API_KEY", "")),
            "OPENAI_API_KEY":       _mask(os.environ.get("OPENAI_API_KEY", "")),
            "ANTHROPIC_API_KEY":    _mask(os.environ.get("ANTHROPIC_API_KEY", "")),
            "AWS_BEARER_TOKEN_BEDROCK": _mask(os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")),
            "AWS_ACCESS_KEY_ID":    _mask(os.environ.get("AWS_ACCESS_KEY_ID", "")),
            "AWS_SECRET_ACCESS_KEY": _mask(os.environ.get("AWS_SECRET_ACCESS_KEY", "")),
            "AWS_REGION_NAME":      os.environ.get("AWS_REGION_NAME", "us-east-1"),
            "PINECONE_API_KEY":    _mask(os.environ.get("PINECONE_API_KEY", "")),
            "WEAVIATE_API_KEY":    _mask(os.environ.get("WEAVIATE_API_KEY", "")),
        }

        # Vector store config
        vs_cfg = cfg.get("vector_store", {})
        vector_store = {
            "provider":            vs_cfg.get("provider", "chromadb"),
            "pinecone_api_key":    _mask(os.environ.get("PINECONE_API_KEY", "") or vs_cfg.get("pinecone_api_key", "")),
            "pinecone_index":      vs_cfg.get("pinecone_index", "memoria"),
            "pinecone_region":     vs_cfg.get("pinecone_region", "us-east-1"),
            "weaviate_url":        vs_cfg.get("weaviate_url", ""),
            "weaviate_api_key":    _mask(os.environ.get("WEAVIATE_API_KEY", "") or vs_cfg.get("weaviate_api_key", "")),
            "weaviate_collection": vs_cfg.get("weaviate_collection", "MemoriaBooks"),
        }

        return {
            "model":             cfg.get("model", ""),
            "max_tokens_per_file": cfg.get("max_tokens_per_file", 2000),
            "max_tokens_output": cfg.get("max_tokens_output", 4000),
            "books_dir":         cfg.get("books_dir", "books"),
            "keys":              keys,
            "vector_store":      vector_store,
            "config_path":       str(_resolve_config_path(config_path)),
            "env_path":          str(Path.home() / ".memoria" / ".env"),
        }

    class SettingsUpdate(BaseModel):
        model: str = ""
        max_tokens_per_file: int = 0
        max_tokens_output: int = 0
        keys: dict = {}  # {"GEMINI_API_KEY": "new_value", ...}
        vector_store: dict = {}  # {"provider": "chromadb|pinecone|weaviate", ...}

    @app.post("/api/settings")
    async def save_settings(req: SettingsUpdate):
        """
        Update config.yaml model/token settings and .env API keys.
        Only non-empty fields are written (partial update).
        """
        import yaml

        # ── Update config.yaml ────────────────────────────────────────────
        cfg_path = _resolve_config_path(config_path)
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            cfg = {}

        changed_cfg = False
        if req.model and req.model != cfg.get("model", ""):
            cfg["model"] = req.model
            changed_cfg = True
        if req.max_tokens_per_file and req.max_tokens_per_file != cfg.get("max_tokens_per_file"):
            cfg["max_tokens_per_file"] = req.max_tokens_per_file
            changed_cfg = True
        if req.max_tokens_output and req.max_tokens_output != cfg.get("max_tokens_output"):
            cfg["max_tokens_output"] = req.max_tokens_output
            changed_cfg = True

        # ── Update vector_store config ─────────────────────────────────────
        if req.vector_store and req.vector_store.get("provider"):
            vs = req.vector_store
            vs_block = {"provider": vs["provider"]}
            if vs["provider"] == "pinecone":
                vs_block["pinecone_index"] = vs.get("pinecone_index", "memoria")
                vs_block["pinecone_region"] = vs.get("pinecone_region", "us-east-1")
            elif vs["provider"] == "weaviate":
                vs_block["weaviate_url"] = vs.get("weaviate_url", "")
                vs_block["weaviate_collection"] = vs.get("weaviate_collection", "MemoriaBooks")

            if cfg.get("vector_store") != vs_block:
                cfg["vector_store"] = vs_block
                changed_cfg = True

        if changed_cfg:
            cfg_path.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8")
            # Invalidate vector provider cache so next search uses new config
            try:
                from .search import _provider_cache
                _provider_cache.clear()
            except Exception:
                pass
            # Also sync the user-level config so both stay in agreement
            user_cfg = Path.home() / ".memoria" / "config.yaml"
            if user_cfg.exists() and user_cfg.resolve() != cfg_path.resolve():
                try:
                    ucfg = yaml.safe_load(user_cfg.read_text(encoding="utf-8")) or {}
                    if req.model:
                        ucfg["model"] = req.model
                    if req.max_tokens_per_file:
                        ucfg["max_tokens_per_file"] = req.max_tokens_per_file
                    if req.max_tokens_output:
                        ucfg["max_tokens_output"] = req.max_tokens_output
                    if req.vector_store and req.vector_store.get("provider"):
                        ucfg["vector_store"] = cfg["vector_store"]
                    user_cfg.write_text(yaml.dump(ucfg, default_flow_style=False, sort_keys=False), encoding="utf-8")
                except Exception:
                    pass  # best-effort sync

        # ── Update .env ───────────────────────────────────────────────────
        env_path = Path.home() / ".memoria" / ".env"
        changed_env = False
        if req.keys:
            env_lines = []
            if env_path.exists():
                env_lines = env_path.read_text(encoding="utf-8").splitlines()

            for key, value in req.keys.items():
                if not value or value.startswith("…") or "…" in value:
                    continue  # skip masked/empty values — user didn't change them
                # Replace existing line or append
                found = False
                for i, line in enumerate(env_lines):
                    if line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="):
                        env_lines[i] = f"{key}={value}"
                        found = True
                        break
                if not found:
                    env_lines.append(f"{key}={value}")
                changed_env = True

            if changed_env:
                env_path.parent.mkdir(parents=True, exist_ok=True)
                env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
                # Immediately reload into current process
                from dotenv import load_dotenv as _ld
                _ld(str(env_path), override=True)

        return {"ok": True, "config_changed": changed_cfg, "env_changed": changed_env}

    @app.get("/api/vector-store/info")
    async def vector_store_info():
        """Return current vector store provider info and status."""
        try:
            from .search import get_provider_info
            info = get_provider_info(books_dir)
            return info
        except Exception as e:
            return {"provider": "chromadb", "status": "error", "error": str(e)}

    @app.post("/api/vector-store/reindex")
    async def vector_store_reindex():
        """Re-index all books into the configured vector store."""
        try:
            from .search import index_all_books
            loop = asyncio.get_event_loop()
            count = await loop.run_in_executor(None, lambda: index_all_books(books_dir))
            return {"ok": True, "indexed": count}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/vector-store/cleanup")
    async def vector_store_cleanup():
        """
        Purge vector store embeddings for projects whose Memory Bank files
        no longer exist. Fixes ghost sources from deleted projects.
        """
        try:
            from .search import purge_orphaned
            loop   = asyncio.get_event_loop()
            purged = await loop.run_in_executor(None, lambda: purge_orphaned(books_dir))
            return {"ok": True, "purged": purged, "count": len(purged)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/pull-github/{project}")
    async def pull_github(project: str):
        """
        Pull recent GitHub activity (commits + PRs) for a project.
        Requires the project's repo_path in its Memory Bank metadata,
        or falls back to CWD-based discovery.
        """
        try:
            from .github_source import generate_github_context
            safe_name = _safe(project)

            # Try to find repo path from the Memory Bank or CWD
            repo_path = None

            # Check if the book has a source_path header
            book_path = Path(books_dir) / f"{safe_name}_memory_bank.md"
            if book_path.exists():
                content = book_path.read_text(encoding="utf-8")
                # Look for repo path in first few lines
                for line in content.splitlines()[:10]:
                    if "source:" in line.lower() or "path:" in line.lower():
                        path_match = re.search(r'[`"]?([A-Za-z]:\\[^`"]+|/[^`"]+)[`"]?', line)
                        if path_match:
                            candidate = Path(path_match.group(1))
                            if (candidate / ".git").exists():
                                repo_path = str(candidate)
                                break

            # Fallback: check if project name matches a known directory
            if not repo_path:
                for candidate in [
                    Path.cwd() / project,
                    Path.cwd().parent / project,
                    Path.home() / "Projects" / project,
                ]:
                    if (candidate / ".git").exists():
                        repo_path = str(candidate)
                        break

            if not repo_path:
                return {"ok": False, "error": f"Could not find git repo for '{project}'. Ensure the project directory contains a .git folder."}

            loop = asyncio.get_event_loop()
            result_path = await loop.run_in_executor(
                None,
                lambda: generate_github_context(repo_path, project, books_dir)
            )

            if result_path:
                return {"ok": True, "path": result_path, "project": project}
            else:
                return {"ok": False, "error": "No recent activity found (no commits or PRs in the configured timeframe)."}

        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/tickets")
    async def get_tickets():
        """
        Return ticket/issue references found across all Memory Banks.
        Provides a cross-reference index: which tickets appear in which projects.
        """
        try:
            from .tickets import scan_all_books, build_ticket_index
            loop = asyncio.get_event_loop()
            index = await loop.run_in_executor(None, lambda: build_ticket_index(books_dir))
            by_project = await loop.run_in_executor(None, lambda: scan_all_books(books_dir))

            return {
                "ticket_index": index,  # ticket_id → [projects]
                "by_project": {k: [t["id"] for t in v] for k, v in by_project.items()},
                "total_tickets": len(index),
            }
        except Exception as e:
            return {"ticket_index": {}, "by_project": {}, "total_tickets": 0, "error": str(e)}

    @app.get("/api/attribution")
    async def get_attribution():
        """Return stored attribution chains summary."""
        try:
            from .attribution import get_chains_summary
            return get_chains_summary(books_dir)
        except Exception as e:
            return {"total_chains": 0, "chains": [], "error": str(e)}

    @app.post("/api/attribution/build")
    async def build_attribution():
        """Rebuild attribution chains from all available data."""
        try:
            from .attribution import build_chains
            loop = asyncio.get_event_loop()
            chains = await loop.run_in_executor(None, lambda: build_chains(books_dir))
            return {"ok": True, "total_chains": len(chains)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/api/tickets/resolve")
    async def resolve_ticket(ticket_id: str):
        """Resolve a single ticket ID and return its cached metadata."""
        try:
            from .tickets import detect_tickets, resolve_tickets
            tickets = [{"id": ticket_id, "type": "jira", "project": "", "number": 0}]
            resolved = resolve_tickets(tickets, config_path)
            return resolved[0] if resolved else {"id": ticket_id, "error": "Not found"}
        except Exception as e:
            return {"id": ticket_id, "error": str(e)}

    @app.post("/api/test-model")
    async def test_model():
        """
        Quick connectivity check — sends a trivial prompt and reports success/failure.
        Returns latency_ms so the UI can show a green badge with response time.
        """
        import litellm
        import time
        from dotenv import load_dotenv as _ld
        _ld(override=True)
        _ld(Path.home() / ".memoria" / ".env", override=True)

        cfg = _load_config(config_path)
        model = cfg.get("model", "")
        if not model:
            return {"ok": False, "error": "No model configured. Enter a model name and save first."}

        loop = asyncio.get_event_loop()
        t0 = time.monotonic()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: litellm.completion(
                    model=model,
                    messages=[{"role": "user", "content": "Say OK"}],
                    max_tokens=5,
                ),
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            text = response.choices[0].message.content or ""
            return {"ok": True, "model": model, "response": text.strip(), "latency_ms": latency_ms}
        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=502,
                content={"ok": False, "model": model, "error": str(exc)},
            )

    # Alias used by onboarding wizard and roadmap spec
    app.post("/api/settings/test")(test_model)

    # ── Adaptive Work Agent ──────────────────────────────────────────────────

    class WorkflowStartRequest(BaseModel):
        ticket_text: str
        project_name: str
        thread_id: Optional[str] = None
        skip_external_fetch: bool = False

    class WorkflowChatRequest(BaseModel):
        thread_id: str
        message: str
        history: list = []

    class WorkflowStopRequest(BaseModel):
        thread_id: str

    @app.post("/api/workflow/start")
    async def workflow_start(req: WorkflowStartRequest):
        """
        Launch the adaptive work agent for a ticket.
        Returns thread_id — client uses it to subscribe to SSE events and to approve artifacts.
        Streams events via GET /api/workflow/stream/{thread_id}.
        """
        try:
            from .agent.graph import start_workflow
            thread_id, _q = await start_workflow(
                ticket_text=req.ticket_text,
                project_name=req.project_name,
                books_dir=books_dir,
                config_path=config_path,
                thread_id=req.thread_id,
                skip_external_fetch=req.skip_external_fetch,
            )
            return {"ok": True, "thread_id": thread_id}
        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    @app.get("/api/workflow/stream/{thread_id}")
    async def workflow_stream(thread_id: str):
        """
        SSE stream for a running workflow thread.
        Events: context_analyzing, context_analyzed, actions_available, done, error
        """
        import json as _json
        from fastapi.responses import StreamingResponse
        from .agent.graph import _get_event_queue

        async def _gen():
            q = _get_event_queue(thread_id)
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {_json.dumps(event)}\n\n"
                    if event.get("type") in ("done", "error", "stopped"):
                        break
                except asyncio.TimeoutError:
                    yield "data: {\"type\": \"ping\"}\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @app.post("/api/workflow/chat")
    async def workflow_chat(req: WorkflowChatRequest):
        """
        Stream a context-aware chat response for a work item.
        Uses the assembled Memory Bank context from analyze_context as the system prompt.
        The user drives all SDLC tasks: code, tests, analysis, PR descriptions, planning.
        """
        from fastapi.responses import StreamingResponse
        from .agent.graph import stream_work_chat
        return StreamingResponse(
            stream_work_chat(req.thread_id, req.message, req.history),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    class WorkflowActionsConfirmRequest(BaseModel):
        thread_id: str
        approved_action_ids: list[str]

    @app.post("/api/workflow/actions/confirm")
    async def workflow_actions_confirm(req: WorkflowActionsConfirmRequest):
        """
        Execute the write-back actions the user selected in the confirmation UI.
        Pushes action_done + done events to the thread's SSE queue so the
        re-subscribed frontend receives live progress.
        Pass approved_action_ids=[] to skip all actions.
        """
        try:
            from .agent.graph import run_confirmed_actions
            result = await run_confirmed_actions(req.thread_id, req.approved_action_ids)
            return result
        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    class WorkflowWriteLocalRequest(BaseModel):
        path: str
        content: str

    @app.post("/api/workflow/write-local")
    async def workflow_write_local(req: WorkflowWriteLocalRequest):
        """Write a file to the local filesystem. Path may be absolute or relative to cwd."""
        import pathlib
        try:
            p = pathlib.Path(req.path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(req.content, encoding="utf-8")
            return {"ok": True, "path": str(p.resolve())}
        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    @app.post("/api/workflow/stop")
    async def workflow_stop(req: WorkflowStopRequest):
        """Stop a running workflow, closing the SSE queue and marking it stopped."""
        try:
            from .agent.graph import stop_workflow
            stop_workflow(req.thread_id)
            return {"ok": True}
        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    @app.get("/api/dashboard/sync-tickets")
    async def dashboard_sync_tickets():
        """
        Fetch assigned open tickets from configured Jira/Linear MCP sources.
        Tries known search tool name patterns; returns raw results per source.
        """
        from .mcp_sources import list_tools_async, call_tool_async, load_sources as _ls

        _SEARCH_PATTERNS = [
            "jira_search", "search_issues", "jira_list_issues", "list_issues",
            "jql_search", "jira_get_issues", "get_issues", "linear_search_issues",
            "atlassian_jira_search", "jira_jql",
        ]
        _JIRA_JQL = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"
        _LINEAR_FILTER: dict = {"assignee": "me", "completedAt": None}

        sources_cfg = _ls(config_path)
        results: list[dict] = []
        errors: list[str] = []

        for source in sources_cfg:
            name = source.get("name", "")
            try:
                tools = await asyncio.wait_for(
                    list_tools_async(name, config_path=config_path),
                    timeout=12.0,
                )
                tool_names = {t["name"] for t in tools}

                search_tool = next((p for p in _SEARCH_PATTERNS if p in tool_names), None)
                if not search_tool:
                    continue  # this source has no known search tool

                # Build args depending on tool name
                if "linear" in search_tool:
                    args: dict = _LINEAR_FILTER
                elif "jql" in search_tool or "jira" in search_tool:
                    args = {"jql": _JIRA_JQL, "max_results": 30}
                else:
                    args = {"query": "assignee:me status:open", "max_results": 30}

                raw = await asyncio.wait_for(
                    call_tool_async(server=name, tool=search_tool, args=args, config_path=config_path),
                    timeout=20.0,
                )
                results.append({"source": name, "tool": search_tool, "raw": raw or ""})
            except Exception as exc:
                errors.append(f"{name}: {exc}")

        if not results and errors:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=502, content={"ok": False, "errors": errors})

        return {"ok": True, "results": results, "errors": errors if errors else None}

    @app.get("/api/context/{ticket_id}")
    async def get_context(ticket_id: str, project: str = "", text: str = ""):
        """
        Assemble context for a ticket — capability gaps + relevant Memory Banks.
        ticket_id: used to fetch from MCP if available.
        project: project name to search Memory Banks.
        text: ticket description (used if MCP is not configured or ticket_id lookup fails).
        """
        try:
            from .agent.context import assemble_context
            ticket_text = text or ticket_id  # fallback to using ticket_id as text
            ctx = await assemble_context(
                ticket_text=ticket_text,
                project_name=project or ticket_id,
                books_dir=books_dir,
                config_path=config_path,
            )
            context_chunks = [
                {"project": r.project, "section": r.section, "text": r.text}
                for r in ctx.retrieval_results[:6]
            ]
            return {
                "ok": True,
                "capability_gaps": ctx.gap_list,
                "summary": ctx.summary(),
                "relevant_modules": ctx.relevant_projects,
                "context_chunks": context_chunks,
                "assets": {
                    "figma_linked": ctx.assets.figma_linked,
                    "branch_exists": ctx.assets.branch_exists,
                    "pr_open": ctx.assets.pr_open,
                    "test_files_exist": ctx.assets.test_files_exist,
                    "mcp_available": ctx.assets.mcp_available,
                },
            }
        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    # ── Morning Brief ────────────────────────────────────────────────────────

    @app.get("/api/brief")
    async def get_brief():
        """
        Return the last generated brief (content + timestamp).
        Returns empty content if no brief has been run yet.
        """
        from .brief import get_last_brief_at, get_last_brief_content
        content = get_last_brief_content()
        last_at = get_last_brief_at()
        return {
            "content":      content or "",
            "generated_at": last_at.isoformat() if last_at else None,
            "has_content":  bool(content),
        }

    @app.get("/api/brief/stream")
    async def brief_stream(days_back: int = 1):
        """
        SSE endpoint — generate a fresh brief and stream it back.
        Emits:
          data: {"type": "item",  "item": {...}}      — one per raw item
          data: {"type": "brief", "text": "..."}      — synthesised markdown
          data: {"type": "done",  "generated_at": "..."} — completion signal
        """
        import json as _json
        from datetime import datetime as _dt
        from .brief import (
            collect_items, synthesize_brief,
            _save_brief_content, _effective_days_back,
        )
        from fastapi.responses import StreamingResponse

        async def _generate():
            import asyncio
            try:
                effective_days = _effective_days_back(days_back)
                items = collect_items(books_dir, config_path, effective_days)

                for item in items[:10]:
                    yield f"data: {_json.dumps({'type': 'item', 'item': item})}\n\n"
                    await asyncio.sleep(0)

                brief_text = synthesize_brief(
                    items, config_path, max_items=10, days_back=effective_days
                )
                _save_brief_content(brief_text)

                yield f"data: {_json.dumps({'type': 'brief', 'text': brief_text})}\n\n"
                yield f"data: {_json.dumps({'type': 'done', 'generated_at': _dt.now().isoformat()})}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/brief/dismiss")
    async def dismiss_brief():
        """Mark the current brief as seen — clears the cached content."""
        state_path = Path.home() / ".memoria" / "brief_state.json"
        if state_path.exists():
            try:
                import json as _json
                data = _json.loads(state_path.read_text(encoding="utf-8"))
                data.pop("last_content", None)
                state_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass
        return {"ok": True}

    # ── Executive Digest ─────────────────────────────────────────────────────

    @app.get("/api/digest/stream")
    async def digest_stream(window: str = "7d"):
        """
        SSE endpoint — build and stream an executive digest.
        Emits:
          data: {"type": "done",  "markdown": "...", "changed_projects": N}
          data: {"type": "error", "message": "..."}
        """
        import json as _json
        from fastapi.responses import StreamingResponse
        from .digest import build_digest

        async def _generate():
            import asyncio
            try:
                result = build_digest(
                    books_dir   = books_dir,
                    window      = window,
                    config_path = config_path,
                )
                yield f"data: {_json.dumps({'type': 'done', 'markdown': result['markdown'], 'changed_projects': len(result['changed_projects'])})}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Agent Memory ─────────────────────────────────────────────────────────

    @app.get("/api/agent-context/{project}")
    async def agent_context_endpoint(project: str, agent: str = None, max_chars: int = 3000):
        """
        Return a compact, agent-optimised context string for a project.
        Designed for direct injection into any LLM agent system prompt.

        Optional query params:
          ?agent=code-reviewer   — use that agent's focus sections
          ?max_chars=2000        — trim to this many characters
        """
        from .chat_agents import build_agent_context, get_agent, load_agents

        focus = None
        if agent:
            defn = get_agent(agent, project, books_dir)
            if defn:
                focus = defn.get("focus")

        context_str, sections = build_agent_context(
            project, books_dir, focus=focus, max_chars=max_chars
        )

        book_path = Path(books_dir) / f"{''.join(c if c.isalnum() or c in '-_' else '_' for c in project)}_memory_bank.md"
        book_mtime = (
            datetime.fromtimestamp(book_path.stat().st_mtime).isoformat()
            if book_path.exists() else None
        )

        return {
            "project":      project,
            "agent":        agent,
            "context":      context_str,
            "char_count":   len(context_str),
            "sections":     list(sections.keys()),
            "book_mtime":   book_mtime,
            "generated_at": datetime.now().isoformat(),
        }

    @app.get("/api/agents/{project}")
    async def list_agents_endpoint(project: str):
        """List all available agents for a project (built-in + custom)."""
        from .chat_agents import load_agents, AGENT_LIBRARY

        agents = load_agents(project, books_dir)
        return {
            "project": project,
            "agents": [
                {
                    "name":     name,
                    "source":   "built-in" if name in AGENT_LIBRARY else "custom",
                    "focus":    defn.get("focus", []),
                    "greeting": defn.get("greeting", ""),
                }
                for name, defn in agents.items()
            ],
        }

    # ── Planning Intelligence ──────────────────────────────────

    @app.post("/api/plan")
    async def plan_endpoint(request: Request):
        """
        Analyse a project plan against Memory Banks and the knowledge graph.

        Body (JSON):
          plan_text  — the raw plan text (required)
          max_books  — max Memory Banks to include in context (default: 8)

        Returns a structured report:
          summary, affected_projects, dependencies, risks, prior_art,
          recommended_actions, _meta
        """
        from .planner import analyze_plan
        from fastapi.responses import JSONResponse

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        plan_text = body.get("plan_text", "").strip()
        if not plan_text:
            return JSONResponse({"error": "plan_text is required"}, status_code=400)

        max_books = int(body.get("max_books", 8))

        try:
            report = analyze_plan(
                plan_text=plan_text,
                config_path=config_path,
                books_dir=books_dir,
                max_books=max_books,
            )
            return report
        except Exception as exc:
            return JSONResponse(
                {"error": str(exc), "summary": f"Analysis failed: {exc}"},
                status_code=500,
            )

    @app.post("/api/plan/stream")
    async def plan_stream_endpoint(request: Request):
        """
        SSE streaming version of /api/plan.
        Emits events: status (progress messages), result (final JSON), error.

        Body: same as /api/plan
        """
        from .planner import (
            find_relevant_books, _get_graph_context, synthesize_report,
            split_plan, merge_reports, analyze_plan, _CHUNK_THRESHOLD,
        )
        from fastapi.responses import JSONResponse
        import asyncio

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        plan_text = body.get("plan_text", "").strip()
        if not plan_text:
            return JSONResponse({"error": "plan_text is required"}, status_code=400)

        max_books = int(body.get("max_books", 8))

        async def event_stream():
            import json as _json

            def sse(event: str, data) -> str:
                payload = _json.dumps(data) if not isinstance(data, str) else data
                return f"event: {event}\ndata: {payload}\n\n"

            try:
                # ── Detect chunks ──
                chunks = split_plan(plan_text) if len(plan_text) >= _CHUNK_THRESHOLD else [plan_text]
                n_chunks = len(chunks)

                if n_chunks > 1:
                    yield sse("status", {
                        "message": f"Large plan detected — analysing {n_chunks} sections…"
                    })
                else:
                    yield sse("status", {"message": "Searching Memory Banks…"})
                await asyncio.sleep(0)

                # ── Analyse each chunk ──
                chunk_reports: list = []
                for i, chunk in enumerate(chunks, 1):
                    if n_chunks > 1:
                        yield sse("status", {
                            "message": f"Analysing section {i}/{n_chunks}…"
                        })
                        await asyncio.sleep(0)

                    books = find_relevant_books(chunk, books_dir, max_books=max_books)
                    project_names = [b["project"] for b in books]
                    graph_context = _get_graph_context(books_dir, project_names)

                    if i == 1 and n_chunks == 1:
                        yield sse("status", {
                            "message": f"Found {len(books)} relevant Memory Banks — synthesising…"
                        })
                        await asyncio.sleep(0)

                    chunk_report = synthesize_report(chunk, books, graph_context, config_path)
                    chunk_report["_meta"] = {
                        "books_searched": len(books),
                        "graph_used": bool(graph_context),
                    }
                    chunk_reports.append(chunk_report)

                # ── Merge ──
                if n_chunks > 1:
                    yield sse("status", {"message": "Merging section reports…"})
                    await asyncio.sleep(0)
                    report = merge_reports(chunk_reports, plan_text)
                else:
                    report = chunk_reports[0] if chunk_reports else {}

                report["_meta"] = {
                    "generated_at": datetime.now().isoformat(),
                    "books_searched": max(
                        (r.get("_meta", {}).get("books_searched", 0) for r in chunk_reports), default=0
                    ),
                    "graph_used": any(
                        r.get("_meta", {}).get("graph_used", False) for r in chunk_reports
                    ),
                    "chunked": n_chunks > 1,
                    "chunk_count": n_chunks,
                }

                yield sse("result", report)
                yield sse("done", {"message": "Analysis complete"})

            except Exception as exc:
                yield sse("error", {"message": str(exc)})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/plan/chat")
    async def plan_chat_endpoint(request: Request):
        """
        SSE streaming chat for follow-up Q&A in a planning session.

        The advisor has full context of the plan and the matched Memory Banks.
        Supports multi-turn conversation history within a session.

        Body (JSON):
          plan_text  — the original plan text (required)
          question   — the user's question (required)
          history    — [{role, content}, ...] previous turns (optional, last 8 used)
          max_books  — max Memory Banks to include in context (default: 6)

        Emits events: chunk {text}, done, error {message}
        """
        from .planner import find_relevant_books, _get_graph_context
        from fastapi.responses import JSONResponse
        import asyncio

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        plan_text = body.get("plan_text", "").strip()
        question  = body.get("question",  "").strip()
        history   = body.get("history",   [])
        max_books = int(body.get("max_books", 6))

        if not question:
            return JSONResponse({"error": "question is required"}, status_code=400)

        async def event_stream():
            import json as _json
            import litellm
            from .models import ModelProvider

            def sse(event: str, data) -> str:
                payload = _json.dumps(data) if not isinstance(data, str) else data
                return f"event: {event}\ndata: {payload}\n\n"

            try:
                provider = ModelProvider(config_path)

                # ── Context: books relevant to plan + question ──
                query = f"{plan_text[:500]}\n\n{question}"
                books = find_relevant_books(query, books_dir, max_books=max_books)
                project_names = [b["project"] for b in books]
                graph_context = _get_graph_context(books_dir, project_names)

                books_section = "\n\n".join(
                    f"### {b['project']}\n{b['text'][:1500]}" for b in books[:4]
                )
                graph_section = (
                    f"\n\n## Knowledge Graph Context\n{graph_context}"
                    if graph_context else ""
                )

                system_prompt = (
                    "You are a technical planning advisor with deep knowledge of this "
                    "organisation's systems, drawn from the Memory Banks below.\n\n"
                    "Answer the user's question concisely and accurately. Be specific — "
                    "cite project names and Memory Bank sections when relevant. "
                    "Do not invent information that isn't in the provided context.\n\n"
                    f"## Project Plan\n\n{plan_text[:2000]}\n\n"
                    f"## Relevant Memory Banks\n\n{books_section}"
                    f"{graph_section}"
                )

                # ── Build messages ──
                messages = [{"role": "system", "content": system_prompt}]
                for turn in history[-8:]:
                    if turn.get("role") in ("user", "assistant") and turn.get("content"):
                        messages.append({"role": turn["role"], "content": turn["content"]})
                messages.append({"role": "user", "content": question})

                # ── Stream response ──
                stream = litellm.completion(
                    model=provider.model,
                    messages=messages,
                    max_tokens=1000,
                    stream=True,
                    timeout=60,
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield sse("chunk", {"text": delta})

                yield sse("done", {"message": "ok"})

            except Exception as exc:
                yield sse("error", {"message": str(exc)})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Plan snapshot (Plan vs Reality Tracker) ─────────────────────────────

    class PlanSnapshotRequest(BaseModel):
        project:   str
        plan_text: str
        report:    dict

    @app.post("/api/plan/save-snapshot")
    async def save_plan_snapshot_endpoint(req: PlanSnapshotRequest):
        """
        Save a plan snapshot for drift tracking.

        Called when the user explicitly clicks "Save as baseline" in the Plan tab.
        Stores plan_text + a compact risk/project summary to books_dir so that
        future Memory Bank updates can be compared against the original intent.

        Body: { project, plan_text, report }
        """
        if not req.project.strip():
            raise HTTPException(status_code=400, detail="project is required")
        if not req.plan_text.strip():
            raise HTTPException(status_code=400, detail="plan_text is required")

        from .planner import save_plan_snapshot
        path = save_plan_snapshot(
            project=req.project,
            plan_text=req.plan_text,
            report=req.report,
            books_dir=books_dir,
        )
        return {
            "ok":      True,
            "project": req.project,
            "path":    str(path),
        }

    @app.get("/api/plan/snapshot/{project}")
    async def get_plan_snapshot_endpoint(project: str):
        """
        Return the saved plan snapshot metadata for a project, or 404 if none exists.

        Does not return the full plan_text — only the compact metadata needed
        by the UI to show the "tracking active" badge.
        """
        from .planner import load_plan_snapshot
        snapshot = load_plan_snapshot(project, books_dir)
        if not snapshot:
            raise HTTPException(status_code=404, detail="No plan snapshot saved for this project.")
        return {
            "project":           snapshot.get("project"),
            "saved_at":          snapshot.get("saved_at"),
            "affected_projects": snapshot.get("affected_projects", []),
            "risk_count":        len(snapshot.get("risks", [])),
        }

    @app.delete("/api/plan/snapshot/{project}")
    async def delete_plan_snapshot_endpoint(project: str):
        """Remove the plan snapshot for a project (stop tracking drift)."""
        from .planner import _snapshot_path
        path = _snapshot_path(project, books_dir)
        if not path.exists():
            raise HTTPException(status_code=404, detail="No snapshot found.")
        path.unlink()
        return {"ok": True, "project": project}

    @app.get("/api/plan/drift/{project}")
    async def get_drift_endpoint(project: str):
        """
        Run the drift check on demand for a project.
        Returns the drift result without writing any file.
        Useful for the UI to show current drift status.
        """
        from .planner import load_plan_snapshot, check_drift
        if not load_plan_snapshot(project, books_dir):
            raise HTTPException(status_code=404, detail="No plan snapshot saved for this project.")
        drift = check_drift(project, books_dir)
        return drift

    @app.get("/api/plan/drift-report/{project}")
    async def get_drift_report_endpoint(project: str):
        """
        Return the written drift report markdown for a project.
        Only exists after a drift has been detected and write_drift_report() was called.
        """
        import re as _re
        safe = _re.sub(r"[^\w\-]", "_", project)
        drift_path = books_path / f"{safe}_drift.md"
        if not drift_path.exists():
            raise HTTPException(status_code=404, detail="No drift report found for this project.")
        content = drift_path.read_text(encoding="utf-8")
        return {"project": project, "content": content}

    @app.delete("/api/plan/drift-report/{project}")
    async def dismiss_drift_report_endpoint(project: str):
        """
        Dismiss a drift report — deletes the _drift.md file so it leaves the review queue.
        The plan snapshot is kept, so drift will be re-flagged on the next Memory Bank update.
        """
        import re as _re
        safe = _re.sub(r"[^\w\-]", "_", project)
        drift_path = books_path / f"{safe}_drift.md"
        if drift_path.exists():
            drift_path.unlink()
        return {"ok": True, "project": project}

    # ── Skills ────────────────────────────────────────────────────────────────

    @app.post("/api/skills/{project}")
    async def generate_skill_endpoint(project: str, fmt: str = "raw"):
        """
        Generate a SKILL.md for a project from its Memory Bank.

        Query params:
          fmt — "raw" (default) | "langchain" | "crewai" | "autogen" | "n8n"

        Returns:
          {"project": ..., "path": ..., "content": ...}
        """
        from .skills import generate_skill
        book_path = Path(books_dir) / f"{_safe(project)}_memory_bank.md"
        if not book_path.exists():
            raise HTTPException(status_code=404, detail="Memory Bank not found for this project.")
        book_content = book_path.read_text(encoding="utf-8")
        skills_dir = str(Path(books_dir) / "_skills")
        loop = asyncio.get_event_loop()
        try:
            out_path = await loop.run_in_executor(
                None,
                lambda: generate_skill(project, book_content, skills_dir, fmt, config_path),
            )
            content = Path(out_path).read_text(encoding="utf-8")
            return {"project": project, "fmt": fmt, "path": out_path, "content": content}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/skills/{project}")
    async def get_skill_endpoint(project: str, fmt: str = "raw"):
        """Return an existing SKILL.md for a project (does not regenerate)."""
        skills_dir = Path(books_dir) / "_skills"
        safe_name = _safe(project)
        suffix = "" if fmt == "raw" else f"_{fmt}"
        if fmt == "raw":
            skill_path = skills_dir / f"{safe_name}_SKILL.md"
        else:
            ext_map = {"langchain": "py", "crewai": "py", "autogen": "py", "n8n": "json"}
            ext = ext_map.get(fmt, "md")
            skill_path = skills_dir / f"{safe_name}_skill{suffix}.{ext}"
        if not skill_path.exists():
            raise HTTPException(status_code=404, detail="Skill file not found. POST to generate it.")
        return {"project": project, "fmt": fmt, "content": skill_path.read_text(encoding="utf-8")}

    # ── Share tokens ─────────────────────────────────────────────────────────

    class CreateTokenRequest(BaseModel):
        projects: list = []
        label: str = ""
        expires: str = "30d"

    @app.get("/api/share/tokens")
    async def list_share_tokens():
        """Return all share tokens (expired ones flagged with is_expired=True)."""
        from .share import list_tokens
        return {"tokens": list_tokens()}

    @app.post("/api/share/tokens")
    async def create_share_token(req: CreateTokenRequest):
        """
        Generate a new consultant share token.

        Body: {projects: [], label: "Acme Inc", expires: "30d"}
        Returns the full token record including the token string.
        """
        from .share import generate_token
        record = generate_token(
            projects=req.projects,
            label=req.label,
            expires=req.expires or None,
        )
        try:
            from .audit import log_action as _audit
            _audit("share_token_created", target=req.label or "unlabeled",
                   detail={"projects": req.projects, "expires": req.expires},
                   config_path=config_path)
        except Exception:
            pass
        return record

    @app.delete("/api/share/tokens/{token}")
    async def revoke_share_token(token: str):
        """Revoke a share token by its token string."""
        from .share import revoke_token
        ok = revoke_token(token)
        if not ok:
            raise HTTPException(status_code=404, detail="Token not found.")
        return {"ok": True}

    @app.delete("/api/share/tokens")
    async def revoke_all_tokens():
        """Revoke all share tokens."""
        from .share import revoke_all
        count = revoke_all()
        return {"ok": True, "revoked": count}

    # ── Coverage ──────────────────────────────────────────────────────────────

    @app.get("/api/admin/coverage")
    async def get_coverage_endpoint(scan_roots: str = ""):
        """
        Return a full knowledge coverage analysis.

        Query params:
          scan_roots — comma-separated list of directories to scan for undocumented projects

        Returns the structured coverage report from coverage.run_coverage_analysis().
        """
        from .coverage import run_coverage_analysis
        roots = [r.strip() for r in scan_roots.split(",") if r.strip()] if scan_roots else []
        loop = asyncio.get_event_loop()
        try:
            report = await loop.run_in_executor(
                None,
                lambda: run_coverage_analysis(books_dir, roots or None),
            )
            return report
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── RBAC / Users ──────────────────────────────────────────────────────────

    class GrantRoleRequest(BaseModel):
        username: str
        role: str  # viewer | contributor | reviewer | admin

    @app.get("/api/admin/rbac")
    async def get_rbac():
        """Return RBAC policy: enabled flag, user list, default role."""
        from .rbac import load_policy, list_all_roles, is_rbac_enabled, get_current_user
        enabled = is_rbac_enabled(config_path)
        policy = load_policy(config_path)
        users = list_all_roles(policy)
        default_role = policy.get("default_role", "contributor")
        current_user = get_current_user(config_path)
        return {
            "enabled": enabled,
            "default_role": default_role,
            "current_user": current_user,
            "users": users,
        }

    @app.post("/api/admin/rbac/users")
    async def grant_rbac_role(req: GrantRoleRequest):
        """Grant or update a user's role."""
        from .rbac import grant_role, Role
        valid_roles = {r.value for r in Role}
        if req.role not in valid_roles:
            raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(sorted(valid_roles))}")
        grant_role(req.username, Role(req.role), config_path)
        return {"ok": True, "username": req.username, "role": req.role}

    @app.delete("/api/admin/rbac/users/{username}")
    async def revoke_rbac_role(username: str):
        """Remove a user from the explicit policy (they fall back to default_role)."""
        from .rbac import revoke_role
        found = revoke_role(username, config_path)
        if not found:
            raise HTTPException(status_code=404, detail="User not found in explicit policy.")
        return {"ok": True}

    @app.put("/api/admin/rbac/settings")
    async def update_rbac_settings(enabled: bool, default_role: str = "contributor"):
        """Toggle RBAC on/off and update the default role."""
        from .rbac import load_policy, save_policy, Role
        valid_roles = {r.value for r in Role}
        if default_role not in valid_roles:
            raise HTTPException(status_code=400, detail=f"Invalid role.")
        policy = load_policy(config_path)
        if not policy:
            policy = {"users": {}, "teams": {}}
        policy["enabled"] = enabled
        policy["default_role"] = default_role
        save_policy(policy, config_path)
        return {"ok": True, "enabled": enabled, "default_role": default_role}

    # ── Audit Log ─────────────────────────────────────────────────────────────

    @app.get("/api/admin/audit")
    async def get_audit_log(
        actor: str = "",
        action: str = "",
        target: str = "",
        ok: Optional[str] = None,        # "true" | "false" | ""
        since: str = "",
        limit: int = 200,
        offset: int = 0,
    ):
        """
        Return paginated audit log entries, newest-first.

        Query params (all optional, substring match):
          actor, action, target — filter by field substring
          ok                    — "true" | "false" to filter by success/failure
          since                 — ISO date prefix (e.g. "2026-01-01")
          limit / offset        — pagination (default limit 200)
        """
        from .audit import query_log, count_log
        ok_filter: Optional[bool] = None
        if ok == "true":
            ok_filter = True
        elif ok == "false":
            ok_filter = False
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None,
            lambda: query_log(
                actor=actor, action=action, target=target,
                ok=ok_filter, since=since, limit=limit, offset=offset,
                config_path=config_path,
            ),
        )
        total = await loop.run_in_executor(
            None,
            lambda: count_log(
                config_path, actor=actor, action=action, target=target,
                ok=ok_filter, since=since,
            ),
        )
        return {"total": total, "offset": offset, "limit": limit, "rows": rows}

    @app.get("/api/admin/audit/export")
    async def export_audit_csv(
        actor: str = "",
        action: str = "",
        target: str = "",
        ok: Optional[str] = None,
        since: str = "",
        limit: int = 5000,
    ):
        """Export audit log as CSV (same filters as GET /api/admin/audit)."""
        from .audit import query_log, export_csv
        from fastapi.responses import Response as FastAPIResponse
        ok_filter: Optional[bool] = None
        if ok == "true":
            ok_filter = True
        elif ok == "false":
            ok_filter = False
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None,
            lambda: query_log(
                actor=actor, action=action, target=target,
                ok=ok_filter, since=since, limit=limit, offset=0,
                config_path=config_path,
            ),
        )
        csv_data = export_csv(rows)
        return FastAPIResponse(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
        )

    # ── Interview ─────────────────────────────────────────────────────────────

    class InterviewAnswerRequest(BaseModel):
        session_id: str
        answer: str

    class InterviewSynthesizeRequest(BaseModel):
        topic: str
        project: str
        transcript: list  # list of {"question": str, "answer": str}

    # In-process interview sessions: {session_id: {topic, project, transcript, questions}}
    _interview_sessions: dict = {}

    @app.post("/api/interview/session")
    async def create_interview_session(topic: str, project: str):
        """
        Start a new interview session.

        Query params:
          topic   — the process or topic being documented
          project — project name to associate with this interview

        Returns {session_id, question} — first question to show the user.
        """
        import uuid
        from .interview import BASE_QUESTIONS
        session_id = str(uuid.uuid4())[:8]
        questions = list(BASE_QUESTIONS)  # list of (key, question_text)
        _interview_sessions[session_id] = {
            "topic": topic,
            "project": project,
            "questions": questions,
            "current_idx": 0,
            "transcript": [],
        }
        first_q = questions[0][1] if questions else None
        return {"session_id": session_id, "question": first_q, "total": len(questions)}

    @app.post("/api/interview/answer")
    async def submit_interview_answer(req: InterviewAnswerRequest):
        """
        Submit an answer to the current interview question.

        Returns {next_question, question_index, total, done}.
        When done=true the client should POST to /api/interview/synthesize.
        """
        sess = _interview_sessions.get(req.session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found or expired.")
        idx = sess["current_idx"]
        questions = sess["questions"]
        if idx >= len(questions):
            return {"done": True, "next_question": None, "question_index": idx, "total": len(questions)}

        _, question_text = questions[idx]
        sess["transcript"].append({"question": question_text, "answer": req.answer})
        sess["current_idx"] = idx + 1

        if sess["current_idx"] >= len(questions):
            return {"done": True, "next_question": None, "question_index": idx + 1, "total": len(questions)}

        next_key, next_q = questions[sess["current_idx"]]
        return {
            "done": False,
            "next_question": next_q,
            "question_index": sess["current_idx"],
            "total": len(questions),
        }

    @app.post("/api/interview/synthesize")
    async def synthesize_interview(req: InterviewSynthesizeRequest):
        """
        SSE endpoint — synthesizes a completed transcript into a Process Memory Bank.

        Body: {topic, project, transcript: [{question, answer}, ...]}

        Events:
          {"type": "progress", "message": "..."}
          {"type": "done",     "project": "...", "path": "..."}
          {"type": "error",    "message": "..."}
        """
        import queue as _queue
        import threading

        q: _queue.Queue = _queue.Queue()

        def _run():
            try:
                from .interview import _SYNTHESIS_SYSTEM, _SYNTHESIS_PROMPT
                from .models import ModelProvider

                model = ModelProvider(config_path)
                qa_text = "\n\n".join(
                    f"Q: {item['question']}\nA: {item['answer']}"
                    for item in req.transcript
                )
                q.put({"type": "progress", "message": "Synthesizing transcript into Process Memory Bank…"})

                content = model.complete(
                    _SYNTHESIS_SYSTEM,
                    _SYNTHESIS_PROMPT.format(
                        topic=req.topic,
                        project=req.project,
                        transcript=qa_text,
                        date=__import__("datetime").datetime.now().strftime("%Y-%m-%d"),
                        model=model.model,
                    ),
                )

                safe_name = _safe(req.project or req.topic)
                out_path = Path(books_dir) / f"{safe_name}_memory_bank.md"
                out_path.write_text(content, encoding="utf-8")
                q.put({"type": "done", "project": req.project, "path": str(out_path)})
            except Exception as exc:
                q.put({"type": "error", "message": str(exc)})

        threading.Thread(target=_run, daemon=True).start()

        async def _stream():
            import json as _json
            while True:
                try:
                    item = await asyncio.get_event_loop().run_in_executor(None, lambda: q.get(timeout=60))
                    yield f"data: {_json.dumps(item)}\n\n"
                    if item["type"] in ("done", "error"):
                        break
                except Exception:
                    break

        return StreamingResponse(_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _safe(name: str) -> str:
    """Convert a project name to a safe filename stem."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _resolve_config_path(config_path: str) -> Path:
    """Find the actual config.yaml that would be loaded (mirrors ModelProvider logic)."""
    path = Path(config_path)
    if path.exists():
        return path.resolve()
    # Walk up from CWD
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / "config.yaml"
        if candidate.exists():
            return candidate.resolve()
    # User-level fallback
    user_cfg = Path.home() / ".memoria" / "config.yaml"
    if user_cfg.exists():
        return user_cfg.resolve()
    # Default — will be created on first save
    return user_cfg


def _load_config(config_path: str) -> dict:
    """Load config.yaml; return {} if it doesn't exist or is unreadable."""
    import yaml
    path = Path(config_path)
    if not path.exists():
        # Try the Memoria home directory as a fallback
        home_cfg = Path.home() / ".memoria" / "config.yaml"
        if home_cfg.exists():
            path = home_cfg
        else:
            return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


async def _auto_dispatch_mcp_tool(
    src: dict,
    question: str,
    config_path: str,
) -> str:
    """
    When a source has no 'pull:' config, connect to the MCP server,
    ask the LLM which tool to call for the given question, call it,
    and return the result as text.

    This is a minimal single-step agent: one tool call per question.
    """
    import json
    import re as _re
    import litellm
    from .mcp_sources import _pull_async, _text_from_content_block

    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        return "[mcp package not installed — run: pip install mcp]"

    import shlex, os as _os
    server_str = src.get("server", "").strip()
    if not server_str:
        return "[No 'server' configured for this MCP source]"

    parts    = shlex.split(server_str)
    cmd, args = parts[0], parts[1:]
    env       = {**_os.environ}
    for k, v in src.get("env", {}).items():
        from .mcp_sources import _expand_env
        env[k] = _expand_env(v)

    params = StdioServerParameters(command=cmd, args=args, env=env)
    loop   = asyncio.get_event_loop()

    cfg   = _load_config(config_path)
    model = cfg.get("model", "gemini/gemini-2.0-flash")

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. List available tools
            tools_result = await session.list_tools()
            tools = tools_result.tools or []
            if not tools:
                return "[No tools available on this MCP server]"

            tool_descs = "\n".join(
                f"- {t.name}: {getattr(t, 'description', 'no description')[:120]}"
                for t in tools
            )

            # 2. Ask the LLM which tool + args to use for this question
            selector_prompt = (
                "You are a tool selector for an MCP server. "
                "Given the user question and available tools, respond with ONLY a JSON object:\n"
                '{"tool": "tool_name", "args": {"param": "value"}}\n'
                "If the question cannot be answered with any listed tool, respond: "
                '{"tool": null}\n\n'
                f"Available tools:\n{tool_descs}\n\n"
                f"User question: {question}"
            )

            try:
                sel_resp = await loop.run_in_executor(
                    None,
                    lambda: litellm.completion(
                        model=model,
                        messages=[{"role": "user", "content": selector_prompt}],
                        max_tokens=200,
                        timeout=30,
                    ),
                )
                raw_json = sel_resp.choices[0].message.content.strip()
                # Extract JSON even if wrapped in markdown code fences
                json_match = _re.search(r"\{.*\}", raw_json, _re.DOTALL)
                selection = json.loads(json_match.group()) if json_match else {}
            except Exception as exc:
                return f"[Tool selection failed: {exc}]"

            tool_name = selection.get("tool")
            if not tool_name:
                return (
                    f"[None of the available tools can answer this question directly. "
                    f"Available tools: {', '.join(t.name for t in tools)}]"
                )

            tool_args = selection.get("args", {})

            # 3. Call the selected tool
            try:
                result = await session.call_tool(tool_name, arguments=tool_args)
                text = "\n\n".join(
                    t for b in result.content
                    if (t := _text_from_content_block(b))
                )
                return text or f"[{tool_name} returned no content]"
            except Exception as exc:
                return f"[Error calling {tool_name}({tool_args}): {exc}]"


async def _stream_answer_live_mcp(
    question:        str,
    book_content:    str,
    live_sources:    list,
    extra_ctx:       str,
    config_path:     str,
    history:         list | None = None,
    model_overrides: dict | None = None,
) -> "AsyncGenerator[str, None]":
    """
    Live-MCP streaming path.

    1. Immediately signals the frontend that a live fetch is in progress.
    2. Calls each configured MCP source tool-set (with a 30 s timeout per source).
    3. Builds the system prompt combining live data + any cached Memory Bank content.
    4. Streams the LLM answer exactly like _stream_answer.
    """
    import asyncio

    source_names = [s.get("name", "source") for s in live_sources]
    yield f"data: {json.dumps({'type': 'mcp_fetch', 'sources': source_names})}\n\n"

    # Fetch live data from every matching MCP source.
    # Two paths:
    #   a) source has pull: config  → call those specific tools (fast, deterministic)
    #   b) source has no pull: config → auto-dispatch: LLM picks the right tool for the question
    from .mcp_sources import _pull_async
    live_parts: list[str] = []
    for src in live_sources:
        name = src.get("name", "source")
        has_pull_config = bool(src.get("pull"))
        try:
            if has_pull_config:
                yield f"data: {json.dumps({'type': 'mcp_tool_call', 'source': name, 'tool': src['pull'][0].get('tool','') if src.get('pull') else '', 'args': src['pull'][0].get('args',{}) if src.get('pull') else {}})}\n\n"
                data = await asyncio.wait_for(_pull_async(src), timeout=30.0)
            else:
                # No pull: config — let the LLM pick the right tool for this question
                # Tool selection happens inside _auto_dispatch_mcp_tool; we emit the
                # dispatch event after selection but before calling (see that function).
                yield f"data: {json.dumps({'type': 'mcp_dispatching', 'source': name, 'message': f'Selecting tool from {name}…'})}\n\n"
                data = await asyncio.wait_for(
                    _auto_dispatch_mcp_tool(src, question, config_path),
                    timeout=45.0,
                )
            live_parts.append(f"## Live data from {name} (fetched now)\n\n{data}")
        except asyncio.TimeoutError:
            live_parts.append(
                f"## {name}\n\n[Live fetch timed out. "
                "Check that the MCP server is running and the token is valid.]"
            )
        except Exception as exc:
            live_parts.append(
                f"## {name}\n\n[Live fetch failed: {exc}]"
            )

    live_ctx = "\n\n---\n\n".join(live_parts)

    # If every live fetch failed, stop here — never let the LLM answer with no
    # real data or it will hallucinate results.
    _FAIL_MARKERS = ("[Live fetch", "[Error calling", "[None of the", "[No tools",
                     "[mcp package", "[No 'server'", "[Tool selection failed")
    all_failed = bool(live_parts) and all(
        any(m in part for m in _FAIL_MARKERS) for part in live_parts
    )
    if all_failed:
        # Extract the first useful error detail for the user
        first_error = live_parts[0] if live_parts else "Unknown error"
        error_msg = (
            f"⚠️ Could not fetch live data from **{', '.join(source_names)}**.\n\n"
            f"{first_error}\n\n"
            "Please check:\n"
            "- The MCP server binary is installed (`npx`, `uvx`, or the configured command)\n"
            "- The required API tokens are set in your `.env` file\n"
            "- The server name in `config.yaml` matches the source you configured"
        )
        yield f"data: {json.dumps({'type': 'mcp_fetch_error'})}\n\n"
        yield f"data: {json.dumps({'type': 'chunk', 'text': error_msg})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    # Partial failures — still have some live data, but flag what failed
    partial_failed = [p for p in live_parts if any(m in p for m in _FAIL_MARKERS)]
    if partial_failed:
        yield f"data: {json.dumps({'type': 'mcp_fetch_partial_error', 'count': len(partial_failed)})}\n\n"

    system = _build_system_prompt(book_content, live_ctx) + extra_ctx

    async for event in _stream_answer(
        system, question, config_path,
        history=history,
        model_overrides=model_overrides or {},
    ):
        yield event


def _build_system_prompt(book_content: str, live_ctx: str = "") -> str:
    # Detect book type from its headings (reuse the same logic as cli.py `ask`)
    is_audio    = "## 1. What This Recording Is About" in book_content
    is_document = "## 1. What This Collection Is About" in book_content or \
                  "## 1. What This Document Is About" in book_content
    is_notebook = "## 1. Purpose & Question" in book_content

    if is_audio:
        role = "an expert analyst who has thoroughly reviewed this recording and transcript"
        tips = "Quote the speaker's actual words when relevant. Reference timestamps if available."
        not_applicable = (
            "This is an audio/transcript document — questions about running code, "
            "file structure, or software setup do not apply."
        )
    elif is_document:
        role = "a knowledgeable analyst who has carefully read this document collection"
        tips = "Preserve exact numbers, dates, names, and certifications. Reference specific sections or documents."
        not_applicable = (
            "This is a document collection — it is NOT a software project. "
            "Questions about running code, build steps, or software setup do not apply here. "
            "If asked such questions, tell the user this is a document collection and describe what it actually covers."
        )
    elif is_notebook:
        role = "a senior data scientist who has reviewed this notebook and its analysis"
        tips = "Be precise about findings vs assumptions. Give exact re-run steps."
        not_applicable = ""
    else:
        role = "a senior engineer who knows this project inside out"
        tips = ("Reference actual file names, function names, and modules. "
                "Explain flows step by step. Point to specific files.")
        not_applicable = ""

    # For MCP sources: live data is fresher than the cached book.
    # Override role/tips if this is primarily live external data.
    if live_ctx and not book_content:
        role = "a knowledgeable assistant with direct access to live external data"
        tips = "Answer precisely from the live data. If something isn't in the data, say so clearly."
        not_applicable = ""

    grounding = (
        "CRITICAL: Your ONLY source of information is the content provided below. "
        "Do NOT use any external knowledge or make up details not present in it. "
        "If the content does not answer the question, say exactly that — do not guess or hallucinate. "
        "Do not describe this tool or system; describe only what is in the content."
    )
    not_applicable_rule = f"- {not_applicable}\n" if not_applicable else ""

    # Build the content section:
    # - Live MCP data (if present) takes priority — it's current.
    # - Cached Memory Bank (if present) supplements with historical/structural context.
    if live_ctx and book_content:
        content_section = (
            "LIVE DATA (fetched from connected services just now — use this as the primary source):\n"
            f"{live_ctx}\n\n"
            "---\n\n"
            "CACHED MEMORY BANK (last sync snapshot — use for background context):\n"
            f"{book_content}"
        )
    elif live_ctx:
        content_section = (
            "LIVE DATA (fetched from connected services just now):\n"
            f"{live_ctx}"
        )
    else:
        content_section = f"REFERENCE CONTENT:\n{book_content}"

    return (
        f"You are {role}.\n"
        "You will answer questions strictly based on the content provided below.\n\n"
        f"{grounding}\n\n"
        f"Rules:\n"
        f"- {tips}\n"
        f"{not_applicable_rule}"
        "- If the content doesn't cover something, say so clearly — never guess.\n"
        "- Keep answers concise unless the user asks for depth.\n"
        "- Format your response with Markdown (code blocks, bold, lists) where it helps.\n\n"
        f"{content_section}"
    )


async def _stream_answer(
    system: str,
    question: str,
    config_path: str,
    history: list | None = None,
    model_overrides: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE events for a streaming LLM response."""
    import litellm

    cfg   = _load_config(config_path)
    ov    = model_overrides or {}
    model = ov.get("model") or cfg.get("model", "gemini/gemini-2.0-flash")
    # Ollama runs locally and can be slow — give it more time
    is_ollama = "ollama" in model.lower()
    timeout = 600 if is_ollama else 120

    # Build conversation: system → history → current question
    # history is [{role:"user"|"assistant", content:"..."}]
    messages: list[dict] = [{"role": "system", "content": system}]
    for h in (history or []):
        role = h.get("role", "user")
        if role not in ("user", "assistant"):
            continue
        content = str(h.get("content", "")).strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    kwargs = {
        "model":    model,
        "messages": messages,
        "stream": True,
        "timeout": timeout,
    }
    # Apply model parameter overrides from UI gear button (take precedence over config)
    if ov.get("max_tokens"):
        kwargs["max_tokens"] = int(ov["max_tokens"])
    elif cfg.get("max_tokens_output"):
        kwargs["max_tokens"] = cfg["max_tokens_output"]
    if ov.get("temperature") is not None:
        kwargs["temperature"] = float(ov["temperature"])
    if ov.get("top_p") is not None:
        kwargs["top_p"] = float(ov["top_p"])

    loop = asyncio.get_event_loop()

    # Send a keepalive comment so the browser connection doesn't time out
    # while waiting for the first LLM token (especially slow for Ollama).
    yield ": keepalive\n\n"

    try:
        # litellm.completion is synchronous — run in thread executor.
        # With stream=True this opens the HTTP connection and returns a generator.
        response = await loop.run_in_executor(
            None,
            lambda: litellm.completion(**kwargs),
        )

        # IMPORTANT: iterate the generator in the thread pool, NOT on the event
        # loop. Calling next() on a litellm streaming generator blocks on network
        # I/O; doing that directly on the event loop prevents SSE data from being
        # flushed to the browser until the entire response is buffered — which
        # causes the "stuck thinking" symptom. Each chunk must be awaited via
        # run_in_executor so the event loop stays free to flush between chunks.
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
        error_msg = str(exc)
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            error_msg = f"Model timed out after {timeout}s. "
            if is_ollama:
                error_msg += "Check that Ollama is running (`ollama serve`) and the model is downloaded (`ollama pull gemma3:12b`)."
            else:
                error_msg += "The model may be overloaded. Try again or switch to a different model in Settings."
        yield f"data: {json.dumps({'type': 'error', 'message': error_msg})}\n\n"


# ── MCP tool-list cache (avoids re-spawning servers on every chat message) ────
# { source_name: {"tools": [...], "ts": float} }
_mcp_tool_cache: dict = {}
_MCP_TOOL_CACHE_TTL = 300  # 5 minutes


async def _get_tools_cached(name: str, config_path: str) -> list[dict]:
    import time
    cached = _mcp_tool_cache.get(name)
    if cached and (time.time() - cached["ts"]) < _MCP_TOOL_CACHE_TTL:
        return cached["tools"]
    from .mcp_sources import list_tools_async
    tools = await asyncio.wait_for(list_tools_async(name, config_path), timeout=30.0)
    _mcp_tool_cache[name] = {"tools": tools, "ts": time.time()}
    return tools


async def _select_mcp_tools_native(
    question: str,
    config_path: str,
    history: list | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Use native LLM function calling to select MCP tool calls.
    The model receives actual MCP tool schemas and generates validated, schema-correct args.
    Falls back to a text-based selector for models that don't support function calling.

    Returns (calls, connection_errors).
      calls  — [{"source", "tool", "args", "_call_id"}, ...]  (_call_id used for ReAct)
      errors — sources that failed to list their tools
    """
    try:
        from .mcp_sources import load_sources
        sources = load_sources(config_path)
        if not sources:
            return [], []
    except Exception:
        return [], []

    tool_results = await asyncio.gather(
        *[_get_tools_cached(s["name"], config_path) for s in sources if s.get("name")],
        return_exceptions=True,
    )

    lm_tools: list[dict] = []
    connection_errors: list[str] = []
    fn_to_source: dict[str, dict] = {}  # sanitised fn name → {"source", "tool"}

    for src, result in zip(sources, tool_results):
        src_name = src.get("name", "")
        if not src_name:
            continue
        if isinstance(result, Exception):
            connection_errors.append(f"{src_name}: {result}")
            continue
        if not result:
            connection_errors.append(f"{src_name}: no tools listed")
            continue
        for t in result:
            # litellm function names must be [a-zA-Z0-9_-]
            fn_name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{src_name}__{t['name']}")
            schema = t.get("inputSchema") or {"type": "object", "properties": {}}
            lm_tools.append({
                "type": "function",
                "function": {
                    "name":        fn_name,
                    "description": (t.get("description") or "")[:500],
                    "parameters":  schema,
                },
            })
            fn_to_source[fn_name] = {"source": src_name, "tool": t["name"]}

    if not lm_tools:
        return [], connection_errors

    cfg   = _load_config(config_path)
    model = cfg.get("model", "gemini/gemini-2.0-flash")

    messages: list[dict] = [
        {"role": "system", "content": (
            "You are a tool selector. Call the appropriate MCP tool(s) to answer the "
            "user's question. Do not call tools for questions answerable from general knowledge."
        )},
    ]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": question})

    import litellm as _ll
    loop = asyncio.get_event_loop()
    try:
        resp = await loop.run_in_executor(
            None,
            lambda: _ll.completion(
                model=model,
                messages=messages,
                tools=lm_tools,
                tool_choice="auto",
                max_tokens=800,
                timeout=30,
            ),
        )
        raw_calls = getattr(resp.choices[0].message, "tool_calls", None) or []
        if not raw_calls:
            return [], connection_errors

        calls = []
        for tc in raw_calls[:3]:
            mapping = fn_to_source.get(tc.function.name)
            if not mapping:
                continue
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            calls.append({
                "source":   mapping["source"],
                "tool":     mapping["tool"],
                "args":     args,
                "_call_id": tc.id,
            })
        return calls, connection_errors

    except Exception as exc:
        # If the model doesn't support function calling, fall back to text selection
        err_lower = str(exc).lower()
        if any(k in err_lower for k in ("tool", "function", "unsupported", "not support")):
            return await _select_mcp_tools_text(question, config_path)
        return [], connection_errors


async def _select_mcp_tools_text(
    question: str,
    config_path: str,
) -> tuple[list[dict], list[str]]:
    """
    Fallback text-based tool selector for models without native function calling.
    Returns calls WITHOUT _call_id (ReAct message format won't be used).
    """
    try:
        from .mcp_sources import load_sources
        from .models import ModelProvider
        sources = load_sources(config_path)
        if not sources:
            return [], []
    except Exception:
        return [], []

    tool_results = await asyncio.gather(
        *[_get_tools_cached(s["name"], config_path) for s in sources if s.get("name")],
        return_exceptions=True,
    )
    catalog_lines: list[str] = []
    connection_errors: list[str] = []
    for src, result in zip(sources, tool_results):
        src_name = src.get("name", "")
        if not src_name:
            continue
        if isinstance(result, Exception):
            connection_errors.append(f"{src_name}: {result}")
            continue
        if not result:
            connection_errors.append(f"{src_name}: no tools listed")
            continue
        tool_entries = []
        for t in result:
            desc     = (t.get("description") or "")[:120]
            schema   = t.get("inputSchema") or {}
            required = schema.get("required") or []
            props    = schema.get("properties") or {}
            params   = ", ".join(
                f'{p}:{props.get(p,{}).get("type","string")}(required)' for p in required
            )
            tool_entries.append(f'    tool="{t["name"]}" desc="{desc}" params=[{params}]')
        catalog_lines.append(f'  source="{src_name}":\n' + "\n".join(tool_entries))

    if not catalog_lines:
        return [], connection_errors

    model = ModelProvider(config_path)
    prompt = (
        f"User question: {question[:600]}\n\n"
        f"Available MCP tools:\n{''.join(catalog_lines)}\n\n"
        "Return a JSON array of tool calls (use exact param names). "
        "Return [] if no tools are needed.\n"
        '[{"source":"name","tool":"tool_name","args":{...}}]'
    )
    try:
        raw = await model.async_complete(
            "You select MCP tool calls. Respond ONLY with a JSON array.", prompt
        )
        raw = raw.strip()
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        calls: list[dict] = json.loads(m.group(0)) if m else []
        if not isinstance(calls, list):
            return [], connection_errors
        return [c for c in calls if c.get("source") and c.get("tool")][:3], connection_errors
    except Exception:
        return [], connection_errors


async def _execute_mcp_tools(
    calls: list[dict],
    config_path: str,
) -> list[dict]:
    """
    Execute approved MCP tool calls.
    Returns a list of structured results — one per call:
      {"call_id": str, "source": str, "tool": str, "content": str, "ok": bool}
    """
    from .mcp_sources import call_tool_async
    results: list[dict] = []
    for i, call in enumerate(calls[:3]):
        src_name  = call.get("source", "")
        tool_name = call.get("tool", "")
        tool_args = call.get("args") or {}
        call_id   = call.get("_call_id") or f"call_{i}"
        if not src_name or not tool_name:
            continue
        try:
            content = await asyncio.wait_for(
                call_tool_async(src_name, tool_name, tool_args, config_path=config_path),
                timeout=15.0,
            )
            results.append({
                "call_id": call_id, "source": src_name, "tool": tool_name,
                "content": content[:4000], "ok": True,
            })
        except asyncio.TimeoutError:
            results.append({
                "call_id": call_id, "source": src_name, "tool": tool_name,
                "content": "[Timed out after 15 s — MCP server may be slow or unresponsive]",
                "ok": False,
            })
        except Exception as exc:
            results.append({
                "call_id": call_id, "source": src_name, "tool": tool_name,
                "content": f"[Tool call failed: {exc}]",
                "ok": False,
            })
    return results


def _build_react_messages(
    system: str,
    history: list,
    question: str,
    approved_calls: list[dict],
    tool_results: list[dict],
) -> list[dict]:
    """
    Build the full message list for a ReAct second pass:
      [system, ...history, user_question, assistant{tool_calls}, tool{result}, ...]
    The model sees its own prior tool invocations alongside their results and
    generates an answer grounded in the actual output — not in assumptions.
    """
    messages: list[dict] = [{"role": "system", "content": system}]
    for h in (history or []):
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": question})

    # Reconstruct the assistant's tool-call turn
    tc_list = []
    for call in approved_calls[:3]:
        fn_name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{call['source']}__{call['tool']}")
        tc_list.append({
            "id":       call.get("_call_id") or f"call_{len(tc_list)}",
            "type":     "function",
            "function": {
                "name":      fn_name,
                "arguments": json.dumps(call.get("args") or {}),
            },
        })
    if tc_list:
        messages.append({"role": "assistant", "content": None, "tool_calls": tc_list})

    # Append one tool-result message per executed call
    for result in tool_results:
        messages.append({
            "role":         "tool",
            "tool_call_id": result["call_id"],
            "content":      result["content"],
        })
    return messages


async def _stream_from_messages(
    messages: list[dict],
    config_path: str,
    model_overrides: dict | None = None,
) -> "AsyncGenerator[str, None]":
    """Stream an LLM response from a pre-built message list (used for ReAct second pass)."""
    import litellm
    cfg     = _load_config(config_path)
    ov      = model_overrides or {}
    model   = ov.get("model") or cfg.get("model", "gemini/gemini-2.0-flash")
    timeout = 600 if "ollama" in model.lower() else 120

    kwargs: dict = {"model": model, "messages": messages, "stream": True, "timeout": timeout}
    if ov.get("max_tokens"):
        kwargs["max_tokens"] = int(ov["max_tokens"])
    elif cfg.get("max_tokens_output"):
        kwargs["max_tokens"] = cfg["max_tokens_output"]
    if ov.get("temperature") is not None:
        kwargs["temperature"] = float(ov["temperature"])
    if ov.get("top_p") is not None:
        kwargs["top_p"] = float(ov["top_p"])

    loop = asyncio.get_event_loop()
    yield ": keepalive\n\n"
    try:
        response = await loop.run_in_executor(None, lambda: litellm.completion(**kwargs))
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


async def _fetch_mcp_context_for_question(question: str, config_path: str) -> tuple[str, list[str]]:
    """Legacy helper for the /api/ask (per-project) path — runs selection + execution in one shot."""
    calls, _errors = await _select_mcp_tools_native(question, config_path)
    if not calls:
        return "", []
    results = await _execute_mcp_tools(calls, config_path)
    parts = [
        f"### [{r['source']} / {r['tool']}]\n{r['content']}"
        for r in results
    ]
    used = list({r["source"] for r in results if r["ok"]})
    if not parts:
        return "", []
    return "## Live Tool Data\n\n" + "\n\n".join(parts), used


async def _stream_global_answer(
    question: str,
    top_k: int,
    books_dir: str,
    config_path: str,
    allowed_projects: Optional[list] = None,
    history: list | None = None,
    model_overrides: dict | None = None,
    approved_tools: Optional[list] = None,
) -> AsyncGenerator[str, None]:
    """
    Search across ALL Memory Banks, build a multi-book context, then stream
    an attributed answer.

    Event sequence:
      1. {"type": "sources", "sources": [{project, section, score}, ...]}
      2. {"type": "chunk",   "text": "..."}  (one or many)
      3. {"type": "done"}
    """
    loop = asyncio.get_event_loop()

    try:
        from .search import search as do_search, index_all_books

        # Auto-index if ChromaDB doesn't exist yet
        db_path = Path(books_dir) / ".chromadb"
        if not db_path.exists():
            await loop.run_in_executor(None, lambda: index_all_books(books_dir))

        hits = await loop.run_in_executor(
            None, lambda: do_search(question, books_dir, top_k=top_k)
        )

        # If still empty (no books indexed) fall back gracefully
        if not hits:
            await loop.run_in_executor(None, lambda: index_all_books(books_dir))
            hits = await loop.run_in_executor(
                None, lambda: do_search(question, books_dir, top_k=top_k)
            )

        if not hits:
            yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
            yield f"data: {json.dumps({'type': 'error', 'message': 'No Memory Banks found. Run memoria analyze first.'})}\n\n"
            return

        # ── Drop stale hits whose Memory Bank file has been deleted ─────────────
        # (Same filter as /api/search — embeddings outlive the .md file until
        #  the next explicit delete_project() call.)
        live_projects = {
            f.stem.replace("_memory_bank", "")
            for f in Path(books_dir).glob("*_memory_bank.md")
            if not f.name.endswith("_draft.md")
        }
        hits = [h for h in hits if h.get("project") in live_projects]

        # ── Scope filtering — remove projects the user cannot access ─────────
        if allowed_projects is not None:
            allowed_set = set(allowed_projects)
            hits = [h for h in hits if h.get("project") in allowed_set]
            if not hits:
                yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
                yield f"data: {json.dumps({'type': 'error', 'message': 'No accessible Memory Banks for your scope.'})}\n\n"
                return

        # ── Group chunks by project, keep top 3 chunks each, top 5 projects ──
        by_project: dict = {}
        for hit in hits:
            p = hit["project"]
            if p not in by_project:
                by_project[p] = []
            if len(by_project[p]) < 3:
                by_project[p].append(hit)
            if len(by_project) >= 5:
                break

        # ── MCP tool approval gate ────────────────────────────────────────────
        # approved_tools is None  → first request; run native function-calling selection
        # approved_tools is []    → user declined; answer from Memory Banks only
        # approved_tools is [...]  → user approved; execute tools then do ReAct pass
        tool_results: list[dict] = []
        mcp_connection_errors: list[str] = []

        if approved_tools is None:
            selected_calls, mcp_connection_errors = await _select_mcp_tools_native(
                question, config_path, history
            )
            if selected_calls:
                yield f"data: {json.dumps({'type': 'tool_selection', 'calls': selected_calls})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return
            if mcp_connection_errors:
                yield f"data: {json.dumps({'type': 'mcp_connection_error', 'errors': mcp_connection_errors})}\n\n"

        elif approved_tools:
            tool_results = await _execute_mcp_tools(approved_tools, config_path)
            ok_sources = list({r["source"] for r in tool_results if r["ok"]})
            if ok_sources:
                yield f"data: {json.dumps({'type': 'mcp_fetch', 'sources': ok_sources})}\n\n"

        # ── Emit sources for attribution chips ────────────────────────────────
        sources_payload = [
            {"project": p, "section": chunks[0]["section"], "score": chunks[0]["score"]}
            for p, chunks in by_project.items()
        ]
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources_payload})}\n\n"

        # ── Build Memory Bank context ─────────────────────────────────────────
        context_parts = []
        for project, chunks in by_project.items():
            context_parts.append(
                f"### Memory Bank: [{project}]\n" + "\n\n".join(c["text"] for c in chunks)
            )

        org_book_ctx = ""
        if len(by_project) >= 2:
            try:
                from .org import ORG_BOOK_NAME
                org_path = Path(books_dir) / ORG_BOOK_NAME
                if org_path.exists() and "Organisation" not in by_project:
                    org_text = org_path.read_text(encoding="utf-8")
                    import re as _re
                    _sec = _re.compile(
                        r"^#{1,3}\s+.*?(TL;DR|Cross-project Map|Project Directory).*?\n(.*?)(?=^#{1,3}\s|\Z)",
                        _re.IGNORECASE | _re.MULTILINE | _re.DOTALL,
                    )
                    snippets = [m.group(2).strip()[:800] for m in _sec.finditer(org_text)]
                    org_book_ctx = (
                        "\n\n---\n\n### Org-Level Overview [Organisation Memory Bank]\n"
                        + ("\n\n".join(snippets[:3]) if snippets else org_text[:2000])
                    )
            except Exception:
                pass

        ticket_ctx = ""
        try:
            from .tickets import enrich_context_with_tickets
            ticket_ctx = enrich_context_with_tickets("\n\n".join(context_parts), question, config_path)
        except Exception:
            pass

        attribution_ctx = ""
        try:
            from .attribution import get_attribution_context
            attribution_ctx = get_attribution_context(question, books_dir)
        except Exception:
            pass

        org_chart_ctx = ""
        try:
            from .org_chart import should_inject, ownership_context_for
            if should_inject(question):
                org_chart_ctx = ownership_context_for(question)
        except Exception:
            pass

        # ── Build system prompt ───────────────────────────────────────────────
        has_tools = bool(tool_results)
        conn_err_note = (
            f"NOTE: MCP sources failed to connect: {'; '.join(mcp_connection_errors)}. "
            "Inform the user and suggest checking auth/config.\n\n"
            if mcp_connection_errors else ""
        )
        system = (
            "You are Memoria, an intelligent assistant with access to Memory Banks "
            "and live MCP tools.\n\n"
            + conn_err_note
            + "Rules:\n"
            + ("- Live tool results are provided below — use them as the PRIMARY source.\n"
               "- For write operations (create/update/delete), ONLY confirm success when "
               "the tool result explicitly shows it succeeded. If the result is an error "
               "or empty, report the failure clearly — never invent a success message.\n"
               if has_tools else
               "- No live tool data was fetched for this response; answer from Memory Banks.\n"
               "- Do not claim to have fetched live data in real time.\n")
            + ("- When ownership info is provided, include owner/team/contact details.\n"
               if org_chart_ctx else "")
            + ("- An org-level overview is included — use it for cross-project context.\n"
               if org_book_ctx else "")
            + "- Cite Memory Bank sources using **[ProjectName]** inline.\n"
            + "- If the content doesn't fully answer the question, say so.\n"
            + "- Be concise unless asked for depth.\n"
            + "- Format with Markdown where it helps.\n\n"
            + "## Memory Bank Context\n\n"
            + "\n\n---\n\n".join(context_parts)
            + org_book_ctx + ticket_ctx + attribution_ctx + org_chart_ctx
        )

        # ── Embed tool results directly in system prompt ──────────────────────
        # Tool results are embedded in the system prompt (not as message history)
        # for maximum cross-provider compatibility (works with Bedrock, Gemini, etc.)
        if tool_results:
            tool_parts = []
            for r in tool_results:
                status = "SUCCESS" if r["ok"] else "ERROR"
                tool_parts.append(
                    f"### [{r['source']} / {r['tool']}] — {status}\n{r['content']}"
                )
            system += (
                "\n\n## Live Tool Results (real data fetched just now)\n\n"
                + "\n\n---\n\n".join(tool_parts)
            )

        # ── Stream the answer ─────────────────────────────────────────────────
        async for event in _stream_answer(
            system, question, config_path,
            history=history or [],
            model_overrides=model_overrides or {},
        ):
            yield event

    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
