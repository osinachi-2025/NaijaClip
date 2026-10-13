from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import models
from core.config import JOB_TIMEOUT_SECONDS
from database import Base
from integrations.groq.client import GroqOutputError
from services.jobs import (
    JobClaimLost,
    claim_job_for_processing,
    dispatch_processing_job,
    fail_job,
    recover_stalled_jobs,
    recover_unpublished_jobs,
    update_job,
    utcnow,
)
from workers.celery_app import celery_app
from workers.tasks import process_clip_export_job, process_video_job


@pytest.fixture
def sqlite_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'celery.db'}")
    Base.metadata.create_all(bind=engine)
    return engine


def _make_video_job(
    db: Session,
    *,
    job_type: models.JobType = models.JobType.EXPORT,
    status: models.JobStatus = models.JobStatus.QUEUED,
    max_attempts: int = 3,
):
    suffix = uuid4().hex[:8]
    user = models.User(id=f"job-user-{suffix}", email=f"user-{suffix}@example.com", password_hash="hash")
    video = models.Video(
        id=f"job-video-{suffix}",
        user_id=user.id,
        title="Job video",
        original_filename="job.mp4",
        status=models.VideoStatus.UPLOADED,
    )
    job = models.ProcessingJob(
        video_id=video.id,
        job_type=job_type,
        status=status,
        current_stage="queued",
        progress=0,
        max_attempts=max_attempts,
    )
    db.add_all([user, video, job])
    db.commit()
    return job


def _complete_job(db: Session, job_id: str) -> None:
    job = db.get(models.ProcessingJob, job_id)
    job.status = models.JobStatus.COMPLETED
    job.completed_at = datetime.now(timezone.utc)
    job.video.status = models.VideoStatus.COMPLETED
    db.commit()


def test_celery_app_initializes_and_registers_tasks():
    assert celery_app is not None
    assert celery_app.conf.broker_url
    assert "workers.tasks.process_video_job" in celery_app.tasks
    assert "workers.tasks.process_clip_export_job" in celery_app.tasks


def test_dispatch_processing_job_sets_task_id_for_queued_job(sqlite_db, monkeypatch):
    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)

        class FakeResult:
            id = "task-123"

        def fake_send_task(name, args=None, kwargs=None, **_):
            assert name == "workers.tasks.process_video_job"
            assert args == [job.id]
            return FakeResult()

        monkeypatch.setattr("workers.tasks.celery_app.send_task", fake_send_task)
        dispatched = dispatch_processing_job(db, job.id)
        assert dispatched == "task-123"
        db.refresh(job)
        assert job.queue_id == "task-123"


