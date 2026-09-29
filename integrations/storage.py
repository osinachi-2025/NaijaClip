from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from core.config import (
    MEDIA_ROOT,
    R2_ACCESS_KEY_ID,
    R2_BUCKET_NAME,
    R2_ENDPOINT_URL,
    R2_SECRET_ACCESS_KEY,
)


class LocalStorage:
    def __init__(self, root: Path = MEDIA_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("invalid storage key")
        return path

    def save_upload(self, source, filename: str, user_id: str, content_type: str | None = None) -> str:
        suffix = Path(filename).suffix.lower()
        key = f"sources/{user_id}/{uuid4()}{suffix}"
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        return key

    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def delete(self, key: str) -> None:
        path = self.path_for(key)
        if path.exists():
            path.unlink()

    def url_for(self, key: str) -> str:
        return f"/media/{key}"

    def download_to_path(self, key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            with self.path_for(key).open("rb") as source:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        return destination


class R2Storage:
    def __init__(self):
        missing = [
            name for name, value in {
                "R2_BUCKET_NAME": R2_BUCKET_NAME,
                "R2_ENDPOINT_URL": R2_ENDPOINT_URL,
                "R2_ACCESS_KEY_ID": R2_ACCESS_KEY_ID,
                "R2_SECRET_ACCESS_KEY": R2_SECRET_ACCESS_KEY,
            }.items() if not value
        ]
        if missing:
            raise RuntimeError(f"missing R2 configuration: {', '.join(missing)}")
        self.bucket = R2_BUCKET_NAME
        self.client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT_URL,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(
                connect_timeout=30,
                read_timeout=300,
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
        )

    def save_upload(self, source, filename: str, user_id: str, content_type: str | None = None) -> str:
        suffix = Path(filename).suffix.lower()
        key = f"sources/{user_id}/{uuid4()}{suffix}"
        extra_args = {"ContentType": content_type} if content_type else {}
        self.client.upload_fileobj(source, self.bucket, key, ExtraArgs=extra_args)
        return key

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def download_to_path(self, key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(destination))
        return destination

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def url_for(self, key: str) -> str | None:
        from core.config import R2_PUBLIC_URL
        return f"{R2_PUBLIC_URL.rstrip('/')}/{key}" if R2_PUBLIC_URL else None

