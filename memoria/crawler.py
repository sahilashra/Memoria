"""
Repo Crawler — walks a directory and reads what matters.
Skips noise (node_modules, .git, binaries) and respects token budgets.
Rich format files (PDF, Word, PowerPoint, Excel, audio) are extracted
via the extractors package rather than skipped.
"""

import yaml
from pathlib import Path
from typing import Dict, List, Tuple


DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv",
    "dist", "build", ".next", "coverage", ".pytest_cache",
    ".mypy_cache", ".tox", "eggs", ".eggs",
}

# True binaries / noise — no extractor can help here
DEFAULT_IGNORE_EXTENSIONS = {
    ".pyc", ".lock", ".log",
    ".ico", ".svg", ".woff", ".ttf", ".map",   # web assets — not informative
    ".exe", ".dll", ".so", ".dylib", ".bin",   # compiled binaries
    ".zip", ".tar", ".gz", ".rar",             # archives
    ".DS_Store",                               # macOS noise
}
# Note: .pdf, .docx, .pptx, .xlsx, .png, .jpg, .ipynb, .html etc. are NOT here —
# they are routed through the extractors package.

DEFAULT_MAX_FILE_SIZE = 50_000  # 50KB


class RepoCrawler:
    """
    Walks a repo directory and returns structured data about it.
    Designed to be token-efficient — large files are noted, not dumped.
    """

    def __init__(self, repo_path: str, config_path: str = "config.yaml"):
        self.repo_path = Path(repo_path).resolve()
        self.config_path = config_path
        self.config = self._load_config(config_path)

        self.ignore_dirs = set(self.config.get("ignore_dirs", [])) | DEFAULT_IGNORE_DIRS
        self.ignore_extensions = set(self.config.get("ignore_extensions", [])) | DEFAULT_IGNORE_EXTENSIONS
        self.max_file_size = self.config.get("max_file_size", DEFAULT_MAX_FILE_SIZE)

        self.structure: List[str] = []
        self.files: Dict[str, dict] = {}
        self.stats = {"total_files": 0, "skipped_large": 0, "skipped_binary": 0}

    def _load_config(self, config_path: str) -> dict:
        # 1. Try the given path directly
        path = Path(config_path)
        if path.exists():
            with open(path, "r", encoding="utf-8-sig") as f:
                return yaml.safe_load(f) or {}

        # 2. Walk up from CWD
        for parent in [Path.cwd(), *Path.cwd().parents]:
            candidate = parent / "config.yaml"
            if candidate.exists():
                try:
                    with open(candidate, "r", encoding="utf-8-sig") as f:
                        data = yaml.safe_load(f) or {}
                    if "model" in data or "books_dir" in data or "ignore_dirs" in data:
                        return data
                except Exception:
                    pass

        # 3. ~/.memoria/config.yaml
        user_config = Path.home() / ".memoria" / "config.yaml"
        if user_config.exists():
            with open(user_config, "r", encoding="utf-8-sig") as f:
                return yaml.safe_load(f) or {}

        return {}

    def crawl(self) -> dict:
        """Main entry point. Returns everything the generator needs."""
        if not self.repo_path.exists():
            raise FileNotFoundError(f"Path not found: {self.repo_path}")

        # ── Single file mode (e.g. memoria analyze --repo recording.m4a) ──
        if self.repo_path.is_file():
            return self._crawl_single_file()

        self._walk(self.repo_path, depth=0)

        return {
            "path": str(self.repo_path),
            "name": self.repo_path.name,
            "structure": "\n".join(self.structure),
            "files": self.files,
            "stats": self.stats,
        }

    def _crawl_single_file(self) -> dict:
        """Handle a single file passed directly as the repo path."""
        # Temporarily set repo_path to parent so relative_to gives the filename, not '.'
        original = self.repo_path
        self.repo_path = self.repo_path.parent
        self._read_file(original)
        self.repo_path = original   # restore
        self.stats["total_files"] = 1
        return {
            "path": str(original.parent),
            "name": original.stem,          # filename without extension as project name
            "structure": original.name,
            "files": self.files,
            "stats": self.stats,
        }

    def _walk(self, path: Path, depth: int):
        """Recursively walk the directory tree."""
        if depth > 8:
            return

        try:
            # Dirs first, then files — easier to read structure
            items = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except PermissionError:
            return

        for item in items:
            if item.name.startswith(".") and item.name not in {".env.example", ".gitignore"}:
                continue

            indent = "  " * depth

            if item.is_dir():
                if item.name in self.ignore_dirs:
                    continue
                self.structure.append(f"{indent}[{item.name}/]")
                self._walk(item, depth + 1)

            elif item.is_file():
                if item.suffix.lower() in self.ignore_extensions:
                    self.stats["skipped_binary"] += 1
                    continue

                self.structure.append(f"{indent}{item.name}")
                self._read_file(item)
                self.stats["total_files"] += 1

    def _read_file(self, file_path: Path):
        """Read a file, respecting size limits. Rich formats go through extractors."""
        from .extractors import SUPPORTED_EXTENSIONS, extract

        relative_path = str(file_path.relative_to(self.repo_path))
        ext = file_path.suffix.lower()

        # --- Rich format: PDF, Word, PowerPoint, Excel, images, audio ---
        if ext in SUPPORTED_EXTENSIONS:
            result = extract(file_path, config_path=self.config_path)
            self.files[relative_path] = {
                "content": result["content"],
                "size": file_path.stat().st_size,
                "truncated": result["truncated"],
                "extension": ext,
                "extractor": result["extractor"],
            }
            return

        # --- Plain text / code ---
        try:
            size = file_path.stat().st_size

            if size > self.max_file_size:
                self.files[relative_path] = {
                    "content": f"[File is {size // 1000}KB — too large to include fully. Key file: review manually.]",
                    "size": size,
                    "truncated": True,
                    "extension": ext,
                }
                self.stats["skipped_large"] += 1
                return

            content = file_path.read_text(encoding="utf-8", errors="ignore").strip()

            self.files[relative_path] = {
                "content": content,
                "size": size,
                "truncated": False,
                "extension": ext,
            }

        except Exception as e:
            self.files[relative_path] = {
                "content": f"[Could not read: {e}]",
                "size": 0,
                "truncated": False,
                "extension": ext,
            }

    def get_chunked_files(self, max_chars_per_chunk: int = 80_000) -> List[Dict]:
        """
        Split files into chunks for large repos.
        Each chunk is safe to send in a single model call.
        """
        chunks = []
        current_chunk = {}
        current_size = 0

        for path, data in self.files.items():
            content_size = len(data["content"])

            if current_size + content_size > max_chars_per_chunk and current_chunk:
                chunks.append(current_chunk)
                current_chunk = {}
                current_size = 0

            current_chunk[path] = data
            current_size += content_size

        if current_chunk:
            chunks.append(current_chunk)

        return chunks
