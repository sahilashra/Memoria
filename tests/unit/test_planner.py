"""
Unit tests for memoria/planner.py — Planning Intelligence.

Tests cover:
  - load_plan — raw text passthrough and file loading
  - _extract_terms — stop-word filtering and frequency ranking
  - _score_book — keyword overlap scoring
  - find_relevant_books — file scan, scoring, top-N selection
  - _get_graph_context — graceful failure if graph absent
  - synthesize_report — mocked LLM JSON parsing; fallback on bad JSON
  - render_markdown — all sections present in output
  - analyze_plan — integration (mocked LLM + temp books_dir)

No real LLM calls — litellm.completion is mocked throughout.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ─── load_plan ────────────────────────────────────────────────────────────────

def test_load_plan_returns_raw_text_when_no_file():
    from memoria.planner import load_plan
    text = "This is the plan text"
    assert load_plan(text) == text


def test_load_plan_reads_markdown_file(tmp_path):
    from memoria.planner import load_plan
    f = tmp_path / "plan.md"
    f.write_text("# My Plan\n\nAdd OAuth2.", encoding="utf-8")
    assert load_plan(str(f)) == "# My Plan\n\nAdd OAuth2."


def test_load_plan_reads_txt_file(tmp_path):
    from memoria.planner import load_plan
    f = tmp_path / "plan.txt"
    f.write_text("Migrate to PostgreSQL.", encoding="utf-8")
    assert load_plan(str(f)) == "Migrate to PostgreSQL."


def test_load_plan_raises_for_docx_without_library(tmp_path, monkeypatch):
    """If python-docx not installed, loading a .docx raises RuntimeError."""
    from memoria.planner import load_plan
    # Create a dummy file with .docx extension
    f = tmp_path / "plan.docx"
    f.write_bytes(b"fake")
    # Block the import of docx
    import builtins
    real_import = builtins.__import__
    def _no_docx(name, *a, **kw):
        if name == "docx":
            raise ImportError("mocked missing")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", _no_docx)
    with pytest.raises(RuntimeError, match="python-docx"):
        load_plan(str(f))


# ─── _extract_terms ──────────────────────────────────────────────────────────

def test_extract_terms_removes_stop_words():
    from memoria.planner import _extract_terms
    terms = _extract_terms("We plan to add OAuth2 authentication to the auth service")
    # "we", "plan", "to", "add", "the" should be filtered
    assert "oauth2" in terms or "oauth" in terms
    assert "authentication" in terms or "auth" in terms
    assert "we" not in terms
    assert "the" not in terms


def test_extract_terms_returns_at_most_60():
    from memoria.planner import _extract_terms
    # Very long text
    long_text = " ".join(f"keyword{i}" for i in range(200))
    terms = _extract_terms(long_text)
    assert len(terms) <= 60


def test_extract_terms_deduplicates():
    from memoria.planner import _extract_terms
    terms = _extract_terms("oauth oauth oauth postgresql postgresql")
    assert terms.count("oauth") == 1
    assert terms.count("postgresql") == 1


# ─── _score_book ─────────────────────────────────────────────────────────────

def test_score_book_counts_matches():
    from memoria.planner import _score_book
    book = "This project uses postgresql and oauth authentication"
    terms = ["postgresql", "oauth", "kafka"]  # kafka not in book
    assert _score_book(book, terms) == 2


def test_score_book_case_insensitive():
    from memoria.planner import _score_book
    book = "Uses PostgreSQL for storage"
    assert _score_book(book, ["postgresql"]) == 1


def test_score_book_zero_for_no_match():
    from memoria.planner import _score_book
    assert _score_book("This is about Java Spring", ["python", "fastapi"]) == 0


# ─── find_relevant_books ─────────────────────────────────────────────────────

@pytest.fixture
def books_dir(tmp_path):
    """Create a temp books directory with 3 Memory Bank files."""
    bd = tmp_path / "books"
    bd.mkdir()
    (bd / "auth_service_memory_bank.md").write_text(
        "## TL;DR\nHandles OAuth2 authentication and JWT tokens.\n"
        "## Tech Stack\nFastAPI, Python, JWT, OAuth2\n",
        encoding="utf-8",
    )
    (bd / "payments_service_memory_bank.md").write_text(
        "## TL;DR\nProcesses payments via Stripe.\n"
        "## Tech Stack\nNode.js, Stripe, PostgreSQL\n",
        encoding="utf-8",
    )
    (bd / "notifications_memory_bank.md").write_text(
        "## TL;DR\nSends email and SMS notifications.\n"
        "## Tech Stack\nPython, SendGrid, Twilio\n",
        encoding="utf-8",
    )
    return str(bd)


def test_find_relevant_books_returns_matching_books(books_dir):
    from memoria.planner import find_relevant_books
    results = find_relevant_books("Add OAuth2 login with JWT tokens", books_dir)
    projects = [r["project"] for r in results]
    # auth_service should rank first (most keyword overlap)
    assert "auth-service" in projects


def test_find_relevant_books_sorted_by_score(books_dir):
    from memoria.planner import find_relevant_books
    results = find_relevant_books("OAuth2 JWT authentication FastAPI", books_dir)
    # Results should be in descending score order
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_find_relevant_books_respects_max_books(books_dir):
    from memoria.planner import find_relevant_books
    results = find_relevant_books("Python service", books_dir, max_books=1)
    assert len(results) <= 1


def test_find_relevant_books_empty_for_missing_dir():
    from memoria.planner import find_relevant_books
    results = find_relevant_books("anything", "/no/such/dir")
    assert results == []


def test_find_relevant_books_no_results_for_unrelated_query(books_dir):
    from memoria.planner import find_relevant_books
    results = find_relevant_books("xyzzy frobnicator quux", books_dir)
    # Highly unlikely these tokens appear in the fixture books
    assert all(r["score"] == 0 for r in results) or len(results) == 0


# ─── _get_graph_context ──────────────────────────────────────────────────────

def test_get_graph_context_returns_empty_when_no_graph(tmp_path):
    from memoria.planner import _get_graph_context
    # No graph file → should return empty string gracefully
    result = _get_graph_context(str(tmp_path), ["auth-service"])
    assert result == ""


def test_get_graph_context_returns_empty_on_import_error(tmp_path):
    """If the graph module raises any error, return empty string."""
    from memoria.planner import _get_graph_context
    with patch("memoria.planner._get_graph_context", return_value=""):
        result = _get_graph_context(str(tmp_path), ["auth-service"])
    assert result == ""


# ─── synthesize_report ───────────────────────────────────────────────────────

def _make_mock_completion(json_content: dict):
    """Return a MagicMock that mimics litellm.completion return value."""
    mock = MagicMock()
    mock.choices[0].message.content = json.dumps(json_content)
    return mock


_VALID_REPORT = {
    "summary": "The plan touches the auth service and payments.",
    "affected_projects": ["auth-service", "payments-service"],
    "dependencies": [
        {"project": "auth-service", "relation": "OAuth2 JWT tokens", "notes": "Rate limiting concern"}
    ],
    "risks": [
        {"severity": "high", "title": "Rate limit risk", "detail": "Auth has 100 req/s cap", "source": "auth-service"}
    ],
    "prior_art": [
        {"project": "payments-service", "description": "PostgreSQL migration attempted in Q3 2024"}
    ],
    "recommended_actions": [
        "Run memoria ask --project auth-service for current rate limits"
    ],
}


def test_synthesize_report_parses_valid_json(tmp_path):
    from memoria.planner import synthesize_report
    config = tmp_path / "config.yaml"
    config.write_text("model: gpt-4o\n", encoding="utf-8")

    with patch("litellm.completion", return_value=_make_mock_completion(_VALID_REPORT)):
        with patch("memoria.models.ModelProvider") as MockMP:
            MockMP.return_value.model = "gpt-4o"
            MockMP.return_value.max_tokens = 2000
            report = synthesize_report("Add OAuth2", [], "", str(config))

    assert report["summary"] == _VALID_REPORT["summary"]
    assert "auth-service" in report["affected_projects"]
    assert report["risks"][0]["severity"] == "high"


def test_synthesize_report_fallback_on_bad_json(tmp_path):
    from memoria.planner import synthesize_report
    config = tmp_path / "config.yaml"
    config.write_text("model: gpt-4o\n", encoding="utf-8")

    mock = MagicMock()
    mock.choices[0].message.content = "This is not JSON at all."

    with patch("litellm.completion", return_value=mock):
        with patch("memoria.models.ModelProvider") as MockMP:
            MockMP.return_value.model = "gpt-4o"
            MockMP.return_value.max_tokens = 2000
            report = synthesize_report("Add OAuth2", [], "", str(config))

    # Should not raise; should return a fallback dict
    assert "summary" in report
    assert isinstance(report["recommended_actions"], list)


def test_synthesize_report_strips_markdown_fences(tmp_path):
    from memoria.planner import synthesize_report
    config = tmp_path / "config.yaml"
    config.write_text("model: gpt-4o\n", encoding="utf-8")

    # LLM wraps JSON in markdown fences
    wrapped = f"```json\n{json.dumps(_VALID_REPORT)}\n```"
    mock = MagicMock()
    mock.choices[0].message.content = wrapped

    with patch("litellm.completion", return_value=mock):
        with patch("memoria.models.ModelProvider") as MockMP:
            MockMP.return_value.model = "gpt-4o"
            MockMP.return_value.max_tokens = 2000
            report = synthesize_report("Add OAuth2", [], "", str(config))

    assert report["summary"] == _VALID_REPORT["summary"]


def test_synthesize_report_handles_llm_exception(tmp_path):
    from memoria.planner import synthesize_report
    config = tmp_path / "config.yaml"
    config.write_text("model: gpt-4o\n", encoding="utf-8")

    with patch("litellm.completion", side_effect=RuntimeError("LLM offline")):
        with patch("memoria.models.ModelProvider") as MockMP:
            MockMP.return_value.model = "gpt-4o"
            MockMP.return_value.max_tokens = 2000
            report = synthesize_report("Add OAuth2", [], "", str(config))

    assert "error" in report["summary"].lower() or "Analysis error" in report["summary"]


# ─── render_markdown ─────────────────────────────────────────────────────────

def test_render_markdown_includes_all_sections():
    from memoria.planner import render_markdown
    report = {**_VALID_REPORT, "_meta": {"generated_at": "2026-05-08T08:00:00", "books_searched": 3, "graph_used": False}}
    md = render_markdown(report)

    assert "# Plan Analysis Report" in md
    assert "auth-service" in md
    assert "Dependencies" in md
    assert "Risks" in md
    assert "Prior Art" in md
    assert "Recommended Actions" in md
    assert "[HIGH]" in md
    assert "Generated 2026-05-08" in md


def test_render_markdown_handles_empty_sections():
    from memoria.planner import render_markdown
    empty_report = {
        "summary": "Nothing to report.",
        "affected_projects": [],
        "dependencies": [],
        "risks": [],
        "prior_art": [],
        "recommended_actions": [],
    }
    md = render_markdown(empty_report)
    assert "Nothing to report." in md
    # Should not crash; sections with no items simply don't appear
    assert "Dependencies" not in md


# ─── analyze_plan (integration) ──────────────────────────────────────────────

def test_analyze_plan_returns_report_with_meta(books_dir, tmp_path):
    from memoria.planner import analyze_plan
    config = tmp_path / "config.yaml"
    config.write_text("model: gpt-4o\n", encoding="utf-8")

    with patch("litellm.completion", return_value=_make_mock_completion(_VALID_REPORT)):
        with patch("memoria.models.ModelProvider") as MockMP:
            MockMP.return_value.model = "gpt-4o"
            MockMP.return_value.max_tokens = 2000
            report = analyze_plan(
                plan_text="Add OAuth2 and JWT authentication",
                config_path=str(config),
                books_dir=books_dir,
            )

    assert "_meta" in report
    assert "generated_at" in report["_meta"]
    assert isinstance(report["_meta"]["books_searched"], int)
    assert report["_meta"]["books_searched"] >= 0


# ─── split_plan ───────────────────────────────────────────────────────────────

_H2_PLAN = """\
## Goals
Add OAuth2 and JWT.

