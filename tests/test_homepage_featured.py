from __future__ import annotations

from io import BytesIO
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app as web_app
import database
import models
import routers.auth as auth_router_module
import routers.homepage as homepage_router_module
from database import Base


class FakeR2Storage:
    def __init__(self, available_keys: set[str]):
        self.available_keys = available_keys
        self.bucket = "test-bucket"
        self.client = self

    def exists(self, key: str) -> bool:
        return key in self.available_keys

    def get_object(self, Bucket: str, Key: str, Range: str | None = None):
        content = f"R2 object:{Key}".encode()
        start, end = 0, len(content) - 1
        content_range = None
        if Range:
            range_parts = Range.removeprefix("bytes=").split("-", 1)
            start = int(range_parts[0] or 0)
            end = min(int(range_parts[1]) if range_parts[1] else end, end)
            content_range = f"bytes {start}-{end}/{len(content)}"
        body = BytesIO(content[start:end + 1])
        result = {
            "Body": body,
            "ContentLength": end - start + 1,
            "ContentType": "video/mp4",
            "AcceptRanges": "bytes",
        }
        if content_range:
            result["ContentRange"] = content_range
        return result


def _setup_database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'homepage-featured.db'}")
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{tmp_path / 'homepage-featured.db'}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False))
    monkeypatch.setattr(auth_router_module, "AUTH_COOKIE_SECURE", False)
    Base.metadata.create_all(engine)
    return engine


def _seed_clips(engine, count: int = 2):
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        admin = models.User(id="featured-admin", email="featured-admin@example.com", password_hash="hash", role=models.UserRole.ADMIN)
        owner = models.User(id="featured-owner", email="featured-owner@example.com", password_hash="hash")
        db.add_all([admin, owner])
        db.flush()
        video = models.Video(
            id="featured-source-video",
            user_id=owner.id,
            title="Private source title",
            original_filename="private-source.mp4",
            source_storage_key="sources/private-source.mp4",
            source_url="https://private.example.test/source.mp4",
            status=models.VideoStatus.COMPLETED,
        )
        clips = [
            models.Clip(
                id=f"featured-clip-{index}",
                video=video,
                title=f"Public demo {index}",
                start_seconds=0,
                end_seconds=42 + index,
                status=models.ClipStatus.READY,
                output_url=f"https://bucket.example.test/private/clip-{index}.mp4",
                output_storage_key=f"exports/owner/video/clip-{index}.mp4",
                thumbnail_url=f"https://bucket.example.test/exports/owner/video/thumbnail-{index}.jpg",
                thumbnail_storage_key=f"exports/owner/video/thumbnail-{index}.jpg",
            )
            for index in range(1, count + 1)
        ]
        db.add_all([video, *clips])
        db.commit()
    return admin, owner, clips


