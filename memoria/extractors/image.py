"""
Image Extractor — describes images using a vision-capable AI model.
Uses the same model configured in config.yaml via LiteLLM.
Works with Claude, GPT-4o, Gemini (all support vision natively).

Supported: .png  .jpg  .jpeg  .gif  .webp  .bmp
"""

from pathlib import Path

# Prompt sent to the vision model for every image
VISION_PROMPT = (
    "Analyse this image thoroughly and extract everything useful:\n\n"
    "1. TEXT — transcribe all visible text exactly as written\n"
    "2. DIAGRAMS — if it's an architecture/flow/ER diagram, describe the components "
    "and their relationships\n"
    "3. SCREENSHOTS — if it's a UI/dashboard screenshot, describe what's shown "
    "and any important data\n"
    "4. CHARTS/GRAPHS — if it contains data visualisation, describe the data and trends\n"
    "5. GENERAL — any other relevant visual information\n\n"
    "Be specific and thorough. This description will be used to build a knowledge base."
)

MIME_MAP = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".bmp":  "image/bmp",
}


def extract_image(file_path: Path, config_path: str = "config.yaml") -> str:
    """
    Describe an image using a vision model via LiteLLM.
    Reads model from config.yaml — same model the rest of Memoria uses.
    Falls back gracefully if the model doesn't support vision.
    """
    import base64
    import litellm
    import yaml

    # ── Load model from config ──
    model = _get_model(config_path)

    # ── Encode image ──
    ext = file_path.suffix.lower()
    mime = MIME_MAP.get(ext, "image/jpeg")
    image_bytes = file_path.read_bytes()

    # Warn on very large images — resize hint
    size_kb = len(image_bytes) // 1024
    if size_kb > 5000:
        return (
            f"[{file_path.name}: image is {size_kb}KB — too large to send to vision model. "
            f"Resize to under 5MB and re-run.]"
        )

    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{image_b64}"

    # ── Call vision model ──
    # Timeout: local models (Ollama) are slow with images — cap at 120s
    timeout = 120 if "ollama" in model.lower() else 60

    try:
        response = litellm.completion(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": VISION_PROMPT},
                ],
            }],
            max_tokens=1500,
            timeout=timeout,
        )
        description = response.choices[0].message.content.strip()
        return f"[Image: {file_path.name}]\n{description}"

    except Exception as e:
        err = str(e).lower()
        if "vision" in err or "image" in err or "multimodal" in err or "unsupported" in err:
            return (
                f"[{file_path.name}: vision not supported by model '{model}'. "
                f"Switch to claude-3, gpt-4o, or gemini in config.yaml]"
            )
        if "timeout" in err or "timed out" in err:
            return (
                f"[{file_path.name}: vision model timed out after {timeout}s. "
                f"Image may be too large for local model.]"
            )
        raise  # let __init__.py handle other errors


def _get_model(config_path: str) -> str:
    """Read model name from config.yaml."""
    import yaml
    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8-sig") as f:
            cfg = yaml.safe_load(f) or {}
            model = cfg.get("model", "")
            if model:
                return model
    # Sensible default — all three major providers support vision
    return "gpt-4o"
