"""
File Watcher — monitors a repo directory for changes and drafts Memory Bank updates.
Uses watchdog under the hood. Debounced so rapid saves don't spam the AI.
"""

import time
import threading
import yaml
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# Extensions worth watching (code and config files only)
WATCH_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".java", ".rb",
    ".rs", ".cpp", ".c", ".cs", ".php", ".swift", ".kt",
    ".yaml", ".yml", ".json", ".toml", ".env", ".sh", ".sql",
    ".md", ".txt", ".html", ".css",
}

DEBOUNCE_SECONDS = 10  # wait 10s of quiet before triggering


class RepoChangeHandler(FileSystemEventHandler):
    """Debounced handler — waits for file activity to settle before triggering."""

    def __init__(self, repo_path: str, on_change_callback, ignore_dirs: set):
        self.repo_path = Path(repo_path)
        self.on_change = on_change_callback
        self.ignore_dirs = ignore_dirs
        self._timer = None
        self._changed_files = set()
        self._lock = threading.Lock()

    def on_modified(self, event):
        self._handle(event)

    def on_created(self, event):
        self._handle(event)

    def on_deleted(self, event):
        self._handle(event)

    def _handle(self, event):
        if event.is_directory:
            return

        path = Path(event.src_path)

        # Skip ignored dirs and extensions
        if any(part in self.ignore_dirs for part in path.parts):
            return
        if path.suffix.lower() not in WATCH_EXTENSIONS:
            return
        # Skip Memoria's own books directory
        if "books" in path.parts or ".archive" in path.parts:
            return

        with self._lock:
            self._changed_files.add(str(path.relative_to(self.repo_path)))
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, self._fire)
            self._timer.start()

    def _fire(self):
        with self._lock:
            files = list(self._changed_files)
            self._changed_files.clear()
        if files:
            self.on_change(files)


def start_watcher(repo_path: str, config_path: str = "config.yaml", books_dir: str = "books"):
    """
    Start watching a repo. Blocks until Ctrl+C.
    When changes settle, drafts a Memory Bank update.
    """
    from rich.console import Console
    from .generator import BookGenerator

    console = Console()
    config = _load_config(config_path)
    ignore_dirs = set(config.get("ignore_dirs", [])) | {
        ".git", "node_modules", "__pycache__", "venv", ".venv"
    }

    generator = BookGenerator(config_path)

    def on_change(changed_files: list):
        console.print(f"\n[cyan]Changes detected in {len(changed_files)} file(s):[/cyan]")
        for f in changed_files[:5]:
            console.print(f"  [dim]{f}[/dim]")
        if len(changed_files) > 5:
            console.print(f"  [dim]... and {len(changed_files) - 5} more[/dim]")

        console.print("\n[cyan]->[/cyan] Drafting Memory Bank update...")
        try:
            draft_path = generator.draft_update(
                repo_path=repo_path,
                output_dir=books_dir,
                on_progress=lambda s: console.print(f"  [cyan]->[/cyan] {s}"),
            )
            console.print(f"\n[green]Draft ready:[/green] [cyan]{draft_path}[/cyan]")
            console.print("[dim]Run [bold]memoria review[/bold] to approve or reject.[/dim]")
        except Exception as e:
            console.print(f"[red]Draft failed:[/red] {e}")

    handler = RepoChangeHandler(repo_path, on_change, ignore_dirs)
    observer = Observer()
    observer.schedule(handler, repo_path, recursive=True)
    observer.start()

    console.print(f"[bold green]Watching[/bold green] [cyan]{repo_path}[/cyan]")
    console.print("[dim]Changes will trigger a Memory Bank draft after 10s of quiet.[/dim]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    try:
        while observer.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        console.print("\n[dim]Watcher stopped.[/dim]")

    observer.join()


def _load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}
