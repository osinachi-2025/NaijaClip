from __future__ import annotations

import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    def __init__(self, message: str, *, returncode: int | None = None, stderr_excerpt: str | None = None):
        super().__init__(message)
        self.returncode = returncode
        self.stderr_excerpt = stderr_excerpt


def _stderr_excerpt(stderr: str, limit: int = 4000) -> str:
    normalized = stderr.strip()
    if len(normalized) <= limit:
        return normalized
    head_size = limit // 2
    tail_size = limit - head_size - 5
    return f"{normalized[:head_size]}\n...\n{normalized[-tail_size:]}"


class FFmpeg:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", ffprobe_bin: str = "ffprobe"):
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin

    def probe(self, source: Path) -> dict:
        result = subprocess.run(
            [self.ffprobe_bin, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            excerpt = _stderr_excerpt(result.stderr)
            raise FFmpegError(excerpt or "ffprobe failed", returncode=result.returncode, stderr_excerpt=excerpt)
        return __import__("json").loads(result.stdout)

    def run(self, args: list[str], output: Path) -> Path:
        result = subprocess.run([self.ffmpeg_bin, "-y", *args], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            excerpt = _stderr_excerpt(result.stderr)
            raise FFmpegError(excerpt or "ffmpeg failed", returncode=result.returncode, stderr_excerpt=excerpt)
        if not output.is_file() or output.stat().st_size == 0:
            raise FFmpegError("ffmpeg did not produce a valid output")
        return output

    def extract_audio(self, source: Path, output: Path) -> Path:
        return self.run(["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)], output)

    def extract_clip(self, source: Path, start: float, end: float, output: Path) -> Path:
        return self.run([
            "-ss", str(start),
            "-i", str(source),
            "-t", str(max(0.0, end - start)),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-vf", "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2:flags=lanczos,setsar=1",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-map_metadata", "-1",
            str(output),
        ], output)
