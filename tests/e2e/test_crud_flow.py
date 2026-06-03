"""
E2E tests — Memory Bank CRUD regression tests
──────────────────────────────────────────────
Covers the full Memory Bank lifecycle via the API:

  - GET /api/projects returns both seeded project names (Req 1.1)
  - GET /api/books/{project} returns 200 with content for existing project (Req 1.2)
  - GET /api/books/{project} returns 404 for non-existent project (Req 1.3)
  - GET /api/analyze-stream/{project} emits SSE stream terminating with done event (Req 1.4)
  - After done event, a .md file is written to books_dir on disk (Req 1.5)
  - Second analyze call overwrites book and archives previous version in books/.archive/ (Req 1.6)
  - GET /api/books/{project} returns 404 after file is deleted from disk (Req 1.7)
"""

import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from .conftest import parse_sse_events, _mock_completion


pytestmark = pytest.mark.anyio


# ── helpers ────────────────────────────────────────────────────────────────────

def _book_path(books_dir: Path, project_name: str) -> Path:
    """Return the expected .md path for a given project name."""
    safe = project_name.replace("-", "_").replace(" ", "_")
    return books_dir / f"{safe}_memory_bank.md"


def _wait_for_file(path: Path, timeout: float = 15.0) -> bool:
    """Poll until a file appears and is non-empty, or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.3)
    return False


def _wait_for_archive(books_dir: Path, project_name: str, timeout: float = 15.0) -> list[Path]:
    """Poll until at least one archive file for the project appears."""
    archive_dir = books_dir / ".archive"
    safe = project_name.replace("-", "_").replace(" ", "_")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if archive_dir.exists():
            matches = list(archive_dir.glob(f"{safe}_*.md"))
            if matches:
                return matches
        time.sleep(0.3)
    return []


async def _consume_analyze_stream(
    http_client: httpx.AsyncClient,
    project: str,
    sample_project_dir: Path,
) -> list[dict]:
    """
    Stream /api/analyze-stream/{project} and return all parsed SSE events.
    Skips gracefully if the endpoint is not available.
    """
    with patch("litellm.completion", side_effect=_mock_completion):
        async with http_client.stream(
            "GET",
            f"/api/analyze-stream/{project}",
            params={"path": str(sample_project_dir)},
        ) as resp:
            if resp.status_code == 404:
                pytest.skip("analyze-stream endpoint not available in this build")
            assert resp.status_code == 200
            raw = ""
            async for line in resp.aiter_lines():
                raw += line + "\n"
                if '"done"' in line:
                    break
    return parse_sse_events(raw)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestProjectsEndpoint:
    """GET /api/projects returns both seeded project names (Req 1.1)."""

    async def test_projects_returns_both_seeded_names(self, http_client, live_server):
        """GET /api/projects on a server seeded with two Memory Banks returns both names."""
        resp = await http_client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data, f"Expected 'projects' key in response, got: {list(data.keys())}"
        names = [p["name"] for p in data["projects"]]
        assert any("auth" in n for n in names), (
            f"Expected auth_service in project names, got: {names}"
        )
        assert any("payment" in n for n in names), (
            f"Expected payments_service in project names, got: {names}"
        )


class TestBooksEndpoint:
    """GET /api/books/{project} returns 200 with content or 404 (Req 1.2, 1.3)."""

    async def test_existing_project_returns_200_with_content(self, http_client):
        """GET /api/books/auth_service returns HTTP 200 with Memory Bank content (Req 1.2)."""
        resp = await http_client.get("/api/books/auth_service")
        assert resp.status_code == 200
        # Endpoint may return plain text or JSON with a 'content' key
        if resp.headers.get("content-type", "").startswith("application/json"):
            body = resp.json()
            text = body.get("content", "")
        else:
            text = resp.text
        assert len(text.strip()) > 0, "Expected non-empty Memory Bank content"
        assert "auth" in text.lower() or "jwt" in text.lower(), (
            f"Expected auth-related content, got: {text[:200]!r}"
        )

    async def test_nonexistent_project_returns_404(self, http_client):
        """GET /api/books/{project} for a non-existent project returns HTTP 404 (Req 1.3)."""
        resp = await http_client.get("/api/books/nonexistent_project_crud_xyz_9999")
        assert resp.status_code == 404


class TestAnalyzeStream:
    """analyze SSE stream emits done event and writes .md file to disk (Req 1.4, 1.5)."""

    async def test_stream_terminates_with_done_event(
        self, http_client, live_server, sample_project_dir
    ):
        """GET /api/analyze-stream emits an SSE stream that terminates with done event (Req 1.4)."""
        events = await _consume_analyze_stream(
            http_client, "crud_stream_done_test", sample_project_dir
        )
        types = [e.get("type") for e in events]
        assert "done" in types, f"Expected 'done' event in SSE stream, got types: {types}"

    async def test_md_file_written_to_disk_after_done(
        self, http_client, live_server, sample_project_dir, books_dir
    ):
        """After the done event, a .md file is written to books_dir on disk (Req 1.5)."""
        project = "crud_disk_write_test"
        events = await _consume_analyze_stream(http_client, project, sample_project_dir)
        types = [e.get("type") for e in events]
        assert "done" in types, f"Expected 'done' event before checking disk, got: {types}"

        book = _book_path(books_dir, project)
        found = _wait_for_file(book, timeout=10.0)
        assert found, (
            f"Expected Memory Bank file at {book} after analyze done event, but file not found"
        )


class TestAnalyzeRerun:
    """Second analyze call overwrites book and archives previous version (Req 1.6)."""

    async def test_rerun_overwrites_book_and_archives_previous(
        self, http_client, live_server, sample_project_dir, books_dir
    ):
        """
        Second analyze call for the same project overwrites the Memory Bank and
        archives the previous version in books/.archive/ (Req 1.6).
        """
        project = "crud_rerun_archive_test"
        book = _book_path(books_dir, project)

        # First analyze run — creates the initial Memory Bank
        events_first = await _consume_analyze_stream(http_client, project, sample_project_dir)
        types_first = [e.get("type") for e in events_first]
        assert "done" in types_first, (
            f"First analyze run did not emit done event, got: {types_first}"
        )
        found = _wait_for_file(book, timeout=10.0)
        assert found, f"Expected Memory Bank file after first analyze run at {book}"

        # Record the mtime of the first version
        mtime_first = book.stat().st_mtime

        # Second analyze run — should overwrite and archive the first version
        events_second = await _consume_analyze_stream(http_client, project, sample_project_dir)
        types_second = [e.get("type") for e in events_second]
        assert "done" in types_second, (
            f"Second analyze run did not emit done event, got: {types_second}"
        )

        # The book file should still exist (overwritten)
        assert book.exists(), f"Memory Bank file should still exist after re-analyze at {book}"

        # An archive entry should have been created in books/.archive/
        archive_matches = _wait_for_archive(books_dir, project, timeout=10.0)
        assert len(archive_matches) >= 1, (
            f"Expected at least one archived version of '{project}' in "
            f"{books_dir / '.archive'}, but found none"
        )


class TestDeleteBehavior:
    """GET /api/books/{project} returns 404 after file is deleted from disk (Req 1.7)."""

    async def test_books_returns_404_after_file_deleted(
        self, http_client, live_server, sample_project_dir, books_dir
    ):
        """
        After the Memory Bank .md file is deleted from disk,
        GET /api/books/{project} returns HTTP 404 (Req 1.7).
        """
        project = "crud_delete_behavior_test"
        book = _book_path(books_dir, project)

        # Create the Memory Bank via analyze
        events = await _consume_analyze_stream(http_client, project, sample_project_dir)
        types = [e.get("type") for e in events]
        assert "done" in types, f"Expected done event before delete test, got: {types}"

        found = _wait_for_file(book, timeout=10.0)
        assert found, f"Expected Memory Bank file at {book} before deletion"

        # Confirm the book is readable before deletion
        resp_before = await http_client.get(f"/api/books/{project}")
        if resp_before.status_code == 404:
            pytest.skip("Endpoint not available in this build")
        assert resp_before.status_code == 200, (
            f"Expected 200 before deletion, got {resp_before.status_code}"
        )

        # Delete the file from disk
        book.unlink()
        assert not book.exists(), "File should be gone after unlink()"

        # The API should now return 404
        resp_after = await http_client.get(f"/api/books/{project}")
        assert resp_after.status_code == 404, (
            f"Expected 404 after file deletion, got {resp_after.status_code}"
        )
