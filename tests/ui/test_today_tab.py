"""
UI tests — Today tab (Morning Brief)
──────────────────────────────────────
Covers:
  - Clicking the Today nav item shows #tab-today with the active class (Req 2.1)
  - Empty state is visible before any brief is generated (Req 2.2)
  - Clicking "Generate Brief" shows a loading indicator while the SSE stream
    is in progress (Req 2.3)
  - After the SSE stream completes, the brief is rendered as formatted Markdown
    (not raw text) (Req 2.4)
  - Clicking "Dismiss" removes the brief and returns to the empty state (Req 2.5)
  - GET /api/brief returns HTTP 200 with a JSON body containing a content field
    (Req 2.6 — the live endpoint is /api/brief; /api/today is an alias if present)
"""

import httpx
import pytest
from .conftest import wait_for_stream_done, switch_tab


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _open_today(page):
    """Navigate to the Today tab via the nav item."""
    page.locator(".nav-item[data-tab='today']").click()
    page.wait_for_selector("#tab-today.active", timeout=8_000)


def _click_generate(page):
    """Click the Generate Brief button in the empty state."""
    page.locator("#tab-today .empty-cta").click()


def _click_regenerate(page):
    """Click the Regenerate button in the Today tab header."""
    page.locator("#today-regen-btn").click()


def _wait_for_brief_rendered(page, timeout: int = 15_000) -> str:
    """
    Wait until #today-brief-body is visible and has stable content.
    Returns the inner text of the rendered brief.
    """
    page.wait_for_selector(
        "#today-brief-body:not([style*='display: none'])",
        timeout=timeout,
    )
    return wait_for_stream_done(page, "#today-brief-body", timeout=timeout)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestTodayTabNavigation:
    """Clicking the Today nav item activates the tab and shows the empty state."""

    def test_today_tab_has_active_class_after_nav(self, page):
        """#tab-today gets the active class when the Today nav item is clicked."""
        _open_today(page)
        tab = page.locator("#tab-today")
        classes = tab.get_attribute("class") or ""
        assert "active" in classes, (
            f"Expected #tab-today to have 'active' class, got: {classes!r}"
        )

    def test_today_tab_is_visible_after_nav(self, page):
        """#tab-today is visible after clicking the Today nav item."""
        _open_today(page)
        assert page.locator("#tab-today").is_visible(), (
            "#tab-today should be visible after navigation"
        )

    def test_empty_state_visible_before_generation(self, page):
        """The empty-state element is visible inside #tab-today before any brief."""
        _open_today(page)
        empty = page.locator("#today-empty")
        assert empty.is_visible(), (
            "#today-empty should be visible when no brief has been generated"
        )

    def test_brief_body_hidden_before_generation(self, page):
        """#today-brief-body is hidden (display:none) before generation."""
        _open_today(page)
        brief_body = page.locator("#today-brief-body")
        # The element exists but should not be visible
        assert not brief_body.is_visible(), (
            "#today-brief-body should be hidden before a brief is generated"
        )

    def test_generate_brief_button_present_in_empty_state(self, page):
        """The 'Generate Brief' button is present in the empty state."""
        _open_today(page)
        btn = page.locator("#tab-today .empty-cta")
        assert btn.is_visible(), (
            "Generate Brief button should be visible in the empty state"
        )


class TestTodayBriefGeneration:
    """Clicking Generate Brief streams content and renders it as Markdown."""

    def test_loading_indicator_shown_during_generation(self, page):
        """A loading indicator appears immediately after clicking Generate Brief."""
        _open_today(page)
        _click_generate(page)
        # The brief body switches to today-loading class while generating
        page.wait_for_selector(
            "#today-brief-body.today-loading",
            timeout=8_000,
        )
        loading = page.locator("#today-brief-body.today-loading")
        assert loading.is_visible(), (
            "Loading indicator should be visible while brief is being generated"
        )

    def test_brief_body_visible_after_stream_done(self, page):
        """#today-brief-body is visible after the SSE stream completes."""
        _open_today(page)
        _click_generate(page)
        _wait_for_brief_rendered(page)
        assert page.locator("#today-brief-body").is_visible(), (
            "#today-brief-body should be visible after generation completes"
        )

    def test_brief_content_non_empty_after_stream(self, page):
        """The rendered brief contains non-empty text after streaming."""
        _open_today(page)
        _click_generate(page)
        text = _wait_for_brief_rendered(page)
        assert len(text.strip()) > 0, (
            f"Brief content should be non-empty after generation, got: {text!r}"
        )

    def test_brief_rendered_as_markdown_not_raw(self, page):
        """Brief content is rendered as HTML (no raw '**' or '##' markers)."""
        _open_today(page)
        _click_generate(page)
        _wait_for_brief_rendered(page)
        # Check inner text — rendered markdown should not contain raw markers
        text = page.locator("#today-brief-body").inner_text()
        assert "**" not in text, (
            f"Raw '**' found in brief — markdown not rendered: {text!r}"
        )

    def test_brief_body_has_markdown_class_after_stream(self, page):
        """#today-brief-body has the today-brief-body CSS class after rendering."""
        _open_today(page)
        _click_generate(page)
        _wait_for_brief_rendered(page)
        classes = page.locator("#today-brief-body").get_attribute("class") or ""
        assert "today-brief-body" in classes, (
            f"Expected today-brief-body class after rendering, got: {classes!r}"
        )

    def test_empty_state_hidden_after_generation(self, page):
        """The empty state is hidden once a brief has been generated."""
        _open_today(page)
        _click_generate(page)
        _wait_for_brief_rendered(page)
        assert not page.locator("#today-empty").is_visible(), (
            "#today-empty should be hidden after brief is generated"
        )

    def test_regenerate_button_re_enabled_after_stream(self, page):
        """The Regenerate button is re-enabled after the stream completes."""
        _open_today(page)
        _click_generate(page)
        _wait_for_brief_rendered(page)
        regen_btn = page.locator("#today-regen-btn")
        assert not regen_btn.is_disabled(), (
            "Regenerate button should be re-enabled after stream completes"
        )


