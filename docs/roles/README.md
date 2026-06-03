# Role-Specific Intelligence — Implementation Index

> Status: SDLC Phase 1 in scope. Other departments deferred to Phase 2.
> These docs are the Jira epics for the Role-Specific Intelligence layer.
> Each one is a self-contained implementation spec: morning brief mockup, key use cases,
> questions the role actually asks, connectors ranked by value, and tribal knowledge capture approach.

---

## How to Use This Index

Each role doc answers four questions:
1. **What do they see every morning?** — the brief mockup tells you what data pipelines to build
2. **What are the 3 highest-value use cases?** — these are the acceptance criteria for the role's implementation
3. **What connectors do they need?** — ranked by signal value, tells you what to build first
4. **What tribal knowledge do they hold?** — tells you what capture hooks to add

Build the shared infrastructure first (Knowledge Graph, Contradiction Detection, Morning Brief Generator).
Roles are largely configuration + UI on top of that shared layer.

---

## SDLC Phase 1 — Implementation Priority

| # | Role | Doc | Priority | Effort | Key Differentiator |
|---|------|-----|----------|--------|--------------------|
| 1 | Developer / Engineer | [DEVELOPER.md](./DEVELOPER.md) | **P0** | 14–18 days | Interactive repo graph with heat/ownership/coverage overlays; ghost author nudges |
| 2 | DevOps / SRE | [QA_DEVOPS.md](./QA_DEVOPS.md) | **P0** | 12–16 days | Prior-incident match at alert time; auto-runbook update after resolution |
| 3 | Product Manager | [PRODUCT_MANAGER.md](./PRODUCT_MANAGER.md) | **P0** | 10–14 days | Contradiction detection across specs, Jira, Slack; undocumented-decision detection |
| 4 | Engineering Manager | [MANAGER_TECHLEAD_ARCHITECT.md](./MANAGER_TECHLEAD_ARCHITECT.md) | **P1** | 10–12 days | Correlated risk signals (ticket silence + Slack questions = stronger signal than either alone) |
| 5 | QA / Test Engineer | [QA_DEVOPS.md](./QA_DEVOPS.md) | **P1** | 10–12 days | Test traceability graph; flakiness pattern auto-detection |
| 6 | Tech Lead | [MANAGER_TECHLEAD_ARCHITECT.md](./MANAGER_TECHLEAD_ARCHITECT.md) | **P1** | 8–10 days | 1-hour onboarding brief (5-layer assembly); PR review with historical ADR context |
| 7 | UI/UX Designer | [DESIGNER.md](./DESIGNER.md) | **P1** | 10–14 days | Figma MCP: context assembly → scaffold generation → implementation drift detection |
| 8 | Software Architect | [MANAGER_TECHLEAD_ARCHITECT.md](./MANAGER_TECHLEAD_ARCHITECT.md) | **P2** | 14–18 days | ADR drift detection; cross-team dependency mapping (4 signal layers) |

**Total: ~88–114 days** — but roles share the knowledge graph, morning brief engine, and connector layer.
Practical estimate with shared infrastructure built once: **~55–70 days** for all 8 roles.

---

## Shared Infrastructure (build once, powers all roles)

| Component | Powers | Estimated Effort |
|-----------|--------|-----------------|
| Knowledge Graph (entities + typed edges) | All roles | 12–15 days |
| Contradiction Detection Engine | PM, Manager, Tech Lead, Architect | 8–10 days |
| Knowledge Decay Scoring | All roles | 5–6 days |
| Morning Brief Generator (role-aware) | All roles | 4–5 days |
| Connector pipelines (GitHub, Jira, Slack, Confluence, Figma, PagerDuty) | All roles (different subsets) | 10–14 days |
| Ghost Author flow | Developer, Tech Lead, Manager | 6–8 days |

---

## Phase 1 Role Docs — Contents at a Glance

