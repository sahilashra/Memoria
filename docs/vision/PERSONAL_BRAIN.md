# Memoria — Personal Brain

> A second brain that has read everything you've ever worked on, knows who you are,
> and proactively helps you be more productive every day.

---

## The Vision

When Sahil opens his laptop at 9am, Memoria already knows:
- What he was working on yesterday and where he left off
- What happened overnight (PRs merged, tickets updated, Slack decisions made)
- What's most important to move on today, ranked
- Personal context he's shared: he's a cricket fan, he's building Memoria, he has a standup at 10am

The opening screen is not a list of notifications. It's a personalised brief assembled from everything Memoria knows about the person — their projects, their interests, their patterns.

---

## The Morning Brief

```
Good morning, Sahil ☀️

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WHERE YOU LEFT OFF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Yesterday you were deep in Memoria's Intelligence Engine docs.
  The PILLAR_3_AI_MODELING.md still has 3 open decisions marked
  as [ TODO ]. You were halfway through the sequence mining section.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WHAT HAPPENED OVERNIGHT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • PR #341 merged — touches auth/middleware.py
    → 3 tests in auth-service are now failing in CI
  • Ticket MEMORIA-89 moved to "In Review"
    → 4 inline comments from the reviewer waiting on you
  • The staging deploy at 2am failed (same migration issue as sprint-3)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TODAY'S FOCUS (ranked by impact)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Review MEMORIA-89 comments — blocking another engineer's PR
  2. Fix the 3 failing auth tests (caused by #341)
  3. Standup in 38 min — your draft status: "Completed IE docs, fixing test regressions"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FOR YOU, PERSONALLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🏏 India vs Australia, 4th Test ended last night.
     India won by 7 wickets. Kohli: 82 runs.

  💡 You've been on the IE docs for 3 days straight — Memoria
     suggests: 30-min deep work session on PILLAR_3 first,
     then context-switch to the failing tests.
```

---

## What Memoria Knows About You (Personal Layer)

Memoria learns personal context from what you tell it and what it observes:

| Signal | How captured | What it unlocks |
|--------|-------------|-----------------|
| Interests (cricket, finance, etc.) | Explicit setup or inferred from browsing patterns | Relevant news/updates in morning brief |
| Work patterns | Session history, commit times | Optimal scheduling suggestions |
| Active projects | Ingested repos, docs | Project-specific context |
| Collaboration network | Git history, Slack patterns | "Who to ask" recommendations |
| Goals (personal/professional) | Explicit in setup | Progress nudges, task prioritisation |
| Preferred working style | Observed over time | Deep work vs. meeting day scheduling |

---

## Personal vs. Work Mode

Memoria runs in two modes, switchable with one keystroke:

```
PERSONAL mode          WORK mode
─────────────────      ──────────────────────
Cricket scores         PR review queue
Personal projects      Sprint status
Reading list           Team blockers
Gym/health tracking    Deployment status
Personal reminders     Company knowledge graph
```

Personal data and work data are kept in separate Memory Bank partitions with separate encryption keys. Personal mode data never appears in work exports.

---

## Personalisation Flows

### First Run Setup

```
Welcome to Memoria, [name].

To personalise your experience, tell me about yourself:

  What are your main interests outside work?
  > Cricket, personal finance, building products

  What projects are you currently working on?
  > Memoria (main), a side project in FastAPI

  What's your preferred working style?
  > Deep work in the morning, meetings in the afternoon

  Any goals you'd like Memoria to help track?
  > Ship Memoria's Intelligence Engine by end of quarter

Memoria will personalise your daily brief based on this.
You can update any of this at any time with `memoria profile edit`.
```

### Ongoing Learning

Memoria learns without asking:
- Your commit patterns → knows your "peak productivity" windows
- Which Memory Bank sections you open most → knows your focus areas
- Which nudges you dismiss → stops surfacing that type
- Which nudges you act on → surfaces more of that type

---

## Personal Brain Feature Roadmap

| Feature | Priority | Notes |
|---------|----------|-------|
| Personalised morning brief | **P0** | Core experience |
| Personal interest integration (cricket scores, news) | P1 | Via RSS/API connectors |
| "Where I left off" context restoration | **P0** | Session state + open files |
| Personal goals tracking | P1 | Explicit input + progress nudges |
| Work/personal mode separation | **P0** | Privacy requirement |
| Suggested focus blocks based on patterns | P2 | Needs 4 weeks of data |
| Personal project Memory Banks | **P0** | Same as work, different partition |

---

## Relationship to Company Brain

The Personal Brain is the individual-facing layer. For users at a company, their Personal Brain is powered by:
- Their own work history and patterns
- The shared Company Brain for organisational context (team health, company knowledge graph)
- Their personal preferences and interests (never shared with the company)

A developer using Memoria at work gets both: "Your team's payments service has a known issue with UUID migrations" (company brain) AND "You've fixed this kind of bug 6 times before — here's your fastest approach" (personal brain).
