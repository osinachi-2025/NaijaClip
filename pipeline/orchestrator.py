from __future__ import annotations

from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from models import JobStatus, VideoStatus
from services.jobs import ensure_job_active, update_job
from core.config import STORAGE_BACKEND
from integrations.ffmpeg import FFmpeg
from integrations.storage import LocalStorage, R2Storage
from .context import PipelineContext
from .stages import PipelineStages


class PipelineOrchestrator:
    def __init__(self, db: Session, stage_handlers: dict[str, Callable[[PipelineContext], None]] | None = None):
        self.db = db
        self.stage_handlers = stage_handlers or {}
        self.storage = R2Storage() if STORAGE_BACKEND == "r2" else LocalStorage()
        self.ffmpeg = FFmpeg()

    def _ingest(self, context: PipelineContext) -> None:
        key = context.video.source_storage_key
        if not key or not self.storage.exists(key):
            raise FileNotFoundError("source video is missing from storage")
        if STORAGE_BACKEND == "r2":
            context.source_path = self.storage.download_to_path(
                key,
                Path("/tmp") / "naijaclip" / context.job.id / Path(key).name,
            )
        else:
            context.source_path = self.storage.path_for(key)

    def _analyze_media(self, context: PipelineContext) -> None:
        context.media_info = self.ffmpeg.probe(context.source_path)
        video_stream = next((stream for stream in context.media_info.get("streams", []) if stream.get("codec_type") == "video"), None)
        if not video_stream:
            raise ValueError("source does not contain a video stream")
        duration = float(context.media_info.get("format", {}).get("duration") or video_stream.get("duration") or 0)
        if duration <= 0:
            raise ValueError("source video has no valid duration")
        context.video.duration_seconds = duration
        context.video.width = int(video_stream.get("width") or 0) or None
        context.video.height = int(video_stream.get("height") or 0) or None
        context.video.video_codec = video_stream.get("codec_name")
        audio_stream = next((stream for stream in context.media_info.get("streams", []) if stream.get("codec_type") == "audio"), None)
        if not audio_stream:
            raise ValueError("source video has no audio stream")
        if audio_stream:
            context.video.audio_codec = audio_stream.get("codec_name")
        self.db.commit()

    def process_video(self, job_id: str) -> PipelineContext:
        from models import ProcessingJob

        job = self.db.get(ProcessingJob, job_id)
        if job is None:
            raise LookupError(f"processing job {job_id} not found")
        context = PipelineContext(job=job, video=job.video)
        if not self.stage_handlers:
            self.stage_handlers = PipelineStages(self.db, self.storage, self.ffmpeg).handlers()
        stages = [
            ("ingestion", JobStatus.INGESTING, 5),
            ("input_validation", JobStatus.INGESTING, 10),
            ("media_analysis", JobStatus.ANALYZING, 15),
            ("audio_extraction", JobStatus.TRANSCRIBING, 20),
            ("deepgram_transcription", JobStatus.TRANSCRIBING, 30),
            ("transcript_validation", JobStatus.TRANSCRIBING, 35),
            ("groq_selection", JobStatus.SELECTING, 45),
            ("candidate_scoring", JobStatus.SCORING, 50),
            ("clip_extraction", JobStatus.EXTRACTING, 58),
            ("yolo_detection", JobStatus.DETECTING, 63),
            ("opencv_tracking", JobStatus.TRACKING, 68),
            ("reframing", JobStatus.REFRAMING, 72),
            ("captions", JobStatus.CAPTIONS, 80),
            ("audio_processing", JobStatus.AUDIO_PROCESSING, 84),
            ("ffmpeg_rendering", JobStatus.RENDERING, 90),
            ("output_validation", JobStatus.VALIDATING, 94),
            ("thumbnail_generation", JobStatus.EXPORTING, 96),
            ("export", JobStatus.EXPORTING, 98),
            ("cleanup", JobStatus.EXPORTING, 99),
        ]
        for stage, status, progress in stages:
            ensure_job_active(self.db, job_id)
            update_job(self.db, job_id, status=status, stage=stage, progress=progress)
            handler = self.stage_handlers.get(stage)
            if handler:
                handler(context)
            elif stage == "ingestion":
                self._ingest(context)
            elif stage == "media_analysis":
                self._analyze_media(context)
            else:
                raise RuntimeError(f"pipeline stage is not configured: {stage}")
            ensure_job_active(self.db, job_id)
        job = self.db.get(ProcessingJob, job_id)
        job.status = JobStatus.COMPLETED
        job.current_stage = "completed"
        job.progress = 100
        job.completed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        job.video.status = VideoStatus.COMPLETED
        self.db.commit()
        return context


def process_video(job_id: str, db: Session) -> PipelineContext:
    return PipelineOrchestrator(db).process_video(job_id)
