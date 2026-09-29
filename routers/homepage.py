from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from app.main import storage
from core.config import MAX_FEATURED_HOMEPAGE_CLIPS, R2_PUBLIC_URL
from database import get_db
from routers.auth import require_admin


router = APIRouter(tags=["homepage"])
logger = logging.getLogger(__name__)


class FeaturedClipOrderRequest(BaseModel):
    clip_ids: list[str] = Field(min_length=1, max_length=MAX_FEATURED_HOMEPAGE_CLIPS)


def _require_r2() -> None:
    if not hasattr(storage, "client") or not hasattr(storage, "exists"):
        raise HTTPException(status_code=503, detail="Homepage clips require R2 storage")


def _clip_object_available(clip: models.Clip) -> bool:
    if clip.status != models.ClipStatus.READY or not clip.output_storage_key:
        return False
    try:
        return storage.exists(clip.output_storage_key)
    except ClientError as error:
        response = error.response
        logger.warning(
            "Featured clip availability check failed clip_id=%s r2_error_code=%s http_status=%s",
            clip.id,
            response.get("Error", {}).get("Code", "unknown"),
            response.get("ResponseMetadata", {}).get("HTTPStatusCode", "unknown"),
        )
        return False
    except Exception as error:
        logger.warning("Featured clip availability check failed clip_id=%s error_type=%s", clip.id, type(error).__name__)
        return False


def _featured_rows(db: Session) -> list[models.Clip]:
    return db.scalars(
        select(models.Clip)
        .join(models.Video)
        .where(
            models.Clip.is_featured.is_(True),
            models.Clip.status == models.ClipStatus.READY,
            models.Clip.output_storage_key.is_not(None),
            models.Clip.deleted_at.is_(None),
            models.Video.deleted_at.is_(None),
        )
        .order_by(models.Clip.featured_order.asc(), models.Clip.featured_at.asc())
    ).all()


def _active_featured_rows(db: Session) -> list[models.Clip]:
    return [clip for clip in _featured_rows(db) if _clip_object_available(clip)]


def _thumbnail_key(clip: models.Clip) -> str | None:
    if clip.thumbnail_storage_key:
        return clip.thumbnail_storage_key
    if not clip.thumbnail_url or not R2_PUBLIC_URL:
        return None

    public_base = urlsplit(R2_PUBLIC_URL.rstrip("/"))
    thumbnail_url = urlsplit(clip.thumbnail_url)
    base_path = public_base.path.rstrip("/")
    if (thumbnail_url.scheme, thumbnail_url.netloc) != (public_base.scheme, public_base.netloc):
        return None
    if not thumbnail_url.path.startswith(f"{base_path}/"):
        return None
    return unquote(thumbnail_url.path[len(base_path) + 1:])


def _renumber_featured(db: Session) -> list[models.Clip]:
    rows = db.scalars(
        select(models.Clip)
        .join(models.Video)
        .where(
            models.Clip.is_featured.is_(True),
            models.Clip.status == models.ClipStatus.READY,
            models.Clip.output_storage_key.is_not(None),
            models.Clip.deleted_at.is_(None),
            models.Video.deleted_at.is_(None),
        )
        .order_by(models.Clip.featured_order.asc(), models.Clip.featured_at.asc())
    ).all()
    for index, clip in enumerate(rows, 1):
        clip.featured_order = index
    return rows


def _public_featured_clip(clip_id: str, db: Session) -> models.Clip:
    clip = db.scalar(
        select(models.Clip)
        .join(models.Video)
        .where(
            models.Clip.id == clip_id,
            models.Clip.is_featured.is_(True),
            models.Clip.deleted_at.is_(None),
            models.Video.deleted_at.is_(None),
        )
    )
    if clip is None or not _clip_object_available(clip):
        raise HTTPException(status_code=404, detail="Featured clip not found")
    return clip


def _stream_object(key: str, media_type: str, request: Request) -> StreamingResponse:
    range_header = request.headers.get("range")
    params = {"Bucket": storage.bucket, "Key": key}
    if range_header:
        if not range_header.startswith("bytes=") or "," in range_header:
            raise HTTPException(status_code=416, detail="Invalid byte range")
        params["Range"] = range_header
    try:
        item = storage.client.get_object(**params)
    except ClientError as error:
        error_code = str(error.response.get("Error", {}).get("Code", ""))
        if error_code in {"416", "InvalidRange"}:
            raise HTTPException(status_code=416, detail="Requested byte range is unavailable") from error
        logger.warning("Featured object stream failed error_type=%s", type(error).__name__)
        raise HTTPException(status_code=404, detail="Featured media is unavailable") from error
    except Exception as error:
        logger.warning("Featured object stream failed error_type=%s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Featured media is temporarily unavailable") from error

    body = item["Body"]

    def chunks():
        try:
            if hasattr(body, "iter_chunks"):
                yield from body.iter_chunks(chunk_size=1024 * 1024)
            else:
                while chunk := body.read(1024 * 1024):
                    yield chunk
        finally:
            body.close()

    headers = {
        "Accept-Ranges": item.get("AcceptRanges", "bytes"),
        "Cache-Control": "public, max-age=60",
    }
    if item.get("ContentLength") is not None:
        headers["Content-Length"] = str(item["ContentLength"])
    if item.get("ContentRange"):
        headers["Content-Range"] = item["ContentRange"]
    return StreamingResponse(chunks(), status_code=206 if item.get("ContentRange") else 200, media_type=media_type, headers=headers)


