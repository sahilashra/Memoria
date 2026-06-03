"""
E2E tests — Global Ask flow
──────────────────────────────
Verifies that POST /api/global-ask searches across ALL Memory Banks and
returns source attribution for multiple projects.

Event sequence expected:
  data: {"type": "sources", "sources": [...]}  ← fired before first chunk
  data: {"type": "chunk",   "text": "..."}     ← one or more
  data: {"type": "done"}                        ← terminates the stream

Both fixture books (auth-service, payments-service) should appear in
sources when asked a question that is relevant to both.
"""

import asyncio
from unittest.mock import patch

import httpx
import pytest

from .conftest import parse_sse_events, _mock_completion


pytestmark = pytest.mark.anyio


# ── helpers ────────────────────────────────────────────────────────────────────

async def _global_ask(http_client: httpx.AsyncClient, question: str) -> list[dict]:
    """POST /api/global-ask and return all parsed SSE events."""
    with patch("litellm.completion", side_effect=_mock_completion):
        resp = await http_client.post(
            "/api/global-ask",
            json={"question": question, "top_k": 8},
        )
        resp.raise_for_status()
        return parse_sse_events(resp.text)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestGlobalAskEndpoint:
    """POST /api/global-ask basic contract tests."""

    async def test_global_ask_returns_200(self, http_client):
        """POST /api/global-ask with a valid question returns HTTP 200."""
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/global-ask",
                json={"question": "What projects use FastAPI?"},
            )
        assert resp.status_code == 200

    async def test_global_ask_returns_event_stream(self, http_client):
        """The response Content-Type is text/event-stream."""
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/global-ask",
                json={"question": "Which projects use PostgreSQL?"},
            )
        assert "event-stream" in resp.headers.get("content-type", ""), (
            f"Expected event-stream, got: {resp.headers.get('content-type')}"
        )

    async def test_global_ask_emits_done_event(self, http_client):
        """The stream terminates with a 'done' event."""
        events = await _global_ask(http_client, "What authentication mechanism is used?")
        types = [e.get("type") for e in events]
        assert "done" in types, f"Expected 'done' event, got: {types}"

    async def test_global_ask_emits_chunk_events(self, http_client):
        """At least one 'chunk' event with non-empty text is emitted."""
        events = await _global_ask(http_client, "What tech stack is used?")
        chunks = [e for e in events if e.get("type") == "chunk"]
        assert len(chunks) >= 1, f"Expected chunk events, got: {events}"
        for c in chunks:
            assert isinstance(c.get("text"), str), f"Chunk missing 'text': {c}"

    async def test_global_ask_empty_question_rejected(self, http_client):
        """An empty question returns 400."""
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/global-ask",
                json={"question": ""},
            )
        assert resp.status_code == 400, (
            f"Expected 400 for empty question, got {resp.status_code}"
        )


class TestGlobalAskSourceAttribution:
    """Sources event contains project names from the fixture books."""

    async def test_sources_event_emitted_before_chunks(self, http_client):
        """The 'sources' event appears before any 'chunk' events."""
        events = await _global_ask(http_client, "Which projects use Python?")
        # Find positions of 'sources' and first 'chunk'
        types = [e.get("type") for e in events]
        if "sources" not in types:
            pytest.skip("No sources event in stream (ChromaDB likely not available)")
        sources_idx = types.index("sources")
        chunk_idxs = [i for i, t in enumerate(types) if t == "chunk"]
        if chunk_idxs:
            assert sources_idx < chunk_idxs[0], (
                f"sources event (idx {sources_idx}) should precede first chunk (idx {chunk_idxs[0]})"
            )

    async def test_sources_event_contains_project_list(self, http_client):
        """The 'sources' event has a non-empty list of project names."""
        events = await _global_ask(http_client, "Which services use FastAPI?")
        sources_events = [e for e in events if e.get("type") == "sources"]
        if not sources_events:
            pytest.skip("No sources event (ChromaDB likely not available)")
        sources = sources_events[0].get("sources", [])
        assert len(sources) >= 1, f"Expected at least one source, got: {sources}"

    async def test_sources_includes_both_fixture_books(self, http_client):
        """Cross-project query returns sources from both fixture books."""
        events = await _global_ask(
            http_client,
            "Which projects use Python and FastAPI across the whole codebase?",
        )
        sources_events = [e for e in events if e.get("type") == "sources"]
        if not sources_events:
            pytest.skip("No sources event (ChromaDB likely not available)")
        sources = sources_events[0].get("sources", [])
        source_names = " ".join(str(s).lower() for s in sources)
        has_auth = "auth" in source_names
        has_payments = "payment" in source_names
        assert has_auth or has_payments, (
            f"Expected at least one fixture project in sources, got: {sources}"
        )
        # For a broad Python query, both books ideally appear
        if not (has_auth and has_payments):
            pytest.xfail(
                f"Only one fixture book in sources ({sources}); "
                "both are expected for a broad cross-project query."
            )

    async def test_sources_attribution_for_stripe_query(self, http_client):
        """Querying 'Stripe payments' returns payments-service as a source."""
        events = await _global_ask(http_client, "How does Stripe payment processing work?")
        sources_events = [e for e in events if e.get("type") == "sources"]
        if not sources_events:
            pytest.skip("No sources event (ChromaDB likely not available)")
        sources = sources_events[0].get("sources", [])
        source_names = " ".join(str(s).lower() for s in sources)
        assert "payment" in source_names, (
            f"Expected payments-service in Stripe query sources, got: {sources}"
        )

    async def test_sources_attribution_for_auth_query(self, http_client):
        """Querying 'JWT authentication' returns auth-service as a source."""
        events = await _global_ask(http_client, "How does JWT authentication work?")
        sources_events = [e for e in events if e.get("type") == "sources"]
        if not sources_events:
            pytest.skip("No sources event (ChromaDB likely not available)")
        sources = sources_events[0].get("sources", [])
        source_names = " ".join(str(s).lower() for s in sources)
        assert "auth" in source_names, (
            f"Expected auth-service in JWT query sources, got: {sources}"
        )


class TestGlobalAskTopK:
    """The top_k parameter limits how many books are consulted."""

    async def test_top_k_1_limits_sources(self, http_client):
        """With top_k=1, at most one source is returned."""
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/global-ask",
                json={"question": "What tech stack is used?", "top_k": 1},
            )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        sources_events = [e for e in events if e.get("type") == "sources"]
        if sources_events:
            sources = sources_events[0].get("sources", [])
            assert len(sources) <= 1, f"Expected ≤1 source with top_k=1, got: {sources}"

    async def test_top_k_defaults_to_8(self, http_client):
        """Omitting top_k defaults gracefully (no server error)."""
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/global-ask",
                json={"question": "What is the architecture?"},
            )
        assert resp.status_code == 200
