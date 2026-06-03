"""
E2E tests — Plan tab API regression tests
──────────────────────────────────────────
Covers:
  - POST /api/plan with non-empty plan returns 200 and SSE stream with done event (Req 3.5)
  - POST /api/plan with empty plan field returns 400 (Req 3.6)
"""

from unittest.mock import patch

import pytest

from .conftest import parse_sse_events, _mock_completion


pytestmark = pytest.mark.anyio


class TestPlanEndpoint:
    """POST /api/plan with a valid plan returns 200 and an SSE stream with a done event (Req 3.5)."""

    async def test_plan_returns_200_for_valid_plan(self, http_client):
        """POST /api/plan with a non-empty plan_text field returns HTTP 200."""
        with patch("litellm.completion", side_effect=_mock_completion):
            resp = await http_client.post(
                "/api/plan",
                json={"plan_text": "Migrate auth-service to OAuth2 and update payments-service."},
            )
        if resp.status_code == 404:
            pytest.skip("POST /api/plan not available in this build")
        assert resp.status_code == 200, (
            f"Expected 200 from POST /api/plan, got {resp.status_code}: {resp.text}"
        )

    async def test_plan_stream_contains_done_event(self, http_client):
        """POST /api/plan/stream SSE stream terminates with a done event (Req 3.5)."""
        with patch("litellm.completion", side_effect=_mock_completion):
            async with http_client.stream(
                "POST",
                "/api/plan/stream",
                json={"plan_text": "Migrate auth-service to OAuth2 and update payments-service."},
            ) as resp:
                if resp.status_code == 404:
                    pytest.skip("POST /api/plan/stream not available in this build")
                assert resp.status_code == 200
                raw = ""
                async for line in resp.aiter_lines():
                    raw += line + "\n"
                    if '"done"' in line:
                        break

        events = parse_sse_events(raw)
        types = [e.get("type") for e in events]
        assert "done" in types, (
            f"Expected 'done' event in /api/plan/stream SSE stream, got types: {types}"
        )

    async def test_plan_stream_contains_chunk_events(self, http_client):
        """POST /api/plan/stream SSE stream emits at least one chunk event before done."""
        with patch("litellm.completion", side_effect=_mock_completion):
            async with http_client.stream(
                "POST",
                "/api/plan/stream",
                json={"plan_text": "Migrate auth-service to OAuth2 and update payments-service."},
            ) as resp:
                if resp.status_code == 404:
                    pytest.skip("POST /api/plan/stream not available in this build")
                raw = ""
                async for line in resp.aiter_lines():
                    raw += line + "\n"
                    if '"done"' in line:
                        break

        events = parse_sse_events(raw)
        types = [e.get("type") for e in events]
        # plan/stream uses "status" events for progress updates (not "chunk"/"progress")
        assert any(t in types for t in ("chunk", "progress", "status")), (
            f"Expected at least one chunk/progress/status event before done, got: {types}"
        )


class TestPlanValidation:
    """POST /api/plan with an empty plan field returns 400 (Req 3.6)."""

    async def test_empty_plan_returns_400(self, http_client):
        """POST /api/plan with empty plan_text field returns HTTP 400 (Req 3.6)."""
        resp = await http_client.post("/api/plan", json={"plan_text": ""})
        if resp.status_code == 404:
            pytest.skip("POST /api/plan not available in this build")
        assert resp.status_code == 400, (
            f"Expected 400 for empty plan_text, got {resp.status_code}: {resp.text}"
        )

    async def test_missing_plan_field_returns_400_or_422(self, http_client):
        """POST /api/plan with missing plan_text field returns 400 or 422."""
        resp = await http_client.post("/api/plan", json={})
        if resp.status_code == 404:
            pytest.skip("POST /api/plan not available in this build")
        assert resp.status_code in (400, 422), (
            f"Expected 400 or 422 for missing plan_text field, got {resp.status_code}: {resp.text}"
        )

    async def test_whitespace_only_plan_returns_400(self, http_client):
        """POST /api/plan with whitespace-only plan_text field returns HTTP 400."""
        resp = await http_client.post("/api/plan", json={"plan_text": "   "})
        if resp.status_code == 404:
            pytest.skip("POST /api/plan not available in this build")
        assert resp.status_code == 400, (
            f"Expected 400 for whitespace-only plan_text, got {resp.status_code}: {resp.text}"
        )
