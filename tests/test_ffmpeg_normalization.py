from __future__ import annotations

import shutil
import subprocess

import pytest

from integrations.ffmpeg import FFmpeg
from integrations.ffmpeg import FFmpegError
from services.jobs import classify_failure


def test_ffmpeg_failure_captures_bounded_stderr_and_is_not_retried(monkeypatch, tmp_path):
    failure = "Error initializing filter 'crop': invalid argument " + "x" * 5000
    monkeypatch.setattr(
        "integrations.ffmpeg.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", failure),
    )

    with pytest.raises(FFmpegError) as raised:
        FFmpeg().run(["-filter_complex", "crop=invalid"], tmp_path / "output.mp4")

    assert raised.value.returncode == 1
    assert len(raised.value.stderr_excerpt) == 4000
    assert classify_failure(raised.value) == "permanent"


@pytest.mark.parametrize(
    ("width", "height", "sar", "rotation", "pixel_format", "expected"),
    [
        (320, 180, "1/1", 0, "yuv420p", (320, 180)),
        (180, 320, "1/1", 0, "yuv420p", (180, 320)),
        (240, 240, "1/1", 0, "yuv420p", (240, 240)),
        (321, 181, "1/1", 0, "yuv444p", (320, 180)),
        (320, 180, "2/1", 0, "yuv420p", (640, 180)),
        (320, 180, "1/1", 90, "yuv420p", (180, 320)),
    ],
)
def test_selected_clip_normalization_handles_dimensions_rotation_and_sar(
    tmp_path, width, height, sar, rotation, pixel_format, expected
):
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        pytest.skip("FFmpeg and ffprobe are required for selected-clip normalization")

    source = tmp_path / "source.mp4"
    output = tmp_path / "selected.mp4"
    command = [
        ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "1",
        "-vf", f"setsar={sar}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", pixel_format,
        "-c:a", "aac",
    ]
    command.append(str(source))
    subprocess.run(command, check=True, capture_output=True)
    if rotation:
        rotated_source = tmp_path / "rotated-source.mp4"
        subprocess.run([
            ffmpeg_bin, "-y", "-display_rotation", str(rotation),
            "-i", str(source), "-c", "copy", str(rotated_source),
        ], check=True, capture_output=True)
        source = rotated_source

    ffmpeg = FFmpeg()
    if rotation:
        source_video = next(stream for stream in ffmpeg.probe(source)["streams"] if stream["codec_type"] == "video")
        assert source_video.get("side_data_list")
    ffmpeg.extract_clip(source, 0, 0.8, output)
    details = ffmpeg.probe(output)
    video = next(stream for stream in details["streams"] if stream["codec_type"] == "video")

    assert (video["width"], video["height"]) == expected
    assert video.get("sample_aspect_ratio") in {None, "1:1"}
    assert not video.get("side_data_list")
    assert any(stream["codec_type"] == "audio" for stream in details["streams"])
    subprocess.run([ffmpeg_bin, "-v", "error", "-i", str(output), "-f", "null", "-"], check=True, capture_output=True)