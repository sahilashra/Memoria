"""
Unit tests for RepoCrawler.
Tests directory walking, ignore rules, single-file mode, and stats.
No AI calls — the crawler only reads files.
"""

import pytest
from pathlib import Path
from memoria.crawler import RepoCrawler


class TestCrawlerBasic:

    def test_crawls_code_project(self, sample_code_project, tmp_config):
        crawler = RepoCrawler(str(sample_code_project), str(tmp_config))
        data = crawler.crawl()
        assert data["name"] == "my_project"
        assert data["stats"]["total_files"] > 0
        assert len(data["files"]) > 0

    def test_finds_python_files(self, sample_code_project, tmp_config):
        crawler = RepoCrawler(str(sample_code_project), str(tmp_config))
        data = crawler.crawl()
        file_keys = list(data["files"].keys())
        assert any("main.py" in k for k in file_keys)
        assert any("utils.py" in k for k in file_keys)

    def test_structure_is_string(self, sample_code_project, tmp_config):
        crawler = RepoCrawler(str(sample_code_project), str(tmp_config))
        data = crawler.crawl()
        assert isinstance(data["structure"], str)
        assert len(data["structure"]) > 0

    def test_returns_required_keys(self, sample_code_project, tmp_config):
        crawler = RepoCrawler(str(sample_code_project), str(tmp_config))
        data = crawler.crawl()
        assert all(k in data for k in ("name", "path", "structure", "files", "stats"))

    def test_stats_has_required_fields(self, sample_code_project, tmp_config):
        crawler = RepoCrawler(str(sample_code_project), str(tmp_config))
        data = crawler.crawl()
        assert "total_files" in data["stats"]
        assert "skipped_large" in data["stats"]
        assert "skipped_binary" in data["stats"]


class TestCrawlerIgnoreRules:

    def test_ignores_node_modules(self, tmp_path, tmp_config):
        (tmp_path / "index.js").write_text("console.log('hi')", encoding="utf-8")
        nm = tmp_path / "node_modules" / "some-lib"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module noise", encoding="utf-8")

        crawler = RepoCrawler(str(tmp_path), str(tmp_config))
        data = crawler.crawl()
        assert not any("node_modules" in k for k in data["files"])

    def test_ignores_pycache(self, tmp_path, tmp_config):
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "main.cpython-311.pyc").write_bytes(b"\x00\x01\x02\x03")

        crawler = RepoCrawler(str(tmp_path), str(tmp_config))
        data = crawler.crawl()
        assert not any("__pycache__" in k for k in data["files"])
        assert not any(".pyc" in k for k in data["files"])

    def test_ignores_binary_files(self, tmp_path, tmp_config):
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "app.exe").write_bytes(b"\x4d\x5a\x90\x00")  # PE header

        crawler = RepoCrawler(str(tmp_path), str(tmp_config))
        data = crawler.crawl()
        assert not any(".exe" in k for k in data["files"])
        assert any(".py" in k for k in data["files"])

    def test_ignores_large_files(self, tmp_path, tmp_config):
        # Create a file larger than DEFAULT_MAX_FILE_SIZE (50KB)
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        big = tmp_path / "big_file.py"
        big.write_text("x = 1\n" * 10_000, encoding="utf-8")  # ~60KB

        crawler = RepoCrawler(str(tmp_path), str(tmp_config))
        data = crawler.crawl()
        assert data["stats"]["skipped_large"] >= 1

    def test_custom_ignore_dirs_from_config(self, tmp_path):
        # Config that ignores 'vendor' dir
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: test\nignore_dirs:\n  - vendor\n", encoding="utf-8")
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / "main.py").write_text("x = 1", encoding="utf-8")
        vendor = proj / "vendor"
        vendor.mkdir()
        (vendor / "lib.py").write_text("vendor code", encoding="utf-8")

        crawler = RepoCrawler(str(proj), str(cfg))
        data = crawler.crawl()
        assert not any("vendor" in k for k in data["files"])


class TestCrawlerSingleFile:

    def test_single_py_file(self, tmp_path, tmp_config):
        f = tmp_path / "script.py"
        f.write_text("print('hello')", encoding="utf-8")
        crawler = RepoCrawler(str(f), str(tmp_config))
        data = crawler.crawl()
        assert data["name"] == "script"
        assert data["stats"]["total_files"] == 1

    def test_single_ipynb_file(self, tmp_path, tmp_config):
        import json
        nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
            {"cell_type": "code", "source": ["x = 1"], "outputs": []}
        ]}
        f = tmp_path / "analysis.ipynb"
        f.write_text(json.dumps(nb), encoding="utf-8")
        crawler = RepoCrawler(str(f), str(tmp_config))
        data = crawler.crawl()
        assert data["name"] == "analysis"

    def test_single_html_file(self, tmp_path, tmp_config):
        f = tmp_path / "page.html"
        f.write_text("<html><body><p>Hello</p></body></html>", encoding="utf-8")
        crawler = RepoCrawler(str(f), str(tmp_config))
        data = crawler.crawl()
        assert data["name"] == "page"
        assert data["stats"]["total_files"] == 1


class TestCrawlerChunking:

    def test_get_chunked_files_returns_list(self, sample_code_project, tmp_config):
        crawler = RepoCrawler(str(sample_code_project), str(tmp_config))
        crawler.crawl()
        chunks = crawler.get_chunked_files()
        assert isinstance(chunks, list)
        assert len(chunks) >= 1
        # Each chunk is a dict of filename -> content
        for chunk in chunks:
            assert isinstance(chunk, dict)
