# Memoria for Product Managers

> Status: SDLC Phase 1

---

## Morning Brief

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WHAT SHIPPED YESTERDAY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • Payments v2 alpha deployed to staging (3 linked tickets closed)
  • 2 bug fixes merged to main, no regressions in CI

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BLOCKED RIGHT NOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • PROJ-412 blocked 6 days — waiting on legal sign-off
  • PROJ-389 no reviewer activity in 4 days (hidden blocker)
  • Slack thread (2 hrs ago): "can't proceed until PM clarifies scope" — @you

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  DECISIONS NEEDED FROM YOU
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • 3 Slack @-mentions with no reply from you
  • 2 Notion comment threads open on the Q3 PRD
  • Action item from Tuesday's roadmap meeting: "PM to confirm API scope by EOW"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SPRINT HEALTH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Committed: 42 pts | Completed: 18 pts | Remaining: 24 pts | Days left: 5
  ⚠️ On track to complete ~28 pts. 3 large unstarted tickets (14 pts) have no assignee.
```

---

## Contradiction Detection

The PM is the primary consumer of Memoria's contradiction engine:

| Contradiction | Sources | Resolution needed |
|---------------|---------|-------------------|
| Ship date conflict | Q3 roadmap: Aug 15 ↔ Slack thread: "blocked until September" | PM + Tech Lead |
| Acceptance criteria vs. design | Jira: "email required at signup" ↔ Figma mockup: "optional flow" | PM + Designer |
| Policy conflict | Handbook: refund limit $20 ↔ Slack #support: "$50 for Enterprise" | Head of Support |
| OKR vs. sprint | Q2 OKR: reduce checkout drop-off 20% ↔ sprint backlog: zero checkout tickets | PM |
| Shipped feature vs. stale spec | Spec: "offline mode supported" ↔ PR #847: "removed, deferring indefinitely" | PM to update spec |

---

## Proactive Nudges

1. **Sprint commitment drift warning (day 3 of sprint)** — "Committed 42 pts. Based on current merge rate, on track for 28. 3 unstarted tickets unassigned. 7 days left. De-scope now or accept the miss?"

2. **Stakeholder alignment gap before key meeting** — "You have a roadmap review with the VP in 2 days. Her last comment was 6 weeks ago, flagging the API monetisation timeline. That timeline has changed. She hasn't been @-mentioned in any update since. Consider a pre-brief."

3. **Repeat customer complaint pattern** — "7 Zendesk tickets in 10 days mention 'export fails' or 'can't download CSV.' No open bug in Jira. Engineering hasn't been notified. Possible unlogged regression."

4. **Knowledge silo risk** — "Marcus has been the sole contributor to the payments integration for 4 months, with no documentation and no PR reviewers. If he leaves, this is a knowledge cliff."

5. **Decision without a record** — "Tuesday's standup agreed to drop Android support for v2.0. No Jira ticket, Notion doc, or Slack pin captures this. Undocumented decisions cause rework when new people join."

---

## Connectors (ranked)

| Tier | Connector | Why |
|------|-----------|-----|
| 1 | Jira/Linear | Ground truth for sprint state, blockers, velocity |
| 1 | Slack | Where 80% of real decisions happen |
| 1 | Confluence/Notion | Specs, PRDs, OKRs — contradiction goldmine |
| 1 | Meeting recordings | Verbal decisions that never get written down |
| 2 | GitHub | PR velocity, deploy frequency — engineering health signal PMs never see |
| 2 | Zendesk/Intercom | Customer pain → missing Jira ticket detection |
| 2 | Calendar (Google/Outlook) | Contextualise brief, detect pre-meeting prep gaps |
| 3 | Analytics (Mixpanel, Amplitude) | Connect product decisions to outcome data |
| 3 | Google Docs/Slides | Sales decks, board materials, exec approvals |
| 3 | Figma comments | Design-spec contradictions |

---

## Questions PMs Ask Memoria

1. "What was the original reason we decided not to build a native mobile app and go PWA instead? Who made that call and when?"
2. "Which engineering decisions made in H1 are now showing up as friction in the payments team's sprint velocity?"
3. "Has the team ever scoped offline mode before? What happened and why was it dropped?"
4. "What did we commit to Acme Corp in terms of SSO support, and is it on the roadmap?"
5. "What is the current consensus on our data retention policy across all sources?"
6. "Which features shipped in Q1 generated the most support tickets in the 30 days after launch?"
7. "Who on the team has the most context on the notification system, and where is it documented?"
8. "What were the open questions from Q2 planning that we said we'd resolve but never formally closed?"
9. "What has engineering said about the technical debt in the auth module, and how old is that debt?"
10. "Last time we did a major navigation redesign, what was the user feedback and did we revert anything?"

---

## Tribal Knowledge Capture

**What PMs know that isn't written down:**
- Why a feature was descoped ("we can do it later" — and that later never gets calendared)
- Informal agreements with customers/partners that aren't in any contract
- The real reason a previous initiative failed (never written honestly in post-mortems)
- The stakeholder map: who's actually a silent blocker, whose informal opinion carries more weight than their title
- Historical failure modes of the product area

**How Memoria captures it:**
- **Decision threading**: when a feature is created in Jira, Memoria pulls Slack threads and meeting segments from the same time window and attaches them as "decision context"
- **Departure capture**: when a PM signals offboarding, Memoria generates a knowledge transfer interview — structured questions derived from everything it knows is undocumented about their project area
- **Rationale tagging**: messages containing "we decided not to," "we chose X because," "the reason we're not" are flagged and stored as rationale records
- **Contradiction-as-documentation**: resolving a contradiction forces the "why" to be re-articulated and permanently recorded
