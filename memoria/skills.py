"""
memoria skills — converts Memory Banks into SKILL.md files.

A SKILL.md file is a structured, agent-consumable summary of a project or process.
It tells an AI agent (Claude, Cursor, Copilot, n8n, LangChain, CrewAI, etc.) what
it can do with a codebase or process — the capabilities, the key operations,
the pitfalls, and the data it needs.

Based on the Anthropic Agent Skills open standard (Dec 2025).
Supports multiple output formats: raw (standard SKILL.md), langchain, crewai,
autogen, n8n.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import ModelProvider

# ── Generation prompts ────────────────────────────────────────────────────────

_SKILL_SYSTEM = """You are an expert at writing SKILL.md files — structured documents
that let AI agents understand what they can do with a system.

A great SKILL.md answers:
1. What is this system / process, in one sentence?
2. What are the 5–10 things an AI agent can *do* with it?
3. For each capability: what inputs does it need, what does it produce, what are the risks?
4. What data, credentials, or context does the agent need to start?
5. What are the absolute rules (things never to do)?

Rules:
- Be specific — name actual functions, endpoints, commands, or steps
- Write for an LLM system prompt — dense, no filler
- Each capability should be independently actionable
- If the source is a Process Memory Bank, each step becomes a capability
- Never invent — if something isn't in the Memory Bank, omit it
"""

_SKILL_PROMPT = """Here is a Memory Bank for a project or process.
Convert it into a SKILL.md file.

PROJECT / PROCESS: {name}
MEMORY BANK:
{content}

Write the SKILL.md using EXACTLY this structure:

# SKILL: {name}
> Version: 1.0 | Generated: {date} | Source: Memoria Memory Bank

## What This Skill Does
[One paragraph. What system or process does this cover? What problem does it solve?
 Who/what runs it? What are the key outcomes?]

## Prerequisites
[Everything an agent needs before it can use this skill:]
- **Access:** [Credentials, tokens, API keys, system access needed]
- **Dependencies:** [Other systems, processes, or data that must be ready first]
- **Context:** [What the agent must know before starting]

## Capabilities

[List 5–15 capabilities. Each one is something the agent can DO.]

### [Capability Name]
**When to use:** [The trigger or user intent that invokes this]
**Input:** [What the agent needs to have/know]
**Steps:**
1. [Concrete action]
2. [Concrete action]
...
**Output:** [What the agent produces or changes]
**Risk:** [What can go wrong — Low / Medium / High]
⚠️ **Never:** [Hard constraint — what never to do in this capability]

[Repeat for each capability...]

