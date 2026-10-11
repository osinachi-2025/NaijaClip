from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from core.config import CELERY_RETRY_BACKOFF_SECONDS, JOB_DISPATCH_LEASE_SECONDS, JOB_TIMEOUT_SECONDS, MAX_RETRIES
from core.error_handling import sanitize_error_message
from models import ClipExport, ExportStatus, JobStatus, JobType, ProcessingJob, VideoStatus

logger = logging.getLogger("naijaclip.jobs")


class JobCancelled(RuntimeError):
    pass


class JobClaimLost(RuntimeError):
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
            if job.video is not None:
                job.video.status = VideoStatus.PROCESSING
    return job


def claim_job_for_processing(db: Session, job_id: str, worker_id: str | None = None) -> ProcessingJob | None:
    worker_id = worker_id or f"worker-{os.getpid()}"
    with db.begin():
        now = utcnow()
        claimed = db.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.status == JobStatus.QUEUED,
                ProcessingJob.cancelled_at.is_(None),
                ProcessingJob.attempt_count < ProcessingJob.max_attempts,
            )
            .values(
                status=JobStatus.INGESTING,
                current_stage="ingestion",
                worker_id=worker_id,
                claimed_at=now,
                last_heartbeat_at=now,
                started_at=func.coalesce(ProcessingJob.started_at, now),
                attempt_count=ProcessingJob.attempt_count + 1,
                progress=case((ProcessingJob.progress < 1, 1), else_=ProcessingJob.progress),
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            return None

        job = db.get(ProcessingJob, job_id)
        db.refresh(job)
        if job.job_type == JobType.CLIP_RENDER:
            clip_export = db.scalar(select(ClipExport).where(ClipExport.processing_job_id == job.id))
            if clip_export is not None:
                clip_export.status = ExportStatus.PROCESSING
        elif job.video is not None:
            job.video.status = VideoStatus.PROCESSING
    return job


def update_job(
    db: Session,
    job_id: str,
    *,
    status: JobStatus,
    stage: str,
    progress: int,
    worker_id: str | None = None,
) -> None:
    if worker_id is not None:
        updated = db.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.worker_id == worker_id,
                ProcessingJob.status.notin_({
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                    JobStatus.CANCELED,
                }),
                ProcessingJob.cancelled_at.is_(None),
            )
            .execution_options(synchronize_session=False)
            .values(
                status=status,
                current_stage=stage,
                progress=max(0, min(100, progress)),
                last_heartbeat_at=utcnow(),
            )
        )
        if updated.rowcount != 1:
            db.rollback()
            raise JobClaimLost(f"processing job {job_id} is no longer owned by {worker_id}")
        db.commit()
        return

    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise LookupError(f"processing job {job_id} not found")
    job.status = status
    job.current_stage = stage
    job.progress = max(0, min(100, progress))
    job.last_heartbeat_at = utcnow()
    db.commit()


def ensure_job_active(db: Session, job_id: str, *, worker_id: str | None = None) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise LookupError(f"processing job {job_id} not found")
    if job.status in {JobStatus.CANCELLED, JobStatus.CANCELED} or job.cancelled_at is not None:
        raise JobCancelled(f"processing job {job_id} was cancelled")
    if worker_id is not None and job.worker_id != worker_id:
        raise JobClaimLost(f"processing job {job_id} is no longer owned by {worker_id}")
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


def recover_stalled_jobs(db: Session, *, timeout_seconds: int | None = None) -> list[str]:
    timeout_seconds = timeout_seconds if timeout_seconds is not None else JOB_TIMEOUT_SECONDS + JOB_RECOVERY_GRACE_SECONDS
    stale_since = utcnow() - timedelta(seconds=timeout_seconds)
    stalled = db.execute(
        select(
            ProcessingJob.id,
            ProcessingJob.status,
            ProcessingJob.last_heartbeat_at,
            ProcessingJob.attempt_count,
            ProcessingJob.max_attempts,
        ).where(
            ProcessingJob.status.notin_({
                JobStatus.QUEUED,
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
                JobStatus.CANCELED,
            }),
            ProcessingJob.cancelled_at.is_(None),
            ProcessingJob.last_heartbeat_at.is_not(None),
            ProcessingJob.last_heartbeat_at < stale_since,
        )
    ).all()

    recovered: list[str] = []
    for job_id, status, last_heartbeat_at, attempt_count, max_attempts in stalled:
        now = utcnow()
        exhausted = attempt_count >= max_attempts
        new_status = JobStatus.FAILED if exhausted else JobStatus.QUEUED
        updated = db.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.status == status,
                ProcessingJob.last_heartbeat_at == last_heartbeat_at,
                ProcessingJob.attempt_count == attempt_count,
                ProcessingJob.cancelled_at.is_(None),
            )
            .values(
                status=new_status,
                current_stage="failed" if exhausted else "retry_waiting",
                error_code="transient",
                error_message="Worker stopped before processing completed.",
                safe_error_message="Processing was interrupted. Please try again." if not exhausted else "Processing failed after repeated worker interruptions.",
                internal_error_details="Worker heartbeat expired.",
                retryable=not exhausted,
                failed_at=now if exhausted else None,
                last_heartbeat_at=now,
                worker_id=None,
                claimed_at=None,
                queue_id=None,
                dispatch_published_at=None,
                dispatch_token=None,
                dispatch_lease_until=None,
                dispatch_retry_at=now if not exhausted else None,
            )
        )
        if updated.rowcount != 1:
            continue

        job = db.get(ProcessingJob, job_id)
        if job.job_type == JobType.CLIP_RENDER:
            clip_export = db.scalar(select(ClipExport).where(ClipExport.processing_job_id == job_id))
            if clip_export is not None:
                clip_export.status = ExportStatus.FAILED if exhausted else ExportStatus.QUEUED
                clip_export.error_message = job.safe_error_message
        elif job.video is not None:
            job.video.status = VideoStatus.FAILED if exhausted else VideoStatus.QUEUED
            if exhausted:
                job.video.processing_error = job.safe_error_message
        if not exhausted:
            recovered.append(job_id)
    db.commit()
    return recovered


