"""
Knowledge Graph — cross-project relationship tracking.

Analyses all Memory Banks in a books directory and builds a persistent graph
showing how projects relate to each other:

  • Shared technologies  (Python, PostgreSQL, Redis, Kafka, …)
  • Service dependencies (Project A calls Project B's API)
  • Domain overlap       (two projects both cover "authentication")
  • Explicit references  (Project A's book mentions Project B by name)

Graph is stored at books/.graph.json — lightweight, git-trackable, no DB required.

Usage
─────
  from memoria.graph import build_graph, get_graph, query_project, impact_analysis

  build_graph("books", config_path="config.yaml")
  graph = get_graph("books")
  related = query_project("my-project", graph)
  dependents = impact_analysis("auth-service", graph)

CLI
───
  memoria graph build               # analyse all books, save graph
  memoria graph show                # print the full graph as a table
  memoria graph query <project>     # connections for one project
  memoria graph impact <project>    # what depends on this project?
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

# ─── Constants ───────────────────────────────────────────────────────────────

GRAPH_FILE = ".graph.json"
GRAPH_VERSION = 1

# Technologies that are too common to imply a meaningful relationship
_COMMON_TECH = {
    "python", "javascript", "typescript", "java", "go", "rust", "c++", "c#",
    "html", "css", "json", "yaml", "xml", "git", "docker", "linux", "bash",
    "shell", "makefile", "markdown", "sql", "http", "https", "rest", "api",
    "restful", "graphql", "grpc", "cli", "sdk", "library",
}

# Relationship labels
REL_DEPENDS_ON        = "depends_on"
REL_SHARES_TECHNOLOGY = "shares_technology"
REL_SAME_DOMAIN       = "same_domain"
REL_REFERENCES        = "references"


# ─── LLM extraction ──────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
You are analysing a software project Memory Bank.
Extract structured metadata and return it as a **single valid JSON object** — no markdown fences, no explanations.

Required keys (use empty list if none apply):
  "description"   : one-sentence summary of what this project does
  "technologies"  : major technologies, frameworks, databases, and protocols (e.g. ["FastAPI","PostgreSQL","Redis"])
  "topics"        : business/problem domains this project covers (e.g. ["authentication","billing","data pipeline"])
  "exposes"       : services, APIs, events, or data models this project provides to others
  "consumes"      : external services, APIs, or systems this project depends on
  "project_refs"  : names of other projects, services, or systems explicitly mentioned

Memory Bank:
{book_content}
"""


