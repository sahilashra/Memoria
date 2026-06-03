"""
Code Generation Capability — generates implementation skeleton from ticket + Memory Banks.

Triggered when needs_implementation is in capability_gaps.
Output: component skeleton / function stubs matching the project's actual
file layout and naming conventions (from Memory Bank context).
Always presented for review — never applied without user approval.
"""

from __future__ import annotations


_SYSTEM_PROMPT = """You are a senior engineer who generates precise, minimal implementation
scaffolding based on a ticket and the project's existing patterns.

Rules:
- Match the project's actual naming conventions, file structure, and patterns.
- Generate stubs and skeletons — function signatures, class structures, imports.
- Do NOT generate full implementations — leave TODOs where logic should go.
- Reference specific file paths from the project context where relevant.
- If the ticket is vague, scaffold the most likely structure and call out the ambiguity.
- Use the same tech stack, style, and conventions visible in the Memory Bank.
"""

_CODE_GEN_PROMPT = """Generate an implementation skeleton for this ticket.

TICKET:
{ticket_text}

PROJECT: {project_name}
RELEVANT MODULES:
{modules_context}

Based on the Memory Bank context above, generate:
1. The file(s) that need to be created or modified (with exact paths)
2. Function/class signatures matching project conventions
3. Import statements
4. TODO comments marking where logic should be implemented
5. Any config or schema changes required

Format your response as:
## Files to Create / Modify
[list each file with its role]

## Implementation Skeleton
```[language]
[skeleton code with TODOs]
```

## Notes
[any ambiguities, assumptions, or things the developer should verify]
"""


async def generate_code_artifact(
    ticket_text: str,
    project_name: str,
    relevant_modules: list[str],
    books_dir: str = "books",
    config_path: str = "config.yaml",
    external_blocks: list | None = None,
) -> str:
    """
    Generate a code skeleton for the given ticket.
    Uses async_complete() — safe to run inside asyncio.gather().

    Context precedence in the prompt:
      1. external_blocks — fetched from linked GitHub URLs (highest priority)
      2. Memory Bank retrieval — project patterns and conventions
    """
    from ...models import ModelProvider
    from ...search import search as _search

    # Primary: external reference content (fetched from linked URLs)
    external_ctx = ""
    if external_blocks:
        parts = []
        for b in external_blocks:
            content = (b.get("content") or "")[:3000]
            parts.append(f"**[EXTERNAL] {b['label']}**\n\n{content}")
        external_ctx = (
            "## Primary Context — Fetched from Linked Repository\n\n"
            + "\n\n---\n\n".join(parts)
            + "\n\n"
        )

    # Secondary: Memory Bank retrieval
    chunks = _search(ticket_text, books_dir=books_dir, top_k=4)
    bank_ctx = "\n\n---\n\n".join(
        f"**{c['project']} / {c['section']}**\n{c['text']}"
        for c in chunks
        if c["project"] == project_name
        or c["project"].startswith(project_name + "/")
    ) or f"No Memory Bank found for {project_name}. Generating from ticket context only."

    modules_context = (external_ctx + bank_ctx) if external_ctx else bank_ctx

    model = ModelProvider(config_path)
    prompt = _CODE_GEN_PROMPT.format(
        ticket_text=ticket_text,
        project_name=project_name,
        modules_context=modules_context,
    )

    return await model.async_complete(_SYSTEM_PROMPT, prompt)
