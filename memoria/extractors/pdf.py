"""
PDF Extractor — pulls text from PDF files using pdfplumber.
Small PDFs (<= CHUNK_PAGES pages): extracted in full.
Large PDFs (> CHUNK_PAGES pages): chunked by page groups, each chunk
summarised individually, then combined — same pattern as large repos.
"""

from pathlib import Path

CHUNK_PAGES   = 50      # pages per chunk for large PDFs
MAX_CHARS     = 40_000  # per-chunk cap before summarisation kicks in


def extract_pdf(file_path: Path, config_path: str = "config.yaml") -> str:
    """
    Extract text from a PDF file.
    Returns full text for small PDFs, or a chunked summary for large ones.
    """
    import pdfplumber

    with pdfplumber.open(file_path) as pdf:
        total_pages = len(pdf.pages)

        if total_pages == 0:
            return "[PDF has no pages]"

        # ── Small PDF: extract everything ──
        if total_pages <= CHUNK_PAGES:
            return _extract_pages(pdf.pages, 0)

        # ── Large PDF: chunk + summarise ──
        return _extract_large(pdf, file_path.name, total_pages, config_path)


def _extract_pages(pages, start_index: int) -> str:
    """Extract text from a list of pdfplumber page objects."""
    pages_text = []
    for i, page in enumerate(pages):
        text = page.extract_text()
        if text and text.strip():
            pages_text.append(f"[Page {start_index + i + 1}]\n{text.strip()}")

    if not pages_text:
        return ""

    return "\n\n".join(pages_text)


def _extract_large(pdf, filename: str, total_pages: int, config_path: str) -> str:
    """
    Chunk a large PDF into CHUNK_PAGES groups, summarise each chunk,
    then stitch the summaries together.
    """
    try:
        import litellm
        import yaml
        model = _get_model(config_path)
    except ImportError:
        # LiteLLM not available (shouldn't happen — it's a core dep)
        # Fall back to truncated extraction
        text = _extract_pages(pdf.pages[:CHUNK_PAGES], 0)
        return text + f"\n\n[... PDF has {total_pages} pages. Only first {CHUNK_PAGES} shown — litellm unavailable for full summarisation ...]"

    chunk_summaries = []
    pages = pdf.pages
    num_chunks = (total_pages + CHUNK_PAGES - 1) // CHUNK_PAGES

    for chunk_idx in range(num_chunks):
        start = chunk_idx * CHUNK_PAGES
        end   = min(start + CHUNK_PAGES, total_pages)
        chunk_pages = pages[start:end]

        raw_text = _extract_pages(chunk_pages, start)
        if not raw_text.strip():
            continue

        # Cap raw text before sending to model
        if len(raw_text) > MAX_CHARS:
            raw_text = raw_text[:MAX_CHARS] + "\n[... chunk truncated ...]"

        page_range = f"pages {start + 1}–{end}"
        try:
            response = litellm.completion(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are summarising a section of a PDF document for a knowledge base. "
                            "Extract all key information: decisions, facts, processes, data, and insights. "
                            "Preserve specific names, numbers, and dates. Be thorough."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Summarise this section ({page_range}) of '{filename}':\n\n{raw_text}",
                    },
                ],
                max_tokens=1000,
            )
            summary = response.choices[0].message.content.strip()
            chunk_summaries.append(f"[{filename} — {page_range}]\n{summary}")
        except Exception as e:
            # On failure, include the raw text truncated
            chunk_summaries.append(f"[{filename} — {page_range}]\n{raw_text[:2000]}\n[summary failed: {e}]")

    if not chunk_summaries:
        return f"[{filename}: PDF has {total_pages} pages but no extractable text — may be scanned/image-based]"

    header = f"[{filename} — {total_pages} pages, summarised in {len(chunk_summaries)} section(s)]\n\n"
    return header + "\n\n---\n\n".join(chunk_summaries)


def _get_model(config_path: str) -> str:
    """Read model from config.yaml."""
    import yaml
    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8-sig") as f:
            cfg = yaml.safe_load(f) or {}
            model = cfg.get("model", "")
            if model:
                return model
    return "gpt-4o"