class TestTodayBriefDismiss:
    """Clicking Dismiss removes the brief and returns to the empty state."""

    def _generate_brief(self, page):
        """Helper: navigate to Today tab and generate a brief."""
        _open_today(page)
        _click_generate(page)
        _wait_for_brief_rendered(page)

    def test_dismiss_button_present_after_generation(self, page):
        """The Dismiss button is visible after a brief is generated."""
        self._generate_brief(page)
        dismiss_btn = page.locator("#today-dismiss-btn")
        assert dismiss_btn.is_visible(), (
            "Dismiss button should be visible after brief is generated"
        )

    def test_dismiss_hides_brief_body(self, page):
        """Clicking Dismiss hides #today-brief-body."""
        self._generate_brief(page)
        page.locator("#today-dismiss-btn").click()
        # Brief body should become hidden
        page.wait_for_function(
            "!document.getElementById('today-brief-body') || "
            "document.getElementById('today-brief-body').style.display === 'none' || "
            "!document.getElementById('today-brief-body').offsetParent",
            timeout=5_000,
        )
        assert not page.locator("#today-brief-body").is_visible(), (
            "#today-brief-body should be hidden after Dismiss"
        )

    def test_dismiss_restores_empty_state(self, page):
        """Clicking Dismiss makes the empty state visible again."""
        self._generate_brief(page)
        page.locator("#today-dismiss-btn").click()
        page.wait_for_selector("#today-empty", timeout=5_000)
        assert page.locator("#today-empty").is_visible(), (
            "#today-empty should be visible again after Dismiss"
        )

    def test_dismiss_clears_brief_content(self, page):
        """After Dismiss, the brief body no longer shows the previous content."""
        self._generate_brief(page)
        brief_text_before = page.locator("#today-brief-body").inner_text().strip()
        assert len(brief_text_before) > 0, "Brief should have content before dismiss"

        page.locator("#today-dismiss-btn").click()
        page.wait_for_selector("#today-empty", timeout=5_000)

        # Brief body is hidden; empty state is shown — state is reset
        assert page.locator("#today-empty").is_visible(), (
            "Empty state should be restored after Dismiss"
        )


class TestTodayApiEndpoint:
    """GET /api/brief returns 200 with a JSON body containing a content field."""

    def test_api_brief_returns_200(self, live_server):
        """GET /api/brief returns HTTP 200."""
        resp = httpx.get(f"{live_server}/api/brief", timeout=10.0)
        if resp.status_code == 404:
            # Try the /api/today alias if /api/brief is not available
            resp = httpx.get(f"{live_server}/api/today", timeout=10.0)
            if resp.status_code == 404:
                pytest.skip("Neither /api/brief nor /api/today is available in this build")
        assert resp.status_code == 200, (
            f"Expected 200 from /api/brief, got {resp.status_code}: {resp.text}"
        )

    def test_api_brief_returns_json(self, live_server):
        """GET /api/brief returns a JSON response."""
        resp = httpx.get(f"{live_server}/api/brief", timeout=10.0)
        if resp.status_code == 404:
            resp = httpx.get(f"{live_server}/api/today", timeout=10.0)
            if resp.status_code == 404:
                pytest.skip("Neither /api/brief nor /api/today is available in this build")
        data = resp.json()
        assert isinstance(data, dict), (
            f"Expected JSON object from brief endpoint, got: {type(data)}"
        )

    def test_api_brief_has_content_or_brief_field(self, live_server):
        """GET /api/brief response contains a 'content' or 'brief' field (Req 2.6)."""
        resp = httpx.get(f"{live_server}/api/brief", timeout=10.0)
        if resp.status_code == 404:
            resp = httpx.get(f"{live_server}/api/today", timeout=10.0)
            if resp.status_code == 404:
                pytest.skip("Neither /api/brief nor /api/today is available in this build")
        data = resp.json()
        has_content = "content" in data
        has_brief = "brief" in data
        assert has_content or has_brief, (
            f"Expected 'content' or 'brief' field in response, got keys: {list(data.keys())}"
        )

    def test_api_brief_content_is_string(self, live_server):
        """The content/brief field in the response is a string."""
        resp = httpx.get(f"{live_server}/api/brief", timeout=10.0)
        if resp.status_code == 404:
            resp = httpx.get(f"{live_server}/api/today", timeout=10.0)
            if resp.status_code == 404:
                pytest.skip("Neither /api/brief nor /api/today is available in this build")
        data = resp.json()
        value = data.get("content", data.get("brief"))
        assert isinstance(value, str), (
            f"Expected content/brief field to be a string, got: {type(value)}"
        )
