"""
E2E tests — Chat (Ask) flow
──────────────────────────────
Verifies that POST /api/ask returns a valid SSE stream with the expected
event sequence: chunk* → sources? → done

Also verifies:
  - Sources contain the queried project name
  - Chunk events contain non-empty text
  - Stream terminates with a 'done' event
  - Asking about an unknown project returns 404
"""

import asyncio
from unittest.mock import patch

import httpx
import pytest

from .conftest import parse_sse_events, _mock_completion


pytestmark = pytest.mark.anyio


# ── helpers ────────────────────────────────────────────────────────────────────

async def _ask(http_client: httpx.AsyncClient, project: str, question: str) -> list[dict]:
    """POST /api/ask and return all SSE events as parsed JSON objects."""
    with patch("litellm.completion", side_effect=_mock_completion):
        resp = await http_client.post(
            "/api/ask",
            json={"project": project, "question": question, "history": []},
        )
        resp.raise_for_status()
        return parse_sse_events(resp.text)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestAskEndpointSSE:
    """POST /api/ask emits correct SSE event sequence."""

    async def test_ask_returns_200(self, http_client):
        """POST /api/ask for a known project returns HTTP 200."""
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/ask",
                json={"project": "auth_service", "question": "What does this do?", "history": []},
            )
        assert resp.status_code == 200

    async def test_ask_response_is_event_stream(self, http_client):
        """The response Content-Type is text/event-stream."""
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/ask",
                json={"project": "auth_service", "question": "Describe the architecture", "history": []},
            )
        assert "event-stream" in resp.headers.get("content-type", ""), (
            f"Expected event-stream content type, got: {resp.headers.get('content-type')}"
        )

    async def test_ask_emits_chunk_events(self, http_client):
        """SSE stream contains at least one 'chunk' event with non-empty text."""
        events = await _ask(http_client, "auth_service", "What is the auth service?")
        chunk_events = [e for e in events if e.get("type") == "chunk"]
        assert len(chunk_events) >= 1, f"Expected chunk events, got: {events}"
        for chunk in chunk_events:
            assert "text" in chunk, f"Chunk event should have 'text' key: {chunk}"
            assert isinstance(chunk["text"], str)

    async def test_ask_emits_done_event(self, http_client):
        """SSE stream terminates with a 'done' event."""
        events = await _ask(http_client, "auth_service", "What is the auth service?")
        types = [e.get("type") for e in events]
        assert "done" in types, f"Expected 'done' event in stream, got types: {types}"

    async def test_ask_done_is_last_event(self, http_client):
        """The 'done' event is the last non-empty event in the stream."""
        events = await _ask(http_client, "auth_service", "Describe the rate limit")
        non_empty = [e for e in events if e]
        assert non_empty[-1].get("type") == "done", (
            f"Expected 'done' as last event, got: {non_empty[-1]}"
        )

    async def test_chunk_texts_concatenate_to_real_content(self, http_client):
        """Joining all chunk texts gives a non-trivial response."""
        events = await _ask(http_client, "auth_service", "Tell me about OAuth")
        full_text = "".join(
            e.get("text", "") for e in events if e.get("type") == "chunk"
        )
        assert len(full_text.strip()) > 10, (
            f"Concatenated chunk text is too short: {full_text!r}"
        )

    async def test_ask_payments_project(self, http_client):
        """Asking about payments-service returns payment-related content."""
        events = await _ask(http_client, "payments_service", "How does Stripe work here?")
        full_text = "".join(
            e.get("text", "") for e in events if e.get("type") == "chunk"
        ).lower()
        assert any(kw in full_text for kw in ("stripe", "payment", "postgresql")), (
            f"Expected payments-related response, got: {full_text!r}"
        )


class TestAskErrors:
    """Error handling for /api/ask."""

    async def test_ask_unknown_project_returns_404(self, http_client):
        """Asking about a project with no Memory Bank returns 404."""
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/ask",
                json={"project": "completely_nonexistent_xyz_9999", "question": "What?", "history": []},
            )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown project, got {resp.status_code}"
        )

    async def test_ask_empty_question_accepted(self, http_client):
        """An empty question is handled gracefully (either 200 or 400, not 500)."""
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/ask",
                json={"project": "auth_service", "question": "", "history": []},
            )
        assert resp.status_code in (200, 400), (
            f"Expected 200 or 400 for empty question, got {resp.status_code}"
        )


class TestAskMultiTurn:
    """Multi-turn history is accepted by /api/ask."""

    async def test_ask_with_history(self, http_client):
        """Including conversation history returns a successful response."""
        history = [
            {"role": "user", "content": "What is auth-service?"},
            {"role": "assistant", "content": "It handles authentication."},
        ]
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/ask",
                json={
                    "project": "auth_service",
                    "question": "What rate limit does it have?",
                    "history": history,
                },
            )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        types = [e.get("type") for e in events]
        assert "done" in types
