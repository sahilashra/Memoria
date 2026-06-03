"""
Intra-project File Graph — relationships between sub-units within one project.

Separate from the inter-project graph.py (which relates projects to each other).
This module relates sub-units (packages, test dirs, modules) WITHIN one project.

Storage: books/{project}/.file_graph.json
         (distinct from the inter-project books/.graph.json)

Edge types:
  "tests"         — test sub-unit covers source sub-unit (name-based heuristic)
  "depends_on"    — static import analysis (Python/JS/TS)
  "calls_api"     — HTTP client pattern detected in source files
  "same_domain"   — LLM-detected semantic overlap (Pass B, requires _mb.md files)
  "shares_types"  — LLM-detected shared data structures
  "implements"    — LLM-detected implementation relationship

Pass A (static, no LLM): always runs, fast.
Pass B (LLM semantic): optional, runs only when _mb.md files exist for both nodes.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

FILE_GRAPH_NAME = ".file_graph.json"
FILE_GRAPH_VERSION = 1

# ─── Data model ───────────────────────────────────────────────────────────────

def _edge(source: str, target: str, etype: str, confidence: float, reason: str) -> dict:
    return {
        "source": source,
        "target": target,
        "type": etype,
        "confidence": round(confidence, 2),
        "reason": reason,
    }


# ─── Pass A helpers ───────────────────────────────────────────────────────────

def _is_test_unit(unit_name: str) -> bool:
    n = Path(unit_name).name.lower()
    return (
        n in {"test", "tests", "__tests__", "spec", "specs"}
        or n.startswith("test_")
        or n.endswith("_test")
        or n.endswith("_tests")
    )


def _source_files(unit_path: Path, extensions: set[str]) -> list[Path]:
    try:
        return [f for f in unit_path.rglob("*") if f.is_file() and f.suffix in extensions]
    except PermissionError:
        return []


def _extract_python_imports(file_path: Path) -> list[str]:
    """Return a list of dotted module names imported by file_path (best-effort)."""
    try:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(file_path))
    except Exception:
        return []

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def _extract_js_imports(file_path: Path) -> list[str]:
    """Extract import paths from JS/TS files via regex (no full parse)."""
    try:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    # Match: import ... from '...' / require('...')
    pattern = r"""(?:import\s+.*?from\s+|require\s*\(\s*)['"]([^'"]+)['"]"""
    return re.findall(pattern, source)


_HTTP_CLIENT_PATTERNS = re.compile(
    r"""(?:requests|httpx|aiohttp|axios|fetch|got|node-fetch)"""
    r"""\s*\.\s*(?:get|post|put|patch|delete|request)\s*\(""",
    re.IGNORECASE,
)


def _has_http_calls(unit_path: Path) -> bool:
    """Return True if any source file in this unit makes HTTP client calls."""
    for f in _source_files(unit_path, {".py", ".js", ".ts", ".jsx", ".tsx"}):
        try:
            if _HTTP_CLIENT_PATTERNS.search(f.read_text(encoding="utf-8", errors="ignore")):
                return True
        except Exception:
            pass
    return False


# ─── Pass A: static analysis ──────────────────────────────────────────────────

def _pass_a_edges(units: list[dict], repo_path: Path) -> list[dict]:
    """
    Build edges from static analysis (no LLM).

    units: list of {path: Path, name: str, type: str}
    """
    edges: list[dict] = []
    unit_by_name = {u["name"]: u for u in units}
    unit_names = list(unit_by_name.keys())

    for unit in units:
        unit_path = unit["path"]
        unit_name = unit["name"]

        # ── Rule 1: test ↔ source name matching ──────────────────────────────
        if _is_test_unit(unit_name):
            # Try to match the test unit to a source unit by name similarity
            test_stem = Path(unit_name).name.lower().lstrip("_")
            for src_name in unit_names:
                if src_name == unit_name:
                    continue
                src_stem = Path(src_name).name.lower()
                # "tests" tests everything at the same depth level
                if test_stem in {"test", "tests", "spec", "specs"}:
                    if Path(src_name).parent == Path(unit_name).parent:
                        edges.append(_edge(
                            unit_name, src_name, "tests", 0.9,
                            f"sibling test directory covering {src_name}",
                        ))
                elif test_stem.replace("test_", "").replace("_test", "") == src_stem:
                    edges.append(_edge(
                        unit_name, src_name, "tests", 0.95,
                        f"test unit name matches source unit name",
                    ))
            continue  # skip import analysis for test dirs

        # ── Rule 2: import-based depends_on ──────────────────────────────────
        py_files = _source_files(unit_path, {".py"})
        js_files = _source_files(unit_path, {".js", ".ts", ".jsx", ".tsx", ".mjs"})

        imported_modules: set[str] = set()
        for f in py_files:
            imported_modules.update(_extract_python_imports(f))
        for f in js_files:
            imported_modules.update(_extract_js_imports(f))

        for imp in imported_modules:
            # Check if the import path corresponds to another sub-unit
            imp_parts = imp.replace("\\", "/").split(".")
            for other_name in unit_names:
                if other_name == unit_name:
                    continue
                other_parts = Path(other_name).parts
                # Match if the import path starts with the other unit's path components
                imp_lower = imp.lower().replace(".", "/")
                other_lower = "/".join(other_parts).lower()
                if imp_lower.startswith(other_lower) or other_lower in imp_lower:
                    edges.append(_edge(
                        unit_name, other_name, "depends_on", 0.8,
                        f"import '{imp}' resolved to sub-unit {other_name}",
                    ))
                    break

        # ── Rule 3: HTTP client calls ─────────────────────────────────────────
        if _has_http_calls(unit_path):
            edges.append(_edge(
                unit_name, "__external__", "calls_api", 0.7,
                "HTTP client calls detected in source files",
            ))

    # Deduplicate edges (same source+target+type → keep highest confidence)
    seen: dict[tuple, dict] = {}
    for e in edges:
        key = (e["source"], e["target"], e["type"])
        if key not in seen or e["confidence"] > seen[key]["confidence"]:
            seen[key] = e
    return list(seen.values())


# ─── Pass B: LLM semantic edges ───────────────────────────────────────────────

_SEMANTIC_PROMPT = """\
You are comparing two software module summaries to detect semantic relationships.

MODULE A: {name_a}
{summary_a}

MODULE B: {name_b}
{summary_b}

Determine if there is a meaningful semantic relationship between these modules.
Return a single JSON object (no markdown fences):

{{
  "relationship": "same_domain" | "shares_types" | "implements" | "none",
  "direction": "a_to_b" | "b_to_a" | "bidirectional" | "none",
  "confidence": 0.0-1.0,
  "reason": "one sentence"
}}

"same_domain" — both modules work on the same business concept (e.g. both deal with auth).
"shares_types" — one module uses data types/schemas defined in the other.
"implements" — one module implements an interface or contract defined in the other.
"none" — no meaningful semantic relationship beyond what imports already capture.
"""


def _extract_tldr(mb_path: Path) -> str:
    """Extract the TL;DR section from a _mb.md file."""
    try:
        content = mb_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    # Find content between ## TL;DR and the next ##
    match = re.search(r"##\s+TL;DR\s*\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if match:
        return match.group(1).strip()[:800]
    return content[:400]


def _pass_b_edges(
    units: list[dict],
    books_dir: Path,
    project_name: str,
    config_path: str,
) -> list[dict]:
    """
    LLM-based semantic edges using TL;DRs from existing _mb.md files.
    Skips any unit that doesn't have a _mb.md yet.
    """
    from itertools import combinations

    safe_proj = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)

    # Load TL;DRs for units that have _mb.md
    summaries: dict[str, str] = {}
    for unit in units:
        safe_segments = [
            "".join(c if c.isalnum() or c in "-_." else "_" for c in seg)
            for seg in Path(unit["name"].replace("\\", "/")).parts
        ]
        mb_path = books_dir / safe_proj / Path(*safe_segments) / "_mb.md"
        tldr = _extract_tldr(mb_path)
        if tldr:
            summaries[unit["name"]] = tldr

    if len(summaries) < 2:
        return []

    try:
        from .models import ModelProvider as _MP
        model = _MP(config_path)
    except Exception:
        return []

    edges: list[dict] = []
    names = list(summaries.keys())

    for name_a, name_b in combinations(names, 2):
        prompt = _SEMANTIC_PROMPT.format(
            name_a=name_a,
            summary_a=summaries[name_a],
            name_b=name_b,
            summary_b=summaries[name_b],
        )
        try:
            raw = model.complete("You detect semantic relationships between software modules.", prompt)
            data = json.loads(raw.strip())
            rel = data.get("relationship", "none")
            direction = data.get("direction", "none")
            confidence = float(data.get("confidence", 0.0))
            reason = data.get("reason", "")

            if rel == "none" or confidence < 0.6:
                continue

            if direction in ("a_to_b", "bidirectional"):
                edges.append(_edge(name_a, name_b, rel, confidence, reason))
            if direction in ("b_to_a", "bidirectional"):
                edges.append(_edge(name_b, name_a, rel, confidence, reason))
        except Exception:
            continue

    return edges


# ─── Public API ───────────────────────────────────────────────────────────────

def build_file_graph(
    repo_path: str | Path,
    project_name: str,
    books_dir: str | Path,
    config_path: str = "config.yaml",
    semantic: bool = True,
    on_progress=None,
) -> dict:
    """
    Build the intra-project file graph and save it to:
        books/{project_name}/.file_graph.json

    Parameters
    ----------
    repo_path : str | Path
        Root of the repository.
    project_name : str
        Project name (used for the books sub-directory).
    books_dir : str | Path
        Root books directory (same as used by BookGenerator).
    config_path : str
        Path to config.yaml (used for Pass B LLM calls).
    semantic : bool
        If True, run Pass B (LLM semantic edges). Requires _mb.md files to exist.
    on_progress : callable | None
        Optional progress callback(message: str).

    Returns the graph dict (also saved to disk).
    """
    from .structure import discover_structure, flatten

    def progress(msg: str):
        if on_progress:
            on_progress(msg)

    root = Path(repo_path).resolve()
    books_path = Path(books_dir)

    # ── Discover sub-units ────────────────────────────────────────────────────
    progress("Discovering sub-unit structure…")
    units_raw = flatten(discover_structure(root))
    units = [{"path": u.path, "name": u.name, "type": u.type} for u in units_raw]
    nodes = [{"path": u["name"], "type": u["type"]} for u in units]

    # ── Pass A: static analysis ───────────────────────────────────────────────
    progress("Pass A: static import and test analysis…")
    edges = _pass_a_edges(units, root)
    progress(f"Pass A complete — {len(edges)} edges found")

    # ── Pass B: LLM semantic edges ────────────────────────────────────────────
    if semantic:
        progress("Pass B: LLM semantic analysis…")
        sem_edges = _pass_b_edges(units, books_path, project_name, config_path)
        edges.extend(sem_edges)
        progress(f"Pass B complete — {len(sem_edges)} semantic edges added")

    # ── Assemble and save graph ───────────────────────────────────────────────
    graph = {
        "version": FILE_GRAPH_VERSION,
        "project": project_name,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
    }

    safe_proj = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)
    out_dir = books_path / safe_proj
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / FILE_GRAPH_NAME
    out_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    progress(f"File graph saved → {out_path}")

    return graph


def load_file_graph(project_name: str, books_dir: str | Path) -> Optional[dict]:
    """Load the file graph for a project, or None if it doesn't exist yet."""
    safe_proj = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)
    path = Path(books_dir) / safe_proj / FILE_GRAPH_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def find_edges(
    project_name: str,
    books_dir: str | Path,
    source: Optional[str] = None,
    target: Optional[str] = None,
    edge_type: Optional[str] = None,
) -> list[dict]:
    """
    Query edges from the file graph with optional filters.
    Returns [] if no graph exists yet.
    """
    graph = load_file_graph(project_name, books_dir)
    if not graph:
        return []
    edges = graph.get("edges", [])
    if source:
        edges = [e for e in edges if e["source"] == source]
    if target:
        edges = [e for e in edges if e["target"] == target]
    if edge_type:
        edges = [e for e in edges if e["type"] == edge_type]
    return edges


def get_neighbors(
    unit_name: str,
    project_name: str,
    books_dir: str | Path,
) -> list[str]:
    """
    Return the names of all sub-units that have any edge to/from unit_name.
    Used by the hybrid RAG retrieval step to expand context.
    """
    graph = load_file_graph(project_name, books_dir)
    if not graph:
        return []
    neighbors: set[str] = set()
    for edge in graph.get("edges", []):
        if edge["source"] == unit_name:
            neighbors.add(edge["target"])
        elif edge["target"] == unit_name:
            neighbors.add(edge["source"])
    neighbors.discard("__external__")
    return sorted(neighbors)
