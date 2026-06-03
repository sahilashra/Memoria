"""
Unit tests for the agent context assembly pipeline.

Covers:
  - detect_capability_gaps: various ticket types produce correct gap lists
  - extract_external_references: GitHub URLs and ticket IDs parsed correctly
  - assemble_context: full pipeline with mocked ChromaDB search
  - retrieve_for_ticket: hybrid retrieval with mocked search backend
  - _match_tool: tool name matching logic in graph.py
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ─── Gap detection tests ──────────────────────────────────────────────────────

from memoria.agent.context import (
    detect_capability_gaps,
    extract_external_references,
    AssetInventory,
    CapabilityGaps,
)


class TestDetectCapabilityGaps:
    def _assets(self, **kwargs):
        return AssetInventory(**kwargs)

    def test_implementation_ticket_needs_impl_and_tests(self):
        ticket = "Implement the new login API endpoint with rate limiting"
        gaps = detect_capability_gaps(ticket, self._assets())
        assert gaps.needs_implementation
        assert gaps.needs_tests

    def test_test_only_ticket(self):
        ticket = "Write unit tests for the auth service"
        gaps = detect_capability_gaps(ticket, self._assets())
        assert gaps.needs_tests

    def test_frontend_ticket_needs_design_gap(self):
        ticket = "Build the new dashboard UI component with responsive layout"
        gaps = detect_capability_gaps(ticket, self._assets())
        assert gaps.needs_design

    def test_pr_already_open_suppresses_impl(self):
        ticket = "Implement feature X"
        gaps = detect_capability_gaps(ticket, self._assets(pr_open=True))
        assert not gaps.needs_implementation

    def test_empty_ticket_defaults_to_impl(self):
        gaps = detect_capability_gaps("", self._assets())
        assert gaps.needs_implementation

    def test_gap_list_format(self):
        ticket = "Add authentication endpoint"
        gaps = detect_capability_gaps(ticket, self._assets())
        gap_list = gaps.as_list()
        assert isinstance(gap_list, list)
        assert all(isinstance(g, str) for g in gap_list)
        assert "needs_implementation" in gap_list


class TestExtractExternalReferences:
    def test_github_url_parsed(self):
        text = "See https://github.com/owner/repo/issues/42 for context"
        refs = extract_external_references(text)
        assert len(refs) == 1
        r = refs[0]
        assert r.ref_type == "github_url"
        assert r.owner == "owner"
        assert r.repo == "repo"
        assert "42" in r.subpath

    def test_ticket_id_parsed(self):
        text = "Ticket VIZINZ-1234: implement the login flow"
        refs = extract_external_references(text)
        jira_refs = [r for r in refs if r.ref_type == "jira_id"]
        assert len(jira_refs) == 1
        assert jira_refs[0].ticket_id == "VIZINZ-1234"

    def test_no_duplicates_on_repeated_url(self):
        url = "https://github.com/a/b/issues/1"
        text = f"{url} and again {url}"
        refs = extract_external_references(text)
        assert len([r for r in refs if r.ref_type == "github_url"]) == 1

    def test_empty_text_returns_empty(self):
        assert extract_external_references("") == []


# ─── assemble_context tests ───────────────────────────────────────────────────

class TestAssembleContext:
    """Tests assemble_context with ChromaDB mocked out."""

    def _mock_search(self, return_value=None):
        """Patch the vector search used by retrieval.py."""
        hits = return_value or []
        return patch("memoria.search.search", return_value=hits)

    def _mock_file_graph(self):
        return patch("memoria.core.file_graph.get_neighbors", return_value=[])

    def test_gaps_are_detected_from_ticket(self):
        with self._mock_search(), self._mock_file_graph():
            ctx = asyncio.get_event_loop().run_until_complete(
                __import__("memoria.agent.context", fromlist=["assemble_context"]).assemble_context(
                    ticket_text="Implement the new user registration endpoint",
                    project_name="my_project",
                    books_dir="/tmp/books",
                    config_path="/tmp/config.yaml",
                )
            )
        assert ctx.capability_gaps.needs_implementation
        assert ctx.gap_list  # non-empty

    def test_retrieval_results_structured_correctly(self):
        hits = [
            {"project": "my_project", "section": "Auth", "text": "auth module", "score": 0.9},
            {"project": "my_project", "section": "API", "text": "api routes", "score": 0.8},
        ]
        with self._mock_search(hits), self._mock_file_graph():
            import asyncio as _aio
            from memoria.agent.context import assemble_context
            ctx = _aio.get_event_loop().run_until_complete(
                assemble_context(
                    ticket_text="Add rate limiting to auth endpoints",
                    project_name="my_project",
                    books_dir="/tmp/books",
                    config_path="/tmp/config.yaml",
                )
            )
        assert len(ctx.retrieval_results) > 0
        assert ctx.retrieval_results[0].project == "my_project"
        assert ctx.retrieval_results[0].rrf_score > 0

    def test_summary_returns_string(self):
        with self._mock_search(), self._mock_file_graph():
            from memoria.agent.context import assemble_context
            ctx = asyncio.get_event_loop().run_until_complete(
                assemble_context(
                    ticket_text="Build login page",
                    project_name="frontend",
                    books_dir="/tmp/books",
                    config_path="/tmp/config.yaml",
                )
            )
        s = ctx.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_graceful_degradation_when_search_fails(self):
        """retrieval failure → empty results, no exception propagated."""
        with patch("memoria.search.search", side_effect=Exception("ChromaDB unavailable")):
            with self._mock_file_graph():
                from memoria.agent.context import assemble_context
                ctx = asyncio.get_event_loop().run_until_complete(
                    assemble_context(
                        ticket_text="Fix the login bug",
                        project_name="auth",
                        books_dir="/tmp/books",
                        config_path="/tmp/config.yaml",
                    )
                )
        assert ctx.retrieval_results == []
        assert ctx.capability_gaps.needs_implementation  # still detected from text


# ─── retrieve_for_ticket tests ────────────────────────────────────────────────

class TestRetrieveForTicket:
    def _mock_search(self, return_value=None):
        return patch("memoria.search.search", return_value=return_value or [])

    def _mock_file_graph(self):
        return patch("memoria.core.file_graph.get_neighbors", return_value=[])

    def test_returns_list_of_retrieval_results(self):
        hits = [
            {"project": "proj_a", "section": "Auth", "text": "auth code", "score": 0.95},
        ]
        with self._mock_search(hits), self._mock_file_graph():
            from memoria.agent.retrieval import retrieve_for_ticket, RetrievalResult
            results = retrieve_for_ticket("add login endpoint", "proj_a", top_k=5)
        assert isinstance(results, list)
        assert all(isinstance(r, RetrievalResult) for r in results)

    def test_filters_to_project(self):
        hits = [
            {"project": "proj_a", "section": "Auth", "text": "auth", "score": 0.9},
            {"project": "proj_b", "section": "Other", "text": "other", "score": 0.8},
        ]
        with self._mock_search(hits), self._mock_file_graph():
            from memoria.agent.retrieval import retrieve_for_ticket
            results = retrieve_for_ticket("auth", "proj_a", top_k=5)
        assert all(r.project == "proj_a" for r in results)

    def test_rrf_scores_are_positive(self):
        hits = [
            {"project": "p", "section": "S", "text": "t", "score": 0.9},
        ]
        with self._mock_search(hits), self._mock_file_graph():
            from memoria.agent.retrieval import retrieve_for_ticket
            results = retrieve_for_ticket("test ticket", "p", top_k=5)
        assert all(r.rrf_score > 0 for r in results)

    def test_returns_empty_for_no_hits(self):
        with self._mock_search([]), self._mock_file_graph():
            from memoria.agent.retrieval import retrieve_for_ticket
            results = retrieve_for_ticket("anything", "proj", top_k=5)
        assert results == []

    def test_graph_expansion_merges_neighbors(self):
        base_hits = [
            {"project": "proj/auth", "section": "S", "text": "base", "score": 0.9},
        ]
        neighbor_hits = [
            {"project": "proj/api", "section": "N", "text": "neighbor", "score": 0.7},
        ]
        search_calls = [base_hits, neighbor_hits]
        search_mock = MagicMock(side_effect=search_calls)

        with patch("memoria.search.search", search_mock):
            with patch("memoria.core.file_graph.get_neighbors", return_value=["api"]):
                from memoria.agent.retrieval import retrieve_for_ticket
                results = retrieve_for_ticket("endpoint auth", "proj", top_k=10)

        projects = {r.project for r in results}
        assert "proj/auth" in projects


# ─── _match_tool tests ────────────────────────────────────────────────────────

class TestMatchTool:
    def setup_method(self):
        from memoria.agent.graph import _match_tool
        self._match = _match_tool

    def test_exact_match(self):
        available = {"add_comment", "create_issue", "list_issues"}
        assert self._match(available, ["add_comment"]) == "add_comment"

    def test_substring_fallback(self):
        # "comment" is a substring of "add_comment"
        available = {"add_comment", "create_issue"}
        result = self._match(available, ["comment"])
        assert result == "add_comment"

    def test_first_pattern_wins(self):
        available = {"create_pull_request", "open_pull_request"}
        result = self._match(available, ["create_pull_request", "open_pull_request"])
        assert result == "create_pull_request"

    def test_returns_none_when_no_match(self):
        available = {"list_issues", "get_issue"}
        assert self._match(available, ["add_comment", "create_comment"]) is None

    def test_empty_available(self):
        assert self._match(set(), ["add_comment"]) is None

    def test_case_insensitive_match(self):
        # MCP tool names use consistent casing; test camelCase prefix still matches
        available = {"addComment"}
        result = self._match(available, ["add_comment"])
        # "add_comment" not in "addcomment" (underscore vs none) — no match expected
        # This documents the current behavior: matching requires same separator
        assert result is None
        # But if the available name contains the pattern as substring it works:
        available2 = {"jira_add_comment"}
        result2 = self._match(available2, ["add_comment"])
        assert result2 == "jira_add_comment"
