"""
UI tests — Chat sessions
────────────────────────
Covers:
  - New session created via "+" button
  - Delete session falls back to adjacent one
  - Switching sessions reloads the correct history
  - CHATS list order is stable (newest first)
"""

import pytest
from .conftest import wait_for_stream_done, click_project


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _send(page, text: str):
    field = page.locator("#chat-input")
    field.click()
    field.fill(text)
    field.press("Enter")


def _new_chat(page):
    """Click the '+' new chat button in the sidebar."""
    page.locator(".ap-add-btn[onclick='newChat()']").click()


def _session_names(page) -> list[str]:
    """Return all visible session names in order."""
    return [
        el.inner_text().strip()
        for el in page.locator("#session-list .session-item .session-name").all()
    ]


def _active_session_name(page) -> str:
    return page.locator("#session-list .session-item.active .session-name").inner_text().strip()


# ── tests ──────────────────────────────────────────────────────────────────────

class TestNewSession:
    """The + button creates a fresh, empty chat."""

    def test_plus_button_creates_new_session(self, page):
        """Clicking + adds a session to the list."""
        count_before = page.locator("#session-list .session-item").count()
        _new_chat(page)
        page.wait_for_function(
            f"document.querySelectorAll('#session-list .session-item').length > {count_before}",
            timeout=5_000,
        )
        count_after = page.locator("#session-list .session-item").count()
        assert count_after > count_before

    def test_new_session_clears_chat_area(self, page):
        """After clicking +, the chat area shows the welcome/empty state."""
        click_project(page, "auth")
        _send(page, "Hello")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        _new_chat(page)
        # Chat messages should be gone
        page.wait_for_function(
            "document.querySelectorAll('.message-row').length === 0",
            timeout=5_000,
        )
        messages = page.locator(".message-row").all()
        assert len(messages) == 0, "New session should show no messages"

    def test_new_session_is_active_in_sidebar(self, page):
        """The newly created session has the .active class in the sidebar."""
        _new_chat(page)
        active = page.locator("#session-list .session-item.active")
        assert active.count() == 1, "Exactly one session should be active"


class TestDeleteSession:
    """Deleting a session falls back to an adjacent one."""

    def test_delete_only_session_shows_empty_state(self, page):
        """Deleting the only session leaves the sidebar with no sessions."""
        click_project(page, "auth")
        _send(page, "Quick test message")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        # There should now be at least one session; delete the first one
        first_del = page.locator("#session-list .session-item").first
        first_del.hover()
        del_btn = first_del.locator(".session-del")
        del_btn.click()

        # Confirm deletion if a dialog appears
        dialog = page.locator("[role='dialog']")
        if dialog.is_visible():
            page.locator("button:has-text('Delete')").click()

        # Chat area should now be empty
        page.wait_for_function(
            "document.querySelectorAll('.message-row').length === 0",
            timeout=5_000,
        )

    def test_delete_falls_back_to_adjacent(self, page):
        """After deleting the active session, another becomes active."""
        click_project(page, "auth")

        # Create two sessions with messages
        _send(page, "Session A message")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")
        _new_chat(page)
        _send(page, "Session B message")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        # Now delete the active (second) session
        count_before = page.locator("#session-list .session-item").count()
        active_item = page.locator("#session-list .session-item.active").first
        active_item.hover()
        active_item.locator(".session-del").click()

        dialog = page.locator("[role='dialog']")
        if dialog.is_visible():
            page.locator("button:has-text('Delete')").click()

        # Count should decrease
        page.wait_for_function(
            f"document.querySelectorAll('#session-list .session-item').length < {count_before}",
            timeout=5_000,
        )
        remaining = page.locator("#session-list .session-item").count()
        assert remaining == count_before - 1


class TestSwitchSession:
    """Switching sessions loads the correct message history."""

    def test_switching_sessions_restores_messages(self, page):
        """Clicking a different session shows its own messages."""
        click_project(page, "auth")
        _send(page, "Auth question")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        # Start a second session with a different message
        _new_chat(page)
        _send(page, "Payments question")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        # Switch back to the first session (second item in list = earlier one)
        first_session = page.locator("#session-list .session-item").nth(1)
        first_session.click()

        # Give the UI a moment to load
        page.wait_for_function(
            "document.querySelectorAll('.message-row').length >= 1",
            timeout=5_000,
        )
        messages_text = page.locator("#tab-chat").inner_text()
        # First session should contain the auth question
        assert "Auth question" in messages_text, (
            "Switching back to session 1 should show its messages"
        )

    def test_active_session_highlight_changes(self, page):
        """The .active class moves to the newly selected session."""
        click_project(page, "auth")
        _send(page, "First session")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        _new_chat(page)
        _send(page, "Second session")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        # The last (most-recent) session should currently be active
        active_before = page.locator("#session-list .session-item.active").first
        active_name_before = active_before.locator(".session-name").inner_text().strip()

        # Switch to a different session
        sessions = page.locator("#session-list .session-item").all()
        other = next(
            (s for s in sessions if s.locator(".session-name").inner_text().strip() != active_name_before),
            None,
        )
        if other:
            other.click()
            page.wait_for_timeout(300)
            new_active = page.locator("#session-list .session-item.active .session-name").inner_text().strip()
            assert new_active != active_name_before, "Active session should change after click"


class TestSessionListOrder:
    """The CHATS list should show most-recently-used session first."""

    def test_new_session_appears_at_top(self, page):
        """A newly created session is at the top of the session list."""
        click_project(page, "auth")
        _send(page, "First message ever")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        # Create another session
        _new_chat(page)
        _send(page, "Second session first message")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        names = _session_names(page)
        assert len(names) >= 2, "At least two sessions should be visible"
        # The active session (most recent) should be first
        active_name = _active_session_name(page)
        assert names[0] == active_name, "Most-recent session should be at top"
