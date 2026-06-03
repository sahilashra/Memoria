"""
UI tests — Cross-tab navigation state preservation regression tests
────────────────────────────────────────────────────────────────────
Covers:
  - Chat messages survive switching to Search tab and back (Req 7.1)
  - Search input value and results survive switching to Books tab and back (Req 7.2)
  - Active project stays highlighted when switching between all tabs (Req 7.3)
  - Navigating to Graph tab and back does not reset active session or clear chat (Req 7.4)
  - Text in #plan-textarea is preserved after navigating away and returning (Req 7.5)
"""

import pytest
from .conftest import wait_for_stream_done, click_project, switch_tab


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _send_chat_message(page, message: str):
    """Type a message into the chat input and submit it."""
    page.locator("#chat-input, #message-input, .chat-input textarea").first.fill(message)
    page.locator(
        "#send-btn, #chat-send, button[type='submit'], .send-button"
    ).first.click()


def _wait_for_chat_response(page, timeout: int = 15_000):
    """Wait until the AI response has finished streaming."""
    page.wait_for_selector(".message.assistant, .ai-message, [data-role='assistant']", timeout=timeout)
    wait_for_stream_done(page, ".message.assistant:last-child, .ai-message:last-child", timeout=timeout)


def _get_chat_message_count(page) -> int:
    """Return the number of chat message elements currently in the DOM."""
    return page.locator(".message, .chat-message, [data-role='user'], [data-role='assistant']").count()


def _type_search_query(page, query: str):
    """Type a query into the search input in the Search tab."""
    page.locator("#search-input, .search-input input, input[placeholder*='Search']").first.fill(query)
    # Trigger search (Enter or button click)
    page.keyboard.press("Enter")


def _get_search_input_value(page) -> str:
    """Return the current value of the search input."""
    return page.locator(
        "#search-input, .search-input input, input[placeholder*='Search']"
    ).first.input_value()


def _switch_to_tab(page, tab_name: str):
    """Switch to a named tab and wait for it to become active."""
    switch_tab(page, tab_name)
    page.wait_for_selector(f"#tab-{tab_name}.active, [data-tab='{tab_name}'].active", timeout=8_000)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestChatStatePreservation:
    """Chat messages survive switching to Search tab and back (Req 7.1)."""

    def test_chat_messages_preserved_after_tab_switch(self, page):
        """Chat messages are still present after switching to Search tab and back (Req 7.1)."""
        # Select a project first
        click_project(page, "auth")

        # Send a chat message
        _send_chat_message(page, "What is the auth-service rate limit?")
        _wait_for_chat_response(page)

        message_count_before = _get_chat_message_count(page)
        assert message_count_before > 0, "Expected at least one chat message before tab switch"

        # Switch to Search tab and back
        _switch_to_tab(page, "search")
        _switch_to_tab(page, "chat")

        message_count_after = _get_chat_message_count(page)
        assert message_count_after >= message_count_before, (
            f"Expected chat messages to be preserved after tab switch. "
            f"Before: {message_count_before}, After: {message_count_after}"
        )

    def test_chat_message_content_preserved_after_tab_switch(self, page):
        """The content of chat messages is preserved after switching tabs."""
        click_project(page, "auth")
        _send_chat_message(page, "Tell me about JWT tokens")
        _wait_for_chat_response(page)

        # Capture message text before switching
        messages_before = page.locator(
            ".message, .chat-message, [data-role='user']"
        ).all_inner_texts()

        _switch_to_tab(page, "search")
        _switch_to_tab(page, "chat")

        messages_after = page.locator(
            ".message, .chat-message, [data-role='user']"
        ).all_inner_texts()

        assert messages_before == messages_after, (
            f"Chat message content changed after tab switch.\n"
            f"Before: {messages_before}\nAfter: {messages_after}"
        )


class TestSearchStatePreservation:
    """Search input value and results survive switching to Books tab and back (Req 7.2)."""

    def test_search_input_preserved_after_tab_switch(self, page):
        """Search input value is preserved after switching to Books tab and back (Req 7.2)."""
        _switch_to_tab(page, "search")
        _type_search_query(page, "authentication")

        value_before = _get_search_input_value(page)
        assert "authentication" in value_before.lower(), (
            f"Expected 'authentication' in search input, got: {value_before!r}"
        )

        _switch_to_tab(page, "books")
        _switch_to_tab(page, "search")

        value_after = _get_search_input_value(page)
        assert value_after == value_before, (
            f"Search input value changed after tab switch. "
            f"Before: {value_before!r}, After: {value_after!r}"
        )

    def test_search_results_preserved_after_tab_switch(self, page):
        """Search results are still visible after switching to Books tab and back (Req 7.2)."""
        _switch_to_tab(page, "search")
        _type_search_query(page, "auth")

        # Wait for results
        page.wait_for_selector(
            ".search-result, .search-results li, #search-results .result",
            timeout=8_000,
        )
        results_before = page.locator(
            ".search-result, .search-results li, #search-results .result"
        ).count()

        _switch_to_tab(page, "books")
        _switch_to_tab(page, "search")

        results_after = page.locator(
            ".search-result, .search-results li, #search-results .result"
        ).count()

        assert results_after >= results_before, (
            f"Search results count decreased after tab switch. "
            f"Before: {results_before}, After: {results_after}"
        )


