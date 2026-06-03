# Project Audit — What Exists, What's Missing, What to Remove

> Generated 2026-05-21 from full codebase scan.
> This file is for review. Mark items with ✓ CONFIRMED / ✗ SKIP / ~ ADJUST before the build starts.

---

## How to Read This

- **EXISTS** — real, working implementation found in codebase
- **EXISTS (no API)** — module exists and works, but not exposed as a REST endpoint yet
- **PARTIAL** — something close exists but doesn't match what the roadmap requires
- **MISSING** — does not exist at all, needs to be built
- **REMOVE** — exists but is out of scope; should be deleted
- **RENAME/ALIAS** — exists under a different name; needs a quick update

---

## Phase 0 — Foundation

| Item | Status | File / Endpoint | Action Required |
|---|---|---|---|
| Settings screen (model config + API key) | **EXISTS** | `memoria/ui.py` → `GET/POST /api/settings`; `memoria/static/index.html` has `.settings-section` UI | None |
| `POST /api/settings/test` (validate API key) | **RENAME/ALIAS** | `POST /api/test-model` exists at line 1897 of `ui.py` — returns `{ok, model, response, error}`, same purpose | Rename endpoint to `/api/settings/test` OR update roadmap to reference `/api/test-model`. One-line change. |
| Memory Bank viewer — rendered Markdown | **EXISTS** | `index.html` has `.markdown-renderer` CSS + JS markdown rendering; `/api/books/{project}` returns raw Markdown | None for basic rendering. The A/B toggle (Markdown ↔ file tree) does NOT exist — that's Phase 1. |
| Analysis progress SSE | **EXISTS** | `GET /api/analyze-stream` (line 849, `ui.py`); emits `type: progress`, `type: done`, `type: error` | None |
| Graph build SSE | **EXISTS** | `GET /api/graph/build-stream` (line 660, `ui.py`) | None |
| Onboarding wizard | **PARTIAL** | The Analyze panel in `index.html` has a 5-step flow (`#ap-step-input`, `#ap-step-single`, `#ap-step-multi`, `#ap-step-progress`, `#ap-step-done`). This is for path selection + analysis. It is NOT a full onboarding wizard (no model config step, no MCP connection step, no "first Memory Bank in 10 minutes" UX). | Build the onboarding wizard as a new screen that shows on first visit (when `GET /api/settings` returns empty model). Reuse the analyze panel as step 3. Reuse `/api/test-model` for step 1 connection test. |
| MCP config via JSON paste | **PARTIAL** | MCP sources managed via `POST /api/sources/add` (line in `ui.py`); Sources tab in UI has add/remove. BUT config format in backend is YAML-based per-source, not the single-JSON Claude Desktop format | The JSON paste UI (paste one blob for all sources) doesn't exist. Current UI is per-source form fields. This needs a new input mode. |

**Phase 0 Summary:** Most of the backend is done. What's genuinely missing is the onboarding wizard as a first-run experience, and the Claude Desktop-format JSON paste for MCP config. Everything else just needs minor aliasing or connecting.

---

## Phase 1 — Foundations That Already Exist

These items are listed in the roadmap as "Foundation — What Exists" and the audit confirms they're real, working code:

