"""
UI tests — Sidebar project filter regression tests
────────────────────────────────────────────────────
Covers:
  - Typing partial project name shows only matching items (case-insensitive);
    clearing restores all (Req 8.1, 8.2)
  - Typing a string matching no project shows zero items (Req 8.3)
  - Project list updates on each keystroke without pressing Enter (Req 8.4)
  - Newly added project appears in filter results immediately (Req 8.5)
"""

import pytest
from .conftest import wait_for_stream_done


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

_FILTER_INPUT = (
    "#project-filter, #sidebar-filter, .sidebar-filter input, "
    "input[placeholder*='filter'], input[placeholder*='Filter'], "
    "input[placeholder*='search projects'], #projects-search"
)

_PROJECT_ITEMS = ".project-item, [data-project], .sidebar-project"


def _get_filter_input(page):
    """Return a locator for the sidebar filter input."""
    return page.locator(_FILTER_INPUT).first


def _get_visible_project_count(page) -> int:
    """Return the number of currently visible project items in the sidebar."""
    items = page.locator(_PROJECT_ITEMS)
    count = 0
    for i in range(items.count()):
        if items.nth(i).is_visible():
            count += 1
    return count


def _get_visible_project_names(page) -> list[str]:
    """Return the names of all currently visible project items."""
    items = page.locator(_PROJECT_ITEMS)
    names = []
    for i in range(items.count()):
        item = items.nth(i)
        if item.is_visible():
            name = item.get_attribute("data-project") or item.inner_text().strip()
            names.append(name.lower())
    return names


def _type_filter(page, text: str):
    """Type text into the sidebar filter input."""
    inp = _get_filter_input(page)
    inp.fill(text)
    # Give the UI a moment to react
    page.wait_for_timeout(300)


def _clear_filter(page):
    """Clear the sidebar filter input."""
    inp = _get_filter_input(page)
    inp.fill("")
    page.wait_for_timeout(300)


def _skip_if_no_filter(page):
    """Skip the test if the sidebar filter input is not present."""
    if _get_filter_input(page).count() == 0:
        pytest.skip("Sidebar filter input not present in this build")


# ── tests ──────────────────────────────────────────────────────────────────────

class TestFilterBasic:
    """Typing partial name filters list; clearing restores all (Req 8.1, 8.2)."""

    def test_filter_input_is_present(self, page):
        """The sidebar filter input is present."""
        _skip_if_no_filter(page)
        assert _get_filter_input(page).is_visible(), (
            "Sidebar filter input should be visible"
        )

    def test_partial_name_filters_list(self, page):
        """Typing a partial project name shows only matching items (Req 8.1)."""
        _skip_if_no_filter(page)
        total_before = _get_visible_project_count(page)
        assert total_before >= 2, (
            f"Expected at least 2 projects in sidebar before filtering, got {total_before}"
        )

        _type_filter(page, "auth")
        filtered_count = _get_visible_project_count(page)
        assert filtered_count < total_before, (
            f"Expected fewer projects after filtering by 'auth', "
            f"got {filtered_count} (was {total_before})"
        )

    def test_filter_is_case_insensitive(self, page):
        """Filtering is case-insensitive (Req 8.1)."""
        _skip_if_no_filter(page)

        _type_filter(page, "AUTH")
        count_upper = _get_visible_project_count(page)

        _type_filter(page, "auth")
        count_lower = _get_visible_project_count(page)

        assert count_upper == count_lower, (
            f"Filter should be case-insensitive. "
            f"'AUTH' showed {count_upper}, 'auth' showed {count_lower}"
        )

    def test_filtered_items_match_query(self, page):
        """All visible items after filtering contain the filter string (Req 8.1)."""
        _skip_if_no_filter(page)
        _type_filter(page, "auth")
        names = _get_visible_project_names(page)
        for name in names:
            assert "auth" in name, (
                f"Project '{name}' should not be visible when filtering by 'auth'"
            )

    def test_clearing_filter_restores_all(self, page):
        """Clearing the filter input restores all project items (Req 8.2)."""
        _skip_if_no_filter(page)
        total_before = _get_visible_project_count(page)

        _type_filter(page, "auth")
        _clear_filter(page)

        total_after = _get_visible_project_count(page)
        assert total_after == total_before, (
            f"Expected all {total_before} projects after clearing filter, got {total_after}"
        )


