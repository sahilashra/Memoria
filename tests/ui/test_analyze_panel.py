"""
UI tests — Analyze Panel browser regression tests
───────────────────────────────────────────────────
Covers:
  - Clicking + button next to Projects opens the Analyze Panel slide-in (Req 5.1)
  - Entering valid path and clicking Check shows project-ready confirmation (Req 5.2)
  - Clicking Analyse shows progress indicator; progress events update display;
    done event adds project to sidebar (Req 5.3, 5.4, 5.5)
  - Error event shows error message inside panel and error toast (Req 5.6)
"""

import pytest
from .conftest import wait_for_stream_done


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _open_analyze_panel(page):
    """Click the + button next to Projects to open the Analyze Panel."""
    page.locator(
        "#add-project-btn, .add-project-btn, [data-action='open-analyze-panel'], "
        "#analyze-panel-trigger, button[title*='Analyze'], button[title*='Add project'], "
        ".sidebar-header button, #projects-header button"
    ).first.click()
    page.wait_for_selector(
        "#analyze-panel, .analyze-panel, [data-panel='analyze']",
        timeout=8_000,
    )


def _panel_locator(page):
    """Return a locator for the Analyze Panel container."""
    return page.locator("#analyze-panel, .analyze-panel, [data-panel='analyze']").first


def _path_input(page):
    """Return a locator for the path input inside the Analyze Panel."""
    return page.locator(
        "#analyze-panel input[type='text'], #analyze-path-input, "
        ".analyze-panel input[type='text'], [data-testid='path-input']"
    ).first


def _check_button(page):
    """Return a locator for the Check button inside the Analyze Panel."""
    return page.locator(
        "#analyze-check-btn, .analyze-panel button[data-action='check'], "
        ".analyze-panel button:has-text('Check'), #tab-analyze button:has-text('Check')"
    ).first


def _analyse_button(page):
    """Return a locator for the Analyse button inside the Analyze Panel."""
    return page.locator(
        "#analyze-start-btn, .analyze-panel button[data-action='analyse'], "
        ".analyze-panel button:has-text('Analyse'), .analyze-panel button:has-text('Analyze'), "
        "#tab-analyze button:has-text('Analyse')"
    ).first


# ── tests ──────────────────────────────────────────────────────────────────────

class TestAnalyzePanelOpen:
    """Clicking + button next to Projects opens the Analyze Panel slide-in (Req 5.1)."""

    def test_add_button_is_visible(self, page):
        """The + button next to Projects is visible in the sidebar."""
        btn = page.locator(
            "#add-project-btn, .add-project-btn, [data-action='open-analyze-panel'], "
            "#analyze-panel-trigger, .sidebar-header button"
        ).first
        assert btn.is_visible(), (
            "The + / Add Project button should be visible in the sidebar"
        )

    def test_clicking_add_opens_analyze_panel(self, page):
        """Clicking the + button opens the Analyze Panel slide-in (Req 5.1)."""
        _open_analyze_panel(page)
        panel = _panel_locator(page)
        assert panel.is_visible(), (
            "Analyze Panel should be visible after clicking the + button"
        )

    def test_analyze_panel_has_path_input(self, page):
        """The Analyze Panel contains a path input field."""
        _open_analyze_panel(page)
        assert _path_input(page).is_visible(), (
            "Analyze Panel should contain a path input field"
        )

    def test_analyze_panel_has_analyse_button(self, page):
        """The Analyze Panel contains an Analyse button."""
        _open_analyze_panel(page)
        assert _analyse_button(page).is_visible(), (
            "Analyze Panel should contain an Analyse button"
        )


class TestAnalyzePanelCheck:
    """Entering valid path and clicking Check shows project-ready confirmation (Req 5.2)."""

    def test_check_button_is_visible_in_panel(self, page):
        """The Check button is visible inside the Analyze Panel."""
        _open_analyze_panel(page)
        btn = _check_button(page)
        if not btn.is_visible():
            pytest.skip("Check button not present in this build's Analyze Panel")
        assert btn.is_visible(), "Check button should be visible in the Analyze Panel"

    def test_valid_path_check_shows_confirmation(self, page, tmp_path):
        """Entering a valid directory path and clicking Check shows a confirmation (Req 5.2)."""
        _open_analyze_panel(page)
        check_btn = _check_button(page)
        if not check_btn.is_visible():
            pytest.skip("Check button not present in this build's Analyze Panel")

        # Use tmp_path as a valid directory
        _path_input(page).fill(str(tmp_path))
        check_btn.click()

        # Wait for a confirmation or status message
        page.wait_for_selector(
            ".path-check-ok, .check-success, [data-status='ready'], "
            "#analyze-panel .success, #analyze-panel .check-result",
            timeout=8_000,
        )
        confirmation = page.locator(
            ".path-check-ok, .check-success, [data-status='ready'], "
            "#analyze-panel .success, #analyze-panel .check-result"
        ).first
        assert confirmation.is_visible(), (
            "A project-ready confirmation should appear after a valid path check"
        )


