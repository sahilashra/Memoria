"""
PowerPoint Extractor — pulls text from .pptx files using python-pptx.
Extracts slide titles + body text, notes, and table content.
"""

from pathlib import Path

MAX_CHARS = 40000


def extract_pptx(file_path: Path) -> str:
    """Extract text from a .pptx file. Returns slide-by-slide text."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation(file_path)
    slides_text = []

    for i, slide in enumerate(prs.slides):
        slide_lines = []
        title = None

        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue

            # Detect title placeholder
            if shape.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE — skip
                continue

            shape_text = []
            for para in shape.text_frame.paragraphs:
                text = para.text.strip()
                if text:
                    shape_text.append(text)

            if not shape_text:
                continue

            # Check if it's the title placeholder
            is_title = (
                hasattr(shape, "placeholder_format")
                and shape.placeholder_format is not None
                and shape.placeholder_format.idx == 0
            )

            if is_title:
                title = shape_text[0]
                slide_lines.extend(shape_text)
            else:
                slide_lines.extend(shape_text)

        # Extract table content
        for shape in slide.shapes:
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        slide_lines.append(" | ".join(cells))

        # Extract speaker notes
        if slide.has_notes_slide:
            notes_text = slide.notes_slide.notes_text_frame.text.strip()
            if notes_text:
                slide_lines.append(f"[Notes: {notes_text}]")

        if slide_lines:
            header = f"[Slide {i + 1}]" + (f" — {title}" if title else "")
            slides_text.append(header + "\n" + "\n".join(slide_lines))

    if not slides_text:
        return "[PowerPoint contained no extractable text]"

    full_text = "\n\n".join(slides_text)
    if len(full_text) > MAX_CHARS:
        full_text = full_text[:MAX_CHARS] + "\n\n[... truncated — document exceeded limit ...]"

    return full_text
