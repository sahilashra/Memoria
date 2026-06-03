# Intelligence Engine — LLM Design Prompts

> ✅ **Decisions resolved** — architecture decisions for all 5 pillars were made
> via Memoria-specific sub-agent brainstorming (see each PILLAR_*.md for results).
> These prompts are retained as reference context. Summary of decisions:
> - **D1:** Memoria feature (new modules + CLI commands), not separate product
> - **D2:** Local forever (`sync_allowed: false`), no cloud path in v1
> - **D3:** Windows + macOS from day one, Linux later
> - **D4:** Memoria Workflow YAML + Python subprocess orchestrator
> - **D5:** Accessibility API primary, `llava:7b-v1.6-Q4_K_M` (Ollama) as fallback

---

## Prompt 1 — Scope: Memoria Feature vs. Standalone Product

```
You are a software architect helping design an AI-powered workflow intelligence system.

CONTEXT — WHAT MEMORIA IS:
Memoria is a local-first, open-source Python CLI + browser UI tool that:
- Crawls code repos, documents, meeting recordings, and wiki exports
- Generates structured "Memory Banks" (Markdown summaries) per project
- Provides semantic search across all Memory Banks via ChromaDB
- Runs a FastAPI server with a browser UI for non-technical users (PMs, executives)
- Supports 20+ AI providers via LiteLLM (Claude, GPT-4, Gemini, Bedrock, Ollama)
- Is fully local — all data in ~/.memoria/, nothing goes to cloud unless user exports

CURRENT INTELLIGENCE CAPABILITIES (already built):
1. companion.py — floating OS overlay; reads active window title + clipboard;
   semantic search against Memory Banks; surfaces relevant snippets; tkinter UI.
2. tracker.py — passive file-change + git-commit daemon; SQLite activity.db;
   records what repos/files are touched.
3. learner.py — watches git commits; uses LLM to label semantic work units;
   confidence-gated Memory Bank draft updates.
4. observer.py — watchdog-based auto-draft daemon; triggers Memory Bank
   updates when files change significantly.

THE PLAN (what we want to build next):
A 5-pillar Intelligence Engine that:
- Pillar 1: Captures OS-level inputs (keyboard, mouse, screenshots, accessibility API)
- Pillar 2: Segments raw events into clean task sessions; scrubs PII
- Pillar 3: Uses VLM + sequence mining to identify repeating workflow patterns
- Pillar 4: Generates automation scripts (e.g. Playwright) for repetitive workflows
- Pillar 5: Local-first privacy model, user-in-the-loop consent

THE QUESTION:
Should the Intelligence Engine be built as:
(A) A NEW SET OF MODULES inside the existing Memoria codebase (new CLI commands
    like `memoria intelligence start`, new FastAPI routes, new tab in browser UI).
    Pro: shares the existing Memory Bank context, one install, consistent UX.
    Con: Memoria's core value prop is documentation — telemetry/automation is a
    different use case that might confuse existing users.

(B) A SEPARATE STANDALONE AGENT PRODUCT that installs separately, has its own
    CLI and UI, but connects to Memoria as a "knowledge backend" (reads Memory
    Banks, uses Memoria's semantic search API).
    Pro: cleaner separation of concerns, can be open-sourced independently,
    users who only want documentation don't get the surveillance module.
    Con: more maintenance, two install steps, harder to cross-reference Memory Banks.

(C) A PLUGIN / EXTENSION ARCHITECTURE where Memoria gains a plugin system and
    the Intelligence Engine is an optional plugin (installed via
    `memoria plugin install intelligence`).

Please recommend the best architectural approach and explain:
1. Which option you recommend and why
2. What the entry-point CLI design should look like (commands, flags)
3. How the Intelligence Engine should reference/use existing Memory Banks
4. Any packaging or distribution considerations
5. Whether a separate database or shared ~/.memoria/ is better for the new telemetry data

Format your answer as: Recommendation, Rationale, CLI Design, Data Layer Design, Risks.
```

---

## Prompt 2 — Privacy Model: Local-Forever vs. Opt-In Team Sync

