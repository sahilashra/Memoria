# System Architecture — Memoria Business

> How the product is built: modules, data flow, storage, and integration model.

---

## Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     Memoria Business                          │
│                                                               │
│   Browser (Web UI)                                            │
│   └── FastAPI + SPA (ui/app.py, port 7860)                   │
│       │                                                       │
│       ├── Core Engine                                         │
│       │   ├── core/structure.py     Sub-unit discovery        │
│       │   ├── crawler.py            File reading              │
│       │   ├── generator.py          Memory Bank generation    │
│       │   ├── models.py             LiteLLM wrapper           │
│       │   ├── search.py             ChromaDB vector index     │
│       │   ├── meta.py               Project metadata          │
│       │   └── core/file_graph.py    Intra-project file graph  │
│       │                                                       │
│       ├── Knowledge Graph                                     │
│       │   └── graph.py              Inter-project edges       │
│       │                             (depends_on, same_domain) │
│       │                                                       │
│       ├── Adaptive Agent Layer (NEW)                          │
│       │   ├── agent/context.py      Ticket → gap detection    │
│       │   ├── agent/retrieval.py    Hybrid RAG (ChromaDB+RRF) │
│       │   ├── agent/graph.py        LangGraph workflow         │
│       │   └── agent/capabilities/                             │
│       │       ├── code_gen.py       Code skeleton generation  │
│       │       ├── test_gen.py       Test generation           │
│       │       └── design_skeleton.py Component skeleton (P2) │
│       │                                                       │
│       ├── Intelligence Layer                                  │
│       │   ├── brief.py              Morning Brief             │
│       │   ├── planner.py            Plan risk analysis        │
│       │   ├── surfacing.py          Contradiction + decay     │
│       │   ├── coverage.py           Knowledge health          │
│       │   ├── skills.py             SKILL.md export           │
│       │   └── interview.py          Expert interview          │
│       │                                                       │
│       ├── Connectors                                          │
│       │   ├── mcp_sources.py        MCP client (subprocess)   │
│       │   ├── mcp_server.py         MCP server (expose banks) │
│       │   ├── connectors/actions.py Transactional write-back  │
│       │   ├── github_source.py      GitHub PR/commit context  │
│       │   └── pull_state.py         Last-pull timestamps      │
│       │                                                       │
│       └── Enterprise                                          │
│           ├── rbac.py               Policy-file RBAC          │
│           ├── audit.py              Append-only audit log     │
│           ├── share.py              Consultant tokens         │
│           ├── sync.py               Team bundle export/import │
│           ├── digest.py             Executive digest          │
│           └── org.py                Org-level Memory Bank     │
│                                                               │
│   External                                                    │
│   ├── LiteLLM → any LLM provider                             │
│   ├── ChromaDB (local, embedded)                              │
│   ├── MCP servers (npx subprocesses: Jira, GitHub, Figma)    │
│   └── SQLite (audit.db, workflow_state.db)                   │
└──────────────────────────────────────────────────────────────┘
```

---

## Storage Layout

```
~/.memoria-business/
├── config.yaml               Model config, LLM params, books_dir, ignore_dirs
├── .env                      API keys
├── policy.yaml               RBAC policy (opt-in)
├── audit.db                  SQLite audit log (every action)
├── workflow_state.db         LangGraph SQLite checkpointer (active ticket sessions)
├── brief_state.json          Last brief timestamp
└── books/
    └── {project-name}/
        ├── _root.md          Root summary (generated last from sub-banks)
        ├── _graph.json       Inter-project knowledge graph
        ├── .file_graph.json  Intra-project file-level graph (NEW, Phase 1)
        ├── _meta.json        Structure tree, last analyzed, sub-unit metadata
        ├── .chromadb/        Embedded vector index (one collection per project)
        ├── _work_sessions/
        │   ├── PROJ-142.md           Session decisions log per ticket
        │   └── PROJ-142_design.md    Generated design skeleton (Phase 2, versioned)
        └── {subpath}/
            └── _mb.md        Sub-unit Memory Bank (one per module/test dir)
```

**Two separate graph files:**
- `_graph.json` — inter-project: projects as nodes, `depends_on`/`same_domain`/`shares_technology`/`references` edges. Built by `graph.py`. Used by the existing graph UI and blast-radius analysis.
- `.file_graph.json` — intra-project: source files and test files as nodes, `tests`/`depends_on`/`calls_api`/`implements` edges. Built by `core/file_graph.py`. Used by the context assembler to identify which modules a ticket touches.

---

## Data Flow — Memory Bank Generation (Hierarchical)

```
1. Structure Discovery (core/structure.py)
   Walk repo → apply 3 rules:
   ① Package boundary files → always split
     (package.json, setup.py, pyproject.toml, pom.xml, __init__.py at subdir root)
   ② Test directories → always split
     (test/, tests/, __tests__, spec/ + *Test.java, *_test.py, *.spec.ts)
   ③ >50 source files in a dir → subdivide recursively
   Output: StructureTree of sub-units (path, type, file_count, boundary_reason)

2. Parallel Processing (generator.py)
   asyncio.gather(*[generate_subunit(unit) for unit in leaf_units])
   Per worker:
   ① RepoCrawler reads files in sub-unit path
   ② Chunk content to model context limit
   ③ LLM extract: architecture, models, workflows, gotchas, ownership
   ④ Synthesize chunks → sub-Memory Bank
   ⑤ Write: books/{project}/{subpath}/_mb.md
   ⑥ Index in ChromaDB collection for project