def task_name_for_job(job: ProcessingJob) -> str:
    if job.job_type == JobType.CLIP_RENDER:
        return "workers.tasks.process_clip_export_job"
    return "workers.tasks.process_video_job"


def dispatch_processing_job(db: Session, job_id: str) -> str | None:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise LookupError(f"processing job {job_id} not found")
    if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.CANCELED}:
        return None
    if job.queue_id:
        published_at = job.dispatch_published_at
        if published_at is not None:
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            if published_at > utcnow() - timedelta(seconds=JOB_TIMEOUT_SECONDS):
                return job.queue_id
    now = utcnow()
    dispatch_token = str(uuid4())
    claimed = db.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == JobStatus.QUEUED,
            ProcessingJob.cancelled_at.is_(None),
            ProcessingJob.attempt_count < ProcessingJob.max_attempts,
            or_(
                ProcessingJob.queue_id.is_(None),
                ProcessingJob.dispatch_published_at.is_(None),
                ProcessingJob.dispatch_published_at <= now - timedelta(seconds=JOB_TIMEOUT_SECONDS),
            ),
            or_(ProcessingJob.dispatch_retry_at.is_(None), ProcessingJob.dispatch_retry_at <= now),
            or_(ProcessingJob.dispatch_lease_until.is_(None), ProcessingJob.dispatch_lease_until <= now),
        )
        .execution_options(synchronize_session=False)
        .values(
            dispatch_token=dispatch_token,
            dispatch_lease_until=now + timedelta(seconds=JOB_DISPATCH_LEASE_SECONDS),
            dispatch_retry_at=None,
            queue_id=None,
            dispatch_published_at=None,
            dispatch_attempts=ProcessingJob.dispatch_attempts + 1,
        )
    )
    db.commit()
    if claimed.rowcount != 1:
        db.refresh(job)
        return job.queue_id

    from workers.celery_app import celery_app

    try:
        result = celery_app.send_task(task_name_for_job(job), args=[job.id])
    except Exception:
        db.refresh(job)
        retry_seconds = min(5 * (2 ** min(max(job.dispatch_attempts - 1, 0), 6)), 300)
        db.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id, ProcessingJob.dispatch_token == dispatch_token)
            .values(
                dispatch_token=None,
                dispatch_lease_until=None,
                dispatch_retry_at=utcnow() + timedelta(seconds=retry_seconds),
            )
        )
        db.commit()
        logger.exception("failed to dispatch job=%s", job.id)
        return None

    db.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == JobStatus.QUEUED,
            ProcessingJob.dispatch_token == dispatch_token,
            ProcessingJob.queue_id.is_(None),
        )
        .values(
            queue_id=result.id,
            dispatch_published_at=utcnow(),
            dispatch_token=None,
            dispatch_lease_until=None,
            dispatch_retry_at=None,
        )
    )
    db.commit()
    return result.id


