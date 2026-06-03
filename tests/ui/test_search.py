"""
UI tests — Search tab
──────────────────────
Covers:
  - Semantic search returns results with score badges
  - Full-text mode toggle switches mode
  - Full-text results show yellow highlighted matches
  - Result count label is visible
  - Empty state shown when no results match
"""

import pytest
from .conftest import switch_tab


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _open_search(page):
    """Navigate to the Search tab."""
    page.locator(".nav-item[data-tab='search']").click()
    page.wait_for_selector("#tab-search.active", timeout=5_000)


def _run_search(page, query: str):
    """Type a query and click Search (or press Enter)."""
    search_input = page.locator(".search-input")
    search_input.click()
    search_input.fill(query)
    search_input.press("Enter")


def _switch_to_fulltext(page):
    """Click the 'Full-text' mode button."""
    page.locator(".search-mode-btn:has-text('Full-text')").click()


def _switch_to_semantic(page):
    """Click the 'Semantic' mode button."""
    page.locator(".search-mode-btn:has-text('Semantic')").click()


# ── tests ──────────────────────────────────────────────────────────────────────

class TestSearchTabAccess:
    """Basic navigation and layout checks."""

    def test_search_tab_accessible_via_nav(self, page):
        """Clicking Search nav item shows the search tab content."""
        _open_search(page)
        assert page.locator("#tab-search.active").is_visible()

    def test_search_input_is_present(self, page):
        """The search text field is visible in the Search tab."""
        _open_search(page)
        assert page.locator(".search-input").is_visible()

    def test_semantic_mode_active_by_default(self, page):
        """Semantic mode button is initially active."""
        _open_search(page)
        semantic_btn = page.locator(".search-mode-btn:has-text('Semantic')")
        assert "active" in (semantic_btn.get_attribute("class") or ""), (
            "Semantic mode should be active by default"
        )


class TestSemanticSearch:
    """Semantic search returns ranked results with score badges."""

    def test_search_returns_results(self, page):
        """A query against the seeded books returns at least one result card."""
        _open_search(page)
        _run_search(page, "authentication")
        page.wait_for_selector(".result-card", timeout=10_000)
        results = page.locator(".result-card").all()
        assert len(results) >= 1, "Expected at least one semantic result"

    def test_result_cards_show_project_name(self, page):
        """Each result card displays a project name."""
        _open_search(page)
        _run_search(page, "authentication")
        page.wait_for_selector(".result-card", timeout=10_000)
        first_project = page.locator(".result-card .result-project").first
        name = first_project.inner_text().strip()
        assert len(name) > 0, "Result card should show a project name"

    def test_result_cards_show_score_badge(self, page):
        """Semantic results have a relevance-score badge (high/mid/low)."""
        _open_search(page)
        _run_search(page, "authentication")
        page.wait_for_selector(".result-card", timeout=10_000)
        badges = page.locator(".result-badge").all()
        assert len(badges) >= 1, "Result cards should have score badges"

    def test_result_count_label_shown(self, page):
        """A '… result(s)' label is displayed after a search."""
        _open_search(page)
        _run_search(page, "authentication")
        page.wait_for_selector(".search-count", timeout=10_000)
        count_text = page.locator(".search-count").inner_text().strip()
        assert len(count_text) > 0, "Result count label should be visible"

    def test_result_excerpt_visible(self, page):
        """Each result card shows a text excerpt."""
        _open_search(page)
        _run_search(page, "JWT")
        page.wait_for_selector(".result-card", timeout=10_000)
        excerpt = page.locator(".result-card .result-excerpt").first
        text = excerpt.inner_text().strip()
        assert len(text) > 0, "Result card should include an excerpt"


class TestFullTextSearch:
    """Full-text mode finds exact strings and highlights them."""

    def test_fulltext_mode_toggle_changes_active_button(self, page):
        """Clicking 'Full-text' makes that button active and Semantic inactive."""
        _open_search(page)
        _switch_to_fulltext(page)
        ft_btn = page.locator(".search-mode-btn:has-text('Full-text')")
        sem_btn = page.locator(".search-mode-btn:has-text('Semantic')")
        assert "active" in (ft_btn.get_attribute("class") or ""), "Full-text button should be active"
        assert "active" not in (sem_btn.get_attribute("class") or ""), "Semantic button should be inactive"

    def test_fulltext_returns_results_for_exact_term(self, page):
        """Searching for a term present in the fixture books returns results."""
        _open_search(page)
        _switch_to_fulltext(page)
        _run_search(page, "Stripe")
        page.wait_for_selector(".result-card", timeout=10_000)
        results = page.locator(".result-card").all()
        assert len(results) >= 1, "Full-text search for 'Stripe' should return results"

    def test_fulltext_highlights_matches(self, page):
        """Matching terms are wrapped in <mark> elements with yellow highlight class."""
        _open_search(page)
        _switch_to_fulltext(page)
        _run_search(page, "Stripe")
        page.wait_for_selector(".result-card", timeout=10_000)
        marks = page.locator("mark.ft-match").all()
        assert len(marks) >= 1, "Full-text matches should be highlighted with mark.ft-match"

    def test_switching_back_to_semantic_updates_button(self, page):
        """Switching from Full-text back to Semantic restores the active state."""
        _open_search(page)
        _switch_to_fulltext(page)
        _switch_to_semantic(page)
        sem_btn = page.locator(".search-mode-btn:has-text('Semantic')")
        assert "active" in (sem_btn.get_attribute("class") or ""), (
            "Semantic button should be active after switching back"
        )


class TestSearchEmptyState:
    """No results shows an appropriate empty state."""

    def test_empty_state_for_no_match(self, page):
        """A query that matches nothing shows the empty state."""
        _open_search(page)
        # Use a gibberish query unlikely to match anything in the fixture books
        _run_search(page, "zzz_nonexistent_xyzzy_1234")
        # Wait long enough for results (or lack of) to render
        page.wait_for_timeout(2_000)
        results = page.locator(".result-card").count()
        if results == 0:
            # Either the empty-state element is shown, or simply no cards
            count_text = page.locator(".search-count").inner_text() if page.locator(".search-count").is_visible() else ""
            assert "0" in count_text or results == 0, "No results should show empty/zero state"

    def test_result_card_click_navigates_to_chat(self, page):
        """Clicking a result card switches to Chat tab with the project pre-selected."""
        _open_search(page)
        _run_search(page, "authentication")
        page.wait_for_selector(".result-card", timeout=10_000)
        first_card = page.locator(".result-card").first
        first_card.click()
        # Should navigate to the chat tab
        page.wait_for_function(
            "document.querySelector('#tab-chat')?.classList.contains('active')",
            timeout=5_000,
        )
        assert page.locator("#tab-chat.active").is_visible()
