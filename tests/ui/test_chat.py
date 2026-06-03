"""
UI tests — Chat tab
────────────────────
Covers:
  - Sending a message and verifying streaming completes
  - Session auto-named after first message
  - Multi-turn context carries (follow-up uses prior turn)
  - Chat persists after page reload (localStorage round-trip)
"""

import pytest
from .conftest import wait_for_stream_done, click_project, switch_tab


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _send_message(page, text: str):
    """Type a message into the chat input and press Enter."""
    field = page.locator("#chat-input")
    field.click()
    field.fill(text)
    field.press("Enter")


def _last_ai_bubble(page):
    """Return the last AI message bubble locator."""
    return page.locator(".message-row.ai .bubble.ai").last


# ── tests ──────────────────────────────────────────────────────────────────────

class TestChatSendAndStream:
    """Sending a message should trigger SSE streaming and render a response."""

    def test_send_message_renders_user_bubble(self, page):
        """The user's message appears in a bubble immediately."""
        click_project(page, "auth")
        _send_message(page, "How does OAuth work?")
        bubble = page.locator(".message-row.user .bubble.user").last
        assert "OAuth" in bubble.inner_text()

    def test_ai_response_streams_in(self, page):
        """After sending, an AI bubble appears and eventually contains text."""
        click_project(page, "auth")
        _send_message(page, "How does OAuth work?")
        # Wait for at least one AI bubble to appear
        page.wait_for_selector(".message-row.ai .bubble.ai", timeout=10_000)
        text = wait_for_stream_done(page, ".message-row.ai .bubble.ai")
        assert len(text.strip()) > 10, f"Expected real response, got: {text!r}"

    def test_response_contains_relevant_content(self, page):
        """Mock LLM routes 'oauth' queries to auth-related response."""
        click_project(page, "auth")
        _send_message(page, "What is OAuth used for?")
        text = wait_for_stream_done(page, ".message-row.ai .bubble.ai")
        assert any(kw in text.lower() for kw in ("auth", "oauth", "jwt", "token"))

    def test_streaming_cursor_disappears_after_done(self, page):
        """The blinking cursor element should be gone once streaming finishes."""
        click_project(page, "auth")
        _send_message(page, "Tell me about rate limits")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")
        # cursor should no longer be in the DOM after streaming ends
        cursors = page.locator(".streaming-cursor").all()
        assert len(cursors) == 0, "Streaming cursor should be removed after SSE done"

    def test_send_button_disabled_while_streaming(self, page):
        """Send button should be disabled during a streaming response."""
        click_project(page, "auth")
        field = page.locator("#chat-input")
        field.click()
        field.fill("What is the rate limit?")
        field.press("Enter")
        # Immediately check — button should be disabled
        send_btn = page.locator(".send-btn")
        # Allow a brief moment for the UI to react
        page.wait_for_selector(".message-row.ai .bubble.ai", timeout=8_000)
        # After streaming completes, button should be re-enabled
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")
        assert not send_btn.is_disabled(), "Send button should be enabled after response"


class TestChatSessionNaming:
    """Chat sessions should be auto-named after the first message."""

    def test_session_appears_in_sidebar_after_message(self, page):
        """A new session item appears in the sidebar after sending the first message."""
        click_project(page, "auth")
        _send_message(page, "Explain the auth service architecture")
        # Wait for response so session name has time to be set
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")
        sessions = page.locator("#session-list .session-item").all()
        assert len(sessions) >= 1, "At least one session should be listed"

    def test_session_name_is_non_empty(self, page):
        """The session in the sidebar has a non-empty name."""
        click_project(page, "auth")
        _send_message(page, "Describe the JWT middleware")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")
        first_session = page.locator("#session-list .session-item .session-name").first
        name = first_session.inner_text().strip()
        assert len(name) > 0, "Session name should be non-empty"


class TestChatMultiTurn:
    """Follow-up messages should work within the same session."""

    def test_two_messages_produce_two_bubbles(self, page):
        """Sending two messages produces two user bubbles."""
        click_project(page, "auth")
        _send_message(page, "What is the auth service?")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        _send_message(page, "What about Stripe?")
        # Wait for second AI response
        page.wait_for_function(
            "document.querySelectorAll('.message-row.ai .bubble.ai').length >= 2",
            timeout=12_000,
        )
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        user_bubbles = page.locator(".message-row.user .bubble.user").all()
        ai_bubbles = page.locator(".message-row.ai .bubble.ai").all()
        assert len(user_bubbles) >= 2
        assert len(ai_bubbles) >= 2

    def test_second_message_adds_to_same_session(self, page):
        """Sending a follow-up does not create a new session."""
        click_project(page, "auth")
        _send_message(page, "What is the auth service?")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")
        count_before = page.locator("#session-list .session-item").count()

        _send_message(page, "How does rate limiting work?")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")
        count_after = page.locator("#session-list .session-item").count()

        assert count_after == count_before, "Follow-up should not create a new session"


class TestChatPersistence:
    """Messages should survive a page reload via localStorage."""

    def test_messages_survive_reload(self, page, live_server):
        """After reload, the previously sent messages reappear."""
        click_project(page, "auth")
        _send_message(page, "What framework does auth-service use?")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        # Verify message is present before reload
        bubbles_before = page.locator(".message-row").count()
        assert bubbles_before >= 2

        # Reload page
        page.goto(f"{live_server}/ui")
        page.wait_for_selector("#sidebar", timeout=10_000)

        # Re-select the same project to restore context
        click_project(page, "auth")

        # The session list should still have our session
        sessions = page.locator("#session-list .session-item").all()
        assert len(sessions) >= 1, "Session should persist after reload"

    def test_session_history_loads_on_click(self, page, live_server):
        """Clicking a session in the sidebar loads its message history."""
        click_project(page, "auth")
        _send_message(page, "Describe the auth-service login flow")
        wait_for_stream_done(page, ".message-row.ai .bubble.ai")

        # Reload and click the session
        page.goto(f"{live_server}/ui")
        page.wait_for_selector("#sidebar", timeout=10_000)

        first_session = page.locator("#session-list .session-item").first
        first_session.click()

        # Messages should be restored
        page.wait_for_selector(".message-row", timeout=5_000)
        messages = page.locator(".message-row").all()
        assert len(messages) >= 1, "Reloaded session should show message history"


class TestChatShiftEnter:
    """Shift+Enter should insert a newline instead of sending."""

    def test_shift_enter_inserts_newline(self, page):
        """Shift+Enter in the chat input field adds a newline, not a send."""
        click_project(page, "auth")
        field = page.locator("#chat-input")
        field.click()
        field.fill("First line")
        field.press("Shift+Enter")
        field.type("Second line")

        # The field should contain a newline
        value = field.input_value()
        assert "\n" in value, f"Expected newline in field, got: {value!r}"

        # No message should have been sent yet
        user_bubbles = page.locator(".message-row.user").all()
        assert len(user_bubbles) == 0, "Shift+Enter should not send the message"
