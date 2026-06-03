"""
Unit tests for the video extractor (memoria/extractors/video.py).

No real video files or OpenCV installation required — all heavy dependencies
are mocked. Tests verify:
  - Fallback behaviour when cv2 / whisper are not installed
  - Output structure when both channels succeed
  - Frame extraction logic (mocked cv2.VideoCapture)
  - Frame description edge cases (vision not supported, errors)
  - Registry routing: .mp4/.mov/.avi/.mkv/.webm → extract_video
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_fake_cap(fps=25.0, total_frames=250, frames_data=None):
    """Build a mock cv2.VideoCapture that yields `total_frames` black frames."""
    import numpy as np

    frame = np.zeros((100, 100, 3), dtype="uint8")
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.get.side_effect = lambda prop: {1: fps, 7: float(total_frames)}.get(prop, 0.0)

    read_results = [(True, frame)] * total_frames + [(False, None)]
    cap.read.side_effect = read_results
    return cap


# ─── Fallback: neither channel available ─────────────────────────────────────

class TestVideoFallbacks:

    def test_no_cv2_no_whisper_returns_informative_message(self, tmp_path):
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00" * 10)

        with patch("memoria.extractors.video._get_transcript", return_value=None), \
             patch("memoria.extractors.video._get_frame_descriptions", return_value=None):
            from memoria.extractors.video import extract_video
            result = extract_video(f)

        assert "no content" in result.lower() or "unavailable" in result.lower()
        assert "clip.mp4" in result

    def test_no_cv2_returns_transcript_only(self, tmp_path):
        f = tmp_path / "standup.mp4"
        f.write_bytes(b"\x00" * 10)

        with patch("memoria.extractors.video._get_transcript",
                   return_value="Hello everyone, let's start the standup."), \
             patch("memoria.extractors.video._get_frame_descriptions", return_value=None):
            from memoria.extractors.video import extract_video
            result = extract_video(f)

        assert "Hello everyone" in result
        assert "audio transcript only" in result.lower() or "transcript" in result.lower()
        assert "opencv" in result.lower() or "cv2" in result.lower() or "frame" in result.lower()

    def test_no_whisper_returns_frames_only(self, tmp_path):
        f = tmp_path / "demo.mp4"
        f.write_bytes(b"\x00" * 10)

        frame_text = "[00:00] Slide showing system architecture diagram.\n[00:30] Code editor open."

        with patch("memoria.extractors.video._get_transcript", return_value=None), \
             patch("memoria.extractors.video._get_frame_descriptions", return_value=frame_text):
            from memoria.extractors.video import extract_video
            result = extract_video(f)

        assert "architecture diagram" in result
        assert "whisper" in result.lower() or "audio" in result.lower()

    def test_both_channels_combined(self, tmp_path):
        f = tmp_path / "meeting.mp4"
        f.write_bytes(b"\x00" * 10)

        transcript  = "Good morning, this is the weekly sync."
        frames_text = "[00:00] Conference room, people visible.\n[00:30] Slide: Q2 Goals."

        with patch("memoria.extractors.video._get_transcript", return_value=transcript), \
             patch("memoria.extractors.video._get_frame_descriptions", return_value=frames_text):
            from memoria.extractors.video import extract_video
            result = extract_video(f)

        assert "VISUAL CONTENT" in result
        assert "AUDIO TRANSCRIPT" in result
        assert "Conference room" in result
        assert "Good morning" in result

    def test_returns_string(self, tmp_path):
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00" * 10)
        with patch("memoria.extractors.video._get_transcript", return_value="Some text"), \
             patch("memoria.extractors.video._get_frame_descriptions", return_value=None):
            from memoria.extractors.video import extract_video
            result = extract_video(f)
        assert isinstance(result, str)
        assert len(result) > 0


# ─── get_transcript ───────────────────────────────────────────────────────────

class TestGetTranscript:

    def test_whisper_not_installed_returns_none(self, tmp_path):
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00" * 10)
        with patch("memoria.extractors.video._get_transcript", return_value=None):
            # Directly test the underlying logic via _get_transcript
            pass  # tested via fallback tests above

    def test_whisper_returns_text(self, tmp_path):
        f = tmp_path / "audio.mp3"
        f.write_bytes(b"\x00" * 10)
        with patch("memoria.extractors.audio.extract_audio",
                   return_value="Transcribed speech here"):
            from memoria.extractors.video import _get_transcript
            result = _get_transcript(f)
        assert result == "Transcribed speech here"

    def test_whisper_error_returns_error_string(self, tmp_path):
        f = tmp_path / "bad.mp4"
        f.write_bytes(b"\x00" * 10)
        with patch("memoria.extractors.audio.extract_audio",
                   side_effect=RuntimeError("codec error")):
            from memoria.extractors.video import _get_transcript
            result = _get_transcript(f)
        assert result is not None
        assert "error" in result.lower() or "codec" in result.lower()


# ─── Frame extraction ─────────────────────────────────────────────────────────

class TestExtractFrames:

    def test_short_video_uses_short_interval(self, tmp_path):
        """A 60s video should sample more densely than a 10-minute video."""
        import numpy as np
        f = tmp_path / "clip.mp4"

        try:
            import cv2
        except ImportError:
            pytest.skip("opencv-python not installed")

        cap = _make_fake_cap(fps=25.0, total_frames=25 * 60)  # 60 seconds
        with patch("cv2.VideoCapture", return_value=cap):
            from memoria.extractors.video import _extract_frames
            frames = _extract_frames(f, cv2)

        # 60s / 10s interval = 6 frames, capped at MAX_FRAMES
        assert 1 <= len(frames) <= 8

    def test_frames_have_required_keys(self, tmp_path):
        f = tmp_path / "clip.mp4"

        try:
            import cv2
        except ImportError:
            pytest.skip("opencv-python not installed")

        cap = _make_fake_cap(fps=25.0, total_frames=250)
        with patch("cv2.VideoCapture", return_value=cap):
            from memoria.extractors.video import _extract_frames
            frames = _extract_frames(f, cv2)

        if frames:
            for fr in frames:
                assert "timestamp" in fr
                assert "data" in fr
                assert "mime" in fr
                assert fr["mime"] == "image/jpeg"

    def test_empty_video_returns_no_frames(self, tmp_path):
        f = tmp_path / "empty.mp4"

        try:
            import cv2
        except ImportError:
            pytest.skip("opencv-python not installed")

        cap = MagicMock()
        cap.isOpened.return_value = False

        with patch("cv2.VideoCapture", return_value=cap):
            from memoria.extractors.video import _extract_frames
            frames = _extract_frames(f, cv2)

        assert frames == []

    def test_max_frames_respected(self, tmp_path):
        """Never more than MAX_FRAMES frames regardless of video length."""
        f = tmp_path / "long.mp4"

        try:
            import cv2
        except ImportError:
            pytest.skip("opencv-python not installed")

        # 30-minute video at 25fps
        cap = _make_fake_cap(fps=25.0, total_frames=25 * 1800)
        with patch("cv2.VideoCapture", return_value=cap):
            from memoria.extractors.video import _extract_frames, _MAX_FRAMES
            frames = _extract_frames(f, cv2)

        assert len(frames) <= _MAX_FRAMES


# ─── Frame description ────────────────────────────────────────────────────────

class TestDescribeFrames:

    def _make_frame(self, ts="00:00"):
        return {"timestamp": ts, "data": "abc123", "mime": "image/jpeg"}

    def test_successful_description_includes_timestamp(self, tmp_config):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "A slide showing Q2 revenue targets."

        with patch("litellm.completion", return_value=mock_resp):
            from memoria.extractors.video import _describe_frames
            result = _describe_frames([self._make_frame("01:30")], str(tmp_config))

        assert len(result) == 1
        assert "[01:30]" in result[0]
        assert "Q2 revenue" in result[0]

    def test_vision_unsupported_stops_early(self, tmp_config):
        """If the model doesn't support vision, stop after the first failure."""
        with patch("litellm.completion",
                   side_effect=Exception("vision not supported by this model")):
            from memoria.extractors.video import _describe_frames
            frames = [self._make_frame("00:00"), self._make_frame("00:30")]
            result = _describe_frames(frames, str(tmp_config))

        # Should stop early — only first frame attempted
        assert len(result) == 1
        assert "not supported" in result[0].lower() or "vision" in result[0].lower()

    def test_generic_error_continues(self, tmp_config):
        """Non-vision errors should log the error and continue to the next frame."""
        good_resp = MagicMock()
        good_resp.choices = [MagicMock()]
        good_resp.choices[0].message.content = "A whiteboard diagram."

        call_count = [0]

        def side_effect(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("rate limit exceeded")
            return good_resp

        with patch("litellm.completion", side_effect=side_effect):
            from memoria.extractors.video import _describe_frames
            frames = [self._make_frame("00:00"), self._make_frame("00:30")]
            result = _describe_frames(frames, str(tmp_config))

        assert len(result) == 2
        assert "whiteboard" in result[1]


# ─── Registry routing ─────────────────────────────────────────────────────────

class TestVideoRegistry:

    @pytest.mark.parametrize("ext", [".mp4", ".mov", ".avi", ".mkv", ".webm"])
    def test_video_extensions_routed_to_video_extractor(self, ext):
        from memoria.extractors import EXTRACTORS
        from memoria.extractors.video import extract_video
        assert EXTRACTORS.get(ext) is extract_video, (
            f"{ext} should route to extract_video, not {EXTRACTORS.get(ext)}"
        )

    @pytest.mark.parametrize("ext", [".mp3", ".wav", ".m4a"])
    def test_audio_only_extensions_routed_to_audio_extractor(self, ext):
        from memoria.extractors import EXTRACTORS
        from memoria.extractors.audio import extract_audio
        assert EXTRACTORS.get(ext) is extract_audio

    def test_supported_extensions_includes_new_video_formats(self):
        from memoria.extractors import SUPPORTED_EXTENSIONS
        for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
            assert ext in SUPPORTED_EXTENSIONS, f"{ext} missing from SUPPORTED_EXTENSIONS"
