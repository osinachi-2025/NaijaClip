from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from core.config import (
    MAX_UPLOAD_BYTES,
    MAX_VIDEO_DURATION_MINUTES,
    MAX_VIDEO_HEIGHT,
    MAX_VIDEO_WIDTH,
    MIN_VIDEO_DURATION_SECONDS,
)
from integrations.ffmpeg import FFmpeg

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}
SUPPORTED_VIDEO_MIME_PREFIXES = ("video/",)
UNSUPPORTED_AUDIO_CODECS = {"none", "unknown"}


class VideoValidationError(ValueError):
    """User-facing validation error for unsupported or unreadable videos."""


def _normalize_media_info(media_info: dict[str, Any]) -> dict[str, Any]:
    """Normalize FFprobe output into simple metadata."""
    format_info = media_info.get("format", {}) or {}
    streams = media_info.get("streams", []) or []
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)

    duration = float(format_info.get("duration") or video_stream.get("duration") or audio_stream.get("duration") or 0.0)
    width = int(video_stream.get("width") or 0) if video_stream else 0
    height = int(video_stream.get("height") or 0) if video_stream else 0
    fps = video_stream.get("r_frame_rate") if video_stream else "0/1"
    try:
        numerator, denominator = fps.split("/") if isinstance(fps, str) and "/" in fps else ("0", "1")
        fps_value = float(numerator) / float(denominator) if float(denominator) else 0.0
    except (TypeError, ValueError):
        fps_value = 0.0

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps_value,
        "video_codec": (video_stream or {}).get("codec_name"),
        "audio_codec": (audio_stream or {}).get("codec_name"),
        "audio_sample_rate": (audio_stream or {}).get("sample_rate"),
        "audio_channels": (audio_stream or {}).get("channels"),
        "audio_duration": float((audio_stream or {}).get("duration") or 0.0),
        "bit_rate": format_info.get("bit_rate"),
        "container": format_info.get("format_name"),
        "mime_type": format_info.get("format_long_name") or (video_stream or {}).get("codec_name") or "unknown",
        "streams": streams,
    }


def classify_audio_quality(media_info: dict[str, Any]) -> str:
    """Return a coarse audio quality status used to stop unusable transcription jobs."""
    streams = media_info.get("streams", []) or []
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if audio_stream is None:
        return "no_audio"

    codec_name = str(audio_stream.get("codec_name") or "").lower()
    if codec_name in UNSUPPORTED_AUDIO_CODECS or codec_name in {"unknown", "none"}:
        return "audio_unusable"

    duration = float((audio_stream.get("duration") or 0.0) or media_info.get("format", {}).get("duration", 0.0))
    sample_rate = float(audio_stream.get("sample_rate") or 0.0)
    channels = int(audio_stream.get("channels") or 0)
    if duration <= 0:
        return "audio_unusable"
    if sample_rate and sample_rate < 8000:
        return "audio_unusable"
    if channels and channels <= 0:
        return "audio_unusable"
    if duration < 1.0:
        return "audio_unusable"

    # A stream is only declared usable when it has the basic metadata needed for transcription.
    return "audio_usable"


def _extract_file_signature(filename: str, content_type: str | None) -> tuple[str, str | None]:
    suffix = Path(filename).suffix.lower()
    mime_from_ext = SUPPORTED_VIDEO_EXTENSIONS.get(suffix)
    if suffix and mime_from_ext:
        return suffix, mime_from_ext
    guessed, _ = mimetypes.guess_type(filename)
    return suffix, guessed or content_type


def validate_video_file(file_path: str | Path, *, filename: str, content_type: str | None = None, file_size: int | None = None) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise VideoValidationError("We couldn't read this video. The file may be corrupted or encoded in an unsupported format.")

    provided_size = file_size if file_size is not None else path.stat().st_size
    if provided_size <= 0:
        raise VideoValidationError("This video is empty or unreadable.")
    if provided_size > MAX_UPLOAD_BYTES:
        raise VideoValidationError(f"This video is larger than the allowed upload size ({MAX_UPLOAD_BYTES / (1024 * 1024):.0f} MB).")

    suffix, guessed_type = _extract_file_signature(filename, content_type)
    if not suffix or suffix not in SUPPORTED_VIDEO_EXTENSIONS:
        raise VideoValidationError("Your video format isn't supported. Please upload MP4, MOV, or WebM.")

    try:
        media_info = FFmpeg().probe(path)
    except Exception as exc:  # pragma: no cover - external tool failure path
        raise VideoValidationError("We couldn't read this video. The file may be corrupted or encoded in an unsupported format.") from exc

    normalized = _normalize_media_info(media_info)
    if not normalized.get("streams"):
        raise VideoValidationError("We couldn't read this video. The file may be corrupted or encoded in an unsupported format.")

    video_stream = next((stream for stream in normalized["streams"] if stream.get("codec_type") == "video"), None)
    if video_stream is None:
        raise VideoValidationError("This file does not contain a readable video stream.")

    codec_name = (video_stream.get("codec_name") or "").lower()
    if not codec_name:
        raise VideoValidationError("This file does not contain a readable video stream.")

    if guessed_type and not str(guessed_type).startswith(SUPPORTED_VIDEO_MIME_PREFIXES):
        raise VideoValidationError("Your video format isn't supported. Please upload MP4, MOV, or WebM.")

    duration = normalized["duration"]
    width = normalized["width"]
    height = normalized["height"]
    fps = normalized["fps"]
    if duration <= 0:
        raise VideoValidationError("We couldn't read this video. The file may be corrupted or encoded in an unsupported format.")
    if duration > MAX_VIDEO_DURATION_MINUTES * 60:
        raise VideoValidationError(f"This video is longer than the maximum supported duration ({MAX_VIDEO_DURATION_MINUTES} minutes).")
    if width > MAX_VIDEO_WIDTH or height > MAX_VIDEO_HEIGHT:
        raise VideoValidationError("This video resolution is larger than the supported limit.")
    if width <= 0 or height <= 0:
        raise VideoValidationError("This file does not contain a readable video stream.")
    if fps <= 0:
        raise VideoValidationError("This video has an invalid frame rate and can't be processed reliably.")
    if duration < MIN_VIDEO_DURATION_SECONDS:
        raise VideoValidationError(f"This video is too short to produce a useful clip. Minimum length is {MIN_VIDEO_DURATION_SECONDS} seconds.")

    audio_status = classify_audio_quality(media_info)
    if audio_status in {"no_audio", "audio_unusable"}:
        raise VideoValidationError("Your video doesn't appear to contain usable speech audio, so NaijaClips can't generate transcript-based clips from it.")

    normalized["audio_status"] = audio_status
    normalized["source_file_size_bytes"] = provided_size
    normalized["file_extension"] = suffix
    normalized["mime_type"] = guessed_type or "video/mp4"
    return normalized


def preview_validation_result(file_path: str | Path, *, filename: str, content_type: str | None = None, file_size: int | None = None) -> dict[str, Any]:
    """Validate a file and return normalized metadata without storing or mutating it."""
    return validate_video_file(file_path, filename=filename, content_type=content_type, file_size=file_size)


def _write_upload_probe(file_obj: Any) -> tuple[Path, dict[str, Any]]:
    with NamedTemporaryFile(suffix=".tmp", delete=False) as temp_handle:
        temp_handle.write(file_obj.read())
        temp_path = Path(temp_handle.name)
    return temp_path, validate_video_file(temp_path, filename="upload.tmp", content_type="video/mp4")