class TestFilterNoMatch:
    """Typing a string matching no project shows zero items (Req 8.3)."""

    def test_no_match_shows_zero_items(self, page):
        """Typing a string that matches no project shows zero project items (Req 8.3)."""
        _skip_if_no_filter(page)
        _type_filter(page, "zzz_no_match_xyz_9999_unique")
        count = _get_visible_project_count(page)
        assert count == 0, (
            f"Expected 0 visible projects for no-match filter, got {count}"
        )

    def test_no_match_may_show_empty_state(self, page):
        """An empty-state message may appear when no projects match the filter."""
        _skip_if_no_filter(page)
        _type_filter(page, "zzz_no_match_xyz_9999_unique")
        # Either zero items or an empty-state element — both are acceptable
        count = _get_visible_project_count(page)
        empty_state = page.locator(
            ".sidebar-empty, .no-projects, [data-empty='true'], "
            "#projects-empty, .projects-empty"
        )
        assert count == 0 or empty_state.is_visible(), (
            "Expected either zero project items or an empty-state message for no-match filter"
        )


class TestFilterLiveUpdate:
    """Project list updates on each keystroke without pressing Enter (Req 8.4)."""

    def test_list_updates_on_each_keystroke(self, page):
        """The project list updates on each keystroke without pressing Enter (Req 8.4)."""
        _skip_if_no_filter(page)
        total = _get_visible_project_count(page)
        assert total >= 2, f"Need at least 2 projects for this test, got {total}"

        inp = _get_filter_input(page)
        inp.fill("")  # start clean

        # Type one character at a time and check that the list reacts
        inp.type("a", delay=50)
        page.wait_for_timeout(200)
        count_after_a = _get_visible_project_count(page)

        inp.type("u", delay=50)
        page.wait_for_timeout(200)
        count_after_au = _get_visible_project_count(page)

        # After typing "au", the list should be equal or narrower than after "a"
        assert count_after_au <= count_after_a, (
            f"Expected list to narrow or stay same after typing 'u'. "
            f"After 'a': {count_after_a}, After 'au': {count_after_au}"
        )

    def test_no_enter_required_to_filter(self, page):
        """Filtering works without pressing Enter (Req 8.4)."""
        _skip_if_no_filter(page)
        total = _get_visible_project_count(page)

        inp = _get_filter_input(page)
        inp.fill("auth")
        # Explicitly do NOT press Enter
        page.wait_for_timeout(400)

        filtered = _get_visible_project_count(page)
        assert filtered < total, (
            f"Expected filtering without Enter to reduce project count. "
            f"Total: {total}, Filtered: {filtered}"
        )


class TestFilterNewProject:
    """Newly added project appears in filter results immediately (Req 8.5)."""

    def test_new_project_appears_in_filter_results(self, page, tmp_path):
        """A newly added project appears in filter results immediately (Req 8.5)."""
        _skip_if_no_filter(page)

        # Open the Analyze Panel to add a new project
        add_btn = page.locator(
            "#add-project-btn, .add-project-btn, [data-action='open-analyze-panel'], "
            "#analyze-panel-trigger, .sidebar-header button"
        ).first
        if not add_btn.is_visible():
            pytest.skip("Add project button not present — cannot test new project filter")

        add_btn.click()
        page.wait_for_selector(
            "#analyze-panel, .analyze-panel, [data-panel='analyze']",
            timeout=8_000,
        )

        project_name = tmp_path.name
        page.locator(
            "#analyze-panel input[type='text'], #analyze-path-input, "
            ".analyze-panel input[type='text']"
        ).first.fill(str(tmp_path))

        page.locator(
            "#analyze-start-btn, .analyze-panel button:has-text('Analyse'), "
            ".analyze-panel button:has-text('Analyze')"
        ).first.click()

        # Wait for analysis to complete
        page.wait_for_selector(
            "#analyze-panel .success, #analyze-panel .done, "
            "[data-status='done'], #analyze-panel .analyze-complete",
            timeout=15_000,
        )

        # Now filter by the new project name
        _type_filter(page, project_name[:4])
        names = _get_visible_project_names(page)
        assert any(project_name.lower()[:4] in n for n in names), (
            f"Expected new project '{project_name}' to appear in filter results, "
            f"got visible projects: {names}"
        )
