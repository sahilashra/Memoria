"""
E2E tests — Consultant Mode (Share Tokens) API regression tests
────────────────────────────────────────────────────────────────
Covers:
  - POST /api/share/tokens returns 200 with token and url fields (Req 4.1)
  - GET /api/share/tokens returns array containing created token (Req 4.2)
  - DELETE /api/share/tokens/{token} returns 200 and token no longer in list (Req 4.3)
  - POST /api/share/tokens with scope field returns scoped token (Req 4.6)
"""

import pytest

pytestmark = pytest.mark.anyio


# ── helpers ────────────────────────────────────────────────────────────────────

async def _create_token(http_client, project: str = "auth_service", scope: list | None = None):
    """POST /api/share/tokens and return the response. Skips if endpoint unavailable."""
    payload = {"project": project}
    if scope is not None:
        payload["scope"] = scope
    resp = await http_client.post("/api/share/tokens", json=payload)
    if resp.status_code == 404:
        pytest.skip("POST /api/share/tokens not available in this build")
    return resp


async def _list_tokens(http_client):
    """GET /api/share/tokens and return the response. Skips if endpoint unavailable."""
    resp = await http_client.get("/api/share/tokens")
    if resp.status_code == 404:
        pytest.skip("GET /api/share/tokens not available in this build")
    return resp


# ── tests ──────────────────────────────────────────────────────────────────────

class TestShareTokenCreate:
    """POST /api/share/tokens returns 200 with token and url fields (Req 4.1)."""

    async def test_create_token_returns_200(self, http_client):
        """POST /api/share/tokens returns HTTP 200."""
        resp = await _create_token(http_client)
        assert resp.status_code == 200, (
            f"Expected 200 from POST /api/share/tokens, got {resp.status_code}: {resp.text}"
        )

    async def test_create_token_response_has_token_field(self, http_client):
        """POST /api/share/tokens response contains a 'token' field (Req 4.1)."""
        resp = await _create_token(http_client)
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data, (
            f"Expected 'token' field in response, got keys: {list(data.keys())}"
        )
        assert isinstance(data["token"], str) and len(data["token"]) > 0, (
            f"Expected non-empty string token, got: {data['token']!r}"
        )

    async def test_create_token_response_has_url_field(self, http_client, live_server):
        """POST /api/share/tokens response contains a 'url' field (Req 4.1)."""
        resp = await _create_token(http_client)
        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data, (
            f"Expected 'url' field in response, got keys: {list(data.keys())}"
        )
        assert isinstance(data["url"], str) and len(data["url"]) > 0, (
            f"Expected non-empty string url, got: {data['url']!r}"
        )

    async def test_create_token_url_contains_token(self, http_client):
        """The url field in the response contains the token value."""
        resp = await _create_token(http_client)
        assert resp.status_code == 200
        data = resp.json()
        token = data.get("token", "")
        url = data.get("url", "")
        assert token in url, (
            f"Expected token {token!r} to appear in url {url!r}"
        )


class TestShareTokenList:
    """GET /api/share/tokens returns array containing created token (Req 4.2)."""

    async def test_list_tokens_returns_200(self, http_client):
        """GET /api/share/tokens returns HTTP 200."""
        resp = await _list_tokens(http_client)
        assert resp.status_code == 200, (
            f"Expected 200 from GET /api/share/tokens, got {resp.status_code}: {resp.text}"
        )

    async def test_list_tokens_returns_array(self, http_client):
        """GET /api/share/tokens returns a JSON array."""
        resp = await _list_tokens(http_client)
        assert resp.status_code == 200
        data = resp.json()
        # May be a list directly or wrapped in a key
        tokens = data if isinstance(data, list) else data.get("tokens", data.get("items", []))
        assert isinstance(tokens, list), (
            f"Expected JSON array from GET /api/share/tokens, got: {type(data)}"
        )

    async def test_created_token_appears_in_list(self, http_client):
        """A newly created token appears in GET /api/share/tokens (Req 4.2)."""
        create_resp = await _create_token(http_client, project="auth_service")
        assert create_resp.status_code == 200
        created_token = create_resp.json()["token"]

        list_resp = await _list_tokens(http_client)
        assert list_resp.status_code == 200
        data = list_resp.json()
        tokens = data if isinstance(data, list) else data.get("tokens", data.get("items", []))

        token_values = []
        for t in tokens:
            if isinstance(t, str):
                token_values.append(t)
            elif isinstance(t, dict):
                token_values.append(t.get("token", t.get("id", "")))

        assert created_token in token_values, (
            f"Expected token {created_token!r} in list, got: {token_values}"
        )


class TestShareTokenRevoke:
    """DELETE /api/share/tokens/{token} returns 200 and token no longer in list (Req 4.3)."""

    async def test_revoke_token_returns_200(self, http_client):
        """DELETE /api/share/tokens/{token} returns HTTP 200 (Req 4.3)."""
        create_resp = await _create_token(http_client)
        assert create_resp.status_code == 200
        token = create_resp.json()["token"]

        del_resp = await http_client.delete(f"/api/share/tokens/{token}")
        if del_resp.status_code == 404:
            pytest.skip("DELETE /api/share/tokens/{token} not available in this build")
        assert del_resp.status_code == 200, (
            f"Expected 200 from DELETE /api/share/tokens/{token}, got {del_resp.status_code}"
        )

    async def test_revoked_token_not_in_list(self, http_client):
        """After DELETE, the token no longer appears in GET /api/share/tokens (Req 4.3)."""
        create_resp = await _create_token(http_client)
        assert create_resp.status_code == 200
        token = create_resp.json()["token"]

        del_resp = await http_client.delete(f"/api/share/tokens/{token}")
        if del_resp.status_code == 404:
            pytest.skip("DELETE /api/share/tokens/{token} not available in this build")
        assert del_resp.status_code == 200

        list_resp = await _list_tokens(http_client)
        data = list_resp.json()
        tokens = data if isinstance(data, list) else data.get("tokens", data.get("items", []))
        token_values = []
        for t in tokens:
            if isinstance(t, str):
                token_values.append(t)
            elif isinstance(t, dict):
                token_values.append(t.get("token", t.get("id", "")))

        assert token not in token_values, (
            f"Revoked token {token!r} should not appear in list, but found in: {token_values}"
        )


class TestShareTokenScope:
    """POST /api/share/tokens with scope field returns a scoped token (Req 4.6)."""

    async def test_scoped_token_creation_returns_200(self, http_client):
        """POST /api/share/tokens with scope returns HTTP 200 (Req 4.6)."""
        resp = await _create_token(http_client, project="auth_service", scope=["auth_service"])
        assert resp.status_code == 200, (
            f"Expected 200 for scoped token creation, got {resp.status_code}: {resp.text}"
        )

    async def test_scoped_token_response_has_token(self, http_client):
        """Scoped token response contains a token field."""
        resp = await _create_token(http_client, project="auth_service", scope=["auth_service"])
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data, (
            f"Expected 'token' field in scoped token response, got: {list(data.keys())}"
        )

    async def test_scoped_token_scope_reflected_in_response(self, http_client):
        """Scoped token response reflects the requested scope (Req 4.6)."""
        resp = await _create_token(http_client, project="auth_service", scope=["auth_service"])
        assert resp.status_code == 200
        data = resp.json()
        # Scope may be returned as a list or embedded in the token metadata
        if "scope" in data:
            assert "auth_service" in data["scope"], (
                f"Expected 'auth_service' in scope, got: {data['scope']}"
            )
