from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.config import JOB_TIMEOUT_SECONDS, MAX_RETRIES
from core.error_handling import sanitize_error_message
from models import ClipExport, ExportStatus, JobStatus, JobType, ProcessingJob, VideoStatus


class JobCancelled(RuntimeError):
    pass


TRANSIENT_FAILURE_PATTERNS = (
    "timeout",
    "temporar",
    "network",
    "connection",
    "rate limit",
    "unavailable",
    "reset by peer",
    "service unavailable",
    "r2",
    "deepgram",
    "groq",
    "retry",
    "busy",
    "too many requests",
)

PERMANENT_FAILURE_PATTERNS = (
    "unsupported format",
    "unsupported video",
    "corrupt",
    "invalid request",
    "no usable audio",
    "audio doesn't appear",
    "not a readable video",
    "no speech",
    "no person",
    "invalid timestamp",
    "malformed json",
    "authentication",
    "permission denied",
    "file is empty",
    "path traversal",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def classify_failure(error: object | str) -> str:
    text = str(error or "").lower()
    if any(token in text for token in TRANSIENT_FAILURE_PATTERNS):
        return "transient"
    if any(token in text for token in PERMANENT_FAILURE_PATTERNS):
        return "permanent"
    if any(token in text for token in ["timeout", "connection", "temporary", "network", "rate limit"]):
        return "transient"
    if any(token in text for token in ["unsupported", "invalid", "corrupt", "missing", "malformed", "empty", "not found", "denied"]):
        return "permanent"
    return "transient"


def should_retry_job(error: object | str, *, attempt_count: int = 0, max_attempts: int | None = None) -> bool:
    max_attempts = max_attempts if max_attempts is not None else MAX_RETRIES
    return classify_failure(error) == "transient" and attempt_count < max_attempts


def claim_next_job(db: Session, worker_id: str | None = None) -> ProcessingJob | None:
    worker_id = worker_id or f"worker-{os.getpid()}"
    with db.begin():
        job = db.execute(
            select(ProcessingJob)
            .where(
                ProcessingJob.status == JobStatus.QUEUED,
                ProcessingJob.attempt_count < ProcessingJob.max_attempts,
            )
            .order_by(ProcessingJob.created_at)
            .with_for_update(skip_locked=True)
        ).scalars().first()
        if job is None:
            return None
        claimed = db.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job.id,
                ProcessingJob.status == JobStatus.QUEUED,
            )
            .values(
                status=JobStatus.INGESTING,
                current_stage="ingestion",
                worker_id=worker_id,
                claimed_at=utcnow(),
                last_heartbeat_at=utcnow(),
                started_at=job.started_at or utcnow(),
                attempt_count=ProcessingJob.attempt_count + 1,
                progress=1,
            )
        )
        if claimed.rowcount != 1:
            return None
        db.refresh(job)
        if job.job_type == JobType.CLIP_RENDER:
            clip_export = db.scalar(select(ClipExport).where(ClipExport.processing_job_id == job.id))
            if clip_export is not None:
                clip_export.status = ExportStatus.PROCESSING
        else:
            job.video.status = VideoStatus.PROCESSING
    return job


def update_job(db: Session, job_id: str, *, status: JobStatus, stage: str, progress: int) -> None:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise LookupError(f"processing job {job_id} not found")
    job.status = status
    job.current_stage = stage
    job.progress = max(0, min(100, progress))
    job.last_heartbeat_at = utcnow()
    db.commit()


def ensure_job_active(db: Session, job_id: str) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise LookupError(f"processing job {job_id} not found")
    if job.status in {JobStatus.CANCELLED, JobStatus.CANCELED} or job.cancelled_at is not None:
        raise JobCancelled(f"processing job {job_id} was cancelled")
    return job


def cancel_job(db: Session, job_id: str) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise LookupError(f"processing job {job_id} not found")
    if job.status not in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.CANCELED}:
        job.status = JobStatus.CANCELLED
        job.current_stage = "cancelled"
        job.cancelled_at = utcnow()
        job.retryable = False
        if job.job_type == JobType.CLIP_RENDER:
            clip_export = db.scalar(select(ClipExport).where(ClipExport.processing_job_id == job.id))
            if clip_export is not None:
                clip_export.status = ExportStatus.FAILED
                clip_export.error_message = "Export was cancelled."
        else:
            job.video.status = VideoStatus.CANCELLED
            job.video.processing_error = "Processing was cancelled."
        db.commit()
    return job


def recover_stalled_jobs(db: Session, *, timeout_seconds: int = JOB_TIMEOUT_SECONDS) -> list[str]:
    stale_since = utcnow() - timedelta(seconds=timeout_seconds)
    stalled = db.execute(
        select(ProcessingJob).where(
            ProcessingJob.status.notin_({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.CANCELED}),
            ProcessingJob.cancelled_at.is_(None),
            ProcessingJob.last_heartbeat_at.is_not(None),
            ProcessingJob.last_heartbeat_at < stale_since,
        )
    ).scalars().all()

    recovered: list[str] = []
    for job in stalled:
        job.status = JobStatus.QUEUED
        job.current_stage = "retry_waiting"
        job.last_heartbeat_at = utcnow()
        job.retryable = should_retry_job(job.error_message or "worker timeout", attempt_count=job.attempt_count, max_attempts=job.max_attempts)
        if job.job_type == JobType.CLIP_RENDER:
            clip_export = db.scalar(select(ClipExport).where(ClipExport.processing_job_id == job.id))
            if clip_export is not None:
                clip_export.status = ExportStatus.QUEUED
        elif job.video is not None:
            job.video.status = VideoStatus.QUEUED
        recovered.append(job.id)
    db.commit()
    return recovered


def fail_job(db: Session, job: ProcessingJob, error: object | str) -> bool:
    failure_type = classify_failure(error)
    safe_message = sanitize_error_message(error, "Something went wrong while processing this video.")
    retryable = should_retry_job(error, attempt_count=job.attempt_count, max_attempts=job.max_attempts)
    job.error_code = failure_type
    job.safe_error_message = safe_message[:4000]
    job.error_message = safe_message[:4000]
    job.internal_error_details = str(error)[:8000] if error is not None else None
    job.retryable = retryable
    job.failed_at = utcnow()
    job.last_heartbeat_at = utcnow()

    if retryable:
        job.status = JobStatus.QUEUED
        job.current_stage = "retry_waiting"
        if job.job_type == JobType.CLIP_RENDER:
            clip_export = db.scalar(select(ClipExport).where(ClipExport.processing_job_id == job.id))
            if clip_export is not None:
                clip_export.status = ExportStatus.QUEUED
                clip_export.error_message = safe_message[:4000]
        elif job.video is not None:
            job.video.status = VideoStatus.QUEUED
    else:
        job.status = JobStatus.FAILED
        job.current_stage = "failed"
        if job.job_type == JobType.CLIP_RENDER:
            clip_export = db.scalar(select(ClipExport).where(ClipExport.processing_job_id == job.id))
            if clip_export is not None:
                clip_export.status = ExportStatus.FAILED
                clip_export.error_message = safe_message[:4000]
        elif job.video is not None:
            job.video.status = VideoStatus.FAILED
            job.video.processing_error = safe_message
    db.commit()
    return job.status == JobStatus.QUEUED


__all__ = [
    "JobCancelled",
    "cancel_job",
    "claim_next_job",
    "classify_failure",
    "ensure_job_active",
    "fail_job",
    "recover_stalled_jobs",
    "should_retry_job",
    "update_job",
]
