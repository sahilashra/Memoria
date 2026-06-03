"""
Planning Intelligence — analyse a project plan against Memory Banks and
the knowledge graph to surface dependencies, risks, and prior art.

  memoria plan myplan.md
  memoria plan --text "We plan to add OAuth2 to the auth service"
  memoria plan quarterly.md --output json

Report structure
────────────────
  summary           — plain-English overview of the plan and its fit
  affected_projects — existing projects touched by the plan
  dependencies      — per-project: what the plan needs and any constraints
  risks             — severity-tagged list with source references
  prior_art         — earlier attempts / relevant decisions from Memory Banks
  recommended_actions — concrete next steps, with memoria commands where relevant
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# ─── Plan loading ─────────────────────────────────────────────────────────────


def load_plan(source: str) -> str:
    """
    Load plan text from a file path or return the string itself.
    Supports .md, .txt, and .docx.
    """
    p = Path(source)
    if not p.exists():
        # treat source as raw plan text
        return source

    suffix = p.suffix.lower()
    if suffix == ".docx":
        try:
            import docx  # python-docx
            doc = docx.Document(str(p))
            return "\n\n".join(para.text for para in doc.paragraphs if para.text.strip())
        except ImportError:
            raise RuntimeError(
                "python-docx is required to read .docx files. "
                "Install it with:  pip install python-docx"
            )
    else:
        return p.read_text(encoding="utf-8")


# ─── Memory Bank relevance search ────────────────────────────────────────────

_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "up",
    "about", "into", "through", "during", "before", "after",
    "each", "once", "here", "there", "when", "where", "why", "how",
    "all", "both", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "same", "so", "than", "too", "very",
    "and", "but", "or", "if", "then", "else",
    "we", "our", "us", "they", "their", "it", "its",
    "plan", "project", "new", "add", "build", "create", "implement",
    "use", "make", "want", "going", "get", "also", "which", "what",
    "who", "this", "that", "these", "those",
}


def _extract_terms(text: str) -> List[str]:
    """Extract meaningful query terms from plan text (simple tokenisation)."""
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9\-_]{2,}\b", text)
    seen: dict = {}
    for w in words:
        lw = w.lower()
        if lw not in _STOP_WORDS:
            seen[lw] = seen.get(lw, 0) + 1
    # Sort by frequency descending, return top 60 unique terms
    ranked = sorted(seen, key=lambda k: seen[k], reverse=True)
    return ranked[:60]


def _score_book(book_text: str, query_terms: List[str]) -> int:
    """Count how many unique query terms appear in the book text."""
    text_lower = book_text.lower()
    return sum(1 for term in query_terms if term in text_lower)


def find_relevant_books(
    plan_text: str,
    books_dir: str,
    max_books: int = 8,
) -> List[Dict]:
    """
    Find Memory Banks most relevant to the plan using keyword overlap.
    Returns list of {project, path, score, text, excerpt} dicts, best first.
    """
    books_path = Path(books_dir)
    if not books_path.exists():
        return []

    query_terms = _extract_terms(plan_text)
    if not query_terms:
        return []

    results = []
    for book_file in sorted(books_path.glob("*_memory_bank.md")):
        try:
            text = book_file.read_text(encoding="utf-8")
        except Exception:
            continue

        score = _score_book(text, query_terms)
        if score == 0:
            continue

        project = book_file.stem.replace("_memory_bank", "").replace("_", "-")
        excerpt = " ".join(text.split()[:60])

        results.append({
            "project": project,
            "path": str(book_file),
            "score": score,
            "text": text,
            "excerpt": excerpt,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_books]


# ─── Knowledge graph context ──────────────────────────────────────────────────

def _get_graph_context(books_dir: str, project_names: List[str]) -> str:
    """
    Pull dependency edges for the matched projects from the knowledge graph.
    Returns a compact formatted string (or empty string if graph is unavailable).
    """
    try:
        from .graph import load_graph  # type: ignore
        graph = load_graph(books_dir)
        if not graph or not graph.get("nodes"):
            return ""

        edges = graph.get("edges", [])
        lines: List[str] = []

        for proj in project_names[:6]:
            proj_lower = proj.lower().replace("-", "_")
            related = [
                e for e in edges
                if proj_lower in e.get("source", "").lower().replace("-", "_")
                or proj_lower in e.get("target", "").lower().replace("-", "_")
            ]
            if related:
                lines.append(f"\n**{proj} graph edges:**")
                for e in related[:5]:
                    src = e.get("source", "?")
                    tgt = e.get("target", "?")
                    lbl = e.get("label", e.get("type", "related"))
                    lines.append(f"  - {src} → {tgt} [{lbl}]")

        return "\n".join(lines) if lines else ""
    except Exception:
        return ""


# ─── LLM synthesis ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a senior technical architect with deep, current knowledge of this organisation's
software systems — drawn from the Memory Banks provided below.

Analyse the project plan against the available Memory Banks and knowledge graph context.
Be specific and grounded: only reference things explicitly visible in the provided
Memory Banks or plan. Do not hallucinate projects, team names, or technical details.

Return ONLY valid JSON matching this exact schema (no markdown fences, no commentary):

{
  "summary": "2–3 sentence plain-English overview of the plan and its technical fit",
  "affected_projects": ["project_a", "project_b"],
  "dependencies": [
    {
      "project": "project_name",
      "relation": "what the plan requires from this project",
      "notes": "any constraints, risks, or required coordination"
    }
  ],
  "risks": [
    {
      "severity": "high|medium|low",
      "title": "short risk name",
      "detail": "explanation; cite the Memory Bank section where you saw this",
      "source": "project whose Memory Bank flagged this risk"
    }
  ],
  "prior_art": [
    {
      "project": "project_name",
      "description": "what was attempted or decided and what was learned"
    }
  ],
  "recommended_actions": [
    "Specific next step; include a memoria command where appropriate"
  ]
}
"""