3. Connection Inference (core/file_graph.py)
   Pass A — static (no LLM):
     test↔source: name matching (AuthServiceTest.java → AuthService.java)
                  path mirroring (tests/auth/ → src/auth/)
     depends_on: parse import statements (ast.parse for Python, regex for JS/TS)
     calls_api: HTTP client patterns (requests.get, fetch, axios) + URL strings
   Pass B — LLM semantic:
     compare TL;DR sections of sub-bank pairs
     edges: same_domain, shares_types, implements
   Output: books/{project}/.file_graph.json (nodes: sub-unit paths; typed edges)

4. Root Summary (generator.py, last)
   LLM synthesizes from: all sub-bank TL;DRs + file graph summary + project metadata
   Output: books/{project}/_root.md
```

---

## Data Flow — Adaptive Work Agent (Phase 1)

```
1. Context Assembly (agent/context.py)
   Input: ticket_id (from Jira via MCP) or free-text description (no MCP required)
   ① Extract work-type signals: labels, keywords, file mentions, linked assets
   ② Asset existence check (if MCP configured): Figma linked? branch exists? tests exist?
   ③ Hybrid retrieval (agent/retrieval.py):
      - Embed ticket text → ChromaDB top-k matches
      - Matched project paths → traverse .file_graph.json edges for neighbors
      - Reciprocal Rank Fusion (RRF) merge of both result sets
   ④ Build capability gap list: ["needs_implementation", "needs_tests"] (etc.)
   Output: WorkContext { ticket, relevant_sub_banks, file_graph_slice, capability_gaps }

2. LangGraph Workflow (agent/graph.py)
   WorkState seeded with: WorkContext + chat_agents.build_agent_context(project)
   Conditional routing:
   - needs_implementation? → code_gen node
   - needs_tests? → test_gen node
   - needs_design? (Phase 2) → design_skeleton node
   Each capability node:
   ① Generate artifact using retrieval results + ticket context
   ② INTERRUPT → emit SSE event: artifact_ready + artifact content
   ③ UI presents for review; user approves or rejects with reason
   ④ POST /api/workflow/approve → LangGraph resumes from interrupt
   ⑤ If rejected: reason appended to next generation prompt; retry
   ⑥ After 3 rejections: stop, prompt user for explicit guidance

3. Action Execution (Phase 2 — connectors/actions.py)
   After all capability gaps resolved:
   Pre-flight: check MCP token scopes before any write
   For each action: show confirmation dialog → user confirms → call_tool_async()
   Transactional: on partial failure, surface recovery state (branch name, etc.)
```

---

## Module Responsibilities (Quick Reference)

| Module | What it owns | What it calls |
|---|---|---|
| `core/structure.py` | Sub-unit boundary detection | filesystem only |
| `core/file_graph.py` | Intra-project file relationships | `ast`, regex |
| `crawler.py` | File reading, encoding, token budgeting | filesystem |
| `generator.py` | Memory Bank text synthesis | `crawler`, `models`, `search` |
| `models.py` | All LLM calls (sync + async) | `litellm` |
| `search.py` | ChromaDB index read/write | `chromadb` |
| `graph.py` | Inter-project knowledge graph | `models`, `search` |
| `agent/context.py` | Ticket analysis, capability gap detection | `mcp_sources`, `agent/retrieval` |
| `agent/retrieval.py` | Hybrid RAG retrieval | `search`, `core/file_graph` |
| `agent/graph.py` | LangGraph workflow orchestration | `agent/context`, `agent/capabilities/*`, `chat_agents` |
| `agent/capabilities/code_gen.py` | Code skeleton generation | `agent/retrieval`, `models` |
| `agent/capabilities/test_gen.py` | Test file generation | `agent/retrieval`, `models` |
| `mcp_sources.py` | MCP subprocess client | `npx` subprocesses |
| `connectors/actions.py` | Transactional write-back: dep ordering, rollback | `mcp_sources.call_tool_async` |
| `audit.py` | Append-only SQLite audit log | `sqlite3`, `rbac` |
| `rbac.py` | Policy-file RBAC: roles, permissions, scopes | `yaml`, filesystem |
| `coverage.py` | Knowledge health analysis | `search`, filesystem |
| `interview.py` | Expert interview Q&A + synthesis | `models` |
| `skills.py` | SKILL.md export from Memory Banks | `models` |
| `chat_agents.py` | Persona-based REPL agents, `build_agent_context()` | `models`, `search` |
| `brief.py` | Morning Brief synthesis | `graph`, `mcp_sources`, `rbac` |

---

## Key Constraints

**LangGraph + FastAPI + SSE (Phase 1):**
The LangGraph `interrupt()` mechanism suspends a coroutine in-process. `POST /api/workflow/start` holds an SSE connection in one process. `POST /api/workflow/approve` must hit the same process to resume the suspended workflow. In Phase 1 (single Uvicorn worker), this works naturally via a process-level `asyncio.Queue` dict keyed by `thread_id`. Multi-worker deployments (Phase 2) require Redis pub/sub — one channel per `thread_id` — so the approve call can reach the correct worker regardless of load balancer routing.

**MCP `asyncio.run()` constraint:**
The existing `pull_source()` function in `mcp_sources.py` calls `asyncio.run(_pull_async(...))`. This cannot be called from inside FastAPI's async event loop (raises `RuntimeError: This event loop is already running`). Before any agent or action endpoint can be built, `mcp_sources.py` must expose a public `async def call_tool_async(server, tool, args, env)` entry point that can be `await`ed directly. This is the highest-priority prerequisite for Phase 1.

**ChromaDB singleton:**
The existing `search.py` and the new `agent/retrieval.py` both need ChromaDB access. Do not instantiate two `PersistentClient` instances pointing at the same directory — SQLite lock conflicts. One shared client instance, accessible via a module-level singleton in `search.py`, used by both.
