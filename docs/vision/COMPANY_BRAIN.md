# Memoria — Company Brain

> The missing layer between raw company data and reliable AI automation.
> Not a company-wide search. Not a chatbot over documents.
> A living map of how a company actually works.

---

## The Problem

Every company has critical know-how scattered everywhere:
- Some of it lives in people's heads
- Some is buried in old Slack threads
- Some is in support tickets no one reads
- Some is in the email account of someone who left 18 months ago

The company works because humans vaguely remember where that knowledge is and how to apply it.

**AI agents can't operate like that.** If we want companies to run on AI automation, we need a new primitive: a company brain. A system that pulls knowledge out of all these fragmented sources, structures it, keeps it current, and makes it executable.

---

## What the Company Brain Is

```
Raw company data          Company Brain              Output
─────────────────────     ──────────────────────     ──────────────────────
Slack threads         →   Knowledge Graph        →   Structured Memory Banks
Jira tickets          →   (entities + edges)     →   Contradiction flags
Meeting recordings    →   Time-weighted          →   Role-specific briefs
Email threads         →   freshness scores       →   Onboarding packages
GitHub PRs            →   Contradiction          →   Cross-team dep maps
Runbooks              →   detection              →   Skills files (future)
Notion/Confluence     →   Pattern detection      →   
Support tickets       →
```

This is not a search engine. Search requires you to know what to look for. The Company Brain surfaces what you need before you know you need it.

---

## Core Capabilities

### 1. Deep Ingestion — Pull Knowledge From Everywhere

**Connectors (ranked by value):**

| Connector | What it captures | Priority |
|-----------|-----------------|----------|
| GitHub/GitLab | PRs, commits, reviews, issues, CI results | **Must-have** |
| Jira / Linear | Tickets, sprints, decisions, blockers | **Must-have** |
| Slack | Decisions, tribal knowledge, real-time context | **Must-have** |
| Confluence / Notion | Docs, ADRs, runbooks, specs | **Must-have** |
| Meeting recordings | Verbal decisions, action items, context | High |
| Zendesk / Intercom | Customer pain points, recurring issues | High |
| Figma | Design decisions, component specs | Medium |
| Email | Executive decisions, vendor commitments | Medium |
| Analytics (Mixpanel, Amplitude) | Outcome data for decisions | Medium |

**Key principle:** Pull metadata, not just text. Who said what, to whom, how fast a ticket was resolved, who approved the override — the relationships are as important as the content.

### 2. Knowledge Graph (GraphRAG)

Store information not as floating embeddings, but inside a structured knowledge graph:

```
Entities (nodes):                  Relationships (edges):
─────────────────                  ──────────────────────
People                         →   "made decision"
Projects/Services              →   "depends on"
Decisions/ADRs                 →   "contradicts"
Policies/Rules                 →   "authored by"
Incidents                      →   "caused by"
Features                       →   "blocks"
Components/Files               →   "references"
```

**Why this matters:** Vector search finds similar text. Graph search finds *connected meaning*. "What would break if we replaced the auth service?" requires graph traversal, not cosine similarity.

### 3. Contradiction Detection

When two sources disagree, surface it immediately:

```python
# Examples of contradictions Memoria detects:
contradictions = [
    {
        "type": "policy_conflict",
        "source_a": "Handbook (2022): Refund limit $20",
        "source_b": "Slack #support (yesterday): 'We do $50 for Enterprise'",
        "flag": "Which is the current operational rule?",
        "route_to": "Head of Support"
    },
    {
        "type": "timeline_conflict",
        "source_a": "Q3 Roadmap: Feature X ships August 15",
        "source_b": "Engineering Slack thread: 'blocked until September'",
        "flag": "Roadmap and engineering timeline are out of sync",
        "route_to": "PM + Tech Lead"
    },
    {
        "type": "spec_vs_implementation",
        "source_a": "Product spec: 'email required at signup'",
        "source_b": "Figma mockup: 'optional, add later flow'",
        "flag": "Design and spec contradict each other",
        "route_to": "Designer + PM"
    }
]
```

### 4. Knowledge Decay Weighting

Knowledge goes stale. A Slack message from yesterday is infinitely more relevant than a Notion doc from 2022:

```
Weight = 0.5 ^ (age_days / half_life_days)

Half-life by source type:
  Slack message:           30 days  (volatile, opinionated)
  GitHub PR description:   90 days  (stable once merged)
  ADR (Architecture Doc):  180 days (designed to be stable)
  Notion/Confluence doc:   90 days  (often forgotten)
  Meeting recording:       30 days  (decisions evolve)
```

Memory Banks show freshness warnings: `⚠️ This section references a 2022 architecture decision — 3 more recent discussions exist.`

### 5. Role-Specific Intelligence

The same Company Brain surfaces different information to different roles:

| Role | Primary value from Company Brain |
|------|----------------------------------|
| Developer | Codebase context, tribal knowledge, onboarding packages |
| Designer | Design decisions, constraint history, component specs |
| Product Manager | Decision history, contradiction flags, sprint health |
| QA Engineer | Test traceability, flaky test patterns, coverage gaps |
| DevOps/SRE | Incident patterns, runbook freshness, runbook auto-update |
| Tech Lead | ADR drift detection, PR historical context, onboarding briefs |
| Engineering Manager | Team health, knowledge concentration, bus-factor risk |
| Architect | Cross-service dependency maps, drift detection, change impact |

---

## GTM Wedge: Start With DevOps / Incident Response

Three departments where tribal knowledge causes the most expensive failures:

1. **DevOps / Incident Response** ← Start here
   - 3am incidents where the fix lives only in someone's head
   - Runbooks that don't capture what engineers actually do
   - Incident postmortems that don't prevent the same incident next month
   - Memoria's auto-runbook-update flow is the highest-ROI first feature

2. **Engineering / SDLC**
   - Onboarding a new engineer takes 3 months; with Memoria it takes 3 days
   - ADR drift — architecture intent vs. reality
   - The "ghost author" problem — key engineers leaving and taking context with them

3. **Customer Support Escalations** (future)
   - Tier-3 engineers resolving edge cases no runbook covers
   - Policy contradictions between handbook and practice
   - Mapping "how refunds actually get approved" vs. "the written policy"

---

## Phase 1: SDLC Focus (Current Priority)

Build the Company Brain for engineering and product teams first. This is:
- The audience Memoria already serves
- The domain with the richest machine-readable signal (git, CI, Jira, Slack)
- The wedge that proves the model before expanding to other departments

The feature set for this phase is documented in `docs/roles/`.

## Phase 2: Expand to Other Departments (Future)

Once the SDLC Company Brain is proven, the same architecture extends to:
- Sales / Deal Desk (how pricing exceptions actually get approved)
- Finance (how budgets are managed in practice vs. policy)
- Consulting (firm-wide methodology and client-specific tribal knowledge)
- HR / People Ops (onboarding, policy, culture)

---

## Privacy Model

The Company Brain is built on the same local-first principles as personal Memoria:

| Principle | Implementation |
|-----------|---------------|
| Data sovereignty | All data in customer's own infrastructure (VPC deployment for enterprise) |
| No model training on company data | Local LLMs (Ollama/LLaMA) or isolated API calls |
| Employee data separation | Individual activity data encrypted with personal keys; company sees only patterns |
| Consent at every layer | Employees see what's captured, can exclude apps/files/times |
| Contradiction arbitration | Humans always resolve flagged contradictions; Memoria never auto-resolves |

---

## Future: Skills Files (Post-MVP)

Once the knowledge graph is mature, the next layer is compiling it into machine-readable skills files that AI agents can execute against:

```yaml
# Example skills file (future feature)
Skill: Process_Refund_Request
Version: 2.4
Last_Updated: 2026-05-15
Source_Decisions:
  - "Support handbook v3.1: refund limit $20"
  - "Slack override (2026-03-12): Enterprise clients get $50 limit"
  - "CEO directive (2026-01-08): no questions asked under $10"

Rules:
  - If amount <= 10: auto_approve
  - If amount <= 20: approve_if_within_policy
  - If amount <= 50 AND customer_tier == "Enterprise": approve_with_note
  - If amount > 50: escalate_to_manager
  - If competitor == "AWS": human_review_required

Confidence: 0.94  # based on recency and consistency of sources
Human_In_Loop: true  # all refunds still confirmed by agent before execution
```

This is a Phase 3 feature. The Company Brain must be built and validated before skills files make sense.