| Item | Status | File |
|---|---|---|
| Repo crawler | **EXISTS** | `memoria/crawler.py` |
| Memory Bank generation (chunked) | **EXISTS** | `memoria/generator.py` — `BookGenerator.generate()`, `_generate_chunked()` |
| LiteLLM model wrapper | **EXISTS** | `memoria/models.py` — `ModelProvider` class |
| Smart update (diff-only re-analysis) | **EXISTS** | `memoria/generator.py` — `smart_update()` |
| ChromaDB semantic search | **EXISTS** | `memoria/search.py`; auto-indexed; `GET /api/search` |
| Full-text search | **EXISTS** | `GET /api/fulltext` in `ui.py` |
| Per-project metadata | **EXISTS** | `memoria/meta.py` |
| Archive + auto-purge | **EXISTS** | `memoria/generator.py` — archive logic |
| Inter-project knowledge graph | **EXISTS** | `memoria/graph.py` — `build_graph()`, `find_edges()`, `impact_analysis()`; `GET /api/graph` |
| Transitive impact analysis | **EXISTS** | `memoria/graph.py` — `impact_analysis()`; `GET /api/graph/impact/{project}` |
| Morning Brief | **EXISTS** | `memoria/brief.py` — `run_brief()`; `GET /api/brief/stream` |
| Plan Analysis | **EXISTS** | `memoria/planner.py`; `POST /api/plan/stream`; drift tracking, snapshot, Q&A all wired |
| Contradiction detection + decay | **EXISTS** | `memoria/surfacing.py` (top-level, NOT `intelligence/surfacing.py`) |
| Knowledge coverage analysis | **EXISTS (no API)** | `memoria/coverage.py` — real implementation; NOT exposed via REST |
| Skills File generator | **EXISTS (no API)** | `memoria/skills.py` — `generate_skill()`; NOT exposed via REST |
| Expert interview mode | **EXISTS (no API)** | `memoria/interview.py` — `run_interview()`; CLI-only; NOT exposed via REST |
| MCP connector layer | **EXISTS** | `memoria/mcp_sources.py` — sync + async; `GET /api/sources`, `GET /api/pull-stream/{source}` |
| MCP server (exposes Memory Banks to agents) | **EXISTS** | `memoria/mcp_server.py` — `memoria serve --mcp` |
| Scheduled MCP pulls | **EXISTS** | `memoria/scheduler.py` + `schedule:` field in source config |
| GitHub connector | **EXISTS** | `memoria/github_source.py`; `POST /api/pull-github/{project}` |
| RBAC | **EXISTS** | `memoria/rbac.py` — 4 roles, 6 permissions, `policy.yaml`; integrated into `/api/search` + `/api/global-ask` |
| Consultant share tokens | **EXISTS (no API)** | `memoria/share.py` — full implementation; token validation in middleware; no standalone REST endpoints for management |
| Team sync | **EXISTS** | `memoria/sync.py` |
| Executive digest | **EXISTS** | `memoria/digest.py`; `GET /api/digest/stream` |
| Org-level Memory Bank | **EXISTS** | `memoria/org.py` |
| Ticket detection + resolution | **EXISTS** | `memoria/tickets.py`; `GET /api/tickets`, `POST /api/tickets/resolve` |
| Attribution chains (ticket → PR → Memory Bank) | **EXISTS** | `memoria/attribution.py`; `POST /api/attribution/build`, `GET /api/attribution` |
| Agent context API | **EXISTS** | `memoria/agents.py` — `build_agent_context()`; `GET /api/agent-context/{project}` |
| Change impact notifications | **EXISTS** | `memoria/impact_notifier.py` |
| Web UI (FastAPI + SPA) | **EXISTS** | `memoria/ui.py` (3,037 lines); `memoria/static/index.html` (6,000+ lines) |
| SSE streaming pattern | **EXISTS** | Used throughout `ui.py` — reusable `StreamingResponse` + `event_generator()` pattern |
| Graph visualization | **EXISTS** | D3-based force-directed graph in `index.html` |
| Update review queue | **EXISTS** | `GET /api/drafts`, `GET /api/update-diff/{project}`, `POST /api/update-apply/{project}` |

---

## Phase 1 — What Needs to Be Built

Everything below is genuinely missing from the codebase.

### 1. MCP Async Entry Point (`mcp_sources.py`)
**Status: ✅ DONE**

`call_tool_async` is implemented in `mcp_sources.py`. `list_tools_async` was also added.

---

### 2. `async_complete()` on `ModelProvider` (`models.py`)
**Status: ✅ DONE**

Implemented in `models.py`. Existing `complete()` untouched.

---

### 3. `core/structure.py` — Sub-unit boundary detection
**Status: MISSING**

The hierarchical Memory Bank architecture requires a module that walks the repo and identifies module/test/package boundaries. Nothing in `generator.py` does this — it processes the whole repo as one unit.

**Action:** Create `memoria/core/__init__.py` and `memoria/core/structure.py`. Implement `discover_structure(repo_path) -> list[SubUnit]` with the 3-rule system (package boundaries, test dirs, >50 files).

---

