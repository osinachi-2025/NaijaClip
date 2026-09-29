from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.main as video_api
import app as web_app
import models
from database import Base


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