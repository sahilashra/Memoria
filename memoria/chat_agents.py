"""
Agent Memory — project-specific AI agents with deep, always-current context.

Memoria becomes the living memory layer for every agent. Instead of writing
stale system prompts by hand, agents pull their context from a Memory Bank
that is updated every time the project changes.

Key concepts
────────────
  Agent context  — a compact (<800 token), agent-optimised summary extracted
                   from the project's Memory Bank; ready to inject into any
                   LLM system prompt or agent framework.

  agents.yaml    — defines named agents (reviewer, debugger, onboarder…)
                   each with a persona prompt + optional focus sections.

  Agent session  — interactive REPL: the agent knows the full project context
                   and answers questions / reviews code via the terminal.

  Built-in library — pre-made agents (code-reviewer, onboarder, debugger)
                     installable with:  memoria agent install <name>

Agent lookup order
──────────────────
  1. {books_dir}/{project}_agents.yaml     — project-specific definitions
  2. ~/.memoria/agents.yaml                — global user definitions
  3. Built-in library (agent_library)      — packaged templates

agents.yaml format
──────────────────
  agents:
    reviewer:
      persona: "You are a senior code reviewer for {project}. ..."
      focus:
        - architecture
        - security
        - performance
      greeting: "I'll review your code with the project context in mind."

    onboarder:
      persona: "You are an onboarding guide for {project}. ..."
      focus:
        - setup
        - common_workflows
"""

import re
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml


# ─── Built-in agent library ───────────────────────────────────────────────────

AGENT_LIBRARY: Dict[str, dict] = {
    "code-reviewer": {
        "persona": (
            "You are a senior code reviewer for **{project}**. "
            "You have deep, current knowledge of this codebase from its Memory Bank. "
            "Review code changes critically but constructively — flag correctness issues, "
            "security risks, performance problems, and maintainability concerns. "
            "Reference specific patterns and conventions from this project."
        ),
        "focus": ["architecture", "tech_stack", "key_components", "ai_agent_notes"],
        "greeting": (
            "Ready to review. Paste a diff, a function, or describe a change "
            "and I'll give you a thorough review grounded in this project's context."
        ),
    },
    "onboarder": {
        "persona": (
            "You are a friendly, expert onboarding guide for **{project}**. "
            "Help new contributors understand the codebase, set it up, and "
            "navigate common workflows. Be welcoming, clear, and specific — "
            "reference actual file names, commands, and patterns from this project."
        ),
        "focus": ["tldr", "tech_stack", "common_workflows", "setup"],
        "greeting": (
            "Welcome! I know this project inside out. "
            "Ask me anything: how to set it up, what a module does, "
            "how to run the tests, or where to start contributing."
        ),
    },
    "debugger": {
        "persona": (
            "You are a debugging specialist for **{project}**. "
            "Diagnose errors, trace bugs, and suggest fixes using full knowledge "
            "of the codebase architecture, known issues, and common failure patterns. "
            "Be systematic: reproduce → isolate → explain → fix."
        ),
        "focus": ["architecture", "key_components", "ai_agent_notes", "known_issues"],
        "greeting": (
            "Describe the error, paste a stack trace, or tell me what's broken. "
            "I'll help you track it down using everything I know about this project."
        ),
    },
    "explainer": {
        "persona": (
            "You are a technical writer and educator for **{project}**. "
            "Explain how this codebase works — from high-level architecture down to "
            "individual functions — in plain language that any developer can follow."
        ),
        "focus": ["tldr", "architecture", "tech_stack", "key_components"],
        "greeting": (
            "What would you like me to explain? "
            "I can cover the overall architecture, a specific module, "
            "a data flow, or why a particular design decision was made."
        ),
    },
    "security-reviewer": {
        "persona": (
            "You are a security-focused code reviewer for **{project}**. "
            "Identify vulnerabilities, OWASP risks, authentication flaws, "
            "injection vectors, and insecure data handling. "
            "Suggest concrete mitigations, not just problems."
        ),
        "focus": ["architecture", "tech_stack", "key_components", "ai_agent_notes"],
        "greeting": (
            "Show me code, a design, or an API surface and I'll assess it for "
            "security risks in the context of this project."
        ),
    },
}


# ─── Agent context extraction ─────────────────────────────────────────────────

_SECTION_ALIASES = {
    "tldr":           ["tl;dr", "tldr", "summary", "overview"],
    "architecture":   ["architecture", "architecture overview", "system design", "design"],
    "tech_stack":     ["tech stack", "technology stack", "technologies", "stack"],
    "key_components": ["key components", "key functions", "components", "modules", "functions"],
    "common_workflows": ["common workflows", "workflows", "how to use", "usage"],
    "ai_agent_notes": ["ai agent notes", "agent notes", "ai notes", "for ai agents"],
    "known_issues":   ["known issues", "open issues", "limitations", "caveats"],
    "setup":          ["setup", "installation", "getting started", "quick start"],
}

