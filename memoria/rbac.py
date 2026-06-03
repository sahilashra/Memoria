"""
Role-Based Access Control (RBAC) for Memoria.

Provides a lightweight, team-friendly permission layer that:
  - Does nothing when RBAC is disabled or no policy file exists (fully backward compatible)
  - Applies to all write/review/admin operations when enabled
  - Identifies the current user from MEMORIA_USER env var, config, or OS login

Roles (least → most privileged)
────────────────────────────────
  viewer      — read Memory Banks, search, ask questions, view graph
  contributor — viewer + generate/update books, pull MCP sources, build graph
  reviewer    — contributor + approve/reject drafts
  admin       — reviewer + manage RBAC policy (grant/revoke roles, init)

Permissions
───────────
  read    — memoria ask, search, list, graph show/query/impact
  write   — memoria analyze, update, split, merge
  review  — memoria review (approve or reject a draft)
  pull    — memoria pull, schedule run/start
  graph   — memoria graph build
  admin   — memoria access grant/revoke, init

Policy file
───────────
  ~/.memoria/policy.yaml   (global — applies to all books directories)

  rbac:
    enabled: true
    default_role: contributor   # role assigned to unknown users
    users:
      alice: admin
      bob: reviewer
      charlie: contributor
      intern: viewer
    teams:
      qa_team:
        members: [dave, eve]
        role: reviewer

Usage
─────
  from memoria.rbac import check_permission, Permission

  # Raise PermissionError if current user cannot write:
  check_permission(Permission.WRITE, config_path)

  # Boolean check:
  if has_permission(Permission.REVIEW, config_path):
      ...

  # Identify current user:
  user = get_current_user(config_path)
"""

import getpass
import os
from enum import Enum
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set

import yaml


# ─── Enums ────────────────────────────────────────────────────────────────────

class Permission(str, Enum):
    READ   = "read"    # view books, search, ask, list, graph read
    WRITE  = "write"   # generate/update books, split, merge
    REVIEW = "review"  # approve or reject drafts
    PULL   = "pull"    # pull MCP sources, schedule
    GRAPH  = "graph"   # build knowledge graph
    ADMIN  = "admin"   # manage policy, init


class Role(str, Enum):
    VIEWER      = "viewer"
    CONTRIBUTOR = "contributor"
    REVIEWER    = "reviewer"
    ADMIN       = "admin"


# ─── Role → Permission mapping ────────────────────────────────────────────────

ROLE_PERMISSIONS: Dict[Role, FrozenSet[Permission]] = {
    Role.VIEWER: frozenset({
        Permission.READ,
    }),
    Role.CONTRIBUTOR: frozenset({
        Permission.READ,
        Permission.WRITE,
        Permission.PULL,
        Permission.GRAPH,
    }),
    Role.REVIEWER: frozenset({
        Permission.READ,
        Permission.WRITE,
        Permission.PULL,
        Permission.GRAPH,
        Permission.REVIEW,
    }),
    Role.ADMIN: frozenset(Permission),   # all permissions
}

_VALID_ROLES = {r.value for r in Role}


# ─── Policy loading / saving ──────────────────────────────────────────────────

def _policy_path(config_path: str) -> Path:
    """
    Resolve the policy file location.
    Looks for  <config_dir>/policy.yaml  first (co-located with config.yaml).
    Falls back to the Memoria home directory (~/.memoria/policy.yaml).
    """
    cfg = Path(config_path)
    # If config_path is a file, look in its parent directory
    if cfg.is_file():
        candidate = cfg.parent / "policy.yaml"
    else:
        candidate = cfg / "policy.yaml"

    if candidate.exists():
        return candidate

    # Default location
    return Path.home() / ".memoria" / "policy.yaml"


def load_policy(config_path: str = "config.yaml") -> dict:
    """
    Load the RBAC policy from the policy.yaml file.
    Returns an empty dict (RBAC disabled) if the file does not exist.
    """
    path = _policy_path(config_path)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data.get("rbac", {})
    except Exception:
        return {}