### 4. Extend `generator.py` for per-sub-unit generation
**Status: PARTIAL**

`generator.py` handles large repos by chunking (lines 933+) but the output is always one flat `{project}_memory_bank.md`. The hierarchical architecture requires `books/{project}/{subpath}/_mb.md` per sub-unit.

**Action:** Add `generate_subunit()` and `_save_subunit_book()` and `generate_hierarchical()` to `BookGenerator`. Do NOT touch existing `generate()`, `_save_book()`, or `smart_update()`. Additive only.

---

### 5. `core/file_graph.py` — Intra-project file graph
**Status: MISSING**

`graph.py` builds an inter-project graph (projects as nodes). The new architecture needs an intra-project graph (sub-units as nodes: `test↔source`, `depends_on`, `calls_api`).

**Action:** Create `memoria/core/file_graph.py`. Write to `books/{project}/.file_graph.json` (separate from `books/.graph.json`). `graph.py` untouched.

---

### 6. `memoria/agent/context.py` — Capability gap detection + context assembler
**Status: ✅ DONE**

Implemented.

---

### 7. `memoria/agent/retrieval.py` — Hybrid RAG
**Status: ✅ DONE**

Implemented.

---

### 8. `memoria/agent/graph.py` — LangGraph workflow
**Status: ✅ DONE**

Implemented. Architecture changed: open-ended chat replaces rigid generate/approve pipeline. `stream_work_chat()` is the agent loop.

---

### 9. `memoria/agent/capabilities/code_gen.py` and `test_gen.py`
**Status: ✅ DONE**

Both implemented. These are utility functions callable from chat, not mandatory pipeline nodes.

---

### 10. New FastAPI routes in `ui.py`
**Status: PARTIAL**

| Endpoint | Status |
|---|---|
| `POST /api/workflow/start` | ✅ DONE |
| `POST /api/workflow/actions/confirm` | ✅ DONE |
| `POST /api/workflow/stop` | ✅ DONE |
| `POST /api/workflow/approve` | REMOVED (no longer needed — open-ended architecture) |
| `POST /api/workflow/chat` | ✅ DONE (new) |
| `GET /api/context/:ticketId` | PARTIAL (context assembly works via workflow start) |
| `POST /api/mcp/fetch` | MISSING |
| `POST /api/skills/{project}` | MISSING (`skills.py` exists, no endpoint) |
| `GET /api/admin/coverage` | MISSING (`coverage.py` exists, no endpoint) |
| `POST /api/interview` SSE | MISSING (`interview.py` exists, no endpoint) |

---

### 11. `/work/:ticketId` — Work Item Detail screen
**Status: PARTIAL**

Core UI done. Three panels exist (left: ticket info + gaps, center: chat + agent, right: context). Sub-Memory Bank viewer and graph slice not yet built.

---

### 12. `/dashboard` — Work Items Dashboard
**Status: PARTIAL (name collision — see note)**

> **IMPORTANT:** `memoria/dashboard.py` EXISTS but it is the personal activity tracking dashboard (runs on port 7861). This is **out of scope** and should be removed (see Removals section). The new `/dashboard` is a completely different thing — a work items view showing assigned Jira/Linear tickets with capability gap badges. These must not be confused.

Basic dashboard with live MCP ticket loading is DONE. Live loading works and gap badges are shown. Full context cards on hover are not yet built.

Work session auto-save is also DONE (`_write_session_log()` in `agent/graph.py`).

---

### 13. Sub-Memory Bank viewer (A/B toggle) + Spider web intra-project graph UI
**Status: MISSING**

The existing graph UI shows the inter-project graph only. No sub-Memory Bank viewer with file tree toggle exists.

**Action:** New `/projects/:name/:subpath` route + view. New D3 graph variant for intra-project spider web (different data source: `.file_graph.json`). A view: rendered Markdown. B toggle: file tree. Side panel: connections.

---

### 14. Share token management REST API
**Status: EXISTS (no API)**

`share.py` has `generate_token()`, `revoke_token()`, `list_tokens()` fully implemented. No REST endpoints expose this — share tokens are enforced in middleware but cannot be managed via the UI.

