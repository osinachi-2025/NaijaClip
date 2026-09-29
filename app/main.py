from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
import database
from database import get_db, initialize_database
from core.config import FRONTEND_URL, GOOGLE_CLIENT_ID, GOOGLE_REDIRECT_URI, MAX_UPLOAD_BYTES, PAYSTACK_PUBLIC_KEY, STORAGE_BACKEND
from integrations.storage import LocalStorage, R2Storage
from routers.auth import get_current_active_user
from services.media_validation import VideoValidationError, validate_video_file
from services.jobs import cancel_job

app = FastAPI(title="NaijaClip API", version="1.0.0")
storage = R2Storage() if STORAGE_BACKEND == "r2" else LocalStorage()


@app.on_event("startup")
def create_schema() -> None:
    initialize_database()


def frontend_config() -> dict:
    return {
        "FRONTEND_URL": FRONTEND_URL,
        "PAYSTACK_PUBLIC_KEY": PAYSTACK_PUBLIC_KEY or "",
        "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID or "",
        "GOOGLE_REDIRECT_URI": GOOGLE_REDIRECT_URI or "",
    }


@app.get("/api/config")
def api_config() -> dict:
    return frontend_config()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/videos", status_code=202)
def create_video_job(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")
    if file.content_type and not file.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="only video uploads are supported")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="this video exceeds the configured upload limit")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix or ".mp4") as upload_temp:
            shutil.copyfileobj(file.file, upload_temp)
            temp_path = Path(upload_temp.name)

        validated = validate_video_file(
            temp_path,
            filename=file.filename,
            content_type=file.content_type,
            file_size=file.size,
        )

        with temp_path.open("rb") as source_handle:
            key = storage.save_upload(source_handle, file.filename, current_user.id, validated.get("mime_type") or file.content_type)

        video = models.Video(
            user_id=current_user.id,
            title=title or Path(file.filename).stem,
            original_filename=file.filename,
            status=models.VideoStatus.UPLOADED,
            source_storage_key=key,
            source_mime_type=validated.get("mime_type") or file.content_type,
            file_size_bytes=int(validated.get("source_file_size_bytes") or file.size or 0),
            duration_seconds=float(validated.get("duration") or 0.0),
            width=int(validated.get("width") or 0) or None,
            height=int(validated.get("height") or 0) or None,
            frame_rate=float(validated.get("fps") or 0.0) or None,
            video_codec=validated.get("video_codec"),
            audio_codec=validated.get("audio_codec"),
            audio_quality_status=validated.get("audio_status"),
            media_metadata=str(validated),
        )
        db.add(video)
        db.flush()
        job = models.ProcessingJob(
            video_id=video.id,
            job_type=models.JobType.EXPORT,
            status=models.JobStatus.QUEUED,
            current_stage="queued",
            progress=0,
        )
        db.add(job)
        db.commit()
        return {"job_id": job.id, "video_id": video.id, "status": job.status.value}
    except VideoValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(models.ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "id": job.id,
        "video_id": job.video_id,
        "status": job.status.value,
        "current_stage": job.current_stage,
        "progress": job.progress,
        "retry_count": job.attempt_count,
        "error_message": job.error_message,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "failed_at": job.failed_at,
        "worker_id": job.worker_id,
    }