def test_admin_can_feature_order_and_remove_clips_without_leaking_source_data(tmp_path, monkeypatch):
    engine = _setup_database(tmp_path, monkeypatch)
    admin, owner, clips = _seed_clips(engine)
    monkeypatch.setattr(auth_router_module, "authenticate_user", lambda email, _password: admin if email == admin.email else None)
    keys = {clip.output_storage_key for clip in clips} | {clip.thumbnail_storage_key for clip in clips}
    monkeypatch.setattr(homepage_router_module, "storage", FakeR2Storage(keys))

    with TestClient(web_app.app) as client:
        assert client.post("/auth/login", json={"email": admin.email, "password": "test"}).status_code == 200
        dashboard_clips = client.get("/api/clips").json()["clips"]
        assert {clip["id"] for clip in dashboard_clips} == {clip.id for clip in clips}
        for clip in clips:
            response = client.post(f"/api/clips/{clip.id}/feature")
            assert response.status_code == 200

        public = client.get("/api/homepage/featured-clips")
        assert public.status_code == 200
        items = public.json()
        assert [item["id"] for item in items] == [clip.id for clip in clips]
        assert set(items[0]) == {"id", "title", "video_url", "thumbnail_url", "duration", "display_order"}
        assert items[0]["duration"] == 43.0
        assert items[0]["video_url"] == f"/api/homepage/featured-clips/{clips[0].id}/video"
        assert items[0]["thumbnail_url"] == f"/api/homepage/featured-clips/{clips[0].id}/thumbnail"
        assert "X-Amz" not in str(items)
        assert "private-source" not in str(items)
        assert owner.id not in str(items)

        media = client.get(items[0]["video_url"], headers={"Range": "bytes=0-4"})
        assert media.status_code == 206
        assert media.headers["accept-ranges"] == "bytes"
        assert media.headers["content-range"].startswith("bytes 0-4/")
        assert media.content.startswith(b"R2 ob")
        assert client.get(items[0]["thumbnail_url"]).status_code == 200

        reordered = client.patch(
            "/api/homepage/featured-clips/order",
            json={"clip_ids": [clips[1].id, clips[0].id]},
        )
        assert reordered.status_code == 200
        assert [item["id"] for item in client.get("/api/homepage/featured-clips").json()] == [clips[1].id, clips[0].id]

        removed = client.delete(f"/api/clips/{clips[1].id}/feature")
        assert removed.status_code == 200
        remaining = client.get("/api/homepage/featured-clips").json()
        assert [item["id"] for item in remaining] == [clips[0].id]
        assert remaining[0]["display_order"] == 1


def test_non_admin_cannot_feature_and_only_ready_existing_r2_objects_qualify(tmp_path, monkeypatch):
    engine = _setup_database(tmp_path, monkeypatch)
    admin, owner, clips = _seed_clips(engine)
    monkeypatch.setattr(auth_router_module, "authenticate_user", lambda email, _password: owner if email == owner.email else None)
    monkeypatch.setattr(homepage_router_module, "storage", FakeR2Storage({clips[0].output_storage_key}))

    with TestClient(web_app.app) as client:
        assert client.post("/auth/login", json={"email": owner.email, "password": "test"}).status_code == 200
        assert client.post(f"/api/clips/{clips[0].id}/feature").status_code == 403

        client.post("/auth/logout")
        monkeypatch.setattr(auth_router_module, "authenticate_user", lambda _email, _password: admin)
        assert client.post("/auth/login", json={"email": admin.email, "password": "test"}).status_code == 200
        missing_object = client.post(f"/api/clips/{clips[1].id}/feature")
        assert missing_object.status_code == 409

        with database.SessionLocal() as db:
            clip = db.get(models.Clip, clips[0].id)
            clip.status = models.ClipStatus.FAILED
            db.commit()
        not_ready = client.post(f"/api/clips/{clips[0].id}/feature")
        assert not_ready.status_code == 409
        assert client.get("/api/homepage/featured-clips").json() == []


def test_feature_limit_and_missing_objects_do_not_break_public_list(tmp_path, monkeypatch):
    engine = _setup_database(tmp_path, monkeypatch)
    admin, _, clips = _seed_clips(engine)
    monkeypatch.setattr(auth_router_module, "authenticate_user", lambda _email, _password: admin)
    monkeypatch.setattr(homepage_router_module, "MAX_FEATURED_HOMEPAGE_CLIPS", 1)
    storage = FakeR2Storage({clip.output_storage_key for clip in clips})
    monkeypatch.setattr(homepage_router_module, "storage", storage)

    with TestClient(web_app.app) as client:
        assert client.post("/auth/login", json={"email": admin.email, "password": "test"}).status_code == 200
        assert client.post(f"/api/clips/{clips[0].id}/feature").status_code == 200
        limited = client.post(f"/api/clips/{clips[1].id}/feature")
        assert limited.status_code == 409
        assert "Maximum number of featured clips" in limited.json()["detail"]

        storage.available_keys.remove(clips[0].output_storage_key)
        assert client.get("/api/homepage/featured-clips").json() == []