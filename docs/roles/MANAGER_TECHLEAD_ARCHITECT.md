# Memoria for Engineering Managers, Tech Leads & Architects

> Status: SDLC Phase 1
> The Architect drift detection and Tech Lead onboarding flows are the deepest value propositions.

---

# ENGINEERING MANAGER

## Morning Brief

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PR HEALTH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  8 open PRs | 3 awaiting review >3 days (outliers vs. team baseline of 1.2 days)
  Bottleneck reviewer this week: Marcus (6 assigned, 1 completed)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SHIPPING VELOCITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Engineers with no merges in 5+ days: Priya, David
  (Note: Priya has high Slack activity on incident threads — likely firefighting)
  (Note: David is quiet everywhere — possible blocker or unclear requirements)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  AT-RISK DEADLINES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • Payments integration milestone: 8 days
    3 of 5 linked tickets: no activity this week
    Open Slack questions from 2 engineers: unresolved
  • AUTH-89 has been "In Progress" for 11 days (expected: 3)
```

## Proactive Alerts (3 highest-value)

1. **Silent engineer** — "Priya has had no merges, no PR comments, and minimal Slack activity for 4 days, on a sprint where she owns 40% of the work." Earliest possible signal of a blocker, before standup catches it.

2. **Chronic review bottleneck** — "PR #847 has been awaiting review from Marcus for 6 days. Marcus was a review bottleneck in 3 of the last 5 sprints." Pattern, not incident.

3. **Correlated risk signal** — "Payments milestone in 8 days. 3 of 5 linked tickets have had no activity this week AND there are unresolved Slack questions from two engineers." Ticket silence + Slack questions together is a stronger risk signal than either alone.

## Questions EMs Ask

1. "Which engineer has the most context on the payments service? I need to assign new PCI compliance work."  
   *(Memoria cross-references commit history, PR authorship, review activity, Slack mentions — returns who has been in the most technical discussions, not just who wrote the most code)*

2. "We're onboarding a contractor for 6 weeks. Which parts of the codebase have the steepest knowledge concentration — one person owns almost all the context?"

3. "My team's sprint velocity dropped 30% last month. What changed?"  
   *(Memoria correlates against refactor starts, meeting load increase, engineer leave, production incident spike)*

4. "Which engineers are doing invisible work — not in Jira tickets but consuming real time?"  
   *(Detects: low ticket throughput + high Slack activity on incidents + high PR review counts with no merges)*

5. "A principal engineer is leaving in 3 weeks. What knowledge exists only in their head?"  
   *(Services where they're sole meaningful committer, ADRs they authored, Slack threads with decisions nowhere else)*

---

---

# TECH LEAD

## Three Core Use Cases

### A. "Should we use approach A or B?" — answer buried in a 2-year-old Slack thread

Memoria searches simultaneously: ADRs, past PR descriptions, design docs, and Slack. It surfaces:
- The prior discussion (even from 2019, if relevant)
- Who argued for what, what the deciding factors were, what the outcome was
- The **outcome signal**: is there evidence the previous decision caused problems? (subsequent incidents, Slack complaints, PRs reverting the choice)

Knowledge decay weighting ensures a fresh Slack discussion about scaling problems ranks higher than a 2-year-old doc saying everything is fine — but the old doc still appears so the Tech Lead can see how the thinking evolved.

Output: "Here is the prior art, what was decided and why, how that decision aged, and the two open questions your current discussion hasn't addressed yet."

### B. Onboarding a new engineer to a complex service in under 1 hour

Trigger: "Generate an onboarding brief for [name] joining the auth service."

Memoria assembles 5 layers, not a dump of links:

| Layer | Content | Source |
|-------|---------|--------|
| 1 — What & Why | 3-paragraph plain-English summary of purpose and rationale | ADRs + original design doc (not README — often stale) |
| 2 — How it works today | Key modules, data flow, external deps, 3 most impactful architectural decisions | Memory Bank for auth-service |
| 3 — Where the bodies are buried | Sharp edges, pending unresolved decisions, tribal knowledge | Incident threads, unresolved Slack debates |
| 4 — Who to talk to | Specific people with context on specific parts, not just "ask the tech lead" | Commit history, PR reviewer patterns |
| 5 — First PRs | Starter tickets from Jira — areas with good test coverage, clear scope, supporting docs | Jira + Memory Bank cross-reference |

Experienced Tech Lead can walk through this in 45 minutes → new engineer makes a PR that afternoon.

### C. PR review with full historical context

For every significant file touched in a PR, Memoria surfaces:
- Last time this code was substantially changed and why
- Whether there were design debates at the time
- Whether the current PR contradicts an existing ADR or past decision

When a reviewer sees an unfamiliar pattern — a custom retry loop instead of the shared library: "Why wasn't the standard retry utility used?" → Memoria: "The shared retry utility had a known issue with this service's connection pool behavior, documented in Slack by [engineer] in January. The workaround was intentional." Reviewer makes an informed call, not a confused guess.

## Questions Tech Leads Ask

1. "We're changing how auth issues tokens. What else in the system will break, and what teams depend on this being stable?"
2. "I'm reviewing a PR that removes `X-Request-ID` header forwarding. Was this ever a deliberate design choice?"
3. "We had a production incident last quarter involving the queue consumer. What was the root cause, and is there anything similar in the current codebase?"
4. "New engineer starts Monday on the data pipeline service. Give me a 1-hour onboarding brief."
5. "We've been 'temporarily' using polling instead of webhooks for inventory sync for 18 months. Is there any record of why?"

---

---

# SOFTWARE ARCHITECT

## Three Core Use Cases

### A. ADR Tracking and Drift Detection

Two modes: passive monitoring and active querying.

**Passive drift watcher:** Memoria continuously indexes the codebase and cross-references against every ADR. Each ADR encodes constraints ("services must not directly access other services' databases"). Memoria translates constraints into detectable patterns and flags violations.

Drift report layers:
- **Active drift**: constraint violated in a recent commit — high urgency
- **Accumulated drift**: violation in code that's been there for months, built on top of — lower urgency, higher blast radius
- **Intentional exceptions**: PR or ADR itself noted "exception to ADR-0023 because [reason]" — not drift

Example: "We decided to avoid direct DB access from services, but 3 services now do it"

Memoria shows the *history* of how each violation happened:
- Service A: "temporary, pending migration to shared data access layer" (PR from 14 months ago). Migration never happened.
- Service B: reviewer flagged it, author replied "we'll fix it next sprint" (6 months ago). Still unfixed.
- Service C: no discussion at all (3 months ago).

Three violations, three different stories, three different remediation paths.

**Active ADR interrogator:** "Which ADRs are most at risk of being completely abandoned?"  
Memoria scores ADRs by ratio of violated constraints, weighted by age and scope. An ADR where 4 of 5 constraints are violated across 8+ services is effectively dead — the architect can decide: rescind it, update it to reflect current practice, or remediate with full scope knowledge.

### B. Cross-Team Dependency Mapping

Four signal layers simultaneously:

| Layer | What it captures |
|-------|-----------------|
| Structural | What calls what, what imports what, what reads whose DB |
| Conversational | Services mentioned together in Slack, teams @-mentioned together in incidents |
| Knowledge | Engineers who understand services owned by other teams (informal "go-to person") |
| Temporal | Services that always deploy together, teams whose sprints are blocked by the same upstream |

"Show me the top 10 cross-team dependencies ranked by coupling risk" → each connection qualified: "Team A calls Team B's payment API 40x/sec with no circuit breaker (structural, high risk), and Team A has 2 engineers who are de facto on-call experts for a Team B service (knowledge, high risk if either leaves)."

### C. "What would change if we replaced service X?"

Five sections in the impact analysis:

1. **Direct callers** — code-level dependencies (API calls, shared imports, schema deps)
2. **Indirect callers** — transitive blast radius
3. **Tribal knowledge loss** — engineers who'd need to preserve context during replacement (especially if leaving soon)
4. **Historical gotchas** — non-obvious behaviors callers have come to depend on, even if they shouldn't
5. **Prior replacement attempts** — "A replacement was attempted in Q3 2023 and abandoned after 6 weeks due to [reasons from Slack and Jira]." Worth more than any architecture diagram.

## Questions Architects Ask

1. "We agreed in ADR-0031 that all services communicate via the event bus. Which services are making direct synchronous API calls, and when was each violation introduced?"

2. "I'm doing a quarterly architecture review. Which parts of the system have drifted furthest from their original design intent, measured against ADRs?"

3. "We're considering extracting notifications logic into its own service. Full blast radius — code dependencies, team dependencies, knowledge dependencies, and prior attempts."

4. "Which of our architecture decisions are now effectively dead — violated so widely that we're building on top of the violation rather than the original intent?"

5. "Show me every place in the system where we have implicit coupling not captured in any ADR, design doc, or formal dependency — the shadow architecture."

---

## Effort Notes

The ADR drift detection and cross-team dependency mapping are the most complex features in this pillar. They require:
- Structured parsing of ADRs into machine-checkable constraints (NLP extraction)
- Codebase analysis beyond simple text search (import graph, API call detection)
- Conversational signal correlation (Slack co-mention patterns)

Recommended implementation order:
1. ADR ingestion + constraint extraction (text-based, simpler)
2. Codebase pattern matching against constraints
3. Cross-repo dependency graph
4. Conversational layer (Slack co-mention analysis)
5. Drift scoring and architect dashboard
