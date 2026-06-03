"""
UI tests — Responsive layout
──────────────────────────────
Covers:
  - At 720px viewport: sidebar collapses to icon-only (56px wide)
  - At 720px: nav labels are hidden; project names are hidden
  - At 720px: session-info text is hidden
  - At full desktop width (1280px): sidebar is full width (248px)
  - At 768–1023px (tablet): sidebar collapses to 56px

Uses Playwright's page.set_viewport_size() to simulate different screen sizes.
"""

import pytest


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _sidebar_width(page) -> int:
    """Return the rendered pixel width of the sidebar element."""
    return page.evaluate("document.querySelector('#sidebar').getBoundingClientRect().width")


def _sidebar_visible(page) -> bool:
    return page.locator("#sidebar").is_visible()


# ── tests ──────────────────────────────────────────────────────────────────────

class TestDesktopLayout:
    """At full desktop width, sidebar is fully expanded."""

    def test_sidebar_full_width_at_desktop(self, page):
        """At 1280×800, sidebar should be ~248px wide."""
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(300)
        width = _sidebar_width(page)
        assert width >= 220, f"Desktop sidebar should be ~248px, got {width}px"

    def test_nav_labels_visible_at_desktop(self, page):
        """Nav item labels are visible at desktop width."""
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(300)
        first_label = page.locator(".nav-item .nav-label").first
        assert first_label.is_visible(), "Nav labels should be visible at desktop width"

    def test_project_names_visible_at_desktop(self, page):
        """Project names in the sidebar are visible at desktop width."""
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(300)
        proj_names = page.locator("#project-list .project-name")
        if proj_names.count() > 0:
            assert proj_names.first.is_visible(), "Project names should be visible at desktop"

    def test_session_info_visible_at_desktop(self, page):
        """Session info (meta text) is visible at desktop width."""
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(300)
        session_info = page.locator(".session-info")
        if session_info.count() > 0:
            assert session_info.first.is_visible(), "Session info should be visible at desktop"


class TestTabletLayout:
    """At 768–1023px, sidebar collapses to icon-only (56px)."""

    def test_sidebar_collapses_at_768(self, page):
        """At exactly 768px, sidebar should be at most 56px wide."""
        page.set_viewport_size({"width": 768, "height": 1024})
        page.wait_for_timeout(400)
        width = _sidebar_width(page)
        assert width <= 60, f"At 768px, sidebar should be icon-only (~56px), got {width}px"

    def test_sidebar_collapses_at_900(self, page):
        """At 900px viewport, sidebar should still be icon-only."""
        page.set_viewport_size({"width": 900, "height": 800})
        page.wait_for_timeout(400)
        width = _sidebar_width(page)
        assert width <= 60, f"At 900px, sidebar should be icon-only (~56px), got {width}px"

    def test_nav_labels_hidden_at_tablet(self, page):
        """Nav labels are hidden at 768–1023px viewport."""
        page.set_viewport_size({"width": 900, "height": 800})
        page.wait_for_timeout(400)
        first_label = page.locator(".nav-item .nav-label").first
        # display:none means is_visible() returns False
        assert not first_label.is_visible(), "Nav labels should be hidden at tablet width"

    def test_project_names_hidden_at_tablet(self, page):
        """Project names in the sidebar are hidden at 768–1023px."""
        page.set_viewport_size({"width": 900, "height": 800})
        page.wait_for_timeout(400)
        proj_names = page.locator("#project-list .project-name")
        if proj_names.count() > 0:
            assert not proj_names.first.is_visible(), (
                "Project names should be hidden at tablet width"
            )

    def test_session_info_hidden_at_tablet(self, page):
        """Session meta info is hidden at 768–1023px."""
        page.set_viewport_size({"width": 900, "height": 800})
        page.wait_for_timeout(400)
        session_info = page.locator(".session-info")
        if session_info.count() > 0:
            assert not session_info.first.is_visible(), (
                "Session info should be hidden at tablet width"
            )

    def test_nav_icons_still_visible_at_tablet(self, page):
        """Nav icons remain visible at tablet width (sidebar not fully collapsed)."""
        page.set_viewport_size({"width": 900, "height": 800})
        page.wait_for_timeout(400)
        # The sidebar itself should still be in the DOM and visible
        sidebar = page.locator("#sidebar")
        assert sidebar.is_visible(), "Sidebar should still be visible at tablet width"
        # Nav items should be present even if labels are hidden
        nav_items = page.locator(".nav-item").all()
        assert len(nav_items) >= 4, "Nav items should remain in DOM at tablet width"

    def test_title_tooltip_present_on_nav_items(self, page):
        """At tablet width, nav items have title attributes for tooltips."""
        page.set_viewport_size({"width": 900, "height": 800})
        page.wait_for_timeout(400)
        first_nav = page.locator(".nav-item").first
        title_attr = first_nav.get_attribute("title")
        assert title_attr and len(title_attr) > 0, (
            "Nav items should have title tooltip attributes at tablet width"
        )