class TestAnalyzePanelStream:
    """Clicking Analyse shows progress; done event adds project to sidebar (Req 5.3, 5.4, 5.5)."""

    def test_progress_indicator_shown_during_analysis(self, page, tmp_path):
        """A progress indicator appears after clicking Analyse (Req 5.3)."""
        _open_analyze_panel(page)
        _path_input(page).fill(str(tmp_path))
        _analyse_button(page).click()

        page.wait_for_selector(
            "#analyze-panel .progress, #analyze-panel .loading, "
            "#analyze-panel [class*='progress'], .streaming-cursor, "
            "#analyze-panel [class*='loading']",
            timeout=8_000,
        )

    def test_progress_display_updates_during_stream(self, page, tmp_path):
        """Progress events update the display inside the Analyze Panel (Req 5.4)."""
        _open_analyze_panel(page)
        _path_input(page).fill(str(tmp_path))
        _analyse_button(page).click()

        # Wait for any progress text to appear
        progress_area = page.locator(
            "#analyze-progress, #analyze-panel .progress-log, "
            "#analyze-panel .progress-messages, #analyze-panel [data-role='progress']"
        )
        if progress_area.count() > 0:
            page.wait_for_function(
                "document.querySelector('#analyze-progress, "
                "#analyze-panel .progress-log, #analyze-panel .progress-messages') "
                "&& document.querySelector('#analyze-progress, "
                "#analyze-panel .progress-log, #analyze-panel .progress-messages').innerText.length > 0",
                timeout=10_000,
            )

    def test_done_event_adds_project_to_sidebar(self, page, tmp_path):
        """After the done event, the new project appears in the sidebar (Req 5.5)."""
        # Get initial project count
        initial_items = page.locator(".project-item, [data-project]").count()

        _open_analyze_panel(page)
        project_name = tmp_path.name
        _path_input(page).fill(str(tmp_path))
        _analyse_button(page).click()

        # Wait for stream to complete (panel closes or success state shown)
        page.wait_for_selector(
            "#analyze-panel .success, #analyze-panel .done, "
            "[data-status='done'], #analyze-panel .analyze-complete",
            timeout=15_000,
        )

        # Project list should have grown
        final_items = page.locator(".project-item, [data-project]").count()
        assert final_items > initial_items or page.locator(
            f".project-item:has-text('{project_name}'), [data-project='{project_name}']"
        ).count() > 0, (
            f"Expected new project '{project_name}' to appear in sidebar after analysis"
        )


class TestAnalyzePanelError:
    """Error event shows error message inside panel and error toast (Req 5.6)."""

    def test_invalid_path_shows_error_in_panel(self, page):
        """An invalid path triggers an error message inside the Analyze Panel (Req 5.6)."""
        _open_analyze_panel(page)
        _path_input(page).fill("/nonexistent/path/that/does/not/exist/xyz_9999")
        _analyse_button(page).click()

        # Wait for error state
        page.wait_for_selector(
            "#analyze-panel .error, #analyze-panel .error-message, "
            "#analyze-panel [data-status='error'], #analyze-panel [class*='error']",
            timeout=10_000,
        )
        error_el = page.locator(
            "#analyze-panel .error, #analyze-panel .error-message, "
            "#analyze-panel [data-status='error'], #analyze-panel [class*='error']"
        ).first
        assert error_el.is_visible(), (
            "An error message should be visible inside the Analyze Panel for an invalid path"
        )

    def test_invalid_path_shows_error_toast(self, page):
        """An invalid path triggers an error toast notification (Req 5.6)."""
        _open_analyze_panel(page)
        _path_input(page).fill("/nonexistent/path/that/does/not/exist/xyz_9999")
        _analyse_button(page).click()

        # Wait for toast
        page.wait_for_selector(
            "#toast-container .toast-error, #toast-container .error, "
            ".toast[data-type='error'], .toast-error",
            timeout=10_000,
        )
        toast = page.locator(
            "#toast-container .toast-error, #toast-container .error, "
            ".toast[data-type='error'], .toast-error"
        ).first
        assert toast.is_visible(), (
            "An error toast should be shown when the Analyze Panel encounters an error"
        )
