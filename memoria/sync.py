"""
Team Sync Layer — opt-in, privacy-first sharing of work graph data.

Each developer decides exactly what they export. Nothing leaves the machine
unless the user explicitly runs `memoria sync --export`. The central layer
merges individual exports into a company-wide view without any cloud service.

──────────────────────────────────────────────────────────────────────────────
Sync bundle format  (~/.memoria/sync/<id>.json)
──────────────────────────────────────────────────────────────────────────────

  {
    "schema_version": 1,
    "bundle_id":      "<uuid>",
    "exported_at":    "<ISO>",
    "exported_by":    "alice",          # optional display name
    "machine_id":     "<sha1>",         # hashed hostname — no PII
    "includes": {
      "activity":  true,
      "graph":     true,
      "org_chart": true
    },
    "activity":  [...],                 # subset of tracker events
    "graph":     {nodes, edges, ...},   # knowledge graph
    "org_chart": {projects, teams}      # ownership data
  }

──────────────────────────────────────────────────────────────────────────────
Merge rules
──────────────────────────────────────────────────────────────────────────────

  Activity  — union; deduplicated by (ts, repo, detail)
  Graph     — union of nodes + edges; last-write-wins for node metadata
  Org chart — union of projects + teams; last-write-wins

──────────────────────────────────────────────────────────────────────────────
Public API
──────────────────────────────────────────────────────────────────────────────

  export_bundle(output_path, include_activity, include_graph, include_org_chart,
                since, author_name, repos) → str  (path written)
  import_bundle(bundle_path) → dict  (parsed bundle)
  merge_bundles(bundles_dir, books_dir) → dict  (merged view)
  sync_status(bundles_dir) → list[dict]
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# ─── Constants ────────────────────────────────────────────────────────────────

MEMORIA_DIR  = Path.home() / ".memoria"
BUNDLES_DIR  = MEMORIA_DIR / "sync"
SCHEMA_VER   = 1


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _machine_id() -> str:
    """Return a stable, non-identifying hash of the hostname."""
    import socket
    return hashlib.sha1(socket.gethostname().encode()).hexdigest()[:12]


def _parse_since(since: Optional[str]) -> Optional[datetime]:
    """Parse '7d', '30d', '2w', 'all' into a datetime or None."""
    if not since or since.lower() == "all":
        return None
    since = since.lower().strip()
    if since.endswith("d"):
        return datetime.now() - timedelta(days=int(since[:-1]))
    if since.endswith("w"):
        return datetime.now() - timedelta(weeks=int(since[:-1]))
    if since.endswith("h"):
        return datetime.now() - timedelta(hours=int(since[:-1]))
    return None


# ─── Export ───────────────────────────────────────────────────────────────────

def export_bundle(
    output_path:       Optional[str] = None,
    include_activity:  bool = True,
    include_graph:     bool = True,
    include_org_chart: bool = True,
    since:             Optional[str] = "30d",
    author_name:       Optional[str] = None,
    repos:             Optional[list] = None,     # restrict activity to these repo paths
    books_dir:         Optional[str] = None,
) -> str:
    """
    Export a sync bundle to *output_path* (default: ~/.memoria/sync/<id>.json).

    Returns the path of the written file.
    """
    BUNDLES_DIR.mkdir(parents=True, exist_ok=True)
    bundle_id  = str(uuid.uuid4())[:8]
    since_dt   = _parse_since(since)

    bundle: dict = {
        "schema_version": SCHEMA_VER,
        "bundle_id":      bundle_id,
        "exported_at":    datetime.now().isoformat(),
        "exported_by":    author_name or "",
        "machine_id":     _machine_id(),
        "includes": {
            "activity":  include_activity,
            "graph":     include_graph,
            "org_chart": include_org_chart,
        },
        "activity":  [],
        "graph":     {},
        "org_chart": {},
    }

    # ── Activity ──────────────────────────────────────────────────────────────
    if include_activity:
        try:
            from .tracker import get_events
            events = get_events(since=since_dt, limit=5_000)
            if repos:
                repo_set = {str(Path(r).resolve()) for r in repos}
                events = [e for e in events if e.get("repo") in repo_set]
            # Strip absolute paths — use repo_name only for privacy
            sanitised = []
            for ev in events:
                sanitised.append({
                    "ts":         ev["ts"],
                    "event_type": ev["event_type"],
                    "repo_name":  ev.get("repo_name", ""),
                    "detail":     ev.get("detail", "")[:120],
                    "tags":       ev.get("tags", ""),
                })
            bundle["activity"] = sanitised
        except Exception:
            bundle["includes"]["activity"] = False

    # ── Graph ─────────────────────────────────────────────────────────────────
    if include_graph:
        try:
            if not books_dir:
                books_dir = str(MEMORIA_DIR / "books")
            from .graph import get_graph
            g = get_graph(books_dir)
            if g.get("nodes"):
                bundle["graph"] = g
            else:
                bundle["includes"]["graph"] = False
        except Exception:
            bundle["includes"]["graph"] = False

    # ── Org chart ─────────────────────────────────────────────────────────────
    if include_org_chart:
        try:
            from .org_chart import all_entries
            entries = all_entries()
            if entries.get("projects") or entries.get("teams"):
                bundle["org_chart"] = entries
            else:
                bundle["includes"]["org_chart"] = False
        except Exception:
            bundle["includes"]["org_chart"] = False

    # ── Write ─────────────────────────────────────────────────────────────────
    if not output_path:
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"sync_{bundle_id}_{ts}.json"
        output_path = str(BUNDLES_DIR / fname)

    Path(output_path).write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


# ─── Import ───────────────────────────────────────────────────────────────────

def import_bundle(bundle_path: str) -> dict:
    """
    Copy a peer's bundle into ~/.memoria/sync/ and return its parsed content.

    Raises ValueError on schema mismatch or corrupt file.
    """
    raw = Path(bundle_path).read_text(encoding="utf-8")
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt bundle — invalid JSON: {exc}") from exc

    if bundle.get("schema_version") != SCHEMA_VER:
        ver = bundle.get("schema_version", "?")
        raise ValueError(
            f"Bundle schema v{ver} not supported (expected v{SCHEMA_VER})"
        )

    BUNDLES_DIR.mkdir(parents=True, exist_ok=True)
    dest = BUNDLES_DIR / Path(bundle_path).name
    dest.write_text(raw, encoding="utf-8")
    return bundle


# ─── Merge ────────────────────────────────────────────────────────────────────

def merge_bundles(
    bundles_dir: Optional[str] = None,
    books_dir:   Optional[str] = None,
) -> dict:
    """
    Merge all sync bundles in *bundles_dir* into a unified company view.

    Returns a merged dict with keys:
      activity, graph, org_chart, contributors, merged_at
    """
    bd = Path(bundles_dir or BUNDLES_DIR)
    if not bd.exists():
        return _empty_merged()

    bundle_files = sorted(bd.glob("sync_*.json"))
    if not bundle_files:
        return _empty_merged()

    merged_activity: list  = []
    merged_graph:    dict  = {"nodes": {}, "edges": []}
    merged_org:      dict  = {"projects": {}, "teams": {}}
    contributors:    list  = []
    seen_events: set       = set()

    for bf in bundle_files:
        try:
            b = json.loads(bf.read_text(encoding="utf-8"))
        except Exception:
            continue

        if b.get("schema_version") != SCHEMA_VER:
            continue

        name = b.get("exported_by") or b.get("machine_id", "unknown")
        contributors.append({
            "name":       name,
            "exported_at": b.get("exported_at", ""),
            "bundle_id":  b.get("bundle_id", ""),
            "activity_events": len(b.get("activity", [])),
        })

        # Activity — union, deduplicated
        for ev in b.get("activity", []):
            key = (ev.get("ts", ""), ev.get("repo_name", ""), ev.get("detail", ""))
            if key not in seen_events:
                seen_events.add(key)
                merged_activity.append(ev)

        # Graph — union nodes (last-write-wins on node metadata), union edges
        for proj, node in b.get("graph", {}).get("nodes", {}).items():
            if proj not in merged_graph["nodes"]:
                merged_graph["nodes"][proj] = node
            else:
                # Merge: last bundle adds/overrides fields
                merged_graph["nodes"][proj].update(node)

        for edge in b.get("graph", {}).get("edges", []):
            # Deduplicate edges by (source, target, relation)
            key_e = (edge.get("source"), edge.get("target"), edge.get("relation"))
            existing = [
                (e.get("source"), e.get("target"), e.get("relation"))
                for e in merged_graph["edges"]
            ]
            if key_e not in existing:
                merged_graph["edges"].append(edge)

        # Org chart — union, last-write-wins
        for proj, entry in b.get("org_chart", {}).get("projects", {}).items():
            if proj not in merged_org["projects"]:
                merged_org["projects"][proj] = entry
            else:
                merged_org["projects"][proj].update(entry)

        for team, entry in b.get("org_chart", {}).get("teams", {}).items():
            if team not in merged_org["teams"]:
                merged_org["teams"][team] = entry
            else:
                merged_org["teams"][team].update(entry)

    merged_activity.sort(key=lambda x: x.get("ts", ""), reverse=True)

    return {
        "merged_at":    datetime.now().isoformat(),
        "contributors": contributors,
        "activity":     merged_activity,
        "graph":        merged_graph,
        "org_chart":    merged_org,
        "stats": {
            "bundles":      len(bundle_files),
            "events":       len(merged_activity),
            "projects":     len(merged_graph["nodes"]),
            "relationships": len(merged_graph["edges"]),
            "org_entries":  len(merged_org["projects"]),
        },
    }


def _empty_merged() -> dict:
    return {
        "merged_at":    datetime.now().isoformat(),
        "contributors": [],
        "activity":     [],
        "graph":        {"nodes": {}, "edges": []},
        "org_chart":    {"projects": {}, "teams": {}},
        "stats":        {
            "bundles": 0, "events": 0, "projects": 0,
            "relationships": 0, "org_entries": 0,
        },
    }


# ─── Status ───────────────────────────────────────────────────────────────────

def sync_status(bundles_dir: Optional[str] = None) -> list[dict]:
    """
    Return metadata for every sync bundle in *bundles_dir*.
    Sorted newest-first.
    """
    bd = Path(bundles_dir or BUNDLES_DIR)
    if not bd.exists():
        return []

    result = []
    for bf in sorted(bd.glob("sync_*.json"), reverse=True):
        try:
            b = json.loads(bf.read_text(encoding="utf-8"))
            result.append({
                "file":          bf.name,
                "bundle_id":     b.get("bundle_id", ""),
                "exported_by":   b.get("exported_by") or b.get("machine_id", "—"),
                "exported_at":   b.get("exported_at", ""),
                "events":        len(b.get("activity", [])),
                "graph_nodes":   len(b.get("graph", {}).get("nodes", {})),
                "org_entries":   len(b.get("org_chart", {}).get("projects", {})),
                "includes":      b.get("includes", {}),
            })
        except Exception:
            result.append({"file": bf.name, "error": "corrupt"})
    return result
