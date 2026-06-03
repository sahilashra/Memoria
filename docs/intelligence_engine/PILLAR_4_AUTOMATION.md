# Pillar 4 — Proactive Surfacing & Memory Bank Enrichment

> **The "Insight Layer"** — turns detected patterns into richer Memory Banks
> and proactive context delivery. Not automation execution.
> Coverage today: **20%** | Status: 🔴 Mostly missing

---

## Goal

When Pillar 3 identifies a repeating pattern or detects a contradiction between
sources, surface that insight to the user and enrich the relevant Memory Bank
automatically. The output is better documentation and smarter context delivery
— not script execution.

---

## What Already Exists

### `memoria/companion.py`
- **Contextual Knowledge Retrieval** — when the active window or clipboard
  matches a known project, surfaces relevant Memory Bank snippets in a
  floating overlay. This is the "retrieval" part of the copilot loop.
- ChromaDB semantic search underpins the lookup.

### `memoria/observer.py`
- **Proactive draft queuing** — when files change significantly, auto-queues
  a Memory Bank draft update and notifies the user. A form of proactive
  surfacing, though triggered by file changes not workflow patterns.

---

## What Is Missing

| Gap | Description | Effort |
|-----|-------------|--------|
| Contradiction Detection Engine | When two ingested sources disagree on the same fact/policy/rule, flag it and ask the domain owner to arbitrate | Medium |
| Knowledge Decay Scoring | Weight recent sources (Slack from yesterday) higher than stale ones (Notion doc from 2022); surface freshness warnings in Memory Banks | Small |
| Pattern → Memory Bank write-back | When Pillar 3 confirms a recurring workflow (≥3 occurrences), auto-draft a `§ Common Workflows` section in the relevant Memory Bank | Medium |
| Morning Brief generator | On first open each day, synthesise overnight changes (PRs merged, tickets updated, Slack decisions) into a role-specific digest | Medium |
| Proactive companion nudges | Companion overlay surfaces ranked insights when context switches: "This file was last touched by an engineer who left — here's their rationale from the PR description" | Medium |
| Context-triggered surfacing | Detect when a known work pattern is starting and surface the relevant Memory Bank section automatically — no script execution, just knowledge delivery | Small |

> **Removed from scope:** RPA script generation, automation execution runner, YAML workflow runner, Playwright script output. These belong in a separate automation product. Memoria's output is enriched knowledge, not executable scripts.

---

## Architecture Decisions (Resolved ✅)

> **D4 →** Contradiction Detection Engine + Knowledge Decay scoring + Pattern → Memory Bank enrichment. Not script execution. Pillar 4's output is richer documentation and proactive context delivery, not runnable automations.

## Architecture Decision

**Three engines: Contradiction Detection, Knowledge Decay scoring, and Pattern → Memory Bank write-back. The output of this pillar is richer knowledge, not runnable scripts.**

### 1. Contradiction Detection Engine

When two ingested sources disagree on the same fact, flag it and route to the domain owner:

```python
def detect_contradictions(memory_banks: list[MemoryBank]) -> list[Contradiction]:
    """
    Compare overlapping claims across Memory Banks using semantic similarity.
    Flag pairs where the same entity is described with conflicting values.
    """
    contradictions = []
    for bank_a, bank_b in combinations(memory_banks, 2):
        for claim_a in bank_a.claims:        # extracted (subject, predicate, object) triples
            for claim_b in bank_b.claims:
                if (claims_share_subject(claim_a, claim_b)
                        and claims_contradict(claim_a, claim_b)):
                    contradictions.append(Contradiction(
                        source_a=bank_a.source_url,
                        source_b=bank_b.source_url,
                        claim_a=claim_a,
                        claim_b=claim_b,
                        detected_at=datetime.utcnow(),
                    ))
    return contradictions
```

Example contradictions surfaced:

| Source A | Source B | Flag |
|----------|----------|------|
| Q3 roadmap: "Feature X ships August 15" | Slack thread (yesterday): "blocked until September" | ⚠️ Ship date conflict |
| Handbook: "Refund limit $20" | Slack policy: "up to $50 for Enterprise" | ⚠️ Policy conflict |
| Design spec: "email required at signup" | Figma mockup: "optional, add later" | ⚠️ Spec conflict |