# Focus sections → which parsed keys to include
_FOCUS_SECTIONS = {
    "tldr":            ["tldr"],
    "architecture":    ["architecture"],
    "tech_stack":      ["tech_stack"],
    "key_components":  ["key_components"],
    "common_workflows":["common_workflows"],
    "ai_agent_notes":  ["ai_agent_notes"],
    "known_issues":    ["known_issues"],
    "setup":           ["common_workflows", "tldr"],
    "security":        ["architecture", "tech_stack", "ai_agent_notes"],
    "performance":     ["architecture", "tech_stack", "key_components"],
}


def _parse_book_sections(book_text: str) -> Dict[str, str]:
    """
    Parse a Memory Bank's markdown into named sections.
    Returns a dict of section_key → content.
    """
    sections: Dict[str, str] = {}
    current_key = None
    current_lines: List[str] = []

    def _flush():
        if current_key and current_lines:
            sections[current_key] = "\n".join(current_lines).strip()

    for line in book_text.splitlines():
        if line.startswith("## "):
            _flush()
            heading = line[3:].strip().lower()
            current_key = None
            for key, aliases in _SECTION_ALIASES.items():
                if any(a in heading for a in aliases):
                    current_key = key
                    break
            current_lines = []
        elif current_key is not None:
            current_lines.append(line)

    _flush()
    return sections


def _trim_section(text: str, max_lines: int = 15) -> str:
    """Keep the first max_lines non-empty lines of a section."""
    lines = [l for l in text.splitlines() if l.strip()]
    trimmed = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        trimmed += f"\n… ({len(lines) - max_lines} more lines omitted)"
    return trimmed


def build_agent_context(
    project: str,
    books_dir: str,
    focus: Optional[List[str]] = None,
    max_chars: int = 3000,
) -> Tuple[str, Dict[str, str]]:
    """
    Build a compact, agent-optimised context string from a project's Memory Bank.

    Returns (context_string, raw_sections_dict).

    The context_string is ready to be injected directly into an LLM system prompt.
    It is trimmed to max_chars so it fits in typical context windows.
    """
    books_path = Path(books_dir)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project)
    book_file = books_path / f"{safe}_memory_bank.md"

    if not book_file.exists():
        # Try fuzzy match
        candidates = list(books_path.glob(f"*_memory_bank.md"))
        match = next(
            (f for f in candidates if project.lower() in f.stem.lower()), None
        )
        if match:
            book_file = match
        else:
            return f"[No Memory Bank found for '{project}']", {}

    try:
        book_text = book_file.read_text(encoding="utf-8")
    except Exception as exc:
        return f"[Could not read Memory Bank: {exc}]", {}

    sections = _parse_book_sections(book_text)
    if not sections:
        # Fallback: use first 100 lines of the raw book
        raw = "\n".join(book_text.splitlines()[:100])
        return f"## Project: {project}\n\n{raw}", {}

    # Decide which sections to include
    if focus:
        keys_to_include = []
        for f in focus:
            keys_to_include.extend(_FOCUS_SECTIONS.get(f, [f]))
        # Always include tldr if available
        if "tldr" not in keys_to_include and "tldr" in sections:
            keys_to_include = ["tldr"] + keys_to_include
        keys_to_include = list(dict.fromkeys(keys_to_include))  # deduplicate, preserve order
    else:
        # Default: tldr + architecture + tech_stack + key_components
        keys_to_include = ["tldr", "architecture", "tech_stack", "key_components", "ai_agent_notes"]

    parts = [f"## Memory Bank: {project}\n"]
    for key in keys_to_include:
        content = sections.get(key)
        if not content:
            continue
        label = key.replace("_", " ").title()
        trimmed = _trim_section(content, max_lines=20)
        parts.append(f"### {label}\n{trimmed}\n")

    context = "\n".join(parts)

    # Hard trim to max_chars
    if len(context) > max_chars:
        context = context[:max_chars - 60] + "\n\n… [Memory Bank truncated to fit context window]"

    return context, sections


# ─── agents.yaml loading ──────────────────────────────────────────────────────

