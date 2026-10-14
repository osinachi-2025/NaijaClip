from __future__ import annotations

import logging
from threading import Event, Thread
from datetime import datetime, timezone

from sqlalchemy import update

from workers.celery_app import celery_app
from database import SessionLocal
import models
from core.config import JOB_TIMEOUT_SECONDS
from pipeline.orchestrator import PipelineOrchestrator
from services.clip_editor import process_clip_export
from services.jobs import JobCancelled, JobClaimLost, claim_job_for_processing, fail_job

logger = logging.getLogger("naijaclip.celery")


def _start_job_heartbeat(job_id: str, worker_id: str) -> tuple[Event, Thread]:
    stop = Event()
    interval = max(5, min(60, JOB_TIMEOUT_SECONDS // 3))

    def heartbeat() -> None:
        while not stop.wait(interval):
            try:
                with SessionLocal() as heartbeat_db:
                    heartbeat_db.execute(
                        update(models.ProcessingJob)
                        .where(
                            models.ProcessingJob.id == job_id,
                            models.ProcessingJob.worker_id == worker_id,
                            models.ProcessingJob.status.notin_({
                                models.JobStatus.COMPLETED,
                                models.JobStatus.FAILED,
                                models.JobStatus.CANCELLED,
                                models.JobStatus.CANCELED,
                                models.JobStatus.QUEUED,
                            }),
                        )
                        .values(last_heartbeat_at=datetime.now(timezone.utc))
                    )
                    heartbeat_db.commit()
            except Exception:
                logger.exception("job heartbeat failed job=%s", job_id)

    thread = Thread(target=heartbeat, name=f"job-heartbeat-{job_id}", daemon=True)
    thread.start()
    return stop, thread


@celery_app.task(name="workers.tasks.process_video_job", bind=True)
def process_video_job(self, job_id: str) -> dict[str, str]:
    db = SessionLocal()
    heartbeat_stop = None
    heartbeat_thread = None
    try:
        worker_id = f"celery:{self.request.id}"
        job = claim_job_for_processing(db, job_id, worker_id=worker_id)
        if job is None:
            current = db.get(models.ProcessingJob, job_id)
            if current is None:
                return {"job_id": job_id, "status": "missing"}
            if current.status in {models.JobStatus.COMPLETED, models.JobStatus.FAILED, models.JobStatus.CANCELLED, models.JobStatus.CANCELED}:
                return {"job_id": job_id, "status": "skipped"}
            return {"job_id": job_id, "status": "duplicate"}

        heartbeat_stop, heartbeat_thread = _start_job_heartbeat(job_id, worker_id)
        logger.info("processing video job=%s via celery task=%s", job_id, self.request.id)
        try:
            PipelineOrchestrator(db, worker_id=worker_id).process_video(job_id)
        except JobClaimLost:
            logger.info("lost ownership of job=%s", job_id)
            return {"job_id": job_id, "status": "ownership_lost"}
        except JobCancelled:
            logger.info("cancelled job=%s", job_id)
            return {"job_id": job_id, "status": "cancelled"}
        except Exception as error:
            logger.exception("video job failed job=%s", job_id)
            try:
                retry_scheduled = fail_job(db, job, error, worker_id=worker_id)
            except JobClaimLost:
                logger.info("discarding failure from stale attempt job=%s", job_id)
                return {"job_id": job_id, "status": "ownership_lost"}
            if retry_scheduled:
                return {"job_id": job_id, "status": "retrying"}
            raise

        return {"job_id": job_id, "status": "completed"}
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
        db.close()


@celery_app.task(name="workers.tasks.process_clip_export_job", bind=True)
def process_clip_export_job(self, job_id: str) -> dict[str, str]:
    db = SessionLocal()
    heartbeat_stop = None
    heartbeat_thread = None
    try:
        worker_id = f"celery:{self.request.id}"
        job = claim_job_for_processing(db, job_id, worker_id=worker_id)
        if job is None:
            current = db.get(models.ProcessingJob, job_id)
            if current is None:
                return {"job_id": job_id, "status": "missing"}
            if current.status in {models.JobStatus.COMPLETED, models.JobStatus.FAILED, models.JobStatus.CANCELLED, models.JobStatus.CANCELED}:
                return {"job_id": job_id, "status": "skipped"}
            return {"job_id": job_id, "status": "duplicate"}

        heartbeat_stop, heartbeat_thread = _start_job_heartbeat(job_id, worker_id)
        logger.info("processing clip export job=%s via celery task=%s", job_id, self.request.id)
        try:
            process_clip_export(db, job_id, worker_id=worker_id)
        except JobClaimLost:
            logger.info("lost ownership of clip export job=%s", job_id)
            return {"job_id": job_id, "status": "ownership_lost"}
        except JobCancelled:
            logger.info("cancelled clip export job=%s", job_id)
            return {"job_id": job_id, "status": "cancelled"}
        except Exception as error:
            logger.exception("clip export job failed job=%s", job_id)
            try:
                retry_scheduled = fail_job(db, job, error, worker_id=worker_id)
            except JobClaimLost:
                logger.info("discarding failure from stale clip export attempt job=%s", job_id)
                return {"job_id": job_id, "status": "ownership_lost"}
            if retry_scheduled:
                return {"job_id": job_id, "status": "retrying"}
            raise

        return {"job_id": job_id, "status": "completed"}
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
        db.close()


__all__ = [
    "process_video_job",
    "process_clip_export_job",
]
