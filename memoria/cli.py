"""
Memoria CLI — friendly interface for any user, technical or not.
Commands: init, analyze, update, ask
"""

import sys
import click
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.markdown import Markdown
from rich import print as rprint

from .generator import BookGenerator
from .models import ModelProvider

# Force UTF-8 on Windows to avoid CP1252 encoding errors
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

console = Console()


# ─── Global books directory ───────────────────────────────────

_PROJECT_SIGNALS = {
    ".git", "package.json", "requirements.txt", "setup.py", "pyproject.toml",
    "Cargo.toml", "go.mod", "Makefile", "Dockerfile", "tsconfig.json",
    "pom.xml", "build.gradle", "composer.json", ".gitignore",
}
_SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
    ".next", "coverage", ".idea", ".vscode", "target", "out", "bin", "obj",
    ".pytest_cache", ".mypy_cache", ".tox", "vendor",
}


def _is_project_dir(path: Path) -> bool:
    """Return True if path looks like an individual project root."""
    try:
        names = {e.name for e in path.iterdir()}
        return bool(names & _PROJECT_SIGNALS)
    except Exception:
        return False


def _find_projects(root: Path, books_dir: str) -> list:
    """
    Walk root up to 2 levels deep to find project-like subdirectories.
    Groups results by their immediate parent under root.

    Each entry: {path, group, is_analyzed, last_analyzed, sub_projects}
    sub_projects is a list of Path for monorepo-style nested projects.
    """
    from datetime import datetime as _dt
    books_path = Path(books_dir)
    results = []
    seen = set()

    def _book_status(p: Path):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in p.name)
        bp = books_path / f"{safe}_memory_bank.md"
        if bp.exists():
            ts = _dt.fromtimestamp(bp.stat().st_mtime).strftime("%Y-%m-%d")
            return True, ts
        return False, None

    def _scan(path: Path, depth: int, group: str):
        if path in seen or path.name.startswith(".") or path.name in _SKIP_DIRS:
            return
        seen.add(path)

        if _is_project_dir(path):
            analyzed, ts = _book_status(path)
            # Monorepo detection — look one level deeper for nested projects
            sub_projs = []
            try:
                for sub in sorted(path.iterdir()):
                    if sub.is_dir() and not sub.name.startswith(".") \
                            and sub.name not in _SKIP_DIRS and _is_project_dir(sub):
                        sub_projs.append(sub)
            except Exception:
                pass
            results.append({
                "path": path,
                "group": group,
                "is_analyzed": analyzed,
                "last_analyzed": ts,
                "sub_projects": sub_projs,
            })
            # Don't recurse further into a project we already captured
            return

        if depth >= 2:
            return

        try:
            for sub in sorted(path.iterdir()):
                if sub.is_dir():
                    _scan(sub, depth + 1, path.name if depth == 1 else group)
        except Exception:
            pass

    try:
        for sub in sorted(root.iterdir()):
            if sub.is_dir():
                _scan(sub, 1, sub.name)
    except Exception:
        pass

    return results


def _run_single_analyze(repo_path: Path, output: str, config: str, yes: bool,
                        context: str = None) -> bool:
    """
    Run a single analyze pass for one project directory.
    Returns True on success, False on skip/error.
    Used by the multi-project flow.
    """
    from .generator import BookGenerator
    from datetime import datetime as _dt

    name_base = repo_path.name
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name_base)
    existing_book = Path(output) / f"{safe_name}_memory_bank.md"

    if existing_book.exists() and not yes:
        last_updated = _dt.fromtimestamp(
            existing_book.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        console.print(
            f"\n  [yellow]Already analyzed[/yellow] (last updated [cyan]{last_updated}[/cyan])")
        console.print("    [1] Update   [2] Skip")
        choice = Prompt.ask("    Choose", choices=["1", "2"], default="2")
        if choice == "2":
            console.print("    [dim]Skipped.[/dim]")
            return False

    if not context:
        dir_type = _peek_dir_type(repo_path)
        question = ("What is this document collection? (1-2 sentences)"
                    if dir_type == "document"
                    else "What does this project do? (1-2 sentences)")
        default = ("A document collection" if dir_type == "document"
                   else "A software project")
        context = Prompt.ask(f"  Context", default=default)

    generator = BookGenerator(config)

    def on_progress(step: str):
        console.print(f"    [cyan]->[/cyan] {step}")

    try:
        output_path = generator.generate(
            repo_path=str(repo_path),
            company_context=context,
            output_dir=output,
            on_progress=on_progress,
        )
        usage = generator.model.usage_summary()
        abs_path = Path(output_path).resolve()
        usage_line = f"  [dim]Tokens:[/dim] {usage}" if usage else ""
        console.print(
            f"  [bold green]✓[/bold green] [cyan]{abs_path.name}[/cyan]{(' · ' + usage_line) if usage_line else ''}")
        # Record source path so `memoria ui` update never asks for it again
        try:
            from .meta import record as _meta_record
            project_name = repo_path.stem if repo_path.is_file() else repo_path.name
            _meta_record(project_name, str(repo_path.resolve()), context)
        except Exception:
            pass
        return True
    except RuntimeError as e:
        err = str(e)
        console.print(f"  [red]✗ Failed:[/red] {err}")
        if err.startswith("[AUTH]"):
            console.print("  [yellow]→[/yellow] Authentication failed. Check your API key or credentials in [bold]~/.memoria/.env[/bold].")
        elif err.startswith("[RATELIMIT]"):
            console.print("  [yellow]→[/yellow] API quota exhausted. Wait a few minutes or switch model in config.yaml.")
        elif err.startswith("[TRANSIENT]"):
            console.print("  [yellow]→[/yellow] Model temporarily overloaded. Retry in 30 seconds.")
        return False
    except Exception as e:
        console.print(f"  [red]✗ Failed:[/red] {e}")
        return False


def _handle_multi_project(root: Path, projects: list, output: str,
                          config: str, yes: bool):
    """
    Interactive flow when analyze detects multiple projects under a directory.
    Shows a grouped list with analysis status, then offers:
      A) Analyze each separately (recommended — default)
      B) Analyze as one combined book
      C) Pick which ones to analyze
    """
    # ── Group by parent folder ────────────────────────────────────────────────
    from collections import OrderedDict
    groups: dict = OrderedDict()
    for p in projects:
        g = p["group"]
        groups.setdefault(g, []).append(p)

    # ── Display ───────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        f"[bold cyan]{len(projects)} projects found[/bold cyan] inside "
        f"[dim]{root}[/dim]\n"
        "[dim]Memoria can analyze each one separately for focused, accurate results.[/dim]",
        border_style="cyan",
    ))

    idx = 1
    project_order = []   # flat list in display order
    for group_name, items in groups.items():
        console.print(f"\n  [bold]{group_name}[dim]/[/dim][/bold]")
        for item in items:
            status = (
                f"[green]✓ analyzed {item['last_analyzed']}[/green]"
                if item["is_analyzed"]
                else "[dim]— not analyzed[/dim]"
            )
            mono_note = ""
            if item["sub_projects"]:
                n = len(item["sub_projects"])
                mono_note = f" [yellow](monorepo: {n} sub-projects)[/yellow]"
            console.print(
                f"  [dim][{idx}][/dim]  {item['path'].name:<28} {status}{mono_note}"
            )
            project_order.append(item)
            idx += 1

    # ── Ask how to proceed ────────────────────────────────────────────────────
    console.print()
    console.print("  [bold][A][/bold] Analyze each project separately  "
                  "[dim](recommended)[/dim]")
    console.print("  [bold][B][/bold] Analyze as one combined book")
    console.print("  [bold][C][/bold] Let me pick which ones")

    if yes:
        choice = "a"
    else:
        choice = Prompt.ask("\n  How to proceed", default="A").strip().lower()

    if choice == "b":
        # Signal to caller: continue with the original single-book flow
        console.print("[dim]Continuing as a single combined analysis…[/dim]")
        return False

    # Build the list of projects to process
    if choice == "c":
        console.print(
            f"\n  Enter numbers to analyze (e.g. [cyan]1,3,5[/cyan] or "
            f"[cyan]all[/cyan]):"
        )
        raw = Prompt.ask("  Selection", default="all").strip().lower()
        if raw == "all":
            to_process = project_order
        else:
            selected = []
            for part in raw.replace(" ", "").split(","):
                try:
                    n = int(part)
                    if 1 <= n <= len(project_order):
                        selected.append(project_order[n - 1])
                except ValueError:
                    pass
            to_process = selected
            if not to_process:
                console.print("[red]No valid selections. Aborting.[/red]")
                raise click.Abort()
    else:
        # Option A — analyze all unanalyzed; offer update for already-done ones
        to_process = project_order

    # ── Validate model config once up front ──────────────────────────────────
    try:
        model = ModelProvider(config)
        console.print(
            f"\n[dim]Using:[/dim] [cyan]{model.provider_name}[/cyan] "
            f"[dim]({model.model})[/dim]"
        )
    except Exception as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise click.Abort()

    # ── Process each project ──────────────────────────────────────────────────
    console.print()
    done, skipped, failed = 0, 0, 0

    for item in to_process:
        p = item["path"]

        # Monorepo: ask whether to drill into sub-projects or treat as one
        if item["sub_projects"] and not yes:
            names = ", ".join(s.name for s in item["sub_projects"][:4])
            console.print(
                f"\n[bold]{p.name}[/bold] [yellow]contains sub-projects:[/yellow] "
                f"[dim]{names}[/dim]"
            )
            console.print("  [1] Analyze each sub-project separately")
            console.print("  [2] Analyze the whole thing as one book")
            sub_choice = Prompt.ask("  Choice", choices=["1", "2"], default="1")
            if sub_choice == "1":
                for sub in item["sub_projects"]:
                    console.print(f"\n[bold]→ {sub.name}[/bold]")
                    ok = _run_single_analyze(sub, output, config, yes)
                    if ok:
                        done += 1
                    else:
                        skipped += 1
                continue

        console.print(f"\n[bold]→ {p.name}[/bold]")
        ok = _run_single_analyze(p, output, config, yes)
        if ok:
            done += 1
        else:
            skipped += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        f"[bold green]Done![/bold green]  "
        f"[cyan]{done}[/cyan] analyzed  ·  "
        f"[dim]{skipped}[/dim] skipped\n\n"
        f"[dim]Run [bold]memoria ui[/bold] to explore them, or "
        f"[bold]memoria graph build[/bold] to map relationships.[/dim]",
        border_style="green",
    ))
    return True


def _default_books_dir() -> str:
    """
    Return the canonical books directory — resolved in this order:
      1. books_dir: from ~/.memoria/config.yaml  (set by `memoria init`),
         but only if the stored value is an absolute path. Relative paths
         in config are treated as legacy/invalid and ignored.
      2. Fallback: ~/.memoria/books/

    Creates the directory if it doesn't exist.
    This makes `memoria analyze`, `memoria ui`, and every other command
    read/write the same location regardless of the working directory.
    """
    import yaml
    cfg_path = Path.home() / ".memoria" / "config.yaml"
    if cfg_path.exists():
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            raw = cfg.get("books_dir", "")
            if raw:
                candidate = Path(raw).expanduser()
                # Only use it if it's an absolute path — relative paths from
                # old configs (books_dir: "books") are CWD-dependent and unsafe.
                if candidate.is_absolute():
                    candidate.mkdir(parents=True, exist_ok=True)
                    return str(candidate)
        except Exception:
            pass
    fallback = Path.home() / ".memoria" / "books"
    fallback.mkdir(parents=True, exist_ok=True)
    return str(fallback)


# ─── Branding ─────────────────────────────────────────────────

def _peek_dir_type(path: Path) -> str:
    """
    Quick (non-recursive) peek at a directory to classify it as:
    'code', 'document', 'process', or 'unknown' — used to adapt the context
    question and progress messages before a full crawl.
    """
    CODE_SIGNALS = {
        "package.json", "requirements.txt", "setup.py", "pyproject.toml",
        "Cargo.toml", "go.mod", "Makefile", "Dockerfile", ".git",
        "tsconfig.json", "pom.xml", "build.gradle",
    }
    CODE_EXTS = {".py", ".js", ".ts", ".go", ".java", ".rb", ".rs", ".cpp", ".cs"}
    DOC_EXTS  = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".csv"}
    PROCESS_KEYWORDS = {
        "sop", "runbook", "playbook", "procedure", "procedures", "workflow",
        "workflows", "checklist", "checklists", "escalation", "protocol",
        "incident", "oncall", "on-call", "deployment", "rollback", "triage",
        "postmortem", "post-mortem", "onboarding", "offboarding",
    }

    try:
        entries = list(path.iterdir())
    except Exception:
        return "unknown"

    names   = {e.name for e in entries}
    exts    = {e.suffix.lower() for e in entries if e.is_file()}
    stems   = {e.stem.lower().replace("-", " ").replace("_", " ") for e in entries if e.is_file()}

    if names & CODE_SIGNALS or exts & CODE_EXTS:
        return "code"
    # Process dir detection — directory name or file stems contain SOP keywords
    dir_name_lower = path.name.lower().replace("-", " ").replace("_", " ")
    dir_words = set(dir_name_lower.split())
    if dir_words & PROCESS_KEYWORDS:
        return "process"
    for stem in stems:
        stem_words = set(stem.split())
        if stem_words & PROCESS_KEYWORDS:
            return "process"
    if exts & DOC_EXTS:
        return "document"
    # Check one level deeper if top level was empty/dirs only
    for sub in entries:
        if sub.is_dir():
            try:
                sub_exts  = {f.suffix.lower() for f in sub.iterdir() if f.is_file()}
                sub_stems = {f.stem.lower().replace("-", " ").replace("_", " ")
                             for f in sub.iterdir() if f.is_file()}
                for ss in sub_stems:
                    if set(ss.split()) & PROCESS_KEYWORDS:
                        return "process"
                if sub_exts & DOC_EXTS:
                    return "document"
                if sub_exts & CODE_EXTS:
                    return "code"
            except Exception:
                pass
    return "unknown"


def _suggest_similar(path: Path):
    """When a path isn't found, search nearby for files with a similar name."""
    parent = path.parent
    if not parent.exists():
        return
    name_lower = path.name.lower()
    name_stem  = path.stem.lower()[:5]   # first 5 chars as fuzzy match seed

    candidates = []
    try:
        # Search parent dir + one level of subdirectories
        search_dirs = [parent] + [d for d in parent.iterdir() if d.is_dir()]
        for d in search_dirs[:10]:   # cap at 10 subdirs to stay fast
            try:
                for p in d.iterdir():
                    if p.is_file() and name_stem and (
                        name_stem in p.name.lower() or
                        p.name.lower().startswith(name_stem) or
                        p.suffix.lower() == path.suffix.lower()  # same extension
                        and name_stem in p.stem.lower()
                    ):
                        if p != path:   # don't suggest the exact wrong path
                            candidates.append(p)
            except Exception:
                pass
    except Exception:
        pass

    if candidates:
        console.print("[dim]Did you mean one of these?[/dim]")
        for s in sorted(set(candidates))[:5]:
            console.print(f"  [cyan]{s}[/cyan]")


def print_banner():
    console.print(Panel.fit(
        "[bold cyan]Memoria[/bold cyan] [dim]- Living memory banks for any codebase[/dim]\n"
        "[dim]Helps humans onboard faster. Helps AI work accurately.[/dim]",
        border_style="cyan"
    ))


# ─── CLI Group ────────────────────────────────────────────────

@click.group()
def cli():
    """Memoria — structure your codebase knowledge, once and forever."""
    pass


# ─── Command: init ────────────────────────────────────────────

@cli.command()
def init():
    """
    Set up Memoria for the first time.
    Creates ~/.memoria/config.yaml with your model and API key.
    Run this once after install — then memoria works from any directory.

    \b
    Examples:
      memoria init
    """
    print_banner()

    config_dir = Path.home() / ".memoria"
    config_path = config_dir / "config.yaml"

    if config_path.exists():
        console.print(f"\n[yellow]Config already exists:[/yellow] [cyan]{config_path}[/cyan]")
        if not Confirm.ask("Overwrite it?", default=False):
            console.print("[dim]Keeping existing config.[/dim]")
            return

    console.print("\n[bold]Let's set up Memoria.[/bold]")
    console.print("[dim]This creates ~/.memoria/config.yaml — works from any directory after this.[/dim]\n")

    # Pick provider
    console.print("[bold]Which AI provider do you want to use?[/bold]")
    console.print("  [1] Google Gemini      [dim](gemini/gemini-2.0-flash)[/dim]")
    console.print("  [2] OpenAI             [dim](gpt-4o)[/dim]")
    console.print("  [3] Anthropic Claude   [dim](claude-sonnet-4-5)[/dim]")
    console.print("  [4] Ollama (local)     [dim](ollama/llama3 — no API key needed)[/dim]")
    console.print("  [5] Other              [dim](enter model name manually)[/dim]")

    choice = Prompt.ask("\n  Provider", choices=["1", "2", "3", "4", "5"], default="1")

    model_map = {
        "1": ("gemini/gemini-2.0-flash",  "GEMINI_API_KEY",    "https://aistudio.google.com/app/apikey"),
        "2": ("gpt-4o",                   "OPENAI_API_KEY",    "https://platform.openai.com/api-keys"),
        "3": ("claude-sonnet-4-5",        "ANTHROPIC_API_KEY", "https://console.anthropic.com/"),
        "4": ("ollama/llama3",            None,                None),
        "5": (None,                       None,                None),
    }

    model, env_var, key_url = model_map[choice]

    if choice == "5":
        model = Prompt.ask("  Enter model name (e.g. gemini/gemini-1.5-pro)")

    # API key
    env_path = config_dir / ".env"
    api_key = None
    if env_var:
        console.print(f"\n[bold]API key for {env_var}[/bold]")
        if key_url:
            console.print(f"[dim]Get one at: {key_url}[/dim]")
        api_key = Prompt.ask(f"  {env_var}", password=True)

    # Books output dir
    books_dir = Prompt.ask(
        "\n  Where should memory banks be saved?",
        default=str(Path.home() / ".memoria" / "books"),
    )

    # Write config
    config_dir.mkdir(parents=True, exist_ok=True)

    config_content = f"""# Memoria Configuration — generated by `memoria init`
# Edit this file to change your model or settings.

model: "{model}"

max_tokens_per_file: 2000
max_tokens_output: 4000

books_dir: "{books_dir}"

ignore_dirs:
  - .git
  - node_modules
  - __pycache__
  - venv
  - .venv
  - dist
  - build
  - .next
  - coverage

ignore_extensions:
  - .pyc
  - .lock
  - .log
  - .ico
  - .woff
  - .ttf
  - .exe
  - .dll
  - .bin
  - .zip
  - .tar
  - .gz

max_file_size: 50000

hooks:
  on_pr_merge:
    enabled: false
    only_if_files_changed: []
  on_schedule:
    enabled: false
    cron: "0 9 * * 1"
  on_manual:
    enabled: true
"""
    config_path.write_text(config_content, encoding="utf-8")

    # Write .env with API key
    if api_key and env_var:
        env_path.write_text(f"{env_var}={api_key}\n", encoding="utf-8")
        console.print(f"\n[dim]API key saved to:[/dim] [cyan]{env_path}[/cyan]")

    console.print()
    console.print(Panel.fit(
        f"[bold green]Memoria is ready![/bold green]\n\n"
        f"[dim]Config:[/dim]  [cyan]{config_path}[/cyan]\n"
        f"[dim]Books:[/dim]   [cyan]{books_dir}[/cyan]\n"
        f"[dim]Model:[/dim]   [cyan]{model}[/cyan]\n\n"
        f"[dim]Run from any directory:\n"
        f"  memoria analyze --repo ./your-project[/dim]",
        border_style="green"
    ))


# ─── Command: analyze ─────────────────────────────────────────

