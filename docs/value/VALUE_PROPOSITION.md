# Value Proposition — Memoria Business

> Who this is for, what problem it solves, and why it's different. The pitch, grounded in specifics.

---

## The Pitch

**"Your engineers ship features end-to-end, without waiting for other roles."**

A backend developer picks up a frontend ticket. There's no Figma spec. Normally: wait 2 days for the designer. With Memoria Business: the system detects the missing spec, generates a component skeleton from the existing design system and ticket requirements, asks the developer to review in 2 minutes, and then generates the code — following the actual patterns in the codebase.

That's the core behavior. The company knowledge that used to live in designers' heads, in tribal Slack threads, and in the tacit understanding of senior engineers — is structured, current, and usable by the AI. The AI doesn't have to guess how your codebase works. It read the Memory Banks first.

---

## Who Buys This

**Primary buyer:** Engineering manager or VP Engineering at a 20–150 person startup or scale-up where:
- Teams are small and roles blur (backend devs write frontend; QA engineers write code)
- Onboarding new engineers takes 6–8 weeks because context is scattered
- Sprint velocity is blocked by "waiting on Figma" or "waiting on API spec"
- AI tools exist but confidently produce wrong outputs because they don't know the codebase

**The buying moment:** When an EM calculates how much engineering time is lost to context hunting, waiting on other roles, and re-learning what was documented in a Jira comment from 2 years ago — and decides that automating that layer is worth a monthly seat fee.

**What's NOT the audience (yet):**
- Enterprises with mature design systems, dedicated QA teams, and separated role workflows — they already have solutions for this
- Solo developers — the value is collaborative; single-person teams don't have the cross-role friction
- Non-engineering teams — the initial product is code-first; other departments come after the engineering workflow is proven

---

## The Three Problems

### Problem 1: Knowledge is scattered and dies

It lives in 3-year-old PR comments, Slack threads nobody can find, and the head of the engineer who just quit. New hires take 6–8 weeks to be productive. The same incidents repeat. Runbooks go stale the moment they're written.

**What Memoria Business does:** Generates hierarchical Memory Banks from the codebase — one per module, one per test directory, connected in a spider web graph. When a new engineer opens the `auth` module, the Memory Bank explains what it does, what depends on it, the known gotchas, and the architectural decisions. Not documentation someone wrote once and forgot to update — knowledge extracted directly from the code and updated when the code changes.

### Problem 2: Tools don't talk to each other

A Jira ticket has no idea what the codebase looks like. A Figma spec doesn't know what the API contract is. An engineer working a ticket pulls context from four different tools before writing a line of code.

**What Memoria Business does:** Connects to every tool via MCP (same config format as Claude Desktop), assembles the context automatically on ticket open — relevant Memory Banks, live Jira details, linked Figma frame, recent PRs — and presents it in one panel. No manual searching.

### Problem 3: AI agents hallucinate company knowledge

Internal automation confidently invents refund policies, escalation paths, and deployment procedures. The general-purpose AI is not the problem — the missing layer is structured, current, company-specific knowledge.

**What Memoria Business does:** Memory Banks become Skills Files via `memoria skills`. Skills Files (Anthropic Agent Skills format, adopted by OpenAI, GitHub, and Atlassian in 2025) give AI agents structured, verifiable domain knowledge. An agent built against a verified SKILL.md for a company's deployment procedure doesn't invent steps — it follows the actual runbook.

---

## The Minimum Viable "Aha Moment"

The system must deliver value with only a repo URL and an API key — before MCP connections are configured. The entry-level experience:

1. Enter API key → test connection (green badge)
2. Paste repo path → 30-second analysis → 8 Memory Banks created
3. Paste any ticket description as text → system returns: which modules are affected, what might break, a code skeleton that matches the actual file structure

This works with zero Jira/GitHub/Figma setup. No sprint tickets required. No MCP configuration. A developer evaluating the tool gets this value in the first 10 minutes, on their own, with their own codebase.

MCP connections amplify value (live tickets, PR data, design specs) but must not be a prerequisite for the first moment of value. Requiring full MCP setup before delivering any value is the fastest path to losing an evaluating engineer.

---

## What Makes It Different

### Competitors

| Tool | What it does | What it can't do |
|---|---|---|
| **Glean** ($7.2B) | Indexes everything; finds anything | No code understanding. No process structure. Every query cold-reads. Cannot act. |
| **Notion AI / Confluence AI** | Q&A over documents | Factual retrieval only. No "what breaks if I change this?" No cross-tool context. |
| **GitHub Copilot** | Line-level code completion | Doesn't know your architecture. Doesn't connect to Jira. Can't detect a missing design spec. |
| **Linear / Jira AI** | Ticket summaries, sprint analytics | Knows tickets; doesn't know the codebase. No Memory Banks. |
| **General LLMs (Claude, GPT-4o)** | Answer any question | No company-specific knowledge. Hallucinate your internal procedures. |

### Memoria Business differentiators

**1. Assembled, not searched.** The ticket opens and the context is already there — Memory Banks, live MCP data, affected modules from the graph. You don't query it; it finds you.

**2. Role-free adaptive agent.** Any team member can work any ticket. The system detects what's missing (design? tests? implementation?) and fills the gap using knowledge from your actual codebase — not generic patterns. A backend dev can pick up a frontend ticket and get a component skeleton that matches your existing component library.

**3. Hierarchical, module-level knowledge.** Not one flat dump for a 1000-file repo. Auth module, payments module, test coverage — each with its own Memory Bank, connected in a spider web. The blast-radius question ("what breaks if I change auth?") is answered in seconds from the intra-project graph.

**4. It takes actions.** When the work is done: branch created, PR opened, ticket updated — one click, user approves each step. The AI handles the ceremony; the engineer handles the thinking.

**5. Process knowledge becomes executable.** `memoria interview` captures tacit knowledge from experts. `memoria skills` converts Memory Banks to SKILL.md files. AI agents built on verified SKILL.md files automate company operations correctly — not from generic guesses.

---

## The Retrieval Quality Risk (Acknowledged)

The PM review identified the single biggest risk: retrieval quality. If the Memory Banks don't accurately represent the codebase, or if the retrieval system returns the wrong modules for a ticket, the generated artifacts will be wrong — and engineers will stop trusting the system immediately.

This is the correct risk to call out. It is mitigated by:

1. **Module-level granularity.** A Memory Bank for `src/auth/` is far more likely to return correctly for a ticket about "fix login timeout" than a flat project-level dump. Smaller, focused units improve precision.

2. **Explicit quality feedback in the UI.** Every retrieval result shows "Was this context helpful?" (thumbs up/down). This data drives tuning — not assumption.

3. **Honest degradation.** When retrieval quality is low (scores below threshold), the system says so: "No closely matching Memory Banks found. Generated artifacts may not match your codebase patterns." It does not pretend confidence it doesn't have.

4. **The Memory Banks are readable.** Developers can open any Memory Bank and see exactly what the system knows about a module. If it's wrong, they can trigger a re-analysis. The knowledge is transparent and correctable — not a black box.

---

## Success Metrics (6 Months)

| Metric | Target |
|---|---|
| Time to first code from ticket open | < 15 minutes (from 2 hours of context hunting) |
| New engineer productive sprint | 2 weeks (from 6–8 weeks) |
| Blast-radius query latency | < 8 seconds for 10-sub-unit project |
| Context assembly acceptance rate | > 85% of retrieved Memory Banks rated "helpful" by developer |
| Teams using it | 50 engineering teams |
| Monthly churn | < 5% |