def save_policy(policy: dict, config_path: str = "config.yaml") -> Path:
    """
    Persist the policy dict to the policy.yaml file.
    Creates the directory if it doesn't exist.
    Returns the path that was written.
    """
    path = _policy_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load any existing file first (to preserve comments / other keys)
    existing: dict = {}
    if path.exists():
        try:
            existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            existing = {}

    existing["rbac"] = policy
    path.write_text(yaml.dump(existing, default_flow_style=False, sort_keys=False),
                    encoding="utf-8")
    return path


def is_rbac_enabled(config_path: str = "config.yaml") -> bool:
    """True if a policy file exists AND rbac.enabled is not explicitly False."""
    policy = load_policy(config_path)
    if not policy:
        return False   # no file → RBAC not configured → open access
    return policy.get("enabled", True)


# ─── User identification ──────────────────────────────────────────────────────

def get_current_user(config_path: str = "config.yaml") -> str:
    """
    Identify the current Memoria user.
    Priority:
      1. MEMORIA_USER environment variable
      2. current_user: key in config.yaml
      3. OS login name (getpass.getuser)
    """
    # 1 — env var
    env_user = os.environ.get("MEMORIA_USER", "").strip()
    if env_user:
        return env_user

    # 2 — config.yaml field
    try:
        cfg = Path(config_path)
        if cfg.is_file():
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            cfg_user = str(data.get("current_user", "")).strip()
            if cfg_user:
                return cfg_user
    except Exception:
        pass

    # 3 — OS login
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


# ─── Role resolution ──────────────────────────────────────────────────────────

def get_user_role(username: str, policy: dict) -> Role:
    """
    Resolve the Role for username using the policy dict.

    Lookup order:
      1. Direct user entry in policy["users"]
      2. First team whose members list includes the username
      3. policy["default_role"]  (falls back to "contributor" if not set)
    """
    # 1 — direct user entry
    users: dict = policy.get("users", {}) or {}
    if username in users:
        raw = str(users[username]).lower()
        if raw in _VALID_ROLES:
            return Role(raw)

    # 2 — team membership
    teams: dict = policy.get("teams", {}) or {}
    for _team_name, team_info in teams.items():
        if not isinstance(team_info, dict):
            continue
        members = team_info.get("members", []) or []
        if username in members:
            raw = str(team_info.get("role", "")).lower()
            if raw in _VALID_ROLES:
                return Role(raw)

    # 3 — default role
    default_raw = str(policy.get("default_role", "contributor")).lower()
    if default_raw in _VALID_ROLES:
        return Role(default_raw)

    return Role.CONTRIBUTOR   # hard fallback


def get_user_permissions(username: str, policy: dict) -> FrozenSet[Permission]:
    """Return the full permission set for username under the given policy."""
    role = get_user_role(username, policy)
    return ROLE_PERMISSIONS[role]


# ─── Enforcement ──────────────────────────────────────────────────────────────

class PermissionDenied(PermissionError):
    """Raised when an RBAC check fails."""
    def __init__(self, user: str, role: Role, required: Permission):
        self.user     = user
        self.role     = role
        self.required = required
        allowed = sorted(p.value for p in ROLE_PERMISSIONS[role])
        super().__init__(
            f"Access denied: '{user}' has role '{role.value}' "
            f"which does not include the '{required.value}' permission.\n"
            f"  Your permissions: {', '.join(allowed)}\n"
            f"  Ask an admin to grant you the '{_minimum_role_for(required).value}' role or higher."
        )


def _minimum_role_for(permission: Permission) -> Role:
    """Return the lowest role that includes the given permission."""
    for role in [Role.VIEWER, Role.CONTRIBUTOR, Role.REVIEWER, Role.ADMIN]:
        if permission in ROLE_PERMISSIONS[role]:
            return role
    return Role.ADMIN


