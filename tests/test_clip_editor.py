import pytest
import shutil
import subprocess
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models
from database import Base
from routers.clip_editor import EditConfigurationRequest, create_clip_export, get_clip_export, load_clip_edit, save_clip_edit
from services.clip_editor import (
    normalize_edit_configuration,
    project_subtitles,
    render_clip_edit,
    remove_section,
    split_segment,
    trim_segment,
)
from services.jobs import claim_next_job


def test_trim_state_keeps_selected_source_range():
    assert trim_segment([{"start": 0, "end": 30}], 0, 3, 27) == [{"start": 3.0, "end": 27.0}]


def test_split_creates_two_source_relative_segments():
    assert split_segment([{"start": 0, "end": 30}], 12.5) == [
        {"start": 0.0, "end": 12.5},
        {"start": 12.5, "end": 30.0},
    ]


def test_remove_section_preserves_remaining_parts_in_sequence():
    assert remove_section([{"start": 0, "end": 30}], 10, 18) == [
        {"start": 0.0, "end": 10.0},
        {"start": 18.0, "end": 30.0},
    ]


def test_subtitle_timing_moves_after_trim_and_removed_gaps():
    configuration = normalize_edit_configuration({
        "segments": [{"start": 3, "end": 10}, {"start": 18, "end": 30}],
        "subtitles": [{"id": "cue-1", "start": 5, "end": 7, "text": "Act now"}],
    }, 30)

    assert project_subtitles(configuration) == [{
        "id": "cue-1:0",
        "start": 2.0,
        "end": 4.0,
        "text": "Act now",
    }]


def test_subtitle_crossing_a_cut_is_split_and_retimed():
    configuration = normalize_edit_configuration({
        "segments": [{"start": 0, "end": 5}, {"start": 8, "end": 12}],
        "subtitles": [{"id": "cue-1", "start": 4, "end": 10, "text": "One complete thought"}],
    }, 12)

    cues = project_subtitles(configuration)
    assert [(cue["start"], cue["end"]) for cue in cues] == [(4.0, 5.0), (5.0, 7.0)]


def test_normalizer_rejects_overlapping_segments_and_cues():
    with pytest.raises(ValueError, match="segments cannot overlap"):
        normalize_edit_configuration({
            "segments": [{"start": 0, "end": 8}, {"start": 7, "end": 12}],
        }, 12)


def test_normalizer_rejects_subtitles_that_cannot_fit_on_one_line():
    with pytest.raises(ValueError, match="too wide for one line"):
        normalize_edit_configuration({
            "segments": [{"start": 0, "end": 12}],
            "subtitles": [{"start": 1, "end": 3, "text": "This subtitle is intentionally much too long to fit on one line"}],
        }, 12)


def _editor_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'editor.db'}")
    Base.metadata.create_all(engine)
    return engine


def _ready_clip(db):
    owner = models.User(id="editor-owner", email="editor@example.com", password_hash="hash")
    other = models.User(id="other-owner", email="other@example.com", password_hash="hash")
    video = models.Video(
        id="editor-video",
        user_id=owner.id,
        title="Source",
        original_filename="source.mp4",
        status=models.VideoStatus.COMPLETED,
    )
    clip = models.Clip(
        id="editor-clip",
        video=video,
        title="Generated clip",
        start_seconds=10,
        end_seconds=30,
        status=models.ClipStatus.READY,
        output_url="https://media.example/clip.mp4",
        output_storage_key="exports/editor-video/clip.mp4",
    )
    db.add_all([owner, other, video, clip])
    db.commit()
    return owner, other, video, clip