```
You are a privacy architect designing the data residency model for an AI
workflow intelligence system built on top of an existing local-first tool.

CONTEXT:
Memoria is a local-first Python tool that generates "Memory Banks" (structured
Markdown summaries) for software projects. It runs 100% locally — all data in
~/.memoria/. Nothing leaves the machine unless the user explicitly runs
`memoria track --export`.

We are building an Intelligence Engine that captures OS-level user behaviour
(active windows, clipboard, eventually keystrokes + screenshots) to detect
repeating workflows and generate automation scripts. The captured data includes:
- Window titles and app names
- Clipboard content (text only)
- File paths touched
- Git commit messages
- (Future) Keystrokes and mouse events
- (Future) Screenshots

THE QUESTION:
We need to decide the privacy model for captured telemetry data:

OPTION A — FULLY LOCAL, FOREVER
All data stays in ~/.memoria/ always. No sync path exists at all. Team-level
pattern sharing is explicitly out of scope. Users can only export via
`memoria intelligence export` (produces a local JSON file they can share
manually).
Pro: Simplest trust model. Zero risk of accidental data leak. Clear user expectation.
Con: Can't surface team-level insights ("3 engineers all do the same CRM data
entry manually — one automation helps everyone").

OPTION B — LOCAL BY DEFAULT, EXPLICIT OPT-IN TEAM SYNC
Data is local by default. An opt-in `memoria intelligence sync --team` command
lets users share their anonymised workflow patterns (not raw events, only
clustered pattern schemas) with a team server or shared directory. Raw events
and PII-masked text never leave the machine — only the abstract workflow schema
JSON is shared.
Pro: Enables team-level pattern discovery. Privacy preserved (only schemas shared).
Con: Adds a sync layer. Shared server becomes a new attack surface.

OPTION C — TIERED CONSENT (per data type)
Users independently opt into sharing for each data type:
- Window activity: local only
- Workflow patterns (abstract): opt-in team share
- Automation scripts: opt-in team share (so one person's generated script
  helps colleagues with the same workflow)
Pro: Maximum user control.
Con: Complex consent UI. Higher engineering effort.

Please recommend which model to use and provide:
1. Recommended option and rationale
2. Exact data types that stay local in all cases (non-negotiable)
3. What (if anything) can be shared and in what form
4. How to implement the consent flow (CLI prompts? UI? config.yaml?)
5. How to handle team scenarios where a sysadmin wants to deploy Memoria
   Intelligence across 50 developer machines
6. GDPR / CCPA considerations for a tool handling potential PII on corporate machines

Format your answer as: Recommendation, Non-Negotiable Local Data, Shareable Data,
Consent Flow Design, Enterprise Deployment, Regulatory Notes.
```

---

## Prompt 3 — Platform Strategy: Windows-First vs. Cross-Platform from Day One

```
You are a platform engineering architect helping decide cross-platform strategy
for a Python-based OS-level telemetry system.

CONTEXT:
Memoria is a Python CLI + FastAPI browser UI tool that already runs on
Windows, macOS, and Linux. Its existing OS-level components are:

companion.py — reads active window title:
  - Windows: ctypes.windll.user32.GetForegroundWindow()
  - macOS: AppKit.NSWorkspace.sharedWorkspace().activeApplication()
  - Linux: xdotool subprocess call

tracker.py — file system events via watchdog (cross-platform)
tracker.py — git commit polling via subprocess (cross-platform)
companion.py — clipboard via pyperclip (cross-platform)

We want to ADD the following OS-level capabilities:
1. Global keyboard hooks (low-level input capture)
2. Mouse coordinate and click capture
3. High-frequency screenshots (1fps or on-event)
4. Accessibility API integration (semantic UI element extraction)

The platform-specific libraries are:
KEYBOARD HOOKS:
  - Windows: pyhook/pyWinhook, or ctypes SetWindowsHookEx
  - macOS: Quartz.CGEventTap (requires Accessibility permission)
  - Linux: python-evdev (raw input device access, requires udev rules)

SCREENSHOTS:
  - All platforms: mss + pillow (fast, cross-platform)

ACCESSIBILITY API:
  - Windows: pywinauto (UI Automation COM interface)
  - macOS: pyobjc AXUIElement / Quartz Accessibility API
  - Linux: AT-SPI via pyatspi (inconsistent across distros)

THE QUESTION:
Should we:
(A) BUILD FOR WINDOWS FIRST, then extend to macOS and Linux later
    Pro: Highest enterprise penetration (where this tool is most valuable).
    Most corporate machines are Windows. Fastest to ship.
    Con: Mac-heavy engineering teams won't be able to use it.

(B) BUILD CROSS-PLATFORM FROM DAY ONE using an abstraction layer:
    Abstract interface `InputCapture` with platform-specific backends.
    Pro: No tech debt. Users on all platforms benefit equally.
    Con: 3x QA surface. Linux accessibility APIs are notoriously unreliable.

(C) BUILD WINDOWS + MACOS ONLY (skip Linux for now):
    Windows + macOS covers ~95% of the target corporate desktop market.
    Linux support added later.

Please recommend a platform strategy and provide:
1. Recommended option and rationale
2. Abstraction layer design (interface + backend pattern in Python)
3. Which platform-specific libraries to use for each capability on each platform
4. How to handle graceful degradation when a platform capability isn't available
   (e.g. Accessibility API not granted on macOS — fall back to window-title-only)
5. CI/CD testing strategy (how to test OS-level hooks without real OS access)
6. Any macOS permission pain points to warn about (accessibility permissions,
   screen recording permissions, notarization for distribution)

Format your answer as: Recommendation, Abstraction Layer Design (with Python
pseudocode), Library Choices by Platform, Graceful Degradation Table, Testing Strategy.
```

