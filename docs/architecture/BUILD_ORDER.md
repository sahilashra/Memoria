# Build Order — Phase 1 Implementation Sequence

> Updated 2026-06-01. Phase 1 complete. Phase 2 items added.

---

## Status Key

- ✅ DONE — implemented and wired end-to-end
- 🔲 NEXT — not yet built; in dependency order

---

## Step 1 — `call_tool_async()` in `mcp_sources.py` ✅ DONE

Async MCP entry point safe to call inside FastAPI.
Also added: `list_tools_async()` for tool discovery, `_discover_tools()` in `agent/graph.py`.

---

## Step 2 — `async_complete()` on `ModelProvider` ✅ DONE

Non-blocking LLM calls via `litellm.acompletion()`.

---

## Step 3 — `memoria/core/structure.py` ✅ DONE

Sub-unit boundary detection. Three rules (applied recursively):
1. Package boundary files: `package.json`, `setup.py`, `pyproject.toml`, `pom.xml`, `build.gradle`, `__init__.py` at subdir root, `Cargo.toml`, `go.mod`
2. Test dirs: `test/`, `tests/`, `__tests__`, `spec/` + `*Test.java`, `*_test.py`, `*.spec.ts`
3. Size cutoff: any dir with >50 source files → subdivide recursively

```python
@dataclass
class SubUnit:
    path: str            # relative to project root
    type: str            # "module" | "tests" | "config" | "root"
    file_count: int
    children: list       # list[SubUnit]
    boundary_reason: str # "package_boundary" | "test_dir" | "size_cutoff"

def discover_structure(repo_path: str) -> list[SubUnit]:
    """Walk repo, apply 3 rules, return leaf sub-units."""
```

**Unblocks:** Step 4.

---

## Step 4 — Extend `generator.py` with sub-unit support ✅ DONE

Adds hierarchical generation alongside existing flat generation. Existing `generate()`, `_save_book()`, `smart_update()` are untouched.

New methods on `BookGenerator`:
- `generate_subunit(subunit, project_name, ...)` — generate one sub-unit Memory Bank
- `_save_subunit_book(content, project_name, subunit_path, output_dir)` — write to `books/{project}/{subunit_path}/_mb.md`, index in ChromaDB as `"{project}/{subunit_path}"`
- `generate_hierarchical(repo_path, project_name, ...)` — discovers structure, processes all sub-units in parallel via `asyncio.gather()`

**Unblocks:** Step 5.

---

## Step 5 — `memoria/core/file_graph.py` ✅ DONE

Intra-project file graph (separate from inter-project `_graph.json`).

- Pass A (static): `ast` for Python, regex for JS/TS; detect test↔source pairs, `depends_on` from imports, `calls_api` from HTTP client patterns
- Pass B (optional LLM): compare sub-bank TL;DRs for `same_domain` / `implements` edges
- Writes `books/{project}/.file_graph.json`

```python
@dataclass
class FileEdge:
    source: str       # sub-unit path
    target: str
    type: str         # "tests" | "depends_on" | "calls_api" | "same_domain"
    confidence: float
    detected_by: str  # "static" | "llm"

def build_file_graph(project_name: str, books_dir: str) -> list[FileEdge]:
```

**Unblocks:** Steps 6 (retrieval enrichment) and UI.

---

## Step 6 — `agent/retrieval.py` + `agent/context.py` ✅ DONE

Hybrid retrieval (ChromaDB + graph traversal + RRF) and context assembler.
Both are implemented and wired into `agent/graph.py`.

---

## Step 7 — `agent/graph.py` + `agent/capabilities/` ✅ DONE

LangGraph workflow (open-ended architecture).

Graph: `analyze_context → execute_actions → END`
Chat: `stream_work_chat()` uses assembled context as system prompt.
Capabilities: `code_gen.py`, `test_gen.py` — utility functions callable from chat, not pipeline nodes.

---

## Step 8 — New FastAPI routes ✅ DONE

All workflow routes wired:
- `POST /api/workflow/start`
- `GET /api/workflow/stream/{thread_id}`
- `POST /api/workflow/chat`
- `POST /api/workflow/actions/confirm`
- `POST /api/workflow/stop`
- `POST /api/workflow/write-local`
- `GET /api/dashboard/sync-tickets`

