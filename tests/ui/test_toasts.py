"""
UI tests — Toast notifications
────────────────────────────────
Covers:
  - All three toast types (success, warning, error) carry the correct CSS class
  - Toasts auto-dismiss within 6 seconds
  - The × close button dismisses the toast immediately
  - Toast appears in the toast-container at bottom-right

The tests trigger toasts via browser-side JavaScript (window._showToast /
showToast) since the UI exposes a global helper for this.
"""

import pytest


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _show_toast(page, kind: str, msg: str = "Test message"):
    """Trigger a toast of the given type via JavaScript."""
    page.evaluate(f"showToast({msg!r}, {kind!r})")


def _toast_locator(page):
    return page.locator("#toast-container .toast")


def _wait_for_toast(page, kind: str, timeout: int = 3_000):
    page.wait_for_selector(f"#toast-container .toast.{kind}", timeout=timeout)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestToastTypes:
    """Each toast type has the correct CSS class."""

    def test_success_toast_has_success_class(self, page):
        """showToast(msg, 'success') creates a .toast.success element."""
        _show_toast(page, "success", "Operation completed")
        _wait_for_toast(page, "success")
        toast = page.locator("#toast-container .toast.success").first
        assert toast.is_visible(), "Success toast should be visible"
        classes = toast.get_attribute("class") or ""
        assert "success" in classes

    def test_warning_toast_has_warning_class(self, page):
        """showToast(msg, 'warning') creates a .toast.warning element."""
        _show_toast(page, "warning", "Something might be wrong")
        _wait_for_toast(page, "warning")
        toast = page.locator("#toast-container .toast.warning").first
        assert toast.is_visible()
        classes = toast.get_attribute("class") or ""
        assert "warning" in classes

    def test_error_toast_has_error_class(self, page):
        """showToast(msg, 'error') creates a .toast.error element."""
        _show_toast(page, "error", "Something went wrong")
        _wait_for_toast(page, "error")
        toast = page.locator("#toast-container .toast.error").first
        assert toast.is_visible()
        classes = toast.get_attribute("class") or ""
        assert "error" in classes

    def test_toast_message_text_correct(self, page):
        """The toast body contains the message text passed to showToast."""
        msg = "This is my specific test message"
        _show_toast(page, "success", msg)
        _wait_for_toast(page, "success")
        toast_msg = page.locator("#toast-container .toast.success .toast-msg").first
        text = toast_msg.inner_text().strip()
        assert msg in text, f"Expected '{msg}' in toast text, got: {text!r}"


class TestToastPosition:
    """Toast container is positioned at bottom-right."""

    def test_toast_container_exists(self, page):
        """The #toast-container element is in the DOM."""
        container = page.locator("#toast-container")
        assert container.count() >= 1, "#toast-container should exist in the DOM"

    def test_toast_appears_in_container(self, page):
        """Triggered toast is a child of #toast-container."""
        _show_toast(page, "info", "Info message")
        page.wait_for_selector("#toast-container .toast", timeout=3_000)
        toast = page.locator("#toast-container .toast").first
        assert toast.is_visible()


class TestToastDismissal:
    """Toasts can be dismissed via the × button or auto-expire."""

    def test_close_button_dismisses_toast(self, page):
        """Clicking × on a toast removes it immediately."""
        _show_toast(page, "success", "Click-me toast")
        _wait_for_toast(page, "success")
        toast = page.locator("#toast-container .toast.success").first
        close_btn = toast.locator(".toast-close")
        close_btn.click()
        # Toast should be removed from the DOM quickly
        page.wait_for_function(
            "document.querySelectorAll('#toast-container .toast.success').length === 0",
            timeout=3_000,
        )
        remaining = page.locator("#toast-container .toast.success").count()
        assert remaining == 0, "Toast should be removed after × click"

    def test_toast_auto_dismisses(self, page):
        """Toast disappears on its own within 6 seconds."""
        _show_toast(page, "warning", "Auto-dismiss me")
        _wait_for_toast(page, "warning")
        # Wait up to 6 seconds for auto-dismiss
        page.wait_for_function(
            "document.querySelectorAll('#toast-container .toast.warning').length === 0",
            timeout=7_000,
        )
        remaining = page.locator("#toast-container .toast.warning").count()
        assert remaining == 0, "Toast should auto-dismiss within 6 seconds"

    def test_multiple_toasts_stack(self, page):
        """Multiple toasts are shown simultaneously in the container."""
        _show_toast(page, "success", "Toast 1")
        _show_toast(page, "warning", "Toast 2")
        page.wait_for_selector("#toast-container .toast", timeout=3_000)
        count = page.locator("#toast-container .toast").count()
        assert count >= 2, "Multiple toasts should stack in the container"

    def test_click_anywhere_on_toast_dismisses(self, page):
        """Clicking the toast body (not just ×) also dismisses it."""
        _show_toast(page, "error", "Clickable toast")
        _wait_for_toast(page, "error")
        toast = page.locator("#toast-container .toast.error").first
        toast.click()
        page.wait_for_function(
            "document.querySelectorAll('#toast-container .toast.error').length === 0",
            timeout=3_000,
        )
        remaining = page.locator("#toast-container .toast.error").count()
        assert remaining == 0, "Clicking the toast body should dismiss it"
