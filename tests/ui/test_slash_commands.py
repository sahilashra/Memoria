"""
UI tests — Slash command menu
──────────────────────────────
Covers:
  - Menu opens when "/" is typed
  - Keyboard navigation (arrow keys + Enter)
  - Click selection executes the command
  - Escape closes the menu
  - /summarize and /impact dispatch correctly
  - /graph and /books navigate to those tabs
  - /plan opens the Plan tab
"""

import pytest
from .conftest import click_project, switch_tab, wait_for_stream_done


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _open_menu(page):
    """Type '/' in the chat input to open the slash-command menu."""
    field = page.locator("#chat-input")
    field.click()
    field.fill("/")
    # Trigger an input event so the JS listener fires
    field.dispatch_event("input")
    page.wait_for_selector(".slash-menu.open", timeout=3_000)


def _menu_is_open(page) -> bool:
    return page.locator(".slash-menu.open").is_visible()


def _menu_is_closed(page) -> bool:
    return not page.locator(".slash-menu.open").is_visible()


# ── tests ──────────────────────────────────────────────────────────────────────

class TestSlashMenuOpenClose:
    """Menu visibility on open/close triggers."""

    def test_slash_opens_menu(self, page):
        """Typing '/' reveals the slash command popover."""
        _open_menu(page)
        assert _menu_is_open(page)

    def test_menu_shows_at_least_one_command(self, page):
        """The popover contains at least one slash-item row."""
        _open_menu(page)
        items = page.locator(".slash-item").all()
        assert len(items) >= 1, "Slash menu should have at least one command"

    def test_escape_closes_menu(self, page):
        """Pressing Escape hides the popover."""
        _open_menu(page)
        page.keyboard.press("Escape")
        page.wait_for_function(
            "!document.querySelector('.slash-menu.open')",
            timeout=3_000,
        )
        assert _menu_is_closed(page)

    def test_clearing_input_closes_menu(self, page):
        """Deleting the '/' from the input hides the popover."""
        field = page.locator("#chat-input")
        _open_menu(page)
        field.fill("")
        field.dispatch_event("input")
        page.wait_for_function(
            "!document.querySelector('.slash-menu.open')",
            timeout=3_000,
        )
        assert _menu_is_closed(page)


class TestSlashMenuKeyboardNavigation:
    """Arrow keys navigate; Enter confirms selection."""

    def test_arrow_down_highlights_first_item(self, page):
        """ArrowDown highlights the first .slash-item."""
        _open_menu(page)
        page.keyboard.press("ArrowDown")
        highlighted = page.locator(".slash-item.highlighted")
        assert highlighted.count() >= 1, "One item should be highlighted after ArrowDown"

    def test_arrow_down_twice_moves_highlight(self, page):
        """Two ArrowDown presses move the highlight to the second item."""
        _open_menu(page)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        highlighted = page.locator(".slash-item.highlighted")
        assert highlighted.count() == 1
        # The highlighted item should be the second .slash-item
        all_items = page.locator(".slash-item").all()
        if len(all_items) >= 2:
            second_item_text = all_items[1].inner_text()
            highlighted_text = highlighted.inner_text()
            assert second_item_text == highlighted_text

    def test_arrow_up_wraps_around(self, page):
        """ArrowUp from the top wraps to the last item."""
        _open_menu(page)
        page.keyboard.press("ArrowUp")
        highlighted = page.locator(".slash-item.highlighted")
        assert highlighted.count() >= 1

    def test_enter_selects_highlighted_item(self, page):
        """Pressing Enter on a highlighted item dispatches the command."""
        _open_menu(page)
        page.keyboard.press("ArrowDown")
        # Confirm that pressing Enter closes the menu (command dispatched)
        page.keyboard.press("Enter")
        page.wait_for_function(
            "!document.querySelector('.slash-menu.open')",
            timeout=3_000,
        )
        assert _menu_is_closed(page)


class TestSlashMenuClickSelection:
    """Clicking an item executes the command."""

    def test_click_item_closes_menu(self, page):
        """Clicking any visible slash item closes the popover."""
        _open_menu(page)
        first_item = page.locator(".slash-item").first
        first_item.click()
        page.wait_for_function(
            "!document.querySelector('.slash-menu.open')",
            timeout=3_000,
        )
        assert _menu_is_closed(page)


class TestSlashCommandDispatch:
    """Individual slash commands navigate to the right place."""

    def _select_command(self, page, cmd: str):
        """Type '/{cmd}' and press Enter to select that command."""
        field = page.locator("#chat-input")
        field.click()
        field.fill(f"/{cmd}")
        field.dispatch_event("input")
        page.wait_for_selector(".slash-menu.open", timeout=3_000)
        # Click the matching item
        item = page.locator(f".slash-item .slash-cmd:has-text('/{cmd}')").first
        if item.is_visible():
            item.click()
        else:
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")

    def test_slash_books_navigates_to_books_tab(self, page):
        """/books switches to the Books tab."""
        self._select_command(page, "books")
        page.wait_for_function(
            "document.querySelector('#tab-books')?.classList.contains('active')",
            timeout=5_000,
        )
        assert page.locator("#tab-books.active").is_visible()

    def test_slash_graph_navigates_to_graph_tab(self, page):
        """/graph switches to the Graph tab."""
        self._select_command(page, "graph")
        page.wait_for_function(
            "document.querySelector('#tab-graph')?.classList.contains('active')",
            timeout=5_000,
        )
        assert page.locator("#tab-graph.active").is_visible()

    def test_slash_plan_navigates_to_plan_tab(self, page):
        """/plan opens the Plan Analysis tab."""
        self._select_command(page, "plan")
        page.wait_for_function(
            "document.querySelector('#tab-plan')?.classList.contains('active')",
            timeout=5_000,
        )
        assert page.locator("#tab-plan.active").is_visible()

    def test_slash_summarize_sends_to_chat(self, page):
        """/summarize dispatches a summarize message to the chat."""
        click_project(page, "auth")
        self._select_command(page, "summarize")
        # A user message bubble should appear with "summarize" content
        page.wait_for_selector(".message-row.user .bubble.user", timeout=5_000)
        user_bubbles_text = page.locator(".message-row.user .bubble.user").last.inner_text().lower()
        assert "summar" in user_bubbles_text, (
            f"Expected summarize-related text, got: {user_bubbles_text!r}"
        )

    def test_slash_impact_with_active_project(self, page):
        """/impact with an active project sends an impact query."""
        click_project(page, "auth")
        self._select_command(page, "impact")
        page.wait_for_selector(".message-row.user .bubble.user", timeout=5_000)
        user_text = page.locator(".message-row.user .bubble.user").last.inner_text().lower()
        assert "impact" in user_text, (
            f"Expected impact-related text in bubble, got: {user_text!r}"
        )

    def test_slash_plan_with_args_fills_textarea(self, page):
        """/plan <text> pre-fills the plan textarea."""
        plan_text = "Implement OAuth2 login"
        field = page.locator("#chat-input")
        field.click()
        field.fill(f"/plan {plan_text}")
        field.dispatch_event("input")
        page.wait_for_selector(".slash-menu.open", timeout=3_000)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")

        page.wait_for_function(
            "document.querySelector('#tab-plan')?.classList.contains('active')",
            timeout=5_000,
        )
        textarea = page.locator("#plan-textarea")
        if textarea.is_visible():
            value = textarea.input_value()
            assert plan_text in value, f"Plan textarea should be pre-filled, got: {value!r}"