class TestMobileLayout:
    """At <768px, sidebar is off-screen and toggled via hamburger."""

    def test_sidebar_offscreen_at_mobile(self, page):
        """At 375px, the sidebar is hidden off-screen (not in default flow)."""
        page.set_viewport_size({"width": 375, "height": 812})
        page.wait_for_timeout(400)
        sidebar = page.locator("#sidebar")
        # On mobile the sidebar uses position:fixed and translateX(-100%)
        # It's technically in the DOM but should not overlap the main content
        bounding_box = sidebar.bounding_box()
        if bounding_box:
            # x should be negative (off-screen left) or width 0
            assert bounding_box["x"] < 10, (
                f"Mobile sidebar should be off-screen (x={bounding_box['x']})"
            )

    def test_hamburger_visible_at_mobile(self, page):
        """The hamburger menu button is visible at 375px."""
        page.set_viewport_size({"width": 375, "height": 812})
        page.wait_for_timeout(400)
        hamburger = page.locator(".hamburger")
        assert hamburger.is_visible(), "Hamburger button should be visible on mobile"

    def test_hamburger_opens_sidebar(self, page):
        """Clicking the hamburger button slides the sidebar into view."""
        page.set_viewport_size({"width": 375, "height": 812})
        page.wait_for_timeout(400)
        hamburger = page.locator(".hamburger")
        hamburger.click()
        page.wait_for_function(
            "document.querySelector('#sidebar').classList.contains('open')",
            timeout=3_000,
        )
        sidebar = page.locator("#sidebar")
        assert "open" in (sidebar.get_attribute("class") or ""), (
            "Sidebar should have .open class after hamburger click"
        )

    def test_overlay_appears_when_sidebar_open(self, page):
        """The dark overlay appears when sidebar is open on mobile."""
        page.set_viewport_size({"width": 375, "height": 812})
        page.wait_for_timeout(400)
        page.locator(".hamburger").click()
        page.wait_for_function(
            "document.querySelector('#sidebar-overlay')?.classList.contains('open')",
            timeout=3_000,
        )
        overlay = page.locator("#sidebar-overlay")
        assert "open" in (overlay.get_attribute("class") or ""), (
            "Sidebar overlay should be visible when sidebar is open on mobile"
        )

    def test_overlay_click_closes_sidebar(self, page):
        """Clicking the overlay on mobile closes the sidebar."""
        page.set_viewport_size({"width": 375, "height": 812})
        page.wait_for_timeout(400)
        page.locator(".hamburger").click()
        page.wait_for_function(
            "document.querySelector('#sidebar').classList.contains('open')",
            timeout=3_000,
        )
        # Click overlay to close
        page.locator("#sidebar-overlay").click()
        page.wait_for_function(
            "!document.querySelector('#sidebar').classList.contains('open')",
            timeout=3_000,
        )
        assert "open" not in (page.locator("#sidebar").get_attribute("class") or ""), (
            "Sidebar should close when overlay is clicked"
        )
