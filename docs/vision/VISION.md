# Memoria Business — Vision

> The "why it matters" behind the roadmap.
> The actual task list (what to build, in what order) lives in [ROADMAP.md](../../ROADMAP.md).

---

## The Problem in One Paragraph

Engineering teams lose enormous amounts of time every day to knowledge friction — a new engineer spends 6 weeks becoming productive because no one documented how the auth module works; a developer spends 2 hours in Slack archaeology before writing their first line of code on a ticket; an AI automation agent confidently invents the wrong refund policy because no one gave it grounded, company-specific knowledge. The tools that are supposed to help — Jira, Figma, GitHub, Slack — don't talk to each other. A ticket has no idea what the codebase looks like. A spec doesn't know what the API contract is. Engineers manually bridge these gaps every day, and the AI agents meant to replace that manual work can't operate in this environment because they lack structured domain knowledge.

---

## What Memoria Business Is

A **role-aware AI work companion** for engineering teams. It combines two layers:

**Memory Banks** — structured, hierarchical knowledge extracted from the codebase, docs, and history. The "brain" that knows how your systems work: what the auth module does, what breaks if you change it, how payments is structured, what the gotchas are in the API layer.

**MCP Connections** — live connections to the tools your team already uses: Jira, GitHub, Figma, Slack. The "hands" that know what's happening right now: which tickets are in sprint, which PRs are open, what the linked Figma spec says.

When a developer opens the app in the morning, they see their sprint tickets — and the app already knows the codebase context, the design specs, the architectural dependencies. They don't search for information. They work *with* the AI, not *at* it.

---

## The Three Problems It Solves

### 1. Knowledge is scattered and dies

Critical know-how lives in 3-year-old PR comments, Slack threads nobody can find, and the head of the engineer who just quit. New hires take 6–8 weeks to be productive. The same incidents repeat. Runbooks go stale the moment they're written.

Memoria Business fixes this by making the codebase itself the source of truth — generating hierarchical Memory Banks that cover every module, keeping them current automatically, and structuring them so AI agents can act on them, not just search them.

### 2. Tools don't talk to each other

A Jira ticket has no idea what the codebase looks like. A Figma spec doesn't know what the API contract is. An engineer working a ticket has to manually pull context from four different tools before they can write a single line of code.

Memoria Business assembles this context automatically — connecting to each tool via MCP, pulling the relevant data on demand, and presenting it in one panel before the developer even starts typing.

### 3. AI agents hallucinate company knowledge

Internal automation confidently invents refund policies, escalation paths, and deployment procedures — because nobody gave the agent grounded, company-specific knowledge. General-purpose AI is not the bottleneck. Domain knowledge is.

Memory Banks are the missing layer. Every module has a structured, current, machine-readable description of how it works. Skills Files (generated from Memory Banks) give AI agents structured, actionable process knowledge in the `SKILL.md` format. An agent running against verified Knowledge Files does not need to guess.

---

## The Developer Workflow (The Aha Moment)

This is the experience Memoria Business is built around:

| Moment | What the app does |
|---|---|
| Monday morning | Shows sprint tickets with codebase context already loaded — no Slack archaeology |
| Before a refactor | "What breaks if I change auth?" → blast-radius from the Knowledge Graph, specific sub-Memory Banks highlighted |
| Working a ticket | Figma spec + relevant Memory Bank sections + AI suggestions in one panel |
| Ready to ship | One-click: create branch → create PR with auto description → update Jira status |

The shift in experience is from **context switching** to **staying in flow**. A developer currently spends the first 2 hours of their day piecing together what they need to work. Memoria Business assembles that context before they ask for it.

---

## How It Works — The Core Loop

```
1. CONNECT
   User pastes MCP JSON config (same format as Claude Desktop)
   App connects to: Jira, GitHub, Figma, Slack, etc.
   No crawling. Tools called on-demand when screens load.

        ↓

2. ANALYZE (one-time, then keeps current)
   Hierarchical Memory Bank generation:
   → Discover sub-structure (package boundaries + size cutoff >50 files)
   → Process each sub-unit in PARALLEL (10x faster than sequential)
   → Generate sub-Memory Bank per module (auth, payments, tests/auth, etc.)
   → Infer connections between sub-banks (test↔source, api→module)
   → Generate root summary from all sub-banks
   → Result: spider web of interconnected knowledge

        ↓

3. ASSEMBLE CONTEXT (on every work item open)
   Ticket opens → app assembles:
   - Relevant sub-Memory Banks (which modules does this ticket touch?)
   - Live data from MCPs (ticket details, linked Figma frame, recent PRs)
   - Connection graph (what else is affected?)
   All in one panel. No searching.

        ↓

4. WORK TOGETHER
   Developer + AI work through the ticket collaboratively.
   Session is ephemeral — no chat history bloat.
   Key decisions auto-saved to the ticket's session log.
   Developer approves each action before it executes.

        ↓

5. ACT
   One-click actions via MCP tools (user approves each):
   → GitHub MCP: create_branch, create_pull_request
   → Jira MCP: update_issue status, add_comment
   → Slack MCP: post_message to team channel
```

---

## Hierarchical Memory Banks — Why They Matter

One flat Memory Bank for a 1000-class Java project is useless. Nothing fits in LLM context. Navigation is impossible. You can't answer "what does the auth module do?" from a document that also contains payments, notifications, and infrastructure detail.