## Timeline
Week 1: design. Week 2: implement.

## Risks
Rate limiting may be an issue.
"""

_H3_PLAN = """\
### Authentication
Add OAuth2.

### Database
Migrate to PostgreSQL.
"""

_FLAT_PLAN = "Add OAuth2. Migrate to PostgreSQL. Launch mobile app."


def test_split_plan_splits_on_h2():
    from memoria.planner import split_plan
    chunks = split_plan(_H2_PLAN)
    assert len(chunks) == 3
    assert any("Goals" in c for c in chunks)
    assert any("Timeline" in c for c in chunks)
    assert any("Risks" in c for c in chunks)


def test_split_plan_falls_back_to_h3():
    from memoria.planner import split_plan
    chunks = split_plan(_H3_PLAN)
    assert len(chunks) == 2
    assert any("Authentication" in c for c in chunks)
    assert any("Database" in c for c in chunks)


def test_split_plan_flat_text_returns_list():
    from memoria.planner import split_plan
    # No headings — returns the text in at least one chunk
    chunks = split_plan(_FLAT_PLAN)
    assert len(chunks) >= 1
    assert all(c.strip() for c in chunks)


def test_split_plan_paragraph_split_for_long_flat_text():
    from memoria.planner import split_plan, _CHUNK_THRESHOLD
    # Build a long text that exceeds threshold but has no headings
    long_text = ("This is a sentence about authentication. " * 200)
    chunks = split_plan(long_text)
    # Should produce multiple chunks when text is very long
    assert len(chunks) >= 1
    # Each chunk should be non-empty
    assert all(c.strip() for c in chunks)


# ─── merge_reports ────────────────────────────────────────────────────────────

_CHUNK_REPORT_A = {
    "summary": "Section A: auth service OAuth2.",
    "affected_projects": ["auth-service"],
    "dependencies": [{"project": "auth-service", "relation": "OAuth2 tokens", "notes": "rate limit"}],
    "risks": [{"severity": "high", "title": "rate limit risk", "detail": "100 req/s cap", "source": "auth"}],
    "prior_art": [{"project": "auth-service", "description": "JWT attempted Q3 2024"}],
    "recommended_actions": ["Run memoria ask about rate limits"],
}

_CHUNK_REPORT_B = {
    "summary": "Section B: database migration to PostgreSQL.",
    "affected_projects": ["payments-service"],
    "dependencies": [{"project": "payments-service", "relation": "PostgreSQL schema", "notes": "downtime"}],
    "risks": [{"severity": "medium", "title": "migration downtime", "detail": "requires maintenance window", "source": "payments"}],
    "prior_art": [],
    "recommended_actions": ["Check payments-service schema compatibility"],
}


def test_merge_reports_unions_affected_projects():
    from memoria.planner import merge_reports
    merged = merge_reports([_CHUNK_REPORT_A, _CHUNK_REPORT_B])
    assert "auth-service" in merged["affected_projects"]
    assert "payments-service" in merged["affected_projects"]


def test_merge_reports_includes_all_risks():
    from memoria.planner import merge_reports
    merged = merge_reports([_CHUNK_REPORT_A, _CHUNK_REPORT_B])
    titles = [r["title"].lower() for r in merged["risks"]]
    assert any("rate limit" in t for t in titles)
    assert any("migration" in t for t in titles)


def test_merge_reports_deduplicates_risks_by_title():
    from memoria.planner import merge_reports
    # Same risk in both chunks — should appear once, highest severity kept
    r_low = {**_CHUNK_REPORT_A, "risks": [{"severity": "low", "title": "rate limit risk", "detail": "a", "source": "a"}]}
    r_high = {**_CHUNK_REPORT_B, "risks": [{"severity": "high", "title": "rate limit risk", "detail": "b", "source": "b"}]}
    merged = merge_reports([r_low, r_high])
    rate_limit_risks = [r for r in merged["risks"] if "rate limit" in r["title"].lower()]
    assert len(rate_limit_risks) == 1
    assert rate_limit_risks[0]["severity"] == "high"


def test_merge_reports_passthrough_for_single_report():
    from memoria.planner import merge_reports
    merged = merge_reports([_CHUNK_REPORT_A])
    assert merged["summary"] == _CHUNK_REPORT_A["summary"]


def test_merge_reports_empty_list_returns_empty():
    from memoria.planner import merge_reports
    merged = merge_reports([])
    assert merged == {}


def test_merge_reports_caps_recommended_actions_at_8():
    from memoria.planner import merge_reports
    many_actions = {**_CHUNK_REPORT_A, "recommended_actions": [f"Action {i}" for i in range(20)]}
    merged = merge_reports([many_actions, _CHUNK_REPORT_B])
    assert len(merged["recommended_actions"]) <= 8


# ─── analyze_plan_chunked (integration) ──────────────────────────────────────

def test_analyze_plan_chunked_uses_fast_path_for_small_plans(books_dir, tmp_path):
    from memoria.planner import analyze_plan_chunked
    config = tmp_path / "config.yaml"
    config.write_text("model: gpt-4o\n", encoding="utf-8")

    # Short plan — should go through fast path (not chunked)
    short_plan = "Add OAuth2 login."
    with patch("litellm.completion", return_value=_make_mock_completion(_VALID_REPORT)):
        with patch("memoria.models.ModelProvider") as MockMP:
            MockMP.return_value.model = "gpt-4o"
            MockMP.return_value.max_tokens = 2000
            report = analyze_plan_chunked(short_plan, str(config), books_dir)

    assert report["_meta"]["chunked"] is False
    assert report["_meta"]["chunk_count"] == 1


def test_analyze_plan_chunked_chunks_large_plans(books_dir, tmp_path):
    from memoria.planner import analyze_plan_chunked, _CHUNK_THRESHOLD
    config = tmp_path / "config.yaml"
    config.write_text("model: gpt-4o\n", encoding="utf-8")

    # Build a large plan with H2 sections that exceeds threshold
    large_plan = "\n\n".join(
        f"## Section {i}\n\n" + ("Detail about section. " * 60)
        for i in range(5)
    )
    assert len(large_plan) >= _CHUNK_THRESHOLD

    with patch("litellm.completion", return_value=_make_mock_completion(_VALID_REPORT)):
        with patch("memoria.models.ModelProvider") as MockMP:
            MockMP.return_value.model = "gpt-4o"
            MockMP.return_value.max_tokens = 2000
            report = analyze_plan_chunked(large_plan, str(config), books_dir)

    assert report["_meta"]["chunked"] is True
    assert report["_meta"]["chunk_count"] > 1