## Data Model
[Key data structures, schemas, or objects the agent works with.
 If it's a process: the key state transitions and their fields.]

## Rules & Constraints
[Global rules that apply to ALL capabilities:]
- NEVER: [Hard constraint]
- ALWAYS: [Requirement]
- PREFER: [Soft guideline]

## Error Handling
| Error | Likely Cause | Recovery |
|-------|-------------|----------|
| [Error or symptom] | [What caused it] | [How to recover] |

## Example Agent Prompts
[3 example user prompts that this skill handles well:]
1. "[Example prompt]"
2. "[Example prompt]"
3. "[Example prompt]"
"""

_LANGCHAIN_PROMPT = """Here is a Memory Bank for a project or process.
Convert it into a LangChain StructuredTool definition.

PROJECT: {name}
MEMORY BANK:
{content}

Output a Python code block that defines LangChain tools for this project.
Include:
- Tool name, description, args_schema (Pydantic model), func placeholder
- One tool per major capability (5-10 tools max)
- Docstrings that explain when to use each tool

Use this pattern:
```python
from langchain.tools import StructuredTool
from pydantic import BaseModel, Field

class [ToolName]Input(BaseModel):
    ...

def [tool_name]([args]) -> str:
    \"\"\"[When to use this tool. What it does. What it returns.]\"\"\"
    # TODO: implement
    raise NotImplementedError

[tool_name]_tool = StructuredTool.from_function(
    func=[tool_name],
    name="[snake_case_name]",
    description="[One line: when to call this, what it does]",
    args_schema=[ToolName]Input,
)
```
"""

_CREWAI_PROMPT = """Here is a Memory Bank for a project or process.
Convert it into a CrewAI Agent + Task definition.

PROJECT: {name}
MEMORY BANK:
{content}

Output a Python code block defining:
1. A CrewAI Agent specialized for this project
2. A set of Task definitions (one per major workflow)
3. Tool stubs for each capability

Use this pattern:
```python
from crewai import Agent, Task, Crew
from crewai_tools import BaseTool

class [CapabilityName]Tool(BaseTool):
    name: str = "[tool_name]"
    description: str = "[When to use. What it does.]"

    def _run(self, **kwargs) -> str:
        # TODO: implement
        raise NotImplementedError

[project_name]_agent = Agent(
    role="[Role title]",
    goal="[What this agent achieves]",
    backstory="[Context from the Memory Bank — what this system is and how it works]",
    tools=[...],
    verbose=True,
)
```
"""

_AUTOGEN_PROMPT = """Here is a Memory Bank for a project or process.
Convert it into a Microsoft AutoGen AssistantAgent definition.

PROJECT: {name}
MEMORY BANK:
{content}

Output a Python code block defining:
1. An AutoGen AssistantAgent with the right system_message
2. Function definitions for each major capability (decorated with @user_proxy.register_for_execution)
3. Brief usage example

Use AutoGen v0.4 patterns.
"""

_N8N_PROMPT = """Here is a Memory Bank for a project or process.
Convert it into an n8n workflow JSON skeleton.

PROJECT: {name}
MEMORY BANK:
{content}

Output a JSON block representing an n8n workflow that:
1. Has a trigger node (Manual or Webhook)
2. Has one node per major capability (use HTTP Request or Code nodes as placeholders)
3. Connects them in a logical sequence
4. Includes notes/sticky-note nodes explaining what each step does

Keep it runnable — real n8n JSON format.
"""

# ── Format map ────────────────────────────────────────────────────────────────

_FORMAT_PROMPTS = {
    "raw":      (_SKILL_PROMPT,      "md"),
    "langchain":(_LANGCHAIN_PROMPT,  "py"),
    "crewai":   (_CREWAI_PROMPT,     "py"),
    "autogen":  (_AUTOGEN_PROMPT,    "py"),
    "n8n":      (_N8N_PROMPT,        "json"),
}


# ── Public API ────────────────────────────────────────────────────────────────

def generate_skill(
    project_name: str,
    book_content: str,
    output_dir: str,
    fmt: str = "raw",
    config_path: str = "config.yaml",
    on_progress=None,
) -> str:
    """
    Generate a SKILL.md (or format-specific file) from a Memory Bank.

    Returns the path to the written file.
    """
    def progress(msg):
        if on_progress:
            on_progress(msg)

    if fmt not in _FORMAT_PROMPTS:
        raise ValueError(f"Unknown format '{fmt}'. Choose: {', '.join(_FORMAT_PROMPTS)}")

    prompt_template, file_ext = _FORMAT_PROMPTS[fmt]
    model = ModelProvider(config_path)

    progress(f"Generating {fmt} skill file for '{project_name}'...")

    content = model.complete(
        _SKILL_SYSTEM,
        prompt_template.format(
            name=project_name,
            content=book_content,
            date=datetime.now().strftime("%Y-%m-%d"),
        ),
    )

    # Determine output filename
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)
    suffix = "" if fmt == "raw" else f"_{fmt}"
    if fmt == "raw":
        filename = f"{safe_name}_SKILL.md"
    else:
        filename = f"{safe_name}_skill{suffix}.{file_ext}"

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_text(content, encoding="utf-8")

    progress(f"Saved to {out_path}")
    return str(out_path)


def list_skills(skills_dir: str) -> list[dict]:
    """List all SKILL.md files in a directory."""
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        return []
    result = []
    for f in sorted(skills_path.glob("*_SKILL.md")):
        stat = f.stat()
        name = f.stem.replace("_SKILL", "").replace("_", " ")
        result.append({
            "name":     name,
            "path":     str(f),
            "size_kb":  round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return result