### Developer / Engineer → [DEVELOPER.md](./DEVELOPER.md)
- Morning brief: overnight PRs, CI failures, ambient risk, standup draft
- Interactive repo explorer (project → module → file → symbol) with toggleable overlays
- 5 proactive nudges (ghost author, non-obvious consumer, prior attempt, PR conflict, test exists + failing)
- 10 specific questions developers ask
- Tribal knowledge capture: ghost author flow + zero-friction 2-sentence prompt on merge

### UI/UX Designer → [DESIGNER.md](./DESIGNER.md)
- Morning brief: blocked designs, overnight Figma comments, design system updates
- Figma MCP workflow: Stage 1 context assembly → Stage 2 scaffold generation → Stage 3 iteration with constraints
- 5 nudges: implementation drift, component update, undocumented constraint, stakeholder constraint, research finding
- 10 specific questions designers ask
- Tribal knowledge: Design Decisions Log per project; active capture prompt; proactive surfacing on screen start

### Product Manager → [PRODUCT_MANAGER.md](./PRODUCT_MANAGER.md)
- Morning brief: what shipped, blocked tickets, decisions needed, sprint health
- Contradiction detection table (5 realistic cross-source conflict types)
- 5 nudges: sprint drift, stakeholder alignment gap, repeat customer complaint, knowledge silo risk, undocumented decision
- 10 specific questions PMs ask
- Tribal knowledge: decision threading, departure capture protocol, rationale tagging

### QA / Test Engineer + DevOps / SRE → [QA_DEVOPS.md](./QA_DEVOPS.md)

**QA:**
- Morning brief: CI health, coverage gaps, new features needing test cases
- Test traceability graph (test↔code edges, requirement↔test edges, flakiness history, co-failure clusters)
- 5 questions QA engineers ask
- Tribal knowledge examples (staging DB reset timing, parallel test constraints, feature flag dependencies)

**DevOps / SRE:**
- Full 3:17am P1 incident walkthrough — Memoria correlates pre-emptively before engineer types anything
- Auto-runbook update flow (SRE approves → PR against runbook + Jira prevention ticket)
- Morning brief: overnight incidents, deployments, infra drift, runbook staleness, pending toil
- 5 questions DevOps engineers ask

### Engineering Manager + Tech Lead + Software Architect → [MANAGER_TECHLEAD_ARCHITECT.md](./MANAGER_TECHLEAD_ARCHITECT.md)

**Engineering Manager:**
- Morning brief: PR health, shipping velocity, at-risk deadlines
- 3 proactive alerts (silent engineer, chronic review bottleneck, correlated risk signal)
- 5 questions EMs ask (knowledge concentration, contractor onboarding, velocity drop, invisible work, principal leaving)

**Tech Lead:**
- Use case A: "Should we use approach A or B?" — buried in 2-year-old Slack thread
- Use case B: Onboarding brief (5-layer assembly — What & Why, How It Works, Where Bodies Are Buried, Who To Talk To, First PRs)
- Use case C: PR review with full ADR + historical decision context
- 5 questions Tech Leads ask

**Software Architect:**
- Use case A: ADR drift detection (active drift / accumulated drift / intentional exceptions — three different remediation paths)
- Use case B: Cross-team dependency mapping (structural + conversational + knowledge + temporal layers)
- Use case C: "What would change if we replaced service X?" (5-section impact analysis including prior replacement attempts)
- 5 questions Architects ask; recommended implementation order

---

## Phase 2 — Other Departments (future)

| Role | Key Value Proposition |
|------|-----------------------|
| VP / Director | Portfolio health, throughput vs. roadmap commitments, cross-team risk radar |
| C-Suite / Executive | Board-ready digest, strategic risk, cross-department dependency surface |
| Finance | Budget vs. engineering velocity, cost-per-feature, vendor contract knowledge |
| Customer Success | Customer-specific knowledge, known issues, SLA tracking |
| Sales / Pre-Sales | Technical capability, competitive differentiation, integration constraints |
| Legal / Compliance | Policy contradiction detection, compliance gaps, contract knowledge |

Phase 2 starts only after all 8 SDLC roles are shipped and the shared infrastructure is stable.