@cli.command()
@click.option("--repo", "-r", default=None, help="Path to the repo to analyze")
@click.option("--context", "-c", default=None, help="Brief company/project context")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
@click.option("--output", "-o", default=None, help="Output directory for memory banks")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.option(
    "--type", "content_type",
    type=click.Choice(["code", "document", "audio", "video", "notebook", "process"], case_sensitive=False),
    default=None,
    help="Force content type (default: auto-detect). Use 'process' for SOPs, runbooks, and workflows.",
)
def analyze(repo, context, config, output, yes, content_type):
    """
    Analyze a repo and generate its Memory Bank.

    \b
    Examples:
      memoria analyze
      memoria analyze --repo ./my-project
      memoria analyze --repo ./my-project --context "B2B SaaS for HR teams"
      memoria analyze --repo ./runbooks --type process
    """
    if not output:
        output = _default_books_dir()
    print_banner()

    # ── RBAC ──
    try:
        from .rbac import check_permission, Permission, PermissionDenied
        check_permission(Permission.WRITE, config)
    except PermissionDenied as exc:
        console.print(f"[bold red]Permission denied[/bold red]\n{exc}")
        raise click.Abort()

    # ── Get repo path ──
    if not repo:
        console.print("\n[bold]Which repo do you want to analyze?[/bold]")
        repo = Prompt.ask(
            "  Repo path",
            default=str(Path.cwd()),
        )

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        console.print(f"[red]Path not found:[/red] {repo_path}")
        _suggest_similar(repo_path)
        raise click.Abort()

    # ── Multi-project detection ──
    # If the given path is a directory containing multiple project-like subdirs,
    # offer to analyze each one separately instead of making one big unfocused book.
    if repo_path.is_dir():
        projects = _find_projects(repo_path, output)
        if len(projects) >= 2:
            handled = _handle_multi_project(repo_path, projects, output, config, yes)
            if handled:   # True = done;  False = user chose "one combined book"
                return
            # False → fall through to single-book analysis below

    # ── Check if book already exists ──
    # Use stem (no extension) for single files so we don't get "recording.m4a_memory_bank.md"
    name_base = repo_path.stem if repo_path.is_file() else repo_path.name
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name_base)
    existing_book = Path(output) / f"{safe_name}_memory_bank.md"
    if existing_book.exists() and not yes:
        from datetime import datetime
        last_updated = datetime.fromtimestamp(existing_book.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        console.print(f"\n[yellow]Memory Bank already exists[/yellow] (last updated: [cyan]{last_updated}[/cyan])")
        console.print(f"[dim]File:[/dim] {existing_book}")
        console.print("\n  [1] Update it — regenerate from current repo state")
        console.print("  [2] Abort — keep the existing book")
        choice = Prompt.ask("\n  Choose", choices=["1", "2"], default="1")
        if choice == "2":
            console.print("[dim]Keeping existing book. Run [bold]memoria ask[/bold] to query it.[/dim]")
            raise click.Abort()

    # ── Get context — question adapts to what's being analyzed ──
    if not context:
        is_file = repo_path.is_file()
        ext = repo_path.suffix.lower() if is_file else ""

        audio_exts  = {".mp3", ".m4a", ".wav"}
        video_exts  = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
        doc_exts    = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}
        image_exts  = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

        if is_file and ext in video_exts:
            question = "What is this video? (meeting recording, lecture, demo, screen recording...)"
            default  = "A video recording"
        elif is_file and ext in audio_exts:
            question = "What is this recording? (meeting, lecture, voice note, call...)"
            default  = "A voice recording"
        elif is_file and ext in doc_exts:
            question = "What is this document? (1-2 sentences)"
            default  = "A document"
        elif is_file and ext in image_exts:
            question = "What is this image? (diagram, screenshot, photo...)"
            default  = "An image"
        elif not is_file:
            dir_type = _peek_dir_type(repo_path)
            if content_type == "process" or dir_type == "process":
                question = "What process or procedure is this? (1-2 sentences)"
                default  = "A process or SOP"
            elif dir_type == "document":
                question = "What is this document collection? (1-2 sentences)"
                default  = "A document collection"
            else:
                question = "What does this project do? (1-2 sentences)"
                default  = "A software project"
        else:
            question = "What does this file do? (1-2 sentences)"
            default  = "A file"

        console.print("\n[bold]Give Memoria some context.[/bold]")
        console.print("[dim]This helps the AI write a better, more accurate memory bank.[/dim]")
        context = Prompt.ask(f"\n  {question}", default=default)

    # ── Confirm ──
    # Show the actual resolved config path, not just "config.yaml"
    from .models import ModelProvider as _MP
    resolved_config = config
    for candidate in [
        config,
        str(Path.cwd() / "config.yaml"),
        str(Path.home() / ".memoria" / "config.yaml"),
    ]:
        if Path(candidate).exists():
            resolved_config = candidate
            break

    console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("[dim]Repo[/dim]", str(repo_path))
    table.add_row("[dim]Context[/dim]", context)
    table.add_row("[dim]Config[/dim]", resolved_config)
    table.add_row("[dim]Output[/dim]", output)
    if content_type:
        table.add_row("[dim]Type[/dim]", f"[magenta]{content_type}[/magenta] [dim](forced)[/dim]")
    console.print(Panel(table, title="Ready to analyze", border_style="green"))

    if not yes and not Confirm.ask("\nProceed?", default=True):
        console.print("[dim]Cancelled. Nothing was generated.[/dim]")
        raise click.Abort()

    # ── Check model config ──
    try:
        model = ModelProvider(config)
        console.print(f"\n[dim]Using:[/dim] [cyan]{model.provider_name}[/cyan] [dim]({model.model})[/dim]")
    except Exception as e:
        console.print(f"[red]Config error:[/red] {e}")
        console.print("[yellow]Tip:[/yellow] Copy .env.example to .env and add your API key.")
        raise click.Abort()

    # ── Generate ──
    console.print()
    generator = BookGenerator(config)
    steps = []

    def on_progress(step: str):
        console.print(f"  [cyan]->[/cyan] {step}")

    try:
        output_path = generator.generate(
            repo_path=str(repo_path),
            company_context=context,
            output_dir=output,
            on_progress=on_progress,
            content_type_override=content_type,
        )
        usage = generator.model.usage_summary()
    except RuntimeError as e:
        err = str(e)
        console.print(f"\n[red]✗ Analysis failed:[/red] {err}")
        if err.startswith("[AUTH]"):
            console.print(
                "[yellow]→[/yellow] Authentication failed. Check your API key or credentials in [bold]~/.memoria/.env[/bold].\n"
                "[yellow]→[/yellow] For AWS: set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION_NAME.\n"
                "[yellow]→[/yellow] Run [bold]memoria init[/bold] to reconfigure."
            )
        elif err.startswith("[RATELIMIT]"):
            console.print(
                "[yellow]→[/yellow] API quota exhausted. Wait a few minutes, check your quota dashboard, "
                "or switch to a different model in [bold]config.yaml[/bold]."
            )
        elif err.startswith("[TRANSIENT]"):
            console.print("[yellow]→[/yellow] Model temporarily overloaded. Retry in 30 seconds — this is on the provider's side.")
        elif "404" in err or "not found" in err.lower():
            console.print("[yellow]→[/yellow] Model not found. Check [bold]model:[/bold] in config.yaml")
        else:
            console.print("[yellow]→[/yellow] Check your API key in [bold].env[/bold] and model name in [bold]config.yaml[/bold]")
        raise click.Abort()
    except Exception as e:
        console.print(f"\n[red]✗ Unexpected error:[/red] {e}")
        raise click.Abort()

    # ── Done ──
    console.print()
    usage_line = f"\n[dim]Tokens used:[/dim] {usage}" if usage else ""
    abs_path = Path(output_path).resolve()
    console.print(Panel.fit(
        f"[bold green]Memory Bank generated![/bold green]\n\n"
        f"[dim]Saved to:[/dim] [cyan]{abs_path}[/cyan]"
        f"{usage_line}\n\n"
        f"[dim]Share this file with your team and AI tools.\n"
        f"Run [bold]memoria update[/bold] to refresh it after major changes.[/dim]",
        border_style="green"
    ))
    # Record source path so `memoria ui` update never asks for it again
    try:
        from .meta import record as _meta_record
        project_name = repo_path.stem if repo_path.is_file() else repo_path.name
        _meta_record(project_name, str(repo_path.resolve()), context)
    except Exception:
        pass


# ─── Helper: GitHub pull (built-in, no MCP server needed) ─────

def _pull_github_source(books_dir: str):
    """Pull GitHub activity (commits + PRs) for projects with git repos."""
    from pathlib import Path
    from .github_source import generate_github_context, get_recent_commits, get_recent_prs

    books_path = Path(books_dir)
    books = list(books_path.glob("*_memory_bank.md"))

    if not books:
        console.print("[yellow]No Memory Banks found.[/yellow] Run [cyan]memoria analyze[/cyan] first.")
        return

    console.print(f"\n[bold]Pulling GitHub activity for {len(books)} project(s)...[/bold]\n")
    success = 0

    for book in books:
        project = book.stem.replace("_memory_bank", "")

        # Try to find the repo path
        repo_path = None
        content = book.read_text(encoding="utf-8")
        # Look for source path in the first 10 lines
        import re
        for line in content.splitlines()[:10]:
            if "source:" in line.lower() or "path:" in line.lower():
                path_match = re.search(r'[`"]?([A-Za-z]:\\[^`"]+|/[^`"]+)[`"]?', line)
                if path_match:
                    candidate = Path(path_match.group(1))
                    if (candidate / ".git").exists():
                        repo_path = str(candidate)
                        break

        # Fallback: try common paths
        if not repo_path:
            for candidate in [
                Path.cwd() / project,
                Path.cwd().parent / project,
                Path.home() / "Projects" / project,
                Path.home() / "projects" / project,
            ]:
                if (candidate / ".git").exists():
                    repo_path = str(candidate)
                    break

        if not repo_path:
            console.print(f"  [dim]{project}:[/dim] [yellow]no git repo found, skipping[/yellow]")
            continue

        # Pull the data
        commits = get_recent_commits(repo_path)
        prs = get_recent_prs(repo_path)

        if not commits and not prs:
            console.print(f"  [dim]{project}:[/dim] [yellow]no recent activity[/yellow]")
            continue

        result_path = generate_github_context(repo_path, project, books_dir)
        if result_path:
            console.print(
                f"  [bold cyan]{project}[/bold cyan]: "
                f"[green]{len(commits)} commits[/green], "
                f"[green]{len(prs)} PRs[/green] → {Path(result_path).name}"
            )
            success += 1

    console.print(f"\n[bold green]Done.[/bold green] Updated GitHub context for {success} project(s).")
    if success:
        console.print("[dim]This context is now injected into Ask queries automatically.[/dim]")


# ─── Command group: schedule ──────────────────────────────────

@cli.group()
def schedule():
    """
    Manage scheduled MCP pulls.

    \b
    Sub-commands:
      list    show all scheduled sources and their last pull time
      run     run all due sources right now (one-shot)
      start   start a blocking scheduler daemon
      reset   clear pull history for a source (next pull will be full)
    """
    pass


@schedule.command(name="list")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def schedule_list(config):
    """
    Show all configured MCP sources with their schedule and last pull time.

    \b
    Examples:
      memoria schedule list
      memoria schedule list --config ~/.memoria/config.yaml
    """
    print_banner()

    from .mcp_sources import load_sources
    from .pull_state import get_all_states
    from .scheduler import parse_interval, describe_interval, is_due

    sources = load_sources(config)
    if not sources:
        console.print("\n[yellow]No MCP sources configured.[/yellow]")
        console.print("[dim]Add  mcp_sources:  to your config.yaml to get started.[/dim]")
        return

    states = get_all_states()

    table = Table(title="MCP Sources", border_style="cyan", show_lines=False)
    table.add_column("Source",    style="bold cyan", min_width=14)
    table.add_column("Schedule",  style="dim",       min_width=10)
    table.add_column("Incremental", justify="center", min_width=11)
    table.add_column("Last pull",  min_width=16)
    table.add_column("Status",    min_width=12)

    for src in sources:
        name     = src.get("name", "?")
        sched    = src.get("schedule", "")
        incr     = "✓" if src.get("incremental") else "—"
        state    = states.get(name, {})
        last_ts  = state.get("last_pull")
        last_str = "never"
        if last_ts:
            try:
                from datetime import datetime as _dt
                last_str = _dt.fromisoformat(last_ts).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                last_str = last_ts

        if not sched:
            sched_str  = "[dim]manual[/dim]"
            status_str = "[dim]—[/dim]"
        else:
            interval = parse_interval(str(sched))
            sched_str = f"[cyan]{describe_interval(str(sched))}[/cyan]" if interval else f"[red]{sched}[/red]"
            if interval is None:
                status_str = "[red]unsupported[/red]"
            elif is_due(src):
                status_str = "[yellow]due now[/yellow]"
            else:
                status_str = "[green]up to date[/green]"

        table.add_row(name, sched_str, incr, last_str, status_str)

    console.print()
    console.print(table)
    console.print()
    console.print("[dim]Run [bold]memoria schedule run[/bold] to pull all due sources.[/dim]")
    console.print("[dim]Run [bold]memoria schedule start[/bold] to keep them updated automatically.[/dim]")


@schedule.command(name="run")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
@click.option("--output", "-o", default=None, help="Output directory for memory banks")
def schedule_run(config, output):
    """
    Run all sources that are currently due right now (one-shot).
    Does nothing if no sources are due.

    \b
    Examples:
      memoria schedule run
      memoria schedule run --output ./books
    """
    if not output:
        output = _default_books_dir()
    print_banner()

    from .scheduler import run_due, due_sources

    sources = due_sources(config)
    if not sources:
        console.print("\n[yellow]No sources are due to run right now.[/yellow]")
        console.print("[dim]Run [bold]memoria schedule list[/bold] to see when each source is next due.[/dim]")
        return

    console.print(f"\n[bold]{len(sources)} source(s) due to run:[/bold] "
                  f"{', '.join(s['name'] for s in sources)}\n")

    results = run_due(
        config_path=config,
        books_dir=output,
        on_progress=lambda s: console.print(f"  [cyan]->[/cyan] {s}"),
    )

    if results:
        console.print()
        console.print(Panel.fit(
            f"[bold green]Scheduled run complete![/bold green]\n\n" +
            "\n".join(f"  [dim]→[/dim] [cyan]{p}[/cyan]" for p in results),
            border_style="green",
        ))
    else:
        console.print("\n[yellow]No books were generated — sources returned no content.[/yellow]")


@schedule.command(name="start")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
@click.option("--output", "-o", default=None, help="Output directory for memory banks")
@click.option("--poll", default=60, show_default=True,
              help="How often to check for due sources (seconds)")
def schedule_start(config, output, poll):
    """
    Start a blocking scheduler daemon — checks for due sources every --poll seconds.
    Press Ctrl+C to stop.

    Run this in a terminal or background process to keep your Memory Banks
    automatically up to date without manual  memoria pull  commands.

    \b
    Examples:
      memoria schedule start
      memoria schedule start --poll 300      # check every 5 minutes
      nohup memoria schedule start &         # background on Unix
    """
    if not output:
        output = _default_books_dir()
    print_banner()

    from .scheduler import start_daemon, scheduled_sources, _load_brief_config

    sources   = scheduled_sources(config)
    brief_cfg = _load_brief_config(config)

    if not sources and not brief_cfg:
        console.print("\n[yellow]No scheduled sources or brief schedule found.[/yellow]")
        console.print("[dim]Add a  schedule:  field to a source, or a  brief:  block:[/dim]")
        console.print("[dim]    mcp_sources:[/dim]")
        console.print("[dim]      - name: confluence[/dim]")
        console.print("[dim]        schedule: daily[/dim]")
        console.print("[dim]    brief:[/dim]")
        console.print("[dim]      schedule: \"0 8 * * 1-5\"  # weekdays at 8 AM[/dim]")
        console.print("[dim]      delivery: terminal[/dim]")
        return

    if brief_cfg:
        console.print(
            f"\n[dim]Brief schedule:[/dim] [cyan]{brief_cfg.get('schedule')}[/cyan]  "
            f"delivery: [cyan]{brief_cfg.get('delivery', 'terminal')}[/cyan]"
        )

    console.print()
    start_daemon(
        config_path=config,
        books_dir=output,
        on_progress=lambda s: console.print(f"  [cyan]->[/cyan] {s}"),
        poll_seconds=poll,
    )


@schedule.command(name="reset")
@click.argument("source_name")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def schedule_reset(source_name, config):
    """
    Clear pull history for SOURCE_NAME so the next pull fetches everything.
    Use this when you want a full re-pull regardless of incremental settings.

    \b
    Examples:
      memoria schedule reset confluence
      memoria schedule reset slack
    """
    from .pull_state import clear_state, get_last_pull

    last = get_last_pull(source_name)
    if last is None:
        console.print(f"[yellow]No pull history found for '{source_name}'.[/yellow]")
        return

    clear_state(source_name)
    console.print(
        f"[green]Cleared.[/green] Pull history for [bold]{source_name}[/bold] removed.\n"
        f"[dim]Next pull will fetch all content (no  since:  filter).[/dim]"
    )


# ─── Command: pull ────────────────────────────────────────────

@cli.command()
@click.option("--source", "-s", default=None, help="Pull from one specific source by name")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
@click.option("--output", "-o", default=None, help="Output directory for memory banks")
@click.option("--list-tools", is_flag=True, help="List available tools on each source (no pull)")
@click.option("--incremental", is_flag=True, default=False,
              help="Force incremental mode: inject last-pull timestamp into tool calls "
                   "(overrides per-source incremental: setting)")
@click.option("--reset", is_flag=True, default=False,
              help="Reset pull history before pulling (forces a full re-pull)")
def pull(source, config, output, list_tools, incremental, reset):
    """
    Pull content from configured MCP sources and generate Memory Banks.

    MCP sources are external data connections — Confluence, Notion, Teams, Slack, etc.
    Configure them under  mcp_sources:  in config.yaml.

    \b
    Examples:
      memoria pull
      memoria pull --source confluence
      memoria pull --list-tools
      memoria pull --incremental          # only fetch content newer than last pull
      memoria pull --reset                # clear pull history, do a full re-pull
      memoria pull --source slack --output ./books
    """
    if not output:
        output = _default_books_dir()
    print_banner()

    # ── RBAC ──
    try:
        from .rbac import check_permission, Permission, PermissionDenied
        check_permission(Permission.PULL, config)
    except PermissionDenied as exc:
        console.print(f"[bold red]Permission denied[/bold red]\n{exc}")
        raise click.Abort()

    from .mcp_sources import load_sources, pull_source
    from .pull_state import get_last_pull, set_last_pull, clear_state

    sources = load_sources(config)
    if not sources:
        console.print("\n[yellow]No MCP sources configured.[/yellow]\n")
        console.print("[bold]Add sources to your config.yaml:[/bold]\n")
        console.print("[dim]    mcp_sources:[/dim]")
        console.print("[dim]      - name: \"confluence\"[/dim]")
        console.print("[dim]        server: \"npx @atlassian/mcp-confluence\"[/dim]")
        console.print("[dim]        env:[/dim]")
        console.print("[dim]          CONFLUENCE_TOKEN: \"${CONFLUENCE_TOKEN}\"[/dim]")
        console.print("[dim]        pull:[/dim]")
        console.print("[dim]          - tool: \"confluence_search\"[/dim]")
        console.print("[dim]            args: {query: \"architecture\", limit: 20}[/dim]\n")
        console.print("[dim]See CAPABILITIES.md for the full connector reference.[/dim]")
        return

    # Special built-in source: github
    if source and source.lower() == "github":
        _pull_github_source(output)
        return

    # Filter to a specific source if requested
    if source:
        targets = [s for s in sources if s.get("name") == source]
        if not targets:
            names = [s.get("name") for s in sources]
            console.print(f"[red]Source '{source}' not found.[/red]")
            console.print(f"[dim]Configured sources: {', '.join(names)}[/dim]")
            raise click.Abort()
    else:
        targets = sources

    if list_tools:
        # Temporarily clear pull: on each source so _pull_async returns the tool list
        targets = [{**s, "pull": []} for s in targets]

    console.print(f"\n[bold]Connecting to {len(targets)} source(s)...[/bold]\n")

    try:
        model = ModelProvider(config)
        console.print(f"[dim]Using:[/dim] [cyan]{model.provider_name}[/cyan] [dim]({model.model})[/dim]\n")
    except Exception as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise click.Abort()

    generator = BookGenerator(config)

    for src in targets:
        name = src.get("name", "unknown")
        console.print(f"[bold cyan]{name}[/bold cyan]")

        # Show last pull time if available
        last = get_last_pull(name)
        if last and not list_tools:
            console.print(f"  [dim]Last pull: {last.strftime('%Y-%m-%d %H:%M')}[/dim]")

        # Handle --reset: clear pull history so this is treated as a first-ever pull
        if reset:
            clear_state(name)
            console.print("  [dim]Pull history cleared — doing a full pull.[/dim]")
            last = None

        console.print(f"  [cyan]->[/cyan] Connecting to [dim]{src.get('server', '')}[/dim]...")

        # Determine since: CLI flag overrides per-source config
        use_incremental = incremental or bool(src.get("incremental"))
        since_dt = (get_last_pull(name) if use_incremental else None)

        if since_dt and not list_tools:
            console.print(f"  [dim]Incremental: fetching content after {since_dt.strftime('%Y-%m-%d %H:%M')}[/dim]")

        try:
            content = pull_source(src, since=since_dt)
        except ImportError as e:
            console.print(f"  [red]Missing dependency:[/red] {e}")
            console.print("  [dim]Run: pip install mcp[/dim]")
            continue
        except Exception as e:
            console.print(f"  [red]Connection failed:[/red] {e}")
            console.print("  [dim]Check your server command and env tokens in .env[/dim]")
            continue

        char_count = len(content)
        console.print(f"  [cyan]->[/cyan] Pulled [bold]{char_count:,}[/bold] chars")

        if list_tools:
            # Just print the tool list, no generation
            console.print()
            for line in content.splitlines():
                console.print(f"  [dim]{line}[/dim]")
            console.print()
            continue

        if not content.strip() or content.startswith("[No content"):
            console.print("  [yellow]No content returned — check your pull: config.[/yellow]\n")
            continue


        # Ask for context if single source; skip prompt for batch pulls
        if len(targets) == 1:
            console.print("\n[bold]Give Memoria some context about this source.[/bold]")
            console.print("[dim]Helps the AI write a more accurate memory bank.[/dim]")
            context = Prompt.ask(
                f"\n  What is [bold]{name}[/bold]?",
                default=src.get("context", f"Content pulled from {name}"),
            )
        else:
            context = src.get("context", f"Content pulled from {name}")

        console.print(f"  [cyan]->[/cyan] Generating memory bank...")

        try:
            output_path = generator.generate_from_text(
                source_name=name,
                company_context=context,
                content=content,
                output_dir=output,
                on_progress=lambda s: console.print(f"  [cyan]->[/cyan] {s}"),
            )
            # Record successful pull with book path
            set_last_pull(name, book_path=output_path)
            usage = generator.model.usage_summary()
            usage_line = f"\n[dim]Tokens used:[/dim] {usage}" if usage else ""
            console.print(Panel.fit(
                f"[bold green]Done![/bold green]  [cyan]{output_path}[/cyan]{usage_line}",
                border_style="green",
            ))
        except Exception as e:
            console.print(f"  [red]Generation failed:[/red] {e}")

        console.print()


