# Memoria — Capabilities Reference

> Detailed documentation for every feature. See README for quickstart and overview.

---

## Table of Contents

1. [What Memoria Can Analyze](#1-what-memoria-can-analyze)
2. [Memory Bank Structure](#2-memory-bank-structure) — including Process/SOP type
3. [CLI Commands Reference](#3-cli-commands-reference) — including `interview`, `skills`, `coverage`, `serve --mcp`
4. [Knowledge Graph](#4-knowledge-graph) — cross-project relationship tracking
5. [Role-Based Access Control](#5-role-based-access-control) — who can do what
6. [Chat UI](#6-chat-ui) — browser-based interface for non-technical users
7. [MCP Source Connectors](#7-mcp-source-connectors) — including scheduled + incremental pulls
8. [Auto-Update Hooks](#8-auto-update-hooks)
9. [Team Workflows — Split & Merge](#9-team-workflows--split--merge)
10. [Config Reference](#10-config-reference)
11. [Optional Dependencies](#11-optional-dependencies)
12. [AI Provider Setup](#12-ai-provider-setup)

---

## 1. What Memoria Can Analyze

### Code Repositories
Any language or framework. Memoria reads source files, understands structure, and produces a developer-focused Memory Bank covering architecture, data flow, key files, workflows, and gotchas.

Signals that identify a code repo: `package.json`, `requirements.txt`, `pyproject.toml`, `.git`, `go.mod`, `Cargo.toml`, `Makefile`, `Dockerfile`, `pom.xml`, etc.

### Document Collections
Folders containing PDFs, Word docs, spreadsheets, or presentations — Certifications folders, research archives, onboarding packs, compliance documents. Memoria produces a document-focused Memory Bank covering key topics, important facts and data, conclusions, and a quick-reference index.

| Format | Extension | Notes |
|---|---|---|
| PDF | `.pdf` | Auto-chunked by page groups for large files (100+ pages) |
| Word | `.docx` | Headings, paragraphs, tables extracted |
| PowerPoint | `.pptx` | Slide titles, content, speaker notes |
| Excel / CSV | `.xlsx`, `.csv` | Sheet names, data summaries, key values |
| HTML | `.html`, `.htm` | Confluence/Notion/SharePoint exports, wikis |

### Jupyter Notebooks
Code cells, markdown cells, and outputs (including error messages) are all captured. Memoria produces a notebook-focused Memory Bank covering the research question, methodology, findings, and how to re-run the analysis.

### Images
PNG, JPG, WEBP, GIF, BMP — architecture diagrams, screenshots, wireframes, photos. Analyzed via the configured vision model. Requires a model that supports vision (Gemini, Claude, GPT-4o).

### Audio
MP3, WAV, M4A — meeting recordings, voice notes, lectures. Transcribed locally via Whisper (free, no API key, runs on your machine). Produces a transcript-focused Memory Bank with key topics, decisions, action items, and timestamps.

```bash
pip install memoria[audio]
# Also requires ffmpeg: https://ffmpeg.org/download.html
```

### Video
MP4, MOV, AVI, MKV, WEBM — screen recordings, demos, conference talks, standups. Produces a Memory Bank that combines **both channels**:

| Channel | What it captures | Requires |
|---|---|---|
| Audio transcript | Everything spoken — timestamped with Whisper | `openai-whisper` + ffmpeg |
| Visual frames | Key frames sampled every 10–60s, described by vision model | `opencv-python` |

Both channels degrade independently — if only Whisper is installed you get the transcript; if only `opencv-python` is installed you get frame descriptions. The Memory Bank uses whichever channels are available.

```bash
pip install memoria[audio]        # audio track only
pip install memoria[video]        # visual frames only  (pip install opencv-python)
pip install memoria[audio,video]  # full: audio + visual frames
```

**Frame sampling:** adaptive to video length — every 10s for short clips, every 30s for medium, every 60s for long. Maximum 8 frames per video to keep API costs low.

### Process Documentation & SOPs
Runbooks, playbooks, SOPs, workflows, checklists, incident procedures, deployment guides. Auto-detected from filename keywords (`runbook`, `sop`, `playbook`, `procedure`, `incident`, `oncall`, `deployment`, `escalation`, etc.) or content signals. Produces a **Process Memory Bank** with structured steps, decision trees, escalation paths, and confidence scores. Force with `--type process`.

### Live Data via MCP
Any system that exposes an MCP server — Confluence, Notion, Teams, Slack, Linear, Jira, and more. See [Section 7](#7-mcp-source-connectors) for full setup.

---

## 2. Memory Bank Structure

Every Memory Bank is a single Markdown file. Sections adapt to content type.

### Code Repository
```
## TL;DR
3-5 bullets. The whole project in 30 seconds.

## 1. What This Project Does
Purpose, users, problem solved, what success looks like.

## 2. Architecture — How It Works
End-to-end data flow. What calls what. Why each layer exists.

## 3. File & Module Map
Every important file — one line each.

## 4. Key Concepts & Data Models
Core abstractions, schemas, types. How they relate.

## 5. How to Run Locally
Exact commands. Env vars. Common setup errors.

## 6. Common Workflows
Step-by-step for the 3-5 most frequent developer tasks.

## 7. Non-Obvious Decisions & Gotchas
Traps, shortcuts, things that look wrong but aren't.

## 8. What's Broken / Missing / Next
Known bugs, tech debt, what needs to be built next.
```

### Document Collection
```
## TL;DR
## 1. What This Collection Is About
## 2. Key Topics & Sections
## 3. Important Data & Facts
## 4. Conclusions & Recommendations
## 5. Open Questions & Gaps
## 6. Quick Reference
```

### Audio / Meeting Transcript
```
## TL;DR
## 1. What This Recording Is About
## 2. Key Topics Discussed
## 3. Decisions Made
## 4. Action Items
## 5. Open Questions
## 6. Full Transcript (timestamped)
```

### Jupyter Notebook
```
## TL;DR
## 1. Purpose & Question
## 2. Data & Sources
## 3. Methodology
## 4. Findings
## 5. How to Re-Run
## 6. Limitations & Next Steps
```

### Process / SOP
Generated when the source contains SOPs, runbooks, playbooks, or workflow documentation. Auto-detected from filename keywords (`sop`, `runbook`, `playbook`, `procedure`, `workflow`, `incident`, etc.) or content signals (`step 1`, `trigger:`, `decision point`). Force with `--type process`.

```
## TL;DR
## Process Overview (name, owner, trigger, scope, frequency, confidence)
## Step-by-Step Procedure (numbered, with who/tool/action/output per step)
## Decision Points & Branching Logic (if/then/else table)
## Exceptions & Edge Cases
## Escalation Paths (situation / contact / SLA)
## Tools & Resources Required
## Confidence & Verification Status (per section)
## Known Issues & Improvement Notes
```

### Versioning
Each Memory Bank is a single file. Previous versions are archived automatically to `books/.archive/` before every overwrite, with a timestamp in the filename. Archives older than 30 days are purged automatically (configurable via `archive_ttl_days` in config.yaml).

---

## 3. CLI Commands Reference

### `memoria init`
One-time setup. Creates `~/.memoria/config.yaml` and `~/.memoria/.env` with your AI provider and API key. Run this once after install — `memoria` then works from any directory.

```bash
memoria init
```

### `memoria analyze`
Analyze any path — code repo, document folder, single file — and generate its Memory Bank.

```bash
memoria analyze                                    # interactive prompts
memoria analyze --repo ./my-project               # code repo
memoria analyze --repo ./Certifications           # document folder
memoria analyze --repo ./standup.m4a              # audio file
memoria analyze --repo ./diagram.png              # image
memoria analyze --repo ./runbooks --type process  # force process/SOP mode
memoria analyze --repo ./my-project --yes         # skip confirmation
memoria analyze --repo ./my-project --output ./team-books
```

`--type` can be: `code`, `document`, `audio`, `video`, `notebook`, `process`. Useful when auto-detection doesn't pick the right mode (e.g. a runbook stored as `.md` files).

**Guards:**
- Warns if a Memory Bank already exists, offers update or abort
- Context question adapts to what's being analyzed (asks "What is this document collection?" not "What does this project do?" for document folders)

**Multi-project detection:**  
When a path containing multiple project-like subdirectories is passed, `analyze` intercepts automatically and presents three options instead of generating one giant unfocused book:

```
Found 10 projects in C:\Sahil\Projects

  Projects/
  [1]  career-ops              — not analyzed
  [2]  codeprism               ✓ analyzed 2026-04-27
  [3]  memoria                 — not analyzed
  ...

  [A] Analyze each separately  (recommended)
  [B] Analyze as one combined book
  [C] Pick which ones
```

- **A** (default): analyze each unanalyzed project in sequence; already-analyzed ones offer update or skip; monorepos (projects containing sub-projects) ask whether to drill in or treat as one
- **B**: single combined book — original behaviour, user consciously opts in
- **C**: numbered picker for selective batch analysis

Detection: 2 levels deep, groups by parent folder, skips `node_modules`/`venv`/`.git`/`dist` etc.

### `memoria update`
Regenerate a Memory Bank from the current state of the repo.

```bash
memoria update
memoria update --repo ./my-project
```

### `memoria ask`
Interactive Q&A against a Memory Bank. The system prompt adapts to the book type (code, document, audio, notebook).

```bash
memoria ask
memoria ask --project my-project
```

Type `exit` to quit the session.

### `memoria scan`
Scan a parent folder. Detects code projects and document folders. Shows a table with type, file count, and existing book status. Pick which ones to analyze.

```bash
memoria scan ./my-projects
memoria scan C:/Sahil/Projects
```

**Detection logic:**
- Code project: has `package.json`, `requirements.txt`, `.git`, `Makefile`, etc.
- Document folder: contains `.pdf`, `.docx`, `.pptx`, `.xlsx` files
- Both types are analyzable — context question adapts per type

### `memoria pull`
Pull content from configured MCP sources and generate Memory Banks. See [Section 7](#7-mcp-source-connectors).

```bash
memoria pull                         # pull all configured sources
memoria pull --source confluence     # pull one source by name
memoria pull --list-tools            # discover available tools without pulling
memoria pull --incremental           # only fetch content newer than last pull
memoria pull --reset                 # clear pull history, force full re-pull
```

Last pull time is shown inline when a source has been pulled before.

### `memoria schedule`
Manage automatic scheduled pulls. Sources run on the interval set in their `schedule:` config field.

```bash
memoria schedule list                # show all scheduled sources + last pull time + status
memoria schedule run                 # run all due sources right now (one-shot)
memoria schedule start               # blocking daemon — checks every 60s until Ctrl+C
memoria schedule start --poll 300    # check every 5 minutes instead
memoria schedule reset confluence    # clear pull history for one source
```

**Running as a background service:**
```bash
# Unix
nohup memoria schedule start --config ~/.memoria/config.yaml &

# Windows (PowerShell — runs in background)
Start-Job { memoria schedule start }
```

See [Section 7](#7-mcp-source-connectors) for the `schedule:` and `incremental:` config fields.

### `memoria search`
Semantic search across all Memory Banks. Powered by ChromaDB (local, auto-indexed).

```bash
memoria search "how does authentication work"
memoria search "what was decided about the database schema" --top 5
memoria search "which files handle payments"
```

### `memoria watch`
Watch a repo for file changes. Waits 10 seconds of quiet after a change, then auto-drafts a Memory Bank update. Human approval required before the live book is updated.

```bash
memoria watch
memoria watch --repo ./my-project
```

### `memoria serve`
Start a webhook server for GitHub PR merge triggers, OR run as an MCP server for AI agents.

```bash
memoria serve                # webhook mode, default port 8000
memoria serve --port 9000
memoria serve --mcp          # MCP server mode (stdio, for Claude Desktop/Cursor/Copilot)
```

**Webhook mode setup:**
1. Run `memoria serve`
2. Expose with `ngrok http 8000`
3. Add ngrok URL as GitHub webhook (path: `/webhook/github`)
4. Merge a PR → Memoria auto-drafts an update
5. Run `memoria review` to approve

**MCP server mode** — connect Memoria to any MCP-compatible AI agent:

```json
// Claude Desktop: ~/.config/claude/claude_desktop_config.json
{
  "mcpServers": {
    "memoria": {
      "command": "memoria",
      "args": ["serve", "--mcp"]
    }
  }
}
```

Tools exposed via MCP:
| Tool | What it does |
|------|-------------|
| `list_projects` | List all Memory Bank projects |
| `get_memory_bank` | Retrieve the full Memory Bank for a project |
| `get_skill_file` | Get or auto-generate the SKILL.md for a project |
| `search` | Semantic search across all Memory Banks |
| `ask` | Natural language Q&A over all Memory Banks |

### `memoria interview`
Guided expert interview — ask a subject-matter expert about a process, capture their answers, and synthesize into a Process Memory Bank automatically.

```bash
memoria interview
memoria interview --topic "Incident response" --project backend-api
memoria interview --topic "New hire onboarding" --duration 20
```

Memoria asks a structured set of questions about the process (trigger, steps, decision points, tools, escalation, etc.) and generates adaptive follow-up questions based on the answers. Type `done` or `skip` at any prompt.

### `memoria skills`
Convert Memory Banks into SKILL.md files — structured capability definitions that AI agents can act on.

```bash
memoria skills                          # pick project interactively
memoria skills --project my-api         # generate SKILL.md for one project
memoria skills --all                    # generate for every project
memoria skills --project my-api --format langchain   # LangChain StructuredTool
memoria skills --project my-api --format crewai      # CrewAI Agent + Tools
memoria skills --project my-api --format n8n         # n8n workflow JSON
```

Output is saved to `books/skills/`. Drop a SKILL.md into any Claude Project, Cursor context, or LLM system prompt and the agent immediately knows what it can do with that project.

### `memoria coverage`
Analyse knowledge coverage across all Memory Banks. Reports health scores, staleness, ownership gaps, and directories with no Memory Bank.

```bash
memoria coverage
memoria coverage --scan ~/projects          # check for undocumented projects
memoria coverage --output coverage.json     # save full report as JSON
```

Health score per book (0–100) based on: age, unknown/TBD markers, missing owner, confidence level. Books older than 30 days get an amber warning; 90+ days = red critical.

### `memoria review`
Review a pending draft (from `watch` or `serve`). Shows a colour diff between the current book and the draft.

```bash
memoria review
memoria review --project my-project
```

Options: approve (applies draft as new live book) or reject (discards draft, keeps current).

### `memoria split`
Split one project's Memory Bank into a shared parent book + N team child books.

```bash
memoria split
memoria split --project my-project
memoria split --project my-project --undo   # reverse the split
```

**Flow:**
1. Describe the split in plain English ("Auth and middleware to Team B, reporting to Team C")
2. Memoria proposes ownership zones based on actual code structure
3. Review and confirm (or adjust)
4. Books generated: `my-project_shared`, `my-project_team-b`, `my-project_team-c`

The `--undo` flag restores the original book. Cost of undoing: zero.

### `memoria merge`
Merge multiple Memory Banks into one unified book.

```bash
memoria merge
```

Select books, name the unified project. Source books are archived automatically.

### `memoria list`
List all Memory Banks with last-updated timestamps and file sizes.

```bash
memoria list
```

### `memoria graph`
Build and query the Knowledge Graph — a persistent map of how your projects relate to each other. See [Section 4](#4-knowledge-graph) for full details.

```bash
memoria graph build                    # analyse all books, detect relationships
memoria graph show                     # print the full graph as a table
memoria graph show --min-confidence 0.7  # only high-confidence edges
memoria graph query auth-service       # all connections for one project
memoria graph impact shared-utils      # what depends on this project?
```

---

## 4. Knowledge Graph

The Knowledge Graph builds a persistent map of cross-project relationships from your existing Memory Banks. No new repo crawling — it re-reads the books you've already generated.

### Setup & Prerequisites

**Before you build a graph you need at least 2 Memory Banks.** The graph has nothing to compare if there's only one.

```bash
# Step 1 — make sure you have Memory Banks
memoria list

# Step 2 — build the graph (one-time; re-run after adding new Memory Banks)
memoria graph build

# Step 3 — explore
memoria graph show
memoria graph query <project-name>
memoria graph impact <project-name>
```

**Requirements:**
- Memory Banks generated via `memoria analyze` (stored in `books/`)
- Your configured AI model + API key (same as `analyze` — one LLM call per project to extract metadata)
- No extra dependencies — graph is stored as `books/.graph.json`, no database needed

**Cost:** One LLM call per Memory Bank on first build. Subsequent `graph build` calls only re-process books that have changed.

**Viewing in the UI:**
```bash
memoria ui        # open the browser UI, then click the Graph tab
```
If the Graph tab shows an empty canvas, run `memoria graph build` first.

### What it detects

| Relationship | When detected | Example |
|---|---|---|
| `depends_on` | Project A lists Project B in its consumed services, or explicitly references it | `frontend` depends on `auth-service` |
| `shares_technology` | Two projects share 2+ non-trivial technologies (PostgreSQL, Kafka, Redis, etc.) | `billing` and `reporting` both use PostgreSQL + Kafka |
| `same_domain` | Two projects share business topics or domains | `payments` and `invoicing` both cover "billing" |
| `references` | One book explicitly names another project | `api-gateway` references `user-service` |

### How it works

1. **LLM extraction** — for each Memory Bank, a single LLM call extracts structured metadata:
   - Technologies used (FastAPI, PostgreSQL, Redis, …)
   - Business domains (authentication, billing, data pipeline, …)
   - What this project exposes to others (REST API, events, libraries)
   - What this project consumes from others (services, APIs, queues)
   - Explicit project references by name

2. **Rule-based edge detection** — pure Python logic compares all project nodes:
   - Service dependency: A's `consumes` list matches B's name or B's `exposes` tokens
   - Technology overlap: ≥2 shared non-trivial technologies
   - Domain overlap: shared topic labels
   - Explicit cross-references: project names in another book's text

3. **Confidence scores** — each edge carries a 0–100% confidence score based on how strongly the evidence supports the relationship.

4. **Persistence** — graph saved to `books/.graph.json` — git-trackable, no database required.

### Graph commands

```bash
# Build (or rebuild) the graph from all books
memoria graph build
memoria graph build --books-dir ./memory-banks

# Show the full graph (nodes + relationships)
memoria graph show
memoria graph show --min-confidence 0.7     # only edges ≥ 70% confidence

# Query a specific project — see everything it connects to
memoria graph query auth-service

# Impact analysis — what breaks if this project changes?
memoria graph impact shared-utils
memoria graph impact core-library
```

### Example output: `memoria graph query auth-service`

```
auth-service   Handles JWT-based authentication for all microservices

  Technologies: FastAPI, PostgreSQL, Redis
  Domains:      authentication, user management
  Exposes:      REST API /auth/*, JWT tokens
  Consumes:     Redis (session store)

Direction    Other project    Relationship      Confidence  Reason
──────────   ─────────────    ────────────────  ──────────  ─────────────────────
← inbound    frontend         depends on        95%         frontend lists auth-service as dependency
← inbound    mobile-app       depends on        95%         mobile-app lists auth-service as dependency
← inbound    api-gateway      depends on        75%         api-gateway consumes 'auth token' ↔ exposes 'JWT tokens'
↔ both       user-service     same domain       70%         Both cover: authentication, user management
```

### Example output: `memoria graph impact core-library`

```
⚠  3 project(s) may be affected by changes to core-library

Depth    Project        Dependency path
──────   ─────────────  ─────────────────────────────────────────
direct   api-gateway    core-library → api-gateway
direct   auth-service   core-library → auth-service
depth 2  frontend       core-library → api-gateway → frontend
```

### Storage format

`books/.graph.json`:
```json
{
  "version": 1,
  "updated": "2025-04-30T14:22:01",
  "nodes": {
    "auth-service": {
      "name": "auth-service",
      "book_path": "books/auth-service_memory_bank.md",
      "description": "JWT-based authentication service",
      "technologies": ["FastAPI", "PostgreSQL", "Redis"],
      "topics": ["authentication", "user management"],
      "exposes": ["REST API /auth/*", "JWT tokens"],
      "consumes": ["Redis session store"],
      "project_refs": []
    }
  },
  "edges": [
    {
      "source": "frontend",
      "target": "auth-service",
      "relation": "depends_on",
      "reason": "frontend lists auth-service as a consumed dependency",
      "confidence": 0.95
    }
  ]
}
```

---

## 5. Role-Based Access Control

Control who on your team can generate books, approve drafts, pull live data, and manage the policy itself. RBAC is **fully backward compatible** — if no policy file exists, all commands work exactly as before.

### Roles

| Role | Permissions | Typical assignees |
|---|---|---|
| `viewer` | read | Stakeholders, executives, external reviewers |
| `contributor` | read, write, pull, graph | Engineers, data scientists |
| `reviewer` | read, write, pull, graph, review | Tech leads, QA leads |
| `admin` | all | DevOps, team leads |

### Permissions

| Permission | What it gates |
|---|---|
| `read` | `memoria ask`, `search`, `list`, `graph show/query/impact` |
| `write` | `memoria analyze`, `update`, `split`, `merge` |
| `review` | `memoria review` (approve or reject a draft) |
| `pull` | `memoria pull`, `schedule run/start` |
| `graph` | `memoria graph build` |
| `admin` | `memoria access grant/revoke/init` |

### Setup

```bash
# Create ~/.memoria/policy.yaml — you become admin, everyone else is contributor
memoria access init

# Restrict unknown users to read-only
memoria access init --default-role viewer

# Grant and revoke roles
memoria access grant bob reviewer
memoria access grant intern viewer
memoria access revoke intern

# Check your own access
memoria access whoami
memoria access check review
```

### Policy file (`~/.memoria/policy.yaml`)

```yaml
rbac:
  enabled: true
  default_role: contributor       # role for users not in any list
  users:
    alice: admin
    bob: reviewer
    charlie: contributor
    intern: viewer
  teams:
    qa_team:
      members: [dave, eve]
      role: reviewer
```

**User identification priority:**
1. `MEMORIA_USER` environment variable
2. `current_user:` field in `config.yaml`
3. OS login name (`getpass.getuser()`)

### Example: `memoria access whoami`

```
alice   role: admin

  Permission  Granted  What it unlocks
  ──────────  ───────  ────────────────────────────────────────────────
  read        ✓        memoria ask, search, list, graph show/query/impact
  write       ✓        memoria analyze, update, split, merge
  review      ✓        memoria review (approve / reject drafts)
  pull        ✓        memoria pull, schedule run/start
  graph       ✓        memoria graph build
  admin       ✓        memoria access grant/revoke, init
```

### Example: denied access

```
$ memoria analyze --repo ./my-project
Permission denied
  'intern' has role 'viewer' which does not include the 'write' permission.
  Your permissions: read
  Ask an admin to grant you the 'contributor' role or higher.
```

### Disabling RBAC

Delete `~/.memoria/policy.yaml`, or set `enabled: false` in it. All commands immediately revert to open access.

---

## 6. Chat UI

A browser-based interface for every role — from a solo developer querying their own codebase to an executive reading a weekly digest. No installation required beyond `memoria ui`; runs fully in the browser at `localhost:7860`.

### Starting the UI

```bash
memoria ui                           # opens http://localhost:7860/ui automatically
memoria ui --port 8080               # custom port
memoria ui --books-dir ./books       # point to a custom books directory
memoria ui --no-open                 # start server without opening browser
```

All options:

| Flag | Default | Description |
|---|---|---|
| `--port` | `7860` | Port to run the UI server on |
| `--books-dir` | `~/.memoria/books` | Directory containing Memory Banks (reads from config.yaml if set) |
| `--config` | `config.yaml` | Path to config.yaml (model + API key selection) |
| `--no-open` | off | Don't auto-open the browser |

### Layout

| Zone | Description |
|---|---|
| Topbar (44 px) | Memoria logo + breadcrumb navigation path (`Workspace / <Tab> / <Project>`) |
| Sidebar (248 px) | App nav items, sidebar filter input, project list with glyphs, user footer |
| Main panel | Active tab content — chat messages, search results, graph, book cards |
| Input bar (pinned) | Full-width text input + send button + composer hints bar |

On screens narrower than 768 px the sidebar collapses to a hamburger menu. Between 768–1023 px the sidebar narrows to 56 px icon-only mode.

#### New UI Components

**Project_Glyphs** — Each project shows a 2-letter monogram (first letter of each word, or first 2 characters for single-word names) in a rounded square with a deterministic colour from an 8-colour palette. Glyphs appear in the sidebar list, Book Cards, and as graph node labels.

**Sidebar_Filter** — A text input above the project list that live-filters projects by name (case-insensitive substring match). The All Books entry is always visible regardless of filter text.

**All_Books_Entry** — A permanent first entry in the project list that activates global cross-book chat mode (searches across all Memory Banks simultaneously).

**Breadcrumb_Topbar** — Shows the current navigation path as `Workspace / <Tab> / <Project>`. Updates automatically when you switch tabs or projects.

**Composer_Hints** — An always-visible single-line hint below the chat input showing the keyboard shortcuts: Enter to send, Shift+Enter for a newline, and `/` for commands.

**Sidebar_Footer** — Pinned at the bottom of the sidebar, showing a circular avatar with your username initial and full username.

**Toast Notifications** — Polished light-theme toasts with colour-coded borders (green = success, amber = warning, red = error, indigo = info). Slide in from the bottom-right, auto-dismiss after 4 s, and support click-to-dismiss.

**Model Parameter Controls** — Gear icon (⚙) in the top-right opens a popover to override model settings per-session:

| Control | Range | Default | Effect |
|---|---|---|---|
| Model | selector | config.yaml value | Override model without editing config |
| Temperature | 0 – 2 | 0.7 | Higher = more creative; lower = more factual |
| Max tokens | integer | 4096 | Cap response length |
| Top-p | 0 – 1 | 1.0 | Nucleus sampling; lower = more focused |

Settings persist to `localStorage` and are injected into every `/api/ask` and `/api/global-ask` call. Reset button restores defaults. Backend applies these overrides *ahead* of `config.yaml` values.

**Stop Button** — While a response is streaming, the send button becomes a red ⬛ stop button. Clicking it aborts the stream immediately via `AbortController` — no partial response is shown, no error toast fires.

### Tabs

| Tab | Who uses it | What it does |
|---|---|---|
| **Chat** | Everyone | Conversational Q&A — single project or global cross-project mode. Multi-turn context carried across messages. Streams token-by-token via SSE. |
| **Search** | Developers, leads | Semantic + full-text search across all Memory Banks with relevance scores and highlighted excerpts. |
| **Graph** | Architects, leads, managers | Interactive force-directed visualisation. Nodes sized by connectivity. Click to navigate connections, ← Back to retrace path. Labels hide when zoomed out. |
| **Books** | Everyone | Browse all Memory Banks: glyph, type badge (code / docs / audio / notebook / sheet / mcp), description, Ask → and Refresh buttons. |
| **Today** | All roles | Morning intelligence brief — what changed, what needs attention, pending approvals, graph alerts. |
| **Exec** | Managers, VPs, execs | Executive digest — weekly/monthly AI-synthesized summary across all projects; choose time window. |
| **Plan** | PMs, leads | Paste a plan → AI identifies risk, dependencies, prior art from your real Memory Banks. |
| **Review** | Leads, reviewers | Pending update drafts with colour diff — approve or reject in one click. |
| **Sources** | Admins | Configure MCP connectors (Jira, Confluence, Slack, Linear…), sync schedules, last-pull timestamps. |
| **Settings** | Admins | Model selector, API keys, token budget config, test-connection button. |

**Chat tab — detail:**

- **Global mode** (no project selected): answers from all Memory Banks simultaneously; source attribution chips above the answer; click a chip to jump to that project's chat
- **Project mode** (project selected): deep Q&A with the full Memory Bank as context; multi-turn conversation history (last 10 messages injected every turn)
- **Streaming + stop**: responses stream token-by-token; the send button becomes a red ⬛ stop button mid-stream — click to cancel immediately
- **Markdown rendering**: bold, italic, code blocks, headers, lists, and tables all rendered inline (no library); AI-returned tables display as HTML `<table>` elements
- **Fuzzy project resolution**: slash commands like `/impact ai trading agent` resolve to `AI-Trading-Agent` automatically — 4-level fuzzy match (exact → contains → all tokens → 60%+ token overlap)

**Graph tab — detail:**

- **Adaptive force layout**: repulsion and spring rest length scale with node count so large graphs spread automatically without bunching
- **Dynamic node sizing**: hub nodes (more connections) render larger (22–40 px radius) — at a glance you can see which projects are central
- **Label declutter**: node name labels hide when zoomed below 0.65× — initials remain; labels reappear when you zoom back in
- **Navigate-to connections**: click a connection in the right panel to drill into that project; ← Back button retraces your path
- **Isolate mode**: click any node → non-connected nodes fade; connected edges highlight; edge labels appear only on selection

### Slash Commands

Type `/` in the input bar to open the command menu. Navigate with arrow keys; select with Enter, Tab, or click; dismiss with Escape.

| Command | What it does |
|---|---|
| `/ask <question>` | Ask a question about the selected project (default — no slash required) |
| `/search <query>` | Semantic search across all Memory Banks |
| `/summarize` | Ask for a brief plain-English summary of the selected project |
| `/impact [project]` | Show which projects depend on this one — uses the Knowledge Graph API, returns a dependency table with depth and path |
| `/graph` | Switch to the Graph tab |
| `/books` | Switch to the Books tab |

> **Default behaviour:** any input that doesn't start with `/` is treated as `/ask` automatically. Users never need to type a slash prefix.

### API Endpoints

The UI is served by a FastAPI application. All endpoints are available for programmatic use:

| Method | Path | Description |
|---|---|---|
| `GET` | `/ui` | Serves the single-page HTML app |
| `GET` | `/api/projects` | List all Memory Banks (name, path, size, last-updated) |
| `POST` | `/api/ask` | SSE Q&A — body: `{"project": "...", "question": "...", "history": [...]}` |
| `POST` | `/api/global-ask` | SSE cross-book Q&A — body: `{"question": "...", "top_k": 8, "history": [...], "model": "...", "temperature": 0.7, "max_tokens": 4096}` |
| `GET` | `/api/search?q=&top=5` | Semantic search across all books |
| `GET` | `/api/fulltext?q=` | Full-text keyword search across all books |
| `GET` | `/api/graph` | Knowledge Graph JSON (nodes + edges) |
| `GET` | `/api/graph/query/{project}` | Node details + relationships for one project |
| `GET` | `/api/graph/impact/{project}` | Transitive dependents of a project |
| `GET` | `/api/graph/build-stream` | SSE — stream graph build progress |
| `GET` | `/api/scan-path?path=...` | Detect projects at a local path (single / multi / file) |
| `GET` | `/api/analyze-stream?repo=...&context=...` | SSE — stream analyze progress for a path |
| `GET` | `/api/books/{project}` | Raw Markdown content of a Memory Bank |
| `GET` | `/api/fs/browse?path=...` | Server-side filesystem browser — returns folder contents for the Analyze panel inline browser |
| `GET` | `/api/agent-context/{project}` | Compact (<3000 char) agent-optimised Memory Bank summary for injection into LLM system prompts |

**Model override fields** (accepted by both `/api/ask` and `/api/global-ask`):
```json
{
  "question": "...",
  "model": "gemini/gemini-2.0-flash",   // optional — overrides config.yaml
  "temperature": 0.7,                    // optional
  "max_tokens": 4096,                    // optional
  "top_p": 1.0,                          // optional
  "history": [                           // optional — last N turns for multi-turn context
    {"role": "user",      "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

SSE event shapes from `/api/ask`:

```
data: {"type": "chunk",   "text": "..."}   — token chunk (one or more per response)
data: {"type": "done"}                      — stream complete
data: {"type": "error",   "message": "..."}— error from the model or server
```

### Global Ask — Cross-Book Intelligence

When no project is selected, the Chat tab operates in **Global Ask** mode. Questions are answered by searching across all Memory Banks automatically — no project selection required.

**How it works:**
1. The question is run through ChromaDB semantic search across all indexed Memory Banks (auto-indexed on first use)
2. Top results are grouped by project — up to 5 projects, 3 relevant chunks each
3. A multi-book context is assembled with source labels
4. The AI answers with inline citations: *"According to **[auth-service]**, ..."*
5. Source attribution chips appear above the answer showing which projects contributed, colour-coded by relevance score

**Source chips:**
- 🟢 Green dot — ≥ 80% relevance
- 🟡 Amber dot — ≥ 60% relevance
- Grey dot — lower relevance
- Click any chip to jump to that project's dedicated chat

**API endpoint:**

```
POST /api/global-ask
Body: {"question": "...", "top_k": 8}
```

SSE event shapes (superset of `/api/ask`):

```
data: {"type": "sources", "sources": [{"project": "...", "section": "...", "score": 0.92}, ...]}
data: {"type": "chunk",   "text": "..."}
data: {"type": "done"}
data: {"type": "error",   "message": "..."}
```

The `sources` event is always emitted first so the UI can render attribution chips before the text starts streaming.

**Fallback:** if ChromaDB hasn't been indexed yet, `global-ask` automatically runs `index_all_books()` before searching. No manual setup required.

### Analyze from UI

Non-technical users can analyze projects directly from the browser — no terminal required.

**How to use:**
1. Click the **+** button next to "Projects" in the sidebar
2. Enter a local path (folder or file) and press **Check**
3. Memoria detects what's there:
   - **Single project** — shows name + whether it's already been analyzed; add optional context and click **Start Analysis**
   - **Multiple projects** — shows a grouped checkbox picker with ✓ already-analyzed and new labels; monorepo projects are badged; select and click **Analyze Selected**
   - **File** — audio, PDF, image, notebook — analyzed directly
4. Progress streams live in a log window; a progress bar shows completion
5. When done: **Open in Chat →** button jumps straight to a conversation; **Analyze another** resets the panel

Multi-project queue: if you selected several projects, they are analyzed back-to-back automatically with a queue counter showing remaining items.

### Design Notes

- **No external dependencies** — the entire frontend is a single HTML file with inline CSS and JS. Works fully offline after the server is running.
- **Light theme** — warm off-white backgrounds (`--bg-main: #f0efeb`), indigo accent (`--accent: #4f46e5`), dark text (`--text-primary: #0e0f0c`). Geist typeface (Google Fonts, system-ui fallback) for body; JetBrains Mono for code. WCAG AA contrast compliant.
- **Inline Markdown rendering** — AI answers are rendered with code blocks, bold, italics, headers, lists, and tables without any Markdown library.
- **Force-directed graph** — pure vanilla JS simulation, no D3 or Canvas library needed; 2-letter glyph labels inside nodes; edge labels appear only on highlight.
- **Responsive sidebar** — narrows to 56 px icon-only mode between 768–1023 px; collapses to off-canvas hamburger below 768 px. All nav items show tooltips in narrow mode.
- **Global mode welcome** — when no project is selected, the Chat tab shows a hero headline, live Memory Bank count, and 4 example questions (clickable to prefill the input).
- **`/impact` is graph-backed** — returns a dependency table from the Knowledge Graph API, not a generic AI response. Requires the graph to be built first (`memoria graph build`).

### Rate Limit Behaviour

When the AI model returns rate-limit errors during `analyze` (both CLI and UI), Memoria:

1. Retries once after a short wait (15 s first retry, 30 s second)
2. Tracks consecutive failures — after **3 rate-limit errors in a row**, aborts the job immediately with a clear message:
   > *"Rate limit hit 3 times in a row. Your API quota may be exhausted. Wait a few minutes, check your quota, or switch model."*
3. In the CLI, prints a `→` guidance line pointing to config.yaml or the quota dashboard
4. In the UI, the error appears in the progress log and the spinner stops

This replaces the old behaviour of waiting 60 s + 120 s + 180 s per chunk (up to 6 minutes before failing).

---

## 7. MCP Source Connectors

Connect directly to live data sources instead of exporting files manually.

### How it works
1. Memoria spawns the MCP server as a subprocess
2. Calls the tools listed under `pull:` in your config
3. Collects the text responses
4. Generates a Memory Bank from the pulled content

### Setup
```bash
pip install memoria[mcp]
```

### Config
Add to `config.yaml` (or `~/.memoria/config.yaml`):

```yaml
mcp_sources:
  - name: "confluence"
    server: "npx @atlassian/mcp-confluence"
    schedule: "daily"          # run automatically every 24 hours
    incremental: true          # only fetch pages updated since last pull
    context: "Engineering wiki and architecture docs"
    env:
      CONFLUENCE_URL: "${CONFLUENCE_URL}"
      CONFLUENCE_TOKEN: "${CONFLUENCE_TOKEN}"
    pull:
      - tool: "confluence_search"
        args: {query: "engineering architecture", limit: 50}

  - name: "notion"
    server: "npx @notionhq/mcp"
    schedule: "weekly"         # refresh once a week
    env:
      NOTION_API_KEY: "${NOTION_API_KEY}"
    pull:
      - tool: "notion_query_database"
        args: {database_id: "your-db-id"}

  - name: "slack"
    server: "npx @slack/mcp"
    schedule: "6h"             # every 6 hours
    incremental: true          # only messages since last pull
    env:
      SLACK_BOT_TOKEN: "${SLACK_BOT_TOKEN}"
    pull:
      - tool: "slack_get_messages"
        args: {channel: "eng-decisions", limit: 100}

  - name: "teams"
    server: "npx @microsoft/mcp-teams"
    env:
      TEAMS_TOKEN: "${TEAMS_TOKEN}"
    pull:
      - tool: "teams_get_messages"
        args: {channel: "dev-general", days: 7}
```

### Auth
Tokens go in `~/.memoria/.env` (created by `memoria init`). The `${VAR}` syntax in config.yaml reads from there — never hardcode tokens in config.

```bash
# ~/.memoria/.env
CONFLUENCE_TOKEN=your-token-here
SLACK_BOT_TOKEN=xoxb-...
NOTION_API_KEY=secret_...
```

### Scheduled Pulls

Add a `schedule:` field to any source to make it run automatically.

| Value | Runs every |
|---|---|
| `hourly` | 60 minutes |
| `daily` | 24 hours |
| `weekly` | 7 days |
| `30m` | 30 minutes |
| `2h` / `6h` / `12h` | 2, 6, or 12 hours |
| `1d` / `7d` | 1 or 7 days |

Start the daemon to keep all scheduled sources up to date:
```bash
memoria schedule start
```

Check what's due without pulling:
```bash
memoria schedule list
```

### Incremental Pulls

Add `incremental: true` to a source to only fetch content updated since the last pull.

**How it works:** Memoria stores the last-pull timestamp in `~/.memoria/pull_state.json`. On the next pull, it inspects the tool's JSON Schema. If the tool exposes a `since`, `after`, `updated_after`, or similar parameter, Memoria injects the timestamp automatically — no config changes needed.

If the tool doesn't support temporal filtering, Memoria falls back to a full pull silently.

**Force a full re-pull:**
```bash
memoria pull --reset                      # resets all sources
memoria schedule reset confluence         # resets one source
```

### Discovering tools
Not sure what tools a server exposes? Run:
```bash
memoria pull --source confluence --list-tools
```
Prints all available tool names without pulling any content.

### Any MCP server
The `server:` field accepts any MCP server command. If a tool publishes an MCP server, Memoria can pull from it — no per-connector code needed.

---

## 8. Auto-Update Hooks

Configure when Memory Banks should be automatically refreshed. All auto-updates go through `memoria review` — a human approves before the live book changes.

```yaml
# config.yaml
hooks:
  on_pr_merge:
    enabled: true
    only_if_files_changed:   # optional: only trigger if these paths changed
      - "src/"
      - "lib/"

  on_schedule:
    enabled: true
    cron: "0 9 * * 1"        # every Monday at 9am

  on_manual:
    enabled: true             # memoria update
```

**Trigger flow:**
- `on_pr_merge` → requires `memoria serve` + GitHub webhook
- `on_schedule` → requires `memoria serve` running as a background process
- `on_manual` → `memoria update` or `memoria watch`

---

## 9. Team Workflows — Split & Merge

### Splitting a team

Use when one team divides into sub-teams with separate ownership areas.

```bash
memoria split --project codeprism
```

**What gets generated:**
- `codeprism_shared_memory_bank.md` — foundation all teams inherit (architecture, shared concepts)
- `codeprism_team-b_memory_bank.md` — Team B's ownership zone
- `codeprism_team-c_memory_bank.md` — Team C's ownership zone

The shared parent is updated once. Every team reads from it. No duplication.

**Reversing a split:**
```bash
memoria split --project codeprism --undo
```
The original book is restored. Child books are archived.

### Merging teams

Use when teams consolidate or projects combine.

```bash
memoria merge
```

Memoria synthesises a unified book — it finds themes across books, surfaces conflicts explicitly, and doesn't just concatenate. Source books are archived.

---

## 10. Config Reference

Default location: `config.yaml` in the project directory, or `~/.memoria/config.yaml` for global config.

**Discovery order:**
1. `--config` flag value
2. Walk up from CWD (like git finds `.git`)
3. `~/.memoria/config.yaml`

```yaml
# AI model (required)
model: "gemini/gemini-2.0-flash"

# Token limits
max_tokens_per_file: 2000      # chars per file before truncation
max_tokens_output: 4000        # max tokens in model response

# Output
books_dir: "~/.memoria/books"  # where Memory Banks are saved (absolute path required)

# Archive
archive_ttl_days: 30           # how long to keep archived versions

# Directories to skip when crawling
ignore_dirs:
  - .git
  - node_modules
  - __pycache__
  - venv
  - dist
  - build

# File extensions to skip
ignore_extensions:
  - .pyc
  - .lock
  - .log
  - .exe
  - .dll
  - .zip

# Max individual file size (bytes) before skipping
max_file_size: 50000

# Auto-update hooks
hooks:
  on_pr_merge:
    enabled: false
    only_if_files_changed: []
  on_schedule:
    enabled: false
    cron: "0 9 * * 1"
  on_manual:
    enabled: true

# MCP source connectors (optional)
# Each source can have: name, server, pull, env, schedule, incremental, context
mcp_sources:
  - name: "confluence"
    server: "npx @atlassian/mcp-confluence"
    schedule: "daily"       # interval: hourly|daily|weekly|30m|2h|6h|12h|1d
    incremental: true       # inject last-pull timestamp into tool calls
    context: "Engineering wiki"   # used as company_context when generating book
    env:
      CONFLUENCE_TOKEN: "${CONFLUENCE_TOKEN}"
    pull:
      - tool: "confluence_search"
        args: {query: "architecture", limit: 50}
```

---

## 11. Optional Dependencies

| Extra | Command | Enables |
|---|---|---|
| Audio/video | `pip install memoria[audio]` + ffmpeg | `.mp3`, `.mp4`, `.m4a`, `.wav`, `.mov` transcription via Whisper |
| MCP connectors | `pip install memoria[mcp]` | `memoria pull`, any MCP server source |
| All | `pip install memoria[all]` | Everything above + BeautifulSoup for richer HTML extraction |

**Whisper model size** (set via env var):
```bash
WHISPER_MODEL=base    # default — fast, good for speech
WHISPER_MODEL=small   # better accuracy, slower
WHISPER_MODEL=medium  # high accuracy, requires more RAM
```

**Language** (set via env var):
```bash
WHISPER_LANGUAGE=en     # default — skip detection, faster
WHISPER_LANGUAGE=auto   # let Whisper detect language
```

Whisper models are cached at `~/.cache/whisper/` — downloaded once, reused on every run.

---

## 12. AI Provider Setup

All providers are configured via one `model:` line in config.yaml. API keys go in `.env`.

| Provider | Model string | Env var |
|---|---|---|
| Google Gemini | `gemini/gemini-2.0-flash` | `GEMINI_API_KEY` |
| Anthropic Claude | `claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |
| OpenAI | `gpt-4o` | `OPENAI_API_KEY` |
| AWS Bedrock | `bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0` | AWS credentials |
| Ollama (local) | `ollama/llama3` | none — no API key needed |

**Vision support** (required for image analysis):
- Gemini: all models support vision
- Claude: `claude-sonnet-4-5` and above
- OpenAI: `gpt-4o`, `gpt-4-turbo`
- Ollama: model-dependent (e.g. `ollama/llava`)

Run `memoria init` to set this up interactively.

---