---

## Sub-Memory Bank UI — ✅ DONE

- **Sub-Memory Bank viewer** — Modules tab in UI. Renders `_mb.md` (Bank view) with file connections side panel (Files view). Inbound/outbound edges shown per sub-unit.
- **Spider web intra-project graph** — D3 force layout in `#tab-modules`. Nodes typed+colored. Edges colored by type (tests/depends_on/calls_api/same_domain/shares_types/implements). Click node → opens sub-bank panel.
- New API endpoints: `GET /api/graph/file/{project}`, `GET /api/subunits/{project}`, `GET /api/books/{project}/{subpath:path}`

---

## Phase 1 Summary — All Complete

| Step | Module | Status |
|---|---|---|
| 3 | `memoria/core/structure.py` | ✅ Done |
| 4 | `generator.py` sub-unit extension | ✅ Done |
| 5 | `memoria/core/file_graph.py` | ✅ Done |
| UI | Sub-Memory Bank viewer + spider web | ✅ Done |
| misc | `POST /api/skills/{project}` endpoint | ✅ Done |
| misc | `GET /api/admin/coverage` endpoint | ✅ Done |
| misc | Interview session endpoints | ✅ Done |

---

## Phase 2 — In Progress

### P2-1 — `memoria/connectors/actions.py` ✅ DONE

Transactional MCP write-back executor. Replaces the hand-rolled loop in `run_confirmed_actions`.

- **Dependency ordering** — `_topo_sort()` ensures branch is created before PR/MR
- **Per-action `ActionResult`** — status: `ok | failed | skipped | rolled_back`
- **Best-effort rollback** — on partial failure, reverses completed actions via `_ROLLBACK_PATTERNS`
- **`on_progress` callback** — caller gets SSE-ready events as each action completes
- `agent/graph.py::run_confirmed_actions` delegates to `execute_actions_transactional()`

---

### P2-2 — `memoria/audit.py` ✅ DONE

Append-only SQLite audit log (`audit.db`, co-located with `config.yaml` or `~/.memoria/`).

- `log_action(action, target, detail, actor, ok)` — write one record
- `query_log(actor, action, target, ok, since, limit, offset)` — filtered read, newest-first
- `export_csv(rows)` — serialize to CSV string
- Hooked into: `generator.py::_save_book` (`generate_book`), `run_confirmed_actions` (`mcp_tool_call:*`), `create_share_token` (`share_token_created`)

---

### P2-3 — Admin UI — All Sub-Panels ✅ DONE

New **Admin** sidebar nav item. Five sub-tabs:

| Sub-tab | What it shows |
|---|---|
| Coverage | Health stats (documented/undocumented/stale/gaps) + per-book table |
| Share Tokens | List + create + revoke consultant tokens |
| Interview | Guided Q&A → SSE synthesis → Process Memory Bank |
| Users | RBAC enable/disable, default role, grant/revoke per-user roles |
| Audit Log | Filterable table + pagination + CSV export |

New REST endpoints added in Phase 2:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/admin/rbac` | RBAC policy: enabled, default role, user list |
| `POST` | `/api/admin/rbac/users` | Grant or update a user's role |
| `DELETE` | `/api/admin/rbac/users/{username}` | Remove explicit role assignment |
| `PUT` | `/api/admin/rbac/settings` | Toggle RBAC on/off, set default role |
| `GET` | `/api/admin/audit` | Paginated audit log (filterable) |
| `GET` | `/api/admin/audit/export` | CSV export of audit log |

---

### P2-4 — Remaining (not yet started)

| Item | Module | Notes |
|---|---|---|
| Multi-worker SSE | `agent/graph.py` | Redis pub/sub, one channel per `thread_id` |
| ~~MCP session pooling~~ | ~~`mcp_sources.py`~~ | ✅ DONE — `_PoolEntry` + `AsyncExitStack`, subprocess kept alive between calls |
| Ghost Author flow | `ui.py` | `POST /api/admin/ghost-author` |
