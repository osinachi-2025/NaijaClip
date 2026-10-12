from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.main as video_api
import app as web_app
import models
from database import Base, get_db
from integrations.storage import LocalStorage
from routers.auth import get_current_active_user
from services.plan_service import monthly_video_usage
from services.plan_service import monthly_limit_for_plan


class _TestStorage:
    def __init__(self):
        self.deleted = []

    def save_upload(self, source, filename, user_id, content_type):
        return f"sources/{user_id}/{filename}"

    def delete(self, key):
        self.deleted.append(key)


def test_video_cancel_and_delete_routes_are_registered():
    registered_routes = {
        (route.path, method)
        for route in web_app.app.routes
        for method in getattr(route, "methods", set())
    }

    assert ("/api/videos/{video_id}/cancel", "POST") in registered_routes
    assert ("/api/videos/{video_id}", "DELETE") in registered_routes


def test_video_upload_is_persisted_for_authenticated_dashboard_user(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'videos.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(video_api, "storage", _TestStorage())
    monkeypatch.setattr(
        video_api,
        "validate_video_file",
        lambda *args, **kwargs: {
            "mime_type": "video/mp4",
            "source_file_size_bytes": 5,
            "duration": 12.5,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "video_codec": "h264",
            "audio_codec": "aac",
            "audio_status": "ok",
        },
    )

    with Session(engine, expire_on_commit=False) as db:
        user = models.User(id="account-1", email="owner@example.com", password_hash="hash")
        another_user = models.User(id="account-2", email="other@example.com", password_hash="hash")
        db.add_all([user, another_user])
        db.commit()

        result = video_api.create_video_job(
            file=UploadFile(filename="podcast.mp4", file=BytesIO(b"video")),
            title="Podcast",
            current_user=user,
            db=db,
        )

        db.add(
            models.Video(
                user_id=another_user.id,
                title="Private upload",
                original_filename="private.mp4",
            )
        )
        db.commit()

        dashboard_data = video_api.list_videos(current_user=user, db=db)

    assert [video["id"] for video in dashboard_data["videos"]] == [result["video_id"]]
    assert dashboard_data["videos"][0]["title"] == "Podcast"
    assert dashboard_data["videos"][0]["duration_seconds"] == 12.5
    assert user.videos_used == 1


def test_video_upload_enforces_monthly_plan_limit_before_storage_write(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    storage = _TestStorage()
    monkeypatch.setattr(video_api, "storage", storage)
    monkeypatch.setattr(video_api, "monthly_limit_for_plan", lambda _plan: 3)
    monkeypatch.setattr(
        video_api,
        "validate_video_file",
        lambda *args, **kwargs: {"mime_type": "video/mp4", "source_file_size_bytes": 5, "duration": 12.5},
    )

    with Session(engine, expire_on_commit=False) as db:
        user = models.User(id="quota-user", email="quota@example.com", password_hash="hash")
        db.add(user)
        for index in range(3):
            db.add(models.Video(
                user_id=user.id,
                title=f"Video {index}",
                original_filename=f"video-{index}.mp4",
            ))
        db.commit()

        with pytest.raises(Exception) as error:
            video_api.create_video_job(
                file=UploadFile(filename="extra.mp4", file=BytesIO(b"video")),
                title="Extra",
                current_user=user,
                db=db,
            )

    assert getattr(error.value, "status_code", None) == 429
    assert storage.deleted == []


def test_monthly_usage_counts_only_current_utc_month_and_owner(tmp_path):
    from datetime import datetime, timezone

    engine = create_engine(f"sqlite:///{tmp_path / 'monthly-usage.db'}")
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    with Session(engine) as db:
        owner = models.User(id="monthly-owner", email="monthly@example.com", password_hash="hash")
        other = models.User(id="other-owner", email="other-monthly@example.com", password_hash="hash")
        db.add_all([
            owner,
            other,
            models.Video(user_id=owner.id, title="Current", original_filename="current.mp4", created_at=now),
            models.Video(user_id=owner.id, title="Previous", original_filename="previous.mp4", created_at=datetime(2026, 9, 30, tzinfo=timezone.utc)),
            models.Video(user_id=other.id, title="Other", original_filename="other.mp4", created_at=now),
        ])
        db.commit()

        assert monthly_video_usage(db, owner.id, now=now) == 1


def test_monthly_plan_limits_accept_enum_and_string_values():
    assert monthly_limit_for_plan(models.PlanCode.FREE) == 3
    assert monthly_limit_for_plan(models.PlanCode.PRO) == 50
    assert monthly_limit_for_plan("pro") == 50


def test_video_can_be_cancelled_only_by_owner(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'cancel.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = models.User(id="owner", email="owner@example.com", password_hash="hash")
        other = models.User(id="other", email="other@example.com", password_hash="hash")
        video = models.Video(user_id=owner.id, title="Source", original_filename="source.mp4")
        job = models.ProcessingJob(video=video, job_type=models.JobType.EXPORT, status=models.JobStatus.QUEUED)
        db.add_all([owner, other, video, job])
        db.commit()

        result = video_api.cancel_video(video.id, current_user=owner, db=db)
        assert result["status"] == models.JobStatus.CANCELLED.value
        assert video.status == models.VideoStatus.CANCELLED

        with pytest.raises(Exception):
            video_api.cancel_video(video.id, current_user=other, db=db)


def test_video_delete_soft_deletes_and_removes_storage_objects(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'delete.db'}")
    Base.metadata.create_all(engine)
    test_storage = _TestStorage()
    monkeypatch.setattr(video_api, "storage", test_storage)
    with Session(engine, expire_on_commit=False) as db:
        owner = models.User(id="owner", email="owner@example.com", password_hash="hash")
        video = models.Video(user_id=owner.id, title="Source", original_filename="source.mp4", source_storage_key="sources/source.mp4")
        clip = models.Clip(
            video=video,
            title="Generated",
            start_seconds=1,
            end_seconds=10,
            output_storage_key="exports/clip.mp4",
            editor_source_storage_key="exports/editor-source.mp4",
            is_featured=True,
            featured_order=1,
        )
        db.add_all([owner, video, clip])
        db.commit()

        video_api.delete_video(video.id, current_user=owner, db=db)

        assert video.deleted_at is not None
        assert clip.is_featured is False
        assert clip.featured_order is None
        assert test_storage.deleted == ["sources/source.mp4", "exports/clip.mp4", "exports/editor-source.mp4"]
        assert video_api.list_videos(current_user=owner, db=db)["videos"] == []


def test_clip_list_repairs_only_verified_missing_playback_urls(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'clip-url-repair.db'}")
    Base.metadata.create_all(engine)

    class ExistingObjectStorage:
        def __init__(self):
            self.keys = {"exports/clip.mp4", "exports/thumbnail.jpg"}

        def exists(self, key):
            return key in self.keys

        def url_for(self, key):
            return f"https://media.example.test/{key}"

    monkeypatch.setattr(video_api, "storage", ExistingObjectStorage())
    with Session(engine, expire_on_commit=False) as db:
        owner = models.User(id="owner", email="owner@example.com", password_hash="hash")
        video = models.Video(user_id=owner.id, title="Source", original_filename="source.mp4")
        clip = models.Clip(
            video=video,
            title="Generated",
            start_seconds=1,
            end_seconds=10,
            status=models.ClipStatus.READY,
            output_storage_key="exports/clip.mp4",
            thumbnail_storage_key="exports/thumbnail.jpg",
        )
        missing_clip = models.Clip(
            video=video,
            title="Missing object",
            start_seconds=10,
            end_seconds=20,
            status=models.ClipStatus.READY,
            output_storage_key="exports/missing.mp4",
        )
        db.add_all([owner, video, clip, missing_clip])
        db.commit()

        result = video_api.list_clips(current_user=owner, db=db)
        clips_by_title = {item["title"]: item for item in result["clips"]}

        assert clips_by_title["Generated"]["output_url"] == f"/api/clips/{clip.id}/video"
        assert clips_by_title["Generated"]["thumbnail_url"] == f"/api/clips/{clip.id}/thumbnail"
        assert clips_by_title["Generated"]["playback_available"] is True
        assert clips_by_title["Missing object"]["output_url"] is None
        assert clips_by_title["Missing object"]["playback_available"] is False
        assert clip.output_url == clips_by_title["Generated"]["output_url"]
        assert missing_clip.output_url is None


def test_private_clip_stream_supports_ranges_and_checks_owner(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'private-stream.db'}")
    Base.metadata.create_all(engine)
    storage = LocalStorage(tmp_path / "private-media")
    monkeypatch.setattr(video_api, "storage", storage)
    object_key = "exports/owner/video/clip.mp4"
    object_path = storage.path_for(object_key)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(b"private-video-bytes")
    with Session(engine, expire_on_commit=False) as db:
        owner = models.User(id="stream-owner", email="stream-owner@example.com", password_hash="hash")
        stranger = models.User(id="stream-stranger", email="stream-stranger@example.com", password_hash="hash")
        video = models.Video(user_id=owner.id, title="Private", original_filename="private.mp4")
        clip = models.Clip(
            id="private-clip",
            video=video,
            title="Private clip",
            start_seconds=0,
            end_seconds=5,
            status=models.ClipStatus.READY,
            output_storage_key=object_key,
        )
        db.add_all([owner, stranger, video, clip])
        db.commit()

    def override_db():
        with Session(engine, expire_on_commit=False) as db:
            yield db

    web_app.app.dependency_overrides[get_db] = override_db
    web_app.app.dependency_overrides[get_current_active_user] = lambda: owner
    try:
        with TestClient(web_app.app) as client:
            owner_response = client.get(
                f"/api/clips/{clip.id}/video",
                headers={"Range": "bytes=2-6"},
            )
            assert owner_response.status_code == 206
            assert owner_response.content == b"ivate"
            assert owner_response.headers["cache-control"] == "private, no-store"

            web_app.app.dependency_overrides[get_current_active_user] = lambda: stranger
            stranger_response = client.get(f"/api/clips/{clip.id}/video")

        assert stranger_response.status_code == 404
    finally:
        web_app.app.dependency_overrides.clear()