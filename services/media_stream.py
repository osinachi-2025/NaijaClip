from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse


_SINGLE_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK_SIZE = 1024 * 1024


def _range_bounds(value: str, size: int) -> tuple[int, int]:
    match = _SINGLE_RANGE.fullmatch(value)
    if not match or size <= 0:
        raise HTTPException(status_code=416, detail="Invalid byte range")
    start_text, end_text = match.groups()
    if not start_text:
        suffix = int(end_text or 0)
        if suffix <= 0:
            raise HTTPException(status_code=416, detail="Invalid byte range")
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(start_text)
        end = min(int(end_text), size - 1) if end_text else size - 1
    if start >= size or end < start:
        raise HTTPException(status_code=416, detail="Requested byte range is unavailable")
    return start, end


def stream_storage_object(storage, key: str, media_type: str, request: Request) -> StreamingResponse:
    range_header = request.headers.get("range")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}

    if hasattr(storage, "client"):
        params = {"Bucket": storage.bucket, "Key": key}
        if range_header:
            if not _SINGLE_RANGE.fullmatch(range_header):
                raise HTTPException(status_code=416, detail="Invalid byte range")
            params["Range"] = range_header
        try:
            item = storage.client.get_object(**params)
        except Exception as error:
            response = getattr(error, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            error_code = str(response.get("Error", {}).get("Code", ""))
            if status == 416 or error_code in {"416", "InvalidRange"}:
                raise HTTPException(status_code=416, detail="Requested byte range is unavailable") from error
            raise HTTPException(
                status_code=404 if status == 404 or error_code in {"404", "NoSuchKey", "NotFound"} else 503,
                detail="Media is unavailable",
            ) from error

        body = item["Body"]

        def r2_chunks():
            try:
                if hasattr(body, "iter_chunks"):
                    yield from body.iter_chunks(chunk_size=_CHUNK_SIZE)
                else:
                    while chunk := body.read(_CHUNK_SIZE):
                        yield chunk
            finally:
                body.close()

        if item.get("ContentLength") is not None:
            headers["Content-Length"] = str(item["ContentLength"])
        if item.get("ContentRange"):
            headers["Content-Range"] = item["ContentRange"]
        return StreamingResponse(
            r2_chunks(),
            status_code=206 if item.get("ContentRange") else 200,
            media_type=media_type,
            headers=headers,
        )

    path: Path = storage.path_for(key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media is unavailable")
    size = path.stat().st_size
    start, end = _range_bounds(range_header, size) if range_header else (0, size - 1)
    headers["Content-Length"] = str(end - start + 1)
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    def local_chunks():
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = source.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        local_chunks(),
        status_code=206 if range_header else 200,
        media_type=media_type,
        headers=headers,
    )