def test_editor_state_is_saved_and_export_uses_existing_job_queue(tmp_path):
    engine = _editor_database(tmp_path)
    with Session(engine, expire_on_commit=False) as db:
        owner, _, video, clip = _ready_clip(db)
        loaded = load_clip_edit(clip.id, current_user=owner, db=db)
        assert loaded["configuration"] == {
            "segments": [{"start": 0.0, "end": 20.0}],
            "subtitles": [],
        }

        configuration = {
            "segments": [{"start": 0, "end": 8}, {"start": 10, "end": 20}],
            "subtitles": [{"id": "cue-1", "start": 2, "end": 4, "text": "Edited words"}],
        }
        payload = EditConfigurationRequest(configuration=configuration)
        save_clip_edit(clip.id, payload, current_user=owner, db=db)
        result = create_clip_export(clip.id, payload, current_user=owner, db=db)

        export = db.get(models.ClipExport, result["export_id"])
        job = db.get(models.ProcessingJob, result["job_id"])
        assert export.edit_configuration["segments"] == configuration["segments"]
        assert job.job_type == models.JobType.CLIP_RENDER
        assert get_clip_export(export.id, current_user=owner, db=db)["status"] == "queued"

        db.commit()
        claim_next_job(db)
        assert export.status == models.ExportStatus.PROCESSING
        assert video.status == models.VideoStatus.COMPLETED


def test_clip_editor_and_export_status_are_owner_scoped(tmp_path):
    engine = _editor_database(tmp_path)
    with Session(engine, expire_on_commit=False) as db:
        owner, other, _, clip = _ready_clip(db)
        with pytest.raises(HTTPException) as error:
            load_clip_edit(clip.id, current_user=other, db=db)
        assert error.value.status_code == 404

        configuration = {"segments": [{"start": 0, "end": 20}], "subtitles": []}
        export = create_clip_export(
            clip.id,
            EditConfigurationRequest(configuration=configuration),
            current_user=owner,
            db=db,
        )
        with pytest.raises(HTTPException) as error:
            get_clip_export(export["export_id"], current_user=other, db=db)
        assert error.value.status_code == 404


def test_editor_prefers_clean_editor_source_url(tmp_path, monkeypatch):
    from app import main

    class FakeStorage:
        def url_for(self, key):
            return f"https://media.example/{key}"

    monkeypatch.setattr(main, "storage", FakeStorage())
    engine = _editor_database(tmp_path)
    with Session(engine, expire_on_commit=False) as db:
        owner, _, _, clip = _ready_clip(db)
        clip.editor_source_storage_key = "exports/editor-video/editor-source/editor-clip.mp4"
        db.commit()

        loaded = load_clip_edit(clip.id, current_user=owner, db=db)

    assert loaded["clip"]["source_url"] == "https://media.example/exports/editor-video/editor-source/editor-clip.mp4"
    assert loaded["clip"]["output_url"] == "https://media.example/clip.mp4"
    assert loaded["clip"]["has_clean_editor_source"] is True


def test_legacy_clip_reports_baked_subtitle_limitation(tmp_path):
    engine = _editor_database(tmp_path)
    with Session(engine, expire_on_commit=False) as db:
        owner, _, _, clip = _ready_clip(db)

        loaded = load_clip_edit(clip.id, current_user=owner, db=db)

    assert loaded["clip"]["has_clean_editor_source"] is False


def test_real_ffmpeg_export_keeps_vertical_resolution_audio_and_subtitles(tmp_path):
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        pytest.skip("FFmpeg and ffprobe are required for the real export test")
    source = tmp_path / "source.mp4"
    output = tmp_path / "edited.mp4"
    subprocess.run([
        ffmpeg_bin,
        "-y",
        "-f", "lavfi", "-i", "color=c=0x0e5c52:s=1080x1920:r=12",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(source),
    ], check=True, capture_output=True)

    render_clip_edit(source, output, {
        "segments": [{"start": 0.1, "end": 0.9}, {"start": 1.1, "end": 1.8}],
        "subtitles": [{"id": "line-1", "start": 0.2, "end": 0.7, "text": "One line stays readable"}],
    }, 2)

    from integrations.ffmpeg import FFmpeg

    result = FFmpeg().probe(output)
    video = next(stream for stream in result["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(stream["codec_type"] == "audio" for stream in result["streams"])
    assert float(result["format"]["duration"]) == pytest.approx(1.5, abs=0.15)

    with pytest.raises(ValueError, match="subtitle cues cannot overlap"):
        normalize_edit_configuration({
            "segments": [{"start": 0, "end": 12}],
            "subtitles": [
                {"start": 1, "end": 3, "text": "First"},
                {"start": 2, "end": 4, "text": "Second"},
            ],
        }, 12)