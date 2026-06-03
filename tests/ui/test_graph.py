"""
UI tests — Graph tab
──────────────────────
Covers:
  - Graph canvas renders (SVG present)
  - Empty state shown when graph not yet built
  - Hover tooltip appears on node hover
  - Clicking a node opens the right-side detail panel
  - Zoom-in / zoom-out controls respond
  - Graph filter toolbar is present
"""

import pytest
from .conftest import switch_tab


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _open_graph(page):
    """Navigate to the Graph tab."""
    page.locator(".nav-item[data-tab='graph']").click()
    page.wait_for_selector("#tab-graph.active", timeout=5_000)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestGraphTabAccess:
    """Basic navigation to the Graph tab."""

    def test_graph_tab_accessible_via_nav(self, page):
        """Clicking Graph nav item shows the graph tab content."""
        _open_graph(page)
        assert page.locator("#tab-graph.active").is_visible()

    def test_graph_tab_contains_svg_or_empty_state(self, page):
        """The Graph tab shows either an SVG canvas or an empty-state guide."""
        _open_graph(page)
        has_svg = page.locator("#tab-graph svg.graph-svg").is_visible()
        has_empty = page.locator("#tab-graph .empty-state").is_visible()
        assert has_svg or has_empty, (
            "Graph tab should show either a graph SVG or an empty-state element"
        )


class TestGraphEmptyState:
    """When no graph is built, guidance is shown."""

    def test_empty_state_shows_instructions(self, page):
        """If the graph has not been built, setup instructions are displayed."""
        _open_graph(page)
        # If no graph data, empty state should appear
        if page.locator("#tab-graph .empty-state").is_visible():
            title = page.locator("#tab-graph .empty-title").inner_text()
            assert len(title.strip()) > 0, "Empty state should have a title"
            body = page.locator("#tab-graph .empty-body").inner_text()
            assert "memoria" in body.lower() or "graph" in body.lower(), (
                "Empty state body should mention how to build the graph"
            )


class TestGraphCanvas:
    """Validate the graph SVG canvas when a graph is present."""

    def _has_graph(self, page) -> bool:
        return page.locator("#tab-graph svg.graph-svg").is_visible()

    def test_graph_svg_is_present(self, page):
        """The graph SVG canvas renders inside the graph tab."""
        _open_graph(page)
        if not self._has_graph(page):
            pytest.skip("Graph not built — skipping canvas tests")
        assert page.locator("#tab-graph svg.graph-svg").is_visible()

    def test_graph_nodes_exist(self, page):
        """Graph nodes (.g-node) are rendered inside the SVG."""
        _open_graph(page)
        if not self._has_graph(page):
            pytest.skip("Graph not built — skipping node tests")
        nodes = page.locator(".g-node").all()
        assert len(nodes) >= 1, "Graph should contain at least one node"

    def test_node_hover_shows_tooltip(self, page):
        """Hovering over a graph node shows the tooltip."""
        _open_graph(page)
        if not self._has_graph(page):
            pytest.skip("Graph not built — skipping hover test")
        first_node = page.locator(".g-node").first
        first_node.hover()
        # Tooltip should become visible
        page.wait_for_function(
            "document.getElementById('graph-tooltip')?.style.display !== 'none'",
            timeout=3_000,
        )
        tooltip = page.locator("#graph-tooltip")
        assert tooltip.is_visible(), "Tooltip should appear on node hover"

    def test_tooltip_contains_title(self, page):
        """The tooltip shows the node project name."""
        _open_graph(page)
        if not self._has_graph(page):
            pytest.skip("Graph not built — skipping tooltip content test")
        first_node = page.locator(".g-node").first
        first_node.hover()
        page.wait_for_function(
            "document.getElementById('graph-tooltip')?.style.display !== 'none'",
            timeout=3_000,
        )
        title = page.locator("#tt-title").inner_text().strip()
        assert len(title) > 0, "Tooltip title should be non-empty"

    def test_node_click_highlights_selected(self, page):
        """Clicking a node adds the .selected class to it."""
        _open_graph(page)
        if not self._has_graph(page):
            pytest.skip("Graph not built — skipping click test")
        first_node = page.locator(".g-node").first
        first_node.click()
        page.wait_for_timeout(300)
        selected = page.locator(".g-node.selected, .g-node.faded").count()
        # At least one node should change state after click
        # (either selected or others become faded)
        assert selected >= 1, "Clicking a node should update visual state"


class TestGraphControls:
    """Zoom controls and filter toolbar are functional."""

    def test_graph_controls_visible(self, page):
        """The zoom controls panel is visible in the Graph tab."""
        _open_graph(page)
        controls = page.locator(".graph-controls")
        assert controls.is_visible(), "Graph zoom controls should be visible"

    def test_zoom_in_button_present(self, page):
        """A zoom-in button exists in the controls."""
        _open_graph(page)
        # Zoom buttons are .graph-btn elements
        btns = page.locator(".graph-controls .graph-btn").all()
        assert len(btns) >= 2, "At least zoom-in and zoom-out buttons should exist"

    def test_zoom_in_button_clickable(self, page):
        """Clicking the first graph control button does not throw an error."""
        _open_graph(page)
        first_btn = page.locator(".graph-controls .graph-btn").first
        # Should not raise
        first_btn.click()
        page.wait_for_timeout(200)  # let any animations settle

    def test_graph_filter_toolbar_present(self, page):
        """The graph filter bar (search, confidence slider, rel toggles) is present."""
        _open_graph(page)
        assert page.locator(".graph-filters").is_visible(), "Graph filter toolbar should be present"

    def test_graph_filter_input_present(self, page):
        """A text input for filtering graph nodes by name is present."""
        _open_graph(page)
        filter_input = page.locator(".graph-filter-input").first
        assert filter_input.is_visible(), "Graph filter input should be visible"
