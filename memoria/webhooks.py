"""
Webhook Server — receives GitHub PR merge events and triggers Memory Bank drafts.
Runs as a FastAPI server. Expose publicly via ngrok for GitHub to reach it.
"""

import hmac
import hashlib
import yaml
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

app = FastAPI(title="Memoria Webhook Server")

# Loaded on startup
_config = {}
_books_dir = "books"
_config_path = "config.yaml"


def load_config(config_path: str = "config.yaml", books_dir: str = "books"):
    global _config, _books_dir, _config_path
    _config_path = config_path
    _books_dir = books_dir
    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            _config = yaml.safe_load(f) or {}


def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC signature."""
    if not secret or not signature:
        return True  # Skip verification if no secret configured
    mac = hmac.new(secret.encode(), payload, hashlib.sha256)
    expected = f"sha256={mac.hexdigest()}"
    return hmac.compare_digest(expected, signature)


def _files_match_filter(changed_files: list, filter_paths: list) -> bool:
    """Check if any changed file matches the configured path filter."""
    if not filter_paths:
        return True  # No filter = always trigger
    for changed in changed_files:
        for f in filter_paths:
            if changed.startswith(f.rstrip("/")):
                return True
    return False


def _draft_update_background(repo_path: str, project_name: str):
    """Run in background after webhook fires."""
    from .generator import BookGenerator
    try:
        generator = BookGenerator(_config_path)
        draft_path = generator.draft_update(
            repo_path=repo_path,
            output_dir=_books_dir,
        )
        print(f"[Memoria] Draft ready: {draft_path}")
        print(f"[Memoria] Run `memoria review --project {project_name}` to approve.")
    except Exception as e:
        print(f"[Memoria] Draft failed: {e}")


@app.post("/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives GitHub webhook events.
    Triggers a Memory Bank draft on PR merge if hook config conditions are met.
    """
    payload_bytes = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    event_type = request.headers.get("X-GitHub-Event", "")

    # Verify signature
    secret = _config.get("webhook_secret", "")
    if not _verify_signature(payload_bytes, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Only handle PR events
    if event_type != "pull_request":
        return JSONResponse({"status": "ignored", "reason": "not a PR event"})

    payload = await request.json()
    action = payload.get("action")
    merged = payload.get("pull_request", {}).get("merged", False)

    # Only trigger on merged PRs
    if action != "closed" or not merged:
        return JSONResponse({"status": "ignored", "reason": "PR not merged"})

    # Check hook config
    hook_config = _config.get("hooks", {}).get("on_pr_merge", {})
    if not hook_config.get("enabled", False):
        return JSONResponse({"status": "ignored", "reason": "on_pr_merge hook disabled"})

    # Check file filter
    changed_files = [
        f["filename"]
        for f in payload.get("pull_request", {}).get("changed_files_detail", [])
    ]
    filter_paths = hook_config.get("only_if_files_changed", [])
    if changed_files and not _files_match_filter(changed_files, filter_paths):
        return JSONResponse({"status": "ignored", "reason": "changed files don't match filter"})

    # Fire background draft
    repo_name = payload.get("repository", {}).get("name", "unknown")
    repo_path = str(Path(".").resolve())  # Default to current dir; override in config
    if "repo_path" in _config:
        repo_path = _config["repo_path"]

    background_tasks.add_task(_draft_update_background, repo_path, repo_name)

    return JSONResponse({
        "status": "triggered",
        "project": repo_name,
        "pr": payload.get("pull_request", {}).get("title"),
    })


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Memoria Webhook Server"}
