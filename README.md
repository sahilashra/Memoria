<p align="center">
  <img src="docs/screenshots/hero.png" alt="Memoria — Adaptive AI Knowledge Infrastructure" width="820"/>
</p>

<h1 align="center">Memoria</h1>

<p align="center">
  <strong>Adaptive AI Knowledge Infrastructure for Engineering Teams</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-blue?style=flat-square&logo=python"/>
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square"/>
  <img src="https://img.shields.io/badge/AI-model--agnostic-purple?style=flat-square"/>
  <img src="https://img.shields.io/badge/lines-21%2C000+-orange?style=flat-square"/>
  <img src="https://img.shields.io/badge/tests-450+-brightgreen?style=flat-square"/>
</p>

<p align="center">
  <em>Jarvis for every engineer, on every laptop, across every role.</em>
</p>

---

## The Problem

Engineering teams lose **20%+ of their time** to knowledge friction:

- A new engineer takes **6-8 weeks** to become productive because context is scattered across Slack, Jira, old PRs, and tribal knowledge
- Developers spend **2+ hours/day** hunting for context before writing a single line of code
- AI coding tools **hallucinate confidently** because they lack structured, company-specific domain knowledge
- Critical knowledge **walks out the door** when engineers leave

---

## What Memoria Does

Memoria pulls structured knowledge out of your codebases, documents, recordings, and tools — then uses it to assemble context for any task, detect what's missing, and help you ship.

```
  Your Sources                    Memory Banks                 Intelligence Layer
  ────────────                    ────────────                 ──────────────────
  Code repos                     Structured per-module        Knowledge Graph
  PDFs, DOCX, PPTX, XLSX    ->   AI-optimized summaries   ->  Blast-radius analysis
  Audio recordings                Auto-updated on change       Semantic search
  Video, Notebooks                Plain Markdown files         Morning briefs
  Jira, GitHub, Confluence                                     Adaptive Q&A
```

### Core Capabilities

| Capability | What it does |
|---|---|
| **Memory Banks** | Hierarchical, AI-generated summaries of how your systems work — one per module, not one giant dump. Auto-updated when code changes. |
| **Knowledge Graph** | Maps how every project and module relates to every other. "What breaks if I change auth?" answered in seconds. |
| **Hybrid RAG Search** | ChromaDB vectors + file graph traversal + Reciprocal Rank Fusion. Not just keyword matching — semantic understanding. |
| **Adaptive Agent** | Assembles relevant Memory Banks for any ticket, detects capability gaps, generates artifacts for review. |
| **10 Format Extractors** | PDF, DOCX, PPTX, XLSX, audio (Whisper), video (OpenCV), Jupyter notebooks, images, HTML, code. |
| **MCP Interop** | Exposes as an MCP server for Claude Desktop, Cursor, Copilot. Also pulls FROM Confluence, Notion, Slack, Jira via MCP. |
| **Morning Brief** | Proactive daily intelligence — what changed, what needs attention, what's at risk. |
| **Role-Adaptive Views** | Same knowledge, different lens: developer sees code context, manager sees risk, executive sees digest. |

---

## Run in 60 Seconds

```bash
git clone https://github.com/sahilashra/Memoria
cd Memoria
pip install -e .

cp .env.example .env            # add your AI provider API key
memoria init                    # pick provider + model (supports free Gemini, local Ollama, or any cloud)
memoria analyze --repo ./your-project
memoria ui                      # open http://localhost:7860/ui
```

That's it. No Docker, no database setup, no cloud account. Memory Banks are plain Markdown files in your repo.

---

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │              Memoria Engine                   │
                    │                                              │
  Sources ──────>   │  Crawler ─> Detector ─> Chunker ─> Generator │
  (code, docs,      │      │                                │      │
   audio, video,    │      v                                v      │
   tools)           │  Extractors (10)              Memory Banks    │
                    │  PDF,DOCX,PPTX,              (Markdown)      │
                    │  XLSX,Audio,Video,                │           │
                    │  Notebook,Image,                  v           │
                    │  HTML,Code                   Vector Index     │
                    │                              (ChromaDB)       │
                    │                                  │           │
                    │              Knowledge Graph <────┘           │
                    │              (relationships,                  │
                    │               blast radius)                   │
                    └──────────────┬───────────────────────────────┘
                                   │
                    ┌──────────────v───────────────────────────────┐
                    │           Interface Layer                     │
                    │                                              │
                    │  CLI (15+ commands)    Web UI (SPA)          │
                    │  MCP Server            REST + SSE API        │
                    │  Webhooks              Scheduled Pulls       │
                    └──────────────────────────────────────────────┘
```

**Model-agnostic** via LiteLLM — works with Claude, GPT-4, Gemini, AWS Bedrock, Azure, or fully local via Ollama. No vendor lock-in.

**Offline-first** — Memory Banks are plain Markdown. The vector index rebuilds from them. Works without internet after initial analysis.

---

## By the Numbers

| Metric | Value |
|---|---|
| Python source | **21,000+ lines** across 56 modules |
| Frontend | **617 KB** single-file SPA, zero npm dependencies |
| Test coverage | **55 test files**, 450+ tests across 5 tiers |
| Format extractors | **10** (PDF, DOCX, PPTX, XLSX, audio, video, notebook, image, HTML, code) |
| Vector backends | **3** (ChromaDB local, Pinecone cloud, Weaviate cloud) |
| LLM providers | **6+** (Claude, GPT-4, Gemini, Bedrock, Azure, Ollama) |
| CLI commands | **15+** (analyze, ask, search, graph, interview, skills, coverage, ...) |
| UI tabs | **10** (Chat, Search, Graph, Books, Today, Exec, Plan, Review, Sources, Settings) |

---

## Who It's For

| Role | What Memoria gives them |
|---|---|
| **Developer** | Code-level Q&A, dependency graph, "what breaks if I change X?", git context per project |
| **QA / DevOps** | Impact alerts when upstream services change, test coverage visibility |
| **Team Lead** | Cross-project status, risk surfacing, sprint intelligence |
| **New Hire** | Ask any question about any system, get cited answers — onboard in hours, not weeks |
| **Manager / VP** | Portfolio health, morning brief, executive digest |
| **Consultant** | Token-scoped read-only access to exactly the projects relevant to their engagement |

---

## Tech Stack

- **Runtime:** Python 3.9+, FastAPI + Uvicorn
- **AI:** LiteLLM (multi-provider), content-adaptive prompting with 6 specialized templates
- **Search:** ChromaDB (local default), Pinecone, Weaviate — hybrid RAG with RRF fusion
- **Extractors:** pdfplumber, python-docx, python-pptx, openpyxl, openai-whisper, opencv-python
- **Interop:** MCP server (Claude Desktop, Cursor) + MCP client (Confluence, Notion, Slack, Jira)
- **Frontend:** Vanilla JS single-file SPA — zero build step, zero npm
- **CLI:** Click + Rich for terminal UI
- **Storage:** Plain Markdown files + JSON graph — no database required

---

## Documentation

| Document | What's in it |
|---|---|
| [ABOUT.md](ABOUT.md) | Full product vision — personal, team, and enterprise scales |
| [CAPABILITIES.md](CAPABILITIES.md) | Every feature in depth — analyzers, Memory Bank structure, CLI, MCP, RBAC, config |
| [docs/architecture/](docs/architecture/) | System architecture, module map, data flow, build order |
| [docs/roles/](docs/roles/) | How each role (developer, QA, manager, designer) uses Memoria |
| [docs/vision/](docs/vision/) | Product vision and company brain concept |

---

## License

MIT — free for personal and commercial use.
