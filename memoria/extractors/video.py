"""
Video Extractor — combines Whisper audio transcription with AI vision frame analysis.

Two independent channels:
  1. Audio track   → Whisper transcription (same as audio extractor)
  2. Visual frames → key frames sampled at adaptive intervals, described by vision model

Each channel degrades independently:
  - cv2 not installed  → returns transcript only (with install hint)
  - whisper not installed → returns frame descriptions only
  - both missing       → returns informative error message
  - vision model can't do images → frame descriptions show a helpful hint

Supported extensions: .mp4  .mov  .avi  .mkv  .webm

Install:
    pip install opencv-python   # frame extraction
    pip install openai-whisper  # audio transcription (also requires ffmpeg)
"""

import base64
from pathlib import Path
from typing import Optional

# ─── Frame sampling constants ────────────────────────────────────────────────

# Adaptive interval: one frame every N seconds, chosen based on video length
_INTERVAL_SHORT  = 10   # < 2 minutes  → every 10s
_INTERVAL_MEDIUM = 30   # 2–10 minutes → every 30s
_INTERVAL_LONG   = 60   # > 10 minutes → every 60s
_MAX_FRAMES      = 8    # never send more than 8 frames to the vision model

# JPEG quality for extracted frames (lower = smaller payload, faster API call)
_JPEG_QUALITY = 70

_FRAME_PROMPT = (
    "This is a frame from a video file at timestamp {ts}. "
    "Describe what is visible in 2-3 sentences: slide content, text on screen, "
    "UI elements, diagrams, whiteboard content, people, or any other relevant visual context. "
    "Be specific and concise."
)


# ─── Public entry point ───────────────────────────────────────────────────────

def extract_video(file_path: Path, config_path: str = "config.yaml") -> str:
    """
    Extract content from a video file.

    Tries both audio transcription and visual frame analysis in parallel.
    Returns a structured text combining whichever channels succeed.
    """
    transcript    = _get_transcript(file_path)
    frames_text   = _get_frame_descriptions(file_path, config_path)

    name = file_path.name

    if frames_text and transcript:
        return (
            f"[Video: {name}]\n\n"
            f"=== VISUAL CONTENT (frame-by-frame) ===\n{frames_text}\n\n"
            f"=== AUDIO TRANSCRIPT ===\n{transcript}"
        )
    elif frames_text:
        return (
            f"[Video: {name} — visual frames only; "
            f"install openai-whisper + ffmpeg for audio transcription]\n\n"
            f"=== VISUAL CONTENT ===\n{frames_text}"
        )
    elif transcript:
        return (
            f"[Video: {name} — audio transcript only; "
            f"install opencv-python for visual frame analysis]\n\n"
            f"{transcript}"
        )
    else:
        return (
            f"[Video: {name}: no content could be extracted. "
            f"Install openai-whisper (+ ffmpeg) for audio and opencv-python for visual frames.]"
        )


# ─── Channel 1: Audio transcript ─────────────────────────────────────────────

def _get_transcript(file_path: Path) -> Optional[str]:
    """Transcribe the audio track via Whisper. Returns None if whisper is not installed."""
    try:
        from .audio import extract_audio
        return extract_audio(file_path)
    except ImportError:
        return None
    except Exception as e:
        return f"[Transcript error: {e}]"


# ─── Channel 2: Visual frames ─────────────────────────────────────────────────

def _get_frame_descriptions(file_path: Path, config_path: str) -> Optional[str]:
    """
    Extract and describe key frames via the vision model.
    Returns None if opencv-python is not installed (no crash — caller handles gracefully).
    """
    try:
        import cv2  # noqa: F401
    except ImportError:
        return None

    try:
        import cv2 as _cv2
        frames = _extract_frames(file_path, _cv2)
        if not frames:
            return None
        descriptions = _describe_frames(frames, config_path)
        return "\n".join(descriptions) if descriptions else None
    except Exception as e:
        return f"[Frame extraction error: {e}]"


def _extract_frames(file_path: Path, cv2) -> list:
    """
    Sample evenly-spaced key frames from the video.
    Returns a list of dicts: {timestamp: "MM:SS", data: "<b64 jpeg>", mime: "image/jpeg"}.
    """
    cap = cv2.VideoCapture(str(file_path))
    if not cap.isOpened():
        return []

    fps          = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s   = total_frames / fps

    # Pick interval based on video length
    if duration_s < 120:
        interval_s = _INTERVAL_SHORT
    elif duration_s < 600:
        interval_s = _INTERVAL_MEDIUM
    else:
        interval_s = _INTERVAL_LONG

    interval_frames = max(int(fps * interval_s), 1)
    frames: list = []
    idx = 0

    while cap.isOpened() and len(frames) < _MAX_FRAMES:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % interval_frames == 0:
            _, buf = cv2.imencode(
                ".jpg", frame,
                [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY]
            )
            b64 = base64.standard_b64encode(buf.tobytes()).decode("utf-8")
            ts_s = idx / fps
            m, s = int(ts_s // 60), int(ts_s % 60)
            frames.append({
                "timestamp": f"{m:02d}:{s:02d}",
                "data":      b64,
                "mime":      "image/jpeg",
            })
        idx += 1

    cap.release()
    return frames


def _describe_frames(frames: list, config_path: str) -> list:
    """
    Call the configured vision model to describe each frame.
    Stops early if the model doesn't support vision (returns a hint instead of crashing).
    """
    import litellm
    from .image import _get_model

    model        = _get_model(config_path)
    descriptions = []

    for f in frames:
        data_url = f"data:{f['mime']};base64,{f['data']}"
        prompt   = _FRAME_PROMPT.format(ts=f["timestamp"])

        try:
            resp = litellm.completion(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }],
                max_tokens=200,
            )
            desc = resp.choices[0].message.content.strip()
            descriptions.append(f"[{f['timestamp']}] {desc}")

        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in ("vision", "unsupported", "multimodal", "image")):
                # Model doesn't support vision — note it and stop sending more frames
                descriptions.append(
                    f"[{f['timestamp']}] [Vision not supported by '{model}' — "
                    f"switch to claude-3, gpt-4o, or gemini in config.yaml]"
                )
                break
            else:
                descriptions.append(f"[{f['timestamp']}] [Frame description failed: {e}]")

    return descriptions
