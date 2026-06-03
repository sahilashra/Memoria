"""
UI tests — Plan tab browser regression tests
─────────────────────────────────────────────
Covers:
  - Clicking the Plan nav item shows #tab-plan with the active class (Req 3.1)
  - #plan-textarea is visible when the Plan tab is active (Req 3.2)
  - Typing a plan and clicking Analyse shows a streaming cursor (Req 3.3)
  - After the SSE stream completes, the analysis result is rendered (Req 3.4)
"""

import pytest
from .conftest import wait_for_stream_done, switch_tab


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _open_plan_tab(page):
    """Navigate to the Plan tab via the nav item."""
    page.locator(".tab[data-tab='plan'], .nav-item[data-tab='plan']").first.click()
    page.wait_for_selector("#tab-plan.active", timeout=8_000)


def _type_plan_and_analyse(page, plan_text: str = "Migrate auth-service to OAuth2."):
    """Type a plan into #plan-textarea and click the Analyse button."""
    page.locator("#plan-textarea").fill(plan_text)
    page.locator("#plan-analyse-btn, #tab-plan button[type='submit'], #tab-plan .analyse-btn").first.click()


def _wait_for_plan_result(page, timeout: int = 15_000) -> str:
    """Wait until the plan result container has stable content and return its text."""
    page.wait_for_selector(
        "#plan-result:not(:empty), #plan-output:not(:empty), #tab-plan .plan-result",
        timeout=timeout,
    )
    selector = "#plan-result, #plan-output, #tab-plan .plan-result"
    return wait_for_stream_done(page, selector, timeout=timeout)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestPlanTabNavigation:
    """Clicking the Plan nav item activates the tab and shows the textarea (Req 3.1, 3.2)."""

    def test_plan_tab_has_active_class_after_nav(self, page):
        """#tab-plan gets the active class when the Plan nav item is clicked (Req 3.1)."""
        _open_plan_tab(page)
        classes = page.locator("#tab-plan").get_attribute("class") or ""
        assert "active" in classes, (
            f"Expected #tab-plan to have 'active' class, got: {classes!r}"
        )

    def test_plan_tab_is_visible_after_nav(self, page):
        """#tab-plan is visible after clicking the Plan nav item."""
        _open_plan_tab(page)
        assert page.locator("#tab-plan").is_visible(), (
            "#tab-plan should be visible after navigation"
        )

    def test_plan_textarea_is_visible(self, page):
        """#plan-textarea is visible when the Plan tab is active (Req 3.2)."""
        _open_plan_tab(page)
        assert page.locator("#plan-textarea").is_visible(), (
            "#plan-textarea should be visible in the Plan tab"
        )

    def test_plan_textarea_is_editable(self, page):
        """#plan-textarea accepts text input."""
        _open_plan_tab(page)
        textarea = page.locator("#plan-textarea")
        textarea.fill("Test plan input")
        assert textarea.input_value() == "Test plan input", (
            "#plan-textarea should accept and retain typed text"
        )

    def test_analyse_button_is_visible(self, page):
        """The Analyse button is visible in the Plan tab."""
        _open_plan_tab(page)
        btn = page.locator(
            "#plan-analyse-btn, #tab-plan button[type='submit'], #tab-plan .analyse-btn"
        ).first
        assert btn.is_visible(), "Analyse button should be visible in the Plan tab"


class TestPlanAnalysis:
    """Typing a plan and clicking Analyse streams content and renders the result (Req 3.3, 3.4)."""

    def test_streaming_cursor_shown_during_analysis(self, page):
        """A streaming cursor or loading indicator appears after clicking Analyse (Req 3.3)."""
        _open_plan_tab(page)
        _type_plan_and_analyse(page)
        # Accept either a .streaming-cursor or a loading class on the result container
        page.wait_for_selector(
            ".streaming-cursor, #plan-result.loading, #plan-output.loading, "
            "#tab-plan .plan-loading, #tab-plan [class*='loading']",
            timeout=8_000,
        )

    def test_analysis_result_visible_after_stream(self, page):
        """The analysis result container is visible after the stream completes (Req 3.4)."""
        _open_plan_tab(page)
        _type_plan_and_analyse(page)
        _wait_for_plan_result(page)
        result = page.locator(
            "#plan-result, #plan-output, #tab-plan .plan-result"
        ).first
        assert result.is_visible(), (
            "Plan analysis result should be visible after stream completes"
        )

    def test_analysis_result_non_empty(self, page):
        """The analysis result contains non-empty text after streaming (Req 3.4)."""
        _open_plan_tab(page)
        _type_plan_and_analyse(page)
        text = _wait_for_plan_result(page)
        assert len(text.strip()) > 0, (
            f"Plan analysis result should be non-empty after stream, got: {text!r}"
        )

    def test_streaming_cursor_removed_after_stream(self, page):
        """The streaming cursor is removed once the stream completes."""
        _open_plan_tab(page)
        _type_plan_and_analyse(page)
        _wait_for_plan_result(page)
        cursor = page.locator(".streaming-cursor")
        assert not cursor.is_visible(), (
            ".streaming-cursor should be removed after stream completes"
        )

    def test_analyse_button_re_enabled_after_stream(self, page):
        """The Analyse button is re-enabled after the stream completes."""
        _open_plan_tab(page)
        _type_plan_and_analyse(page)
        _wait_for_plan_result(page)
        btn = page.locator(
            "#plan-analyse-btn, #tab-plan button[type='submit'], #tab-plan .analyse-btn"
        ).first
        assert not btn.is_disabled(), (
            "Analyse button should be re-enabled after stream completes"
        )
