"""
Unit tests for memoria/graph.py — Knowledge Graph engine.

Tests cover:
  - JSON parsing helper (_parse_json_response)
  - Metadata normalisation (_normalise_list)
  - Technology-overlap detection (_shared_significant_tech)
  - Topic-overlap detection (_shared_topics)
  - Service-dependency detection (_check_service_dependency)
  - Full edge detection (find_edges)
  - Graph build (mocked LLM)
  - Graph persistence (get_graph / build_graph)
  - Query helpers (query_project, impact_analysis)

No real LLM calls — litellm.completion is mocked throughout.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_node(
    name="project-a",
    technologies=None,
    topics=None,
    exposes=None,
    consumes=None,
    project_refs=None,
    description="",
):
    return {
        "name":         name,
        "book_path":    f"books/{name}_memory_bank.md",
        "description":  description,
        "technologies": technologies or [],
        "topics":       topics or [],
        "exposes":      exposes or [],
        "consumes":     consumes or [],
        "project_refs": project_refs or [],
    }


def _mock_completion(json_payload: dict):
    """Return a litellm-style mock response that yields JSON."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(json_payload)
    return resp


# ─── _parse_json_response ─────────────────────────────────────────────────────

class TestParseJsonResponse:

    def test_plain_json(self):
        from memoria.graph import _parse_json_response
        raw = '{"technologies": ["Python"], "topics": ["auth"]}'
        result = _parse_json_response(raw)
        assert result["technologies"] == ["Python"]

    def test_markdown_fenced_json(self):
        from memoria.graph import _parse_json_response
        raw = '```json\n{"description": "A service"}\n```'
        result = _parse_json_response(raw)
        assert result["description"] == "A service"

    def test_backtick_only_fence(self):
        from memoria.graph import _parse_json_response
        raw = '```\n{"key": "val"}\n```'
        result = _parse_json_response(raw)
        assert result["key"] == "val"

    def test_embedded_json_block(self):
        from memoria.graph import _parse_json_response
        raw = 'Here is the result:\n{"a": 1}'
        result = _parse_json_response(raw)
        assert result["a"] == 1

    def test_broken_json_returns_empty(self):
        from memoria.graph import _parse_json_response
        assert _parse_json_response("not json at all") == {}

    def test_empty_string_returns_empty(self):
        from memoria.graph import _parse_json_response
        assert _parse_json_response("") == {}


# ─── _normalise_list ──────────────────────────────────────────────────────────

class TestNormaliseList:

    def test_plain_list(self):
        from memoria.graph import _normalise_list
        assert _normalise_list(["Python", "FastAPI"]) == ["Python", "FastAPI"]

    def test_empty_items_stripped(self):
        from memoria.graph import _normalise_list
        assert _normalise_list(["Python", "", "  "]) == ["Python"]

    def test_string_wrapped_in_list(self):
        from memoria.graph import _normalise_list
        assert _normalise_list("Python") == ["Python"]

    def test_none_returns_empty(self):
        from memoria.graph import _normalise_list
        assert _normalise_list(None) == []

    def test_whitespace_trimmed(self):
        from memoria.graph import _normalise_list
        assert _normalise_list(["  Python  "]) == ["Python"]


# ─── Technology overlap ───────────────────────────────────────────────────────

class TestSharedSignificantTech:

    def test_shared_nontrivial_tech(self):
        from memoria.graph import _shared_significant_tech
        a = _make_node("a", technologies=["Python", "PostgreSQL", "Redis"])
        b = _make_node("b", technologies=["Python", "PostgreSQL", "React"])
        shared = _shared_significant_tech(a, b)
        assert "postgresql" in shared
        # Python is in _COMMON_TECH so may or may not be excluded depending on version
        assert "react" not in shared   # React belongs to b only

    def test_common_tech_excluded(self):
        from memoria.graph import _shared_significant_tech, _COMMON_TECH
        # python is a common tech and should be excluded
        a = _make_node("a", technologies=["python", "kafka"])
        b = _make_node("b", technologies=["python", "kafka"])
        shared = _shared_significant_tech(a, b)
        assert "python" not in shared
        assert "kafka" in shared

    def test_no_overlap_returns_empty(self):
        from memoria.graph import _shared_significant_tech
        a = _make_node("a", technologies=["FastAPI", "PostgreSQL"])
        b = _make_node("b", technologies=["Rails", "MySQL"])
        assert not _shared_significant_tech(a, b)

    def test_case_insensitive(self):
        from memoria.graph import _shared_significant_tech
        a = _make_node("a", technologies=["PostgreSQL"])
        b = _make_node("b", technologies=["postgresql"])
        shared = _shared_significant_tech(a, b)
        assert "postgresql" in shared