**Action:** Add `GET/POST/DELETE /api/share/tokens` endpoints to `ui.py`. Two-hour task.

---

### 15. RBAC management REST API
**Status: EXISTS (no API)**

`rbac.py` has `grant_role()`, `revoke_role()`, `get_user_permissions()` fully implemented. Only implicitly used in search filtering — not manageable via UI.

**Action:** Add `GET/POST /api/admin/rbac` endpoints. Route to admin screen in Phase 2.

---

## Phase 2 — What Needs to Be Built

| Item | Status | Notes |
|---|---|---|
| `connectors/actions.py` — MCP write actions | **MISSING** | `create_branch`, `create_pull_request`, `update_issue`, `post_message` |
| Confirmation dialog (pre-action) | **MISSING** | UI component for approve-before-execute |
| Transactional action model (partial failure recovery) | **MISSING** | Especially for `create_branch` + `create_pull_request` sequence |
| Admin screen (`/admin` route) | **MISSING** | Tabs: Users, Audit, Ghost Author, Coverage, Digest |
| Share token management UI | **MISSING** | Backend exists; REST API endpoints missing (see Phase 1 #14) |
| Ghost Author flow API endpoint | **MISSING** | `ghost_author.py` (if it exists) not exposed |
| Interview UI (browser-based) | **MISSING** | `interview.py` is CLI-only; needs SSE endpoint + UI panel |
| Redis + LangGraph multi-worker resume | **MISSING** | Phase 1 runs single-worker; Phase 2 needs Redis for horizontal scale |
| MCP session pooling (`MCPSessionPool`) | **MISSING** | Phase 1 per-call subprocess is OK; Phase 2 needs persistent sessions |
| Design skeleton generation capability | **MISSING** | `agent/capabilities/design_skeleton.py` |

---

## Phase 3 — What Needs to Be Built

| Item | Status | Notes |
|---|---|---|
| Coverage dashboard UI | **MISSING** | `coverage.py` exists and works; needs UI surface + `/api/admin/coverage` endpoint |
| Demo mode (sample data) | **MISSING** | No demo/sample data exists |
| Design system extraction (Figma MCP scan) | **MISSING** | No dedicated Figma Memory Bank concept yet |
| Mobile-responsive layout | **MISSING** | Current SPA is desktop-only |
| Executive Digest config UI | **MISSING** | `digest.py` exists; `GET /api/digest/stream` works; no config UI |

---

## Files to Remove

These files exist in the codebase but are **out of scope for Memoria Business**. They should be deleted.

| File | Reason |
|---|---|
| `memoria/listener.py` | Voice interface (`memoria listen`). Personal consumer feature. No team value. |
| `memoria/companion.py` | Ambient overlay (`memoria companion`). Desktop-only, separate project concept. |
| `memoria/tracker.py` | Personal work tracker (`memoria track`). Individual activity tracking, not a team tool. |
| `memoria/dashboard.py` | Personal tracking dashboard (`memoria dashboard`). Runs on port 7861. **Distinct from and must not be confused with the new work-items `/dashboard` route being built.** |
| `memoria/observer.py` | Passive file observer (`memoria observe`). Surveillance-adjacent, parked. |
| `memoria/learner.py` | Semantic workflow learner (`memoria learn`). Depends on telemetry activity.db. Parked. |
| `memoria/intelligence/connectors.py` | Telemetry Pillar 1 — event ingestion from connector pulls into `activity.db`. Out of scope. |
| `memoria/intelligence/segmentation.py` | Telemetry Pillar 2 — session boundary detection, HDBSCAN. Out of scope. |
| `memoria/intelligence/modeling.py` | Telemetry Pillar 3 — AI modeling of telemetry sessions. Out of scope. |
| `memoria/intelligence/surfacing.py` | Telemetry Pillar 4 output — NOT the same as `memoria/surfacing.py` (which is kept). This one reads from `activity.db` telemetry. Out of scope. |
| `memoria/intelligence/db.py` | Telemetry activity database (`activity.db`). Supports the parked telemetry pillars. |
| `memoria/intelligence/privacy.py` | DPAPI/Keychain encryption for `activity.db`. Only needed if telemetry is enabled. |

> After removing the above, the `memoria/intelligence/` directory will be empty. Its `__init__.py` should also be removed. The new agent modules go under `memoria/agent/` (new sub-package).

---

## Files to Rename / Update

| File | Change | Reason |
|---|---|---|
| `memoria/agents.py` | Rename to `memoria/chat_agents.py` | Avoids name confusion with new `memoria/agent/graph.py` LangGraph workflow. Existing `/api/agent-context/{project}` and `/api/agents/{project}` endpoints stay intact — just update the import in `ui.py`. |
| `POST /api/test-model` | Alias as `POST /api/settings/test` | Roadmap and onboarding wizard reference `/api/settings/test`. Existing `/api/test-model` works. Either rename or add an alias route. |

---

## Docs to Archive / Remove

These doc files describe features that are out of scope (telemetry pillars):

| File | Status |
|---|---|
| `docs/intelligence_engine/PILLAR_1_TELEMETRY.md` | Archive — telemetry parked |
| `docs/intelligence_engine/PILLAR_2_SEGMENTATION.md` | Archive — segmentation parked |
| `docs/intelligence_engine/PILLAR_3_AI_MODELING.md` | Archive — AI modeling parked |
| `docs/intelligence_engine/PILLAR_4_AUTOMATION.md` | Partial — Pillar 4 (contradiction detection, decay scoring) is kept in `surfacing.py` but the telemetry-based automation output is removed. Review before deleting. |
| `docs/intelligence_engine/PILLAR_5_PRIVACY.md` | Archive — privacy layer was for `activity.db` encryption |
| `docs/intelligence_engine/README.md` | Archive — describes the full telemetry engine |
| `docs/vision/PERSONAL_BRAIN.md` | Archive — personal brain is a separate future product |
| `docs/roles/` (all files) | Archive — role-specific docs from the old role-selection model. The adaptive agent makes explicit roles unnecessary. Keep as reference but remove from active docs. |

---

## Revised Phase 0 Scope

Based on the audit, here is what Phase 0 actually requires (separating "already done" from "genuinely new work"):

### Already done — no work needed:
- Settings screen + `GET/POST /api/settings` ✓
- Memory Bank viewer (rendered Markdown) ✓
- Analysis progress SSE (`/api/analyze-stream`) ✓
- Connection test (`POST /api/test-model`) ✓

### New work for Phase 0:
1. **Onboarding wizard** — new first-run screen; 3 steps; connect model config (reuse `/api/test-model`), connect MCP tools (new JSON paste mode), analyze first project (reuse analyze panel). Estimated: 3–4 days frontend.
2. **MCP JSON paste input** — new input mode in settings/onboarding that accepts the Claude Desktop single-JSON format and converts to per-source config internally. Estimated: 1 day.
3. **`POST /api/settings/test` alias** — 30-minute change.

### Files to delete before Phase 0:
Remove the out-of-scope files listed above so the codebase is clean before new development starts.

---

## Quick Reference — Build Priority Order

Based on dependencies:

```
DELETE out-of-scope files first (clean slate)
    ↓
RENAME agents.py → chat_agents.py
    ↓
Phase 0:
  1. Add POST /api/settings/test alias (30 min)
  2. Build onboarding wizard UI (3-4 days)
  3. MCP JSON paste input mode (1 day)
    ↓
Phase 1 prerequisites (must be in this order):
  4. call_tool_async() in mcp_sources.py (2-4 hours)
  5. async_complete() in models.py (1-2 hours)
  6. core/structure.py (2-3 days)
  7. generator.py sub-unit extension (3-4 days)
  8. core/file_graph.py (2-3 days)
  9. agent/retrieval.py + agent/context.py (2-3 days)
 10. agent/graph.py + agent/capabilities/ (4-5 days)
 11. New FastAPI routes (2 days)
    ↓
Phase 1 UI:
 12. /work/:ticketId screen (3-4 days)
 13. /dashboard work items view (2-3 days)
 14. Sub-Memory Bank viewer + spider web graph (3-4 days)
    ↓
Phase 2:
 15. connectors/actions.py (2-3 days)
 16. Admin screen + share token API (2-3 days)
```
