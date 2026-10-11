from __future__ import annotations

import logging
import os
import shutil
import signal
import time
from pathlib import Path

import database
from database import initialize_database
import models
from pipeline.orchestrator import PipelineOrchestrator
from services.jobs import JobCancelled, claim_next_job, fail_job, recover_stalled_jobs
from core.config import WORKER_POLL_SECONDS

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("naijaclip.worker")


class Worker:
    def __init__(self, poll_seconds: float = WORKER_POLL_SECONDS):
        if os.getenv("NAIJACLIP_ENABLE_LEGACY_WORKER") != "1":
            raise RuntimeError("Legacy polling worker is disabled; set NAIJACLIP_ENABLE_LEGACY_WORKER=1 to opt in.")
        self.poll_seconds = poll_seconds
        self.running = True
        initialize_database()

    def stop(self, *_args):
        self.running = False

    def run_once(self) -> bool:
        db = database.SessionLocal()
        try:
            recover_stalled_jobs(db)
            job = claim_next_job(db)
            if job is None:
                return False
            logger.info("claimed job=%s worker=%s", job.id, job.worker_id)
            try:
                if job.job_type == models.JobType.CLIP_RENDER:
                    from services.clip_editor import process_clip_export
                    process_clip_export(db, job.id)
                else:
                    PipelineOrchestrator(db).process_video(job.id)
                logger.info("completed job=%s", job.id)
            except JobCancelled:
                logger.info("cancelled job=%s", job.id)
            except Exception as error:
                logger.exception("job failed job=%s", job.id)
                fail_job(db, job, error)
                shutil.rmtree(Path("/tmp") / "naijaclip" / job.id, ignore_errors=True)
            return True
        finally:
            db.close()

    def run_forever(self):
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        while self.running:
            if not self.run_once():
                time.sleep(self.poll_seconds)


def run_legacy_worker() -> None:
    if os.getenv("NAIJACLIP_ENABLE_LEGACY_WORKER") != "1":
        raise SystemExit(
            "Legacy polling worker is disabled. Set NAIJACLIP_ENABLE_LEGACY_WORKER=1 to opt in; "
            "production should use celery -A workers.celery_app worker --loglevel=info."
        )
    Worker().run_forever()


if __name__ == "__main__":
    run_legacy_worker()