def test_task_executes_existing_video_pipeline(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        called = {}

        def fake_process_video(self, job_id: str):
            called["job_id"] = job_id
            return SimpleNamespace(id=job_id)

        monkeypatch.setattr("pipeline.orchestrator.PipelineOrchestrator.process_video", fake_process_video)

        result = process_video_job(job.id)
        assert result["status"] == "completed"
        assert called["job_id"] == job.id


def test_queued_job_with_persisted_task_id_claims_and_runs(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        job.queue_id = "celery-task-123"
        db.commit()
        called = []

        def fake_process_video(self, job_id: str):
            called.append(job_id)
            _complete_job(self.db, job_id)

        monkeypatch.setattr("pipeline.orchestrator.PipelineOrchestrator.process_video", fake_process_video)

        result = process_video_job(job.id)

        assert result["status"] == "completed"
        assert called == [job.id]


def test_duplicate_delivery_cannot_claim_running_job(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        process_calls = []
        duplicate_results = []

        def fake_process_video(self, job_id: str):
            process_calls.append(job_id)
            duplicate_results.append(process_video_job(job_id))
            _complete_job(self.db, job_id)

        monkeypatch.setattr("pipeline.orchestrator.PipelineOrchestrator.process_video", fake_process_video)

        result = process_video_job(job.id)

        assert result["status"] == "completed"
        assert duplicate_results == [{"job_id": job.id, "status": "duplicate"}]
        assert process_calls == [job.id]
        db.refresh(job)
        assert db.get(models.ProcessingJob, job.id).attempt_count == 1


def test_task_skips_completed_and_duplicate_jobs(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db, status=models.JobStatus.COMPLETED)
        assert process_video_job(job.id)["status"] == "skipped"

        cancelled_job = _make_video_job(db, status=models.JobStatus.CANCELLED)
        assert process_video_job(cancelled_job.id)["status"] == "skipped"

        queued_job = _make_video_job(db, status=models.JobStatus.QUEUED)
        queued_job.status = models.JobStatus.INGESTING
        queued_job.worker_id = "celery:already-running"
        queued_job.queue_id = "duplicate-task"
        db.commit()
        assert process_video_job(queued_job.id)["status"] == "duplicate"


def test_recover_unpublished_jobs_dispatches_queued_jobs(sqlite_db, monkeypatch):
    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)

        called = []

        def fake_dispatch(db_session, job_id):
            called.append(job_id)
            return "recovered-task"

        monkeypatch.setattr("services.jobs.dispatch_processing_job", fake_dispatch)
        recovered = recover_unpublished_jobs(db)

        assert recovered == [job.id]
        assert called == [job.id]


def test_redis_failure_is_recovered_by_a_later_session(sqlite_db, monkeypatch):
    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        calls = []

        def unavailable(*_args, **_kwargs):
            calls.append("unavailable")
            raise ConnectionError("Redis is unavailable")

        monkeypatch.setattr("workers.tasks.celery_app.send_task", unavailable)
        assert dispatch_processing_job(db, job.id) is None
        db.refresh(job)
        assert job.queue_id is None
        assert job.dispatch_retry_at is not None
        job.dispatch_retry_at = utcnow() - timedelta(seconds=1)
        db.commit()

    class FakeResult:
        id = "recovered-task"

    def publish(name, args=None, **_kwargs):
        calls.append((name, args))
        return FakeResult()

    monkeypatch.setattr("workers.tasks.celery_app.send_task", publish)
    with Session(sqlite_db, expire_on_commit=False) as restarted_db:
        assert recover_unpublished_jobs(restarted_db) == [job.id]
        recovered_job = restarted_db.get(models.ProcessingJob, job.id)
        assert recovered_job.queue_id == "recovered-task"
    assert calls[0] == "unavailable"
    assert len(calls) == 2


def test_unclaimed_published_message_is_republished_after_timeout(sqlite_db, monkeypatch):
    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        job.queue_id = "lost-redis-message"
        job.dispatch_published_at = utcnow() - timedelta(seconds=JOB_TIMEOUT_SECONDS + 1)
        db.commit()
        published = []

        class FakeResult:
            id = "replacement-message"

        def publish(name, args=None, **_kwargs):
            published.append((name, args))
            return FakeResult()

        monkeypatch.setattr("workers.tasks.celery_app.send_task", publish)
        assert recover_unpublished_jobs(db) == [job.id]
        db.refresh(job)
        assert job.queue_id == "replacement-message"
        assert job.dispatch_published_at is not None
        assert len(published) == 1


def test_concurrent_dispatch_recovery_obeys_database_lease(sqlite_db, monkeypatch):
    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        nested_recovery = []

        class FakeResult:
            id = "single-published-task"

        def publish(_name, _args=None, **_kwargs):
            with Session(sqlite_db, expire_on_commit=False) as concurrent_db:
                nested_recovery.extend(recover_unpublished_jobs(concurrent_db))
            return FakeResult()

        monkeypatch.setattr("workers.tasks.celery_app.send_task", publish)
        assert dispatch_processing_job(db, job.id) == "single-published-task"
        db.refresh(job)
        assert job.queue_id == "single-published-task"
        assert nested_recovery == []


def test_publish_then_database_update_failure_is_duplicate_safe(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)
    published = []

    class FakeResult:
        def __init__(self, task_id):
            self.id = task_id

    def publish(_name, args=None, **_kwargs):
        published.append(args[0])
        return FakeResult(f"task-{len(published)}")

    monkeypatch.setattr("workers.tasks.celery_app.send_task", publish)
    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        process_calls = []

        def fake_process_video(self, job_id: str):
            process_calls.append(job_id)
            _complete_job(self.db, job_id)

        monkeypatch.setattr("pipeline.orchestrator.PipelineOrchestrator.process_video", fake_process_video)
        original_commit = db.commit
        commit_count = 0

        def fail_after_publish():
            nonlocal commit_count
            commit_count += 1
            if commit_count == 2:
                raise RuntimeError("simulated database failure after publish")
            original_commit()

        monkeypatch.setattr(db, "commit", fail_after_publish)
        with pytest.raises(RuntimeError, match="after publish"):
            dispatch_processing_job(db, job.id)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = db.get(models.ProcessingJob, job.id)
        job.dispatch_lease_until = utcnow() - timedelta(seconds=1)
        db.commit()
        assert recover_unpublished_jobs(db) == [job.id]

    assert published == [job.id, job.id]
    assert process_video_job(job.id)["status"] == "completed"
    assert process_video_job(job.id)["status"] == "skipped"
    assert process_calls == [job.id]


def test_transient_retry_reclaims_job_through_queue_id_lifecycle(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        job.queue_id = "first-task"
        db.commit()
        process_calls = []

        def fake_process_video(self, job_id: str):
            process_calls.append(job_id)
            if len(process_calls) == 1:
                raise TimeoutError("temporary network timeout")
            _complete_job(self.db, job_id)

        monkeypatch.setattr("pipeline.orchestrator.PipelineOrchestrator.process_video", fake_process_video)
        assert process_video_job(job.id)["status"] == "retrying"
        db.refresh(job)
        assert job.status == models.JobStatus.QUEUED
        assert job.queue_id is None
        assert job.attempt_count == 1
        job.dispatch_retry_at = utcnow() - timedelta(seconds=1)
        db.commit()

        class FakeResult:
            id = "retry-task"

        monkeypatch.setattr("workers.tasks.celery_app.send_task", lambda *_args, **_kwargs: FakeResult())
        assert recover_unpublished_jobs(db) == [job.id]
        db.refresh(job)
        assert job.queue_id == "retry-task"
        assert process_video_job(job.id)["status"] == "completed"
        db.refresh(job)
        assert job.status == models.JobStatus.COMPLETED
        assert job.attempt_count == 2
        assert process_calls == [job.id, job.id]


def test_retry_exhaustion_marks_job_failed(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db, max_attempts=1)
        job.queue_id = "last-attempt"
        db.commit()
        monkeypatch.setattr(
            "pipeline.orchestrator.PipelineOrchestrator.process_video",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("network timeout")),
        )

        result = process_video_job(job.id)

        db.refresh(job)
        assert result["status"] == "failed"
        assert job.status == models.JobStatus.FAILED
        assert job.attempt_count == 1
        assert job.queue_id is None


def test_permanent_failure_is_terminal_without_retry(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        job.queue_id = "permanent-error-task"
        db.commit()
        monkeypatch.setattr(
            "pipeline.orchestrator.PipelineOrchestrator.process_video",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("unsupported video format")),
        )

        result = process_video_job(job.id)

        db.refresh(job)
        assert result["status"] == "failed"
        assert job.status == models.JobStatus.FAILED
        assert job.attempt_count == 1
        assert job.dispatch_retry_at is None


def test_exhausted_groq_json_recovery_marks_video_job_failed_with_diagnostics(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        job.queue_id = "groq-json-error-task"
        db.commit()
        error = GroqOutputError(
            "Groq output failure after bounded recovery "
            "({'model': 'openai/gpt-oss-120b', 'chunk': 2, "
            "'http_status': 400, 'error_code': 'json_validate_failed'})"
        )
        monkeypatch.setattr(
            "pipeline.orchestrator.PipelineOrchestrator.process_video",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
        )

        result = process_video_job(job.id)

        db.refresh(job)
        assert result["status"] == "failed"
        assert job.status == models.JobStatus.FAILED
        assert job.retryable is False
        assert job.error_code == "permanent"
        assert "openai/gpt-oss-120b" in job.internal_error_details
        assert "json_validate_failed" in job.internal_error_details
        assert job.video.processing_error == "We couldn't process this video right now. Please try again."


def test_worker_loss_redelivery_is_recovered_from_database_state(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        job.queue_id = "worker-lost-task"
        db.commit()
        claimed = claim_job_for_processing(db, job.id, worker_id="celery:worker-lost-task")
        assert claimed is not None
        job.last_heartbeat_at = utcnow() - timedelta(seconds=60)
        db.commit()
        assert process_video_job(job.id)["status"] == "duplicate"
        assert recover_stalled_jobs(db, timeout_seconds=1) == [job.id]
        db.refresh(job)
        assert job.status == models.JobStatus.QUEUED
        assert job.queue_id is None

        class FakeResult:
            id = "recovered-worker-task"

        monkeypatch.setattr("workers.tasks.celery_app.send_task", lambda *_args, **_kwargs: FakeResult())
        assert recover_unpublished_jobs(db) == [job.id]
        monkeypatch.setattr(
            "pipeline.orchestrator.PipelineOrchestrator.process_video",
            lambda orchestrator, job_id: _complete_job(orchestrator.db, job_id),
        )
        assert process_video_job(job.id)["status"] == "completed"
        db.refresh(job)
        assert job.attempt_count == 2
        assert job.status == models.JobStatus.COMPLETED


def test_recovered_attempt_fences_stale_worker_writes(sqlite_db):
    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db)
        old_owner = "celery:old-attempt"
        replacement_owner = "celery:new-attempt"
        assert claim_job_for_processing(db, job.id, worker_id=old_owner) is not None
        job.last_heartbeat_at = utcnow() - timedelta(seconds=60)
        db.commit()

        assert recover_stalled_jobs(db, timeout_seconds=1) == [job.id]
        assert claim_job_for_processing(db, job.id, worker_id=replacement_owner) is not None

        with pytest.raises(JobClaimLost):
            update_job(
                db,
                job.id,
                status=models.JobStatus.ANALYZING,
                stage="stale-worker-stage",
                progress=50,
                worker_id=old_owner,
            )
        with pytest.raises(JobClaimLost):
            fail_job(db, job, TimeoutError("late timeout"), worker_id=old_owner)

        db.refresh(job)
        assert job.worker_id == replacement_owner
        assert job.status == models.JobStatus.INGESTING
        assert job.current_stage == "ingestion"


@pytest.mark.parametrize("module", ["tasks.worker", "app.tasks.worker"])
def test_legacy_worker_entry_points_require_explicit_opt_in(module, tmp_path):
    repository = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("NAIJACLIP_ENABLE_LEGACY_WORKER", None)
    env["DATABASE_URL"] = "sqlite:///:memory:"
    env["PYTHONPATH"] = str(repository)

    result = subprocess.run(
        [sys.executable, "-m", module],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Legacy polling worker is disabled" in result.stderr


def test_clip_render_task_executes_clip_export(sqlite_db, monkeypatch):
    SessionLocal = sessionmaker(bind=sqlite_db, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("workers.tasks.SessionLocal", SessionLocal)

    with Session(sqlite_db, expire_on_commit=False) as db:
        job = _make_video_job(db, job_type=models.JobType.CLIP_RENDER)
        job.queue_id = "clip-export-task"
        db.commit()

        called = {}

        def fake_process_clip_export(db_session, job_id, *, worker_id=None):
            called["job_id"] = job_id
            db_session.get(models.ProcessingJob, job_id).status = models.JobStatus.COMPLETED
            db_session.commit()

        monkeypatch.setattr("workers.tasks.process_clip_export", fake_process_clip_export)
        result = process_clip_export_job(job.id)
        assert result["status"] == "completed"
        assert called["job_id"] == job.id