# ─── Command: update ──────────────────────────────────────────

@cli.command()
@click.option("--repo", "-r", default=None, help="Path to the repo")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
@click.option("--output", "-o", default=None, help="Books directory")
def update(repo, config, output):
    """
    Re-analyze a repo and update its Memory Bank.
    Run this after major PRs, new features, or team changes.

    \b
    Examples:
      memoria update
      memoria update --repo ./my-project
    """
    if not output:
        output = _default_books_dir()
    print_banner()

    # ── RBAC ──
    try:
        from .rbac import check_permission, Permission, PermissionDenied
        check_permission(Permission.WRITE, config)
    except PermissionDenied as exc:
        console.print(f"[bold red]Permission denied[/bold red]\n{exc}")
        raise click.Abort()

    console.print("\n[bold]Updating Memory Bank...[/bold]")
    console.print("[dim]This will regenerate the book from the current state of the repo.[/dim]\n")

    # Re-use analyze logic but skip the confirmation questions
    if not repo:
        repo = Prompt.ask("  Repo path", default=str(Path.cwd()))

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        console.print(f"[red]Path not found:[/red] {repo_path}")
        _suggest_similar(repo_path)
        raise click.Abort()

    # Look for existing book to infer context
    books_dir = Path(output)
    _dir_type = _peek_dir_type(repo_path) if repo_path.is_dir() else "unknown"
    existing_context = "A document collection" if _dir_type == "document" else "A software project"
    if books_dir.exists():
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in repo_path.name)
        existing_books = list(books_dir.glob(f"{safe_name}_memory_bank.md"))
        if existing_books:
            from datetime import datetime
            last_updated = datetime.fromtimestamp(existing_books[0].stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            console.print(f"[dim]Found existing memory bank (last updated: {last_updated}). Refreshing...[/dim]")

    context = Prompt.ask(
        "  Updated context (or press Enter to keep existing)",
        default=existing_context,
    )

    generator = BookGenerator(config)

    try:
        output_path = generator.generate(
            repo_path=str(repo_path),
            company_context=context,
            output_dir=output,
            on_progress=lambda s: console.print(f"  [cyan]->[/cyan] {s}"),
        )
        console.print(f"\n[green]Updated![/green] Saved to: [cyan]{Path(output_path).resolve()}[/cyan]")
    except Exception as e:
        console.print(f"\n[red]Update failed:[/red] {e}")
        raise click.Abort()


# ─── Command: ask ─────────────────────────────────────────────

@cli.command()
@click.option("--project", "-p", default=None, help="Project name (matches book filename)")
@click.option("--books-dir", default=None, help="Books directory")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def ask(project, books_dir, config):
    """
    Ask a question about a project using its Memory Bank.
    Great for onboarding, debugging, or quick lookups.

    \b
    Examples:
      memoria ask
      memoria ask --project my-project
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    books_path = Path(books_dir)
    if not books_path.exists():
        console.print("[red]No books found.[/red] Run [cyan]memoria analyze[/cyan] first.")
        raise click.Abort()

    # ── Find available books ──
    available = list(books_path.glob("*_memory_bank.md"))
    if not available:
        console.print("[red]No memory banks found.[/red] Run [cyan]memoria analyze[/cyan] first.")
        raise click.Abort()

    # ── Pick a project ──
    if not project or not any(project.lower() in b.name.lower() for b in available):
        if len(available) == 1:
            book_path = available[0]
        else:
            console.print("\n[bold]Available Memory Banks:[/bold]")
            for i, b in enumerate(available):
                from datetime import datetime
                last_updated = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                label = b.stem.replace("_memory_bank", "").replace("_", " ")
                console.print(f"  [{i + 1}] {label}  [dim](updated {last_updated})[/dim]")

            choice = Prompt.ask("\n  Which project?", default="1")
            try:
                book_path = available[int(choice) - 1]
            except (ValueError, IndexError):
                console.print("[red]Invalid choice.[/red]")
                raise click.Abort()
    else:
        book_path = next(b for b in available if project.lower() in b.name.lower())

    # ── Load book ──
    book_content = book_path.read_text(encoding="utf-8")
    from datetime import datetime
    last_updated = datetime.fromtimestamp(book_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    project_label = book_path.stem.replace("_memory_bank", "").replace("_", " ")
    console.print(f"\n[bold cyan]{project_label}[/bold cyan] [dim]— Memory Bank (updated {last_updated})[/dim]\n")

    # ── Q&A loop — system prompt adapts to the type of Memory Bank ──
    model = ModelProvider(config)

    # Detect book type from its content
    is_audio    = "## 1. What This Recording Is About" in book_content
    is_document = "## 1. What This Collection Is About" in book_content or \
                  "## 1. What This Document Is About" in book_content
    is_notebook = "## 1. Purpose & Question" in book_content

    if is_audio:
        system = (
            "You are an expert analyst who has thoroughly reviewed this transcript/recording.\n"
            "You have read the full Memory Bank below and can answer questions about it.\n\n"
            "Rules:\n"
            "- Quote the speaker's actual words when relevant\n"
            "- Reference specific topics, timestamps, or sections from the Memory Bank\n"
            "- If asked about decisions or action items, be precise\n"
            "- If the Memory Bank doesn't cover something, say so clearly — don't guess\n"
            "- Keep answers concise unless the user asks for depth\n\n"
            f"MEMORY BANK:\n{book_content}"
        )
    elif is_document:
        system = (
            "You are a knowledgeable analyst who has thoroughly read this document collection.\n"
            "You have read the full Memory Bank below and can answer questions about it.\n\n"
            "Rules:\n"
            "- Reference specific sections, facts, and data from the Memory Bank\n"
            "- Preserve exact numbers, dates, and names when quoting facts\n"
            "- If asked for a summary, use the TL;DR and Key Topics sections\n"
            "- If the Memory Bank doesn't cover something, say so clearly — don't guess\n"
            "- Keep answers concise unless the user asks for depth\n\n"
            f"MEMORY BANK:\n{book_content}"
        )
    elif is_notebook:
        system = (
            "You are a senior data scientist who has reviewed this notebook and its analysis.\n"
            "You have read the full Memory Bank below and can answer questions about it.\n\n"
            "Rules:\n"
            "- Reference specific findings, methods, and data from the Memory Bank\n"
            "- Be precise about what the analysis found vs what it assumed\n"
            "- If asked how to re-run, give the exact steps from the Memory Bank\n"
            "- If the Memory Bank doesn't cover something, say so clearly — don't guess\n\n"
            f"MEMORY BANK:\n{book_content}"
        )
    else:
        system = (
            "You are a senior engineer who knows this project inside out.\n"
            "You have read the full Memory Bank below and can answer questions about it.\n\n"
            "Rules:\n"
            "- Be specific — reference actual file names, function names, modules from the Memory Bank\n"
            "- If asked how something works, explain the flow step by step\n"
            "- If asked where to start, point to specific files\n"
            "- If the Memory Bank doesn't cover something, say so clearly — don't guess\n"
            "- Keep answers concise unless the user asks for depth\n\n"
            f"MEMORY BANK:\n{book_content}"
        )

    console.print("[dim]Ask anything about this project. Type [bold]exit[/bold] to quit.[/dim]\n")

    while True:
        question = Prompt.ask("[bold cyan]You[/bold cyan]")
        if question.lower() in {"exit", "quit", "q", "bye"}:
            console.print("[dim]Goodbye![/dim]")
            break

        try:
            answer = model.complete(system, question)
            console.print(f"\n[bold green]Memoria[/bold green]")
            console.print(Markdown(answer))
            console.print()
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}\n")


# ─── Command: scan ────────────────────────────────────────────

@cli.command()
@click.argument("folder")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
@click.option("--output", "-o", default=None, help="Output directory for memory banks")
def scan(folder, config, output):
    """
    Scan a folder of projects and pick which ones to analyze.
    Use this when you have multiple projects in one directory.

    \b
    Examples:
      memoria scan C:/Sahil/Projects
      memoria scan ./my-projects
    """
    if not output:
        output = _default_books_dir()
    print_banner()

    # Signals that indicate a real software project
    PROJECT_SIGNALS = {
        "package.json", "requirements.txt", "setup.py", "pyproject.toml",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "Makefile",
        "Dockerfile", ".git", "tsconfig.json", "composer.json", "Gemfile",
        "CMakeLists.txt", "*.sln", "*.csproj",
    }

    # Rich format extensions that qualify a folder as a document collection
    DOCUMENT_SIGNALS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}

    def _is_project(d: Path) -> bool:
        for signal in PROJECT_SIGNALS:
            if signal.startswith("*"):
                if list(d.glob(signal)):
                    return True
            elif (d / signal).exists():
                return True
        return False

    def _is_document_folder(d: Path) -> bool:
        """Folder with rich-format files (PDFs, Word docs, etc.) but no code signals."""
        try:
            # Only check 2 levels deep — avoids crawling huge trees for a quick signal
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in DOCUMENT_SIGNALS:
                    return True
                if f.is_dir():
                    for ff in f.iterdir():
                        if ff.is_file() and ff.suffix.lower() in DOCUMENT_SIGNALS:
                            return True
        except Exception:
            pass
        return False

    folder_path = Path(folder).resolve()
    if not folder_path.exists():
        console.print(f"[red]Folder not found:[/red] {folder_path}")
        raise click.Abort()

    all_dirs = sorted([
        d for d in folder_path.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ])

    if not all_dirs:
        console.print(f"[yellow]No subdirectories found in[/yellow] {folder_path}")
        raise click.Abort()

    # Classify each folder
    projects = [d for d in all_dirs if _is_project(d)]
    doc_folders = [d for d in all_dirs if not _is_project(d) and _is_document_folder(d)]
    skipped = [d for d in all_dirs if not _is_project(d) and not _is_document_folder(d)]

    # Merge both types — all are analyzable
    analyzable = projects + doc_folders

    if not analyzable:
        console.print("[yellow]No recognisable projects or document folders found.[/yellow]")
        console.print("[dim]A project needs: package.json, requirements.txt, .git, etc.[/dim]")
        console.print("[dim]A document folder needs: .pdf, .docx, .pptx, .xlsx files.[/dim]")
        raise click.Abort()

    # Check which already have books
    from datetime import datetime
    books_path = Path(output)

    def _book_status(d: Path):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in d.name)
        book = books_path / f"{safe}_memory_bank.md"
        if book.exists():
            ts = datetime.fromtimestamp(book.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            return f"book updated {ts}"
        return None

    # Display table
    p_count = len(projects)
    d_count = len(doc_folders)
    summary_parts = []
    if p_count:
        summary_parts.append(f"[bold]{p_count} project(s)[/bold]")
    if d_count:
        summary_parts.append(f"[bold]{d_count} document folder(s)[/bold]")
    console.print(f"\n[bold]Found[/bold] {' + '.join(summary_parts)} [bold]in[/bold] [cyan]{folder_path}[/cyan]")
    if skipped:
        console.print(f"[dim]Skipped {len(skipped)} empty/unrecognized folder(s): "
                      f"{', '.join(d.name for d in skipped[:5])}"
                      f"{'...' if len(skipped) > 5 else ''}[/dim]")
    console.print()

    table = Table(border_style="dim", show_header=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Folder", style="bold")
    table.add_column("Type", style="dim")
    table.add_column("Files", justify="right", style="dim")
    table.add_column("Status", style="dim")

    for i, d in enumerate(analyzable):
        try:
            file_count = sum(1 for _ in d.rglob("*") if _.is_file())
        except Exception:
            file_count = 0
        folder_type = "[cyan]code[/cyan]" if d in projects else "[magenta]docs[/magenta]"
        status = _book_status(d) or "[dim]no book yet[/dim]"
        table.add_row(str(i + 1), d.name, folder_type, str(file_count), status)

    console.print(table)

    # Let user pick
    console.print("\n[bold]Which folder do you want to analyze?[/bold]")
    console.print("[dim]Enter a number, comma-separated numbers, or 'all'[/dim]")
    choice = Prompt.ask("\n  Pick", default="1")

    selected = []
    if choice.strip().lower() == "all":
        selected = analyzable
    else:
        try:
            indices = [int(x.strip()) - 1 for x in choice.split(",")]
            selected = [analyzable[i] for i in indices if 0 <= i < len(analyzable)]
        except (ValueError, IndexError):
            console.print("[red]Invalid selection.[/red]")
            raise click.Abort()

    if not selected:
        console.print("[red]No valid projects selected.[/red]")
        raise click.Abort()

    # Analyze each — respecting the "already exists" guard
    generator = BookGenerator(config)

    for project_path in selected:
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_path.name)
        existing_book = books_path / f"{safe_name}_memory_bank.md"

        console.print(f"\n[bold cyan]Project:[/bold cyan] {project_path.name}")

        # Already has a book — ask before overwriting
        if existing_book.exists():
            ts = datetime.fromtimestamp(existing_book.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            console.print(f"  [yellow]Book already exists[/yellow] (last updated: [cyan]{ts}[/cyan])")
            console.print("    [1] Update it    [2] Skip")
            action = Prompt.ask("    Choose", choices=["1", "2"], default="2")
            if action == "2":
                console.print("  [dim]Skipped.[/dim]")
                continue

        is_doc = project_path in doc_folders
        default_ctx = "A document collection" if is_doc else "A software project"
        context = Prompt.ask(
            f"  What is [bold]{project_path.name}[/bold]? (brief description)",
            default=default_ctx,
        )

        try:
            output_path = generator.generate(
                repo_path=str(project_path),
                company_context=context,
                output_dir=output,
                on_progress=lambda s: console.print(f"  [cyan]->[/cyan] {s}"),
            )
            console.print(f"  [green]Done![/green] Saved to: [cyan]{output_path}[/cyan]")
        except Exception as e:
            console.print(f"  [red]Failed:[/red] {e}")
            if Confirm.ask("  Continue with next project?", default=True):
                continue
            else:
                raise click.Abort()

    console.print(f"\n[bold green]All done![/bold green] Run [cyan]memoria list[/cyan] to see your memory banks.")


# ─── Command: split ───────────────────────────────────────────

@cli.command()
@click.option("--project", "-p", default=None, help="Project name to split")
@click.option("--undo", is_flag=True, help="Undo a previously executed split")
@click.option("--books-dir", default=None, help="Books directory")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def split(project, undo, books_dir, config):
    """
    Split one project's Memory Bank into multiple team books.
    Use when a team divides into sub-teams with separate ownership zones.
    Use --undo to reverse a split and restore the original book.

    \b
    Examples:
      memoria split
      memoria split --project codeprism
      memoria split --project codeprism --undo
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    # ── Undo path ──
    if undo:
        if not project:
            project = Prompt.ask("  Which project split to undo?")
        generator = BookGenerator(config)
        try:
            restored = generator.undo_split(
                project_name=project,
                output_dir=books_dir,
                on_progress=lambda s: console.print(f"  [cyan]->[/cyan] {s}"),
            )
            console.print(Panel.fit(
                f"[bold green]Split undone![/bold green]\n\n"
                f"[dim]Original book restored:[/dim] [cyan]{restored}[/cyan]\n"
                f"[dim]Child books archived to books/.archive/[/dim]",
                border_style="green"
            ))
        except (FileNotFoundError, RuntimeError) as e:
            console.print(f"[red]Cannot undo:[/red] {e}")
            raise click.Abort()
        return

    books_path = Path(books_dir)
    available = list(books_path.glob("*_memory_bank.md"))
    if not available:
        console.print("[red]No memory banks found.[/red] Run [cyan]memoria analyze[/cyan] first.")
        raise click.Abort()

    # ── Pick project ──
    if not project or not any(project.lower() in b.name.lower() for b in available):
        if len(available) == 1:
            book_path = available[0]
        else:
            console.print("\n[bold]Which project is splitting?[/bold]")
            for i, b in enumerate(available):
                label = b.stem.replace("_memory_bank", "").replace("_", " ")
                console.print(f"  [{i + 1}] {label}")
            choice = Prompt.ask("\n  Pick", default="1")
            try:
                book_path = available[int(choice) - 1]
            except (ValueError, IndexError):
                console.print("[red]Invalid choice.[/red]")
                raise click.Abort()
    else:
        book_path = next(b for b in available if project.lower() in b.name.lower())

    project_name = book_path.stem.replace("_memory_bank", "")
    book_content = book_path.read_text(encoding="utf-8")

    # ── Get split description ──
    console.print(f"\n[bold]Describe the team split for [cyan]{project_name}[/cyan][/bold]")
    console.print("[dim]Example: Auth and middleware goes to Team B, reporting to Team C, infra to Team D[/dim]")
    split_description = Prompt.ask("\n  How is the team splitting?")

    # ── Propose split ──
    console.print("\n  [cyan]->[/cyan] Analysing memory bank and proposing split boundaries...")
    generator = BookGenerator(config)

    try:
        proposal = generator.propose_split(book_content, split_description)
    except Exception as e:
        console.print(f"[red]Failed to generate proposal:[/red] {e}")
        raise click.Abort()

    console.print()
    console.print(Panel(proposal, title="Proposed Split", border_style="cyan"))

    # ── Confirm or adjust ──
    console.print("\n[bold]Options:[/bold]")
    console.print("  [1] Looks good — generate the books")
    console.print("  [2] Let me adjust the proposal first")
    console.print("  [3] Abort")
    choice = Prompt.ask("\n  Choose", choices=["1", "2", "3"], default="1")

    if choice == "3":
        raise click.Abort()

    confirmed_proposal = proposal
    if choice == "2":
        console.print("[dim]Paste your adjusted proposal below. Type END on a new line when done:[/dim]")
        lines = []
        while True:
            line = Prompt.ask("")
            if line.strip().upper() == "END":
                break
            lines.append(line)
        confirmed_proposal = "\n".join(lines)

    # ── Save plan before executing (enables --undo) ──
    console.print("\n  [cyan]->[/cyan] Saving split plan (run with --undo to reverse this)...")
    generator.save_split_plan(
        project_name=project_name,
        book_content=book_content,
        split_description=split_description,
        confirmed_proposal=confirmed_proposal,
        output_dir=books_dir,
    )

    # ── Execute split ──
    console.print()
    try:
        results = generator.execute_split(
            book_content=book_content,
            project_name=project_name,
            split_description=split_description,
            confirmed_proposal=confirmed_proposal,
            output_dir=books_dir,
            on_progress=lambda s: console.print(f"  [cyan]->[/cyan] {s}"),
        )
    except Exception as e:
        console.print(f"[red]Split failed:[/red] {e}")
        console.print("[dim]Split plan saved — your original book is untouched.[/dim]")
        raise click.Abort()

    console.print()
    console.print(Panel.fit(
        "[bold green]Split complete![/bold green]\n\n" +
        "\n".join(f"  [dim]{role}:[/dim] [cyan]{path}[/cyan]" for role, path in results.items()) +
        "\n\n[dim]To undo: [bold]memoria split --project " + project_name + " --undo[/bold][/dim]",
        border_style="green"
    ))


# ─── Command: merge ───────────────────────────────────────────

@cli.command()
@click.option("--books-dir", default=None, help="Books directory")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def merge(books_dir, config):
    """
    Merge multiple team Memory Banks into one unified book.
    Use when teams consolidate or projects combine.

    \b
    Examples:
      memoria merge
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    books_path = Path(books_dir)
    available = list(books_path.glob("*_memory_bank.md"))

    if len(available) < 2:
        console.print("[red]Need at least 2 memory banks to merge.[/red]")
        console.print("Run [cyan]memoria analyze[/cyan] to create more books first.")
        raise click.Abort()

    # ── Show available books ──
    console.print("\n[bold]Available Memory Banks:[/bold]")
    for i, b in enumerate(available):
        from datetime import datetime
        last_updated = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        label = b.stem.replace("_memory_bank", "").replace("_", " ")
        console.print(f"  [{i + 1}] {label}  [dim](updated {last_updated})[/dim]")

    # ── Pick books to merge ──
    console.print("\n[dim]Enter numbers separated by commas (e.g. 1,2,3)[/dim]")
    selection = Prompt.ask("  Which books to merge?")
    try:
        indices = [int(x.strip()) - 1 for x in selection.split(",")]
        selected = [available[i] for i in indices if 0 <= i < len(available)]
    except (ValueError, IndexError):
        console.print("[red]Invalid selection.[/red]")
        raise click.Abort()

    if len(selected) < 2:
        console.print("[red]Select at least 2 books.[/red]")
        raise click.Abort()

    # ── Get merge details ──
    console.print(f"\n[bold]Merging:[/bold] {', '.join(b.stem.replace('_memory_bank','') for b in selected)}")
    merged_name = Prompt.ask("\n  Name for the merged project")
    merge_context = Prompt.ask("  Why are these teams merging?", default="Teams consolidating")

    # ── Execute merge ──
    console.print()
    books_data = [(b.stem.replace("_memory_bank", ""), b.read_text(encoding="utf-8")) for b in selected]
    generator = BookGenerator(config)

    try:
        output_path = generator.merge_books(
            books=books_data,
            merged_project_name=merged_name,
            merge_context=merge_context,
            output_dir=books_dir,
            on_progress=lambda s: console.print(f"  [cyan]->[/cyan] {s}"),
        )
    except Exception as e:
        console.print(f"[red]Merge failed:[/red] {e}")
        raise click.Abort()

    console.print()
    console.print(Panel.fit(
        f"[bold green]Merge complete![/bold green]\n\n"
        f"[dim]Unified book:[/dim] [cyan]{output_path}[/cyan]\n\n"
        f"[dim]Source books archived. Run [bold]memoria ask[/bold] to query the merged book.[/dim]",
        border_style="green"
    ))


# ─── Command: list ────────────────────────────────────────────

@cli.command(name="list")
@click.option("--books-dir", default=None, help="Books directory")
def list_books(books_dir):
    """List all generated Memory Banks."""
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    books_path = Path(books_dir)
    available = list(books_path.glob("*_memory_bank.md")) if books_path.exists() else []

    if not available:
        console.print("\n[yellow]No memory banks yet.[/yellow] Run [cyan]memoria analyze[/cyan] to create one.")
        return

    from datetime import datetime
    table = Table(title="Memory Banks", border_style="cyan")
    table.add_column("Project", style="bold")
    table.add_column("File")
    table.add_column("Last Updated")
    table.add_column("Size", justify="right")

    for book in sorted(available):
        size_kb = book.stat().st_size // 1024
        last_updated = datetime.fromtimestamp(book.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        project = book.stem.replace("_memory_bank", "").replace("_", " ").title()
        table.add_row(project, book.name, last_updated, f"{size_kb}KB")

    console.print()
    console.print(table)


# ─── Command: watch ───────────────────────────────────────────

@cli.command()
@click.option("--repo", "-r", default=None, help="Repo path to watch")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
@click.option("--output", "-o", default=None, help="Books directory")
def watch(repo, config, output):
    """
    Watch a repo for file changes and auto-draft Memory Bank updates.
    Waits 10 seconds of quiet after a change before drafting — no spam.
    Run `memoria review` to approve or reject each draft.

    \b
    Examples:
      memoria watch
      memoria watch --repo ./my-project
    """
    if not output:
        output = _default_books_dir()
    print_banner()

    if not repo:
        repo = Prompt.ask("  Repo to watch", default=str(Path.cwd()))

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        console.print(f"[red]Path not found:[/red] {repo_path}")
        _suggest_similar(repo_path)
        raise click.Abort()

    from .watcher import start_watcher
    start_watcher(str(repo_path), config, output)


# ─── Command: serve ───────────────────────────────────────────

@cli.command()
@click.option("--port",   default=8000, help="Port to run webhook server on")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
@click.option("--output", "-o", default=None, help="Books directory")
@click.option(
    "--mcp", "mcp_mode", is_flag=True,
    help="Run as a stdio MCP server instead of the webhook server. "
         "Use with Claude Desktop, Cursor, or any MCP-compatible agent.",
)
def serve(port, config, output, mcp_mode):
    """
    Start the webhook server for GitHub PR merge triggers.
    Expose publicly with ngrok so GitHub can reach it.

    Use --mcp to run as a Model Context Protocol (MCP) server instead —
    this lets Claude Desktop, Cursor, Copilot, and n8n access all your
    Memory Banks and SKILL.md files directly.

    \b
    Webhook mode:
      1. Run: memoria serve
      2. Run: ngrok http 8000
      3. Add ngrok URL as GitHub webhook (path: /webhook/github)
      4. Merge a PR — Memoria auto-drafts an update
      5. Run: memoria review to approve

    \b
    MCP mode (add to Claude Desktop config):
      {
        "mcpServers": {
          "memoria": {
            "command": "memoria",
            "args": ["serve", "--mcp"]
          }
        }
      }

    Examples:
      memoria serve
      memoria serve --port 9000
      memoria serve --mcp
    """
    if not output:
        output = _default_books_dir()

    # ── MCP mode: stdio server for Claude Desktop / Cursor / Copilot ──
    if mcp_mode:
        from .mcp_server import run_mcp_server
        # No banner — stdout must stay clean for JSON-RPC messages
        run_mcp_server(books_dir=output, config_path=config)
        return

    print_banner()
    console.print(f"\n[bold]Starting webhook server on port {port}...[/bold]")
    console.print(f"[dim]Webhook endpoint:[/dim] [cyan]http://localhost:{port}/webhook/github[/cyan]")
    console.print(f"[dim]Health check:[/dim] [cyan]http://localhost:{port}/health[/cyan]")
    console.print("\n[dim]Expose publicly with: [bold]ngrok http " + str(port) + "[/bold][/dim]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    import uvicorn
    from .webhooks import app, load_config
    load_config(config, output)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


# ─── Command: ui ──────────────────────────────────────────────

@cli.command()
@click.option("--port",      default=7860,        help="Port to run the UI server on")
@click.option("--books-dir", default=None,        help="Directory containing Memory Banks")
@click.option("--config",    default="config.yaml", help="Path to config.yaml")
@click.option("--no-open",   is_flag=True,        help="Don't open the browser automatically")
def ui(port, books_dir, config, no_open):
    """
    Launch the Memoria web UI — a browser-based chat interface for Memory Banks.
    Opens http://localhost:<port>/ui automatically (pass --no-open to disable).

    \b
    Examples:
      memoria ui
      memoria ui --port 8080
      memoria ui --books-dir ./books --no-open
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    url = f"http://localhost:{port}/ui"
    console.print(f"\n[bold]Starting Memoria UI on port {port}...[/bold]")
    console.print(f"[dim]Open:[/dim] [cyan]{url}[/cyan]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    if not no_open:
        import threading
        import webbrowser
        # Give uvicorn ~1 s to start before opening the browser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    import uvicorn
    from .ui import create_app
    app = create_app(books_dir=books_dir, config_path=config)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


# ─── Command: review ──────────────────────────────────────────

@cli.command()
@click.option("--project", "-p", default=None, help="Project name")
@click.option("--books-dir", default=None, help="Books directory")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def review(project, books_dir, config):
    """
    Review a pending Memory Bank draft and approve or reject it.
    Shows a diff between current book and the draft.

    \b
    Examples:
      memoria review
      memoria review --project codeprism
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    # ── RBAC ──
    try:
        from .rbac import check_permission, Permission, PermissionDenied
        check_permission(Permission.REVIEW, config)
    except PermissionDenied as exc:
        console.print(f"[bold red]Permission denied[/bold red]\n{exc}")
        raise click.Abort()

    books_path = Path(books_dir)

    # Find drafts
    drafts = list(books_path.glob("*_draft.md"))
    if not drafts:
        console.print("\n[yellow]No pending drafts.[/yellow]")
        console.print("[dim]Drafts are created by [bold]memoria watch[/bold] or the webhook server.[/dim]")
        return

    # Pick draft
    if not project or not any(project.lower() in d.name.lower() for d in drafts):
        if len(drafts) == 1:
            draft_path = drafts[0]
        else:
            console.print("\n[bold]Pending drafts:[/bold]")
            for i, d in enumerate(drafts):
                from datetime import datetime
                ts = datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                console.print(f"  [{i + 1}] {d.stem.replace('_draft', '')}  [dim](drafted {ts})[/dim]")
            choice = Prompt.ask("\n  Which draft?", default="1")
            try:
                draft_path = drafts[int(choice) - 1]
            except (ValueError, IndexError):
                console.print("[red]Invalid choice.[/red]")
                raise click.Abort()
    else:
        draft_path = next(d for d in drafts if project.lower() in d.name.lower())

    project_name = draft_path.stem.replace("_draft", "")
    draft_content = draft_path.read_text(encoding="utf-8")

    # Show diff vs current book
    import difflib
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)
    current_path = books_path / f"{safe_name}_memory_bank.md"

    if current_path.exists():
        current_lines = current_path.read_text(encoding="utf-8").splitlines()
        draft_lines = draft_content.splitlines()
        diff = list(difflib.unified_diff(
            current_lines, draft_lines,
            fromfile="current", tofile="draft",
            lineterm="", n=2
        ))

        if diff:
            console.print(f"\n[bold]Changes in draft vs current book:[/bold] [dim]({len(diff)} lines changed)[/dim]\n")
            for line in diff[:60]:  # Show first 60 lines of diff
                if line.startswith("+") and not line.startswith("+++"):
                    console.print(f"[green]{line}[/green]")
                elif line.startswith("-") and not line.startswith("---"):
                    console.print(f"[red]{line}[/red]")
                else:
                    console.print(f"[dim]{line}[/dim]")
            if len(diff) > 60:
                console.print(f"\n[dim]... {len(diff) - 60} more lines. Full draft at: {draft_path}[/dim]")
        else:
            console.print("\n[yellow]No changes detected between draft and current book.[/yellow]")
    else:
        console.print(f"\n[dim]No existing book found — this will create a new one.[/dim]")
        console.print(f"\n[bold]Draft preview (first 30 lines):[/bold]")
        for line in draft_content.splitlines()[:30]:
            console.print(line)

    # Decision
    console.print()
    console.print("  [1] Approve — apply draft as new live book")
    console.print("  [2] Reject  — discard draft, keep current book")
    choice = Prompt.ask("\n  Decision", choices=["1", "2"], default="1")

    from .generator import BookGenerator
    generator = BookGenerator(config)

    if choice == "1":
        live_path = generator.apply_draft(project_name, books_dir)
        # Re-index in vector search after approval
        try:
            from .search import index_book
            content = Path(live_path).read_text(encoding="utf-8")
            index_book(project_name, content, books_dir)
        except Exception:
            pass
        console.print(f"\n[bold green]Approved![/bold green] Live book updated: [cyan]{live_path}[/cyan]")
    else:
        generator.reject_draft(project_name, books_dir)
        console.print("\n[yellow]Draft rejected.[/yellow] Current book unchanged.")


# ─── Command: search ──────────────────────────────────────────

@cli.command()
@click.argument("query")
@click.option("--books-dir", default=None, help="Books directory")
@click.option("--top", default=5, help="Number of results to show")
@click.option("--fulltext", is_flag=True, default=False, help="Exact keyword search instead of semantic")
def search(query, books_dir, top, fulltext):
    """
    Search across all Memory Banks.

    Default: semantic search — finds relevant sections even without exact words.
    With --fulltext: exact case-insensitive substring match, like Ctrl+F across
    every Memory Bank.

    \b
    Examples:
      memoria search "how does authentication work"
      memoria search "database connection" --top 3
      memoria search "FastAPI" --fulltext
      memoria search "def handle_submit" --fulltext --top 20
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    from pathlib import Path

    books_path = Path(books_dir)
    if not books_path.exists() or not list(books_path.glob("*_memory_bank.md")):
        console.print("[red]No memory banks found.[/red] Run [cyan]memoria analyze[/cyan] first.")
        raise click.Abort()

    # ── Full-text (exact) path ────────────────────────────────────────────────
    if fulltext:
        needle = query.lower()
        hits: list[dict] = []

        for book_file in sorted(books_path.glob("*_memory_bank.md")):
            project = book_file.stem.replace("_memory_bank", "")
            try:
                lines = book_file.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue

            current_heading = ""
            heading_at: list[str] = [""] * len(lines)
            for idx, ln in enumerate(lines):
                if ln.startswith("#"):
                    current_heading = ln.lstrip("#").strip()
                heading_at[idx] = current_heading

            for idx, ln in enumerate(lines):
                if needle not in ln.lower():
                    continue
                ctx_start = max(0, idx - 1)
                ctx_end   = min(len(lines), idx + 2)
                hits.append({
                    "project": project,
                    "section": heading_at[idx],
                    "line":    idx + 1,
                    "excerpt": "\n".join(lines[ctx_start:ctx_end]),
                })

        if not hits:
            console.print(f"\n[yellow]No exact match for:[/yellow] {query}")
            console.print("[dim]Try semantic search (without --fulltext) for conceptual matches.[/dim]")
            return

        shown = hits[:top]
        total = len(hits)
        console.print(f"\n[bold]Full-text results for:[/bold] [cyan]{query}[/cyan]  "
                      f"[dim]({total} match{'es' if total != 1 else ''})[/dim]\n")

        for i, hit in enumerate(shown):
            console.print(
                f"[bold]{i + 1}.[/bold]  "
                f"[bold cyan]{hit['project']}[/bold cyan] [dim]— {hit['section']} · line {hit['line']}[/dim]"
            )
            for ln in hit["excerpt"].splitlines():
                # Bold the matching term
                import re as _re
                highlighted = _re.sub(
                    f"({_re.escape(query)})",
                    r"[bold yellow]\1[/bold yellow]",
                    ln,
                    flags=_re.IGNORECASE,
                )
                console.print(f"   [dim]{highlighted[:120]}[/dim]")
            console.print()

        if total > top:
            console.print(f"[dim]… and {total - top} more. Use --top {total} to see all.[/dim]")
        return

    # ── Semantic path ─────────────────────────────────────────────────────────
    from .search import search as do_search, index_all_books, get_provider_info

    # Show active provider
    info = get_provider_info(books_dir)
    provider = info.get("provider", "chromadb")
    if provider != "chromadb":
        console.print(f"[dim]Vector store: {provider}[/dim]")

    # Auto-index if DB doesn't exist yet (ChromaDB only)
    if provider == "chromadb":
        db_path = books_path / ".chromadb"
        if not db_path.exists():
            console.print("[dim]Building search index for the first time...[/dim]")
            count = index_all_books(books_dir)
            console.print(f"[dim]Indexed {count} book(s).[/dim]\n")

    results = do_search(query, books_dir, top_k=top)

    if not results:
        console.print(f"\n[yellow]No results for:[/yellow] {query}")
        console.print("[dim]Try [bold]--fulltext[/bold] for exact matches, or [bold]memoria search --reindex[/bold] if you've added new books recently.[/dim]")
        return

    console.print(f"\n[bold]Results for:[/bold] [cyan]{query}[/cyan]\n")

    for i, hit in enumerate(results):
        score_color = "green" if hit["score"] > 0.7 else "yellow" if hit["score"] > 0.4 else "red"
        console.print(
            f"[bold]{i + 1}.[/bold] [{score_color}]{hit['score']:.0%} match[/{score_color}]  "
            f"[bold cyan]{hit['project']}[/bold cyan] [dim]— {hit['section']}[/dim]"
        )
        excerpt = hit["text"].replace(f"[{hit['project']}] {hit['section']}", "").strip()
        for line in excerpt.splitlines()[:4]:
            if line.strip():
                console.print(f"   [dim]{line.strip()[:100]}[/dim]")
        console.print()


# ─── Command group: graph ─────────────────────────────────────

@cli.group()
def graph():
    """
    Knowledge Graph — cross-project relationship tracking.
    Build a persistent map of how your projects relate to each other.

    \b
    Commands:
      memoria graph build               analyse all books, build the graph
      memoria graph show                print the full graph as a table
      memoria graph query <project>     show connections for one project
      memoria graph impact <project>    show what depends on this project
    """
    pass


def _resolve_project(project: str, nodes: dict) -> str:
    """
    Resolve a user-supplied project name to an actual graph node key.

    Resolution order:
      1. Exact match
      2. Case-insensitive exact match
      3. Substring match (input is contained in a node name)
      4. difflib close-match (similarity ≥ 0.5)
      5. Interactive numbered picker — shown when nothing else matches
    """
    import difflib

    if project in nodes:
        return project

    # 2. Case-insensitive exact
    lower_map = {k.lower(): k for k in nodes}
    if project.lower() in lower_map:
        matched = lower_map[project.lower()]
        console.print(f"[dim]Matched: {matched}[/dim]")
        return matched

    # 3. Substring
    substr = [k for k in nodes if project.lower() in k.lower()]
    if len(substr) == 1:
        console.print(f"[dim]Matched: {substr[0]}[/dim]")
        return substr[0]
    if len(substr) > 1:
        # Multiple substring matches — fall through to picker with these pre-filtered
        candidates = substr
    else:
        # 4. difflib similarity
        candidates = difflib.get_close_matches(project, nodes.keys(), n=5, cutoff=0.4)

    # If difflib found suggestions, show those first; otherwise show everything
    all_projects = sorted(nodes.keys())
    picker_list  = candidates if candidates else all_projects

    # 5. Interactive picker — always shows all projects so user is never stuck
    console.print(f"\n[yellow]No exact match for '[bold]{project}[/bold]'.[/yellow]")
    if candidates:
        console.print(f"[dim]Closest matches ({len(candidates)} found):[/dim]\n")
    else:
        console.print("[dim]Available projects:[/dim]\n")

    for i, name in enumerate(picker_list, 1):
        desc = nodes[name].get("description", "") or ""
        console.print(f"  [cyan]{i}[/cyan]  {name}  [dim]{desc[:60]}[/dim]")

    # Also offer "show all" if we only showed partial results
    if candidates and len(candidates) < len(all_projects):
        console.print(f"  [dim]a[/dim]  [dim]Show all {len(all_projects)} projects[/dim]")
        console.print()
        choice = console.input("[bold]Enter number (or 'a' for all, Enter to abort):[/bold] ").strip()
        if choice.lower() == 'a':
            console.print()
            for i, name in enumerate(all_projects, 1):
                desc = nodes[name].get("description", "") or ""
                console.print(f"  [cyan]{i}[/cyan]  {name}  [dim]{desc[:60]}[/dim]")
            console.print()
            choice = console.input("[bold]Enter number (or press Enter to abort):[/bold] ").strip()
            picker_list = all_projects
    else:
        console.print()
        choice = console.input("[bold]Enter number (or press Enter to abort):[/bold] ").strip()

    if not choice.isdigit() or not (1 <= int(choice) <= len(picker_list)):
        raise click.Abort()
    return picker_list[int(choice) - 1]


@graph.command("build")
@click.option("--books-dir", default=None, help="Books directory")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
@click.option("--projects", "-p", default=None, help="Only rebuild specific projects (comma-separated)")
@click.option("--exclude", "-x", default=None, help="Skip these projects (comma-separated)")
@click.option("--incremental", "-i", is_flag=True, help="Only re-analyse books that changed since last build")
@click.option("--watch", "-w", is_flag=True, help="Continuously watch for Memory Bank changes and rebuild incrementally")
@click.option("--debounce", default=15, show_default=True, help="Seconds to wait after last change before rebuilding (--watch only)")
def graph_build(books_dir, config, projects, exclude, incremental, watch, debounce):
    """
    Analyse Memory Banks and build the Knowledge Graph.
    Saves the result to books/.graph.json.

    \b
    Examples:
      memoria graph build
      memoria graph build --projects auth-service,api-gateway
      memoria graph build --exclude legacy-app
      memoria graph build --incremental
      memoria graph build --watch
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    # ── RBAC ──
    try:
        from .rbac import check_permission, Permission, PermissionDenied
        check_permission(Permission.GRAPH, config)
    except PermissionDenied as exc:
        console.print(f"[bold red]Permission denied[/bold red]\n{exc}")
        raise click.Abort()

    books_path = Path(books_dir)
    if not books_path.exists() or not list(books_path.glob("*_memory_bank.md")):
        console.print("[red]No memory banks found.[/red] Run [cyan]memoria analyze[/cyan] first.")
        raise click.Abort()

    # ── Watch mode ────────────────────────────────────────────────────────────
    if watch:
        from .graph import build_graph as _build, watch_and_rebuild
        import logging
        logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

        # Initial full build first
        console.print(f"\n[bold]Building Knowledge Graph (initial full build)…[/bold]")
        try:
            g = _build(
                books_dir=books_dir, config_path=config,
                on_progress=lambda s: console.print(f"  [cyan]→[/cyan] {s}"),
            )
            n, e = len(g.get("nodes", {})), len(g.get("edges", []))
            console.print(f"  [green]✓[/green] {n} project(s), {e} relationship(s)\n")
        except Exception as exc:
            console.print(f"[yellow]Initial build failed:[/yellow] {exc}\n")

        def _on_rebuilt(project_name: str, updated_graph: dict) -> None:
            n = len(updated_graph.get("nodes", {}))
            e = len(updated_graph.get("edges", []))
            console.print(
                f"  [green]✓[/green] [{project_name}] rebuilt — "
                f"{n} node(s), {e} edge(s)"
            )
            # Fire impact notifications for downstream owners
            try:
                from .impact_notifier import on_commit
                on_commit(repo_path=books_dir, commit_msg=f"Graph rebuild: {project_name}")
            except Exception:
                pass

        console.print(
            Panel.fit(
                f"[bold green]Graph watcher running[/bold green]\n"
                f"[dim]Watching: {books_dir}[/dim]\n"
                f"[dim]Debounce: {debounce}s  ·  Press Ctrl+C to stop[/dim]",
                border_style="green",
            )
        )
        watch_and_rebuild(
            books_dir=books_dir,
            config_path=config,
            on_rebuilt=_on_rebuilt,
            debounce_s=float(debounce),
        )
        return

    # Parse comma-separated lists
    project_list = [p.strip() for p in projects.split(",")] if projects else None
    exclude_list = [e.strip() for e in exclude.split(",")] if exclude else None

    # Save build config to config.yaml graph: block if flags were passed
    if project_list or exclude_list or incremental:
        _save_graph_config(config, project_list, exclude_list, incremental)

    from .graph import build_graph

    mode = "incremental" if incremental else "full"
    scope = f"projects: {', '.join(project_list)}" if project_list else "all projects"
    if exclude_list:
        scope += f" (excluding: {', '.join(exclude_list)})"

    console.print(f"\n[bold]Building Knowledge Graph ({mode})…[/bold]")
    console.print(f"[dim]Scope: {scope}[/dim]\n")

    try:
        g = build_graph(
            books_dir=books_dir,
            config_path=config,
            on_progress=lambda s: console.print(f"  [cyan]→[/cyan] {s}"),
            projects=project_list,
            exclude=exclude_list,
            incremental=incremental,
        )
        n = len(g.get("nodes", {}))
        e = len(g.get("edges", []))
        console.print(
            Panel.fit(
                f"[green]Graph built![/green]  "
                f"[bold]{n}[/bold] project(s)  ·  [bold]{e}[/bold] relationship(s)\n"
                f"[dim]Saved to: {Path(books_dir) / '.graph.json'}[/dim]",
                border_style="green",
            )
        )
    except Exception as exc:
        console.print(f"\n[red]Graph build failed:[/red] {exc}")
        raise click.Abort()


def _save_graph_config(config_path: str, projects, exclude, incremental):
    """Persist graph build options to config.yaml graph: block."""
    import yaml
    cfg_path = Path(config_path)
    if not cfg_path.exists():
        cfg_path = Path.home() / ".memoria" / "config.yaml"
    if not cfg_path.exists():
        return

    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8-sig")) or {}
        graph_cfg = cfg.get("graph", {})

        if exclude:
            graph_cfg["exclude_projects"] = exclude
        if incremental:
            graph_cfg["incremental"] = True

        if graph_cfg:
            cfg["graph"] = graph_cfg
            cfg_path.write_text(
                yaml.dump(cfg, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
    except Exception:
        pass  # best-effort


@graph.command("show")
@click.option("--books-dir", default=None, help="Books directory")
@click.option("--min-confidence", default=0.0, type=float,
              help="Only show edges with confidence ≥ this value (0.0–1.0)")
def graph_show(books_dir, min_confidence):
    """
    Print the full Knowledge Graph as a table.

    \b
    Examples:
      memoria graph show
      memoria graph show --min-confidence 0.7
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    from .graph import get_graph, graph_summary

    g = get_graph(books_dir)
    if not g["nodes"]:
        console.print("[yellow]No graph found.[/yellow] Run [cyan]memoria graph build[/cyan] first.")
        return

    console.print(f"\n[bold]Knowledge Graph[/bold]  [dim]{graph_summary(g)}[/dim]\n")

    # ── Nodes table ────────────────────────────────────────────
    node_table = Table(title="Projects", border_style="cyan", show_lines=False)
    node_table.add_column("Project",     style="bold cyan", no_wrap=True)
    node_table.add_column("Description", style="white")
    node_table.add_column("Technologies", style="dim", max_width=45)
    node_table.add_column("Topics",       style="dim", max_width=35)

    for name, node in sorted(g["nodes"].items()):
        node_table.add_row(
            name,
            (node.get("description") or "—")[:80],
            ", ".join(node.get("technologies", [])[:6]) or "—",
            ", ".join(node.get("topics", [])[:5]) or "—",
        )

    console.print(node_table)

    # ── Edges table ────────────────────────────────────────────
    edges = [e for e in g.get("edges", []) if e.get("confidence", 0) >= min_confidence]

    if not edges:
        console.print("\n[dim]No relationships detected above the confidence threshold.[/dim]")
        return

    edge_table = Table(title="\nRelationships", border_style="cyan", show_lines=False)
    edge_table.add_column("From",       style="bold cyan",  no_wrap=True)
    edge_table.add_column("→ To",       style="bold green", no_wrap=True)
    edge_table.add_column("Type",       style="yellow",     no_wrap=True)
    edge_table.add_column("Confidence", style="white",      no_wrap=True)
    edge_table.add_column("Reason",     style="dim")

    _REL_LABELS = {
        "depends_on":        "depends on",
        "shares_technology": "shares tech",
        "same_domain":       "same domain",
        "references":        "references",
    }

    for edge in sorted(edges, key=lambda x: (-x.get("confidence", 0), x["source"])):
        conf  = edge.get("confidence", 0)
        color = "green" if conf >= 0.8 else "yellow" if conf >= 0.5 else "red"
        edge_table.add_row(
            edge["source"],
            edge["target"],
            _REL_LABELS.get(edge["relation"], edge["relation"]),
            f"[{color}]{conf:.0%}[/{color}]",
            (edge.get("reason") or "")[:70],
        )

    console.print(edge_table)


@graph.command("query")
@click.argument("project")
@click.option("--books-dir", default=None, help="Books directory")
def graph_query(project, books_dir):
    """
    Show all known relationships for PROJECT.

    \b
    Examples:
      memoria graph query my-api
      memoria graph query auth-service
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    from .graph import get_graph, query_project

    g = get_graph(books_dir)
    if not g["nodes"]:
        console.print("[yellow]No graph found.[/yellow] Run [cyan]memoria graph build[/cyan] first.")
        return

    nodes = g["nodes"]
    project = _resolve_project(project, nodes)

    node = nodes[project]
    console.print(f"\n[bold cyan]{project}[/bold cyan]  [dim]{node.get('description', '')}[/dim]\n")

    # Project details
    if node.get("technologies"):
        console.print(f"  [bold]Technologies:[/bold] {', '.join(node['technologies'][:8])}")
    if node.get("topics"):
        console.print(f"  [bold]Domains:[/bold]      {', '.join(node['topics'][:6])}")
    if node.get("exposes"):
        console.print(f"  [bold]Exposes:[/bold]      {', '.join(node['exposes'][:4])}")
    if node.get("consumes"):
        console.print(f"  [bold]Consumes:[/bold]     {', '.join(node['consumes'][:4])}")
    console.print()

    results = query_project(project, g)

    if not results:
        console.print("[dim]No relationships found for this project.[/dim]")
        console.print("[dim]Tip: run [bold]memoria graph build[/bold] to refresh.[/dim]")
        return

    _REL_LABELS = {
        "depends_on":        "depends on",
        "shares_technology": "shares tech with",
        "same_domain":       "same domain as",
        "references":        "references",
    }
    _REL_ARROWS = {
        "depends_on":        ("→", "red"),
        "shares_technology": ("↔", "blue"),
        "same_domain":       ("≈", "yellow"),
        "references":        ("↗", "cyan"),
    }

    table = Table(border_style="cyan", show_lines=False)
    table.add_column("Direction",    style="dim",        no_wrap=True)
    table.add_column("Other Project",style="bold cyan",  no_wrap=True)
    table.add_column("Relationship", style="yellow",     no_wrap=True)
    table.add_column("Confidence",   style="white",      no_wrap=True)
    table.add_column("Reason",       style="dim")

    for r in results:
        direction_label = "→ outbound" if r["direction"] == "outbound" else "← inbound"
        conf  = r.get("confidence", 0)
        color = "green" if conf >= 0.8 else "yellow" if conf >= 0.5 else "red"
        rel_label = _REL_LABELS.get(r["relation"], r["relation"])
        table.add_row(
            direction_label,
            r["other"],
            rel_label,
            f"[{color}]{conf:.0%}[/{color}]",
            (r.get("reason") or "")[:65],
        )

    console.print(table)


@graph.command("impact")
@click.argument("project")
@click.option("--books-dir", default=None, help="Books directory")
def graph_impact(project, books_dir):
    """
    Show what would be affected if PROJECT changes.
    Traces all direct and transitive dependents.

    \b
    Examples:
      memoria graph impact auth-service
      memoria graph impact shared-utils
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    from .graph import get_graph, impact_analysis

    g = get_graph(books_dir)
    if not g["nodes"]:
        console.print("[yellow]No graph found.[/yellow] Run [cyan]memoria graph build[/cyan] first.")
        return

    nodes = g["nodes"]
    project = _resolve_project(project, nodes)

    node = nodes[project]
    console.print(f"\n[bold]Impact analysis for:[/bold] [bold cyan]{project}[/bold cyan]")
    console.print(f"[dim]{node.get('description', '')}[/dim]\n")

    dependents = impact_analysis(project, g)

    if not dependents:
        console.print("[green]No known dependents.[/green]")
        console.print(f"[dim]No other projects in the graph depend on {project}.[/dim]")
        return

    console.print(
        f"[bold yellow]⚠  {len(dependents)} project(s) may be affected by changes to {project}[/bold yellow]\n"
    )

    table = Table(border_style="yellow", show_lines=False)
    table.add_column("Depth",   style="dim",       no_wrap=True)
    table.add_column("Project", style="bold cyan",  no_wrap=True)
    table.add_column("Dependency path", style="dim")

    for dep in dependents:
        depth_label = "direct" if dep["depth"] == 1 else f"depth {dep['depth']}"
        table.add_row(depth_label, dep["project"], dep["path"])

    console.print(table)


# ─── Command group: access ────────────────────────────────────

@cli.group()
def access():
    """
    Role-Based Access Control — manage who can do what in Memoria.

    \b
    Commands:
      memoria access whoami               show your current user, role, and permissions
      memoria access list                 show all users and their roles
      memoria access grant <user> <role>  give a user a role
      memoria access revoke <user>        remove a user's role assignment
      memoria access check <permission>   test if you have a permission
      memoria access init                 create a policy.yaml with sensible defaults
    """
    pass


@access.command("whoami")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def access_whoami(config):
    """
    Show the current user, their role, and what they can do.

    \b
    Examples:
      memoria access whoami
    """
    from .rbac import (
        get_current_user, load_policy, get_user_role, get_user_permissions,
        is_rbac_enabled, ROLE_PERMISSIONS, Permission,
    )

    user   = get_current_user(config)
    policy = load_policy(config)

    if not is_rbac_enabled(config):
        console.print(f"\n[bold cyan]{user}[/bold cyan]  [dim](RBAC disabled — full access)[/dim]")
        console.print(
            "[dim]To enable RBAC, run [bold]memoria access init[/bold] "
            "or create ~/.memoria/policy.yaml[/dim]"
        )
        return

    role  = get_user_role(user, policy)
    perms = get_user_permissions(user, policy)

    console.print(f"\n[bold cyan]{user}[/bold cyan]  role: [bold yellow]{role.value}[/bold yellow]\n")

    table = Table(show_header=True, border_style="cyan")
    table.add_column("Permission", style="bold")
    table.add_column("Granted", style="white", no_wrap=True)
    table.add_column("What it unlocks", style="dim")

    _DESCRIPTIONS = {
        Permission.READ:   "memoria ask, search, list, graph show/query/impact",
        Permission.WRITE:  "memoria analyze, update, split, merge",
        Permission.REVIEW: "memoria review (approve / reject drafts)",
        Permission.PULL:   "memoria pull, schedule run/start",
        Permission.GRAPH:  "memoria graph build",
        Permission.ADMIN:  "memoria access grant/revoke, init",
    }

    for p in Permission:
        granted = p in perms
        icon    = "[green]✓[/green]" if granted else "[red]✗[/red]"
        table.add_row(p.value, icon, _DESCRIPTIONS.get(p, ""))

    console.print(table)


@access.command("list")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def access_list(config):
    """
    Show all users and their assigned roles.

    \b
    Examples:
      memoria access list
    """
    from .rbac import load_policy, list_all_roles, is_rbac_enabled

    if not is_rbac_enabled(config):
        console.print("\n[yellow]RBAC is not enabled.[/yellow]")
        console.print("[dim]Run [bold]memoria access init[/bold] to set up access control.[/dim]")
        return

    policy = load_policy(config)
    rows   = list_all_roles(policy)
    default_role = policy.get("default_role", "contributor")

    console.print(
        f"\n[bold]RBAC policy[/bold]  "
        f"[dim]default role for unlisted users: [bold]{default_role}[/bold][/dim]\n"
    )

    if not rows:
        console.print("[dim]No users explicitly assigned. Everyone gets the default role.[/dim]")
        return

    _ROLE_COLORS = {
        "viewer":      "dim",
        "contributor": "white",
        "reviewer":    "cyan",
        "admin":       "bold yellow",
    }

    table = Table(border_style="cyan", show_lines=False)
    table.add_column("User",   style="bold cyan",  no_wrap=True)
    table.add_column("Role",   no_wrap=True)
    table.add_column("Source", style="dim",        no_wrap=True)

    for row in rows:
        color = _ROLE_COLORS.get(row["role"], "white")
        table.add_row(row["username"], f"[{color}]{row['role']}[/{color}]", row["source"])

    console.print(table)


@access.command("grant")
@click.argument("user")
@click.argument("role", type=click.Choice(["viewer", "contributor", "reviewer", "admin"],
                                          case_sensitive=False))
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def access_grant(user, role, config):
    """
    Grant USER the specified ROLE.
    Creates the policy file if it doesn't exist yet.

    \b
    Examples:
      memoria access grant bob reviewer
      memoria access grant alice admin
      memoria access grant intern viewer
    """
    from .rbac import (
        check_permission, Permission, PermissionDenied,
        grant_role, Role, _policy_path,
    )

    # Only admins can grant roles
    try:
        check_permission(Permission.ADMIN, config)
    except PermissionDenied as exc:
        console.print(f"[bold red]Permission denied[/bold red]\n{exc}")
        raise click.Abort()

    grant_role(user, Role(role.lower()), config)
    path = _policy_path(config)
    console.print(
        f"\n[green]✓[/green]  [bold cyan]{user}[/bold cyan] → role [bold yellow]{role}[/bold yellow]"
        f"  [dim](saved to {path})[/dim]"
    )


@access.command("revoke")
@click.argument("user")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def access_revoke(user, config):
    """
    Remove USER's explicit role assignment.
    They will fall back to the policy's default_role.

    \b
    Examples:
      memoria access revoke bob
    """
    from .rbac import (
        check_permission, Permission, PermissionDenied,
        revoke_role,
    )

    try:
        check_permission(Permission.ADMIN, config)
    except PermissionDenied as exc:
        console.print(f"[bold red]Permission denied[/bold red]\n{exc}")
        raise click.Abort()

    removed = revoke_role(user, config)
    if removed:
        console.print(
            f"\n[green]✓[/green]  [bold cyan]{user}[/bold cyan] removed from policy. "
            f"They now use the default role."
        )
    else:
        console.print(f"\n[yellow]{user}[/yellow] was not found in the policy.")


@access.command("check")
@click.argument("permission", type=click.Choice(
    ["read", "write", "review", "pull", "graph", "admin"], case_sensitive=False
))
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def access_check(permission, config):
    """
    Test whether the current user has PERMISSION.
    Exits 0 if allowed, 1 if denied.

    \b
    Examples:
      memoria access check write
      memoria access check review
      memoria access check admin
    """
    from .rbac import has_permission, Permission, get_current_user

    p    = Permission(permission.lower())
    user = get_current_user(config)
    ok   = has_permission(p, config)

    if ok:
        console.print(f"\n[green]✓[/green]  [bold cyan]{user}[/bold cyan] has [bold]{permission}[/bold] permission.")
    else:
        console.print(f"\n[red]✗[/red]  [bold cyan]{user}[/bold cyan] does [bold]not[/bold] have [bold]{permission}[/bold] permission.")
        raise SystemExit(1)


@access.command("init")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
@click.option("--default-role",
              type=click.Choice(["viewer", "contributor", "reviewer", "admin"], case_sensitive=False),
              default="contributor",
              help="Role assigned to users not explicitly listed")
def access_init(config, default_role):
    """
    Create ~/.memoria/policy.yaml with sensible defaults.
    The current OS user is set as admin. All other users get default_role.

    Safe to run on a fresh setup. Does NOT overwrite an existing policy.

    \b
    Examples:
      memoria access init
      memoria access init --default-role viewer
    """
    from .rbac import init_policy, get_current_user, _policy_path

    path = _policy_path(config)
    if path.exists():
        console.print(f"\n[yellow]Policy already exists:[/yellow] {path}")
        console.print("[dim]Edit it directly, or use [bold]memoria access grant/revoke[/bold].[/dim]")
        return

    created = init_policy(default_role=default_role.lower(), config_path=config)
    user    = get_current_user(config)

    console.print(
        Panel.fit(
            f"[green]RBAC policy created![/green]\n\n"
            f"  Admin:        [bold cyan]{user}[/bold cyan]\n"
            f"  Default role: [bold yellow]{default_role}[/bold yellow]\n"
            f"  Policy file:  [dim]{created}[/dim]\n\n"
            f"[dim]Add team members with [bold]memoria access grant <user> <role>[/bold][/dim]",
            border_style="green",
        )
    )


# ─── Access sub-commands: scopes ─────────────────────────────

@access.command("scope-set")
@click.argument("scope_name")
@click.option("--projects", "-p", required=True,
              help="Comma-separated project names, or '*' for all.")
@click.option("--description", "-d", default="", help="Human-readable description.")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def access_scope_set(scope_name, projects, description, config):
    """
    Create or update a scope (named project filter).

    \b
    Examples:
      memoria access scope-set backend --projects auth-service,api-gateway
      memoria access scope-set org     --projects '*' --description "All projects"
    """
    from .rbac import set_scope, check_permission, Permission, PermissionDenied
    try:
        check_permission(Permission.ADMIN, config)
    except PermissionDenied as exc:
        console.print(f"[red]Permission denied:[/red] {exc}")
        raise click.Abort()

    proj_list = ["*"] if projects.strip() == "*" else [p.strip() for p in projects.split(",")]
    set_scope(scope_name, proj_list, description=description, config_path=config)
    console.print(
        f"[green]✓[/green] Scope [bold]{scope_name}[/bold] → "
        f"{'all projects' if proj_list == ['*'] else ', '.join(proj_list)}"
    )


@access.command("scope-assign")
@click.argument("username")
@click.argument("scopes", nargs=-1, required=True)
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def access_scope_assign(username, scopes, config):
    """
    Assign one or more scopes to a user.

    \b
    Examples:
      memoria access scope-assign bob backend
      memoria access scope-assign alice org
      memoria access scope-assign charlie backend payments
    """
    from .rbac import assign_scope, check_permission, Permission, PermissionDenied
    try:
        check_permission(Permission.ADMIN, config)
    except PermissionDenied as exc:
        console.print(f"[red]Permission denied:[/red] {exc}")
        raise click.Abort()

    assign_scope(username, list(scopes), config_path=config)
    console.print(
        f"[green]✓[/green] [bold]{username}[/bold] → scope(s): {', '.join(scopes)}"
    )


@access.command("scope-list")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def access_scope_list(config):
    """List all defined scopes and their members."""
    from .rbac import load_policy, list_scopes
    policy = load_policy(config)
    if not policy:
        console.print("[dim]No policy file found. Run [cyan]memoria access init[/cyan].[/dim]")
        return

    scopes = list_scopes(policy)
    if not scopes:
        console.print(
            "[dim]No scopes defined.[/dim]\n"
            "[dim]Create one with [cyan]memoria access scope-set <name> --projects a,b[/cyan][/dim]"
        )
        return

    table = Table(title="Access-Controlled Scopes", show_header=True, header_style="bold cyan")
    table.add_column("Scope",       style="bold white")
    table.add_column("Projects",    style="cyan",   max_width=50)
    table.add_column("Members",     style="yellow")
    table.add_column("Description", style="dim")

    for s in scopes:
        projects = s["projects"]
        if isinstance(projects, list):
            proj_str = ", ".join(projects[:5]) + (" …" if len(projects) > 5 else "")
        else:
            proj_str = str(projects)
        table.add_row(
            s["scope"],
            proj_str,
            ", ".join(s["members"]) or "—",
            s.get("description") or "",
        )
    console.print(table)


# ─── Command group: agent ─────────────────────────────────────

@cli.group()
def agent():
    """
    Project-specific AI agents with deep, always-current context.

    Each agent is backed by a Memory Bank so it never uses a stale system
    prompt — context is extracted fresh from the latest book on every session.

    \b
    Commands:
      list      show available agents for a project
      run       start an interactive session with a named agent
      install   add a built-in agent template to your project
      context   print the agent context that would be injected (debug)
    """
    pass


@agent.command("list")
@click.option("--project", "-p", default=None, help="Project name")
@click.option("--books-dir", default=None, help="Books directory")
def agent_list(project, books_dir):
    """
    Show all available agents for a project.
    Includes built-in library agents and any custom agents.yaml definitions.

    \b
    Examples:
      memoria agent list --project my-project
      memoria agent list
    """
    if not books_dir:
        books_dir = _default_books_dir()
    print_banner()

    from .chat_agents import load_agents, AGENT_LIBRARY

    if not project:
        # Show all projects with Memory Banks
        books_path = Path(books_dir)
        books = list(books_path.glob("*_memory_bank.md"))
        if not books:
            console.print("[yellow]No Memory Banks found.[/yellow] Run [cyan]memoria analyze[/cyan] first.")
            return
        project = books[0].stem.replace("_memory_bank", "")
        if len(books) > 1:
            console.print(f"\n[dim]Showing agents for: [bold]{project}[/bold] (use --project to specify)[/dim]\n")

    agents = load_agents(project, books_dir)

    table = Table(title=f"Agents — {project}", border_style="cyan", show_lines=False)
    table.add_column("Name",    style="bold cyan", no_wrap=True)
    table.add_column("Source",  style="dim", no_wrap=True)
    table.add_column("Focus",   style="dim")
    table.add_column("Greeting / Description", style="white", max_width=60)

    for name, defn in sorted(agents.items()):
        source = "built-in" if name in AGENT_LIBRARY else "custom"
        focus = ", ".join(defn.get("focus", [])[:4]) or "—"
        greeting = defn.get("greeting", defn.get("persona", ""))[:80]
        table.add_row(name, source, focus, greeting)

    console.print()
    console.print(table)
    console.print()
    console.print(
        f"[dim]Run an agent: [bold]memoria agent run <name> --project {project}[/bold][/dim]\n"
        f"[dim]Install a template: [bold]memoria agent install code-reviewer --project {project}[/bold][/dim]"
    )


@agent.command("run")
@click.argument("agent_name")
@click.option("--project", "-p", default=None, help="Project name")
@click.option("--books-dir", default=None, help="Books directory")
@click.option("--config", default="config.yaml", help="Path to config.yaml")
def agent_run(agent_name, project, books_dir, config):
    """
    Start an interactive session with a named agent.

    The agent knows the full project context from its Memory Bank and maintains
    conversation history for the duration of the session.

    \b
    Examples:
      memoria agent run onboarder --project my-project
      memoria agent run code-reviewer --project auth-service
      memoria agent run debugger --project api-gateway
    """
    if not books_dir:
        books_dir = _default_books_dir()

    if not project:
        # Auto-detect: pick the most recently updated book
        books_path = Path(books_dir)
        books = sorted(books_path.glob("*_memory_bank.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not books:
            console.print("[red]No Memory Banks found.[/red] Run [cyan]memoria analyze[/cyan] first.")
            raise click.Abort()
        project = books[0].stem.replace("_memory_bank", "")
        console.print(f"[dim]Using most recent project: [bold]{project}[/bold][/dim]")

    # Resolve config
    from pathlib import Path as _P
    cfg = _P(config).expanduser()
    if not cfg.exists():
        cfg = _P.home() / ".memoria" / "config.yaml"

    from .chat_agents import run_agent_session
    run_agent_session(
        agent_name=agent_name,
        project=project,
        books_dir=books_dir,
        config_path=str(cfg),
    )


@agent.command("install")
@click.argument("library_name")
@click.option("--project", "-p", default=None, help="Project name (default: global install)")
@click.option("--books-dir", default=None, help="Books directory")
@click.option("--global", "global_install", is_flag=True,
              help="Install to ~/.memoria/agents.yaml instead of per-project file")
def agent_install(library_name, project, books_dir, global_install):
    """
    Add a built-in agent template to your project or global agents.yaml.

    \b
    Built-in agents:
      code-reviewer    — reviews diffs / PRs with project context
      onboarder        — setup guide and getting-started assistant
      debugger         — traces bugs using Memory Bank knowledge
      explainer        — explains architecture and design decisions
      security-reviewer — identifies security vulnerabilities

    \b
    Examples:
      memoria agent install code-reviewer --project my-project
      memoria agent install onboarder --global
      memoria agent install debugger --project auth-service
    """
    if not books_dir:
        books_dir = _default_books_dir()

    project = project or "default"

    from .chat_agents import install_agent, AGENT_LIBRARY

    if library_name not in AGENT_LIBRARY:
        console.print(f"[red]'{library_name}' not found in agent library.[/red]")
        console.print(f"\nAvailable agents: [cyan]{', '.join(AGENT_LIBRARY.keys())}[/cyan]")
        raise click.Abort()

    try:
        path = install_agent(library_name, project, books_dir, global_install=global_install)
        scope = "global" if global_install else f"project '{project}'"
        console.print(
            Panel.fit(
                f"[green]Installed![/green]  [bold]{library_name}[/bold] added to {scope}\n"
                f"[dim]File: {path}[/dim]\n\n"
                f"[dim]Start a session: [bold]memoria agent run {library_name} --project {project}[/bold][/dim]",
                border_style="green",
            )
        )
    except Exception as exc:
        console.print(f"[red]Install failed:[/red] {exc}")
        raise click.Abort()


@agent.command("skills")
@click.argument("action", type=click.Choice(["list", "install", "info"]), default="list")
@click.argument("skill_name", required=False, default=None)
@click.option("--books-dir", default=None, help="Books directory")
@click.option("--config",    default="config.yaml", help="Path to config.yaml")
def agent_skills(action, skill_name, books_dir, config):
    """
    Browse and install community SKILL.md files for common processes.

    \b
    Examples:
      memoria agent skills list                         # show available skills
      memoria agent skills install incident-triage      # install a skill
      memoria agent skills info code-review             # show skill details
    """
    if not books_dir:
        books_dir = _default_books_dir()

    # ── Built-in skill library ──
    _SKILL_LIBRARY = {
        "incident-triage": {
            "name":        "Incident Triage",
            "description": "Guide an AI agent through P1/P2 incident triage: detect, escalate, coordinate, resolve, post-mortem.",
            "process":     "incident-response",
            "version":     "1.0",
            "tags":        ["operations", "on-call", "sre"],
            "template": """# SKILL: Incident Triage
> Version: 1.0 | Source: Memoria Community Library

## What This Skill Does
Guides an AI agent (or human operator) through the steps of triaging a production incident,
from initial detection through escalation, resolution, and post-mortem documentation.

## Prerequisites
- **Access:** Monitoring dashboard, PagerDuty/alerting system, Slack/incident channel
- **Context:** Severity levels (P1 = site down, P2 = major degradation, P3 = minor)

## Capabilities

### Detect and Classify
**When:** An alert fires or someone reports a possible incident
**Input:** Alert text, monitoring dashboard state, user reports
**Steps:**
1. Check monitoring dashboard for anomalies
2. Classify severity: P1 (complete outage), P2 (degraded), P3 (minor)
3. Check if alert is a known false-positive
**Output:** Severity classification + go/no-go for escalation
**Risk:** Low

### Escalate to On-Call
**When:** P1 or P2 incident confirmed
**Input:** Severity level, incident description
**Steps:**
1. Page on-call engineer via PagerDuty
2. Open incident channel (#incident-YYYY-MM-DD)
3. Post initial status: what is broken, when it started, who is working it
**Output:** Incident channel created, on-call paged
**Risk:** Medium — ensure correct on-call is paged

### Coordinate Response
**When:** During an active incident
**Steps:**
1. Assign Incident Commander (IC) and Communications Lead
2. Establish 15-min update cadence in incident channel
3. Identify blast radius — which systems and users are affected
4. Spin up war room call if P1
**Output:** Roles assigned, cadence established

### Resolve and Verify
**When:** Fix is deployed
**Steps:**
1. Verify metrics return to baseline
2. Check error rates, latency, user reports
3. Confirm resolution with monitoring for 10+ minutes
4. Announce "incident resolved" in channel
**Output:** Incident closed

### Post-Mortem
**When:** Within 48h of resolution
**Steps:**
1. Write timeline: what happened, when, what was done
2. Identify root cause (5 whys)
3. List action items with owners and due dates
4. Share with team
**Output:** Post-mortem document

## Rules & Constraints
- NEVER: Close an incident without verifying fix in production
- ALWAYS: Create an incident channel — verbal-only coordination loses information
- PREFER: Blameless post-mortems — focus on systems, not people

## Error Handling
| Error | Likely Cause | Recovery |
|-------|-------------|----------|
| Alert but no anomaly in monitoring | False positive | Silence alert, add to false-positive list |
| Cannot reach on-call | Wrong PagerDuty schedule | Page secondary on-call, then escalate to manager |
""",
        },
        "code-review": {
            "name":        "Code Review",
            "description": "Structured AI-assisted code review: security, performance, logic, style.",
            "process":     "code-review",
            "version":     "1.0",
            "tags":        ["development", "quality", "security"],
            "template": """# SKILL: Code Review
> Version: 1.0 | Source: Memoria Community Library

## What This Skill Does
Provides a structured framework for AI-assisted code review, covering security,
performance, correctness, and code quality.

## Prerequisites
- **Access:** Code diff / PR, codebase context
- **Context:** Language, framework, team style guide

## Capabilities

### Security Review
**When:** Any PR touching authentication, authorization, data handling, or external inputs
**Steps:**
1. Check for SQL injection, XSS, path traversal, command injection
2. Verify input validation on all user-supplied data
3. Check secret management — no hardcoded keys or credentials
4. Review auth checks on every protected route
**Risk:** High — security issues can be silent until exploited

### Performance Review
**When:** PRs touching hot paths, database queries, or loops over large datasets
**Steps:**
1. Identify N+1 queries
2. Check for missing indexes on new query patterns
3. Look for unbounded loops or missing pagination
4. Check cache invalidation correctness
**Risk:** Medium

### Logic & Correctness
**When:** Every PR
**Steps:**
1. Trace the happy path end-to-end
2. Test the failure path — what happens when external calls fail?
3. Check edge cases: empty input, zero, null, concurrent access
4. Verify error handling propagates correctly
**Risk:** Medium

### Style & Maintainability
**When:** Every PR
**Steps:**
1. Check naming clarity (functions do what their names say)
2. Look for overly complex functions (>50 lines, >4 parameters)
3. Verify tests cover the new behaviour
4. Check documentation for non-obvious logic
**Risk:** Low

## Rules & Constraints
- NEVER: Approve a PR with a hardcoded secret
- ALWAYS: Check the test coverage for critical paths
- PREFER: Request changes over blocking — suggest don't demand

## Error Handling
| Issue | Action |
|-------|--------|
| No tests for critical path | Request changes: add tests before merge |
| Potential security issue | Block PR, discuss in private first |
""",
        },
        "customer-onboarding": {
            "name":        "Customer Onboarding",
            "description": "Guide a new customer from signup to first value (activation).",
            "process":     "customer-onboarding",
            "version":     "1.0",
            "tags":        ["customer-success", "growth", "sales"],
            "template": """# SKILL: Customer Onboarding
> Version: 1.0 | Source: Memoria Community Library

## What This Skill Does
Guides a customer success manager or AI agent through the onboarding of a new customer,
from signed contract through successful activation (first value moment).

## Prerequisites
- **Access:** CRM (Salesforce/HubSpot), onboarding tracker, product admin panel
- **Context:** Customer segment (SMB/Enterprise), contracted features, key stakeholders

## Capabilities

### Kickoff Preparation
**When:** Contract signed, customer in CRM as "Onboarding"
**Steps:**
1. Assign Customer Success Manager
2. Create customer Slack channel and invite key contacts
3. Prepare kickoff deck (company context, timeline, success metrics)
4. Schedule kickoff call (target: within 5 business days of signature)
**Output:** Kickoff call scheduled, channel created

### Technical Setup
**When:** Kickoff call completed
**Steps:**
1. Provision account in product
2. Set up SSO if contracted
3. Import/migrate existing data if needed
4. Configure integrations (CRM, ticketing, etc.)
**Output:** Account live and configured

### Training
**When:** Technical setup complete
**Steps:**
1. Run admin training (1h) — focus on setup and user management
2. Run end-user training (30min) — focus on daily workflows
3. Share self-service resources: docs, video library, community
**Output:** Admin and key users trained

### First Value Check
**When:** 2 weeks post-kickoff
**Steps:**
1. Review usage: are users logging in? Core features used?
2. Address blockers raised in kickoff
3. Identify expansion potential
4. Mark "Activated" in CRM if core use case completed
**Output:** Customer activated or escalation plan created

## Rules & Constraints
- NEVER: Consider onboarding done without the customer confirming first value
- ALWAYS: Document blockers — unresolved blockers become churn risk

## Error Handling
| Situation | Action |
|-----------|--------|
| Low adoption after 2 weeks | Escalate to CS Manager + Sales, arrange executive sponsor call |
| Technical blocker not resolved | Escalate to engineering with SLA |
""",
        },
        "deploy-checklist": {
            "name":        "Deployment Checklist",
            "description": "Pre/post deployment checks for production releases.",
            "process":     "deployment",
            "version":     "1.0",
            "tags":        ["devops", "deployment", "release"],
            "template": """# SKILL: Deployment Checklist
> Version: 1.0 | Source: Memoria Community Library

## What This Skill Does
Runs an AI agent through pre-deployment and post-deployment checks to reduce
the risk of production incidents during and after releases.

## Capabilities

### Pre-Deploy
**When:** Before deploying any production change
**Steps:**
1. Confirm change has been reviewed and approved (PR merged or change approved)
2. Check deployment window — avoid peak traffic unless emergency
3. Verify rollback plan exists and has been tested
4. Notify on-call engineer of upcoming deploy
5. Check monitoring dashboards — confirm baseline is healthy before deploying

### Deploy
**Steps:**
1. Run deploy command per runbook
2. Monitor deployment progress — watch for errors
3. Verify new version is live: check version endpoint or build hash

### Post-Deploy
**Steps:**
1. Monitor error rates for 10 minutes — watch for spikes
2. Check latency — P50, P95, P99
3. Run smoke tests against production
4. Confirm no unexpected alerts
5. Post "deploy complete" in team channel

### Rollback
**When:** Error rate spike, P1 alert, or data issues detected
**Steps:**
1. Decision: rollback vs hotfix (rollback if fix is >30 min away)
2. Execute rollback per runbook
3. Verify rollback complete, metrics returning to baseline
4. Post-mortem required for any rollback

## Rules & Constraints
- NEVER: Deploy on Friday after 3pm without explicit approval
- ALWAYS: Have a rollback plan before deploying
- PREFER: Deploy to staging first, production second
""",
        },
    }

    # ── LIST ──
    if action == "list":
        print_banner()
        console.print("\n[bold]Community Agent Skills Library[/bold]")
        console.print("[dim]Use [bold]memoria agent skills install <name>[/bold] to add to your project.[/dim]\n")

        from rich.table import Table as _Table
        tbl = _Table(show_header=True, box=None, padding=(0, 2))
        tbl.add_column("Name",        style="bold cyan")
        tbl.add_column("Tags",        style="dim")
        tbl.add_column("Description")

        for key, sk in _SKILL_LIBRARY.items():
            tbl.add_row(
                key,
                ", ".join(sk["tags"]),
                sk["description"],
            )
        console.print(tbl)

    # ── INFO ──
    elif action == "info":
        if not skill_name or skill_name not in _SKILL_LIBRARY:
            console.print(f"[red]Unknown skill:[/red] {skill_name or '(none)'}")
            console.print("[dim]Run [bold]memoria agent skills list[/bold] to see available skills.[/dim]")
            raise click.Abort()
        sk = _SKILL_LIBRARY[skill_name]
        print_banner()
        console.print(Panel(
            f"[bold]{sk['name']}[/bold] v{sk['version']}\n\n"
            f"{sk['description']}\n\n"
            f"[dim]Tags:[/dim] {', '.join(sk['tags'])}",
            title=f"Skill: {skill_name}",
            border_style="cyan",
        ))
        console.print("\n[bold]SKILL.md preview:[/bold]\n")
        console.print(sk["template"][:800] + "\n[dim]...[/dim]")

    # ── INSTALL ──
    elif action == "install":
        if not skill_name or skill_name not in _SKILL_LIBRARY:
            console.print(f"[red]Unknown skill:[/red] {skill_name or '(none)'}")
            console.print("[dim]Available: " + ", ".join(_SKILL_LIBRARY.keys()) + "[/dim]")
            raise click.Abort()
        sk = _SKILL_LIBRARY[skill_name]
        skills_dir = Path(books_dir) / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        out_file = skills_dir / f"{skill_name}_SKILL.md"
        out_file.write_text(sk["template"], encoding="utf-8")
        console.print(Panel.fit(
            f"[bold green]Installed:[/bold green] [bold]{sk['name']}[/bold]\n\n"
            f"[dim]Saved to:[/dim] [cyan]{out_file.resolve()}[/cyan]\n\n"
            "[dim]Drop this file into a Claude Project, Cursor context, or agent system prompt.[/dim]",
            border_style="green",
        ))


@agent.command("context")
@click.option("--project", "-p", default=None, help="Project name")
@click.option("--agent-name", "-a", default=None, help="Agent name (uses agent's focus sections)")
@click.option("--books-dir", default=None, help="Books directory")
def agent_context(project, agent_name, books_dir):
    """
    Print the agent context string that would be injected into the system prompt.
    Useful for debugging and for copy-pasting into other agent frameworks.

    \b
    Examples:
      memoria agent context --project my-project
      memoria agent context --project my-project --agent-name code-reviewer
    """
    if not books_dir:
        books_dir = _default_books_dir()

    if not project:
        books_path = Path(books_dir)
        books = sorted(books_path.glob("*_memory_bank.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not books:
            console.print("[red]No Memory Banks found.[/red]")
            raise click.Abort()
        project = books[0].stem.replace("_memory_bank", "")

    from .chat_agents import build_agent_context, get_agent

    focus = None
    if agent_name:
        defn = get_agent(agent_name, project, books_dir)
        if defn:
            focus = defn.get("focus")

    context, sections = build_agent_context(project, books_dir, focus=focus)

    console.print(Panel(
        context,
        title=f"[bold cyan]Agent context — {project}[/bold cyan]"
              + (f" ({agent_name})" if agent_name else ""),
        border_style="cyan",
    ))
    console.print(
        f"\n[dim]{len(context)} chars · {len(context.split())} words · "
        f"sections: {', '.join(sections.keys()) or 'none parsed'}[/dim]"
    )


# ─── Command: coverage ────────────────────────────────────────

@cli.command()
@click.option("--books-dir",     default=None,  help="Books directory")
@click.option("--scan",  "-s",   multiple=True, help="Additional directories to scan for undocumented projects (can repeat)")
@click.option("--output", "-o",  default=None,  help="Write JSON report to file")
@click.option("--config",        default="config.yaml", help="Path to config.yaml")
def coverage(books_dir, scan, output, config):
    """
    Analyse knowledge coverage across all Memory Banks.

    Reports: stale books, ownership gaps, low-confidence process docs,
    sections with unknown/TBD markers, and directories with no Memory Bank.

    \b
    Examples:
      memoria coverage
      memoria coverage --scan ~/projects
      memoria coverage --output coverage.json
    """
    import json as _json
    from .coverage import run_coverage_analysis

    if not books_dir:
        books_dir = _default_books_dir()

    print_banner()
    console.print("\n[bold]Running knowledge coverage analysis...[/bold]\n")

    report = run_coverage_analysis(
        books_dir=books_dir,
        scan_roots=list(scan) if scan else None,
    )

    if "error" in report:
        console.print(f"[red]Error:[/red] {report['error']}")
        raise click.Abort()

    s = report["summary"]

    # ── Health score badge ──
    score = s["overall_health"]
    if score >= 80:
        score_color, score_icon = "green",  "●"
    elif score >= 60:
        score_color, score_icon = "yellow", "●"
    else:
        score_color, score_icon = "red",    "●"

    # ── Summary panel ──
    summary_lines = [
        f"[bold]Overall health score:[/bold] [{score_color}]{score_icon} {score}/100[/{score_color}]",
        "",
        f"  [green]✓[/green]  {s['total_documented']} project(s) documented",
    ]
    if s["total_undocumented"]:
        summary_lines.append(f"  [yellow]○[/yellow]  {s['total_undocumented']} project(s) with no Memory Bank")
    if s["stale_critical"]:
        summary_lines.append(f"  [red]✗[/red]  {s['stale_critical']} book(s) stale >90 days — likely outdated")
    if s["stale_warning"]:
        summary_lines.append(f"  [yellow]![/yellow]  {s['stale_warning']} book(s) stale >30 days")
    if s["ownership_gaps"]:
        summary_lines.append(f"  [yellow]![/yellow]  {s['ownership_gaps']} book(s) missing owner / DRI")
    if s["low_health_books"]:
        summary_lines.append(f"  [red]✗[/red]  {s['low_health_books']} book(s) have a health score below 60")

    console.print(Panel("\n".join(summary_lines), title="Coverage Summary", border_style=score_color))

    # ── Per-book table ──
    if report["books"]:
        console.print()
        from rich.table import Table as _Table
        tbl = _Table(title="Memory Bank Health", show_lines=False, box=None, padding=(0, 2))
        tbl.add_column("Project",    style="bold")
        tbl.add_column("Score",      justify="right")
        tbl.add_column("Age",        justify="right")
        tbl.add_column("Size",       justify="right")
        tbl.add_column("Issues",     style="dim")

        for bk in sorted(report["books"], key=lambda x: x["health_score"]):
            sc = bk["health_score"]
            sc_str = f"[green]{sc}[/green]" if sc >= 80 else f"[yellow]{sc}[/yellow]" if sc >= 60 else f"[red]{sc}[/red]"
            age_str = f"{bk['age_days']}d"
            if bk["staleness"] == "critical":
                age_str = f"[red]{age_str}[/red]"
            elif bk["staleness"] == "warning":
                age_str = f"[yellow]{age_str}[/yellow]"

            issues = []
            if bk["ownership_gap"]:       issues.append("no owner")
            if bk["low_confidence"]:      issues.append("low confidence")
            if bk["unknown_markers"] > 3: issues.append(f"{bk['unknown_markers']} unknowns")
            if bk["flagged_sections"]:    issues.append(f"{len(bk['flagged_sections'])} gap sections")

            tbl.add_row(
                bk["project"],
                sc_str,
                age_str,
                f"{bk['size_kb']}kb",
                ", ".join(issues) or "[dim]—[/dim]",
            )
        console.print(tbl)

    # ── Undocumented projects ──
    if report["undocumented"]:
        console.print()
        console.print("[bold yellow]Undocumented projects found:[/bold yellow]")
        for u in report["undocumented"]:
            sigs = ", ".join(u["signals"])
            console.print(f"  [yellow]○[/yellow]  [bold]{u['name']}[/bold]  [dim]{u['path']}[/dim]  [dim]({sigs})[/dim]")
        console.print(f"\n  [dim]Run [bold]memoria analyze --repo <path>[/bold] to document them.[/dim]")

    # ── JSON output ──
    if output:
        Path(output).write_text(_json.dumps(report, indent=2), encoding="utf-8")
        console.print(f"\n[dim]Report saved to:[/dim] [cyan]{Path(output).resolve()}[/cyan]")

    console.print()


# ─── Command: brief ───────────────────────────────────────────

@cli.command()
@click.option("--since",     default=None,     help="Look back N days (e.g. 2d). Default: config or 1d.")
@click.option("--delivery",  default=None,     help="terminal | notify | slack | silent")
@click.option("--max-items", default=None, type=int, help="Cap number of items shown (default: 10)")
@click.option("--config",    default="config.yaml", help="Path to config.yaml")
@click.option("--books-dir", default=None,     help="Books directory")
@click.option("--output",    default="terminal", help="terminal | json  (output format, not delivery channel)")
def brief(since, delivery, max_items, config, books_dir, output):
    """
    Generate a morning intelligence brief — what changed, what needs attention,
    what to do next.

    Reads your draft queue, Memory Banks, graph impact, and MCP source
    freshness; synthesises them into a concise, actionable summary.

    \b
    Examples:
      memoria brief                    # today's brief (terminal)
      memoria brief --since 2d         # cover the last 2 days
      memoria brief --delivery notify  # also send a desktop notification
      memoria brief --delivery slack   # post to configured Slack channel
      memoria brief --output json      # machine-readable JSON

    \b
    Config (config.yaml):
      brief:
        schedule: "0 8 * * 1-5"   # weekdays at 8 AM via memoria schedule start
        delivery: terminal          # default delivery for scheduled runs
        days_back: 1
        max_items: 10
    """
    import yaml as _yaml

    if not books_dir:
        books_dir = _default_books_dir()

    # Load defaults from config brief: block
    brief_cfg: dict = {}
    cfg_path = Path(config).expanduser()
    if not cfg_path.exists():
        cfg_path = Path.home() / ".memoria" / "config.yaml"
    if cfg_path.exists():
        try:
            cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            brief_cfg = cfg.get("brief", {}) or {}
        except Exception:
            pass

    # CLI flags override config
    days_back_val = 1
    if since:
        # Parse "2d", "3", "1d" → int days
        s = since.strip().lower().rstrip("d")
        try:
            days_back_val = max(1, int(s))
        except ValueError:
            console.print(f"[red]Invalid --since value:[/red] {since}  (use e.g. '2d' or '3')")
            raise click.Abort()
    else:
        days_back_val = int(brief_cfg.get("days_back", 1))

    delivery_val = delivery or brief_cfg.get("delivery", "terminal")
    max_items_val = max_items or int(brief_cfg.get("max_items", 10))

    # Need at least a config.yaml to call the LLM
    config_resolved = str(cfg_path)

    if output == "json":
        # Machine-readable path — generate silently and print JSON
        from .brief import run_brief
        import json as _json
        result = run_brief(
            config_path=config_resolved,
            books_dir=books_dir,
            days_back=days_back_val,
            delivery="silent",
            max_items=max_items_val,
        )
        click.echo(_json.dumps(result, indent=2, default=str))
        return

    # Terminal / notify / slack path
    print_banner()
    from .brief import run_brief
    run_brief(
        config_path=config_resolved,
        books_dir=books_dir,
        days_back=days_back_val,
        delivery=delivery_val,
        max_items=max_items_val,
    )


# ─── Command: plan ────────────────────────────────────────────

@cli.command()
@click.argument("file", required=False, default=None)
@click.option("--text",      "-t", default=None,       help="Plan text directly (instead of a file).")
@click.option("--output",    "-o", default="terminal",
              type=click.Choice(["terminal", "json", "markdown"]),
              help="Output format (default: terminal).")
@click.option("--chunk/--no-chunk", default=True,
              help="Auto-chunk large plans by heading structure (default: on).")
@click.option("--max-books", default=8, type=int,       help="Max Memory Banks to include in context (default: 8).")
@click.option("--config",    default="config.yaml",     help="Path to config.yaml")
@click.option("--books-dir", default=None,              help="Books directory")
def plan(file, text, output, chunk, max_books, config, books_dir):
    """
    Analyse a project plan against your Memory Banks and knowledge graph.

    Surfaces dependencies, risks, prior art, and recommended next steps
    before a single line of code is written.

    Large plans are automatically split by heading structure and analysed
    section-by-section, then merged into one unified report.

    \b
    Examples:
      memoria plan quarterly-plan.md
      memoria plan plan.md --output json
      memoria plan --text "Add OAuth2 to the auth service"
      cat plan.md | memoria plan --text -
      memoria plan big-plan.md --no-chunk   # force single-pass (truncates)

    \b
    The report covers:
      • Affected projects     — which existing Memory Banks this plan touches
      • Dependencies          — what the plan needs from each project
      • Risks                 — severity-tagged issues with Memory Bank citations
      • Prior art             — earlier attempts or decisions found in books
      • Recommended actions   — concrete next steps with memoria commands
    """
    from .planner import load_plan, analyze_plan, analyze_plan_chunked, render_terminal, render_markdown
    import json as _json

    if not books_dir:
        books_dir = _default_books_dir()

    # Resolve config
    cfg_path = Path(config).expanduser()
    if not cfg_path.exists():
        cfg_path = Path.home() / ".memoria" / "config.yaml"
    config_resolved = str(cfg_path)

    # ── Load plan text ──
    if text:
        # Check for stdin pipe: --text -
        if text == "-":
            import sys as _sys
            plan_text = _sys.stdin.read().strip()
        else:
            plan_text = text
    elif file:
        try:
            plan_text = load_plan(file)
        except Exception as exc:
            console.print(f"[red]Could not read plan file:[/red] {exc}")
            raise click.Abort()
    else:
        console.print(
            "[yellow]No plan provided.[/yellow] "
            "Pass a file path or use [bold]--text[/bold] with your plan text."
        )
        console.print(
            "\n[dim]Example:[/dim]  memoria plan quarterly-plan.md\n"
            "          memoria plan --text 'Add OAuth2 to the auth service'"
        )
        raise click.Abort()

    if not plan_text.strip():
        console.print("[red]Plan text is empty.[/red]")
        raise click.Abort()

    # ── Run analysis ──
    print_banner()
    console.print()

    # Decide whether to chunk
    from .planner import _CHUNK_THRESHOLD
    will_chunk = chunk and len(plan_text) >= _CHUNK_THRESHOLD

    if will_chunk:
        from .planner import split_plan
        n_chunks = len(split_plan(plan_text))
        spinner_msg = (
            f"[cyan]Plan is large — analysing in {n_chunks} section(s)…[/cyan]"
            if n_chunks > 1
            else "[cyan]Searching Memory Banks and analysing plan…[/cyan]"
        )
    else:
        spinner_msg = "[cyan]Searching Memory Banks and analysing plan…[/cyan]"

    with console.status(spinner_msg, spinner="dots"):
        if will_chunk:
            report = analyze_plan_chunked(
                plan_text=plan_text,
                config_path=config_resolved,
                books_dir=books_dir,
                max_books=max_books,
            )
        else:
            report = analyze_plan(
                plan_text=plan_text,
                config_path=config_resolved,
                books_dir=books_dir,
                max_books=max_books,
            )

    # Note chunking in terminal output
    meta = report.get("_meta", {})
    if meta.get("chunked") and meta.get("chunk_count", 1) > 1:
        console.print(
            f"[dim]ℹ  Plan split into {meta['chunk_count']} sections — "
            f"results merged into a unified report.[/dim]\n"
        )

    # ── Output ──
    if output == "json":
        click.echo(_json.dumps(report, indent=2, default=str))
    elif output == "markdown":
        click.echo(render_markdown(report))
    else:
        render_terminal(report, console=console)


# ─── Command: interview ───────────────────────────────────────

@cli.command()
@click.option("--topic",      "-t", default=None, help="The process or procedure to document")
@click.option("--project",    "-p", default=None, help="Which project this process belongs to (for context)")
@click.option("--duration",   "-d", default=30,   show_default=True, help="Max interview duration in minutes")
@click.option("--books-dir",        default=None, help="Books directory")
@click.option("--config",           default="config.yaml", help="Path to config.yaml")
def interview(topic, project, duration, books_dir, config):
    """
    Interview a subject-matter expert and turn their answers into a Process Memory Bank.

    \b
    Memoria will ask guided questions about a process or SOP.
    Adaptive follow-ups probe gaps automatically.
    At the end, the answers are synthesised into a structured Process Memory Bank.

    \b
    Examples:
      memoria interview
      memoria interview --topic "Incident response" --project backend-api
      memoria interview --topic "New hire onboarding" --duration 20
    """
    from .interview import run_interview

    if not books_dir:
        books_dir = _default_books_dir()

    print_banner()

    # ── Get topic ──
    if not topic:
        console.print("\n[bold]What process do you want to document?[/bold]")
        console.print("[dim]Examples: incident response, deployment runbook, new hire onboarding[/dim]")
        topic = Prompt.ask("\n  Process / topic").strip()
        if not topic:
            console.print("[red]Topic is required.[/red]")
            raise click.Abort()

    if not project:
        project = Prompt.ask(
            "  Which project or team does this belong to?",
            default="General"
        ).strip()

    # ── Welcome ──
    console.print()
    console.print(Panel.fit(
        f"[bold]Interview:[/bold] {topic}\n"
        f"[dim]Project:[/dim] {project}   "
        f"[dim]Time limit:[/dim] {duration} min\n\n"
        "[dim]Answer each question as thoroughly as you can.\n"
        "Type [bold]skip[/bold] to skip a question, [bold]done[/bold] to end early.[/dim]",
        border_style="cyan",
        title="📋 Process Interview",
    ))

    qa_pairs_display = []

    def _on_print(msg):
        console.print(msg)

    def _on_prompt(question: str) -> str:
        return Prompt.ask("  ▸")

    try:
        result = run_interview(
            topic=topic,
            project=project,
            duration_minutes=duration,
            config_path=config,
            books_dir=books_dir,
            on_print=_on_print,
            on_prompt=_on_prompt,
        )
    except RuntimeError as e:
        console.print(f"\n[red]✗ Interview failed:[/red] {e}")
        raise click.Abort()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interview interrupted.[/yellow]")
        raise click.Abort()

    # ── Done ──
    minutes = result["duration_seconds"] // 60
    seconds = result["duration_seconds"] % 60
    n_questions = len(result["transcript"])
    abs_path = Path(result["output_path"]).resolve()

    console.print()
    console.print(Panel.fit(
        f"[bold green]Process Memory Bank created![/bold green]\n\n"
        f"[dim]Topic:[/dim]      {topic}\n"
        f"[dim]Questions:[/dim]  {n_questions} asked\n"
        f"[dim]Duration:[/dim]   {minutes}m {seconds}s\n"
        f"[dim]Saved to:[/dim]   [cyan]{abs_path}[/cyan]\n\n"
        f"[dim]Run [bold]memoria ask --project \"{topic}\"[/bold] to query it.[/dim]",
        border_style="green",
        title="✓ Interview complete",
    ))


# ─── Command: skills ──────────────────────────────────────────

@cli.command()
@click.option("--project",   "-p", default=None, help="Project name (matches book filename). Omit to pick interactively.")
@click.option("--all",       "-a", "all_projects", is_flag=True, help="Generate skill files for ALL projects.")
@click.option(
    "--format",  "-f", "fmt",
    type=click.Choice(["raw", "langchain", "crewai", "autogen", "n8n"], case_sensitive=False),
    default="raw", show_default=True,
    help="Output format.",
)
@click.option("--output",    "-o", default=None, help="Output directory (default: books dir / skills/)")
@click.option("--books-dir",       default=None, help="Books directory")
@click.option("--config",          default="config.yaml", help="Path to config.yaml")
def skills(project, all_projects, fmt, output, books_dir, config):
    """
    Convert Memory Banks into SKILL.md files for AI agents.

    \b
    A SKILL.md tells any AI agent (Claude, Cursor, Copilot, n8n, LangChain, CrewAI)
    what it can do with a project — capabilities, inputs, outputs, constraints.

    \b
    Examples:
      memoria skills                          # pick a project, generate SKILL.md
      memoria skills --project my-api         # specific project
      memoria skills --all                    # every project at once
      memoria skills --project my-api --format langchain   # LangChain tools
      memoria skills --project my-api --format crewai      # CrewAI agent
      memoria skills --project my-api --format n8n         # n8n workflow JSON
    """
    from .skills import generate_skill, list_skills

    if not books_dir:
        books_dir = _default_books_dir()

    if not output:
        output = str(Path(books_dir) / "skills")

    print_banner()

    books_path = Path(books_dir)
    if not books_path.exists():
        console.print("[red]Books directory not found.[/red] Run [bold]memoria analyze[/bold] first.")
        raise click.Abort()

    # ── Collect target books ──
    all_books = sorted(books_path.glob("*_memory_bank.md"))
    if not all_books:
        console.print("[yellow]No memory banks found.[/yellow] Run [bold]memoria analyze[/bold] first.")
        raise click.Abort()

    if all_projects:
        targets = all_books
    elif project:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project)
        found = books_path / f"{safe}_memory_bank.md"
        if not found.exists():
            # Fuzzy: find a book whose name contains the project string
            matches = [b for b in all_books if project.lower() in b.stem.lower()]
            if not matches:
                console.print(f"[red]No memory bank found for:[/red] {project}")
                console.print("[dim]Run [bold]memoria list[/bold] to see available projects.[/dim]")
                raise click.Abort()
            found = matches[0]
        targets = [found]
    else:
        # Interactive picker
        console.print("\n[bold]Which project do you want to convert?[/bold]")
        names = [b.stem.replace("_memory_bank", "").replace("_", " ") for b in all_books]
        for i, name in enumerate(names, 1):
            console.print(f"  [cyan]{i}[/cyan]  {name}")
        raw = Prompt.ask("\n  Enter number or project name")
        try:
            idx = int(raw) - 1
            targets = [all_books[idx]]
        except (ValueError, IndexError):
            matches = [b for b in all_books if raw.lower() in b.stem.lower()]
            if not matches:
                console.print(f"[red]No match for:[/red] {raw}")
                raise click.Abort()
            targets = [matches[0]]

    # ── Generate ──
    console.print()
    generated = []
    for book_file in targets:
        proj_name = book_file.stem.replace("_memory_bank", "").replace("_", " ")
        content = book_file.read_text(encoding="utf-8")

        console.print(f"[dim]→[/dim] [bold]{proj_name}[/bold] ({fmt})")
        try:
            out_path = generate_skill(
                project_name=proj_name,
                book_content=content,
                output_dir=output,
                fmt=fmt,
                config_path=config,
                on_progress=lambda s: console.print(f"  [cyan]->[/cyan] {s}"),
            )
            generated.append((proj_name, out_path))
            console.print(f"  [green]✓[/green] [cyan]{Path(out_path).resolve()}[/cyan]")
        except Exception as e:
            console.print(f"  [red]✗ Failed:[/red] {e}")

    # ── Summary ──
    if generated:
        console.print()
        console.print(Panel.fit(
            f"[bold green]{len(generated)} skill file{'s' if len(generated) > 1 else ''} generated[/bold green]\n\n"
            + "\n".join(f"[dim]•[/dim] [cyan]{Path(p).name}[/cyan]" for _, p in generated)
            + f"\n\n[dim]Directory:[/dim] [cyan]{Path(output).resolve()}[/cyan]\n"
            "[dim]Drop any SKILL.md into a Claude Project, Cursor context, or agent system prompt.[/dim]",
            border_style="green",
            title="✓ Skills generated",
        ))


# ─── Command: digest ──────────────────────────────────────────

@cli.command()
@click.option("--window",   "-w",  default="7d",  help="Time window: '7d', '30d', '24h'. Default: 7d.")
@click.option("--output",   "-o",  default="terminal",
              type=click.Choice(["terminal", "markdown", "json", "slack", "email"], case_sensitive=False),
              help="Output destination. Default: terminal.")
@click.option("--books-dir",       default=None,  help="Books directory.")
@click.option("--config",          default=None,  help="Path to config.yaml.")
@click.option("--slack-webhook",   default=None,  help="Slack webhook URL (overrides config.yaml).")
@click.option("--to",              default=None, multiple=True, help="Email recipient(s). Repeat for multiple.")
def digest(window, output, books_dir, config, slack_webhook, to):
    """
    Executive Digest — weekly/monthly AI summary for managers and PMs.

    Higher-level than the Morning Brief: covers what changed across all
    projects, what was shipped, what's at risk, and recommended actions.

    Delivery options: terminal, Slack webhook, or email.
    Configure Slack/email in config.yaml under the 'digest:' key.

    \b
    Examples:
      memoria digest                          # last 7 days, terminal
      memoria digest --window 30d             # last month
      memoria digest --output markdown        # raw Markdown (pipe to file)
      memoria digest --output slack           # post to Slack (configure webhook)
      memoria digest --output email --to pm@company.com
    """
    import json as _json
    from .digest import build_digest, deliver_slack, deliver_email

    resolved_books_dir = books_dir or _default_books_dir()
    resolved_config    = config or str(Path.home() / ".memoria" / "config.yaml")

    print_banner()
    console.print()

    try:
        with console.status(
            f"[cyan]Building {window} executive digest…[/cyan]", spinner="dots"
        ):
            result = build_digest(
                books_dir   = resolved_books_dir,
                window      = window,
                config_path = resolved_config,
                on_progress = lambda s: console.log(f"[cyan]->[/cyan] {s}"),
            )
    except ValueError as e:
        console.print(f"[yellow]⚠[/yellow] {e}")
        return
    except RuntimeError as e:
        console.print(f"[red]✗[/red] {e}")
        return

    md_text = result["markdown"]

    # ── Terminal ──────────────────────────────────────────────────────────────
    if output == "terminal":
        from rich.markdown import Markdown as _MD
        console.print(f"\n[bold green]✓[/bold green] Digest ready "
                      f"([cyan]{len(result['changed_projects'])}[/cyan] projects updated)\n")
        console.print(_MD(md_text))
        return

    # ── Markdown ──────────────────────────────────────────────────────────────
    if output == "markdown":
        click.echo(md_text)
        return

    # ── JSON ──────────────────────────────────────────────────────────────────
    if output == "json":
        click.echo(_json.dumps(result, indent=2, default=str))
        return

    # ── Slack ─────────────────────────────────────────────────────────────────
    if output == "slack":
        webhook = slack_webhook
        if not webhook:
            # Try config.yaml
            try:
                import yaml
                cfg = yaml.safe_load(
                    Path(resolved_config).read_text(encoding="utf-8")) or {}
                webhook = cfg.get("digest", {}).get("slack_webhook", "")
            except Exception:
                pass
        if not webhook:
            console.print("[red]✗[/red] No Slack webhook URL. Provide with [cyan]--slack-webhook <url>[/cyan] "
                          "or set [bold]digest.slack_webhook[/bold] in config.yaml.")
            return
        try:
            deliver_slack(md_text, webhook)
            console.print("[green]✓[/green] Digest posted to Slack.")
        except Exception as exc:
            console.print(f"[red]✗[/red] Slack delivery failed: {exc}")
        return

    # ── Email ─────────────────────────────────────────────────────────────────
    if output == "email":
        # Load email config from config.yaml
        try:
            import yaml
            cfg = yaml.safe_load(
                Path(resolved_config).read_text(encoding="utf-8")) or {}
        except Exception:
            cfg = {}
        email_cfg = cfg.get("digest", {}).get("email", {})

        smtp_host = email_cfg.get("smtp_host", "smtp.gmail.com")
        smtp_port = email_cfg.get("smtp_port", 587)
        from_addr = email_cfg.get("from", "")
        to_addrs  = list(to) or email_cfg.get("to", [])
        subject   = email_cfg.get("subject", "Weekly Memoria Digest")
        username  = email_cfg.get("username") or os.environ.get("SMTP_USERNAME")
        password  = email_cfg.get("password") or os.environ.get("SMTP_PASSWORD")

        if not from_addr or not to_addrs:
            console.print(
                "[red]✗[/red] Email requires [bold]digest.email.from[/bold] and "
                "[bold]digest.email.to[/bold] in config.yaml (or --to flag)."
            )
            return

        try:
            deliver_email(
                markdown  = md_text,
                smtp_host = smtp_host,
                smtp_port = smtp_port,
                from_addr = from_addr,
                to_addrs  = to_addrs,
                subject   = subject,
                username  = username,
                password  = password,
            )
            console.print(f"[green]✓[/green] Digest emailed to: {', '.join(to_addrs)}")
        except Exception as exc:
            console.print(f"[red]✗[/red] Email delivery failed: {exc}")


# ─── Command: org-chart ───────────────────────────────────────

@cli.command("org-chart")
@click.option("--set",       "set_project",  default=None, metavar="PROJECT", help="Set/update ownership for a project.")
@click.option("--owner",                     default=None, help="Owner name (use with --set).")
@click.option("--team",                      default=None, help="Team name (use with --set or --team-only).")
@click.option("--email",                     default=None, help="Owner email (use with --set).")
@click.option("--slack",                     default=None, help="Slack handle or channel (use with --set).")
@click.option("--domain",                    default=None, help="Domain/area (e.g. 'authentication') (use with --set).")
@click.option("--notes",                     default=None, help="Free-text notes (use with --set).")
@click.option("--who-owns",  "who_owns",     default=None, metavar="PROJECT", help="Look up who owns a project.")
@click.option("--list",      "show_list",    is_flag=True, help="List all registered projects and owners.")
@click.option("--list-teams","list_teams_flag", is_flag=True, help="List all registered teams.")
@click.option("--unowned",                   is_flag=True, help="Show Memory Bank projects with no owner registered.")
@click.option("--search",    "search_query", default=None, metavar="QUERY",   help="Search by name, owner, team, or domain.")
@click.option("--remove",                    default=None, metavar="PROJECT",  help="Remove a project from the org chart.")
@click.option("--books-dir",                 default=None, help="Books directory (used for --unowned).")
def org_chart(set_project, owner, team, email, slack, domain, notes,
              who_owns, show_list, list_teams_flag, unowned, search_query,
              remove, books_dir):
    """
    Queryable Org Chart — link people, teams, and domains to projects.

    Answers: "Who owns checkout?", "What does the Backend team own?",
    "Which projects have no owner?"

    Ownership data is stored in ~/.memoria/org_chart.yaml and is injected
    into Global Ask answers when questions mention ownership.

    \b
    Examples:
      memoria org-chart --set auth-service --owner "Jane Smith" --team Backend
      memoria org-chart --who-owns auth-service
      memoria org-chart --list
      memoria org-chart --team Backend
      memoria org-chart --unowned
      memoria org-chart --search "payments"
    """
    from .org_chart import (
        set_owner, get_owner, remove_project,
        list_by_team, list_unowned, search as _search,
        list_teams, all_entries,
    )

    resolved_books_dir = books_dir or _default_books_dir()

    # ── --set ─────────────────────────────────────────────────────────────────
    if set_project:
        entry = set_owner(
            project=set_project,
            owner=owner, team=team, email=email,
            slack=slack, domain=domain, notes=notes,
        )
        console.print(f"\n[green]✓[/green] Saved [cyan]{set_project}[/cyan]")
        for k, v in entry.items():
            if v:
                console.print(f"  [dim]{k}:[/dim] {v}")
        console.print()
        return

    # ── --remove ──────────────────────────────────────────────────────────────
    if remove:
        ok = remove_project(remove)
        if ok:
            console.print(f"[green]✓[/green] Removed: {remove}")
        else:
            console.print(f"[yellow]⚠[/yellow] Not found: {remove}")
        return

    # ── --who-owns ────────────────────────────────────────────────────────────
    if who_owns:
        entry = get_owner(who_owns)
        if not entry:
            console.print(f"[yellow]⚠[/yellow] No entry for [cyan]{who_owns}[/cyan].")
            console.print(f"  Add one with: [cyan]memoria org-chart --set {who_owns} --owner \"Name\" --team Team[/cyan]")
            return
        console.print(f"\n[bold cyan]{who_owns}[/bold cyan]")
        for k, v in entry.items():
            if v:
                label = k.capitalize()
                console.print(f"  [dim]{label}:[/dim] {v}")
        console.print()
        return

    # ── --search ──────────────────────────────────────────────────────────────
    if search_query:
        results = _search(search_query)
        if not results:
            console.print(f"[dim]No matches for: {search_query}[/dim]")
            return
        table = Table(title=f'Search: "{search_query}"', show_header=True, header_style="bold cyan")
        table.add_column("Project", style="bold white")
        table.add_column("Owner",   style="cyan")
        table.add_column("Team",    style="yellow")
        table.add_column("Domain",  style="dim")
        for r in results:
            table.add_row(r["project"], r.get("owner","—"), r.get("team","—"), r.get("domain","—"))
        console.print(table)
        return

    # ── --list-teams ──────────────────────────────────────────────────────────
    if list_teams_flag:
        teams = list_teams()
        if not teams:
            console.print("[dim]No teams registered.[/dim]")
            console.print("[dim]Add one via:[/dim] [cyan]memoria org-chart --set MyProject --team Backend[/cyan]")
            return
        table = Table(title="Teams", show_header=True, header_style="bold cyan")
        table.add_column("Team",     style="bold white")
        table.add_column("Lead",     style="cyan")
        table.add_column("Slack",    style="dim")
        table.add_column("Projects", style="yellow")
        for t in teams:
            table.add_row(
                t["team"],
                t.get("lead", "—"),
                t.get("slack", "—"),
                str(len(t["projects"])),
            )
        console.print(table)
        return

    # ── --team filter (used without --set) ────────────────────────────────────
    if team and not set_project:
        results = list_by_team(team)
        if not results:
            console.print(f"[dim]No projects registered for team: {team}[/dim]")
            return
        table = Table(title=f"Team: {team}", show_header=True, header_style="bold cyan")
        table.add_column("Project", style="bold white")
        table.add_column("Owner",   style="cyan")
        table.add_column("Email",   style="dim")
        table.add_column("Slack",   style="dim")
        table.add_column("Domain",  style="yellow")
        for r in results:
            table.add_row(r["project"], r.get("owner","—"), r.get("email","—"),
                          r.get("slack","—"), r.get("domain","—"))
        console.print(table)
        return

    # ── --unowned ─────────────────────────────────────────────────────────────
    if unowned:
        missing = list_unowned(resolved_books_dir)
        if not missing:
            console.print("[green]✓[/green] All projects have an owner registered.")
            return
        console.print(f"\n[yellow]⚠[/yellow] [bold]{len(missing)}[/bold] project(s) have no owner:\n")
        for p in missing:
            console.print(f"  [dim]•[/dim] [cyan]{p}[/cyan]")
            console.print(
                f"      [dim]memorua org-chart --set {p} --owner \"Name\" --team Team[/dim]"
            )
        console.print()
        return

    # ── --list (default) ──────────────────────────────────────────────────────
    if show_list or not any([set_project, remove, who_owns, search_query,
                              list_teams_flag, unowned, team]):
        data = all_entries()
        projects = data.get("projects", {})
        if not projects:
            console.print("[dim]Org chart is empty.[/dim]")
            console.print(
                "[dim]Start with:[/dim] [cyan]memoria org-chart --set MyProject "
                "--owner \"Jane Smith\" --team Backend[/cyan]"
            )
            return
        table = Table(title="Org Chart", show_header=True, header_style="bold cyan")
        table.add_column("Project", style="bold white")
        table.add_column("Owner",   style="cyan")
        table.add_column("Team",    style="yellow")
        table.add_column("Email",   style="dim")
        table.add_column("Slack",   style="dim")
        table.add_column("Domain",  style="dim")
        for proj, e in sorted(projects.items()):
            table.add_row(
                proj,
                e.get("owner", "—"), e.get("team",  "—"),
                e.get("email", "—"), e.get("slack",  "—"),
                e.get("domain","—"),
            )
        console.print(table)
        unowned_count = len(list_unowned(resolved_books_dir))
        if unowned_count:
            console.print(
                f"\n[yellow]⚠[/yellow] [bold]{unowned_count}[/bold] project(s) unowned. "
                f"Run [cyan]memoria org-chart --unowned[/cyan] to see them."
            )


# ─── Command: org ─────────────────────────────────────────────

@cli.command()
@click.option("--output",    "-o", default="terminal",
              type=click.Choice(["terminal", "markdown", "json"], case_sensitive=False),
              help="Output format: terminal (default), markdown, or json.")
@click.option("--books-dir", default=None,
              help="Books directory. Defaults to ~/.memoria/books/.")
@click.option("--config",    default=None,
              help="Path to config.yaml. Defaults to ~/.memoria/config.yaml.")
@click.option("--rebuild",   is_flag=True,
              help="Force rebuild even if an org book already exists.")
def org(output, books_dir, config, rebuild):
    """
    Build the Organisation Memory Bank — a meta-book that synthesises ALL
    project Memory Banks into one authoritative picture of your entire org.

    The org book captures: tech landscape, project directory, cross-project
    dependencies, risk radar, onboarding path, and open questions.

    \b
    Examples:
      memoria org                          # build + display in terminal
      memoria org --output markdown        # print raw Markdown
      memoria org --output json            # machine-readable output
      memoria org --rebuild                # force fresh synthesis
    """
    import json as _json
    from .org import build_org_book, build_summaries, render_org_terminal, render_org_json, ORG_BOOK_NAME

    resolved_books_dir = books_dir or _default_books_dir()
    resolved_config    = config or str(Path.home() / ".memoria" / "config.yaml")

    # ── Check if org book already exists and is fresh (< 1 day) ──────────────
    org_path = Path(resolved_books_dir) / ORG_BOOK_NAME
    if org_path.exists() and not rebuild:
        import time as _time
        age_hours = (_time.time() - org_path.stat().st_mtime) / 3600
        if age_hours < 24:
            if output == "terminal":
                render_org_terminal(str(org_path), console)
            elif output == "markdown":
                click.echo(org_path.read_text(encoding="utf-8"))
            else:
                projects, _ = build_summaries(resolved_books_dir)
                click.echo(_json.dumps(render_org_json(projects, str(org_path)), indent=2))
            console.print(
                f"\n[dim]Org book from {age_hours:.0f}h ago. "
                f"Use [bold]--rebuild[/bold] to regenerate.[/dim]"
            )
            return

    # ── Build ─────────────────────────────────────────────────────────────────
    print_banner()
    console.print()

    try:
        with console.status("[cyan]Synthesising org-level Memory Bank…[/cyan]", spinner="dots"):
            result_path = build_org_book(
                config_path  = resolved_config,
                books_dir    = resolved_books_dir,
                on_progress  = lambda s: console.log(f"[cyan]->[/cyan] {s}"),
            )
    except ValueError as e:
        console.print(f"[yellow]⚠[/yellow] {e}")
        return
    except RuntimeError as e:
        console.print(f"[red]✗[/red] {e}")
        return

    console.print(f"\n[bold green]✓[/bold green] Org Memory Bank written → [cyan]{result_path}[/cyan]\n")

    if output == "terminal":
        render_org_terminal(result_path, console)
    elif output == "markdown":
        click.echo(Path(result_path).read_text(encoding="utf-8"))
    else:
        projects, _ = build_summaries(resolved_books_dir)
        click.echo(_json.dumps(render_org_json(projects, result_path), indent=2))


# ─── Command: share ───────────────────────────────────────────

@cli.command()
@click.option("--project",    "-p", multiple=True,  default=None,  help="Scope token to one or more projects. Repeat for multiple. Omit for all-project access.")
@click.option("--expires",    "-e", default=None,   help="Expiry: '7d', '30d', '24h'. Default: no expiry.")
@click.option("--label",      "-l", default="",     help="Human-readable label for this token (e.g. 'Acme consultant').")
@click.option("--revoke",     "-r", default=None,   help="Revoke a specific token (paste the tok_… string).")
@click.option("--revoke-all",       is_flag=True,   help="Revoke all share tokens.")
@click.option("--list",       "show_list", is_flag=True, help="List all active share tokens.")
@click.option("--port",             default=7860,   help="UI server port (used to print the share URL). Default: 7860.")
def share(project, expires, label, revoke, revoke_all, show_list, port):
    """
    Manage read-only share tokens for Consultant Mode.

    Give an external consultant a scoped, read-only URL to your Memoria UI
    without them needing a full Memoria setup.

    \b
    Examples:
      memoria share --project MyApp --expires 7d --label "Acme consultant"
      memoria share --list
      memoria share --revoke tok_abc123
      memoria share --revoke-all
    """
    from .share import (
        generate_token as _gen,
        revoke_token   as _revoke,
        revoke_all     as _revoke_all,
        list_tokens    as _list,
    )

    if revoke_all:
        count = _revoke_all()
        console.print(f"[green]✓[/green] Revoked {count} token(s).")
        return

    if revoke:
        ok = _revoke(revoke)
        if ok:
            console.print(f"[green]✓[/green] Revoked: {revoke}")
        else:
            console.print(f"[yellow]⚠[/yellow] Token not found: {revoke}")
        return

    if show_list:
        tokens = _list()
        if not tokens:
            console.print("[dim]No share tokens found.[/dim]")
            return
        table = Table(title="Share Tokens", show_header=True, header_style="bold cyan")
        table.add_column("Token",      style="cyan",  no_wrap=True)
        table.add_column("Label",      style="white")
        table.add_column("Projects",   style="yellow")
        table.add_column("Expires",    style="white")
        table.add_column("Created",    style="dim")
        table.add_column("Status",     style="green")
        for t in tokens:
            projects_str = ", ".join(t.get("projects") or []) or "[dim]all[/dim]"
            expires_str  = t.get("expires_at", "—") or "—"
            if expires_str != "—":
                expires_str = expires_str[:16].replace("T", " ")
            created_str  = (t.get("created_at") or "")[:10]
            status       = "[red]expired[/red]" if t.get("is_expired") else "[green]active[/green]"
            table.add_row(
                t["token"][:24] + "…",
                t.get("label") or "—",
                projects_str,
                expires_str,
                created_str,
                status,
            )
        console.print(table)
        return

    # ── Generate a new token ─────────────────────────────────────
    projects_list = list(project)   # tuple → list
    try:
        record = _gen(
            projects=projects_list,
            label=label,
            expires=expires,
        )
    except ValueError as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        return

    token_str = record["token"]
    url       = f"http://localhost:{port}/?token={token_str}"

    console.print()
    console.print(Panel.fit(
        f"[bold green]Share token created[/bold green]\n\n"
        f"[bold]Token:[/bold]   [cyan]{token_str}[/cyan]\n"
        f"[bold]URL:[/bold]     [cyan]{url}[/cyan]\n"
        f"[bold]Label:[/bold]   {record.get('label') or '—'}\n"
        f"[bold]Projects:[/bold] {', '.join(projects_list) if projects_list else 'all'}\n"
        f"[bold]Expires:[/bold] {record.get('expires_at', 'never') or 'never'}",
        title="Consultant Mode",
        border_style="green",
    ))
    console.print()
    console.print("[dim]Send this URL to your consultant. They get read-only access — no Memoria setup needed.[/dim]")
    console.print("[dim]Revoke at any time with:[/dim] [cyan]memoria share --revoke " + token_str[:20] + "…[/cyan]")
    console.print()


# ─── Command: sync ────────────────────────────────────────────

@cli.command()
@click.option("--export",       "do_export",    is_flag=True,   help="Export a sync bundle to ~/.memoria/sync/.")
@click.option("--import",       "import_path",  default=None,   metavar="FILE",
              help="Import a peer's sync bundle file into ~/.memoria/sync/.")
@click.option("--merge",        "do_merge",     is_flag=True,   help="Merge all imported bundles into a company-wide view and print a summary.")
@click.option("--status",       "show_status",  is_flag=True,   help="List all sync bundles and who contributed them.")
@click.option("--output",       "-o",           default=None,   metavar="FILE",
              help="Where to write the exported bundle (--export only). Default: ~/.memoria/sync/.")
@click.option("--author",       "-a",           default=None,   help="Your display name (embedded in the bundle).")
@click.option("--since",        default="30d",  show_default=True,
              help="Activity window to export ('7d', '30d', 'all').")
@click.option("--no-activity",  is_flag=True,   help="Exclude activity events from the export.")
@click.option("--no-graph",     is_flag=True,   help="Exclude the knowledge graph from the export.")
@click.option("--no-org-chart", is_flag=True,   help="Exclude org chart data from the export.")
@click.option("--repos",        default=None,
              help="Restrict exported activity to these repo paths (comma-separated).")
@click.option("--books-dir",    default=None,   help="Books directory (for graph export).")
def sync(do_export, import_path, do_merge, show_status, output, author,
         since, no_activity, no_graph, no_org_chart, repos, books_dir):
    """
    Team Sync Layer — share work graph data with your team.

    Privacy first: nothing leaves your machine unless you explicitly run
    `--export`. You control exactly what goes in the bundle.

    \b
    Workflow:
      1.  memoria sync --export --author alice    # export YOUR data
      2.  memoria sync --import alice_sync.json   # import a teammate's bundle
      3.  memoria sync --merge                    # see the merged company view
      4.  memoria sync --status                   # list all imported bundles

    \b
    Examples:
      memoria sync --export
      memoria sync --export --since 7d --no-activity --author alice
      memoria sync --import /path/to/bob_sync.json
      memoria sync --merge
      memoria sync --status
    """
    from .sync import (
        export_bundle   as _export,
        import_bundle   as _import,
        merge_bundles   as _merge,
        sync_status     as _status,
    )

    print_banner()

    if not any([do_export, import_path, do_merge, show_status]):
        console.print(
            "No action specified. Use [cyan]--export[/cyan], [cyan]--import <file>[/cyan], "
            "[cyan]--merge[/cyan], or [cyan]--status[/cyan].\n"
            "Run [cyan]memoria sync --help[/cyan] for details."
        )
        return

    # ── Status ───────────────────────────────────────────────────────────────
    if show_status:
        entries = _status()
        if not entries:
            console.print("[dim]No sync bundles found in ~/.memoria/sync/[/dim]\n"
                          "[dim]Run [cyan]memoria sync --export[/cyan] to create your first one.[/dim]")
            return

        table = Table(title="Sync Bundles", show_header=True, header_style="bold cyan")
        table.add_column("File",        style="dim",    no_wrap=True, max_width=40)
        table.add_column("Author",      style="white")
        table.add_column("Exported",    style="white")
        table.add_column("Events",      justify="right", style="cyan")
        table.add_column("Graph nodes", justify="right", style="cyan")
        table.add_column("Org entries", justify="right", style="cyan")

        for e in entries:
            if "error" in e:
                table.add_row(e["file"], "[red]corrupt[/red]", "—", "—", "—", "—")
                continue
            exp = (e.get("exported_at") or "")[:16].replace("T", " ")
            table.add_row(
                e["file"],
                e.get("exported_by") or "—",
                exp,
                str(e.get("events", 0)),
                str(e.get("graph_nodes", 0)),
                str(e.get("org_entries", 0)),
            )
        console.print(table)
        return

    # ── Export ───────────────────────────────────────────────────────────────
    if do_export:
        if not books_dir:
            books_dir = _default_books_dir()

        repo_list = [r.strip() for r in repos.split(",")] if repos else None

        console.print("\n[bold]Exporting sync bundle…[/bold]\n")
        includes: list[str] = []
        if not no_activity:  includes.append("activity")
        if not no_graph:     includes.append("knowledge graph")
        if not no_org_chart: includes.append("org chart")
        console.print(f"  [dim]Including:[/dim] {', '.join(includes)}")
        console.print(f"  [dim]Window:[/dim]    {since}")
        if author:
            console.print(f"  [dim]Author:[/dim]    {author}")

        try:
            path = _export(
                output_path       = output,
                include_activity  = not no_activity,
                include_graph     = not no_graph,
                include_org_chart = not no_org_chart,
                since             = since,
                author_name       = author,
                repos             = repo_list,
                books_dir         = books_dir,
            )
            size_kb = Path(path).stat().st_size // 1024
            console.print(Panel.fit(
                f"[green]Bundle exported![/green]\n"
                f"[dim]Path:[/dim] {path}\n"
                f"[dim]Size:[/dim] {size_kb} KB\n\n"
                f"Share this file with teammates, then they can run:\n"
                f"[cyan]memoria sync --import {Path(path).name}[/cyan]",
                border_style="green",
            ))
        except Exception as exc:
            console.print(f"[red]✗ Export failed:[/red] {exc}")
        return

    # ── Import ───────────────────────────────────────────────────────────────
    if import_path:
        if not Path(import_path).exists():
            console.print(f"[red]File not found:[/red] {import_path}")
            return
        try:
            b = _import(import_path)
            author_name = b.get("exported_by") or b.get("machine_id", "unknown")
            exported_at = (b.get("exported_at") or "")[:16].replace("T", " ")
            n_events    = len(b.get("activity", []))
            n_nodes     = len(b.get("graph", {}).get("nodes", {}))
            n_org       = len(b.get("org_chart", {}).get("projects", {}))
            console.print(Panel.fit(
                f"[green]Bundle imported![/green]\n"
                f"[dim]Author:[/dim]   {author_name}\n"
                f"[dim]Exported:[/dim] {exported_at}\n"
                f"[dim]Contents:[/dim] {n_events} activity events, "
                f"{n_nodes} graph nodes, {n_org} org chart entries\n\n"
                f"Run [cyan]memoria sync --merge[/cyan] to see the combined team view.",
                border_style="green",
            ))
        except ValueError as exc:
            console.print(f"[red]✗ Import failed:[/red] {exc}")
        return

    # ── Merge ─────────────────────────────────────────────────────────────────
    if do_merge:
        if not books_dir:
            books_dir = _default_books_dir()

        console.print("\n[bold]Merging sync bundles…[/bold]\n")
        try:
            merged = _merge(books_dir=books_dir)
        except Exception as exc:
            console.print(f"[red]✗ Merge failed:[/red] {exc}")
            return

        stats = merged.get("stats", {})
        if stats.get("bundles", 0) == 0:
            console.print("[dim]No sync bundles found. Import some first:[/dim]\n"
                          "[cyan]  memoria sync --import <file>[/cyan]")
            return

        # Contributors table
        contrib_table = Table(title="Contributors", show_header=True, header_style="bold cyan")
        contrib_table.add_column("Name",     style="white")
        contrib_table.add_column("Exported", style="dim")
        contrib_table.add_column("Events",   justify="right", style="cyan")
        for c in merged.get("contributors", []):
            exp = (c.get("exported_at") or "")[:16].replace("T", " ")
            contrib_table.add_row(
                c.get("name", "—"),
                exp,
                str(c.get("activity_events", 0)),
            )
        console.print(contrib_table)
        console.print()

        # Stats summary
        console.print(Panel.fit(
            f"[bold green]Merged company view[/bold green]\n\n"
            f"  [bold]{stats.get('bundles', 0)}[/bold] contributor(s)\n"
            f"  [bold]{stats.get('events', 0)}[/bold] activity event(s)\n"
            f"  [bold]{stats.get('projects', 0)}[/bold] graph project(s)  ·  "
            f"[bold]{stats.get('relationships', 0)}[/bold] relationship(s)\n"
            f"  [bold]{stats.get('org_entries', 0)}[/bold] org chart entrie(s)",
            border_style="green",
        ))

        # Top active repos across all contributors
        if merged.get("activity"):
            from collections import Counter
            repo_counts = Counter(e.get("repo_name", "—") for e in merged["activity"])
            top = repo_counts.most_common(8)
            if top:
                console.print("\n[bold]Most active repos (all contributors):[/bold]")
                for repo, count in top:
                    bar = "█" * min(count // max(1, top[0][1] // 20 or 1), 30)
                    console.print(f"  {repo:<30} {bar} {count}")
    # No output — called in background by shell hook


# ─── Entry Point ──────────────────────────────────────────────

def main():
    cli()


if __name__ == "__main__":
    main()
