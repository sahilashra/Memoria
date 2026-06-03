"""
Spreadsheet Extractor — pulls data from .xlsx / .xls / .csv files.
Uses openpyxl for Excel, csv stdlib for CSVs.
Each sheet becomes a named section. Empty rows skipped.
"""

from pathlib import Path

MAX_CHARS = 40000
MAX_ROWS_PER_SHEET = 500   # avoid dumping 100K-row data files
MAX_COLS = 20               # skip very wide sheets sensibly


def extract_xlsx(file_path: Path) -> str:
    """Extract text from spreadsheet. Returns tabular data as plain text."""
    ext = file_path.suffix.lower()

    if ext == ".csv":
        return _extract_csv(file_path)
    else:
        return _extract_excel(file_path)


def _extract_excel(file_path: Path) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    sections = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows_text = []
        row_count = 0

        for row in ws.iter_rows(values_only=True, max_col=MAX_COLS):
            if row_count >= MAX_ROWS_PER_SHEET:
                rows_text.append(f"[... {ws.max_row - MAX_ROWS_PER_SHEET} more rows not shown ...]")
                break

            cells = [str(c).strip() if c is not None else "" for c in row]
            line = " | ".join(cells)
            if line.strip(" |"):   # skip fully empty rows
                rows_text.append(line)
                row_count += 1

        if rows_text:
            sections.append(f"[Sheet: {sheet_name}]\n" + "\n".join(rows_text))

    wb.close()

    if not sections:
        return "[Spreadsheet contained no data]"

    full_text = "\n\n".join(sections)
    if len(full_text) > MAX_CHARS:
        full_text = full_text[:MAX_CHARS] + "\n\n[... truncated — document exceeded limit ...]"

    return full_text


def _extract_csv(file_path: Path) -> str:
    import csv

    rows_text = []
    row_count = 0

    with open(file_path, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if row_count >= MAX_ROWS_PER_SHEET:
                rows_text.append(f"[... more rows not shown ...]")
                break
            line = " | ".join(str(c).strip() for c in row[:MAX_COLS])
            if line.strip(" |"):
                rows_text.append(line)
                row_count += 1

    if not rows_text:
        return "[CSV contained no data]"

    full_text = "\n".join(rows_text)
    if len(full_text) > MAX_CHARS:
        full_text = full_text[:MAX_CHARS] + "\n\n[... truncated ...]"

    return full_text