@app.post("/api/videos/{video_id}/cancel")
def cancel_video(
    video_id: str,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    video = db.scalar(select(models.Video).where(models.Video.id == video_id, models.Video.user_id == current_user.id, models.Video.deleted_at.is_(None)))
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")
    job = max(video.jobs, key=lambda item: item.created_at, default=None)
    if job is None:
        raise HTTPException(status_code=409, detail="video has no processing job")
    cancel_job(db, job.id)
    return {"video_id": video.id, "job_id": job.id, "status": job.status.value}


@app.delete("/api/videos/{video_id}", status_code=204)
def delete_video(
    video_id: str,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    video = db.scalar(select(models.Video).where(models.Video.id == video_id, models.Video.user_id == current_user.id, models.Video.deleted_at.is_(None)))
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")

    active_job = next((job for job in video.jobs if job.status not in {models.JobStatus.COMPLETED, models.JobStatus.FAILED, models.JobStatus.CANCELLED, models.JobStatus.CANCELED}), None)
    if active_job is not None:
        cancel_job(db, active_job.id)
    storage_keys = [video.source_storage_key]
    storage_keys.extend(clip.output_storage_key for clip in video.clips)
    storage_keys.extend(clip.editor_source_storage_key for clip in video.clips)
    storage_keys.extend(clip.thumbnail_storage_key for clip in video.clips)
    storage_keys.extend(export.output_storage_key for clip in video.clips for export in clip.exports)
    storage_keys.extend(clip.thumbnail_url for clip in video.clips if clip.thumbnail_url and not clip.thumbnail_url.startswith("/media/"))
    for key in storage_keys:
        if not key:
            continue
        try:
            storage.delete(key.removeprefix("/media/"))
        except Exception:
            pass
    video.deleted_at = datetime.now(timezone.utc)
    for clip in video.clips:
        clip.is_featured = False
        clip.featured_order = None
        clip.featured_at = None
    remaining_featured = db.scalars(
        select(models.Clip)
        .join(models.Video)
        .where(
            models.Clip.is_featured.is_(True),
            models.Clip.deleted_at.is_(None),
            models.Video.deleted_at.is_(None),
        )
        .order_by(models.Clip.featured_order.asc(), models.Clip.featured_at.asc())
    ).all()
    for index, clip in enumerate(remaining_featured, 1):
        clip.featured_order = index
    db.commit()
    return None


def _video_payload(video: models.Video) -> dict:
    latest_job = max(video.jobs, key=lambda job: job.created_at, default=None)
    return {
        "id": video.id,
        "title": video.title,
        "original_filename": video.original_filename,
        "status": video.status.value,
        "duration_seconds": float(video.duration_seconds) if video.duration_seconds is not None else None,
        "clip_count": len(video.clips),
        "created_at": video.created_at.isoformat(),
        "job": {
            "id": latest_job.id,
            "status": latest_job.status.value,
            "current_stage": latest_job.current_stage,
            "progress": latest_job.progress,
            "error_message": latest_job.error_message,
        } if latest_job else None,
    }


@app.get("/api/videos")
def list_videos(current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    videos = db.scalars(
        select(models.Video)
        .where(models.Video.user_id == current_user.id, models.Video.deleted_at.is_(None))
        .order_by(models.Video.created_at.desc())
    ).all()
    return {"videos": [_video_payload(video) for video in videos]}


@app.get("/api/clips")
def list_clips(current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    clip_filters = [
        models.Clip.deleted_at.is_(None),
        models.Video.deleted_at.is_(None),
    ]
    if current_user.role != models.UserRole.ADMIN:
        clip_filters.append(models.Video.user_id == current_user.id)
    clips = db.scalars(
        select(models.Clip)
        .join(models.Video)
        .where(*clip_filters)
        .order_by(models.Clip.created_at.desc(), models.Clip.rank)
    ).all()
    return {"clips": [{
        "id": clip.id,
        "video_id": clip.video_id,
        "title": clip.title,
        "description": clip.description,
        "start_seconds": float(clip.start_seconds),
        "end_seconds": float(clip.end_seconds),
        "duration_seconds": float(clip.end_seconds - clip.start_seconds),
        "status": clip.status.value,
        "score": float(clip.ai_score) if clip.ai_score is not None else None,
        "output_url": clip.output_url,
        "thumbnail_url": clip.thumbnail_url,
        "can_feature": clip.status == models.ClipStatus.READY and bool(clip.output_storage_key),
        "is_featured": clip.is_featured,
        "featured_order": clip.featured_order,
        "created_at": clip.created_at.isoformat(),
    } for clip in clips]}