---

## Prompt 4 — Automation Output: What Format Should Generated Scripts Use?

```
You are an automation architect helping design the output format for an AI
system that observes user workflows and generates automation scripts.

CONTEXT:
We are building an Intelligence Engine that:
1. Watches OS-level user interactions (active windows, clicks, keystrokes)
2. Uses AI to identify repeating multi-step workflows (e.g. "copy data from
   Excel into a CRM web form every Monday morning")
3. Generates automation scripts for those workflows so the user can run them
   with one click instead of doing the 12 manual steps themselves

Example workflow we'd need to automate:
- User opens Excel, selects cells A1:F20, copies
- Switches to Chrome, navigates to salesforce.com/opportunities/new
- Pastes into "Revenue Forecast" field
- Fills "Close Date" field with today + 30 days
- Clicks Submit button
- Closes Excel

The target users are:
- Non-technical users (PMs, sales reps, operations staff)
- Semi-technical users (analysts, junior engineers)
- Power users who might want to edit the generated script

THE QUESTION:
What should the primary automation output format be?

OPTION A — PLAYWRIGHT PYTHON
Pro: Works across Chrome/Firefox/Safari. Visible browser mode (user can watch it
run). Pausable. Well-documented. Python already in the stack.
Con: Requires `playwright install`. Only automates browser + clipboard interactions.
Can't automate desktop apps (Excel, Outlook, native Win32 apps).

OPTION B — PYAUTOGUI + KEYBOARD (pixel-based)
Pro: Automates any app (browser AND native desktop apps). No browser install.
Cross-platform. Simple API.
Con: Brittle — breaks if window positions or button sizes change. Needs screen
resolution to be consistent. Poor at handling dynamic content.

OPTION C — WINDOWS COM / WIN32 API (Windows only)
Pro: Directly manipulates Excel via COM (xlwings, win32com). More reliable than
pixel-clicking. Native to Windows.
Con: Windows-only. Complex API. Not beginner-friendly.

OPTION D — MULTI-FORMAT OUTPUT
Generate different script formats depending on what the workflow involves:
- Browser-only → Playwright
- Desktop apps (Excel, Outlook) → pywin32 COM on Windows / AppleScript on Mac
- API-detectable patterns → Python requests calls
- Everything else → PyAutoGUI fallback

OPTION E — LOW-CODE FORMAT (YAML recipe)
Generate a human-readable YAML recipe first:
  steps:
    - app: excel | action: copy | range: A1:F20
    - app: chrome | action: navigate | url: salesforce.com/...
    - app: chrome | action: fill | selector: [data-field="revenue"] | value: {{clipboard}}
    - app: chrome | action: click | selector: button[type=submit]
Then compile YAML → Playwright/PyAutoGUI/COM at execution time.
Pro: Users can edit the YAML without writing code. Portable. Compilable to
multiple backends. LLM can generate it reliably (structured output).
Con: More engineering. Need a recipe compiler/interpreter.

Please recommend the best automation output strategy for this use case and provide:
1. Recommended format(s) and rationale
2. How to handle the browser vs. native desktop app split
3. Script template structure (what boilerplate should every generated script have:
   dry-run mode, confirmation prompts, rollback hooks, logging)
4. How non-technical users should review and approve before running
5. Error handling patterns when the automation fails mid-workflow
6. How to version/store generated scripts in ~/.memoria/automations/

Format your answer as: Recommendation, Script Template (code example),
Browser vs. Desktop Strategy, User Review Flow, Error Handling, Storage Design.
```

---

## Prompt 5 — VLM Choice: Cloud vs. Local Quantized Model for Screenshot Parsing

