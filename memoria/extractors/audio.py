"""
Audio / Video Extractor — transcribes media files using OpenAI Whisper.
Whisper is NOT in requirements.txt by default (it's large + needs ffmpeg).
This module fails gracefully if Whisper isn't installed.

To enable:
    pip install openai-whisper
    # also install ffmpeg: https://ffmpeg.org/download.html

Supported: .mp3  .mp4  .m4a  .wav  .mov
"""

from pathlib import Path

# Whisper model size. "base" is fast + good enough for meeting transcripts.
# Override by setting WHISPER_MODEL env var: tiny | base | small | medium | large
DEFAULT_MODEL = "base"

# Language for transcription. "en" skips language detection and is faster + more accurate
# for English audio. Set WHISPER_LANGUAGE=auto to let Whisper detect automatically.
DEFAULT_LANGUAGE = "en"


def extract_audio(file_path: Path) -> str:
    """
    Transcribe audio/video using Whisper.
    Raises ImportError if whisper is not installed — caller handles gracefully.
    """
    import os
    from pathlib import Path as _Path

    try:
        import whisper
        import warnings
        warnings.filterwarnings("ignore", message="FP16 is not supported on CPU")
    except ImportError:
        raise ImportError(
            "openai-whisper is not installed. "
            "Run: pip install openai-whisper  (also requires ffmpeg)"
        )

    model_name = os.environ.get("WHISPER_MODEL", DEFAULT_MODEL)
    language = os.environ.get("WHISPER_LANGUAGE", DEFAULT_LANGUAGE)
    if language.lower() == "auto":
        language = None   # let Whisper detect

    # Explicit cache dir so the model is never re-downloaded across runs
    cache_dir = _Path.home() / ".cache" / "whisper"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Load model (downloads once, then uses cache_dir on every subsequent run)
    model = whisper.load_model(model_name, download_root=str(cache_dir))

    transcribe_kwargs = {"verbose": False}
    if language:
        transcribe_kwargs["language"] = language

    result = model.transcribe(str(file_path), **transcribe_kwargs)
    text = result.get("text", "").strip()

    if not text:
        return f"[{file_path.name}: no speech detected]"

    # Add segment timestamps if available
    segments = result.get("segments", [])
    if segments:
        lines = []
        for seg in segments:
            start = _fmt_time(seg["start"])
            lines.append(f"[{start}] {seg['text'].strip()}")
        return "\n".join(lines)

    return text


def _fmt_time(seconds: float) -> str:
    """Format seconds as MM:SS."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"
