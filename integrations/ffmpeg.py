from __future__ import annotations

import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


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
            raise FFmpegError(result.stderr.strip() or "ffprobe failed")
        return __import__("json").loads(result.stdout)

    def run(self, args: list[str], output: Path) -> Path:
        result = subprocess.run([self.ffmpeg_bin, "-y", *args], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise FFmpegError(result.stderr.strip() or "ffmpeg failed")
        if not output.is_file() or output.stat().st_size == 0:
            raise FFmpegError("ffmpeg did not produce a valid output")
        return output

    def extract_audio(self, source: Path, output: Path) -> Path:
        return self.run(["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)], output)

    def extract_clip(self, source: Path, start: float, end: float, output: Path) -> Path:
        return self.run(["-ss", str(start), "-to", str(end), "-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-c", "copy", str(output)], output)
