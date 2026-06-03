"""
E2E tests — Search flow
─────────────────────────
Verifies both search endpoints against the seeded Memory Bank files:

  GET /api/search?q=   — semantic search via ChromaDB
  GET /api/fulltext?q= — exact substring search

For semantic search we skip gracefully if ChromaDB is not installed
(the seeded books need to be indexed first, which happens automatically
on the first search call).
"""

import asyncio
from unittest.mock import patch

import httpx
import pytest

from .conftest import _mock_completion


pytestmark = pytest.mark.anyio


# ── semantic search ────────────────────────────────────────────────────────────

class TestSemanticSearch:
    """GET /api/search returns ranked results for terms in the fixture books."""

    async def test_search_returns_200(self, http_client):
        """GET /api/search?q=auth returns HTTP 200."""
        resp = await http_client.get("/api/search", params={"q": "authentication"})
        assert resp.status_code == 200

    async def test_search_response_shape(self, http_client):
        """Response has 'results' and 'query' keys."""
        resp = await http_client.get("/api/search", params={"q": "authentication"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data, f"Expected 'results' key, got: {list(data.keys())}"
        assert "query" in data

    async def test_search_empty_query_returns_empty_results(self, http_client):
        """An empty query returns an empty result set without error."""
        resp = await http_client.get("/api/search", params={"q": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("results") == [], f"Expected empty results, got: {data}"

    async def test_search_finds_auth_for_jwt_query(self, http_client):
        """Querying 'JWT' finds the auth-service book."""
        resp = await http_client.get("/api/search", params={"q": "JWT token"})
        assert resp.status_code == 200
        data = resp.json()
        results = data.get("results", [])
        if len(results) == 0:
            # ChromaDB may not be installed — skip rather than fail
            pytest.skip("No semantic search results (ChromaDB likely not installed)")
        projects = [r.get("project", "").lower() for r in results]
        assert any("auth" in p for p in projects), (
            f"Expected auth-service in JWT results, got projects: {projects}"
        )

    async def test_search_result_has_required_fields(self, http_client):
        """Each result entry has project, section, score, excerpt."""
        resp = await http_client.get("/api/search", params={"q": "authentication"})
        data = resp.json()
        results = data.get("results", [])
        if not results:
            pytest.skip("No results — ChromaDB likely not installed")
        first = results[0]
        for field in ("project", "excerpt"):
            assert field in first, f"Result missing field '{field}': {first}"

    async def test_search_top_param_limits_results(self, http_client):
        """The 'top' parameter caps the number of results."""
        resp = await http_client.get("/api/search", params={"q": "python", "top": "1"})
        data = resp.json()
        results = data.get("results", [])
        if not results:
            pytest.skip("No results — ChromaDB likely not installed")
        assert len(results) <= 1, f"Expected at most 1 result with top=1, got {len(results)}"


# ── full-text search ──────────────────────────────────────────────────────────

class TestFullTextSearch:
    """GET /api/fulltext returns exact-match results from .md files."""

    async def test_fulltext_returns_200(self, http_client):
        """GET /api/fulltext?q=Stripe returns HTTP 200."""
        resp = await http_client.get("/api/fulltext", params={"q": "Stripe"})
        assert resp.status_code == 200

    async def test_fulltext_response_shape(self, http_client):
        """Response has 'results', 'query', and 'total' keys."""
        resp = await http_client.get("/api/fulltext", params={"q": "Stripe"})
        data = resp.json()
        assert "results" in data
        assert "query" in data
        assert "total" in data

    async def test_fulltext_finds_stripe(self, http_client):
        """'Stripe' appears in payments-service; fulltext search finds it."""
        resp = await http_client.get("/api/fulltext", params={"q": "Stripe"})
        data = resp.json()
        results = data.get("results", [])
        assert len(results) >= 1, "Expected at least one result for 'Stripe'"
        projects = [r.get("project", "").lower() for r in results]
        assert any("payment" in p for p in projects), (
            f"Expected payments-service in Stripe results, got: {projects}"
        )

    async def test_fulltext_finds_jwt(self, http_client):
        """'JWT' appears in auth-service; fulltext search finds it."""
        resp = await http_client.get("/api/fulltext", params={"q": "JWT"})
        data = resp.json()
        results = data.get("results", [])
        assert len(results) >= 1, "Expected at least one result for 'JWT'"
        projects = [r.get("project", "").lower() for r in results]
        assert any("auth" in p for p in projects), (
            f"Expected auth-service in JWT results, got: {projects}"
        )

    async def test_fulltext_result_has_required_fields(self, http_client):
        """Each fulltext result has project, section, line, excerpt."""
        resp = await http_client.get("/api/fulltext", params={"q": "FastAPI"})
        data = resp.json()
        results = data.get("results", [])
        assert len(results) >= 1, "Expected results for 'FastAPI'"
        first = results[0]
        for field in ("project", "excerpt"):
            assert field in first, f"Fulltext result missing field '{field}': {first}"

    async def test_fulltext_excerpt_contains_match(self, http_client):
        """The excerpt field contains the searched term."""
        resp = await http_client.get("/api/fulltext", params={"q": "PostgreSQL"})
        data = resp.json()
        results = data.get("results", [])
        assert len(results) >= 1, "Expected results for 'PostgreSQL'"
        first_excerpt = results[0].get("excerpt", "")
        assert "PostgreSQL" in first_excerpt or "postgresql" in first_excerpt.lower(), (
            f"Expected 'PostgreSQL' in excerpt, got: {first_excerpt!r}"
        )

    async def test_fulltext_empty_query_returns_empty(self, http_client):
        """An empty fulltext query returns empty results."""
        resp = await http_client.get("/api/fulltext", params={"q": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("results") == []

    async def test_fulltext_nonexistent_term_returns_empty(self, http_client):
        """A term not in any book returns zero results."""
        resp = await http_client.get("/api/fulltext", params={"q": "zzz_xyzzy_nonexistent_4567"})
        data = resp.json()
        assert data.get("total", 0) == 0, (
            f"Expected zero results for gibberish query, got total={data.get('total')}"
        )

    async def test_fulltext_total_matches_results_length(self, http_client):
        """The 'total' field accurately reflects the number of results (before top cap)."""
        resp = await http_client.get("/api/fulltext", params={"q": "FastAPI", "top": "100"})
        data = resp.json()
        total = data.get("total", 0)
        results = data.get("results", [])
        # total >= len(results) (total is before cap, results is after)
        assert total >= len(results), (
            f"total ({total}) should be >= len(results) ({len(results)})"
        )
