"""
Unit tests for memoria/share.py — Consultant Mode token management.

Tests cover:
  - _parse_expires   — '7d', '24h' parsing; invalid formats raise ValueError
  - generate_token   — creates file, correct shape, expiry wired, scoped projects
  - revoke_token     — removes from store; False when not found
  - revoke_all       — clears all tokens; returns count
  - list_tokens      — returns all; expired flag correct
  - validate_token   — valid → returns record; expired → None + lazy purge;
                       unknown → None
  - is_project_allowed — empty list = all allowed; scoped list filters correctly

All tests use a monkeypatched _TOKENS_PATH pointing to a temp file so they
never touch ~/.memoria/share_tokens.json.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_tokens_path(tmp_path, monkeypatch):
    """Redirect all share module storage to a temp file."""
    fake_path = tmp_path / "share_tokens.json"
    import memoria.share as share_mod
    monkeypatch.setattr(share_mod, "_TOKENS_PATH", fake_path)
    yield fake_path


# ─── _parse_expires ───────────────────────────────────────────────────────────

class TestParseExpires:
    def test_none_returns_none(self):
        from memoria.share import _parse_expires
        assert _parse_expires(None) is None

    def test_days(self):
        from memoria.share import _parse_expires
        result = _parse_expires("7d")
        dt = datetime.fromisoformat(result)
        diff = dt - datetime.now()
        assert 6 <= diff.days <= 7

    def test_hours(self):
        from memoria.share import _parse_expires
        result = _parse_expires("24h")
        dt = datetime.fromisoformat(result)
        diff = dt - datetime.now()
        assert diff.total_seconds() > 23 * 3600
        assert diff.total_seconds() < 25 * 3600

    def test_invalid_format_raises(self):
        from memoria.share import _parse_expires
        with pytest.raises(ValueError):
            _parse_expires("1w")

    def test_invalid_string_raises(self):
        from memoria.share import _parse_expires
        with pytest.raises(ValueError):
            _parse_expires("forever")

    def test_empty_string_returns_none(self):
        """Empty string is treated the same as None — no expiry."""
        from memoria.share import _parse_expires
        assert _parse_expires("") is None


# ─── generate_token ───────────────────────────────────────────────────────────

class TestGenerateToken:
    def test_returns_record_with_token(self):
        from memoria.share import generate_token
        rec = generate_token(projects=[], label="Test")
        assert "token" in rec
        assert rec["token"].startswith("tok_")
        assert len(rec["token"]) > 10

    def test_file_created(self, patch_tokens_path):
        from memoria.share import generate_token
        generate_token(projects=[])
        assert patch_tokens_path.exists()

    def test_token_persisted_to_file(self, patch_tokens_path):
        from memoria.share import generate_token
        rec = generate_token(projects=[], label="Saved")
        data = json.loads(patch_tokens_path.read_text())
        assert rec["token"] in data

    def test_label_stored(self):
        from memoria.share import generate_token
        rec = generate_token(projects=[], label="Acme consultant")
        assert rec["label"] == "Acme consultant"

    def test_projects_stored(self):
        from memoria.share import generate_token
        rec = generate_token(projects=["ProjectA", "ProjectB"])
        assert rec["projects"] == ["ProjectA", "ProjectB"]

    def test_empty_projects_means_all(self):
        from memoria.share import generate_token
        rec = generate_token(projects=[])
        assert rec["projects"] == []

    def test_expires_stored(self):
        from memoria.share import generate_token
        rec = generate_token(projects=[], expires="7d")
        assert rec["expires_at"] is not None
        dt = datetime.fromisoformat(rec["expires_at"])
        assert dt > datetime.now()

    def test_no_expiry(self):
        from memoria.share import generate_token
        rec = generate_token(projects=[])
        assert rec["expires_at"] is None

    def test_created_at_set(self):
        from memoria.share import generate_token
        rec = generate_token(projects=[])
        assert "created_at" in rec
        dt = datetime.fromisoformat(rec["created_at"])
        assert (datetime.now() - dt).total_seconds() < 5

    def test_multiple_tokens_accumulate(self, patch_tokens_path):
        from memoria.share import generate_token
        r1 = generate_token(projects=[])
        r2 = generate_token(projects=[])
        data = json.loads(patch_tokens_path.read_text())
        assert r1["token"] in data
        assert r2["token"] in data

    def test_invalid_expiry_raises(self):
        from memoria.share import generate_token
        with pytest.raises(ValueError):
            generate_token(projects=[], expires="1week")


# ─── revoke_token ─────────────────────────────────────────────────────────────

class TestRevokeToken:
    def test_revoke_existing(self, patch_tokens_path):
        from memoria.share import generate_token, revoke_token
        rec = generate_token(projects=[])
        result = revoke_token(rec["token"])
        assert result is True

    def test_revoked_token_gone_from_file(self, patch_tokens_path):
        from memoria.share import generate_token, revoke_token
        rec = generate_token(projects=[])
        revoke_token(rec["token"])
        data = json.loads(patch_tokens_path.read_text())
        assert rec["token"] not in data

    def test_revoke_nonexistent_returns_false(self):
        from memoria.share import revoke_token
        result = revoke_token("tok_doesnotexist")
        assert result is False

    def test_revoke_one_leaves_others(self, patch_tokens_path):
        from memoria.share import generate_token, revoke_token
        r1 = generate_token(projects=[])
        r2 = generate_token(projects=[])
        revoke_token(r1["token"])
        data = json.loads(patch_tokens_path.read_text())
        assert r1["token"] not in data
        assert r2["token"] in data


# ─── revoke_all ───────────────────────────────────────────────────────────────

class TestRevokeAll:
    def test_returns_count(self):
        from memoria.share import generate_token, revoke_all
        generate_token(projects=[])
        generate_token(projects=[])
        count = revoke_all()
        assert count == 2

    def test_clears_file(self, patch_tokens_path):
        from memoria.share import generate_token, revoke_all
        generate_token(projects=[])
        revoke_all()
        data = json.loads(patch_tokens_path.read_text())
        assert data == {}

    def test_empty_store_returns_zero(self):
        from memoria.share import revoke_all
        count = revoke_all()
        assert count == 0


# ─── list_tokens ──────────────────────────────────────────────────────────────

class TestListTokens:
    def test_empty_returns_empty_list(self):
        from memoria.share import list_tokens
        assert list_tokens() == []

    def test_active_token_in_list(self):
        from memoria.share import generate_token, list_tokens
        rec = generate_token(projects=[], label="Active")
        tokens = list_tokens()
        assert any(t["token"] == rec["token"] for t in tokens)

    def test_expired_token_flagged(self, patch_tokens_path):
        """Expired tokens appear in list but with is_expired=True."""
        from memoria.share import list_tokens
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        data = {
            "tok_expired123": {
                "token": "tok_expired123",
                "label": "Expired",
                "projects": [],
                "expires_at": past,
                "created_at": datetime.now().isoformat(),
            }
        }
        patch_tokens_path.write_text(json.dumps(data), encoding="utf-8")
        tokens = list_tokens()
        expired = next(t for t in tokens if t["token"] == "tok_expired123")
        assert expired["is_expired"] is True

    def test_non_expired_token_not_flagged(self):
        from memoria.share import generate_token, list_tokens
        generate_token(projects=[], expires="7d")
        tokens = list_tokens()
        assert all(not t.get("is_expired") for t in tokens)

    def test_multiple_tokens_all_listed(self):
        from memoria.share import generate_token, list_tokens
        r1 = generate_token(projects=[], label="A")
        r2 = generate_token(projects=[], label="B")
        r3 = generate_token(projects=[], label="C")
        tokens = list_tokens()
        ids = {t["token"] for t in tokens}
        assert {r1["token"], r2["token"], r3["token"]} <= ids


# ─── validate_token ───────────────────────────────────────────────────────────

class TestValidateToken:
    def test_valid_token_returns_record(self):
        from memoria.share import generate_token, validate_token
        rec = generate_token(projects=["P1"])
        result = validate_token(rec["token"])
        assert result is not None
        assert result["token"] == rec["token"]

    def test_unknown_token_returns_none(self):
        from memoria.share import validate_token
        result = validate_token("tok_doesnotexist")
        assert result is None

    def test_empty_string_returns_none(self):
        from memoria.share import validate_token
        assert validate_token("") is None

    def test_none_returns_none(self):
        from memoria.share import validate_token
        assert validate_token(None) is None

    def test_expired_token_returns_none(self, patch_tokens_path):
        from memoria.share import validate_token
        past = (datetime.now() - timedelta(seconds=1)).isoformat()
        data = {
            "tok_expired": {
                "token": "tok_expired",
                "label": "",
                "projects": [],
                "expires_at": past,
                "created_at": datetime.now().isoformat(),
            }
        }
        patch_tokens_path.write_text(json.dumps(data), encoding="utf-8")
        result = validate_token("tok_expired")
        assert result is None

    def test_expired_token_lazily_purged(self, patch_tokens_path):
        """After validating an expired token, it should be removed from storage."""
        from memoria.share import validate_token
        past = (datetime.now() - timedelta(seconds=1)).isoformat()
        data = {
            "tok_expired": {
                "token": "tok_expired",
                "label": "",
                "projects": [],
                "expires_at": past,
                "created_at": datetime.now().isoformat(),
            }
        }
        patch_tokens_path.write_text(json.dumps(data), encoding="utf-8")
        validate_token("tok_expired")
        remaining = json.loads(patch_tokens_path.read_text())
        assert "tok_expired" not in remaining

    def test_no_expiry_token_always_valid(self):
        from memoria.share import generate_token, validate_token
        rec = generate_token(projects=[])
        assert rec["expires_at"] is None
        result = validate_token(rec["token"])
        assert result is not None

    def test_future_expiry_is_valid(self):
        from memoria.share import generate_token, validate_token
        rec = generate_token(projects=[], expires="30d")
        result = validate_token(rec["token"])
        assert result is not None


# ─── is_project_allowed ───────────────────────────────────────────────────────

class TestIsProjectAllowed:
    def test_empty_projects_allows_all(self):
        from memoria.share import is_project_allowed
        token = {"projects": []}
        assert is_project_allowed(token, "AnyProject") is True
        assert is_project_allowed(token, "AnotherProject") is True

    def test_scoped_allows_listed(self):
        from memoria.share import is_project_allowed
        token = {"projects": ["ProjectA", "ProjectB"]}
        assert is_project_allowed(token, "ProjectA") is True

    def test_scoped_blocks_unlisted(self):
        from memoria.share import is_project_allowed
        token = {"projects": ["ProjectA"]}
        assert is_project_allowed(token, "ProjectC") is False

    def test_missing_projects_key_allows_all(self):
        from memoria.share import is_project_allowed
        token = {}  # no "projects" key
        assert is_project_allowed(token, "Anything") is True

    def test_none_projects_allows_all(self):
        from memoria.share import is_project_allowed
        token = {"projects": None}
        assert is_project_allowed(token, "Anything") is True

    def test_case_sensitive(self):
        from memoria.share import is_project_allowed
        token = {"projects": ["projecta"]}
        assert is_project_allowed(token, "ProjectA") is False
        assert is_project_allowed(token, "projecta") is True