def check_permission(permission: Permission, config_path: str = "config.yaml") -> None:
    """
    Assert that the current user has `permission`.

    - If RBAC is disabled or no policy file exists → silently returns (no-op).
    - If the user has the required permission → silently returns.
    - If the user lacks the permission → raises PermissionDenied.

    This is the primary enforcement function. Call it at the top of any
    command that should be permission-gated.
    """
    if not is_rbac_enabled(config_path):
        return   # RBAC off — allow everything

    policy = load_policy(config_path)
    user   = get_current_user(config_path)
    role   = get_user_role(user, policy)
    perms  = ROLE_PERMISSIONS[role]

    if permission not in perms:
        raise PermissionDenied(user, role, permission)


def has_permission(permission: Permission, config_path: str = "config.yaml") -> bool:
    """
    Boolean version of check_permission.
    Returns True if the current user has the permission (or RBAC is disabled).
    """
    try:
        check_permission(permission, config_path)
        return True
    except PermissionDenied:
        return False


# ─── Policy management helpers ────────────────────────────────────────────────

def grant_role(username: str, role: Role, config_path: str = "config.yaml") -> None:
    """
    Add or update a user's role in the policy file.
    Creates the policy file if it doesn't exist.
    """
    policy = load_policy(config_path)
    if not policy:
        policy = {"enabled": True, "default_role": "contributor", "users": {}}

    users = policy.setdefault("users", {})
    users[username] = role.value
    save_policy(policy, config_path)


def revoke_role(username: str, config_path: str = "config.yaml") -> bool:
    """
    Remove a user from the policy file.
    Returns True if the user was found and removed, False if they weren't in the policy.
    """
    policy = load_policy(config_path)
    if not policy:
        return False

    users = policy.get("users", {}) or {}
    if username not in users:
        return False

    del users[username]
    policy["users"] = users
    save_policy(policy, config_path)
    return True


def list_all_roles(policy: dict) -> List[dict]:
    """
    Return a flat list of {username, role, source} for display.
    source is "user" or "team:<team_name>".
    """
    result: List[dict] = []
    seen: Set[str] = set()

    # Direct user assignments
    for username, raw_role in (policy.get("users", {}) or {}).items():
        r = str(raw_role).lower()
        if r in _VALID_ROLES:
            result.append({"username": username, "role": r, "source": "user"})
            seen.add(username)

    # Team assignments
    for team_name, team_info in (policy.get("teams", {}) or {}).items():
        if not isinstance(team_info, dict):
            continue
        raw_role = str(team_info.get("role", "")).lower()
        if raw_role not in _VALID_ROLES:
            continue
        for member in (team_info.get("members", []) or []):
            if member not in seen:
                result.append({"username": member, "role": raw_role,
                                "source": f"team:{team_name}"})
                seen.add(member)

    result.sort(key=lambda x: (x["role"], x["username"]))
    return result


def init_policy(
    default_role: str = "contributor",
    config_path: str = "config.yaml",
) -> Path:
    """
    Create a new policy.yaml with sensible defaults.
    If a policy already exists, does not overwrite it.
    Returns the path.
    """
    path = _policy_path(config_path)
    if path.exists():
        return path

    current_user = get_current_user(config_path)
    policy = {
        "enabled": True,
        "default_role": default_role,
        "users": {current_user: "admin"},
        "teams": {},
    }
    return save_policy(policy, config_path)


# ─── Scoped views (team / department / org) ───────────────────────────────────
#
# Policy extension:
#
#   rbac:
#     ...
#     scopes:
#       backend:
#         projects: [auth-service, api-gateway]
#         description: "Backend team view"
#       org:
#         projects: "*"   # wildcard — all projects
#     user_scopes:
#       alice: org         # string → single scope
#       bob: backend       # bob sees only backend projects
#       charlie: [backend, payments]  # list → union of scopes

_WILDCARD = "*"