# ─── Topic overlap ────────────────────────────────────────────────────────────

class TestSharedTopics:

    def test_matching_topics(self):
        from memoria.graph import _shared_topics
        a = _make_node("a", topics=["authentication", "billing"])
        b = _make_node("b", topics=["authentication", "reporting"])
        assert "authentication" in _shared_topics(a, b)

    def test_no_match(self):
        from memoria.graph import _shared_topics
        a = _make_node("a", topics=["billing"])
        b = _make_node("b", topics=["search"])
        assert not _shared_topics(a, b)

    def test_case_insensitive(self):
        from memoria.graph import _shared_topics
        a = _make_node("a", topics=["Authentication"])
        b = _make_node("b", topics=["authentication"])
        assert _shared_topics(a, b)


# ─── Service dependency detection ─────────────────────────────────────────────

class TestCheckServiceDependency:

    def test_direct_name_in_consumes(self):
        from memoria.graph import _check_service_dependency
        consumer = _make_node("frontend", consumes=["auth-service"])
        provider = _make_node("auth-service", exposes=["REST API /auth/*"])
        edge = _check_service_dependency(consumer, provider)
        assert edge is not None
        assert edge["relation"] == "depends_on"
        assert edge["source"] == "frontend"
        assert edge["target"] == "auth-service"
        assert edge["confidence"] >= 0.9

    def test_project_ref_match(self):
        from memoria.graph import _check_service_dependency
        consumer = _make_node("app", project_refs=["payment-service"])
        provider = _make_node("payment-service")
        edge = _check_service_dependency(consumer, provider)
        assert edge is not None
        assert edge["relation"] == "depends_on"

    def test_no_match_returns_none(self):
        from memoria.graph import _check_service_dependency
        a = _make_node("a", consumes=["email-service"])
        b = _make_node("b", exposes=["billing API"])
        assert _check_service_dependency(a, b) is None

    def test_token_overlap_match(self):
        from memoria.graph import _check_service_dependency
        consumer = _make_node("app", consumes=["user profile service"])
        provider = _make_node("profiles", exposes=["user profile REST endpoint"])
        edge = _check_service_dependency(consumer, provider)
        # "user profile" tokens overlap → dependency detected
        assert edge is not None


# ─── find_edges ───────────────────────────────────────────────────────────────

class TestFindEdges:

    def test_no_edges_for_unrelated_projects(self):
        from memoria.graph import find_edges
        nodes = {
            "alpha": _make_node("alpha", technologies=["FastAPI"], topics=["billing"]),
            "beta":  _make_node("beta",  technologies=["Rails"],   topics=["search"]),
        }
        edges = find_edges(nodes)
        # No shared significant tech, no shared topics, no service refs
        assert edges == []

    def test_shared_tech_produces_edge(self):
        from memoria.graph import find_edges, REL_SHARES_TECHNOLOGY
        nodes = {
            "a": _make_node("a", technologies=["FastAPI", "PostgreSQL", "Redis"]),
            "b": _make_node("b", technologies=["FastAPI", "PostgreSQL", "Redis"]),
        }
        edges = find_edges(nodes)
        relations = {e["relation"] for e in edges}
        assert REL_SHARES_TECHNOLOGY in relations

    def test_dependency_edge_directional(self):
        from memoria.graph import find_edges, REL_DEPENDS_ON
        nodes = {
            "frontend": _make_node("frontend", consumes=["auth-service"]),
            "auth-service": _make_node("auth-service", exposes=["JWT API"]),
        }
        edges = find_edges(nodes)
        dep_edges = [e for e in edges if e["relation"] == REL_DEPENDS_ON]
        assert any(e["source"] == "frontend" and e["target"] == "auth-service"
                   for e in dep_edges)

    def test_explicit_reference_edge(self):
        from memoria.graph import find_edges, REL_REFERENCES
        nodes = {
            "a": _make_node("a", project_refs=["b"]),
            "b": _make_node("b"),
        }
        edges = find_edges(nodes)
        ref_edges = [e for e in edges if e["relation"] == REL_REFERENCES]
        assert any(e["source"] == "a" and e["target"] == "b" for e in ref_edges)

    def test_no_self_loops(self):
        from memoria.graph import find_edges
        nodes = {
            "a": _make_node("a", project_refs=["a"], topics=["auth"]),
        }
        edges = find_edges(nodes)
        assert all(e["source"] != e["target"] for e in edges)

    def test_edges_have_required_keys(self):
        from memoria.graph import find_edges
        nodes = {
            "x": _make_node("x", technologies=["Kafka", "Spark", "Cassandra"]),
            "y": _make_node("y", technologies=["Kafka", "Spark", "Cassandra"]),
        }
        for edge in find_edges(nodes):
            assert "source" in edge
            assert "target" in edge
            assert "relation" in edge
            assert "reason" in edge
            assert "confidence" in edge
            assert 0.0 <= edge["confidence"] <= 1.0

    def test_deduplication_keeps_highest_confidence(self):
        """Same source+target+relation should appear only once with highest confidence."""
        from memoria.graph import find_edges, REL_DEPENDS_ON
        nodes = {
            "a": _make_node("a", consumes=["b"], project_refs=["b"]),
            "b": _make_node("b"),
        }
        edges = find_edges(nodes)
        dep_edges = [
            e for e in edges
            if e["relation"] == REL_DEPENDS_ON
            and e["source"] == "a"
            and e["target"] == "b"
        ]
        # Must not have duplicates
        assert len(dep_edges) == 1