def _agents_yaml_paths(project: str, books_dir: str) -> List[Path]:
    """Return candidate paths for agents.yaml, in priority order."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project)
    return [
        Path(books_dir) / f"{safe}_agents.yaml",           # project-specific
        Path.home() / ".memoria" / "agents.yaml",           # global user
    ]


def load_agents(project: str, books_dir: str) -> Dict[str, dict]:
    """
    Load agent definitions for a project.

    Resolution order:
      1. {books_dir}/{project}_agents.yaml  — project-specific
      2. ~/.memoria/agents.yaml             — global
      3. Built-in library                   — always available as fallback

    Returns a dict of agent_name → agent_def.
    """
    agents: Dict[str, dict] = {}

    # Layer 1+2: file-based definitions (later files override earlier)
    for path in reversed(_agents_yaml_paths(project, books_dir)):
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                file_agents = data.get("agents", {})
                if isinstance(file_agents, dict):
                    agents.update(file_agents)
            except Exception:
                pass

    # Layer 3: built-in library (never overrides user definitions)
    for name, defn in AGENT_LIBRARY.items():
        if name not in agents:
            agents[name] = defn

    return agents


def get_agent(name: str, project: str, books_dir: str) -> Optional[dict]:
    """Return a single agent definition by name, or None if not found."""
    agents = load_agents(project, books_dir)
    # Try exact match, then case-insensitive, then prefix match
    if name in agents:
        return agents[name]
    lower = {k.lower(): v for k, v in agents.items()}
    return lower.get(name.lower())


def install_agent(
    library_name: str,
    project: str,
    books_dir: str,
    global_install: bool = False,
) -> Path:
    """
    Copy a built-in agent template into the project or global agents.yaml.
    Returns the path of the file that was written.
    """
    if library_name not in AGENT_LIBRARY:
        raise ValueError(
            f"'{library_name}' not in agent library. "
            f"Available: {', '.join(AGENT_LIBRARY.keys())}"
        )

    if global_install:
        target = Path.home() / ".memoria" / "agents.yaml"
    else:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project)
        target = Path(books_dir) / f"{safe}_agents.yaml"

    existing: dict = {}
    if target.exists():
        try:
            existing = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except Exception:
            pass

    agents = existing.setdefault("agents", {})
    agents[library_name] = AGENT_LIBRARY[library_name]

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.dump(existing, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target


# ─── Interactive agent session ────────────────────────────────────────────────

_SESSION_HISTORY_LIMIT = 12  # keep last N turns in context


def run_agent_session(
    agent_name: str,
    project: str,
    books_dir: str,
    config_path: str,
    on_chunk: Optional[callable] = None,
) -> None:
    """
    Start an interactive terminal session with a named agent.

    The agent's system prompt is built from:
      1. The agent's persona (from agents.yaml or built-in library)
      2. The project's agent context (extracted from its Memory Bank)

    Conversation history is kept in-memory for the session duration.
    Press Ctrl+C or type /exit to end the session.
    """
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.live import Live
    import litellm
    from .models import ModelProvider

    console = Console()

    # ── Load agent + context ──
    agent_def = get_agent(agent_name, project, books_dir)
    if agent_def is None:
        console.print(f"[red]Agent '{agent_name}' not found.[/red]")
        console.print(f"[dim]Run [bold]memoria agent list --project {project}[/bold] to see available agents.[/dim]")
        return

    agent_context, _ = build_agent_context(
        project, books_dir,
        focus=agent_def.get("focus"),
    )

    persona = agent_def.get("persona", "You are a helpful assistant.")
    persona = persona.replace("{project}", project)
    greeting = agent_def.get("greeting", f"Ready to help with {project}.")
    greeting = greeting.replace("{project}", project)

    system_prompt = f"{persona}\n\n{agent_context}"

    # ── Banner ──
    console.print()
    console.print(Panel(
        f"[bold]{agent_name}[/bold] agent for [bold cyan]{project}[/bold cyan]\n"
        f"[dim]{greeting}[/dim]\n\n"
        f"[dim]Type your question. [bold]/exit[/bold] or Ctrl+C to end the session.[/dim]",
        border_style="cyan",
        title="[bold cyan]Memoria Agent[/bold cyan]",
    ))
    console.print()

    provider = ModelProvider(config_path)
    history: List[dict] = []

    try:
        while True:
            # ── Get user input ──
            try:
                user_input = console.input("[bold cyan]You:[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue
            if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
                break

            history.append({"role": "user", "content": user_input})

            # ── Build messages ──
            messages = [{"role": "system", "content": system_prompt}]
            # Keep last N turns
            messages.extend(history[-_SESSION_HISTORY_LIMIT:])

            # ── Stream response ──
            console.print()
            response_text = ""
            try:
                stream = litellm.completion(
                    model=provider.model,
                    messages=messages,
                    max_tokens=provider.max_tokens,
                    stream=True,
                    timeout=60,
                )

                console.print(f"[bold]{agent_name}:[/bold] ", end="")
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        response_text += delta
                        console.print(delta, end="", highlight=False)
                console.print("\n")

            except KeyboardInterrupt:
                console.print("\n[dim](interrupted)[/dim]\n")
                response_text = response_text or "(interrupted)"

            except Exception as exc:
                console.print(f"\n[red]Error:[/red] {exc}\n")
                response_text = f"(error: {exc})"

            history.append({"role": "assistant", "content": response_text})

            # Trim history to limit
            if len(history) > _SESSION_HISTORY_LIMIT:
                history = history[-_SESSION_HISTORY_LIMIT:]

    except KeyboardInterrupt:
        pass

    console.print("\n[dim]Session ended.[/dim]\n")