def extract_node_meta(
    book_path: str,
    config_path: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Dict:
    """
    Use the configured LLM to extract structured metadata from a Memory Bank.

    Returns a dict with keys: name, book_path, description, technologies,
    topics, exposes, consumes, project_refs.
    Falls back to an empty-but-valid node if the LLM call fails.
    """
    from .models import ModelProvider

    book = Path(book_path)
    project_name = book.stem.replace("_memory_bank", "")
    content = book.read_text(encoding="utf-8")

    # Truncate to avoid huge token bills (graph analysis doesn't need every line)
    truncated = content[:8000] if len(content) > 8000 else content

    model = ModelProvider(config_path)
    prompt = _EXTRACTION_PROMPT.format(book_content=truncated)

    try:
        raw = model.complete(
            "You extract structured JSON from software documentation. Return only valid JSON.",
            prompt,
        )
        meta = _parse_json_response(raw)
    except Exception as exc:
        if on_progress:
            on_progress(f"  [warn] Could not extract metadata from {project_name}: {exc}")
        meta = {}

    return {
        "name":         project_name,
        "book_path":    str(book_path),
        "description":  meta.get("description", ""),
        "technologies": _normalise_list(meta.get("technologies", [])),
        "topics":       _normalise_list(meta.get("topics", [])),
        "exposes":      _normalise_list(meta.get("exposes", [])),
        "consumes":     _normalise_list(meta.get("consumes", [])),
        "project_refs": _normalise_list(meta.get("project_refs", [])),
    }


def _parse_json_response(raw: str) -> Dict:
    """Strip markdown fences and parse JSON. Returns {} on failure."""
    # Remove ```json ... ``` or ``` ... ``` fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: find the first {...} block
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return {}


def _normalise_list(value) -> List[str]:
    """Ensure a value is a list of non-empty stripped strings."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


# ─── Edge detection ───────────────────────────────────────────────────────────

def find_edges(nodes: Dict[str, Dict]) -> List[Dict]:
    """
    Inspect all project nodes and return a list of relationship edges.
    Pure Python — no LLM calls.

    Each edge:  {source, target, relation, reason, confidence}
    """
    edges: List[Dict] = []
    names = list(nodes.keys())

    for i, a_name in enumerate(names):
        a = nodes[a_name]
        a_text = _node_full_text(a)           # full book text for reference search

        for b_name in names[i + 1:]:          # avoid duplicates (a→b only, not b→a)
            b = nodes[b_name]

            # ── 1. Direct service dependency ─────────────────────────────────
            dep = _check_service_dependency(a, b)
            if dep:
                edges.append(dep)
            dep_rev = _check_service_dependency(b, a)
            if dep_rev:
                edges.append(dep_rev)

            # ── 2. Technology overlap ─────────────────────────────────────────
            shared_tech = _shared_significant_tech(a, b)
            if len(shared_tech) >= 2:       # at least 2 shared non-trivial techs
                edges.append({
                    "source":     a_name,
                    "target":     b_name,
                    "relation":   REL_SHARES_TECHNOLOGY,
                    "reason":     f"Both use: {', '.join(sorted(shared_tech)[:5])}",
                    "confidence": round(min(0.5 + 0.1 * len(shared_tech), 0.85), 2),
                })

            # ── 3. Domain/topic overlap ───────────────────────────────────────
            shared_topics = _shared_topics(a, b)
            if shared_topics:
                edges.append({
                    "source":     a_name,
                    "target":     b_name,
                    "relation":   REL_SAME_DOMAIN,
                    "reason":     f"Same domain: {', '.join(sorted(shared_topics)[:4])}",
                    "confidence": round(min(0.4 + 0.15 * len(shared_topics), 0.80), 2),
                })

            # ── 4. Explicit cross-reference ───────────────────────────────────
            if _is_referenced(b_name, a) or b_name in a.get("project_refs", []):
                edges.append({
                    "source":     a_name,
                    "target":     b_name,
                    "relation":   REL_REFERENCES,
                    "reason":     f"{a_name} explicitly mentions {b_name}",
                    "confidence": 0.95,
                })
            if _is_referenced(a_name, b) or a_name in b.get("project_refs", []):
                edges.append({
                    "source":     b_name,
                    "target":     a_name,
                    "relation":   REL_REFERENCES,
                    "reason":     f"{b_name} explicitly mentions {a_name}",
                    "confidence": 0.95,
                })

    # Deduplicate: same source+target+relation keeps highest confidence
    return _deduplicate_edges(edges)


def _check_service_dependency(consumer: Dict, provider: Dict) -> Optional[Dict]:
    """
    Return an edge if `consumer` appears to consume something that `provider` exposes.
    """
    consumer_name = consumer["name"]
    provider_name = provider["name"]
    consumes      = [c.lower() for c in consumer.get("consumes", [])]
    exposes       = [e.lower() for e in provider.get("exposes", [])]
    provider_refs = [r.lower() for r in consumer.get("project_refs", [])]

    # Direct name reference in consumes
    if provider_name.lower() in consumes or provider_name.lower() in provider_refs:
        return {
            "source":     consumer_name,
            "target":     provider_name,
            "relation":   REL_DEPENDS_ON,
            "reason":     f"{consumer_name} lists {provider_name} as a consumed dependency",
            "confidence": 0.95,
        }

    # Token overlap between consumer.consumes and provider.exposes
    for consumed_item in consumes:
        for exposed_item in exposes:
            tokens_c = set(re.split(r"[\s/\-_]", consumed_item))
            tokens_e = set(re.split(r"[\s/\-_]", exposed_item))
            overlap   = tokens_c & tokens_e - {"", "api", "the", "a", "an", "of"}
            if len(overlap) >= 2:
                return {
                    "source":     consumer_name,
                    "target":     provider_name,
                    "relation":   REL_DEPENDS_ON,
                    "reason":     f"{consumer_name} consumes '{consumed_item}' — matches {provider_name}'s '{exposed_item}'",
                    "confidence": 0.75,
                }
    return None


def _shared_significant_tech(a: Dict, b: Dict) -> set:
    """Return shared technologies that are non-trivial (not in _COMMON_TECH)."""
    a_tech = {t.lower() for t in a.get("technologies", [])}
    b_tech = {t.lower() for t in b.get("technologies", [])}
    return (a_tech & b_tech) - _COMMON_TECH


def _shared_topics(a: Dict, b: Dict) -> set:
    """Return shared topics/domains between two project nodes."""
    a_topics = {t.lower() for t in a.get("topics", [])}
    b_topics = {t.lower() for t in b.get("topics", [])}
    return a_topics & b_topics


def _is_referenced(target_name: str, source_node: Dict) -> bool:
    """True if target_name appears verbatim in source's project_refs."""
    refs = [r.lower() for r in source_node.get("project_refs", [])]
    return target_name.lower() in refs


def _node_full_text(node: Dict) -> str:
    """Concatenate all string fields for reference search."""
    parts = [node.get("description", "")]
    for key in ("technologies", "topics", "exposes", "consumes", "project_refs"):
        parts.extend(node.get(key, []))
    return " ".join(parts).lower()


def _deduplicate_edges(edges: List[Dict]) -> List[Dict]:
    """Keep highest-confidence edge per (source, target, relation) triple."""
    best: Dict[tuple, Dict] = {}
    for e in edges:
        key = (e["source"], e["target"], e["relation"])
        if key not in best or e["confidence"] > best[key]["confidence"]:
            best[key] = e
    return list(best.values())


# ─── Build / load graph ───────────────────────────────────────────────────────

def build_graph(
    books_dir: str,
    config_path: str,
    on_progress: Optional[Callable[[str], None]] = None,
    projects: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
    incremental: bool = False,
) -> Dict:
    """
    Analyse Memory Banks in books_dir, extract metadata, detect relationships,
    persist to books_dir/.graph.json, and return the graph dict.

    Parameters
    ----------
    projects : list[str] | None
        Only rebuild these projects (by name). Others keep their existing nodes.
    exclude : list[str] | None
        Skip these projects entirely.
    incremental : bool
        If True, keep existing nodes and only re-analyse books that changed
        since the last graph build timestamp.
    """
    books_path = Path(books_dir)
    books = sorted(books_path.glob("*_memory_bank.md"))

    if not books:
        if on_progress:
            on_progress("No Memory Banks found — run [cyan]memoria analyze[/cyan] first.")
        return _empty_graph()

    # Load existing graph for incremental/partial rebuilds
    existing_graph = _empty_graph()
    graph_path = books_path / GRAPH_FILE
    if (incremental or projects) and graph_path.exists():
        try:
            existing_graph = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    last_build = existing_graph.get("updated", "")
    existing_nodes = existing_graph.get("nodes", {})

    # Determine which books to process
    exclude_set = set(e.lower() for e in (exclude or []))
    project_set = set(p.lower() for p in (projects or []))

    nodes: Dict[str, Dict] = {}

    for book in books:
        project_name = book.stem.replace("_memory_bank", "")

        # Skip excluded projects
        if project_name.lower() in exclude_set:
            if on_progress:
                on_progress(f"Skipping (excluded): {project_name}")
            continue

        # If --projects specified, only process those
        if project_set and project_name.lower() not in project_set:
            # Keep existing node if available
            if project_name in existing_nodes:
                nodes[project_name] = existing_nodes[project_name]
            continue

        # Incremental: skip if book hasn't changed since last build
        if incremental and last_build and project_name in existing_nodes:
            import os
            book_mtime = datetime.fromtimestamp(os.path.getmtime(book))
            try:
                build_time = datetime.fromisoformat(last_build)
                if book_mtime < build_time:
                    if on_progress:
                        on_progress(f"Unchanged, reusing: {project_name}")
                    nodes[project_name] = existing_nodes[project_name]
                    continue
            except (ValueError, TypeError):
                pass

        if on_progress:
            on_progress(f"Extracting metadata: {project_name}")
        node = extract_node_meta(str(book), config_path, on_progress)
        nodes[project_name] = node

    if on_progress:
        on_progress(f"Detecting cross-project relationships ({len(nodes)} project(s))…")

    edges = find_edges(nodes)

    graph = {
        "version": GRAPH_VERSION,
        "updated": datetime.now().isoformat(timespec="seconds"),
        "nodes":   nodes,
        "edges":   edges,
    }

    graph_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")

    if on_progress:
        on_progress(
            f"Graph saved: {len(nodes)} project(s), {len(edges)} relationship(s) → {graph_path}"
        )

    return graph


def get_graph(books_dir: str) -> Dict:
    """
    Load the graph from books_dir/.graph.json.
    Returns an empty graph dict if the file does not exist.
    """
    graph_path = Path(books_dir) / GRAPH_FILE
    if not graph_path.exists():
        return _empty_graph()
    try:
        return json.loads(graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_graph()


def _empty_graph() -> Dict:
    return {
        "version": GRAPH_VERSION,
        "updated": None,
        "nodes": {},
        "edges": [],
    }


# ─── Query helpers ────────────────────────────────────────────────────────────

def query_project(project_name: str, graph: Dict) -> List[Dict]:
    """
    Return all edges where project_name is the source or target.
    Each result includes the relationship plus the other project's metadata.
    """
    nodes  = graph.get("nodes", {})
    edges  = graph.get("edges", [])
    results: List[Dict] = []

    for edge in edges:
        if edge["source"] == project_name:
            other = edge["target"]
            direction = "outbound"
        elif edge["target"] == project_name:
            other = edge["source"]
            direction = "inbound"
        else:
            continue

        results.append({
            **edge,
            "other":       other,
            "direction":   direction,
            "other_meta":  nodes.get(other, {}),
        })

    # Sort by confidence descending
    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results


def impact_analysis(project_name: str, graph: Dict) -> List[Dict]:
    """
    Return the projects that depend on project_name (direct + transitive).
    Useful for answering "what breaks if I change this project?"

    Returns a list of {project, depth, path, relation}.
    """
    edges  = graph.get("edges", [])

    # Build adjacency: who depends on whom  (target → list of sources)
    rev_deps: Dict[str, List[str]] = {}
    for edge in edges:
        if edge["relation"] == REL_DEPENDS_ON:
            rev_deps.setdefault(edge["target"], []).append(edge["source"])

    # BFS from project_name
    visited: Dict[str, int] = {}   # project → depth
    queue   = [(project_name, 0, [project_name])]
    results: List[Dict] = []

    while queue:
        current, depth, path = queue.pop(0)
        for dependent in rev_deps.get(current, []):
            if dependent in visited:
                continue
            visited[dependent] = depth + 1
            results.append({
                "project": dependent,
                "depth":   depth + 1,
                "path":    " → ".join(path + [dependent]),
                "relation": REL_DEPENDS_ON,
            })
            queue.append((dependent, depth + 1, path + [dependent]))

    results.sort(key=lambda x: x["depth"])
    return results


def graph_summary(graph: Dict) -> str:
    """Return a short human-readable summary of the graph."""
    n = len(graph.get("nodes", {}))
    e = len(graph.get("edges", []))
    updated = graph.get("updated") or "never"
    return f"{n} project(s), {e} relationship(s) — last built {updated}"


# ─── Continuous graph engine ──────────────────────────────────────────────────

def watch_and_rebuild(
    books_dir:   str,
    config_path: str = "",
    on_rebuilt:  Optional[Callable[[str, Dict], None]] = None,
    debounce_s:  float = 15.0,
) -> None:
    """
    Blocking loop that watches *books_dir* for Memory Bank changes and
    triggers an incremental graph rebuild whenever a ``*_memory_bank.md``
    file is created or modified.

    Parameters
    ----------
    books_dir   : directory containing ``*_memory_bank.md`` files
    config_path : optional path to ``config.yaml`` for the LLM provider
    on_rebuilt  : callback(project_name, updated_graph) fired after each rebuild
    debounce_s  : seconds to wait after the last change before rebuilding
                  (coalesces rapid multi-file changes into one rebuild)
    """
    import logging
    import threading
    import time
    from watchdog.observers import Observer as _Observer
    from watchdog.events import FileSystemEventHandler

    log = logging.getLogger("memoria.graph.watch")

    _pending: dict = {}          # project_name → timestamp-of-change
    _lock = threading.Lock()

    class _BookHandler(FileSystemEventHandler):
        def _handle(self, path: str) -> None:
            p = Path(path)
            if not p.name.endswith("_memory_bank.md"):
                return
            if p.name.endswith("_draft.md"):
                return
            project_name = p.stem.replace("_memory_bank", "")
            with _lock:
                _pending[project_name] = time.monotonic()

        def on_modified(self, event):
            if not event.is_directory:
                self._handle(event.src_path)

        def on_created(self, event):
            if not event.is_directory:
                self._handle(event.src_path)

    def _rebuild_loop() -> None:
        while True:
            time.sleep(2)
            now = time.monotonic()
            with _lock:
                ready = [
                    proj for proj, ts in list(_pending.items())
                    if now - ts >= debounce_s
                ]
                for proj in ready:
                    del _pending[proj]

            for project_name in ready:
                log.info("Graph: detected change in '%s' — rebuilding (incremental)", project_name)
                try:
                    def _progress(msg: str) -> None:
                        log.debug("graph rebuild: %s", msg)

                    updated = build_graph(
                        books_dir,
                        config_path=config_path,
                        on_progress=_progress,
                        projects=[project_name],
                        incremental=True,
                    )
                    log.info(
                        "Graph: rebuilt — %d nodes, %d edges",
                        len(updated.get("nodes", {})),
                        len(updated.get("edges", [])),
                    )
                    if on_rebuilt:
                        try:
                            on_rebuilt(project_name, updated)
                        except Exception as cb_err:
                            log.debug("on_rebuilt callback error: %s", cb_err)
                except Exception as exc:
                    log.warning("Graph rebuild failed for '%s': %s", project_name, exc)

    rebuild_thread = threading.Thread(target=_rebuild_loop, daemon=True)
    rebuild_thread.start()

    obs = _Observer()
    obs.schedule(_BookHandler(), books_dir, recursive=False)
    obs.start()
    log.info("Graph watcher started on '%s' (debounce %.0fs)", books_dir, debounce_s)

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        obs.stop()
        obs.join(timeout=3)
        log.info("Graph watcher stopped.")
