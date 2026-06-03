"""
Test Generation Capability — generates test scaffolding from ticket + code artifact.

Triggered when needs_tests is in capability_gaps.
Output: test file additions matching the project's test conventions
(file naming, assertion style, fixture patterns).
Always presented for review — never applied without user approval.
"""

from __future__ import annotations


_SYSTEM_PROMPT = """You are a senior engineer who writes precise test scaffolding
that matches the project's existing test patterns.

Rules:
- Match the project's test framework, file naming, and assertion style exactly.
- Generate test structure and test case stubs — not full implementations.
- Cover the happy path, edge cases, and error cases from the ticket.
- Reference the actual functions/classes being tested by name.
- Leave TODOs where test logic needs to be filled in.
- Do NOT duplicate tests that are obviously already written.
"""

_TEST_GEN_PROMPT = """Generate test scaffolding for this ticket.

TICKET:
{ticket_text}

PROJECT: {project_name}
IMPLEMENTATION TO TEST:
{code_context}

TEST PATTERNS FROM PROJECT:
{test_context}

Based on the above, generate:
1. The test file path (following project naming conventions)
2. Test class/function structure
3. Test case stubs for: happy path, edge cases, error handling
4. Any required fixtures or mocks (stub them, don't implement)

Format your response as:
## Test File
`path/to/test_file.py` (or `.spec.ts` etc.)

## Test Scaffolding
```[language]
[test structure with TODOs]
```

## Coverage Notes
[what's covered, what's deliberately left out, what needs manual verification]
"""


async def generate_test_artifact(
    ticket_text: str,
    project_name: str,
    generated_code: str | None,
    relevant_modules: list[str],
    books_dir: str = "books",
    config_path: str = "config.yaml",
) -> str:
    """
    Generate test scaffolding for the given ticket and (optionally) the code artifact.
    Uses async_complete() — safe to run inside asyncio.gather().
    """
    from ...models import ModelProvider
    from ...search import search as _search

    # Pull test-specific context from Memory Banks
    test_query = f"test patterns {ticket_text[:100]}"
    chunks = _search(test_query, books_dir=books_dir, top_k=4)

    test_context = "\n\n---\n\n".join(
        f"**{c['project']} / {c['section']}**\n{c['text']}"
        for c in chunks
        if c["project"] == project_name
        or c["project"].startswith(project_name + "/")
    ) or "No test Memory Bank found — using general test conventions."

    code_context = generated_code or "(no implementation artifact — writing tests from ticket only)"

    model = ModelProvider(config_path)
    prompt = _TEST_GEN_PROMPT.format(
        ticket_text=ticket_text,
        project_name=project_name,
        code_context=code_context[:2000],  # truncate to avoid token overflow
        test_context=test_context,
    )

    return await model.async_complete(_SYSTEM_PROMPT, prompt)