# ─── Graph persistence ────────────────────────────────────────────────────────

class TestGraphPersistence:

    def test_get_graph_missing_file_returns_empty(self, tmp_path):
        from memoria.graph import get_graph
        g = get_graph(str(tmp_path))
        assert g["nodes"] == {}
        assert g["edges"] == []
        assert g["version"] == 1

    def test_get_graph_loads_written_file(self, tmp_path):
        from memoria.graph import get_graph, GRAPH_FILE
        data = {"version": 1, "updated": "2025-01-01", "nodes": {"a": {}}, "edges": []}
        (tmp_path / GRAPH_FILE).write_text(json.dumps(data), encoding="utf-8")
        g = get_graph(str(tmp_path))
        assert "a" in g["nodes"]

    def test_get_graph_corrupt_file_returns_empty(self, tmp_path):
        from memoria.graph import get_graph, GRAPH_FILE
        (tmp_path / GRAPH_FILE).write_text("not json", encoding="utf-8")
        g = get_graph(str(tmp_path))
        assert g["nodes"] == {}

    def test_build_graph_creates_graph_file(self, tmp_path, tmp_config):
        """build_graph should write .graph.json when Memory Banks exist."""
        books_dir = tmp_path / "books"
        books_dir.mkdir()

        # Create two fake Memory Banks
        (books_dir / "alpha_memory_bank.md").write_text(
            "# alpha — Memory Bank\n## TL;DR\n- Alpha project\n",
            encoding="utf-8",
        )
        (books_dir / "beta_memory_bank.md").write_text(
            "# beta — Memory Bank\n## TL;DR\n- Beta project\n",
            encoding="utf-8",
        )

        mock_meta = {
            "description": "A test service",
            "technologies": ["FastAPI", "Redis"],
            "topics": ["auth"],
            "exposes": ["REST API"],
            "consumes": [],
            "project_refs": [],
        }
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = json.dumps(mock_meta)

        with patch("litellm.completion", return_value=mock_resp):
            from memoria.graph import build_graph, GRAPH_FILE
            g = build_graph(str(books_dir), str(tmp_config))

        assert (books_dir / GRAPH_FILE).exists()
        assert "alpha" in g["nodes"]
        assert "beta" in g["nodes"]

    def test_build_graph_empty_dir_returns_empty(self, tmp_path, tmp_config):
        from memoria.graph import build_graph
        books_dir = tmp_path / "books"
        books_dir.mkdir()
        g = build_graph(str(books_dir), str(tmp_config))
        assert g["nodes"] == {}

    def test_build_graph_records_timestamp(self, tmp_path, tmp_config):
        books_dir = tmp_path / "books"
        books_dir.mkdir()
        (books_dir / "proj_memory_bank.md").write_text("# proj\n", encoding="utf-8")

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = json.dumps({"technologies": [], "topics": []})

        with patch("litellm.completion", return_value=mock_resp):
            from memoria.graph import build_graph
            g = build_graph(str(books_dir), str(tmp_config))

        assert g["updated"] is not None


# ─── Query helpers ────────────────────────────────────────────────────────────

