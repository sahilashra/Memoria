# Memoria for QA Engineers & DevOps/SRE

> Status: SDLC Phase 1
> DevOps Incident Response is the single highest-ROI use case across all roles.

---

# QA / TEST ENGINEER

## Morning Brief

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TEST HEALTH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  CI overnight: 13 failures in payments module
  → 9 of those same 9 that failed last Thursday (billing cycle trigger)
  → 4 are NEW failures — likely from PR #441

  Flaky this week: test_ab_experiment_cohort (passes on retry, 3rd time in 5 days)
  Chronic (failing 3+ days): test_refresh_on_expiry (11 days, no ticket)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  COVERAGE GAPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • PR #441 modified cart/discount.py — no test file touched
    Existing coverage: 3 tests in test_cart.py (last updated 6 months ago)
  • PR #445 (in review) touches auth module — high defect density area,
    you should review before merge

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  NEW FEATURES NEEDING TEST CASES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • PROJ-388 moved to "Done" — Memoria drafted test case stubs from
    the acceptance criteria. Review and approve?
```

---

## Graph: Test Traceability

**Test-to-code edges:**
- `test_checkout_flow.py` → `checkout/views.py`, `checkout/cart.py`, `stripe_integration.py`
- When any source node changes, downstream test nodes light up

**Requirement-to-test edges:**
- Jira PROJ-112 ("Guest checkout must not require email") → 3 tests covering it
- Instantly answers: "Are we testing this requirement?"

**Clicking a test node reveals:**
- Pass/fail rate over last 30 runs
- Time-of-day and environment correlations ("fails 80% of runs between 2am–4am on staging-eu")
- Last 3 times it failed: what changed in the codebase at the time
- Whether it's quarantined, who quarantined it, the tracking ticket
- Co-failure clusters: tests that tend to fail together (implicit dependencies)
- Staleness signal: when the test was last updated vs. when the code it covers was last changed

**Pattern detection (passive):**
After 30 days: "Whenever `config/feature_flags.py` is modified, `test_ab_experiment_cohort` fails within 2 runs — 7 occurrences observed." Not a rule someone wrote; a pattern Memoria found.

---

## Questions QA Engineers Ask

1. "Which tests cover the coupon redemption logic that landed in PR #447 yesterday, and when were they last updated?"
2. "Show me every test that has failed more than twice in the last 2 weeks but is not currently tracked in Jira."
3. "What's the test coverage story for the auth module — which paths are untested, and which tests are outdated?"
4. "Last time we had a regression in the checkout flow, what was the root cause and which test should have caught it but didn't?"
5. "Which tests should I not run in parallel right now, and why?"

---

## QA Tribal Knowledge

What Memoria captures and surfaces:

- `test_checkout_flow` is flaky Tuesday/Wednesday because staging DB resets at 1am Monday. Don't re-run more than twice before Wednesday noon.
- Never run payment tests in parallel — they share a DB sequence for transaction IDs.
- Mobile Safari E2E tests require `LEGACY_REDIRECT=false` on staging. If anyone toggled it, tests fail with a misleading 302.
- Performance tests are calibrated against exactly 50,000 seed records. If staging was migrated, baselines are wrong.
- `test_invoice_generation` takes 8 minutes and must stay in CI — it caught the most expensive bug ever shipped.

---

---

# DEVOPS / SRE

## Incident Response — The Highest-Value Use Case

### Scenario: 3:17am, P1 Alert — Payment Processing Latency

**Step 0: Memoria correlates before the engineer types anything**

By the time the on-call SRE opens their terminal, Memoria has already:
- Matched alert fingerprint against incident history
- Found 3 prior incidents with similar signatures
- Pulled Memory Banks: payments service, Stripe integration, DB connection pool runbook, last 5 production deploys
- Identified: a deploy happened 47 minutes ago (v2.31.4 → v2.31.5)

**Step 1: Opening brief**

```
Similar to INC-0089 (Nov 2024).
That incident: Stripe webhook handler timing out due to DB connection pool
exhaustion caused by a migration not gated behind a feature flag.
Resolution time: 34 minutes.
Fix: rolled back migration, added connection pool monitoring.