### 2. Knowledge Decay Scoring

Recent sources outweigh stale ones. Apply a time-decay weight to every Memory Bank claim:

```python
def decay_weight(source_date: datetime, half_life_days: int = 90) -> float:
    """
    Exponential decay: a source from `half_life_days` ago has weight 0.5.
    A Slack message from yesterday: ~1.0. A Notion doc from 2022: ~0.05.
    """
    age_days = (datetime.utcnow() - source_date).days
    return 0.5 ** (age_days / half_life_days)
```

Freshness warnings surface in the Memory Bank UI: `⚠️ This architecture decision (Notion, 2022) may be stale — 3 more recent Slack threads discuss this topic.`

### 3. Pattern → Memory Bank Write-back

When Pillar 3 confirms a recurring workflow, auto-draft a Memory Bank section:

```python
def write_workflow_to_memory_bank(workflow: Workflow, bank: MemoryBank) -> None:
    """Append detected pattern to § Common Workflows section."""
    section = f"""
### {workflow.name} (~{workflow.duration_p50_min} min, {workflow.recurrence})
**Trigger:** {workflow.trigger_description}
**Steps:** {' → '.join(workflow.steps)}
**Files typically touched:** {', '.join(f'`{f}`' for f in workflow.key_files)}
**Friction point:** {workflow.friction_note or 'None detected'}
<!-- auto-detected by Pillar 3, {workflow.occurrences} occurrences, confirmed {date.today()} -->
"""
    bank.append_to_section("§ Common Workflows", section)
    learner.queue_draft(bank, confidence=workflow.confidence)
```

## Proposed Surfacing Flow

```
Pillar 3 detects "wf_auth_jwt_fix" — 8 occurrences, 0.82 confidence
          ↓
  Contradiction Engine scans all sources on related topics
  (e.g. "ADR-0012 says token expiry = 30min, yesterday's Slack says 15min → flag")
          ↓
  Knowledge Decay: stale sources deprioritised, freshness warning added
          ↓
  Morning Brief generator queues insight for tomorrow's opening digest
          ↓
  Memory Bank auto-draft queued via learner.py:
    "§ Common Workflows: JWT Token Expiry Fix (~2 hrs, biweekly)"
    confidence: 0.82 → user reviews draft, one-click approve
          ↓
  Companion overlay surfaces when context matches next time:
  ┌──────────────────────────────────────────────────────┐
  │  [Memoria]  auth-service                             │
  │──────────────────────────────────────────────────────│
  │  Key files: auth/jwt.py ...                          │  ← snippet area
  │──────────────────────────────────────────────────────│
  │  💡 You've done this 8×: JWT Token Expiry Fix        │  ← insight nudge
  │  [View Memory Bank section]  [Dismiss]               │
  └──────────────────────────────────────────────────────┘
```

---

## Pattern Registry Schema

Tracks detected patterns and their Memory Bank documentation status (not automations):

```json
{
  "patterns": [
    {
      "id": "wf_auth_jwt_fix",
      "name": "JWT Token Expiry Fix",
      "occurrences": 8,
      "confidence": 0.82,
      "memory_bank_section": "§ Common Workflows",
      "memory_bank_updated": "2026-05-15",
      "contradiction_flags": [
        "ADR-0012 says expiry=30min; Slack thread (2026-05-14) says 15min"
      ],
      "status": "documented",
      "last_surfaced_to_user": "2026-05-15T09:00:00",
      "dismissed_count": 0
    }
  ]
}
```

---

## Effort Estimate

| Component | Days |
|-----------|------|
| Contradiction Detection Engine | 3–4 |
| Knowledge Decay scoring + freshness warnings | 1–2 |
| Pattern → Memory Bank write-back | 2–3 |
| Morning Brief generator | 3–4 |
| Companion nudge (read-only: View Memory Bank) | 2–3 |
| Pattern Registry storage | 1 |
| **Total** | **12–17 days** |

