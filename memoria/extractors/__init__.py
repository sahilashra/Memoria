"""
Rich Format Extractors — pull text from non-code files.
Each extractor is independent. Missing optional dependencies
are handled gracefully — the file is noted but not crashed on.

Supported:
  Documents  — PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx), HTML
  Notebooks  — Jupyter (.ipynb)
  Images     — PNG, JPG, WEBP, GIF, BMP  (via vision model)
  Audio      — MP3, WAV, M4A             (via Whisper — optional)
  Video      — MP4, MOV, AVI, MKV, WEBM  (Whisper audio + cv2 visual frames)
"""

from pathlib import Path
from .pdf   import extract_pdf
from .docx  import extract_docx
from .pptx  import extract_pptx
from .xlsx  import extract_xlsx
from .audio import extract_audio
from .video import extract_video
from .image import extract_image
from .ipynb import extract_ipynb
from .html  import extract_html

# Map extensions to extractor functions
EXTRACTORS = {
    # Documents
    ".pdf":  extract_pdf,
    ".docx": extract_docx,
    ".doc":  extract_docx,
    ".pptx": extract_pptx,
    ".ppt":  extract_pptx,
    ".xlsx": extract_xlsx,
    ".xls":  extract_xlsx,
    ".csv":  extract_xlsx,
    # Web / wiki exports
    ".html": extract_html,
    ".htm":  extract_html,
    # Notebooks
    ".ipynb": extract_ipynb,
    # Images
    ".png":  extract_image,
    ".jpg":  extract_image,
    ".jpeg": extract_image,
    ".gif":  extract_image,
    ".webp": extract_image,
    ".bmp":  extract_image,
    # Audio-only (no visual track)
    ".mp3":  extract_audio,
    ".m4a":  extract_audio,
    ".wav":  extract_audio,
    # Video — audio transcript + visual frame analysis
    ".mp4":  extract_video,
    ".mov":  extract_video,
    ".avi":  extract_video,
    ".mkv":  extract_video,
    ".webm": extract_video,
}

# File types this package can handle
SUPPORTED_EXTENSIONS = set(EXTRACTORS.keys())


def extract(file_path: Path, config_path: str = "config.yaml") -> dict:
    """
    Extract text from any supported file.
    Returns: {content, extension, truncated, extractor}
    Falls back gracefully if extractor is unavailable.
    """
    ext = file_path.suffix.lower()
    extractor_fn = EXTRACTORS.get(ext)

    if not extractor_fn:
        return {
            "content": f"[No extractor for {ext}]",
            "extension": ext,
            "truncated": False,
            "extractor": "none",
        }

    try:
        # Image and PDF extractors accept an optional config_path
        import inspect
        sig = inspect.signature(extractor_fn)
        if "config_path" in sig.parameters:
            content = extractor_fn(file_path, config_path=config_path)
        else:
            content = extractor_fn(file_path)

        return {
            "content": content,
            "extension": ext,
            "truncated": len(content) > 40_000,
            "extractor": extractor_fn.__module__.split(".")[-1],
        }
    except ImportError as e:
        return {
            "content": f"[{file_path.name}: extractor not installed — {e}]",
            "extension": ext,
            "truncated": False,
            "extractor": "missing",
        }
    except Exception as e:
        return {
            "content": f"[{file_path.name}: extraction failed — {e}]",
            "extension": ext,
            "truncated": False,
            "extractor": "error",
        }