@router.get("/api/homepage/featured-clips")
def list_homepage_featured_clips(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _require_r2()
    items: list[dict[str, Any]] = []
    for clip in _featured_rows(db):
        if len(items) >= MAX_FEATURED_HOMEPAGE_CLIPS:
            break
        if not _clip_object_available(clip):
            continue

        try:
            video_url = f"/api/homepage/featured-clips/{clip.id}/video"
            thumbnail_key = _thumbnail_key(clip)
            thumbnail_url = f"/api/homepage/featured-clips/{clip.id}/thumbnail" if thumbnail_key and storage.exists(thumbnail_key) else None
        except Exception as error:
            logger.warning("Featured clip URL signing failed clip_id=%s error_type=%s", clip.id, type(error).__name__)
            continue

        items.append({
            "id": clip.id,
            "title": clip.title,
            "video_url": video_url,
            "thumbnail_url": thumbnail_url,
            "duration": round(float(clip.end_seconds - clip.start_seconds), 2),
            "display_order": clip.featured_order,
        })
    return items


@router.get("/api/homepage/featured-clips/{clip_id}/video")
def stream_homepage_featured_clip(clip_id: str, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    _require_r2()
    clip = _public_featured_clip(clip_id, db)
    return _stream_object(clip.output_storage_key, clip.output_mime_type or "video/mp4", request)


@router.get("/api/homepage/featured-clips/{clip_id}/thumbnail")
def stream_homepage_featured_thumbnail(clip_id: str, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    _require_r2()
    clip = _public_featured_clip(clip_id, db)
    key = _thumbnail_key(clip)
    if not key or not storage.exists(key):
        raise HTTPException(status_code=404, detail="Featured thumbnail not found")
    return _stream_object(key, "image/jpeg", request)


@router.post("/api/clips/{clip_id}/feature", status_code=status.HTTP_200_OK)
def feature_clip(
    clip_id: str,
    current_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_r2()
    clip = db.scalar(
        select(models.Clip)
        .join(models.Video)
        .where(
            models.Clip.id == clip_id,
            models.Clip.deleted_at.is_(None),
            models.Video.deleted_at.is_(None),
        )
    )
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not _clip_object_available(clip):
        raise HTTPException(status_code=409, detail="Only available, completed clips can be featured")
    if clip.is_featured:
        return {"id": clip.id, "is_featured": True, "featured_order": clip.featured_order}

    active = _active_featured_rows(db)
    if len(active) >= MAX_FEATURED_HOMEPAGE_CLIPS:
        raise HTTPException(
            status_code=409,
            detail="Maximum number of featured clips reached. Remove an existing featured clip before adding another.",
        )

    now = datetime.now(timezone.utc)
    clip.is_featured = True
    clip.featured_order = max((row.featured_order or 0 for row in active), default=0) + 1
    clip.featured_at = now
    db.commit()
    return {"id": clip.id, "is_featured": True, "featured_order": clip.featured_order}


@router.delete("/api/clips/{clip_id}/feature", status_code=status.HTTP_200_OK)
def unfeature_clip(
    clip_id: str,
    current_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    clip = db.scalar(select(models.Clip).where(models.Clip.id == clip_id, models.Clip.deleted_at.is_(None)))
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip.is_featured = False
    clip.featured_order = None
    clip.featured_at = None
    db.flush()
    rows = _renumber_featured(db)
    db.commit()
    return {"id": clip.id, "is_featured": False, "featured_count": len(rows)}


@router.patch("/api/homepage/featured-clips/order")
def order_homepage_featured_clips(
    payload: FeaturedClipOrderRequest,
    _: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = _active_featured_rows(db)
    requested = payload.clip_ids
    current_ids = {clip.id for clip in rows}
    if len(requested) != len(set(requested)) or set(requested) != current_ids:
        raise HTTPException(status_code=409, detail="Order must include each available featured clip exactly once")
    by_id = {clip.id: clip for clip in rows}
    for index, clip_id in enumerate(requested, 1):
        by_id[clip_id].featured_order = index
    db.commit()
    return {"clip_ids": requested}