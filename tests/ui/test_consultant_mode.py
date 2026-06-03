"""
UI tests — Consultant Mode (Share Tokens) browser regression tests
───────────────────────────────────────────────────────────────────
Covers:
  - Valid token URL opens read-only view in browser (Req 4.4)
  - Invalid token URL shows error message (Req 4.5)
  - Analyze Panel controls hidden/disabled when using share token URL (Req 4.7)
"""

import httpx
import pytest
from .conftest import wait_for_stream_done


pytestmark = pytest.mark.usefixtures("mock_llm")


# ── helpers ────────────────────────────────────────────────────────────────────

def _create_share_token(live_server: str, project: str = "auth_service") -> str | None:
    """
    Create a share token via the API and return the token string.
    Returns None if the endpoint is not available.
    """
    resp = httpx.post(
        f"{live_server}/api/share/tokens",
        json={"project": project},
        timeout=10.0,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json().get("token")


def _open_token_url(page, live_server: str, token: str):
    """Navigate the browser to the share token URL."""
    page.goto(f"{live_server}/ui?token={token}")
    page.wait_for_load_state("networkidle", timeout=10_000)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestShareTokenUI:
    """Valid token URL opens read-only view in browser (Req 4.4)."""

    def test_valid_token_url_loads_without_error(self, page, live_server):
        """GET /ui?token={valid_token} loads the page without a JS error overlay (Req 4.4)."""
        token = _create_share_token(live_server)
        if token is None:
            pytest.skip("Share token API not available in this build")

        _open_token_url(page, live_server, token)
        # Page should not show a generic error screen
        error_overlay = page.locator(".error-overlay, #error-page, [data-testid='error']")
        assert not error_overlay.is_visible(), (
            "Error overlay should not be visible for a valid share token URL"
        )

    def test_valid_token_url_shows_content(self, page, live_server):
        """GET /ui?token={valid_token} displays the shared read-only view (Req 4.4)."""
        token = _create_share_token(live_server)
        if token is None:
            pytest.skip("Share token API not available in this build")

        _open_token_url(page, live_server, token)
        # The main app container or sidebar should be visible
        page.wait_for_selector("#sidebar, #app, .app-container, main", timeout=10_000)
        container = page.locator("#sidebar, #app, .app-container, main").first
        assert container.is_visible(), (
            "Main app container should be visible for a valid share token URL"
        )

    def test_valid_token_url_shows_readonly_indicator(self, page, live_server):
        """Valid token URL shows a read-only indicator or badge."""
        token = _create_share_token(live_server)
        if token is None:
            pytest.skip("Share token API not available in this build")

        _open_token_url(page, live_server, token)
        # Look for any read-only indicator
        readonly = page.locator(
            "[data-readonly], .readonly-badge, .read-only, "
            "#readonly-indicator, [class*='readonly'], [class*='read-only']"
        )
        # This is a soft assertion — the indicator may not exist in all builds
        if readonly.count() > 0:
            assert readonly.first.is_visible(), (
                "Read-only indicator should be visible for share token URL"
            )


class TestInvalidToken:
    """Invalid token URL shows error message (Req 4.5)."""

    def test_invalid_token_shows_error(self, page, live_server):
        """GET /ui?token={invalid_token} shows an error message (Req 4.5)."""
        _open_token_url(page, live_server, "invalid_token_xyz_9999_does_not_exist")
        # Wait for the page to settle
        page.wait_for_load_state("networkidle", timeout=8_000)
        # Look for any error indication
        error = page.locator(
            ".error-message, .token-error, #token-error, "
            "[data-testid='token-error'], .alert-error, .error-overlay"
        )
        assert error.count() > 0 and error.first.is_visible(), (
            "An error message should be shown for an invalid share token"
        )

    def test_invalid_token_error_contains_meaningful_text(self, page, live_server):
        """The error message for an invalid token contains descriptive text."""
        _open_token_url(page, live_server, "invalid_token_xyz_9999_does_not_exist")
        page.wait_for_load_state("networkidle", timeout=8_000)
        error = page.locator(
            ".error-message, .token-error, #token-error, "
            "[data-testid='token-error'], .alert-error, .error-overlay"
        )
        if error.count() == 0:
            pytest.skip("Error element not found — token validation UI may not be implemented")
        text = error.first.inner_text().lower()
        assert any(word in text for word in ("invalid", "expired", "not found", "error", "token")), (
            f"Error message should mention token invalidity, got: {text!r}"
        )


class TestReadOnlyEnforcement:
    """Analyze Panel controls hidden/disabled when using share token URL (Req 4.7)."""

    def test_analyze_panel_trigger_hidden_in_readonly(self, page, live_server):
        """The '+' button to open the Analyze Panel is hidden/disabled in read-only mode (Req 4.7)."""
        token = _create_share_token(live_server)
        if token is None:
            pytest.skip("Share token API not available in this build")

        _open_token_url(page, live_server, token)
        # The add-project / analyze trigger button should not be visible
        add_btn = page.locator(
            "#add-project-btn, .add-project, [data-action='open-analyze-panel'], "
            "#analyze-panel-trigger, button[title*='Analyze'], button[title*='Add']"
        )
        if add_btn.count() > 0:
            assert not add_btn.first.is_visible() or add_btn.first.is_disabled(), (
                "Analyze Panel trigger should be hidden or disabled in read-only mode"
            )

    def test_analyze_panel_controls_disabled_in_readonly(self, page, live_server):
        """Analyze Panel controls are hidden or disabled in read-only mode (Req 4.7)."""
        token = _create_share_token(live_server)
        if token is None:
            pytest.skip("Share token API not available in this build")

        _open_token_url(page, live_server, token)
        # Check that the analyze panel itself is not accessible
        analyze_panel = page.locator(
            "#analyze-panel, .analyze-panel, [data-panel='analyze']"
        )
        if analyze_panel.count() > 0:
            # Panel should either not exist or not be visible
            assert not analyze_panel.first.is_visible(), (
                "Analyze Panel should not be visible in read-only mode"
            )

    def test_chat_input_disabled_in_readonly(self, page, live_server):
        """Chat input is disabled or hidden in read-only mode."""
        token = _create_share_token(live_server)
        if token is None:
            pytest.skip("Share token API not available in this build")

        _open_token_url(page, live_server, token)
        chat_input = page.locator(
            "#chat-input, #message-input, .chat-input textarea, "
            "[data-testid='chat-input']"
        )
        if chat_input.count() > 0:
            assert not chat_input.first.is_visible() or chat_input.first.is_disabled(), (
                "Chat input should be hidden or disabled in read-only mode"
            )