⚠️ Note from INC-0089 (NOT in the official runbook):
"The standard DB runbook doesn't cover connection pool — check
POOL_MAX_CONNECTIONS env var on payments pods before restarting."
```

**Step 2: Live investigation support**

- "Show me connection pool metrics for the payments pods" → surfaces specific Prometheus queries that worked in INC-0089
- "What changed in v2.31.5?" → PR diff summary + engineer's comment: "migrates invoice table to UUID — migration runs on startup, wasn't tested against production row count"
- "Known issues with UUID migrations and connection pools?" → finds a Slack message from 6 months ago: "UUID migrations on tables >10M rows lock the table for seconds in Postgres 14 — use `gen_random_uuid()` not `uuid_generate_v4()` and run as background migration"

**Step 3: The fix**

SRE runs 3 commands not in any runbook:
1. `kubectl rollout undo deployment/payments-service -n production`
2. `psql -c "SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE query LIKE '%invoice%' AND state = 'active'"`
3. Custom pod-cycling script to reset connection pool without dropping in-flight transactions

**Step 4: Auto-runbook update (the capture moment)**

Incident resolved at 4:02am (45 min). Memoria prompts:

```
"This incident has ended. I noticed 3 commands were run that aren't in any runbook.
Want to capture them?"
```

Memoria drafts:
```
INC-0141 (May 2026): UUID migration on large table → connection pool exhaustion
Symptoms: latency spike + connection errors on payments service
Root cause: ALTER TABLE invoices ran synchronously at startup in v2.31.5,
  locking table for 4+ min on 40M rows
Resolution: (1) rollback deployment, (2) cancel active migration queries,
  (3) cycle pods in order [replica-2 → replica-1 → replica-0]
Prevention: all migrations >1M rows must use CONCURRENTLY and be flagged
  in the deploy checklist
```

SRE approves with minor edits. Memoria:
1. Appends to the incident's Memory Bank
2. Opens a PR against the runbook
3. Creates a Jira ticket for the "migrations >1M rows" prevention rule

**Next engineer at 3am gets INC-0141 in Step 1. Resolution time: 8 minutes.**

---

## Morning Brief (DevOps)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  OVERNIGHT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • INC-0139 fired at 2:14am (latency spike, payments) — resolved by Ana
    Root cause in Memory Bank. Action item: add circuit breaker ticket created.
  • 3 deployments to production: auth-service v1.8.2, api-gateway v3.1, ui v4.7
  • Post-deploy: no anomalies detected on auth or api-gateway. UI latency +12ms
    (within baseline range)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  INFRA DRIFT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • staging-worker pod count: 2 (expected: 3) — no alert fired, manual check needed
  • Feature flag LEGACY_REDIRECT toggled at 11pm without a ticket

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  RUNBOOK STALENESS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • redis-failover.md references redis-primary-01 (decommissioned in March)
  • k8s-scaling.md references deprecated autoscaler API version

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PENDING TOIL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • report-worker pod manually restarted every Monday morning — 6 weeks in a row
    No ticket exists. This is automatable.
```

---

## Questions DevOps Engineers Ask

1. "We're seeing elevated 502s on the API gateway — what incidents have we had with similar symptoms in the last year, and what was the fix?"
2. "Walk me through every change made to the production Kubernetes cluster in the last 72 hours — deployments, config map changes, secret rotations, manual kubectl commands."
3. "Which of our runbooks would fail if an engineer followed them exactly right now, because the infrastructure they describe has changed?"
4. "The on-call rotation switches tonight and the incoming engineer hasn't worked with payments infrastructure before — generate a briefing covering the 5 most likely incidents and the tribal knowledge they need."
5. "We're deprecating the legacy-auth service next sprint — what does Memoria know about every downstream dependency, runbook reference, test, and incident involving it?"
