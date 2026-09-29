from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from database import get_db
from routers.auth import get_current_active_user
from services.clip_editor import normalize_edit_configuration
from services.subtitles import build_subtitle_chunks


router = APIRouter(tags=["clip-editor"])


class EditConfigurationRequest(BaseModel):
    configuration: dict[str, Any]


def _owned_clip(clip_id: str, user: models.User, db: Session) -> models.Clip:
    clip = db.scalar(
        select(models.Clip)
        .join(models.Video)
        .where(
            models.Clip.id == clip_id,
            models.Video.user_id == user.id,
            models.Video.deleted_at.is_(None),
            models.Clip.deleted_at.is_(None),
        )
    )
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    if clip.status != models.ClipStatus.READY or not clip.output_storage_key:
        raise HTTPException(status_code=409, detail="clip is not ready for editing")
    return clip


def _clip_duration(clip: models.Clip) -> float:
    return float(clip.end_seconds - clip.start_seconds)


def _default_subtitles(clip: models.Clip, db: Session) -> list[dict[str, Any]]:
    transcript_segments = db.scalars(
        select(models.TranscriptSegment)
        .join(models.Transcript)
        .where(models.Transcript.video_id == clip.video_id)
        .order_by(models.TranscriptSegment.start_seconds)
    ).all()
    clip_start, clip_end = float(clip.start_seconds), float(clip.end_seconds)
    chunks = build_subtitle_chunks(
        [
            {
                "start": float(segment.start_seconds),
                "end": float(segment.end_seconds),
                "text": segment.text,
            }
            for segment in transcript_segments
        ],
        clip_start=clip_start,
        clip_end=clip_end,
    )
    return [
        {
            "id": f"cue-{index}",
            "start": max(0.0, chunk["start"] - clip_start),
            "end": min(clip_end - clip_start, chunk["end"] - clip_start),
            "text": chunk["text"],
        }
        for index, chunk in enumerate(chunks, 1)
    ]


def _source_url(clip: models.Clip) -> str | None:
    source_key = clip.editor_source_storage_key or clip.output_storage_key
    if not clip.editor_source_storage_key and clip.output_url:
        return clip.output_url
    from app.main import storage

    public_url = storage.url_for(source_key) if source_key else None
    if public_url:
        return public_url
    if source_key and hasattr(storage, "client"):
        return storage.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": storage.bucket, "Key": source_key},
            ExpiresIn=3600,
        )
    return None


def _export_payload(clip_export: models.ClipExport, db: Session) -> dict[str, Any]:
    job = db.get(models.ProcessingJob, clip_export.processing_job_id) if clip_export.processing_job_id else None
    return {
        "id": clip_export.id,
        "status": clip_export.status.value,
        "output_url": clip_export.output_url,
        "error_message": clip_export.error_message,
        "created_at": clip_export.created_at.isoformat(),
        "job": {
            "id": job.id,
            "status": job.status.value,
            "current_stage": job.current_stage,
            "progress": job.progress,
        } if job else None,
    }


@router.get("/api/clips/{clip_id}/edit")
def load_clip_edit(
    clip_id: str,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    clip = _owned_clip(clip_id, current_user, db)
    duration = _clip_duration(clip)
    edit = db.scalar(
        select(models.ClipEdit).where(
            models.ClipEdit.clip_id == clip.id,
            models.ClipEdit.user_id == current_user.id,
        )
    )
    configuration = edit.configuration if edit else {
        "segments": [{"start": 0, "end": duration}],
        "subtitles": _default_subtitles(clip, db),
    }
    return {
        "clip": {
            "id": clip.id,
            "title": clip.title,
            "duration_seconds": duration,
            "source_url": _source_url(clip),
            "output_url": clip.output_url,
            "has_clean_editor_source": bool(clip.editor_source_storage_key),
            "thumbnail_url": clip.thumbnail_url,
        },
        "configuration": normalize_edit_configuration(configuration, duration),
        "exports": [
            _export_payload(item, db)
            for item in db.scalars(
                select(models.ClipExport)
                .where(models.ClipExport.clip_id == clip.id, models.ClipExport.edit_configuration.is_not(None))
                .order_by(models.ClipExport.created_at.desc())
            ).all()
        ],
    }


@router.put("/api/clips/{clip_id}/edit")
def save_clip_edit(
    clip_id: str,
    payload: EditConfigurationRequest,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    clip = _owned_clip(clip_id, current_user, db)
    try:
        configuration = normalize_edit_configuration(payload.configuration, _clip_duration(clip))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    edit = db.scalar(
        select(models.ClipEdit).where(
            models.ClipEdit.clip_id == clip.id,
            models.ClipEdit.user_id == current_user.id,
        )
    )
    if edit is None:
        edit = models.ClipEdit(clip_id=clip.id, user_id=current_user.id, configuration=configuration)
        db.add(edit)
    else:
        edit.configuration = configuration
    db.commit()
    return {"configuration": configuration, "updated_at": edit.updated_at.isoformat()}


@router.post("/api/clips/{clip_id}/exports", status_code=status.HTTP_202_ACCEPTED)
def create_clip_export(
    clip_id: str,
    payload: EditConfigurationRequest,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    clip = _owned_clip(clip_id, current_user, db)
    try:
        configuration = normalize_edit_configuration(payload.configuration, _clip_duration(clip))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    edit = db.scalar(
        select(models.ClipEdit).where(
            models.ClipEdit.clip_id == clip.id,
            models.ClipEdit.user_id == current_user.id,
        )
    )
    if edit is None:
        edit = models.ClipEdit(clip_id=clip.id, user_id=current_user.id, configuration=configuration)
        db.add(edit)
    else:
        edit.configuration = configuration

    clip_export = models.ClipExport(
        clip_id=clip.id,
        status=models.ExportStatus.QUEUED,
        edit_configuration=configuration,
    )
    db.add(clip_export)
    db.flush()
    job = models.ProcessingJob(
        video_id=clip.video_id,
        job_type=models.JobType.CLIP_RENDER,
        status=models.JobStatus.QUEUED,
        current_stage="queued",
        progress=0,
    )
    db.add(job)
    db.flush()
    clip_export.processing_job_id = job.id
    db.commit()
    return {"export_id": clip_export.id, "job_id": job.id, "status": clip_export.status.value}


@router.get("/api/clip-exports/{export_id}")
def get_clip_export(
    export_id: str,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    clip_export = db.scalar(
        select(models.ClipExport)
        .join(models.Clip)
        .join(models.Video)
        .where(
            models.ClipExport.id == export_id,
            models.Video.user_id == current_user.id,
            models.Video.deleted_at.is_(None),
            models.Clip.deleted_at.is_(None),
        )
    )
    if clip_export is None or clip_export.edit_configuration is None:
        raise HTTPException(status_code=404, detail="clip export not found")
    job = db.get(models.ProcessingJob, clip_export.processing_job_id) if clip_export.processing_job_id else None
    return {
        "id": clip_export.id,
        "status": clip_export.status.value,
        "output_url": clip_export.output_url,
        "error_message": clip_export.error_message,
        "job": {
            "id": job.id,
            "status": job.status.value,
            "current_stage": job.current_stage,
            "progress": job.progress,
        } if job else None,
    }