# Intelligence Engine — Master Index

> This directory tracks the design and implementation of Memoria's 5-pillar
> autonomous workflow intelligence system.
>
> **Status:** Architecture decisions resolved — all 5 pillars have detailed
> implementation specs. See each pillar doc for full decisions and code.

---

## What This Is

The Intelligence Engine extends Memoria from a *knowledge documentation tool*
into an *ambient AI that watches how you work, learns your workflows, and
proactively helps automate them*.

The system observes interactions at the OS level, segments them into meaningful
tasks, models patterns with AI, and generates actionable automations — all
locally, with explicit user consent at every step.

---

## The Five Pillars

| # | Pillar | File | Coverage Today | Status |
|---|--------|------|---------------|--------|
| 1 | Telemetry & Data Capture | [PILLAR_1_TELEMETRY.md](./PILLAR_1_TELEMETRY.md) | 40% | 🔶 Partial |
| 2 | Activity Segmentation & Cleaning | [PILLAR_2_SEGMENTATION.md](./PILLAR_2_SEGMENTATION.md) | 25% | 🔴 Mostly missing |
| 3 | Workflow Understanding & AI Modeling | [PILLAR_3_AI_MODELING.md](./PILLAR_3_AI_MODELING.md) | 30% | 🔶 Partial |
| 4 | Proactive Automation & Copilot | [PILLAR_4_AUTOMATION.md](./PILLAR_4_AUTOMATION.md) | 20% | 🔴 Mostly missing |
| 5 | Privacy, Security & Edge Deployment | [PILLAR_5_PRIVACY.md](./PILLAR_5_PRIVACY.md) | 60% | 🔶 Partial |

---

## Architecture Decisions (Resolved)

| # | Question | Decision | Pillar Docs |
|---|----------|---------|------------|
| D1 | Memoria feature or separate product? | **New modules inside Memoria** — `memoria intelligence start`, new FastAPI routes, companion extension | All |
| D2 | Privacy model | **Local forever** — `sync_allowed: false` in v1, no cloud path exists. No data leaves `~/.memoria/` | P2, P5 |
| D3 | Target platforms | **Windows + macOS from day one** (Linux later) — AppleScript + UIA for URL, DPAPI + Keychain for encryption | P1, P5 |
| D4 | Automation output format | **Memoria Workflow YAML + Python subprocess orchestrator** — `{{ memory_bank.* }}` template variables, not Playwright | P4 |
| D5 | VLM for screenshot parsing | **Accessibility API primary, `llava:7b-v1.6-Q4_K_M` via Ollama as fallback** — screenshots only for poor-a11y apps | P3, P5 |

---

## How to Use This Directory

1. Read the pillar doc for the component you're implementing
2. Follow the `## Proposed Implementation` section — code templates are ready to adapt
3. Check `## What Is Missing` gap tables for exact effort estimates
4. Update coverage % and status in this README as gaps are closed
5. Add completed items to `ROADMAP.md` ✅

---

## Related Files

| File | Purpose |
|------|---------|
| `ROADMAP.md` | High-level roadmap — Intelligence Engine section at bottom |
| `memoria/companion.py` | Pillar 1 partial: active window + clipboard detection |
| `memoria/tracker.py` | Pillar 1 partial: file change + git commit events, SQLite activity log |
| `memoria/learner.py` | Pillar 3 partial: LLM labeling of git commits, confidence scoring |
| `memoria/observer.py` | Pillar 4 partial: auto-draft on file change |
| `memoria/search.py` | Pillar 4 partial: ChromaDB semantic search used by companion |
