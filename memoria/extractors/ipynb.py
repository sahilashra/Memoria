"""
Jupyter Notebook Extractor — pulls code, markdown, and outputs from .ipynb files.
No extra dependencies — notebooks are plain JSON.
Preserves cell structure so the AI understands the narrative flow.
"""

from pathlib import Path
import json

MAX_OUTPUT_CHARS = 500   # cap per-cell output (print statements, error traces, etc.)
MAX_CHARS = 40000


def extract_ipynb(file_path: Path) -> str:
    """Extract all cells from a Jupyter notebook. Returns structured plain text."""

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            nb = json.load(f)
    except json.JSONDecodeError as e:
        return f"[{file_path.name}: invalid notebook JSON — {e}]"

    cells = nb.get("cells", [])
    if not cells:
        return "[Notebook is empty]"

    kernel = nb.get("metadata", {}).get("kernelspec", {}).get("display_name", "")
    language = nb.get("metadata", {}).get("kernelspec", {}).get("language", "python")

    sections = []
    if kernel:
        sections.append(f"[Jupyter Notebook — {kernel}]")

    for i, cell in enumerate(cells):
        cell_type = cell.get("cell_type", "")
        source = "".join(cell.get("source", []))

        if not source.strip():
            continue

        if cell_type == "markdown":
            sections.append(source.strip())

        elif cell_type == "code":
            sections.append(f"```{language}\n{source.strip()}\n```")

            # Include cell outputs (print output, errors, display data)
            outputs = cell.get("outputs", [])
            output_texts = []
            for out in outputs:
                out_type = out.get("output_type", "")

                if out_type in ("stream", "display_data", "execute_result"):
                    text = out.get("text", out.get("data", {}).get("text/plain", []))
                    if isinstance(text, list):
                        text = "".join(text)
                    text = text.strip()
                    if text:
                        output_texts.append(text[:MAX_OUTPUT_CHARS])

                elif out_type == "error":
                    ename = out.get("ename", "Error")
                    evalue = out.get("evalue", "")
                    output_texts.append(f"{ename}: {evalue}")

            if output_texts:
                combined = "\n".join(output_texts)
                sections.append(f"Output:\n{combined}")

        elif cell_type == "raw":
            sections.append(f"[raw]\n{source.strip()}")

    if not sections:
        return "[Notebook contained no extractable content]"

    full_text = "\n\n".join(sections)
    if len(full_text) > MAX_CHARS:
        full_text = full_text[:MAX_CHARS] + "\n\n[... truncated — notebook exceeded limit ...]"

    return full_text
