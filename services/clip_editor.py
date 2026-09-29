from __future__ import annotations

import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from core.config import STORAGE_BACKEND
from integrations.ffmpeg import FFmpeg
from integrations.storage import LocalStorage, R2Storage
from services.jobs import update_job
from services.subtitles import SUBTITLE_FORCE_STYLE, SUBTITLE_MAX_WIDTH_EM, build_srt, subtitle_width_em


MIN_SEGMENT_SECONDS = 0.08
MAX_EDIT_SEGMENTS = 100
MAX_EDIT_SUBTITLES = 1000
MAX_SUBTITLE_LENGTH = 4000


def normalize_edit_configuration(configuration: dict[str, Any], duration: float) -> dict[str, list[dict[str, Any]]]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("clip duration must be positive")
    if not isinstance(configuration, dict):
        raise ValueError("edit configuration must be an object")

    raw_segments = configuration.get("segments")
    raw_subtitles = configuration.get("subtitles", [])
    if not isinstance(raw_segments, list) or not raw_segments or len(raw_segments) > MAX_EDIT_SEGMENTS:
        raise ValueError("edit must contain between 1 and 100 segments")
    if not isinstance(raw_subtitles, list) or len(raw_subtitles) > MAX_EDIT_SUBTITLES:
        raise ValueError("edit contains too many subtitles")

    segments = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            raise ValueError("each segment must be an object")
        start, end = _time(segment.get("start")), _time(segment.get("end"))
        if start < 0 or end > duration or end - start < MIN_SEGMENT_SECONDS:
            raise ValueError("segment timestamps are outside the clip or too short")
        segments.append({"start": start, "end": end})
    segments.sort(key=lambda segment: segment["start"])
    if any(left["end"] > right["start"] for left, right in zip(segments, segments[1:])):
        raise ValueError("segments cannot overlap")

    subtitles = []
    for cue in raw_subtitles:
        if not isinstance(cue, dict):
            raise ValueError("each subtitle must be an object")
        start, end = _time(cue.get("start")), _time(cue.get("end"))
        text = cue.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_SUBTITLE_LENGTH:
            raise ValueError("subtitle text must contain between 1 and 4000 characters")
        normalized_text = " ".join(text.split())
        if subtitle_width_em(normalized_text) > SUBTITLE_MAX_WIDTH_EM:
            raise ValueError("subtitle is too wide for one line; shorten the text before saving or exporting")
        if start < 0 or end > duration or end - start < 0.04:
            raise ValueError("subtitle timestamps are outside the clip or too short")
        subtitles.append({
            "id": str(cue.get("id") or uuid4()),
            "start": start,
            "end": end,
            "text": normalized_text,
        })
    subtitles.sort(key=lambda cue: (cue["start"], cue["end"]))
    if any(left["end"] > right["start"] for left, right in zip(subtitles, subtitles[1:])):
        raise ValueError("subtitle cues cannot overlap")
    return {"segments": segments, "subtitles": subtitles}


def trim_segment(segments: list[dict[str, Any]], index: int, start: float, end: float) -> list[dict[str, float]]:
    if index < 0 or index >= len(segments):
        raise ValueError("segment does not exist")
    updated = [dict(segment) for segment in segments]
    updated[index] = {"start": _time(start), "end": _time(end)}
    return updated


def split_segment(segments: list[dict[str, Any]], time: float) -> list[dict[str, float]]:
    split_time = _time(time)
    result = []
    split = False
    for segment in segments:
        start, end = float(segment["start"]), float(segment["end"])
        if start + MIN_SEGMENT_SECONDS <= split_time <= end - MIN_SEGMENT_SECONDS:
            result.extend(({"start": start, "end": split_time}, {"start": split_time, "end": end}))
            split = True
        else:
            result.append({"start": start, "end": end})
    if not split:
        raise ValueError("playhead must be inside a segment to split it")
    return result


def remove_section(segments: list[dict[str, Any]], start: float, end: float) -> list[dict[str, float]]:
    remove_start, remove_end = _time(start), _time(end)
    if remove_end - remove_start < MIN_SEGMENT_SECONDS:
        raise ValueError("removed section is too short")
    result = []
    for segment in segments:
        segment_start, segment_end = float(segment["start"]), float(segment["end"])
        if remove_end <= segment_start or remove_start >= segment_end:
            result.append({"start": segment_start, "end": segment_end})
            continue
        if remove_start - segment_start >= MIN_SEGMENT_SECONDS:
            result.append({"start": segment_start, "end": min(remove_start, segment_end)})
        if segment_end - remove_end >= MIN_SEGMENT_SECONDS:
            result.append({"start": max(remove_end, segment_start), "end": segment_end})
    if not result:
        raise ValueError("the edit must keep at least one video section")
    return result


def project_subtitles(configuration: dict[str, Any]) -> list[dict[str, Any]]:
    projected = []
    output_offset = 0.0
    for segment_index, segment in enumerate(configuration["segments"]):
        segment_start, segment_end = float(segment["start"]), float(segment["end"])
        for cue in configuration["subtitles"]:
            start = max(segment_start, float(cue["start"]))
            end = min(segment_end, float(cue["end"]))
            if end - start < 0.04:
                continue
            projected.append({
                "id": f"{cue['id']}:{segment_index}",
                "start": output_offset + start - segment_start,
                "end": output_offset + end - segment_start,
                "text": cue["text"],
            })
        output_offset += segment_end - segment_start
    projected.sort(key=lambda cue: (cue["start"], cue["end"]))
    return projected


