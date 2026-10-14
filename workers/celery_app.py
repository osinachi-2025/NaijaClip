from __future__ import annotations

from celery import Celery

from core.config import (
    CELERY_BROKER_URL,
    CELERY_QUEUE,
    CELERY_RESULT_BACKEND,
    JOB_TIMEOUT_SECONDS,
    WORKER_CONCURRENCY,
)

celery_app = Celery(
    "naijaclip",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND or None,
    include=["workers.tasks"],
)

celery_app.conf.update(
    task_default_queue=CELERY_QUEUE,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=10,
    task_track_started=True,
    task_time_limit=JOB_TIMEOUT_SECONDS + 60,
    task_soft_time_limit=JOB_TIMEOUT_SECONDS,
    result_expires=3600,
    worker_concurrency=WORKER_CONCURRENCY,
    timezone="UTC",
)

__all__ = ["celery_app"]