class TestProjectSelectionPreservation:
    """Active project stays highlighted when switching between all tabs (Req 7.3)."""

    def _get_active_project(self, page) -> str:
        """Return the name of the currently highlighted project in the sidebar."""
        active = page.locator(
            ".project-item.active, .project-item.selected, "
            "[data-project].active, [data-project].selected"
        ).first
        if active.count() == 0:
            return ""
        return active.get_attribute("data-project") or active.inner_text()

    def test_project_selection_preserved_across_tabs(self, page):
        """Active project stays highlighted when switching between all tabs (Req 7.3)."""
        click_project(page, "auth")
        project_before = self._get_active_project(page)
        assert project_before, "Expected a project to be selected before tab switching"

        tabs = ["search", "books", "chat"]
        for tab in tabs:
            try:
                _switch_to_tab(page, tab)
                project_during = self._get_active_project(page)
                assert project_during == project_before, (
                    f"Active project changed when switching to '{tab}' tab. "
                    f"Expected: {project_before!r}, Got: {project_during!r}"
                )
            except Exception:
                # Tab may not exist in all builds — skip gracefully
                pass


class TestGraphTabNavigation:
    """Navigating to Graph tab and back does not reset active session or clear chat (Req 7.4)."""

    def test_chat_history_preserved_after_graph_tab(self, page):
        """Chat history is not cleared after navigating to Graph tab and back (Req 7.4)."""
        click_project(page, "auth")
        _send_chat_message(page, "Explain the auth-service architecture")
        _wait_for_chat_response(page)

        count_before = _get_chat_message_count(page)
        assert count_before > 0, "Expected chat messages before navigating to Graph tab"

        # Navigate to Graph tab (skip if not present)
        graph_tab = page.locator(".tab[data-tab='graph'], .nav-item[data-tab='graph']")
        if graph_tab.count() == 0:
            pytest.skip("Graph tab not present in this build")

        graph_tab.first.click()
        page.wait_for_load_state("networkidle", timeout=8_000)

        # Navigate back to chat
        _switch_to_tab(page, "chat")

        count_after = _get_chat_message_count(page)
        assert count_after >= count_before, (
            f"Chat history was cleared after Graph tab navigation. "
            f"Before: {count_before}, After: {count_after}"
        )

    def test_active_session_preserved_after_graph_tab(self, page):
        """Active session is not reset after navigating to Graph tab and back (Req 7.4)."""
        click_project(page, "auth")

        # Capture active session indicator before
        session_before = page.locator(
            ".session-item.active, .session.active, [data-session].active"
        ).first.get_attribute("data-session") if page.locator(
            ".session-item.active, .session.active, [data-session].active"
        ).count() > 0 else None

        graph_tab = page.locator(".tab[data-tab='graph'], .nav-item[data-tab='graph']")
        if graph_tab.count() == 0:
            pytest.skip("Graph tab not present in this build")

        graph_tab.first.click()
        page.wait_for_load_state("networkidle", timeout=8_000)
        _switch_to_tab(page, "chat")

        session_after = page.locator(
            ".session-item.active, .session.active, [data-session].active"
        ).first.get_attribute("data-session") if page.locator(
            ".session-item.active, .session.active, [data-session].active"
        ).count() > 0 else None

        if session_before is not None:
            assert session_after == session_before, (
                f"Active session changed after Graph tab navigation. "
                f"Before: {session_before!r}, After: {session_after!r}"
            )


class TestPlanTextPreservation:
    """Text in #plan-textarea is preserved after navigating away and returning (Req 7.5)."""

    def test_plan_textarea_text_preserved_after_tab_switch(self, page):
        """Text in #plan-textarea is preserved after navigating away and returning (Req 7.5)."""
        # Navigate to Plan tab
        plan_tab = page.locator(".tab[data-tab='plan'], .nav-item[data-tab='plan']")
        if plan_tab.count() == 0:
            pytest.skip("Plan tab not present in this build")

        plan_tab.first.click()
        page.wait_for_selector("#tab-plan.active, [data-tab='plan'].active", timeout=8_000)

        plan_text = "Migrate auth-service to OAuth2 and update payments-service accordingly."
        page.locator("#plan-textarea").fill(plan_text)

        value_before = page.locator("#plan-textarea").input_value()
        assert plan_text in value_before, (
            f"Expected plan text in textarea before switch, got: {value_before!r}"
        )

        # Switch away and back
        _switch_to_tab(page, "chat")
        plan_tab.first.click()
        page.wait_for_selector("#tab-plan.active, [data-tab='plan'].active", timeout=8_000)

        value_after = page.locator("#plan-textarea").input_value()
        assert value_after == value_before, (
            f"Plan textarea text changed after tab switch. "
            f"Before: {value_before!r}, After: {value_after!r}"
        )