def _time(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("timestamps must be numbers") from error
    if not math.isfinite(result):
        raise ValueError("timestamps must be finite")
    return result


def render_clip_edit(
    source: Path,
    output: Path,
    configuration: dict[str, Any],
    duration: float,
    ffmpeg: FFmpeg | None = None,
) -> dict[str, list[dict[str, Any]]]:
    ffmpeg = ffmpeg or FFmpeg()
    normalized = normalize_edit_configuration(configuration, duration)
    media_info = ffmpeg.probe(source)
    video_stream = next((stream for stream in media_info.get("streams", []) if stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in media_info.get("streams", []) if stream.get("codec_type") == "audio"), None)
    if not video_stream or (int(video_stream.get("width", 0)), int(video_stream.get("height", 0))) != (1080, 1920):
        raise ValueError("source clip must be 1080x1920")
    if not audio_stream:
        raise ValueError("source clip has no audio stream")

    output.parent.mkdir(parents=True, exist_ok=True)
    subtitles_path = output.with_suffix(".srt")
    projected_cues = project_subtitles(normalized)
    graph: list[str] = []
    concat_labels = []
    for index, segment in enumerate(normalized["segments"]):
        start, end = segment["start"], segment["end"]
        video_label, audio_label = f"v{index}", f"a{index}"
        graph.append(f"[0:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS[{video_label}]")
        graph.append(f"[0:a:0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[{audio_label}]")
        concat_labels.extend((f"[{video_label}]", f"[{audio_label}]"))
    graph.append(f"{''.join(concat_labels)}concat=n={len(normalized['segments'])}:v=1:a=1[joinedv][joineda]")
    graph.append("[joineda]anull[outa]")
    if projected_cues:
        subtitles_path.write_text(build_srt(projected_cues), encoding="utf-8")
        escaped_path = str(subtitles_path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        graph.append(
            f"[joinedv]subtitles='{escaped_path}':original_size=1080x1920:force_style='{SUBTITLE_FORCE_STYLE}'[outv]"
        )
    else:
        graph.append("[joinedv]null[outv]")

    try:
        ffmpeg.run([
            "-i", str(source),
            "-filter_complex", ";".join(graph),
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(output),
        ], output)
    finally:
        subtitles_path.unlink(missing_ok=True)

    rendered = ffmpeg.probe(output)
    rendered_video = next((stream for stream in rendered.get("streams", []) if stream.get("codec_type") == "video"), None)
    if not rendered_video or (int(rendered_video.get("width", 0)), int(rendered_video.get("height", 0))) != (1080, 1920):
        raise ValueError("edited export is not 1080x1920")
    if not any(stream.get("codec_type") == "audio" for stream in rendered.get("streams", [])):
        raise ValueError("edited export has no audio stream")
    return normalized


def process_clip_export(db: Session, job_id: str) -> None:
    job = db.get(models.ProcessingJob, job_id)
    if job is None or job.job_type != models.JobType.CLIP_RENDER:
        raise LookupError("clip export job not found")
    clip_export = db.scalar(select(models.ClipExport).where(models.ClipExport.processing_job_id == job.id))
    if clip_export is None:
        raise LookupError("clip export record not found")
    clip = clip_export.clip
    video = clip.video
    source_key = clip.editor_source_storage_key or clip.output_storage_key
    if not source_key:
        raise FileNotFoundError("source clip is missing from storage")

    storage = R2Storage() if STORAGE_BACKEND == "r2" else LocalStorage()
    work_dir = Path("/tmp") / "naijaclip" / job.id
    work_dir.mkdir(parents=True, exist_ok=True)
    update_job(db, job.id, status=models.JobStatus.INGESTING, stage="editor_source", progress=10)
    if not storage.exists(source_key):
        raise FileNotFoundError("source clip is missing from storage")
    if STORAGE_BACKEND == "r2":
        source = storage.download_to_path(source_key, work_dir / "source.mp4")
    else:
        source = storage.path_for(source_key)

    output = work_dir / "edited.mp4"
    update_job(db, job.id, status=models.JobStatus.RENDERING, stage="editor_rendering", progress=35)
    duration = float(clip.end_seconds - clip.start_seconds)
    normalized = render_clip_edit(source, output, clip_export.edit_configuration or {}, duration)
    clip_export.edit_configuration = normalized

    key = f"exports/{video.user_id}/{video.id}/edited/{clip.id}/{clip_export.id}.mp4"
    update_job(db, job.id, status=models.JobStatus.EXPORTING, stage="editor_upload", progress=85)
    with output.open("rb") as rendered_file:
        if hasattr(storage, "client"):
            storage.client.upload_fileobj(rendered_file, storage.bucket, key, ExtraArgs={"ContentType": "video/mp4"})
        else:
            destination = storage.path_for(key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as destination_file:
                shutil.copyfileobj(rendered_file, destination_file)

    now = datetime.now(timezone.utc)
    clip_export.status = models.ExportStatus.COMPLETED
    clip_export.output_storage_key = key
    clip_export.output_url = storage.url_for(key)
    clip_export.file_size_bytes = output.stat().st_size
    clip_export.error_message = None
    clip_export.completed_at = now
    job.status = models.JobStatus.COMPLETED
    job.current_stage = "completed"
    job.progress = 100
    job.completed_at = now
    db.commit()