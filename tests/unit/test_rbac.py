"""
Unit tests for memoria/rbac.py — Role-Based Access Control engine.

Tests cover:
  - Role → Permission mapping completeness
  - Policy loading (missing file, disabled, malformed)
  - User identification (env var, config field, OS fallback)
  - Role resolution (direct, team, default)
  - Permission enforcement (allowed, denied, RBAC off)
  - Policy management helpers (grant, revoke, list, init)
  - PermissionDenied exception message content
  - Minimum-role-for-permission helper

No filesystem side effects — all file I/O is directed to tmp_path.
"""

import os
import yaml
import pytest
from pathlib import Path
from unittest.mock import patch


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _write_policy(tmp_path: Path, policy_dict: dict) -> Path:
    """Write a policy.yaml next to a fake config.yaml and return the config path."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text('model: "mock/mock"\n', encoding="utf-8")
    (tmp_path / "policy.yaml").write_text(
        yaml.dump({"rbac": policy_dict}), encoding="utf-8"
    )
    return cfg


def _policy_with_users(**users) -> dict:
    return {"enabled": True, "default_role": "contributor", "users": users}


# ─── Role → Permission mapping ────────────────────────────────────────────────

class TestRolePermissions:

    def test_viewer_has_only_read(self):
        from memoria.rbac import ROLE_PERMISSIONS, Role, Permission
        perms = ROLE_PERMISSIONS[Role.VIEWER]
        assert Permission.READ in perms
        assert Permission.WRITE not in perms
        assert Permission.REVIEW not in perms
        assert Permission.PULL not in perms
        assert Permission.GRAPH not in perms
        assert Permission.ADMIN not in perms

    def test_contributor_has_read_write_pull_graph(self):
        from memoria.rbac import ROLE_PERMISSIONS, Role, Permission
        perms = ROLE_PERMISSIONS[Role.CONTRIBUTOR]
        assert Permission.READ in perms
        assert Permission.WRITE in perms
        assert Permission.PULL in perms
        assert Permission.GRAPH in perms
        assert Permission.REVIEW not in perms
        assert Permission.ADMIN not in perms

    def test_reviewer_adds_review_over_contributor(self):
        from memoria.rbac import ROLE_PERMISSIONS, Role, Permission
        reviewer_perms    = ROLE_PERMISSIONS[Role.REVIEWER]
        contributor_perms = ROLE_PERMISSIONS[Role.CONTRIBUTOR]
        assert reviewer_perms > contributor_perms
        assert Permission.REVIEW in reviewer_perms

    def test_admin_has_all_permissions(self):
        from memoria.rbac import ROLE_PERMISSIONS, Role, Permission
        admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
        for p in Permission:
            assert p in admin_perms

    def test_roles_are_strictly_ordered(self):
        """Each higher role is a strict superset of the one below it."""
        from memoria.rbac import ROLE_PERMISSIONS, Role
        v  = ROLE_PERMISSIONS[Role.VIEWER]
        c  = ROLE_PERMISSIONS[Role.CONTRIBUTOR]
        r  = ROLE_PERMISSIONS[Role.REVIEWER]
        a  = ROLE_PERMISSIONS[Role.ADMIN]
        assert v < c < r < a


# ─── Policy loading ───────────────────────────────────────────────────────────

class TestLoadPolicy:

    def test_missing_file_returns_empty(self, tmp_path):
        from memoria.rbac import load_policy
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: mock\n", encoding="utf-8")
        assert load_policy(str(cfg)) == {}

    def test_loads_enabled_policy(self, tmp_path):
        from memoria.rbac import load_policy
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"alice": "admin"}})
        policy = load_policy(str(cfg))
        assert policy["enabled"] is True
        assert policy["users"]["alice"] == "admin"

    def test_disabled_flag_respected(self, tmp_path):
        from memoria.rbac import load_policy, is_rbac_enabled
        cfg = _write_policy(tmp_path, {"enabled": False, "users": {"alice": "admin"}})
        assert is_rbac_enabled(str(cfg)) is False

    def test_malformed_yaml_returns_empty(self, tmp_path):
        from memoria.rbac import load_policy
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: mock\n", encoding="utf-8")
        (tmp_path / "policy.yaml").write_text("::invalid: yaml:\n  - oops", encoding="utf-8")
        result = load_policy(str(cfg))
        assert result == {}

    def test_is_rbac_enabled_false_when_no_file(self, tmp_path):
        from memoria.rbac import is_rbac_enabled
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: mock\n", encoding="utf-8")
        assert is_rbac_enabled(str(cfg)) is False

    def test_is_rbac_enabled_true_when_file_exists(self, tmp_path):
        from memoria.rbac import is_rbac_enabled
        cfg = _write_policy(tmp_path, {"enabled": True})
        assert is_rbac_enabled(str(cfg)) is True


# ─── User identification ──────────────────────────────────────────────────────

class TestGetCurrentUser:

    def test_env_var_takes_priority(self, tmp_path):
        from memoria.rbac import get_current_user
        cfg = tmp_path / "config.yaml"
        cfg.write_text('model: mock\ncurrent_user: config_user\n', encoding="utf-8")
        with patch.dict(os.environ, {"MEMORIA_USER": "env_user"}):
            assert get_current_user(str(cfg)) == "env_user"

    def test_config_field_used_when_no_env_var(self, tmp_path):
        from memoria.rbac import get_current_user
        cfg = tmp_path / "config.yaml"
        cfg.write_text('model: mock\ncurrent_user: config_user\n', encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "MEMORIA_USER"}
        with patch.dict(os.environ, env, clear=True):
            assert get_current_user(str(cfg)) == "config_user"

    def test_falls_back_to_os_user(self, tmp_path):
        from memoria.rbac import get_current_user
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: mock\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "MEMORIA_USER"}
        with patch.dict(os.environ, env, clear=True), \
             patch("getpass.getuser", return_value="os_user"):
            assert get_current_user(str(cfg)) == "os_user"

    def test_unknown_returned_when_everything_fails(self, tmp_path):
        from memoria.rbac import get_current_user
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: mock\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "MEMORIA_USER"}
        with patch.dict(os.environ, env, clear=True), \
             patch("getpass.getuser", side_effect=Exception("no tty")):
            result = get_current_user(str(cfg))
        assert result == "unknown"


# ─── Role resolution ──────────────────────────────────────────────────────────

class TestGetUserRole:

    def test_direct_user_entry_resolved(self, tmp_path):
        from memoria.rbac import get_user_role, Role
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"alice": "admin"}})
        from memoria.rbac import load_policy
        policy = load_policy(str(cfg))
        assert get_user_role("alice", policy) == Role.ADMIN

    def test_team_membership_resolved(self, tmp_path):
        from memoria.rbac import get_user_role, Role
        cfg = _write_policy(tmp_path, {
            "enabled": True,
            "users": {},
            "teams": {
                "qa": {"members": ["bob", "carol"], "role": "reviewer"}
            }
        })
        from memoria.rbac import load_policy
        policy = load_policy(str(cfg))
        assert get_user_role("bob", policy) == Role.REVIEWER

    def test_default_role_used_for_unknown_user(self, tmp_path):
        from memoria.rbac import get_user_role, Role
        cfg = _write_policy(tmp_path, {
            "enabled": True, "default_role": "viewer", "users": {"alice": "admin"}
        })
        from memoria.rbac import load_policy
        policy = load_policy(str(cfg))
        assert get_user_role("unknown_user", policy) == Role.VIEWER

    def test_direct_entry_beats_team(self, tmp_path):
        """If a user appears in both users: and a team, users: wins."""
        from memoria.rbac import get_user_role, Role
        cfg = _write_policy(tmp_path, {
            "enabled": True,
            "users": {"dave": "admin"},
            "teams": {"qa": {"members": ["dave"], "role": "viewer"}}
        })
        from memoria.rbac import load_policy
        policy = load_policy(str(cfg))
        assert get_user_role("dave", policy) == Role.ADMIN

    def test_invalid_role_value_falls_back_to_default(self, tmp_path):
        from memoria.rbac import get_user_role, Role
        cfg = _write_policy(tmp_path, {
            "enabled": True,
            "default_role": "contributor",
            "users": {"eve": "superuser"}    # invalid role
        })
        from memoria.rbac import load_policy
        policy = load_policy(str(cfg))
        # "superuser" not a valid role — falls to default
        assert get_user_role("eve", policy) == Role.CONTRIBUTOR

    def test_case_insensitive_role_values(self, tmp_path):
        from memoria.rbac import get_user_role, Role
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"frank": "ADMIN"}})
        from memoria.rbac import load_policy
        policy = load_policy(str(cfg))
        assert get_user_role("frank", policy) == Role.ADMIN


# ─── Permission enforcement ───────────────────────────────────────────────────

class TestCheckPermission:

    def test_rbac_off_always_allows(self, tmp_path):
        """When no policy file exists, all checks pass silently."""
        from memoria.rbac import check_permission, Permission
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: mock\n", encoding="utf-8")
        # Should not raise for any permission
        for p in Permission:
            check_permission(p, str(cfg))   # no exception

    def test_rbac_disabled_flag_allows_all(self, tmp_path):
        from memoria.rbac import check_permission, Permission
        cfg = _write_policy(tmp_path, {
            "enabled": False,
            "users": {"alice": "viewer"}
        })
        with patch.dict(os.environ, {"MEMORIA_USER": "alice"}):
            for p in Permission:
                check_permission(p, str(cfg))   # viewer, but RBAC disabled — no exception

    def test_admin_allowed_everything(self, tmp_path):
        from memoria.rbac import check_permission, Permission
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"alice": "admin"}})
        with patch.dict(os.environ, {"MEMORIA_USER": "alice"}):
            for p in Permission:
                check_permission(p, str(cfg))   # no exception

    def test_viewer_denied_write(self, tmp_path):
        from memoria.rbac import check_permission, Permission, PermissionDenied
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"intern": "viewer"}})
        with patch.dict(os.environ, {"MEMORIA_USER": "intern"}):
            with pytest.raises(PermissionDenied):
                check_permission(Permission.WRITE, str(cfg))

    def test_viewer_denied_review(self, tmp_path):
        from memoria.rbac import check_permission, Permission, PermissionDenied
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"intern": "viewer"}})
        with patch.dict(os.environ, {"MEMORIA_USER": "intern"}):
            with pytest.raises(PermissionDenied):
                check_permission(Permission.REVIEW, str(cfg))

    def test_contributor_allowed_write_but_not_review(self, tmp_path):
        from memoria.rbac import check_permission, Permission, PermissionDenied
        cfg = _write_policy(tmp_path, {
            "enabled": True, "users": {"dev": "contributor"}
        })
        with patch.dict(os.environ, {"MEMORIA_USER": "dev"}):
            check_permission(Permission.WRITE, str(cfg))   # allowed
            with pytest.raises(PermissionDenied):
                check_permission(Permission.REVIEW, str(cfg))   # denied

    def test_reviewer_allowed_review(self, tmp_path):
        from memoria.rbac import check_permission, Permission
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"qalead": "reviewer"}})
        with patch.dict(os.environ, {"MEMORIA_USER": "qalead"}):
            check_permission(Permission.REVIEW, str(cfg))   # no exception

    def test_has_permission_returns_bool(self, tmp_path):
        from memoria.rbac import has_permission, Permission
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"intern": "viewer"}})
        with patch.dict(os.environ, {"MEMORIA_USER": "intern"}):
            assert has_permission(Permission.READ,  str(cfg)) is True
            assert has_permission(Permission.WRITE, str(cfg)) is False


# ─── PermissionDenied message ─────────────────────────────────────────────────

class TestPermissionDeniedMessage:

    def test_message_contains_username(self, tmp_path):
        from memoria.rbac import check_permission, Permission, PermissionDenied
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"intern": "viewer"}})
        with patch.dict(os.environ, {"MEMORIA_USER": "intern"}):
            with pytest.raises(PermissionDenied) as exc_info:
                check_permission(Permission.WRITE, str(cfg))
        assert "intern" in str(exc_info.value)

    def test_message_contains_role(self, tmp_path):
        from memoria.rbac import check_permission, Permission, PermissionDenied
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"intern": "viewer"}})
        with patch.dict(os.environ, {"MEMORIA_USER": "intern"}):
            with pytest.raises(PermissionDenied) as exc_info:
                check_permission(Permission.WRITE, str(cfg))
        assert "viewer" in str(exc_info.value)

    def test_message_contains_required_permission(self, tmp_path):
        from memoria.rbac import check_permission, Permission, PermissionDenied
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"intern": "viewer"}})
        with patch.dict(os.environ, {"MEMORIA_USER": "intern"}):
            with pytest.raises(PermissionDenied) as exc_info:
                check_permission(Permission.WRITE, str(cfg))
        assert "write" in str(exc_info.value)

    def test_message_suggests_minimum_role(self, tmp_path):
        from memoria.rbac import check_permission, Permission, PermissionDenied
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"intern": "viewer"}})
        with patch.dict(os.environ, {"MEMORIA_USER": "intern"}):
            with pytest.raises(PermissionDenied) as exc_info:
                check_permission(Permission.WRITE, str(cfg))
        msg = str(exc_info.value)
        # Should suggest a role that includes WRITE (contributor or higher)
        assert "contributor" in msg or "reviewer" in msg or "admin" in msg


# ─── Minimum role helper ──────────────────────────────────────────────────────

class TestMinimumRoleFor:

    def test_read_minimum_is_viewer(self):
        from memoria.rbac import _minimum_role_for, Permission, Role
        assert _minimum_role_for(Permission.READ) == Role.VIEWER

    def test_write_minimum_is_contributor(self):
        from memoria.rbac import _minimum_role_for, Permission, Role
        assert _minimum_role_for(Permission.WRITE) == Role.CONTRIBUTOR

    def test_review_minimum_is_reviewer(self):
        from memoria.rbac import _minimum_role_for, Permission, Role
        assert _minimum_role_for(Permission.REVIEW) == Role.REVIEWER

    def test_admin_minimum_is_admin(self):
        from memoria.rbac import _minimum_role_for, Permission, Role
        assert _minimum_role_for(Permission.ADMIN) == Role.ADMIN


# ─── Policy management ────────────────────────────────────────────────────────

class TestGrantRevoke:

    def test_grant_creates_policy_file(self, tmp_path):
        from memoria.rbac import grant_role, Role, load_policy
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: mock\n", encoding="utf-8")
        # No policy.yaml yet
        with patch("memoria.rbac._policy_path", return_value=tmp_path / "policy.yaml"):
            grant_role("bob", Role.REVIEWER, str(cfg))
            policy = load_policy(str(cfg))
        assert policy["users"]["bob"] == "reviewer"

    def test_grant_updates_existing_role(self, tmp_path):
        from memoria.rbac import grant_role, Role, load_policy
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"bob": "viewer"}})
        grant_role("bob", Role.ADMIN, str(cfg))
        policy = load_policy(str(cfg))
        assert policy["users"]["bob"] == "admin"

    def test_revoke_removes_user(self, tmp_path):
        from memoria.rbac import revoke_role, load_policy
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"alice": "admin", "bob": "viewer"}})
        result = revoke_role("bob", str(cfg))
        assert result is True
        policy = load_policy(str(cfg))
        assert "bob" not in policy.get("users", {})

    def test_revoke_returns_false_for_unknown_user(self, tmp_path):
        from memoria.rbac import revoke_role
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"alice": "admin"}})
        assert revoke_role("ghost", str(cfg)) is False

    def test_revoke_no_policy_returns_false(self, tmp_path):
        from memoria.rbac import revoke_role
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: mock\n", encoding="utf-8")
        assert revoke_role("nobody", str(cfg)) is False


class TestListAllRoles:

    def test_direct_users_listed(self, tmp_path):
        from memoria.rbac import list_all_roles
        policy = {"enabled": True, "users": {"alice": "admin", "bob": "viewer"}}
        rows = list_all_roles(policy)
        names = {r["username"] for r in rows}
        assert "alice" in names
        assert "bob" in names

    def test_team_members_listed(self, tmp_path):
        from memoria.rbac import list_all_roles
        policy = {
            "enabled": True,
            "users": {},
            "teams": {"qa": {"members": ["carol", "dave"], "role": "reviewer"}},
        }
        rows = list_all_roles(policy)
        names = {r["username"] for r in rows}
        assert "carol" in names
        assert "dave" in names

    def test_source_field_set_correctly(self, tmp_path):
        from memoria.rbac import list_all_roles
        policy = {
            "enabled": True,
            "users": {"alice": "admin"},
            "teams": {"qa": {"members": ["bob"], "role": "reviewer"}},
        }
        rows   = {r["username"]: r for r in list_all_roles(policy)}
        assert rows["alice"]["source"] == "user"
        assert "team:qa" in rows["bob"]["source"]

    def test_no_duplicates_user_beats_team(self, tmp_path):
        """A user in both users: and a team appears only once (via users: lookup)."""
        from memoria.rbac import list_all_roles
        policy = {
            "enabled": True,
            "users": {"dave": "admin"},
            "teams": {"qa": {"members": ["dave", "eve"], "role": "reviewer"}},
        }
        rows  = list_all_roles(policy)
        daves = [r for r in rows if r["username"] == "dave"]
        assert len(daves) == 1
        assert daves[0]["role"] == "admin"


class TestInitPolicy:

    def test_creates_policy_with_current_user_as_admin(self, tmp_path):
        from memoria.rbac import init_policy, load_policy
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: mock\n", encoding="utf-8")

        with patch("memoria.rbac._policy_path", return_value=tmp_path / "policy.yaml"), \
             patch("memoria.rbac.get_current_user", return_value="testadmin"):
            init_policy(config_path=str(cfg))
            policy = load_policy(str(cfg))

        assert policy["users"]["testadmin"] == "admin"
        assert policy["enabled"] is True

    def test_does_not_overwrite_existing(self, tmp_path):
        from memoria.rbac import init_policy, load_policy
        cfg = _write_policy(tmp_path, {"enabled": True, "users": {"original": "admin"}})
        init_policy(config_path=str(cfg))
        policy = load_policy(str(cfg))
        # Original should be untouched
        assert "original" in policy["users"]