```
You are an ML infrastructure architect helping decide the inference strategy
for a vision-language model component in a privacy-sensitive local tool.

CONTEXT:
Memoria is a local-first Python tool. We are building an Intelligence Engine
that captures screenshots at 1fps (or on UI event) and needs a Vision-Language
Model (VLM) to extract semantic meaning from them — specifically:
- "What UI elements are visible?" (buttons, text fields, table headers, form labels)
- "What application is this and what screen/state is it in?" (e.g. "Salesforce
  Opportunity form, Revenue Forecast field focused")
- "What action did the user just take?" (e.g. "Clicked Submit button")

This parsed semantic understanding feeds into Pillar 3's sequence mining, which
identifies repeating workflow patterns.

CONSTRAINTS:
1. Privacy: some users work with highly sensitive data (healthcare, finance,
   legal). Screenshots may contain passwords, PII, confidential documents.
   Cloud inference means screenshots leave the machine — a hard blocker for
   some users.
2. Latency: at 1fps, we need inference in under 3 seconds ideally. Very large
   models (70B+) are too slow on typical hardware.
3. Hardware: target machines are corporate laptops — typically 16–32GB RAM,
   possibly no dedicated GPU, or a modest 4–8GB VRAM GPU.
4. Cost: cloud API calls at 1fps = ~86,400 calls/day per user. At $0.002/image
   (GPT-4V pricing), that is ~$173/day per user — completely unviable.
5. Existing stack: Memoria already supports Ollama for local LLM inference.
   LiteLLM provides the abstraction layer.

THE OPTIONS:

OPTION A — CLOUD VLM (Claude claude-3-5-sonnet / GPT-4V via LiteLLM)
Pro: Best accuracy. Zero local compute. Uses existing LiteLLM integration.
Con: Screenshots leave the machine. Cost prohibitive at 1fps. Latency varies.
Potential fix: only send screenshots on significant UI change (not 1fps), reduce
frequency to once per session or on workflow boundary events.

OPTION B — LOCAL OLLAMA (llava-1.6:7b or llava-1.6:34b)
Pro: Fully local. Uses existing Ollama integration. 7B fits in 6GB VRAM.
Con: Lower accuracy than GPT-4V for fine-grained UI element detection. Slower
on CPU-only machines (~10–30s per image on CPU).

OPTION C — UI-TARS (7B, Byte Dance, specialised for UI understanding)
Pro: Trained specifically on UI screenshots — outperforms general VLMs on
element detection, action grounding, workflow understanding.
Con: Not yet in Ollama registry (manual GGUF setup needed). 7B model requires
~6GB VRAM or ~20GB RAM for CPU inference.

OPTION D — LIGHTWEIGHT HYBRID (fast local model for element detection +
cloud LLM only for complex reasoning)
Use a small, fast local model (e.g. a fine-tuned MobileViT or CLIP-based
model trained specifically on UI screenshots) for low-level element detection.
Only call the cloud LLM for high-level workflow understanding (much rarer, e.g.
once per detected session boundary, not per frame).
Pro: Fast + private for the common case. Cloud only for complex reasoning.
Con: Two models to manage. Fine-tuned UI-specific model may not exist off-the-shelf.

OPTION E — ACCESSIBILITY API FIRST, VLM AS FALLBACK
Use the OS Accessibility API (UI Automation on Windows, AXUIElement on macOS)
as the primary source of semantic UI element data — it's free, fast, and
doesn't require image processing. Only fall back to VLM for apps that block
Accessibility API access (some Electron apps, some web content).
Pro: Zero ML inference cost for the common case. Deterministic, structured output.
Con: Not all apps support Accessibility API fully. Requires Pillar 1's
accessibility integration to be complete first.

Please recommend the best VLM/inference strategy for this use case and provide:
1. Recommended option (or hybrid combination) and rationale
2. Specific model names and quantization levels (e.g. llava:7b-v1.6-Q4_K_M)
3. Inference pipeline design (how screenshots flow from capture → semantic output)
4. How to handle users with no GPU (CPU-only inference fallback)
5. Privacy guarantee design (can the user verify screenshots never leave the machine?)
6. How to reduce the number of VLM calls (change detection, event-triggered vs. polling)
7. Integration with the existing Ollama / LiteLLM stack in Memoria

Format your answer as: Recommendation, Model Specs, Inference Pipeline (diagram
or pseudocode), CPU Fallback Strategy, Privacy Architecture, Call Reduction Design.
```
