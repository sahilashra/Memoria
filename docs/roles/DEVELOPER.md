# Memoria for Developers / Engineers

> Status: SDLC Phase 1 — highest priority role

---

## Morning Brief

When an engineer opens their laptop, Memoria has already read everything overnight:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WHAT HAPPENED WHILE YOU WERE OFFLINE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • PR #341 (FE team) merged at 11:43pm — touches auth/middleware.py
    3 tests in tests/auth/ are now red in CI
  • AUTH-89 moved to "In Review" — 4 inline comments on JWT expiry logic
  • Staging deploy at 2am failed — same migration issue as sprint-3
    (Memory Bank has the fix from last time)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TODAY'S PRIORITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. AUTH-89 review comments — blocking another engineer's PR
  2. Fix 3 failing auth tests (from PR #341)
  3. Standup in 38 min — draft status: "Completed auth module refactor,
     fixing downstream test regressions from FE merge"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  AMBIENT RISK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • rate_limiter.py (your current PR) was also touched in #341
    → likely merge conflict incoming
  • python-jose pinned at vulnerable version (CVE published yesterday)
  • TODO in payments/gateway.py line 203: "revisit after FE deploys" — that's now
```

---

## Graph: Interactive Repo Explorer

The knowledge graph becomes a navigable repo explorer:

**Click on a project node → expands to module nodes:**

```
auth-service
├── src/
│   ├── auth/           (🔴 3 tests failing, 94% coverage)
│   ├── payments/       (🟡 hot file: 8 PRs this month)
│   └── middleware/     (⚠️ last author departed 3 months ago)
├── tests/
│   ├── test_auth.py    → click: CI history, flakiness %, tribal notes
│   └── test_jwt.py     → click: "failing due to PR #341, fix in Memory Bank"
└── migrations/
```

**File node overlays (toggleable):**
- 🔥 Heat map: commit frequency last 30/90 days
- 👤 Ownership: color by last author (grey = departed engineer)
- 🧪 Test coverage: red/yellow/green per file
- 🔗 PR blast radius: hover a file → highlights all files changed together across PRs

**Cross-repo edges:**
- API contract edges: if `payments-service` calls `auth-service /v2/validate`, draw that edge. If auth's schema changes, edge turns red.
- Shared library drift: two repos pinned to different versions of the same internal lib

---

## Proactive Nudges (5 highest-value)

1. **"Ghost author"** — when you open a file or create a PR touching it: "The last person with deep context on `billing/invoice_generator.py` left 4 months ago. Their mental model is captured in PR #288 and a Slack thread from March. Read it before you edit?"

2. **"Non-obvious downstream consumer"** — at PR creation: "You're modifying `auth/token_schema.py`. The data-pipeline repo imports this schema via a vendored copy last synced 6 weeks ago. Your change will silently break the pipeline at next sync."

3. **"Ticket was attempted before"** — when you pick up a Jira ticket: "AUTH-112 was opened and closed as 'won't fix' in Q3 last year. The engineer explained why the naive solution breaks under concurrent token refresh. Memoria has that thread."

4. **"PR conflict with in-flight work"** — "Ana's PR #349 (1 approval) also touches `rate_limiter.py` and restructures the class you're extending. Coordinate before either of you merges."

5. **"Test you're writing already exists and is failing"** — "There are 3 existing tests for `token_refresh()`. One (`test_refresh_on_expiry`) has been failing for 11 days. Investigate before adding coverage."

---

## Connectors (ranked by value)

| Tier | Connector | What it captures |
|------|-----------|-----------------|
| 1 | GitHub/GitLab | PRs, commits, reviews, CI status, diffs |
| 1 | Local filesystem | Open files, active branch, uncommitted changes |
| 1 | CI/CD pipelines | Test results, build logs, failure reasons |
| 2 | Jira/Linear | Tickets, sprint state, blockers |
| 2 | Slack | Threads linked to PRs and tickets (not all of Slack) |
| 2 | Test result history | Pass/fail time-series, flakiness signal |
| 3 | PR review comments | Inline architectural explanations |
| 3 | Local shell history (opt-in) | Debug commands, git bisect sessions |
| 3 | Confluence/Notion | ADRs — highest-value document type |
| 3 | Dependency vulnerability feeds | CVEs vs. lockfile versions |

---

## Questions Engineers Ask Memoria

1. "Why does the payment service occasionally return 503 on `/charge` between 2–4am?"
2. "What changed between last Tuesday and today that could have broken the E2E auth tests?"
3. "I need to add a field to the User model — what else will break, including services I don't know about?"
4. "Who actually understands the retry logic in `job_queue.py`? I need a human."
5. "We had a major incident in March around DB connection pooling. What did we change after that, and are all action items done?"
6. "What's the actual difference between `AuthToken` and `SessionToken`? They seem to do the same thing."
7. "I'm getting a `NullPointerException` in `invoice_renderer.py` line 87 — has anyone seen this before?"
8. "Which parts of the codebase have zero test coverage and were modified in the last 30 days?"
9. "We're considering swapping Redis for a different cache layer — what would that actually touch across all services?"
10. "I have to onboard to the billing service this week. Give me the 20-minute version: how it works, what's brittle, and what I should never touch without asking someone first."

---

## Tribal Knowledge Capture

**What lives only in a senior dev's head:**
- Why a particular architecture choice was made (and the alternatives that were tried and failed)
- "Load-bearing but embarrassing" code — wrong, known, unfixed because the fix is risky
- Informal ownership: "Don't touch the XML parser without asking Riya, even though she hasn't committed in 8 months"
- Known-broken things that are *intentionally* not fixed: "The memory leak in the worker restarts every 6 hours deliberately"
- "It only breaks when" knowledge: quirks that only appear in production under specific conditions

**How Memoria captures the "why":**
- **PR descriptions** with reasoning ("We're NOT using the ORM here because of a performance issue in the Q2 load test")
- **PR review back-and-forth** — "I thought about that but it breaks when X" is gold
- **Slack threads linked from PRs** — the decision that didn't fit in the PR description
- **Commit message bodies** (rare but high signal)

**Ghost Author flow:** When a key engineer leaves, Memoria generates a "knowledge profile" — synthesises every PR description they wrote, every review comment, every Slack thread where they explained a decision. Attaches it as a permanent annotation to every file they primarily authored. Surfaces it proactively whenever another engineer touches those files.

**Zero-friction capture UX:** When an engineer merges a PR with a short description touching an undocumented file, Memoria prompts (once): "This PR touches `auth/middleware.py` — a file with no documentation. Want to add 2 sentences that future engineers will see?" The bar is intentionally low.
