from __future__ import annotations

from io import BytesIO

import integrations.storage as storage_mod


def test_r2_storage_sets_extended_read_timeout(monkeypatch):
    captured = {}

    class DummyClient:
        pass

    def fake_client(*args, **kwargs):
        captured.update(kwargs)
        return DummyClient()

    monkeypatch.setattr(storage_mod, "R2_BUCKET_NAME", "demo-bucket")
    monkeypatch.setattr(storage_mod, "R2_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setattr(storage_mod, "R2_ACCESS_KEY_ID", "access-key")
    monkeypatch.setattr(storage_mod, "R2_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setattr(storage_mod.boto3, "client", fake_client)

    storage_mod.R2Storage()

    assert "config" in captured
    assert captured["config"].read_timeout >= 300
    assert captured["config"].connect_timeout >= 30


def test_generated_clip_upload_and_exists_share_client_bucket_and_key(monkeypatch):
    captured = {}

    class MemoryR2Client:
        def __init__(self):
            self.objects = {}
            self.uploads = []
            self.heads = []

        def upload_fileobj(self, fileobj, bucket, key, *, ExtraArgs):
            self.uploads.append((bucket, key, ExtraArgs))
            self.objects[(bucket, key)] = fileobj.read()

        def head_object(self, *, Bucket, Key):
            self.heads.append((Bucket, Key))
            if (Bucket, Key) not in self.objects:
                raise storage_mod.ClientError(
                    {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                    "HeadObject",
                )
            return {"ContentLength": len(self.objects[(Bucket, Key)])}

    client = MemoryR2Client()

    def fake_client(service_name, **kwargs):
        captured.update(service_name=service_name, **kwargs)
        return client

    monkeypatch.setattr(storage_mod, "R2_BUCKET_NAME", "naijaclip-test-bucket")
    monkeypatch.setattr(storage_mod, "R2_ENDPOINT_URL", "https://account-id.r2.cloudflarestorage.com")
    monkeypatch.setattr(storage_mod, "R2_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setattr(storage_mod, "R2_SECRET_ACCESS_KEY", "test-secret-key")
    monkeypatch.setattr(storage_mod.boto3, "client", fake_client)

    storage = storage_mod.R2Storage()
    object_key = "exports/user-id/video-id/clip-1.mp4"
    storage.client.upload_fileobj(
        BytesIO(b"generated clip bytes"),
        storage.bucket,
        object_key,
        ExtraArgs={"ContentType": "video/mp4"},
    )

    assert storage.exists(object_key) is True
    assert captured["service_name"] == "s3"
    assert captured["endpoint_url"] == "https://account-id.r2.cloudflarestorage.com"
    assert captured["region_name"] == "auto"
    assert client.uploads == [("naijaclip-test-bucket", object_key, {"ContentType": "video/mp4"})]
    assert client.heads == [("naijaclip-test-bucket", object_key)]
