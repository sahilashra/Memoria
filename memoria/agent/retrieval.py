"""
Hybrid Retrieval — combines ChromaDB vector search + file graph traversal + RRF.

Design (from docs/framework/FRAMEWORK_CHOICES.md):
  Step 1: embed ticket text → ChromaDB top-k matches
  Step 2: take matched project/sub-unit names → traverse .file_graph.json for neighbors
          → fetch those neighbor chunks from ChromaDB
  Step 3: Reciprocal Rank Fusion (RRF) merge — deterministic, no extra model call

NO LangChain LCEL chains. Direct ChromaDB access via the shared singleton in search.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ─── Result type ──────────────────────────────────────────────────────────────

@dataclass
class RetrievalResult:
    project: str        # project or sub-unit name the chunk came from
    section: str        # section heading within the Memory Bank
    text: str           # the chunk text
    vector_rank: int    # rank in the vector results (0 = not in vector results)
    graph_rank: int     # rank in the graph-expanded results (0 = not in graph results)
    rrf_score: float    # Reciprocal Rank Fusion combined score
    source: str = ""    # "vector" | "graph" | "both"

    def __repr__(self) -> str:
        return f"RetrievalResult({self.project!r}, rrf={self.rrf_score:.3f})"


# ─── RRF ──────────────────────────────────────────────────────────────────────

_RRF_K = 60  # standard constant — dampens outlier ranks

def _rrf_score(rank_a: int, rank_b: int) -> float:
    """
    Reciprocal Rank Fusion score.
    rank_a / rank_b are 1-based; pass 0 if the item is absent from that list.
    """
    score = 0.0
    if rank_a > 0:
        score += 1.0 / (rank_a + _RRF_K)
    if rank_b > 0:
        score += 1.0 / (rank_b + _RRF_K)
    return score


# ─── Core retrieval ───────────────────────────────────────────────────────────

def retrieve_for_ticket(
    ticket_text: str,
    project_name: str,
    books_dir: str = "books",
    top_k: int = 8,
    graph_expand: bool = True,
) -> list[RetrievalResult]:
    """
    Retrieve ranked Memory Bank chunks relevant to ticket_text.

    Parameters
    ----------
    ticket_text : str
        The ticket description / title to search against.
    project_name : str
        Restrict results to this project (and its sub-units).
    books_dir : str
        Books directory — same as used by BookGenerator.
    top_k : int
        Number of final results to return after RRF merge.
    graph_expand : bool
        If True, expand results via the file graph (Step 2).
        Set False to skip graph traversal (e.g. when no graph exists yet).

    Returns
    -------
    Ranked list of RetrievalResult, highest rrf_score first.
    """
    from ..search import search as _vector_search
    from ..core.file_graph import get_neighbors, load_file_graph

    # ── Step 1: vector search ─────────────────────────────────────────────────
    raw_vector = _vector_search(ticket_text, books_dir=books_dir, top_k=top_k * 2)
    # Filter to this project (exact match OR sub-unit prefix match)
    vector_hits = [
        r for r in raw_vector
        if r["project"] == project_name
        or r["project"].startswith(project_name + "/")
        or r["project"].startswith(project_name + "\\")
    ]

    # Assign 1-based vector ranks
    vector_ranked: dict[str, int] = {}   # key = "project::section"
    for rank, hit in enumerate(vector_hits, start=1):
        key = f"{hit['project']}::{hit['section']}"
        vector_ranked[key] = rank

    # ── Step 2: graph expansion ───────────────────────────────────────────────
    graph_ranked: dict[str, int] = {}
    graph_texts: dict[str, dict] = {}  # key → {project, section, text}

    if graph_expand:
        # Find which sub-units appeared in vector results
        matched_units = {hit["project"] for hit in vector_hits}

        # Expand to neighbors via file graph
        neighbor_units: set[str] = set()
        for unit in matched_units:
            # unit may be project_name (flat) or project_name/sub/path (hierarchical)
            sub_path = unit[len(project_name):].lstrip("/\\") or "."
            neighbors = get_neighbors(sub_path, project_name, books_dir)
            for n in neighbors:
                neighbor_units.add(f"{project_name}/{n}" if n != "." else project_name)

        # Fetch chunks for neighbor units from ChromaDB
        if neighbor_units:
            neighbor_hits = _vector_search(ticket_text, books_dir=books_dir, top_k=top_k)
            neighbor_filtered = [
                r for r in neighbor_hits
                if r["project"] in neighbor_units
                and r["project"] not in matched_units  # only NEW hits
            ]
            for rank, hit in enumerate(neighbor_filtered, start=1):
                key = f"{hit['project']}::{hit['section']}"
                graph_ranked[key] = rank
                graph_texts[key] = hit

    # ── Step 3: RRF merge ─────────────────────────────────────────────────────
    all_keys: set[str] = set(vector_ranked) | set(graph_ranked)
    results: list[RetrievalResult] = []

    # Build a lookup of hit data for keys that only appear in graph (not vector)
    vector_data = {
        f"{h['project']}::{h['section']}": h for h in vector_hits
    }

    for key in all_keys:
        vr = vector_ranked.get(key, 0)
        gr = graph_ranked.get(key, 0)
        score = _rrf_score(vr, gr)

        hit = vector_data.get(key) or graph_texts.get(key)
        if not hit:
            continue

        source = "both" if (vr > 0 and gr > 0) else ("vector" if vr > 0 else "graph")
        results.append(RetrievalResult(
            project=hit["project"],
            section=hit["section"],
            text=hit["text"],
            vector_rank=vr,
            graph_rank=gr,
            rrf_score=score,
            source=source,
        ))

    results.sort(key=lambda r: r.rrf_score, reverse=True)
    return results[:top_k]


def retrieve_multi_project(
    ticket_text: str,
    project_names: list[str],
    books_dir: str = "books",
    top_k: int = 8,
) -> list[RetrievalResult]:
    """
    Retrieve across multiple projects (for cross-repo tickets in Phase 3).
    Runs retrieval per project and merges with RRF by combining all ranked lists.
    """
    all_results: list[RetrievalResult] = []
    for proj in project_names:
        all_results.extend(
            retrieve_for_ticket(ticket_text, proj, books_dir, top_k=top_k)
        )
    all_results.sort(key=lambda r: r.rrf_score, reverse=True)
    return all_results[:top_k]