def synthesize_report(
    plan_text: str,
    books: List[Dict],
    graph_context: str,
    config_path: str,
) -> dict:
    """
    Use LLM to produce a structured analysis report from the plan + context.
    Returns the parsed report dict.
    """
    import litellm
    from .models import ModelProvider

    provider = ModelProvider(config_path)

    # ── Assemble context ──
    parts = [f"## Project Plan\n\n{plan_text[:3000]}\n"]

    if books:
        parts.append("## Relevant Memory Banks\n")
        for book in books[:6]:
            # Trim each book to fit within reasonable token budget
            book_snippet = book["text"][:1800]
            parts.append(
                f"### {book['project']} (relevance score: {book['score']})\n"
                f"{book_snippet}\n"
            )

    if graph_context:
        parts.append(f"## Knowledge Graph Context\n{graph_context}\n")

    user_content = "\n".join(parts)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        response = litellm.completion(
            model=provider.model,
            messages=messages,
            max_tokens=2000,
            timeout=90,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if the model adds them despite instructions
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        # Extract JSON object from response
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            raw = json_match.group(0)

        return json.loads(raw)

    except json.JSONDecodeError as exc:
        return {
            "summary": f"[Report parse error — raw LLM output follows]\n{raw[:600]}",
            "affected_projects": [],
            "dependencies": [],
            "risks": [],
            "prior_art": [],
            "recommended_actions": [f"JSON parse error: {exc}"],
        }
    except Exception as exc:
        return {
            "summary": f"Analysis error: {exc}",
            "affected_projects": [],
            "dependencies": [],
            "risks": [],
            "prior_art": [],
            "recommended_actions": [],
        }


# ─── Main entry point ─────────────────────────────────────────────────────────

def analyze_plan(
    plan_text: str,
    config_path: str,
    books_dir: str,
    max_books: int = 8,
) -> dict:
    """
    Analyse a project plan against Memory Banks and the knowledge graph.

    Args:
        plan_text:   Raw plan text (already loaded from file or stdin).
        config_path: Path to config.yaml (for LLM credentials).
        books_dir:   Directory containing Memory Bank .md files.
        max_books:   Maximum number of Memory Banks to include in context.

    Returns:
        Structured report dict with keys:
        summary, affected_projects, dependencies, risks, prior_art,
        recommended_actions, _meta.
    """
    books = find_relevant_books(plan_text, books_dir, max_books=max_books)
    project_names = [b["project"] for b in books]
    graph_context = _get_graph_context(books_dir, project_names)

    report = synthesize_report(plan_text, books, graph_context, config_path)
    report["_meta"] = {
        "generated_at": datetime.now().isoformat(),
        "books_searched": len(books),
        "graph_used": bool(graph_context),
        "chunked": False,
        "chunk_count": 1,
    }
    return report


# ─── Chunked analysis (drain & crumble) ──────────────────────────────────────

# Default threshold: plans longer than this are split before sending to the LLM.
# ~4000 chars ≈ ~1000 tokens — leaves room for Memory Bank context alongside it.
_CHUNK_THRESHOLD = 4000


def split_plan(plan_text: str) -> List[str]:
    """
    Split a plan into coherent sections by markdown heading structure.

    Strategy (in order of preference):
      1. Split on `## ` headings (H2) — clearest section boundary
      2. If no H2 headings, split on `### ` headings (H3)
      3. If no headings at all, split into ~_CHUNK_THRESHOLD char blobs at
         paragraph boundaries (double newlines)

    Returns a list of non-empty chunk strings.
    """
    # Try H2 first
    h2_parts = re.split(r"(?=^## )", plan_text, flags=re.MULTILINE)
    h2_parts = [p.strip() for p in h2_parts if p.strip()]
    if len(h2_parts) > 1:
        return h2_parts

    # Try H3
    h3_parts = re.split(r"(?=^### )", plan_text, flags=re.MULTILINE)
    h3_parts = [p.strip() for p in h3_parts if p.strip()]
    if len(h3_parts) > 1:
        return h3_parts

    # No heading structure — split by paragraphs into ~_CHUNK_THRESHOLD blobs
    paragraphs = [p.strip() for p in re.split(r"\n\n+", plan_text) if p.strip()]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for para in paragraphs:
        if current_len + len(para) > _CHUNK_THRESHOLD and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [plan_text]


def merge_reports(reports: List[dict], plan_text: str = "") -> dict:
    """
    Merge N chunk reports into a single unified report.

    Rules:
      - summary: short paragraph noting the plan was analysed in N chunks
      - affected_projects: union, deduplicated
      - dependencies: union; duplicates by project merged (notes combined)
      - risks: union; identical titles deduplicated; highest severity wins
      - prior_art: union, deduplicated by project
      - recommended_actions: union, top 8 deduplicated by first 40 chars
    """
    if not reports:
        return {}
    if len(reports) == 1:
        return reports[0]

    # Summary — synthesise from individual summaries
    chunk_summaries = "; ".join(
        r.get("summary", "")[:120] for r in reports if r.get("summary")
    )
    summary = (
        f"Plan analysed in {len(reports)} sections. "
        f"Combined findings: {chunk_summaries}"
    )[:500]

    # Affected projects — ordered union
    seen_proj: dict = {}
    for r in reports:
        for p in r.get("affected_projects", []):
            seen_proj.setdefault(p, True)
    affected_projects = list(seen_proj)

    # Dependencies — merge by project name
    dep_map: dict = {}
    for r in reports:
        for d in r.get("dependencies", []):
            proj = d.get("project", "?")
            if proj not in dep_map:
                dep_map[proj] = dict(d)
            else:
                # Append notes if different
                existing_notes = dep_map[proj].get("notes", "")
                new_notes = d.get("notes", "")
                if new_notes and new_notes not in existing_notes:
                    dep_map[proj]["notes"] = f"{existing_notes}; {new_notes}".strip("; ")
    dependencies = list(dep_map.values())

    # Risks — deduplicate by title, highest severity wins
    _sev_order = {"high": 0, "medium": 1, "low": 2}
    risk_map: dict = {}
    for r in reports:
        for risk in r.get("risks", []):
            title = risk.get("title", "?").lower()
            if title not in risk_map:
                risk_map[title] = dict(risk)
            else:
                existing_sev = _sev_order.get(risk_map[title].get("severity", "low"), 2)
                new_sev = _sev_order.get(risk.get("severity", "low"), 2)
                if new_sev < existing_sev:
                    risk_map[title] = dict(risk)
    # Sort: high first
    risks = sorted(risk_map.values(), key=lambda x: _sev_order.get(x.get("severity", "low"), 2))

    # Prior art — deduplicate by project
    prior_map: dict = {}
    for r in reports:
        for p in r.get("prior_art", []):
            proj = p.get("project", "?")
            prior_map.setdefault(proj, p)
    prior_art = list(prior_map.values())

    # Recommended actions — deduplicate by first 40 chars
    action_seen: dict = {}
    for r in reports:
        for a in r.get("recommended_actions", []):
            key = a[:40].lower()
            action_seen.setdefault(key, a)
    recommended_actions = list(action_seen.values())[:8]

    return {
        "summary": summary,
        "affected_projects": affected_projects,
        "dependencies": dependencies,
        "risks": risks,
        "prior_art": prior_art,
        "recommended_actions": recommended_actions,
    }


# ─── Plan vs Reality Tracker ─────────────────────────────────────────────────

def _snapshot_path(project: str, books_dir: str) -> Path:
    safe = re.sub(r"[^\w\-]", "_", project)
    return Path(books_dir) / f"{safe}_plan_snapshot.json"


def save_plan_snapshot(
    project: str,
    plan_text: str,
    report: dict,
    books_dir: str,
) -> Path:
    """
    Persist a plan snapshot alongside the Memory Bank for drift tracking.

    Saves `{books_dir}/{project}_plan_snapshot.json` containing:
      plan_text     — full original plan text
      affected_projects — list from the report
      risks         — list from the report (title + source + severity kept)
      saved_at      — ISO timestamp
      project       — project name

    Returns the path written.
    """
    path = _snapshot_path(project, books_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Keep only the fields needed for drift comparison — avoid storing huge report blobs
    snapshot = {
        "project":           project,
        "plan_text":         plan_text,
        "affected_projects": report.get("affected_projects", []),
        "risks": [
            {
                "title":    r.get("title", ""),
                "source":   r.get("source", ""),
                "severity": r.get("severity", "low"),
            }
            for r in report.get("risks", [])
        ],
        "saved_at": datetime.now().isoformat(),
    }
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return path


def load_plan_snapshot(project: str, books_dir: str) -> Optional[dict]:
    """Return the saved snapshot dict, or None if no snapshot exists."""
    path = _snapshot_path(project, books_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def check_drift(project: str, books_dir: str) -> dict:
    """
    Structural drift check — compare saved plan snapshot against the current
    Memory Bank without calling the LLM.

    Logic:
      For every risk in the snapshot, check whether its title keywords and
      source project appear somewhere in the updated Memory Bank text.
      A risk is "missing" if neither its title words nor its source appear.

      If ≥ 30% of tracked risks are missing OR any tracked affected_projects
      are no longer mentioned in the Memory Bank, flag as drifted.

    Returns:
      {
        "has_drift":          bool,
        "missing_risks":      [{title, source, severity}, ...],
        "missing_projects":   [str, ...],
        "total_risks":        int,
        "checked_at":         ISO str,
        "snapshot_saved_at":  ISO str,
      }
    """
    result: dict = {
        "has_drift":         False,
        "missing_risks":     [],
        "missing_projects":  [],
        "total_risks":       0,
        "checked_at":        datetime.now().isoformat(),
        "snapshot_saved_at": "",
    }

    snapshot = load_plan_snapshot(project, books_dir)
    if not snapshot:
        return result  # no snapshot — nothing to compare

    result["snapshot_saved_at"] = snapshot.get("saved_at", "")

    # Read current Memory Bank
    safe = re.sub(r"[^\w\-]", "_", project)
    book_path = Path(books_dir) / f"{safe}_memory_bank.md"
    if not book_path.exists():
        return result

    book_text = book_path.read_text(encoding="utf-8").lower()

    # Check missing risks
    risks = snapshot.get("risks", [])
    result["total_risks"] = len(risks)
    missing_risks: List[dict] = []

    for risk in risks:
        title_words = [
            w for w in re.findall(r"[a-zA-Z]{3,}", risk.get("title", ""))
            if w.lower() not in _STOP_WORDS
        ]
        source = risk.get("source", "").lower().replace("-", "_").replace(" ", "_")

        title_found = any(w.lower() in book_text for w in title_words)
        source_found = source and source in book_text.replace("-", "_").replace(" ", "_")

        if not title_found and not source_found:
            missing_risks.append(risk)

    # Check missing affected projects
    affected = snapshot.get("affected_projects", [])
    missing_projects = [
        p for p in affected
        if p.lower().replace("-", "_").replace(" ", "_")
        not in book_text.replace("-", "_").replace(" ", "_")
    ]

    result["missing_risks"] = missing_risks
    result["missing_projects"] = missing_projects

    # Drift threshold: ≥30% of risks missing OR any tracked project gone
    drift_threshold = 0.3
    risk_drift = len(risks) > 0 and (len(missing_risks) / len(risks)) >= drift_threshold
    result["has_drift"] = risk_drift or bool(missing_projects)

    return result


def write_drift_report(project: str, drift: dict, books_dir: str) -> Path:
    """
    Write a human-readable drift report as `{project}_drift.md` in books_dir.

    This file is picked up by `/api/drafts` and surfaced in the Review Queue
    so the team is notified of divergence without any extra UI work.

    Returns the path written.
    """
    safe = re.sub(r"[^\w\-]", "_", project)
    path = Path(books_dir) / f"{safe}_drift.md"

    missing_risks = drift.get("missing_risks", [])
    missing_projects = drift.get("missing_projects", [])
    checked_at = drift.get("checked_at", "")
    snapshot_at = drift.get("snapshot_saved_at", "")
    total_risks = drift.get("total_risks", 0)

    lines: List[str] = [
        f"# {project} — Plan vs Reality Drift",
        f"> Detected: {checked_at[:16].replace('T', ' ')}  "
        f"| Plan baseline saved: {snapshot_at[:16].replace('T', ' ')}",
        "",
        "The Memory Bank was updated after this project started. "
        "Some items from the original plan are no longer represented.",
        "",
    ]

    if missing_projects:
        lines.append("## Missing Projects")
        lines.append(
            "These projects were in the original plan's affected list but "
            "are no longer mentioned in the Memory Bank:"
        )
        for p in missing_projects:
            lines.append(f"- `{p}`")
        lines.append("")

    if missing_risks:
        lines.append(f"## Missing Risks ({len(missing_risks)}/{total_risks})")
        lines.append(
            "These risks were identified at plan time but are no longer "
            "referenced in the updated Memory Bank:"
        )
        for r in missing_risks:
            sev = r.get("severity", "low").upper()
            title = r.get("title", "")
            source = r.get("source", "")
            lines.append(f"- **[{sev}]** {title}" + (f" _(from {source})_" if source else ""))
        lines.append("")

    lines += [
        "## Recommended Actions",
        "- Review whether these risks were addressed, accepted, or are no longer relevant.",
        "- If addressed: update the Memory Bank to reflect what was done.",
        "- If still relevant: re-run `memoria plan` with the current plan for a fresh analysis.",
        "",
        "_This file was auto-generated by Memoria's plan drift checker. "
        "Approve it to dismiss, or reject to keep it pending._",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def analyze_plan_chunked(
    plan_text: str,
    config_path: str,
    books_dir: str,
    max_books: int = 8,
    chunk_threshold: int = _CHUNK_THRESHOLD,
) -> dict:
    """
    Analyse a plan, automatically chunking it if it exceeds chunk_threshold.

    Large plans are split by heading structure (H2 → H3 → paragraph blobs).
    Each chunk is analysed independently then merged into a unified report.

    This is the "drain & crumble" approach:
      - Small plans (< threshold): single pass, same as analyze_plan()
      - Large plans (≥ threshold): auto-split → analyse each chunk → merge

    The _meta block reports whether chunking was used and how many chunks.
    """
    if len(plan_text) < chunk_threshold:
        # Fits in one pass — fast path
        return analyze_plan(plan_text, config_path, books_dir, max_books)

    chunks = split_plan(plan_text)

    if len(chunks) == 1:
        # Couldn't split (no structure) — fall back to single pass with truncation
        return analyze_plan(plan_text, config_path, books_dir, max_books)

    # Analyse each chunk
    chunk_reports: List[dict] = []
    for chunk in chunks:
        try:
            report = analyze_plan(chunk, config_path, books_dir, max_books)
            chunk_reports.append(report)
        except Exception:
            # One chunk failing shouldn't abort the whole analysis
            continue

    merged = merge_reports(chunk_reports, plan_text)
    merged["_meta"] = {
        "generated_at": datetime.now().isoformat(),
        "books_searched": max(
            r.get("_meta", {}).get("books_searched", 0) for r in chunk_reports
        ) if chunk_reports else 0,
        "graph_used": any(
            r.get("_meta", {}).get("graph_used", False) for r in chunk_reports
        ),
        "chunked": True,
        "chunk_count": len(chunks),
    }
    return merged


# ─── Renderers ────────────────────────────────────────────────────────────────

_SEV_COLORS = {"high": "red", "medium": "yellow", "low": "green"}


def render_terminal(report: dict, console=None) -> None:
    """Print the plan analysis report to the terminal using Rich."""
    from rich.console import Console as _Console
    from rich.panel import Panel

    con = console or _Console()

    summary = report.get("summary", "No summary generated.")
    con.print()
    con.print(Panel(
        summary,
        title="[bold cyan]Plan Analysis[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    con.print()

    affected = report.get("affected_projects", [])
    if affected:
        con.print(f"[bold]Affected Projects:[/bold] {', '.join(affected)}")
        con.print()

    deps = report.get("dependencies", [])
    if deps:
        con.print("[bold]Dependencies[/bold]")
        for d in deps:
            con.print(f"  [cyan]◆ {d.get('project', '?')}[/cyan] — {d.get('relation', '')}")
            if d.get("notes"):
                con.print(f"    [dim]{d['notes']}[/dim]")
        con.print()

    risks = report.get("risks", [])
    if risks:
        con.print("[bold]Risks[/bold]")
        for r in risks:
            sev = r.get("severity", "medium").lower()
            color = _SEV_COLORS.get(sev, "white")
            con.print(f"  [{color}]▲ {r.get('title', '?')}[/{color}]  [{sev.upper()}]")
            if r.get("detail"):
                con.print(f"    {r['detail']}")
            if r.get("source"):
                con.print(f"    [dim]Source: {r['source']}[/dim]")
        con.print()

    prior = report.get("prior_art", [])
    if prior:
        con.print("[bold]Prior Art[/bold]")
        for p in prior:
            con.print(f"  [dim]◆[/dim] [cyan]{p.get('project', '?')}[/cyan]: {p.get('description', '')}")
        con.print()

    actions = report.get("recommended_actions", [])
    if actions:
        con.print("[bold]Recommended Actions[/bold]")
        for i, action in enumerate(actions, 1):
            con.print(f"  {i}. {action}")
        con.print()

    meta = report.get("_meta", {})
    if meta:
        con.print(
            f"[dim]Generated {meta.get('generated_at', '')} · "
            f"{meta.get('books_searched', 0)} Memory Banks matched · "
            f"Graph: {'yes' if meta.get('graph_used') else 'no'}[/dim]"
        )
        con.print()


def render_markdown(report: dict) -> str:
    """Return the plan analysis report as a Markdown string."""
    lines = ["# Plan Analysis Report\n"]

    summary = report.get("summary", "")
    if summary:
        lines.extend([summary, ""])

    affected = report.get("affected_projects", [])
    if affected:
        lines.extend([f"**Affected projects:** {', '.join(affected)}", ""])

    deps = report.get("dependencies", [])
    if deps:
        lines.append("## Dependencies\n")
        for d in deps:
            lines.append(f"- **{d.get('project', '?')}** — {d.get('relation', '')}")
            if d.get("notes"):
                lines.append(f"  - _{d['notes']}_")
        lines.append("")

    risks = report.get("risks", [])
    if risks:
        lines.append("## Risks\n")
        for r in risks:
            sev = r.get("severity", "medium").upper()
            lines.append(f"- **[{sev}] {r.get('title', '?')}**")
            if r.get("detail"):
                lines.append(f"  {r['detail']}")
            if r.get("source"):
                lines.append(f"  _(Source: {r['source']})_")
        lines.append("")

    prior = report.get("prior_art", [])
    if prior:
        lines.append("## Prior Art\n")
        for p in prior:
            lines.append(f"- **{p.get('project', '?')}**: {p.get('description', '')}")
        lines.append("")

    actions = report.get("recommended_actions", [])
    if actions:
        lines.append("## Recommended Actions\n")
        for i, action in enumerate(actions, 1):
            lines.append(f"{i}. {action}")
        lines.append("")

    meta = report.get("_meta", {})
    if meta:
        lines.extend([
            "---",
            f"_Generated {meta.get('generated_at', '')} · "
            f"{meta.get('books_searched', 0)} Memory Banks matched_",
        ])

    return "\n".join(lines)
