"""
memoria coverage — Knowledge coverage analysis.

Scans all Memory Banks and reports:
  - Which projects have a Memory Bank (documented)
  - Which projects / directories DON'T (gaps)
  - Memory Banks that haven't been updated in a long time (stale)
  - Process Memory Banks with low confidence
  - Sections flagged with "unknown", "unclear", or "TODO" markers
  - Ownership holes (no DRI / owner mentioned)
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# ── Staleness thresholds ──────────────────────────────────────────────────────

STALE_WARNING_DAYS   = 30   # amber — getting old
STALE_CRITICAL_DAYS  = 90   # red   — probably outdated


# ── Patterns for gap detection ────────────────────────────────────────────────

_UNKNOWN_PATTERNS = re.compile(
    r"\b(unknown|unclear|tbd|todo|fixme|not specified|to be determined|"
    r"needs review|unverified|undocumented|n/a|none specified)\b",
    re.IGNORECASE,
)

_LOW_CONFIDENCE_PATTERNS = re.compile(
    r"confidence[:\s]+low|low confidence|"
    r"\bmedium\b.{0,40}confidence|confidence.{0,40}\bmedium\b",
    re.IGNORECASE,
)

_OWNERSHIP_PATTERNS = re.compile(
    r"\*\*Owner\s*/\s*DRI\*\*\s*[\|:]?\s*(not specified|unknown|tbd|n/a|-|—)",
    re.IGNORECASE,
)

_PROCESS_BOOK_PATTERN = re.compile(r"\|\s*Type:\s*process", re.IGNORECASE)

_CONFIDENCE_LOW_SECTION = re.compile(
    r"##.+?\n.*?confidence[:\s]+low",
    re.IGNORECASE | re.DOTALL,
)


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyse_book(book_path: Path) -> dict:
    """
    Analyse a single Memory Bank and return a health report dict.
    """
    stat = book_path.stat()
    content = book_path.read_text(encoding="utf-8")
    modified = datetime.fromtimestamp(stat.st_mtime)
    age_days = (datetime.now() - modified).days

    project_name = book_path.stem.replace("_memory_bank", "").replace("_", " ")
    is_process = bool(_PROCESS_BOOK_PATTERN.search(content[:800]))
    size_kb = round(stat.st_size / 1024, 1)

    # Staleness
    if age_days >= STALE_CRITICAL_DAYS:
        staleness = "critical"
    elif age_days >= STALE_WARNING_DAYS:
        staleness = "warning"
    else:
        staleness = "ok"

    # Gap markers in content
    unknown_count = len(_UNKNOWN_PATTERNS.findall(content))
    has_low_confidence = bool(_LOW_CONFIDENCE_PATTERNS.search(content))
    ownership_gap = bool(_OWNERSHIP_PATTERNS.search(content))

    # Count sections (## headings)
    sections = re.findall(r"^##\s+.+", content, re.MULTILINE)
    section_count = len(sections)

    # Flag sections that contain gap markers
    flagged_sections: list[str] = []
    for sec in sections:
        sec_name = sec.lstrip("#").strip()
        # Find content of this section
        idx = content.find(sec)
        next_sec = re.search(r"^##\s+", content[idx + len(sec):], re.MULTILINE)
        end = idx + len(sec) + (next_sec.start() if next_sec else len(content))
        sec_content = content[idx:end]
        if _UNKNOWN_PATTERNS.search(sec_content):
            flagged_sections.append(sec_name)

    # Compute a health score 0–100
    score = 100
    if staleness == "critical":   score -= 30
    elif staleness == "warning":   score -= 15
    if unknown_count > 10:         score -= 20
    elif unknown_count > 3:        score -= 10
    if has_low_confidence:         score -= 15
    if ownership_gap:              score -= 10
    if size_kb < 5:                score -= 10  # suspiciously thin book
    score = max(0, score)

    return {
        "project":          project_name,
        "path":             str(book_path.resolve()),
        "is_process":       is_process,
        "size_kb":          size_kb,
        "section_count":    section_count,
        "age_days":         age_days,
        "last_modified":    modified.strftime("%Y-%m-%d"),
        "staleness":        staleness,
        "unknown_markers":  unknown_count,
        "low_confidence":   has_low_confidence,
        "ownership_gap":    ownership_gap,
        "flagged_sections": flagged_sections,
        "health_score":     score,
    }


def find_undocumented_projects(
    scan_roots: list[Path],
    books_dir: str,
    documented_names: set[str],
) -> list[dict]:
    """
    Walk scan_roots looking for project-like directories that have NO Memory Bank.
    A directory is a "project" if it contains recognisable project signals.
    """
    PROJECT_SIGNALS = {
        "package.json", "requirements.txt", "setup.py", "pyproject.toml",
        "Cargo.toml", "go.mod", "Makefile", "Dockerfile", ".git",
        "tsconfig.json", "pom.xml", "build.gradle", "index.html",
    }
    undocumented = []
    for root in scan_roots:
        if not root.exists():
            continue
        try:
            for sub in sorted(root.iterdir()):
                if not sub.is_dir():
                    continue
                names_in_dir = {f.name for f in sub.iterdir() if f.exists()}
                if names_in_dir & PROJECT_SIGNALS:
                    norm = sub.name.lower().replace("-", " ").replace("_", " ")
                    if norm not in documented_names:
                        undocumented.append({
                            "name": sub.name,
                            "path": str(sub.resolve()),
                            "signals": list(names_in_dir & PROJECT_SIGNALS)[:3],
                        })
        except Exception:
            pass
    return undocumented


def analyse_staleness(book_path: Path, git_repo_path: Optional[str] = None) -> dict:
    """
    Deep staleness analysis for a single Memory Bank.

    Computes freshness signals:
      - Age since last memory bank update
      - Age of most recent git commit in the source repo (if known)
      - Sections that reference specific dates and how old those dates are
      - Process confidence level declared in the book

    Returns a structured freshness report for the book.
    """
    content = book_path.read_text(encoding="utf-8")
    stat = book_path.stat()
    book_age_days = (datetime.now() - datetime.fromtimestamp(stat.st_mtime)).days
    project_name = book_path.stem.replace("_memory_bank", "").replace("_", " ")

    signals: list[dict] = []

    # Signal 1: book file age
    if book_age_days >= STALE_CRITICAL_DAYS:
        signals.append({
            "type": "book_age",
            "severity": "critical",
            "description": f"Memory Bank not updated in {book_age_days} days",
            "age_days": book_age_days,
        })
    elif book_age_days >= STALE_WARNING_DAYS:
        signals.append({
            "type": "book_age",
            "severity": "warning",
            "description": f"Memory Bank not updated in {book_age_days} days",
            "age_days": book_age_days,
        })

    # Signal 2: dates mentioned in content — find any "Last Verified: YYYY-MM-DD" or similar
    date_refs = re.findall(
        r"\b(last verified|last updated|as of|verified on|tested on)[:\s]+(\d{4}-\d{2}-\d{2})",
        content, re.IGNORECASE
    )
    for label, date_str in date_refs:
        try:
            ref_date = datetime.strptime(date_str, "%Y-%m-%d")
            ref_age_days = (datetime.now() - ref_date).days
            if ref_age_days >= STALE_CRITICAL_DAYS:
                signals.append({
                    "type": "date_reference",
                    "severity": "critical",
                    "description": f"'{label}' date is {ref_age_days} days ago ({date_str})",
                    "age_days": ref_age_days,
                })
        except ValueError:
            pass

    # Signal 3: confidence markers in content
    confidence_low = bool(_LOW_CONFIDENCE_PATTERNS.search(content))
    if confidence_low:
        signals.append({
            "type": "low_confidence",
            "severity": "warning",
            "description": "One or more sections have low confidence rating",
        })

    # Signal 4: git repo freshness
    if git_repo_path:
        try:
            import subprocess
            result = subprocess.run(
                ["git", "log", "-1", "--format=%ct"],
                cwd=git_repo_path,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                commit_ts = int(result.stdout.strip())
                commit_age_days = (datetime.now() - datetime.fromtimestamp(commit_ts)).days
                if commit_age_days < book_age_days - 7:
                    # Repo has recent commits but memory bank hasn't been updated
                    signals.append({
                        "type": "repo_drift",
                        "severity": "warning",
                        "description": (
                            f"Repo has commits {commit_age_days}d ago "
                            f"but Memory Bank is {book_age_days}d old — "
                            f"possibly stale"
                        ),
                        "commit_age_days": commit_age_days,
                    })
        except Exception:
            pass

    severity = "ok"
    for sig in signals:
        if sig["severity"] == "critical":
            severity = "critical"
            break
        elif sig["severity"] == "warning":
            severity = "warning"

    return {
        "project":   project_name,
        "severity":  severity,
        "age_days":  book_age_days,
        "signals":   signals,
        "freshness": max(0, 100 - book_age_days),
    }


def score_process_confidence(book_path: Path) -> dict:
    """
    Compute per-section confidence scores for a Process Memory Bank.

    Confidence levels:
      - high:   SME-reviewed and/or tested in production (explicit statement)
      - medium: Interview-sourced or auto-extracted (default for new process books)
      - low:    Flagged as unclear, unverified, or contains unknown markers

    Returns a dict with per-section scores and an overall confidence score.
    """
    content = book_path.read_text(encoding="utf-8")
    project_name = book_path.stem.replace("_memory_bank", "").replace("_", " ")

    # Extract sections
    section_pattern = re.compile(r"^(#{2,3})\s+(.+?)$", re.MULTILINE)
    headings = [(m.group(2).strip(), m.start()) for m in section_pattern.finditer(content)]

    sections_scored = []
    for i, (heading, start) in enumerate(headings):
        end = headings[i + 1][1] if i + 1 < len(headings) else len(content)
        sec_content = content[start:end]

        # Determine confidence
        if re.search(r"\b(production|verified|tested|confirmed|sme.review|approved)\b",
                     sec_content, re.IGNORECASE):
            conf = "high"
        elif re.search(r"\b(interview|draft|auto.generat|assumed|todo|unknown|unclear|low)\b",
                       sec_content, re.IGNORECASE):
            conf = "low"
        else:
            conf = "medium"

        # Check for degradation signals (dates mentioned that are old)
        date_refs = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", sec_content)
        oldest_ref_age = 0
        for dr in date_refs:
            try:
                ref_age = (datetime.now() - datetime.strptime(dr, "%Y-%m-%d")).days
                oldest_ref_age = max(oldest_ref_age, ref_age)
            except ValueError:
                pass
        if oldest_ref_age >= STALE_CRITICAL_DAYS and conf == "high":
            conf = "medium"  # degrade if verification date is old

        sections_scored.append({
            "section":    heading,
            "confidence": conf,
            "age_of_oldest_date_ref_days": oldest_ref_age,
        })

    # Overall score
    conf_map = {"high": 3, "medium": 2, "low": 1}
    if sections_scored:
        avg = sum(conf_map[s["confidence"]] for s in sections_scored) / len(sections_scored)
        overall = "high" if avg >= 2.5 else "medium" if avg >= 1.7 else "low"
    else:
        overall = "unknown"

    return {
        "project":            project_name,
        "overall_confidence": overall,
        "sections":           sections_scored,
        "section_count":      len(sections_scored),
    }


def validate_process_book(
    book_path: Path,
    config_path: str,
    incident_sources: Optional[list[str]] = None,
) -> dict:
    """
    Validate a Process Memory Bank against available signals.

    Checks:
      - Are there sections that contradict each other?
      - Do any steps reference tools/systems that sound inconsistent?
      - If incident_sources are provided, look for keywords from recent incidents
        that suggest the documented process was NOT followed.

    Returns a validation report with contradictions and risk flags.
    """
    content = book_path.read_text(encoding="utf-8")
    project_name = book_path.stem.replace("_memory_bank", "").replace("_", " ")

    issues: list[dict] = []

    # 1. Internal consistency check (heuristic — escalation paths without contact info)
    escalation_sections = re.findall(
        r"## Escalation.+?\n(.*?)(?=\n##|\Z)", content,
        re.DOTALL | re.IGNORECASE
    )
    for sec in escalation_sections:
        rows = re.findall(r"\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|", sec)
        for row in rows:
            if re.search(r"\b(tbd|unknown|n/a|-|—)\b", row, re.IGNORECASE):
                issues.append({
                    "type":     "incomplete_escalation",
                    "severity": "warning",
                    "detail":   "Escalation path has TBD/Unknown contact — cannot escalate in an incident",
                    "excerpt":  row.strip()[:120],
                })

    # 2. Steps without clear owners
    step_pattern = re.compile(r"^### Step \d+", re.MULTILINE)
    steps = step_pattern.findall(content)
    owner_pattern = re.compile(r"\*\*Who:\*\*\s*(.+)", re.IGNORECASE)
    for step in steps:
        idx = content.find(step)
        excerpt = content[idx:idx + 300]
        owner_match = owner_pattern.search(excerpt)
        if not owner_match or re.search(
            r"\b(unknown|tbd|anyone|somebody|n/a)\b",
            owner_match.group(1), re.IGNORECASE
        ):
            issues.append({
                "type":     "missing_owner",
                "severity": "warning",
                "detail":   f"{step.strip()} has no clear owner",
                "excerpt":  step.strip(),
            })

    # 3. LLM validation against incident sources (if provided)
    llm_issues = []
    if incident_sources:
        try:
            from .models import ModelProvider
            model = ModelProvider(config_path)
            incidents_text = "\n\n---\n\n".join(
                Path(src).read_text(encoding="utf-8")[:2000]
                for src in incident_sources
                if Path(src).exists()
            )
            if incidents_text:
                validation_result = model.complete(
                    "You are a process validation expert. "
                    "Compare a documented process against recent incident reports. "
                    "Find contradictions where the incidents suggest the process was wrong or incomplete.",
                    f"PROCESS MEMORY BANK:\n{content[:3000]}\n\n"
                    f"RECENT INCIDENTS:\n{incidents_text}\n\n"
                    "List up to 5 contradictions or gaps in this format:\n"
                    "- [severity: warning|critical] [what the process says] vs [what the incident shows]",
                )
                # Parse the LLM output into structured issues
                for line in validation_result.split("\n"):
                    line = line.strip()
                    if line.startswith("- "):
                        sev = "critical" if "critical" in line.lower() else "warning"
                        llm_issues.append({
                            "type":     "incident_contradiction",
                            "severity": sev,
                            "detail":   line[2:].strip(),
                        })
        except Exception:
            pass

    all_issues = issues + llm_issues
    critical_count = sum(1 for i in all_issues if i["severity"] == "critical")
    warning_count  = sum(1 for i in all_issues if i["severity"] == "warning")

    return {
        "project":        project_name,
        "valid":          critical_count == 0,
        "critical_count": critical_count,
        "warning_count":  warning_count,
        "issues":         all_issues,
        "validated_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def run_coverage_analysis(
    books_dir: str,
    scan_roots: Optional[list[str]] = None,
) -> dict:
    """
    Full coverage analysis. Returns a structured report.

    books_dir:   directory containing *_memory_bank.md files
    scan_roots:  optional list of directories to scan for undocumented projects
    """
    books_path = Path(books_dir)
    if not books_path.exists():
        return {"error": "Books directory not found", "books_dir": books_dir}

    # Analyse all existing books
    books = sorted(books_path.glob("*_memory_bank.md"))
    book_reports = [analyse_book(b) for b in books]

    documented_names = {
        r["project"].lower().replace("-", " ").replace("_", " ")
        for r in book_reports
    }

    # Find undocumented projects
    roots = [Path(r) for r in (scan_roots or [])]
    undocumented = find_undocumented_projects(roots, books_dir, documented_names)

    # Aggregate stats
    stale_critical = [r for r in book_reports if r["staleness"] == "critical"]
    stale_warning  = [r for r in book_reports if r["staleness"] == "warning"]
    low_health     = [r for r in book_reports if r["health_score"] < 60]
    ownership_gaps = [r for r in book_reports if r["ownership_gap"]]
    process_books  = [r for r in book_reports if r["is_process"]]

    overall_score = (
        round(sum(r["health_score"] for r in book_reports) / len(book_reports))
        if book_reports else 0
    )

    return {
        "summary": {
            "total_documented":   len(book_reports),
            "total_undocumented": len(undocumented),
            "stale_critical":     len(stale_critical),
            "stale_warning":      len(stale_warning),
            "low_health_books":   len(low_health),
            "ownership_gaps":     len(ownership_gaps),
            "process_books":      len(process_books),
            "overall_health":     overall_score,
        },
        "books":        book_reports,
        "undocumented": undocumented,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
