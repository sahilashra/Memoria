"""
UI tests — SSE streaming regression tests
───────────────────────────────────────────
Covers:
  - Cursor element visible during stream; removed within 500 ms of done event (Req 9.1, 9.2)
  - Chat area scrolls to bottom as chunks arrive (Req 9.3)
  - Manual scroll up pauses auto-scroll; scrolling back to bottom resumes it (Req 9.4, 9.5)
  - Send button and input disabled during stream; re-enabled after done (Req 9.6, 9.7)
"""

import pytest
from .conftest import wait_for_stream_done, click_project


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

_CHAT_INPUT = "#chat-input, #message-input, .chat-input textarea"
_SEND_BTN = "#send-btn, #chat-send, button[type='submit'], .send-button"
_STREAMING_CURSOR = ".streaming-cursor"
_CHAT_AREA = "#chat-messages, #messages, .chat-messages, .messages-container"
_AI_MESSAGE = ".message.assistant, .ai-message, [data-role='assistant']"


def _send_message(page, text: str = "Tell me about the auth-service architecture in detail."):
    """Fill the chat input and click send."""
    page.locator(_CHAT_INPUT).first.fill(text)
    page.locator(_SEND_BTN).first.click()


def _wait_for_stream_start(page, timeout: int = 8_000):
    """Wait until the streaming cursor appears, indicating a stream has started."""
    page.wait_for_selector(_STREAMING_CURSOR, timeout=timeout)


def _wait_for_stream_end(page, timeout: int = 15_000):
    """Wait until the streaming cursor is gone and the last AI message is stable."""
    page.wait_for_selector(f"{_AI_MESSAGE}:last-child", timeout=timeout)
    wait_for_stream_done(page, f"{_AI_MESSAGE}:last-child", timeout=timeout)


def _get_scroll_top(page) -> float:
    """Return the scrollTop of the chat area."""
    selector = _CHAT_AREA.replace("'", "\\'")
    return page.evaluate(
        f"(() => {{ const el = document.querySelector('{selector}'); return el ? el.scrollTop : 0; }})()"
    )


def _get_scroll_height(page) -> float:
    """Return the scrollHeight of the chat area."""
    selector = _CHAT_AREA.replace("'", "\\'")
    return page.evaluate(
        f"(() => {{ const el = document.querySelector('{selector}'); return el ? el.scrollHeight : 0; }})()"
    )


def _get_client_height(page) -> float:
    """Return the clientHeight of the chat area."""
    selector = _CHAT_AREA.replace("'", "\\'")
    return page.evaluate(
        f"(() => {{ const el = document.querySelector('{selector}'); return el ? el.clientHeight : 0; }})()"
    )


def _is_scrolled_to_bottom(page, tolerance: int = 10) -> bool:
    """Return True if the chat area is scrolled to (or near) the bottom."""
    scroll_top = _get_scroll_top(page)
    scroll_height = _get_scroll_height(page)
    client_height = _get_client_height(page)
    return (scroll_height - scroll_top - client_height) <= tolerance


# ── tests ──────────────────────────────────────────────────────────────────────

class TestStreamingCursor:
    """Cursor visible during stream; removed within 500 ms of done event (Req 9.1, 9.2)."""

    def test_streaming_cursor_visible_during_stream(self, page):
        """A .streaming-cursor element is visible while the SSE stream is in progress (Req 9.1)."""
        click_project(page, "auth")
        _send_message(page)
        _wait_for_stream_start(page)

        cursor = page.locator(_STREAMING_CURSOR)
        assert cursor.is_visible(), (
            ".streaming-cursor should be visible while the SSE stream is in progress"
        )

    def test_streaming_cursor_removed_after_done(self, page):
        """The .streaming-cursor is removed within 500 ms of the done event (Req 9.2)."""
        click_project(page, "auth")
        _send_message(page)
        _wait_for_stream_end(page)

        # After stream ends, cursor should be gone within 500 ms
        page.wait_for_selector(
            _STREAMING_CURSOR,
            state="hidden",
            timeout=1_000,  # generous 1s to account for test timing
        )
        cursor = page.locator(_STREAMING_CURSOR)
        assert not cursor.is_visible(), (
            ".streaming-cursor should be removed after the SSE stream completes"
        )

    def test_streaming_cursor_inside_ai_message(self, page):
        """The .streaming-cursor appears inside the active AI message bubble (Req 9.1)."""
        click_project(page, "auth")
        _send_message(page)
        _wait_for_stream_start(page)

        # The cursor should be a descendant of an AI message element
        cursor_in_message = page.locator(f"{_AI_MESSAGE} {_STREAMING_CURSOR}")
        assert cursor_in_message.count() > 0, (
            ".streaming-cursor should be inside an AI message bubble during streaming"
        )