The solution is intelligent sub-structuring:

```
Project Root
├── Structure Discovery Pass
│   Rule 1: Package boundary files always split
│            (package.json, setup.py, pom.xml, __init__.py, build.gradle)
│   Rule 2: Test dirs always split
│            (test/, tests/, __tests__, spec/ + *Test.java, *_test.py, *.spec.ts)
│   Rule 3: >50 files in a dir → subdivide further
│   Rule 4: Apply recursively until each unit is manageable
│
├── Parallel Processing (all sub-units simultaneously)
│   [src/auth/]      → Worker 1 → Auth Memory Bank
│   [src/payments/]  → Worker 2 → Payments Memory Bank
│   [src/api/]       → Worker 3 → API Memory Bank
│   [tests/auth/]    → Worker 4 → Auth Tests Memory Bank
│   [tests/payments/]→ Worker 5 → Payments Tests Memory Bank
│
├── Connection Inference (spider web)
│   Static (fast): naming conventions, import statements
│   LLM (semantic): "are these two modules in the same domain?"
│   Edge types: tests, depends_on, calls_api, implements, shares_types
│
└── Root Summary (generated last, from sub-banks + connections)
    "Here's the whole project. Here are its sub-systems. Here's how they connect."
```

The dashboard shows this as a spider web graph. Click a node → sub-Memory Bank text (A view). Toggle → file tree (B view).

This means:
- When a ticket touches auth, we know to load the Auth Memory Bank, the Auth Tests Memory Bank, and the API Layer Memory Bank — not the whole project
- When a developer asks "what breaks if I change auth?", we traverse the graph and return specific sub-banks
- When a new engineer is onboarding, they can navigate the graph and understand the codebase module by module

---

## Why Context Is Assembled, Not Searched

The core difference between Memoria Business and a documentation search tool:

**Search approach:** The developer opens a ticket, then manually searches for relevant context in four different places — Jira for the full ticket, Figma for the spec, GitHub for recent PRs, Confluence for the relevant Architecture Decision Record. This is what every other tool requires.

**Assembled approach:** The ticket opens. Memoria Business calls the Jira MCP, the Figma MCP, and the GitHub MCP simultaneously. It maps the ticket description to relevant sub-Memory Banks. It traverses the graph to find connected modules. It returns all of this assembled and ranked in a single panel, before the developer types anything.

The developer does not search. The context finds them.

---

## The Company Brain Angle

Memoria Business is also the missing layer between raw company data and reliable AI automation.

Every company has critical know-how scattered everywhere — in people's heads, old email threads, Slack conversations, support tickets, ADRs, runbooks. AI agents can't operate on scattered knowledge. They need structured, current, executable process knowledge.

Memory Banks provide this. The `memoria skills` command converts Memory Banks into `SKILL.md` files — the Anthropic Agent Skills open standard, adopted by OpenAI, GitHub, Atlassian, and Figma within 90 days of publication. An AI agent running against a verified SKILL.md for a company's refund process doesn't need to guess. It follows the actual procedure.

The path: Memory Banks → verified process knowledge → Skills Files → AI agents that can automate company operations correctly and consistently.

---

## Future Roles — Same Architecture, Different MCPs

The developer workflow is Phase 1. The same architecture generalises to every role in an engineering organisation:

| Role | MCPs on load | Context assembled |
|---|---|---|
| **QA** | Jira, GitHub, PagerDuty | Test tickets, failing test context, incident history, coverage gaps |
| **Designer** | Figma, Linear, Notion | Active design files, component inventory, design token usage, which screens touch which APIs |
| **Engineering Manager** | Jira, GitHub | Sprint health, PR review queue, knowledge gaps, risk before a deploy |

And beyond engineering:
- **Finance / Marketing / Legal** — same architecture, different MCPs, different workflow patterns

The company brain becomes the shared foundation. Different roles see the view relevant to them. The same Memory Banks, the same graph, the same MCP infrastructure — tuned per role.

---

## What Makes This Different

### 1. Context is assembled, not searched
You don't search for information about a ticket. The app assembles it for you — Memory Banks + live tool data — before you even start typing.

### 2. Hierarchical, parallel knowledge
Large repos are understood at the right granularity. Auth module, payments module, test coverage — each with its own Memory Bank, connected in a spider web. Not one flat dump.

### 3. It takes actions, not just answers
When the work is done, one click: branch created, PR opened, ticket updated. The AI handles the ceremony. The developer handles the thinking.

### 4. The company brain is the missing layer
Memory Banks become Skills Files. Skills Files give AI agents structured domain knowledge. The result: AI automation that works on the first attempt because it was built against verified company knowledge — not generic guesses.

---

## What Success Looks Like

- A developer opens the app at 9am. Sprint tickets are there. Context is loaded. First line of code in under 15 minutes — not 2 hours of Slack archaeology.
- A Staff Engineer asks "what breaks if I change the auth middleware?" and gets a spider-web blast-radius diagram with specific sub-Memory Banks highlighted — in 8 seconds.
- A new engineer is productive in 2 weeks, not 8, because every module they touch has a Memory Bank.
- An internal AI agent follows a deployment runbook correctly on the first attempt because it was built against verified SKILL.md files.
- 50 engineering teams using it — churn under 5% monthly.
