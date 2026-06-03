"""
Structure Discovery — identify sub-unit boundaries within a repository.

Rules (applied in priority order):
  1. Package boundary files always split a directory into its own sub-unit:
     package.json, setup.py, pyproject.toml, pom.xml, build.gradle,
     Cargo.toml, go.mod, __init__.py (at subdir root, not repo root)
  2. Test directories always split:
     test/, tests/, __tests__, spec/, specs/ and name patterns like *_test, *_spec
  3. Any directory with more than LARGE_DIR_THRESHOLD source files subdivides;
     the rule is applied recursively so very large dirs keep splitting.

Output: flat list of SubUnit — each represents a directory that gets its own
Memory Bank. The generator decides which files to include per unit (only
files directly in that directory, not files in child sub-units).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── Constants ────────────────────────────────────────────────────────────────

PACKAGE_BOUNDARY_FILES = frozenset({
    "package.json", "setup.py", "pyproject.toml", "pom.xml",
    "build.gradle", "build.gradle.kts", "Cargo.toml", "go.mod",
    "CMakeLists.txt", "composer.json", "Gemfile", "mix.exs",
})

TEST_DIR_NAMES = frozenset({
    "test", "tests", "__tests__", "spec", "specs",
})

SOURCE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
    ".cpp", ".c", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".kt", ".scala", ".clj", ".ex", ".exs", ".elm", ".vue", ".svelte",
    ".mjs", ".cjs",
})

IGNORE_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", "venv", ".venv", "env",
    "dist", "build", ".next", "out", "coverage", ".pytest_cache",
    ".mypy_cache", ".tox", "eggs", ".eggs", "target", ".gradle",
    ".idea", ".vscode", "__pycache__",
})

LARGE_DIR_THRESHOLD = 50
MIN_FILES_FOR_UNIT = 2  # ignore near-empty directories


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass
class SubUnit:
    path: Path              # absolute path to the sub-unit directory
    name: str               # path relative to repo root (e.g. "src/auth")
    type: str               # "package" | "test" | "large" | "root"
    file_count: int         # number of source files owned by this unit
    boundary_reason: str    # human-readable explanation
    children: list[SubUnit] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"SubUnit({self.name!r}, type={self.type!r}, files={self.file_count})"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_ignored(name: str) -> bool:
    return name in IGNORE_DIRS or name.startswith(".")


def _is_test_dir(dir_path: Path) -> bool:
    name = dir_path.name.lower()
    return (
        name in TEST_DIR_NAMES
        or name.startswith("test_")
        or name.endswith("_test")
        or name.endswith("_tests")
        or name.endswith("_spec")
        or name.endswith("_specs")
    )


def _package_boundary_file(dir_path: Path) -> Optional[str]:
    """Return the name of the boundary file found in dir_path, or None."""
    for fname in PACKAGE_BOUNDARY_FILES:
        if (dir_path / fname).exists():
            return fname
    # __init__.py marks a Python sub-package (but not the repo root itself)
    if (dir_path / "__init__.py").exists():
        return "__init__.py"
    return None


def _count_direct_source_files(dir_path: Path) -> int:
    """Count source files directly in dir_path (not in subdirectories)."""
    return sum(
        1 for f in dir_path.iterdir()
        if f.is_file() and f.suffix in SOURCE_EXTENSIONS
    )


def _count_all_source_files(dir_path: Path) -> int:
    """Count all source files in dir_path including all subdirectories."""
    count = 0
    try:
        for f in dir_path.rglob("*"):
            if f.is_file() and f.suffix in SOURCE_EXTENSIONS:
                if not any(_is_ignored(part) for part in f.relative_to(dir_path).parts):
                    count += 1
    except PermissionError:
        pass
    return count


# ─── Core walk ────────────────────────────────────────────────────────────────

def _walk(
    current: Path,
    root: Path,
    depth: int,
    parent_is_boundary: bool,
) -> list[SubUnit]:
    """
    Recursively walk `current`, returning SubUnit leaves.

    parent_is_boundary: True when the caller already emitted a SubUnit for
    `current`'s parent — used to avoid emitting the parent again.
    """
    units: list[SubUnit] = []

    try:
        children = sorted(
            d for d in current.iterdir()
            if d.is_dir() and not _is_ignored(d.name)
        )
    except PermissionError:
        return units

    for child in children:
        rel = str(child.relative_to(root))

        # ── Rule 2: test directory ────────────────────────────────────────────
        if _is_test_dir(child):
            count = _count_all_source_files(child)
            if count >= MIN_FILES_FOR_UNIT:
                units.append(SubUnit(
                    path=child,
                    name=rel,
                    type="test",
                    file_count=count,
                    boundary_reason="test directory",
                ))
            # Don't recurse — test dirs are always leaf units
            continue

        # ── Rule 1: package boundary file ────────────────────────────────────
        boundary_file = _package_boundary_file(child)
        if boundary_file and depth >= 1:
            count = _count_all_source_files(child)
            if count >= MIN_FILES_FOR_UNIT:
                unit = SubUnit(
                    path=child,
                    name=rel,
                    type="package",
                    file_count=count,
                    boundary_reason=f"package boundary: {boundary_file}",
                )
                # Recurse to find deeper boundaries within this package
                unit.children = _walk(child, root, depth + 1, parent_is_boundary=True)
                units.append(unit)
            continue

        # ── Rule 3: large directory ───────────────────────────────────────────
        total_count = _count_all_source_files(child)
        if total_count > LARGE_DIR_THRESHOLD:
            unit = SubUnit(
                path=child,
                name=rel,
                type="large",
                file_count=total_count,
                boundary_reason=f"large directory ({total_count} source files > {LARGE_DIR_THRESHOLD})",
            )
            # Recurse — large dirs must subdivide further
            unit.children = _walk(child, root, depth + 1, parent_is_boundary=True)
            units.append(unit)
            continue

        # ── No rule matched — recurse into this dir transparently ────────────
        units.extend(_walk(child, root, depth + 1, parent_is_boundary=False))

    return units


# ─── Public API ───────────────────────────────────────────────────────────────

def discover_structure(repo_path: str | Path) -> list[SubUnit]:
    """
    Walk repo_path and return a flat list of SubUnit leaf nodes.

    Each SubUnit represents a directory that should get its own Memory Bank.
    The list is ordered depth-first (parent before children).
    If no sub-unit boundaries are found the entire repo is returned as one unit.

    Parameters
    ----------
    repo_path : str | Path
        Root of the repository or project directory to analyse.
    """
    root = Path(repo_path).resolve()
    units = _walk(root, root, depth=0, parent_is_boundary=False)

    if not units:
        count = _count_all_source_files(root)
        return [SubUnit(
            path=root,
            name=".",
            type="root",
            file_count=count,
            boundary_reason="no sub-unit boundaries detected — entire repository",
        )]

    return units


def flatten(units: list[SubUnit]) -> list[SubUnit]:
    """Return all SubUnits in depth-first order, including nested children."""
    result: list[SubUnit] = []
    for unit in units:
        result.append(unit)
        if unit.children:
            result.extend(flatten(unit.children))
    return result


def find_unit(units: list[SubUnit], path: Path) -> Optional[SubUnit]:
    """Find a SubUnit by exact path match (searches children recursively)."""
    for unit in flatten(units):
        if unit.path == path:
            return unit
    return None