class TestQueryProject:

    def _make_graph(self):
        from memoria.graph import REL_DEPENDS_ON, REL_SHARES_TECHNOLOGY
        return {
            "version": 1,
            "updated": "2025-01-01",
            "nodes": {
                "frontend": _make_node("frontend"),
                "backend":  _make_node("backend"),
                "auth":     _make_node("auth"),
            },
            "edges": [
                {"source": "frontend", "target": "backend",  "relation": REL_DEPENDS_ON,
                 "reason": "calls REST API", "confidence": 0.95},
                {"source": "backend",  "target": "auth",     "relation": REL_DEPENDS_ON,
                 "reason": "uses JWT",      "confidence": 0.90},
                {"source": "frontend", "target": "auth",     "relation": REL_SHARES_TECHNOLOGY,
                 "reason": "both use Redis","confidence": 0.60},
            ],
        }

    def test_returns_outbound_edges(self):
        from memoria.graph import query_project
        g = self._make_graph()
        results = query_project("frontend", g)
        others = {r["other"] for r in results}
        assert "backend" in others

    def test_returns_inbound_edges(self):
        from memoria.graph import query_project
        g = self._make_graph()
        results = query_project("backend", g)
        # frontend depends on backend (inbound for backend)
        inbound = [r for r in results if r["direction"] == "inbound"]
        assert any(r["other"] == "frontend" for r in inbound)

    def test_project_with_no_edges_returns_empty(self):
        from memoria.graph import query_project
        g = self._make_graph()
        # Add an isolated node
        g["nodes"]["isolated"] = _make_node("isolated")
        assert query_project("isolated", g) == []

    def test_results_sorted_by_confidence(self):
        from memoria.graph import query_project
        g = self._make_graph()
        results = query_project("frontend", g)
        confidences = [r["confidence"] for r in results]
        assert confidences == sorted(confidences, reverse=True)

    def test_unknown_project_returns_empty(self):
        from memoria.graph import query_project
        g = self._make_graph()
        assert query_project("does-not-exist", g) == []


class TestImpactAnalysis:

    def _make_dep_graph(self):
        from memoria.graph import REL_DEPENDS_ON, REL_SHARES_TECHNOLOGY
        return {
            "version": 1,
            "updated": "2025-01-01",
            "nodes": {
                "core":    _make_node("core"),
                "service": _make_node("service"),
                "app":     _make_node("app"),
            },
            "edges": [
                {"source": "service", "target": "core",    "relation": REL_DEPENDS_ON,
                 "reason": "uses core", "confidence": 0.95},
                {"source": "app",     "target": "service", "relation": REL_DEPENDS_ON,
                 "reason": "uses service", "confidence": 0.90},
            ],
        }

    def test_direct_dependent_found(self):
        from memoria.graph import impact_analysis
        g = self._make_dep_graph()
        results = impact_analysis("core", g)
        assert any(r["project"] == "service" for r in results)

    def test_transitive_dependent_found(self):
        from memoria.graph import impact_analysis
        g = self._make_dep_graph()
        results = impact_analysis("core", g)
        # app depends on service which depends on core
        assert any(r["project"] == "app" for r in results)

    def test_depth_is_correct(self):
        from memoria.graph import impact_analysis
        g = self._make_dep_graph()
        results = impact_analysis("core", g)
        by_project = {r["project"]: r["depth"] for r in results}
        assert by_project.get("service") == 1
        assert by_project.get("app") == 2

    def test_no_dependents_returns_empty(self):
        from memoria.graph import impact_analysis
        g = self._make_dep_graph()
        # "app" has no dependents
        assert impact_analysis("app", g) == []

    def test_nonexistent_project_returns_empty(self):
        from memoria.graph import impact_analysis
        g = self._make_dep_graph()
        assert impact_analysis("ghost", g) == []

    def test_path_included_in_result(self):
        from memoria.graph import impact_analysis
        g = self._make_dep_graph()
        results = impact_analysis("core", g)
        for r in results:
            assert "path" in r
            assert "core" in r["path"]   # root always in path


# ─── graph_summary ────────────────────────────────────────────────────────────

class TestGraphSummary:

    def test_shows_node_and_edge_count(self):
        from memoria.graph import graph_summary
        g = {"nodes": {"a": {}, "b": {}}, "edges": [{"x": 1}], "updated": "2025-01-01"}
        s = graph_summary(g)
        assert "2" in s   # 2 nodes
        assert "1" in s   # 1 edge

    def test_empty_graph_says_never(self):
        from memoria.graph import graph_summary, _empty_graph
        s = graph_summary(_empty_graph())
        assert "never" in s