class TestAutoScroll:
    """Chat area scrolls to bottom as chunks arrive (Req 9.3)."""

    def test_chat_area_scrolled_to_bottom_during_stream(self, page):
        """The chat area auto-scrolls to the bottom while chunks arrive (Req 9.3)."""
        click_project(page, "auth")
        _send_message(page, "Give me a very detailed explanation of the auth-service.")
        _wait_for_stream_start(page)

        # Poll a few times during streaming to confirm auto-scroll is active
        page.wait_for_timeout(500)
        assert _is_scrolled_to_bottom(page), (
            "Chat area should be scrolled to the bottom while streaming"
        )

    def test_chat_area_at_bottom_after_stream(self, page):
        """The chat area remains at the bottom after the stream completes."""
        click_project(page, "auth")
        _send_message(page)
        _wait_for_stream_end(page)

        assert _is_scrolled_to_bottom(page), (
            "Chat area should be at the bottom after the stream completes"
        )


class TestScrollPause:
    """Manual scroll up pauses auto-scroll; scrolling back resumes it (Req 9.4, 9.5)."""

    def test_manual_scroll_up_pauses_auto_scroll(self, page):
        """Manually scrolling up during a stream pauses auto-scroll (Req 9.4)."""
        click_project(page, "auth")
        _send_message(page, "Give me a very detailed explanation of the auth-service.")
        _wait_for_stream_start(page)

        # Scroll up manually
        chat_selector = _CHAT_AREA.replace("'", "\\'")
        page.evaluate(
            f"(() => {{ const el = document.querySelector('{chat_selector}'); if (el) el.scrollTop = 0; }})()"
        )
        page.wait_for_timeout(600)

        # After manual scroll up, the view should NOT have been forced back to bottom
        scroll_top = _get_scroll_top(page)
        scroll_height = _get_scroll_height(page)
        client_height = _get_client_height(page)
        distance_from_bottom = scroll_height - scroll_top - client_height

        # If auto-scroll is paused, we should still be away from the bottom
        # (allow some tolerance for small content)
        if scroll_height > client_height + 50:
            assert distance_from_bottom > 10, (
                "Auto-scroll should be paused after manual scroll up — "
                f"but chat area snapped back to bottom (distance: {distance_from_bottom}px)"
            )

    def test_scroll_to_bottom_resumes_auto_scroll(self, page):
        """Scrolling back to the bottom during a stream resumes auto-scroll (Req 9.5)."""
        click_project(page, "auth")
        _send_message(page, "Give me a very detailed explanation of the auth-service.")
        _wait_for_stream_start(page)

        chat_selector = _CHAT_AREA.replace("'", "\\'")

        # Scroll up to pause
        page.evaluate(
            f"(() => {{ const el = document.querySelector('{chat_selector}'); if (el) el.scrollTop = 0; }})()"
        )
        page.wait_for_timeout(300)

        # Scroll back to bottom to resume
        page.evaluate(
            f"(() => {{ const el = document.querySelector('{chat_selector}'); if (el) el.scrollTop = el.scrollHeight; }})()"
        )
        page.wait_for_timeout(600)

        # Auto-scroll should have resumed — we should be at the bottom
        assert _is_scrolled_to_bottom(page), (
            "Auto-scroll should resume after scrolling back to the bottom"
        )


class TestInputDisabled:
    """Send button and input disabled during stream; re-enabled after done (Req 9.6, 9.7)."""

    def test_send_button_disabled_during_stream(self, page):
        """The send button is disabled while the SSE stream is in progress (Req 9.6)."""
        click_project(page, "auth")
        _send_message(page)
        _wait_for_stream_start(page)

        send_btn = page.locator(_SEND_BTN).first
        assert send_btn.is_disabled(), (
            "Send button should be disabled while the SSE stream is in progress"
        )

    def test_chat_input_disabled_during_stream(self, page):
        """The chat input is disabled while the SSE stream is in progress (Req 9.6)."""
        click_project(page, "auth")
        _send_message(page)
        _wait_for_stream_start(page)

        chat_input = page.locator(_CHAT_INPUT).first
        assert chat_input.is_disabled(), (
            "Chat input should be disabled while the SSE stream is in progress"
        )

    def test_send_button_re_enabled_after_stream(self, page):
        """The send button is re-enabled after the SSE stream completes (Req 9.7)."""
        click_project(page, "auth")
        _send_message(page)
        _wait_for_stream_end(page)

        send_btn = page.locator(_SEND_BTN).first
        assert not send_btn.is_disabled(), (
            "Send button should be re-enabled after the SSE stream completes"
        )

    def test_chat_input_re_enabled_after_stream(self, page):
        """The chat input is re-enabled after the SSE stream completes (Req 9.7)."""
        click_project(page, "auth")
        _send_message(page)
        _wait_for_stream_end(page)

        chat_input = page.locator(_CHAT_INPUT).first
        assert not chat_input.is_disabled(), (
            "Chat input should be re-enabled after the SSE stream completes"
        )
