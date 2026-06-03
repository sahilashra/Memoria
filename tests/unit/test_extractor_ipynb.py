"""
Unit tests for the Jupyter notebook extractor.
No extra dependencies — notebooks are plain JSON.
All test notebooks are created in-memory (no fixture files needed).
"""

import json
import pytest
from pathlib import Path
from memoria.extractors.ipynb import extract_ipynb


def _write_notebook(tmp_path, cells, kernel="python3", language="python"):
    """Helper: write a .ipynb file and return its Path."""
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": kernel, "language": language}
        },
        "cells": cells,
    }
    path = tmp_path / "test_notebook.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    return path


class TestExtractIpynb:

    def test_basic_code_cell(self, tmp_path):
        cells = [{"cell_type": "code", "source": ["import pandas as pd\ndf = pd.read_csv('data.csv')"], "outputs": []}]
        path = _write_notebook(tmp_path, cells)
        result = extract_ipynb(path)
        assert "import pandas" in result
        assert "```python" in result

    def test_basic_markdown_cell(self, tmp_path):
        cells = [{"cell_type": "markdown", "source": ["## Data Analysis\nThis notebook analyzes sales data."]}]
        path = _write_notebook(tmp_path, cells)
        result = extract_ipynb(path)
        assert "## Data Analysis" in result
        assert "analyzes sales data" in result

    def test_code_and_markdown_together(self, tmp_path):
        cells = [
            {"cell_type": "markdown", "source": ["# Introduction\nLoad and explore the dataset."]},
            {"cell_type": "code", "source": ["import pandas as pd"], "outputs": []},
        ]
        path = _write_notebook(tmp_path, cells)
        result = extract_ipynb(path)
        assert "# Introduction" in result
        assert "import pandas" in result

    def test_cell_output_included(self, tmp_path):
        cells = [{
            "cell_type": "code",
            "source": ["print('hello world')"],
            "outputs": [{"output_type": "stream", "text": ["hello world\n"]}],
        }]
        path = _write_notebook(tmp_path, cells)
        result = extract_ipynb(path)
        assert "hello world" in result
        assert "Output:" in result

    def test_error_output_included(self, tmp_path):
        cells = [{
            "cell_type": "code",
            "source": ["1/0"],
            "outputs": [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero", "traceback": []}],
        }]
        path = _write_notebook(tmp_path, cells)
        result = extract_ipynb(path)
        assert "ZeroDivisionError" in result

    def test_empty_notebook_returns_message(self, tmp_path):
        path = _write_notebook(tmp_path, [])
        result = extract_ipynb(path)
        assert "empty" in result.lower()

    def test_kernel_name_in_output(self, tmp_path):
        cells = [{"cell_type": "code", "source": ["x = 1"], "outputs": []}]
        path = _write_notebook(tmp_path, cells, kernel="Python 3 (ipykernel)")
        result = extract_ipynb(path)
        assert "Python 3" in result

    def test_empty_cells_skipped(self, tmp_path):
        cells = [
            {"cell_type": "code", "source": [], "outputs": []},          # empty
            {"cell_type": "markdown", "source": ["   "]},                 # whitespace only
            {"cell_type": "code", "source": ["x = 42"], "outputs": []},  # real
        ]
        path = _write_notebook(tmp_path, cells)
        result = extract_ipynb(path)
        assert "x = 42" in result
        # The empty cell content should not inflate the result
        assert result.count("```") == 2  # one open, one close

    def test_invalid_json_returns_error_message(self, tmp_path):
        path = tmp_path / "bad.ipynb"
        path.write_text("this is not json {{{", encoding="utf-8")
        result = extract_ipynb(path)
        assert "invalid" in result.lower() or "error" in result.lower()

    def test_large_output_truncated(self, tmp_path):
        # Output longer than MAX_OUTPUT_CHARS should be cut
        long_output = "x" * 2000
        cells = [{
            "cell_type": "code",
            "source": ["print('x' * 2000)"],
            "outputs": [{"output_type": "stream", "text": [long_output]}],
        }]
        path = _write_notebook(tmp_path, cells)
        result = extract_ipynb(path)
        # Should contain output but not the full 2000 chars
        assert "Output:" in result
        assert len(result) < len(long_output) + 500  # truncated

    def test_raw_cell_included(self, tmp_path):
        cells = [{"cell_type": "raw", "source": ["This is raw RST content"]}]
        path = _write_notebook(tmp_path, cells)
        result = extract_ipynb(path)
        assert "raw RST content" in result

    def test_r_language_notebook(self, tmp_path):
        cells = [{"cell_type": "code", "source": ["df <- read.csv('data.csv')"], "outputs": []}]
        path = _write_notebook(tmp_path, cells, kernel="R", language="R")
        result = extract_ipynb(path)
        assert "```R" in result
        assert "read.csv" in result
