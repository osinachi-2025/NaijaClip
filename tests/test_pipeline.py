import sys
from types import SimpleNamespace

import pytest
from core.config import CAMERA_DEAD_ZONE, TRACK_LOST_HOLD_FRAMES
import pipeline.orchestrator as orchestrator_module
import pipeline.stages as stages_module
from pipeline.context import PipelineContext
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.scoring import score_candidate
from pipeline.stages import (
    SUBTITLE_FORCE_STYLE,
    PipelineStages,
    _append_final_video_filter,
    _build_subtitle_chunks,
    _crop_axis_expression,
    _deepgram_word_segments,
    _simplify_camera_points,
    _smooth_camera_trajectory,
)
from pipeline.validation import CandidateScores, ClipCandidate, clip_duration_range, remove_overlaps, validate_candidates


def scores(value=8):
    return CandidateScores(**{field: value for field in (
        "hook", "value", "emotional_impact", "clarity", "story_completeness",
        "quotability", "engagement_potential", "payoff", "context_independence",
        "clip_efficiency",
    )})


def test_invalid_timestamps_are_rejected():
    candidate = ClipCandidate(start=10, end=20, reason="valid", scores=scores())
    assert validate_candidates([candidate], source_duration=15) == []


def test_overlapping_candidates_are_deduplicated():
    candidates = [
        ClipCandidate(start=0, end=20, reason="first", scores=scores()),
        ClipCandidate(start=10, end=30, reason="overlap", scores=scores(9)),
    ]
    valid = remove_overlaps(validate_candidates(candidates, source_duration=40, minimum_duration=1))
    assert len(valid) == 1


def test_final_score_uses_python_weights():
    candidate = ClipCandidate(start=0, end=40, reason="complete", scores=scores())
    scored = score_candidate(validate_candidates([candidate], 60)[0], speech_density=8)
    assert 7.5 <= scored.final_score <= 8.5


def test_candidate_stops_at_natural_pause_without_duration_padding():
    candidate = ClipCandidate(start=12, end=15, reason="needs broader context", scores=scores())
    segments = [
        {"start": 10, "end": 18, "text": "natural boundary"},
        {"start": 22, "end": 28, "text": "later sentence"},
    ]
    valid = validate_candidates([candidate], source_duration=40, minimum_duration=20, transcript_segments=segments)
    assert len(valid) == 1
    assert valid[0].candidate.start == 10.0
    assert valid[0].candidate.end == 18.0


def test_candidate_end_mid_sentence_expands_through_punctuation():
    candidate = ClipCandidate(start=10, end=12.5, reason="pricing mistake", scores=scores())
    segments = [
        {"start": 10, "end": 12, "text": "The biggest mistake agents make is"},
        {"start": 12.2, "end": 15, "text": "pricing a property without checking its market value."},
    ]

    valid = validate_candidates([candidate], 30, transcript_segments=segments)

    assert len(valid) == 1
    assert (valid[0].candidate.start, valid[0].candidate.end) == (10, 15)


def test_candidate_start_mid_sentence_expands_back_to_sentence_start():
    candidate = ClipCandidate(start=2.5, end=5, reason="property advice", scores=scores())
    segments = [
        {"start": 0, "end": 1, "text": "The"},
        {"start": 1, "end": 2, "text": "biggest mistake"},
        {"start": 2, "end": 3, "text": "is"},
        {"start": 3, "end": 4, "text": "pricing"},
        {"start": 4, "end": 5, "text": "without understanding the market."},
    ]

    valid = validate_candidates([candidate], 30, transcript_segments=segments)

    assert len(valid) == 1
    assert (valid[0].candidate.start, valid[0].candidate.end) == (0, 5)


def test_complete_sentence_is_not_expanded_to_neighboring_sentence():
    candidate = ClipCandidate(start=0, end=2, reason="market advice", scores=scores())
    segments = [
        {"start": 0, "end": 1, "text": "Market prices"},
        {"start": 1, "end": 2, "text": "change every year."},
        {"start": 4, "end": 6, "text": "Agents should research carefully."},
    ]

    valid = validate_candidates([candidate], 30, transcript_segments=segments)

    assert len(valid) == 1
    assert (valid[0].candidate.start, valid[0].candidate.end) == (0, 2)


