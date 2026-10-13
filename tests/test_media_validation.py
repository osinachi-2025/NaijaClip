from __future__ import annotations

import pytest

from services.jobs import classify_failure, should_retry_job
from services.media_validation import classify_audio_quality, validate_video_file


def test_validate_video_file_rejects_unsupported_format(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("not a video", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported|format"):
        validate_video_file(source, filename="sample.txt", content_type="text/plain")


def test_classify_audio_quality_reports_missing_audio():
    assert classify_audio_quality({"streams": [{"codec_type": "video"}]}) == "no_audio"


def test_retry_policy_marks_transient_failures():
    assert should_retry_job("Deepgram timeout while transcribing audio") is True
    assert should_retry_job("unsupported format cannot be decoded") is False
    assert classify_failure("temporary R2 failure") == "transient"


def test_groq_json_generation_and_payload_size_failures_are_permanent():
    assert classify_failure("Groq output failure json_validate_failed") == "permanent"
    assert should_retry_job("Groq request payload too large (HTTP 413)") is False
    assert classify_failure("Groq HTTP 429 too many requests") == "transient"