def recover_unpublished_jobs(db: Session, *, limit: int = 100) -> list[str]:
    stale_publication_before = utcnow() - timedelta(seconds=JOB_TIMEOUT_SECONDS)
    jobs = db.execute(
        select(ProcessingJob)
        .where(
            ProcessingJob.status == JobStatus.QUEUED,
            ProcessingJob.cancelled_at.is_(None),
            or_(
                ProcessingJob.queue_id.is_(None),
                ProcessingJob.dispatch_published_at.is_(None),
                ProcessingJob.dispatch_published_at <= stale_publication_before,
            ),
            ProcessingJob.attempt_count < ProcessingJob.max_attempts,
            or_(ProcessingJob.dispatch_retry_at.is_(None), ProcessingJob.dispatch_retry_at <= utcnow()),
            or_(ProcessingJob.dispatch_lease_until.is_(None), ProcessingJob.dispatch_lease_until <= utcnow()),
        )
        .order_by(ProcessingJob.created_at)
        .limit(limit)
    ).scalars().all()
    recovered: list[str] = []
    for job in jobs:
        result = dispatch_processing_job(db, job.id)
        if result:
            recovered.append(job.id)
    return recovered


def fail_job(
    db: Session,
    job: ProcessingJob,
    error: object | str,
    *,
    worker_id: str | None = None,
) -> bool:
    failure_type = classify_failure(error)
    safe_message = sanitize_error_message(error, "Something went wrong while processing this video.")
    retryable = should_retry_job(error, attempt_count=job.attempt_count, max_attempts=job.max_attempts)
    now = utcnow()
    new_status = JobStatus.QUEUED if retryable else JobStatus.FAILED
    if worker_id is not None:
        updated = db.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job.id,
                ProcessingJob.worker_id == worker_id,
                ProcessingJob.status.notin_({
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                    JobStatus.CANCELED,
                    JobStatus.QUEUED,
                }),
                ProcessingJob.cancelled_at.is_(None),
            )
            .execution_options(synchronize_session=False)
            .values(
                status=new_status,
                current_stage="retry_waiting" if retryable else "failed",
                error_code=failure_type,
                safe_error_message=safe_message[:4000],
                error_message=safe_message[:4000],
                internal_error_details=str(error)[:8000] if error is not None else None,
                retryable=retryable,
                failed_at=now,
                last_heartbeat_at=now,
                queue_id=None,
                dispatch_published_at=None,
                dispatch_token=None,
                dispatch_lease_until=None,
                dispatch_retry_at=(
                    now + timedelta(seconds=min(CELERY_RETRY_BACKOFF_SECONDS * max(1, job.attempt_count), 300))
                    if retryable
                    else None
                ),
                worker_id=None,
                claimed_at=None,
            )
        )
        if updated.rowcount != 1:
            db.rollback()
            raise JobClaimLost(f"processing job {job.id} is no longer owned by {worker_id}")
        job = db.get(ProcessingJob, job.id)
        db.refresh(job)
        if retryable:
            if job.job_type == JobType.CLIP_RENDER:
                clip_export = db.scalar(select(ClipExport).where(ClipExport.processing_job_id == job.id))
                if clip_export is not None:
                    clip_export.status = ExportStatus.QUEUED
                    clip_export.error_message = safe_message[:4000]
            elif job.video is not None:
                job.video.status = VideoStatus.QUEUED
        else:
            if job.job_type == JobType.CLIP_RENDER:
                clip_export = db.scalar(select(ClipExport).where(ClipExport.processing_job_id == job.id))
                if clip_export is not None:
                    clip_export.status = ExportStatus.FAILED
                    clip_export.error_message = safe_message[:4000]
            elif job.video is not None:
                job.video.status = VideoStatus.FAILED
                job.video.processing_error = safe_message
        db.commit()
        return retryable

    job.error_code = failure_type
    job.safe_error_message = safe_message[:4000]
    job.error_message = safe_message[:4000]
    job.internal_error_details = str(error)[:8000] if error is not None else None
    job.retryable = retryable
    job.failed_at = now
    job.last_heartbeat_at = now
    job.queue_id = None
    job.dispatch_published_at = None
    job.dispatch_token = None
    job.dispatch_lease_until = None
    job.worker_id = None
    job.claimed_at = None

    if retryable:
        job.status = new_status
        job.current_stage = "retry_waiting"
        retry_seconds = min(CELERY_RETRY_BACKOFF_SECONDS * max(1, job.attempt_count), 300)
        job.dispatch_retry_at = now + timedelta(seconds=retry_seconds)
        if job.job_type == JobType.CLIP_RENDER:
            clip_export = db.scalar(select(ClipExport).where(ClipExport.processing_job_id == job.id))
            if clip_export is not None:
                clip_export.status = ExportStatus.QUEUED
                clip_export.error_message = safe_message[:4000]
        elif job.video is not None:
            job.video.status = VideoStatus.QUEUED
    else:
        job.status = new_status
        job.current_stage = "failed"
        job.dispatch_retry_at = None
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
    "JobClaimLost",
    "cancel_job",
    "claim_job_for_processing",
    "claim_next_job",
    "classify_failure",
    "dispatch_processing_job",
    "ensure_job_active",
    "fail_job",
    "recover_stalled_jobs",
    "recover_unpublished_jobs",
    "should_retry_job",
    "task_name_for_job",
    "update_job",
]