def get_scope_projects(scope_name: str, policy: dict) -> Optional[list]:
    """
    Return the list of project names in *scope_name*, or None if the scope
    is a wildcard / not defined.
    """
    scopes: dict = policy.get("scopes", {}) or {}
    if scope_name not in scopes:
        return None
    projects = scopes[scope_name].get("projects", _WILDCARD)
    if projects == _WILDCARD or projects == ["*"]:
        return None   # no filter — all projects visible
    return list(projects) if isinstance(projects, (list, tuple)) else [str(projects)]


def get_accessible_projects(
    username: str,
    policy: dict,
    all_project_names: List[str],
) -> Optional[List[str]]:
    """
    Return the subset of *all_project_names* that *username* may access,
    based on the ``user_scopes`` and ``scopes`` blocks in the policy.

    Returns:
      None  → no restriction (user has org-wide access or no scopes configured)
      list  → the allowed project names (may be empty if user has no projects)

    Admins always get None (unrestricted).
    """
    # Admins are never restricted
    role = get_user_role(username, policy)
    if role == Role.ADMIN:
        return None

    user_scopes_raw: dict = policy.get("user_scopes", {}) or {}
    if username not in user_scopes_raw:
        # No scope restriction — inherit default (unrestricted)
        return None

    assigned = user_scopes_raw[username]
    if not assigned:
        return None
    if isinstance(assigned, str):
        assigned = [assigned]

    allowed_set: set = set()
    wildcard_found = False

    for scope_name in assigned:
        projects = get_scope_projects(scope_name, policy)
        if projects is None:
            wildcard_found = True
            break
        allowed_set.update(projects)

    if wildcard_found:
        return None   # at least one of their scopes is org-wide

    return [p for p in all_project_names if p in allowed_set]


def set_scope(
    scope_name: str,
    projects: List[str],
    description: str = "",
    config_path: str = "config.yaml",
) -> None:
    """Create or update a scope in the policy file."""
    policy = load_policy(config_path)
    if not policy:
        policy = {"enabled": True, "default_role": "contributor", "users": {}}
    scopes = policy.setdefault("scopes", {})
    scopes[scope_name] = {
        "projects":    projects if projects != [_WILDCARD] else _WILDCARD,
        "description": description,
    }
    save_policy(policy, config_path)


def assign_scope(
    username: str,
    scope_names: List[str],
    config_path: str = "config.yaml",
) -> None:
    """Assign one or more scopes to a user."""
    policy = load_policy(config_path)
    if not policy:
        policy = {"enabled": True, "default_role": "contributor", "users": {}}
    user_scopes = policy.setdefault("user_scopes", {})
    user_scopes[username] = scope_names if len(scope_names) > 1 else scope_names[0]
    save_policy(policy, config_path)


def remove_scope(scope_name: str, config_path: str = "config.yaml") -> bool:
    """Delete a scope. Returns True if it existed."""
    policy = load_policy(config_path)
    scopes = policy.get("scopes", {}) or {}
    if scope_name not in scopes:
        return False
    del scopes[scope_name]
    policy["scopes"] = scopes
    save_policy(policy, config_path)
    return True


def list_scopes(policy: dict) -> List[dict]:
    """Return all defined scopes as a list of dicts."""
    scopes: dict = policy.get("scopes", {}) or {}
    user_scopes: dict = policy.get("user_scopes", {}) or {}

    # Invert user_scopes → scope → members
    scope_members: Dict[str, List[str]] = {}
    for user, assigned in user_scopes.items():
        names = [assigned] if isinstance(assigned, str) else list(assigned)
        for sn in names:
            scope_members.setdefault(sn, []).append(user)

    result = []
    for sn, info in scopes.items():
        projects = info.get("projects", _WILDCARD)
        result.append({
            "scope":       sn,
            "projects":    projects,
            "description": info.get("description", ""),
            "members":     scope_members.get(sn, []),
        })
    return sorted(result, key=lambda x: x["scope"])
