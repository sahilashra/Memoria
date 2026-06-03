"""
Audit log — append-only SQLite record of every write action taken in Memoria.

Schema (audit_log table):
    id          INTEGER PRIMARY KEY AUTOINCREMENT
    ts          TEXT     — ISO-8601 UTC timestamp
    actor       TEXT     — username (from RBAC or OS login)
    action      TEXT     — e.g. "generate_book", "mcp_tool_call", "share_token_created"
    target      TEXT     — project name, file path, token label, etc.
    detail      TEXT     — JSON or free-text extra context
    ok          INTEGER  — 1 success, 0 failure

Usage::

    from memoria.audit import log_action, query_log, export_csv

    log_action("generate_book", target="auth-service", actor="alice")
    rows = query_log(actor="alice", limit=50)
    csv_text = export_csv(rows)
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── DB location ──────────────────────────────────────────────────────────────

def _db_path(config_path: str = "config.yaml") -> Path:
    from pathlib import Path as _P
    cfg = _P(config_path)
    if cfg.is_file():
        candidate = cfg.parent / "audit.db"
    else:
        candidate = _P.home() / ".memoria" / "audit.db"
    return candidate


# ─── Connection pool (per-thread, one conn per DB file) ──────────────────────

_local = threading.local()


def _conn(config_path: str) -> sqlite3.Connection:
    db = str(_db_path(config_path))
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "db", None) != db:
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
        _local.db = db
        _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT    NOT NULL,
            actor   TEXT    NOT NULL DEFAULT '',
            action  TEXT    NOT NULL,
            target  TEXT    NOT NULL DEFAULT '',
            detail  TEXT    NOT NULL DEFAULT '',
            ok      INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit_log(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor  ON audit_log(actor)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
    conn.commit()


# ─── Write ────────────────────────────────────────────────────────────────────

def log_action(
    action: str,
    target: str = "",
    detail: str | dict = "",
    actor: str = "",
    ok: bool = True,
    config_path: str = "config.yaml",
) -> int:
    """
    Append one audit record. Returns the row id.

    Parameters
    ----------
    action  : short verb-noun label, e.g. "generate_book", "mcp_tool_call"
    target  : project name, file, token label, or other object
    detail  : free text or dict (auto-serialized to JSON)
    actor   : username; resolved from env/config if empty
    ok      : True = success, False = failure
    """
    if not actor:
        try:
            from .rbac import get_current_user
            actor = get_current_user(config_path)
        except Exception:
            actor = os.environ.get("MEMORIA_USER", "") or "unknown"

    if isinstance(detail, dict):
        detail = json.dumps(detail, ensure_ascii=False)

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = _conn(config_path)
    cur = conn.execute(
        "INSERT INTO audit_log (ts, actor, action, target, detail, ok) VALUES (?,?,?,?,?,?)",
        (ts, actor, action, target, str(detail), 1 if ok else 0),
    )
    conn.commit()
    return cur.lastrowid


# ─── Read ─────────────────────────────────────────────────────────────────────

def query_log(
    actor: str = "",
    action: str = "",
    target: str = "",
    ok: Optional[bool] = None,
    since: str = "",          # ISO date string, e.g. "2026-01-01"
    limit: int = 200,
    offset: int = 0,
    config_path: str = "config.yaml",
) -> list[dict]:
    """
    Query audit records. All filters are optional substring matches.
    Returns newest-first list of dicts with keys: id, ts, actor, action, target, detail, ok.
    """
    conn = _conn(config_path)
    clauses = []
    params: list = []

    if actor:
        clauses.append("actor LIKE ?")
        params.append(f"%{actor}%")
    if action:
        clauses.append("action LIKE ?")
        params.append(f"%{action}%")
    if target:
        clauses.append("target LIKE ?")
        params.append(f"%{target}%")
    if ok is not None:
        clauses.append("ok = ?")
        params.append(1 if ok else 0)
    if since:
        clauses.append("ts >= ?")
        params.append(since)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params += [limit, offset]
    sql = f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ? OFFSET ?"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_log(
    config_path: str = "config.yaml",
    actor: str = "",
    action: str = "",
    target: str = "",
    ok: Optional[bool] = None,
    since: str = "",
) -> int:
    """Total number of audit records matching the given filters (or all if no filters)."""
    conn = _conn(config_path)
    clauses = []
    params: list = []
    if actor:
        clauses.append("actor LIKE ?")
        params.append(f"%{actor}%")
    if action:
        clauses.append("action LIKE ?")
        params.append(f"%{action}%")
    if target:
        clauses.append("target LIKE ?")
        params.append(f"%{target}%")
    if ok is not None:
        clauses.append("ok = ?")
        params.append(1 if ok else 0)
    if since:
        clauses.append("ts >= ?")
        params.append(since)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params).fetchone()[0]


# ─── Export ───────────────────────────────────────────────────────────────────

def export_csv(rows: list[dict]) -> str:
    """Serialize a list of audit row dicts to CSV string."""
    if not rows:
        return "id,ts,actor,action,target,detail,ok\n"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id", "ts", "actor", "action", "target", "detail", "ok"])
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