def test_long_complete_thought_can_exceed_normal_clip_target():
    candidate = ClipCandidate(start=15, end=40, reason="complete real estate thought", scores=scores())
    segments = [
        {"start": 10, "end": 35, "text": "The complete explanation includes the market context and the reasons buyers should compare the location, title, access, development plan, and the true cost before they make a decision"},
        {"start": 35.2, "end": 70, "text": "because each of those details changes the value and the risks, which is why a careful buyer checks all of them before making an offer."},
    ]

    valid = validate_candidates([candidate], 100, minimum_duration=8, maximum_duration=20, transcript_segments=segments)

    assert len(valid) == 1
    assert (valid[0].candidate.start, valid[0].candidate.end) == (10, 70)
    assert valid[0].duration == 60


def test_word_timestamp_boundaries_never_cut_a_word_or_connector():
    candidate = ClipCandidate(start=0.5, end=1.0, reason="property location", scores=scores())
    segments = [
        {"start": 0.0, "end": 0.4, "text": "The"},
        {"start": 0.4, "end": 0.8, "text": "property"},
        {"start": 0.8, "end": 1.1, "text": "because"},
        {"start": 4.2, "end": 4.8, "text": "location"},
        {"start": 4.8, "end": 5.4, "text": "matters."},
    ]

    valid = validate_candidates([candidate], 15, transcript_segments=segments)

    assert len(valid) == 1
    assert (valid[0].candidate.start, valid[0].candidate.end) == (0.0, 5.4)


def test_semantic_metadata_is_returned_for_complete_candidate():
    candidate = ClipCandidate(start=12, end=15, reason="needs broader context", scores=scores())
    segments = [
        {"start": 10, "end": 18, "text": "One of the biggest reasons my business failed was because I mismanaged cash flow."},
        {"start": 22, "end": 28, "text": "I kept reinvesting everything and eventually I could not pay suppliers."},
    ]
    valid = validate_candidates([candidate], source_duration=40, minimum_duration=20, transcript_segments=segments)
    assert len(valid) == 1
    assert valid[0].semantic_metadata.is_complete_thought is True
    assert valid[0].semantic_metadata.ends_on_sentence_boundary is True
    assert valid[0].semantic_metadata.contains_conclusion_or_answer is True


def test_clip_duration_scales_with_video_runtime():
    short_min, short_max = clip_duration_range(18)
    long_min, long_max = clip_duration_range(420)
    assert short_max < long_max
    assert short_min < long_min
    assert short_min >= 6
    assert long_min >= 20


