# Memoria for UI/UX Designers

> Status: SDLC Phase 1 — Figma MCP integration is the key differentiator

---

## Morning Brief

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  YOUR FOCUS TODAY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • DESIGN-45 (Checkout Redesign) is blocking frontend sprint starting tomorrow
    Missing: button disabled + error states for the primary CTA

  • DESIGN-38 (Notification Center): PM left a Figma comment at 11:43pm
    "stakeholder wants a count badge variant we didn't spec" — new scope,
    not discussed in any meeting transcript

  • Your "Card / Product Tile v2" was published to the design system by a colleague
    2 of your in-progress screens still reference the old v1 component

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WAITING ON OTHERS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • Content team: final microcopy for Order History empty state (11 days, due today)
  • Legal: cookie consent modal copy review (6 days, blocks mobile web launch)
```

---

## Figma MCP Workflow

The key differentiator: Memoria → Figma MCP closes the full loop from *knowing the constraint* to *generating the compliant artifact* to *detecting implementation drift*.

### Stage 1: Context Assembly (before Figma is touched)

When the designer says "start the checkout redesign for DESIGN-45", Memoria builds a pre-flight context bundle:

```
From Jira Memory Bank:
  ✓ Acceptance criteria + linked mockup references
  ✓ Slack threads tagged to this ticket
  ✓ Meeting notes: "stakeholder: no multi-step modals, keep it single page"

From Figma Memory Bank:
  ✓ Design system tokens (colors, typography, spacing)
  ✓ Component library (available components + variants + usage rules)
  ✓ Prior checkout flow explorations (v1 from 2 years ago — what was kept/rejected/why)
  ✓ Most recent similar screens (onboarding, payment) for consistency reference

From Brand Guidelines Memory Bank:
  ✓ Platform constraints: "no bottom sheets on web", "touch targets min 44px"
  ✓ Stakeholder preferences logged from past reviews
```

Memoria surfaces a pre-flight summary before generating anything:

```
Ready to start DESIGN-45. Here's what I know:

Design system: Polaris v2.1 (updated 3 weeks ago)
Available: Button (5 variants), Input Field (6 states), Progress Indicator (step)
Constraints: single-page layout, no modal overlays (from ticket)
From March 3 review: step indicator must remain visible on scroll
Stakeholder note (Slack, March 7 — Sarah): "the back button caused support
  tickets in old flow, de-emphasize it"

Proceed with these constraints?  [Yes]  [Edit]
```

### Stage 2: Generation

Memoria calls the Figma MCP to:
1. Read live token values from the design system file (not cached)
2. Get component IDs from the library (so they stay linked, not detached copies)
3. Create a frame in the designer's working file with the correct grid/dimensions
4. Place components using their library IDs with tokens applied by name (not hex value)
5. Add a notes layer documenting which constraints were applied and their sources

Output: a structurally correct, token-compliant, component-linked scaffold. The designer refines it — Memoria handles the mechanical fidelity.

### Stage 3: Iteration with Context

"Make the CTA button bigger" → Memoria checks: does a Large variant exist in the design system? If yes, swaps the variant. If no: "The design system doesn't have a Large CTA variant — should I create a custom size or flag this to the design system team?"

"Use the existing color tokens for error states" → Memoria pulls exact token names (`$color-feedback-error-default`, `$color-feedback-error-subtle`) and applies them by name, not hex value.

---

## Proactive Nudges

1. **Implementation drift detection** — "The checkout button you designed in DESIGN-45 was implemented in production yesterday. 3 divergences: (1) border-radius 6px vs your spec 8px, (2) hover color using raw hex instead of `$color-primary-700` token, (3) disabled opacity 0.4 vs your 0.5. File a bug or update spec?"

2. **Design system component update** — "A component in 7 of your active screens was updated in the design system 2 days ago. The image aspect ratio changed from 4:3 to 16:9. Affects your Checkout Step 1 layout."

3. **Undocumented constraint at risk** — "You're starting the mobile navigation redesign. In a Slack thread from August, the CEO said 'no hamburger menus — we tried it in 2022 and it killed engagement.' This was never written into a spec. Add it as a constraint to DESIGN-61 before you start?"

4. **Stakeholder constraint not yet reflected** — "Yesterday's product review decided guest checkout must be the *primary* path, not secondary. Your current Figma exploration treats it as secondary. Revise spec or flag discrepancy to the PM?"

5. **Research finding you haven't applied** — "You're designing the Order History empty state. The Q3 usability study found users abandon empty states without a CTA at 67%. Your current design has no CTA. Relevant research: 'Q3 Usability Study — Session Recordings Analysis'."

---

## Connectors (ranked)

| Tier | Connector | Why |
|------|-----------|-----|
| 1 | Figma (+ MCP) | Primary workspace; read AND write |
| 1 | Jira/Linear | Source of work — every ticket, criteria, history |
| 1 | Slack | Where real design decisions live |
| 2 | Meeting recordings | Design reviews, stakeholder sessions |
| 2 | Confluence/Notion | Brand guidelines, design system docs |
| 2 | GitHub | What actually got built (implementation drift detection) |
| 3 | User research repos | Connect design decisions to evidence |
| 3 | Dev handoff comments | Where engineers noted spec questions |
| 3 | Analytics (Mixpanel, Amplitude) | Did past designs work? |

---

## Questions Designers Ask Memoria

1. "What were the constraints agreed on in last Tuesday's design review for the mobile checkout?"
2. "What color tokens does the current design system define for error and warning states?"
3. "Has anyone already designed a guest checkout flow? Show me what we built before and why we moved away."
4. "What did Sarah say about the back button on checkout? I think there was a Slack thread."
5. "Which components in my current Figma draft are using detached instances instead of linked library components?"
6. "What accessibility requirements from the last audit still apply to forms?"
7. "Has the content team delivered final copy for the Order History empty state?"
8. "What's the difference between how I specced the notification badge and how it was implemented in React?"
9. "I'm starting the settings page redesign — what are all the constraints, decisions, and stakeholder preferences I should know?"
10. "What did the Q3 user research find about how users navigate the account section?"

---

## Tribal Knowledge Capture

**What designers lose when someone leaves:**
- Why a navigation pattern was chosen over alternatives
- Platform-specific constraints discovered through pain ("modals on mobile cause keyboard issues because of our React Native scroll view setup")
- Stakeholder aesthetic preferences ("CEO dislikes dense tables — use cards for executive-facing views")
- Engineering constraints affecting design ("our animation library can't do spring physics")
- Historical failures ("we tested a bottom nav bar in 2021, engagement dropped, we reverted — see Amplitude report")
- Legal edge cases ("you cannot pre-check consent checkboxes under GDPR — legal flagged this in the April review")

**How Memoria captures it:**
- Ingests Slack threads and meeting transcripts, extracting "decisions" and "constraints" as named entities with dates and owners
- Builds a structured **Design Decisions Log** per project
- Active layer: when Memoria detects a potential implicit constraint in a thread, prompts: "Do you want me to formally add this to the project Memory Bank as a constraint?" One click, not a form.
- The proactive nudge closes the loop: when a designer starts work on a screen with prior decisions attached, Memoria surfaces them *unprompted* before the mistake is made.