def test_reframing_uses_center_crop_without_tracking():
    context = PipelineContext(
        job=SimpleNamespace(id="job-1"),
        video=SimpleNamespace(width=1920, height=1080),
        media_info={"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]},
        selected_clips=[SimpleNamespace()],
    )

    PipelineStages(db=None, storage=None).reframing(context)

    assert context.selected_clips[0].reframe == {"x": 656, "y": 0, "width": 607, "height": 1079}


def test_reframing_uses_tracking_coordinates_for_speaker_position():
    selected_clip = SimpleNamespace(tracking={
        "tracks": [
            {"frame": 0, "box": (1200, 200, 200, 300), "ok": True},
            {"frame": 1, "box": (1300, 220, 200, 300), "ok": True},
        ],
    })
    context = PipelineContext(
        job=SimpleNamespace(id="job-1"),
        video=SimpleNamespace(width=1920, height=1080),
        media_info={"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]},
        selected_clips=[selected_clip],
    )

    PipelineStages(db=None, storage=None).reframing(context)

    assert selected_clip.reframe["x"] == 996
    assert selected_clip.reframe["x_expression"] == "996"
    assert len(selected_clip.smoothed_camera_path) == 2


def test_reframing_holds_last_valid_crop_after_tracking_loss():
    selected_clip = SimpleNamespace(tracking={
        "width": 854,
        "height": 480,
        "fps": 25,
        "tracks": [
            {"frame": 0, "box": (400, 80, 100, 300), "ok": True},
            *({"frame": frame, "box": None, "ok": False} for frame in range(1, 90)),
        ],
    })
    context = PipelineContext(
        job=SimpleNamespace(id="job-1"),
        video=SimpleNamespace(width=854, height=480),
        media_info={"streams": [{"codec_type": "video", "width": 854, "height": 480}]},
        selected_clips=[selected_clip],
    )

    PipelineStages(db=None, storage=None).reframing(context)

    assert selected_clip.reframe["x"] == 315
    assert "315" in selected_clip.reframe["x_expression"]
    assert "292" in selected_clip.reframe["x_expression"]


def test_dynamic_crop_expression_preserves_path_without_fixed_keyframe_cap():
    linear_points = [(frame, frame // 2) for frame in range(500)]
    assert _crop_axis_expression(linear_points).count("if(") == 1

    varying_points = [(frame, (frame % 20) * 4) for frame in range(500)]
    simplified = _simplify_camera_points(varying_points)
    assert len(simplified) > 48
    for left, right in zip(simplified, simplified[1:]):
        left_frame, left_value = left
        right_frame, right_value = right
        for frame, value in varying_points[left_frame + 1:right_frame]:
            expected = left_value + (right_value - left_value) * (frame - left_frame) / (right_frame - left_frame)
            assert abs(value - expected) <= 0.75


def test_camera_trajectory_smooths_motion_without_modifying_raw_tracking():
    raw_tracks = [
        {"frame": 0, "box": (1200, 200, 200, 300), "ok": True},
        *({"frame": frame, "box": (1600, 200, 200, 300), "ok": True} for frame in range(1, 80)),
    ]
    original_tracks = [dict(track) for track in raw_tracks]

    camera = _smooth_camera_trajectory(
        raw_tracks,
        crop_width=607,
        crop_height=1079,
        frame_width=1920,
        frame_height=1080,
        center_crop=(656, 0),
        fps=25,
    )

    assert raw_tracks == original_tracks
    raw_target = 1600 + 100 - 607 // 2
    assert camera[-1]["x"] != raw_target
    assert camera[-1]["x"] > camera[0]["x"]
    assert max(abs(right["x"] - left["x"]) for left, right in zip(camera, camera[1:])) <= 5


def test_camera_dead_zone_suppresses_small_subject_motion():
    camera = _smooth_camera_trajectory(
        [
            {"frame": 0, "box": (1200, 200, 200, 300), "ok": True},
            {"frame": 1, "box": (1210, 200, 200, 300), "ok": True},
        ],
        crop_width=607,
        crop_height=1079,
        frame_width=1920,
        frame_height=1080,
        center_crop=(656, 0),
        fps=25,
    )

    assert camera[1]["x"] == camera[0]["x"]


def test_camera_rejects_tracking_jitter_without_changing_raw_points():
    tracking = [
        {"frame": frame, "box": (1200 + offset, 200, 200, 300), "ok": True}
        for frame, offset in enumerate((0, 30, -30, 25, -25, 10, -10, 0))
    ]
    original = [dict(point) for point in tracking]

    camera = _smooth_camera_trajectory(
        tracking,
        crop_width=607,
        crop_height=1079,
        frame_width=1920,
        frame_height=1080,
        center_crop=(656, 0),
        fps=30,
    )

    assert tracking == original
    assert len({point["x"] for point in camera}) == 1


def test_camera_follows_large_movement_without_teleporting():
    tracking = [
        *({"frame": frame, "box": (800, 200, 200, 300), "ok": True} for frame in range(30)),
        *({"frame": frame, "box": (1400, 200, 200, 300), "ok": True} for frame in range(30, 330)),
    ]

    camera = _smooth_camera_trajectory(
        tracking,
        crop_width=607,
        crop_height=1079,
        frame_width=1920,
        frame_height=1080,
        center_crop=(656, 0),
        fps=30,
    )

    target_x = 1400 + 100 - 607 // 2
    assert camera[-1]["x"] >= target_x - int(CAMERA_DEAD_ZONE * 607 / 1080) - 2
    assert max(abs(right["x"] - left["x"]) for left, right in zip(camera, camera[1:])) <= 5


def test_camera_holds_then_gradually_returns_after_tracking_loss():
    tracks = [
        {"frame": 0, "box": (1200, 200, 200, 300), "ok": True},
        *({"frame": frame, "box": None, "ok": False} for frame in range(1, 50)),
    ]
    camera = _smooth_camera_trajectory(
        tracks,
        crop_width=607,
        crop_height=1079,
        frame_width=1920,
        frame_height=1080,
        center_crop=(656, 0),
        fps=25,
    )

    assert camera[TRACK_LOST_HOLD_FRAMES]["x"] == camera[0]["x"]
    assert camera[TRACK_LOST_HOLD_FRAMES + 5]["x"] < camera[0]["x"]
    assert camera[-1]["x"] > 656
    assert camera[-1]["x"] < camera[0]["x"]


def test_camera_path_covers_all_frames_and_holds_missing_observations():
    tracking = {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "target_frames": 3600,
        "tracks": [
            {"frame": 0, "box": (800, 200, 200, 300), "ok": True},
            {"frame": 1, "box": (810, 200, 200, 300), "ok": True},
        ],
    }
    clip = SimpleNamespace(tracking=tracking)
    context = PipelineContext(
        job=SimpleNamespace(id="job-1"),
        video=SimpleNamespace(width=1920, height=1080),
        media_info={"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]},
        selected_clips=[clip],
    )

    PipelineStages(db=None, storage=None).reframing(context)

    assert len(tracking["tracks"]) == 2
    assert len(clip.smoothed_camera_path) == 3600
    assert clip.smoothed_camera_path[-1]["frame"] == 3599
    assert clip.smoothed_camera_path[-1]["x"] == 656


def test_orchestrator_runs_detection_tracking_and_reframing_before_render(monkeypatch):
    events = []
    job = SimpleNamespace(id="job-1", video=SimpleNamespace())
    db = SimpleNamespace(get=lambda *_args: job, commit=lambda: None)
    monkeypatch.setattr(orchestrator_module, "ensure_job_active", lambda *_args: None)
    monkeypatch.setattr(orchestrator_module, "update_job", lambda _db, _job_id, *, stage, **_kwargs: events.append(stage))
    handler_names = {
        "ingestion", "input_validation", "media_analysis", "audio_extraction",
        "deepgram_transcription", "transcript_validation", "groq_selection",
        "candidate_scoring", "clip_extraction", "yolo_detection", "opencv_tracking",
        "reframing", "captions", "audio_processing", "ffmpeg_rendering",
        "output_validation", "thumbnail_generation", "export", "cleanup",
    }
    handlers = {name: lambda _context: None for name in handler_names}

    PipelineOrchestrator(db, stage_handlers=handlers).process_video("job-1")

    assert events.index("clip_extraction") < events.index("yolo_detection")
    assert events.index("yolo_detection") < events.index("opencv_tracking")
    assert events.index("opencv_tracking") < events.index("reframing")
    assert events.index("reframing") < events.index("ffmpeg_rendering")


def test_selected_clip_detection_flows_through_tracking_to_reframing(tmp_path, monkeypatch):
    class Coordinates(list):
        def tolist(self):
            return list(self)

    class FakeCapture:
        def __init__(self, frames):
            self.frames = iter(frames)
            self.released = False

        def isOpened(self):
            return True

        def read(self):
            return next(self.frames, (False, None))

        def get(self, _property):
            return 3

        def release(self):
            self.released = True

    class FakeTracker:
        def __init__(self):
            self.updates = iter(((1300, 200, 200, 300), (1400, 200, 200, 300)))

        def init(self, _frame, box):
            self.initial_box = box
            return True

        def update(self, _frame):
            return True, next(self.updates)

    frame = SimpleNamespace(shape=(1080, 1920, 3))
    selected_path = tmp_path / "selected-clip.mp4"
    source_path = tmp_path / "full-upload.mp4"
    selected_path.touch()
    source_path.touch()
    capture_paths = []

    class FakeCV2:
        CAP_PROP_FPS = 5
        CAP_PROP_FRAME_COUNT = 7

        def VideoCapture(self, path):
            capture_paths.append(path)
            frames = [(True, frame)] if len(capture_paths) == 1 else [(True, frame), (True, frame), (True, frame)]
            return FakeCapture(frames)

        def TrackerCSRT_create(self):
            return FakeTracker()

    detection_box = SimpleNamespace(
        cls=[0],
        xyxy=[Coordinates((1200.0, 200.0, 1400.0, 500.0))],
        conf=[0.95],
    )
    detector_calls = []

    class FakeDetector:
        def detect(self, image):
            detector_calls.append(image)
            return [SimpleNamespace(names={0: "person"}, boxes=[detection_box])]

    monkeypatch.setitem(sys.modules, "cv2", FakeCV2())
    monkeypatch.setattr(stages_module, "YOLODetector", lambda *_args, **_kwargs: FakeDetector())
    selected_clip = SimpleNamespace(extracted_path=selected_path)
    context = PipelineContext(
        job=SimpleNamespace(id="job-1"),
        video=SimpleNamespace(width=1920, height=1080),
        source_path=source_path,
        media_info={"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]},
        selected_clips=[selected_clip],
        extracted_clips=[source_path, selected_path],
    )
    stages = PipelineStages(db=None, storage=None)

    stages.yolo_detection(context)
    stages.opencv_tracking(context)
    stages.reframing(context)

    assert detector_calls == [frame]
    assert capture_paths == [str(selected_path), str(selected_path)]
    assert selected_clip.tracking["tracks"][-1]["box"] == (1400, 200, 200, 300)
    assert selected_clip.reframe["x"] == 996
    assert len(selected_clip.smoothed_camera_path) == 3
    assert selected_clip.tracking["tracks"][0]["box"] == (1200, 200, 200, 300)


def test_tracking_loss_reacquires_person_and_records_new_bbox(tmp_path, monkeypatch):
    class Coordinates(list):
        def tolist(self):
            return list(self)

    frame = SimpleNamespace(shape=(480, 854, 3))

    class FakeCapture:
        def __init__(self):
            self.frames = iter([(True, frame), (True, frame), (True, frame), (False, None)])

        def isOpened(self):
            return True

        def read(self):
            return next(self.frames)

        def get(self, _property):
            return 3

        def release(self):
            pass

    tracker_initializations = 0

    class FakeTracker:
        def init(self, _frame, _box):
            nonlocal tracker_initializations
            tracker_initializations += 1
            self.recovered = tracker_initializations > 1
            return True

        def update(self, _frame):
            return (True, (390, 100, 120, 330)) if self.recovered else (False, None)

    class FakeCV2:
        CAP_PROP_FPS = 5
        CAP_PROP_FRAME_COUNT = 7

        def VideoCapture(self, _path):
            return FakeCapture()

        def TrackerCSRT_create(self):
            return FakeTracker()

    detection = SimpleNamespace(
        cls=[0],
        xyxy=[Coordinates((390.0, 100.0, 510.0, 430.0))],
        conf=[0.96],
    )

    class FakeDetector:
        def __init__(self):
            self.calls = 0

        def detect(self, _frame):
            self.calls += 1
            return [SimpleNamespace(names={0: "person"}, boxes=[detection])]

    monkeypatch.setitem(sys.modules, "cv2", FakeCV2())
    clip_path = tmp_path / "selected.mp4"
    clip_path.touch()
    selected_clip = SimpleNamespace(
        extracted_path=clip_path,
        detections=[{"xyxy": [300, 80, 420, 400], "confidence": 0.9}],
    )
    context = PipelineContext(
        job=SimpleNamespace(id="job-1"),
        selected_clips=[selected_clip],
    )
    stages = PipelineStages(db=None, storage=None)
    detector = FakeDetector()
    stages._yolo_detector = detector

    stages.opencv_tracking(context)

    assert detector.calls == 1
    assert selected_clip.tracking["tracks"][1] == {
        "frame": 1,
        "box": (390, 100, 120, 330),
        "ok": True,
    }
    assert selected_clip.tracking["tracking_recovery_count"] == 1
    assert selected_clip.tracking["total_frames"] == 3
    assert selected_clip.tracking["valid_tracking_points"] == 3
    assert selected_clip.tracking["tracking_loss_frames"] == 1


def test_tracking_retries_yolo_when_initial_detection_is_empty(tmp_path, monkeypatch):
    frame = SimpleNamespace(shape=(480, 854, 3))

    class Coordinates(list):
        def tolist(self):
            return list(self)

    class FakeCapture:
        def __init__(self):
            self.frames = iter([(True, frame), (True, frame), (True, frame), (False, None)])

        def isOpened(self):
            return True

        def read(self):
            return next(self.frames)

        def get(self, prop):
            return 3 if prop == 5 else 3

        def release(self):
            pass

    class FakeTracker:
        def init(self, _frame, _box):
            return True

        def update(self, _frame):
            return True, (390, 100, 120, 330)

    class FakeCV2:
        CAP_PROP_FPS = 5
        CAP_PROP_FRAME_COUNT = 7

        def VideoCapture(self, _path):
            return FakeCapture()

        def TrackerCSRT_create(self):
            return FakeTracker()

    detection = SimpleNamespace(
        cls=[0],
        xyxy=[Coordinates((390.0, 100.0, 510.0, 430.0))],
        conf=[0.96],
    )

    class FakeDetector:
        def __init__(self):
            self.calls = 0

        def detect(self, _frame):
            self.calls += 1
            return [SimpleNamespace(names={0: "person"}, boxes=[detection])]

    monkeypatch.setitem(sys.modules, "cv2", FakeCV2())
    clip_path = tmp_path / "late-person.mp4"
    clip_path.touch()
    selected_clip = SimpleNamespace(extracted_path=clip_path, detections=[])
    context = PipelineContext(job=SimpleNamespace(id="job-1"), selected_clips=[selected_clip])
    stages = PipelineStages(db=None, storage=None)
    detector = FakeDetector()
    stages._yolo_detector = detector

    stages.opencv_tracking(context)

    assert detector.calls == 1
    assert selected_clip.tracking["tracked_frames"] == 3
    assert selected_clip.tracking["total_frames"] == 3
    assert selected_clip.tracking["tracking_recovery_count"] == 1
    assert selected_clip.tracking["tracking_coverage_percent"] == pytest.approx(200 / 3)
    assert [track["frame"] for track in selected_clip.tracking["tracks"]] == [0, 1, 2]
    assert selected_clip.tracking["tracks"][0]["ok"] is False


def test_tracking_without_any_person_uses_full_duration_center_fallback(tmp_path, monkeypatch):
    frame = SimpleNamespace(shape=(480, 854, 3))

    class FakeCapture:
        def __init__(self):
            self.frames = iter([(True, frame), (True, frame), (True, frame), (False, None)])

        def isOpened(self):
            return True

        def read(self):
            return next(self.frames)

        def get(self, prop):
            return 3 if prop in (5, 7) else 0

        def release(self):
            pass

    class FakeCV2:
        CAP_PROP_FPS = 5
        CAP_PROP_FRAME_COUNT = 7

        def VideoCapture(self, _path):
            return FakeCapture()

        def TrackerCSRT_create(self):
            return object()

    class FakeDetector:
        def detect(self, _frame):
            return []

    monkeypatch.setitem(sys.modules, "cv2", FakeCV2())
    clip_path = tmp_path / "no-person.mp4"
    clip_path.touch()
    selected_clip = SimpleNamespace(extracted_path=clip_path, detections=[])
    stages = PipelineStages(db=None, storage=None)
    stages._yolo_detector = FakeDetector()
    stages.opencv_tracking(PipelineContext(job=SimpleNamespace(id="job-1"), selected_clips=[selected_clip]))

    context = PipelineContext(
        job=SimpleNamespace(id="job-1"),
        video=SimpleNamespace(width=854, height=480),
        media_info={"streams": [{"codec_type": "video", "width": 854, "height": 480}]},
        selected_clips=[selected_clip],
    )
    stages.reframing(context)

    assert selected_clip.tracking["tracked_frames"] == 3
    assert selected_clip.tracking["valid_tracking_points"] == 0
    assert selected_clip.tracking["tracking_coverage_percent"] == 0
    assert len(selected_clip.smoothed_camera_path) == 3
    assert all(not point["tracked"] for point in selected_clip.smoothed_camera_path)
    assert selected_clip.reframe["x"] == 292


@pytest.mark.parametrize("frame_count", [150, 900, 3600])
def test_tracking_reads_every_frame_without_duration_cutoff(frame_count, tmp_path, monkeypatch):
    class FakeCapture:
        def __init__(self):
            self.position = 0
            self.frame = SimpleNamespace(shape=(480, 854, 3))

        def isOpened(self):
            return True

        def read(self):
            if self.position >= frame_count:
                return False, None
            self.position += 1
            return True, self.frame

        def get(self, prop):
            return 30 if prop == 5 else frame_count

        def release(self):
            pass

    class FakeTracker:
        def init(self, _frame, _box):
            return True

        def update(self, _frame):
            return True, (390, 100, 120, 330)

    class FakeCV2:
        CAP_PROP_FPS = 5
        CAP_PROP_FRAME_COUNT = 7

        def VideoCapture(self, _path):
            return FakeCapture()

        def TrackerCSRT_create(self):
            return FakeTracker()

    monkeypatch.setitem(sys.modules, "cv2", FakeCV2())
    monkeypatch.setenv("FACE_TRACK_MAX_SECONDS", "5")
    monkeypatch.setenv("FACE_TRACK_TIMEOUT_SECONDS", "2")
    clip_path = tmp_path / f"selected-{frame_count}.mp4"
    clip_path.touch()
    selected_clip = SimpleNamespace(
        extracted_path=clip_path,
        detections=[{"xyxy": [390, 100, 510, 430], "confidence": 0.96}],
    )
    context = PipelineContext(job=SimpleNamespace(id="job-1"), selected_clips=[selected_clip])

    PipelineStages(db=None, storage=None).opencv_tracking(context)

    assert len(selected_clip.tracking["tracks"]) == frame_count
    assert selected_clip.tracking["total_frames"] == frame_count
    assert selected_clip.tracking["tracked_frames"] == frame_count
    assert selected_clip.tracking["valid_tracking_points"] == frame_count
    assert selected_clip.tracking["tracking_coverage_percent"] == 100
    assert selected_clip.tracking["tracking_loss_frames"] == 0


def test_ffmpeg_rendering_interpolates_subtitle_filter_values(tmp_path):
    class FakeFFmpeg:
        def __init__(self):
            self.args = None

        def run(self, args, output):
            self.args = args
            output.touch()
            (tmp_path / "editor-source-1.mp4").write_bytes(b"source")
            return output

    fake_ffmpeg = FakeFFmpeg()
    subtitle_path = tmp_path / "clip-1.srt"
    subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    context = PipelineContext(
        job=SimpleNamespace(id="job-1"),
        selected_clips=[SimpleNamespace(
            extracted_path=tmp_path / "clip-1.mp4",
            caption_path=subtitle_path,
            reframe={"x": 0, "y": 0, "width": 607, "height": 1079},
        )],
        temp_dir=tmp_path,
    )

    PipelineStages(db=None, storage=None, ffmpeg=fake_ffmpeg).ffmpeg_rendering(context)

    video_filter = fake_ffmpeg.args[fake_ffmpeg.args.index("-filter_complex") + 1]
    assert "{subtitle}" not in video_filter
    assert str(subtitle_path) in video_filter
    assert "{subtitle_style}" not in video_filter
    assert video_filter.index("crop=") < video_filter.index("scale=1080:1920") < video_filter.index("split=")
    assert video_filter.index("[caption_input]") < video_filter.index("subtitles=")
    assert "original_size=1080x1920" in video_filter
    assert video_filter.endswith(f"force_style='{SUBTITLE_FORCE_STYLE}'[captioned]")
    assert "[editor_source]" in fake_ffmpeg.args
    assert "[captioned]" in fake_ffmpeg.args
    assert "[bg]" not in video_filter
    assert "[bgblur]" not in video_filter
    assert "boxblur=" not in video_filter
    assert "overlay=" not in video_filter
    assert f"force_style='{SUBTITLE_FORCE_STYLE}'" in video_filter


def test_real_ffmpeg_pipeline_render_keeps_crop_subtitles_audio_and_editor_source(tmp_path):
    import shutil
    import subprocess

    from integrations.ffmpeg import FFmpeg

    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        pytest.skip("FFmpeg and ffprobe are required for the production render test")

    source = tmp_path / "tracked-source.mp4"
    subtitles = tmp_path / "tracked-source.srt"
    subprocess.run([
        ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", "color=c=0x185c52:s=854x480:r=12",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ], check=True, capture_output=True)
    subtitles.write_text("1\n00:00:00,300 --> 00:00:01,500\nA complete subtitle line\n", encoding="utf-8")
    selected_clip = SimpleNamespace(
        extracted_path=source,
        caption_path=subtitles,
        reframe={
            "x": 0,
            "y": 0,
            "width": 270,
            "height": 480,
            "x_expression": _crop_axis_expression([(0, 0), (24, 292), (47, 584)]),
            "y_expression": "0",
        },
    )
    context = PipelineContext(
        job=SimpleNamespace(id="real-pipeline-render"),
        selected_clips=[selected_clip],
        temp_dir=tmp_path,
    )

    stages = PipelineStages(db=None, storage=None, ffmpeg=FFmpeg())
    stages.ffmpeg_rendering(context)
    stages.output_validation(context)

    for output in (selected_clip.rendered_path, selected_clip.editor_source_path):
        details = FFmpeg().probe(output)
        video = next(stream for stream in details["streams"] if stream["codec_type"] == "video")
        assert (video["width"], video["height"]) == (1080, 1920)
        assert any(stream["codec_type"] == "audio" for stream in details["streams"])
        assert float(details["format"]["duration"]) == pytest.approx(4, abs=0.2)
        subprocess.run([ffmpeg_bin, "-v", "error", "-i", str(output), "-f", "null", "-"], check=True, capture_output=True)


def test_subtitle_chunks_are_single_line_sequential_and_preserve_words():
    original_text = "This is the best property investment opportunity in Lagos"
    chunks = _build_subtitle_chunks(
        [{"start": 1.0, "end": 5.0, "text": original_text}],
        max_chars=32,
        max_width_em=100,
    )

    assert [chunk["text"] for chunk in chunks] == [
        "This is the best property",
        "investment opportunity in Lagos",
    ]
    assert " ".join(chunk["text"] for chunk in chunks) == original_text
    assert all("\n" not in chunk["text"] and "\r" not in chunk["text"] for chunk in chunks)
    assert all(previous["end"] <= following["start"] for previous, following in zip(chunks, chunks[1:]))


def test_caption_chunking_prefers_deepgram_word_timestamps():
    context = SimpleNamespace(_deepgram_raw={
        "results": {"channels": [{"alternatives": [{"words": [
            {"start": 0.1, "end": 0.3, "word": "hello", "punctuated_word": "Hello"},
            {"start": 0.31, "end": 0.6, "word": "world", "punctuated_word": "world!"},
        ]}]}]},
    })

    assert _deepgram_word_segments(context) == [
        {"start": 0.1, "end": 0.3, "text": "Hello"},
        {"start": 0.31, "end": 0.6, "text": "world!"},
    ]


def test_caption_stage_writes_sequential_word_timed_srt_cues(tmp_path, caplog):
    caplog.set_level("INFO", logger="pipeline.stages")
    selected_clip = SimpleNamespace(
        candidate=SimpleNamespace(candidate=SimpleNamespace(start=1.0, end=5.0)),
    )
    context = PipelineContext(
        job=SimpleNamespace(id="job-1"),
        selected_clips=[selected_clip],
        transcript_segments=[{"start": 1.0, "end": 5.0, "text": "fallback phrase should not be used"}],
        temp_dir=tmp_path,
    )
    context._deepgram_raw = {
        "results": {"channels": [{"alternatives": [{"words": [
            {"start": 1.0, "end": 1.4, "word": "this", "punctuated_word": "This"},
            {"start": 1.4, "end": 1.6, "word": "is", "punctuated_word": "is"},
            {"start": 1.6, "end": 2.0, "word": "clear", "punctuated_word": "clear."},
        ]}]}]},
    }

    PipelineStages(db=None, storage=None).captions(context)

    subtitle_text = selected_clip.caption_path.read_text(encoding="utf-8")
    assert "This is clear." in subtitle_text
    assert "fallback phrase" not in subtitle_text
    assert "\nThis is clear.\n" in subtitle_text
    assert "00:00:00,000 --> 00:00:01,000" in subtitle_text
    assert f"path={selected_clip.caption_path}" in caplog.text
    assert "entries=1" in caplog.text
    assert "within_clip=True" in caplog.text


def test_subtitle_style_is_bottom_center_transparent_and_outlined():
    assert "Alignment=2" in SUBTITLE_FORCE_STYLE
    assert "MarginV=55" in SUBTITLE_FORCE_STYLE
    assert "BorderStyle=1" in SUBTITLE_FORCE_STYLE
    assert "BackColour=&H00000000" in SUBTITLE_FORCE_STYLE
    assert "PrimaryColour=&H00FFFFFF" in SUBTITLE_FORCE_STYLE
    assert "OutlineColour=&H00000000" in SUBTITLE_FORCE_STYLE
    assert "Fontsize=7" in SUBTITLE_FORCE_STYLE
    assert "Bold=1" in SUBTITLE_FORCE_STYLE
    assert "Outline=1" in SUBTITLE_FORCE_STYLE
    assert "Shadow=0" in SUBTITLE_FORCE_STYLE
    assert "WrapStyle=2" in SUBTITLE_FORCE_STYLE


def test_output_validation_still_requires_1080_by_1920():
    class ProbeFFmpeg:
        def __init__(self, width, height):
            self.width = width
            self.height = height

        def probe(self, _path):
            return {"streams": [
                {"codec_type": "video", "width": self.width, "height": self.height},
                {"codec_type": "audio"},
            ]}

    context = PipelineContext(
        job=SimpleNamespace(id="job-1"),
        selected_clips=[SimpleNamespace(rendered_path="rendered.mp4")],
    )

    PipelineStages(db=None, storage=None, ffmpeg=ProbeFFmpeg(1080, 1920)).output_validation(context)
    with pytest.raises(ValueError, match="1080x1920"):
        PipelineStages(db=None, storage=None, ffmpeg=ProbeFFmpeg(1080, 1919)).output_validation(context)


def test_final_video_filter_handles_trailing_graph_separators():
    final_filter = "scale=1080:1920"

    assert _append_final_video_filter("crop=10:10:0:0,", final_filter) == "crop=10:10:0:0,scale=1080:1920"
    assert _append_final_video_filter("crop=10:10:0:0; \n", final_filter) == "crop=10:10:0:0,scale=1080:1920"


def test_sentence_boundary_extension_avoids_cliffhangers():
    candidate = ClipCandidate(start=12, end=15, reason="cuts in the middle", scores=scores())
    segments = [
        {"start": 10, "end": 18, "text": "This sentence is not finished yet."},
        {"start": 18, "end": 24, "text": "Another sentence starts here."},
    ]
    valid = validate_candidates([candidate], source_duration=30, minimum_duration=8, transcript_segments=segments)
    assert len(valid) == 1
    assert valid[0].candidate.end >= 18.0
    assert valid[0].candidate.end <= 24.0
