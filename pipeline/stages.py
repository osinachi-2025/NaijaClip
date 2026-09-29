from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import CaptionStyle, Clip, ClipAspectRatio, ClipExport, ClipStatus, ExportStatus, Transcript, TranscriptSegment, TranscriptStatus
from core.config import (
    CAMERA_ACCELERATION,
    CAMERA_DEAD_ZONE,
    CAMERA_LOOK_AHEAD_MAX,
    CAMERA_LOOK_AHEAD_SECONDS,
    CAMERA_MAX_STEP,
    CAMERA_MAX_VELOCITY,
    CAMERA_PATH_MAX_ERROR_PX,
    CAMERA_RETURN_SPEED,
    CAMERA_SMOOTHING_ALPHA,
    MIN_TRACKING_COVERAGE,
    TRACK_LOST_HOLD_FRAMES,
)
from integrations.deepgram import DeepgramClient
from integrations.ffmpeg import FFmpeg
from integrations.groq.client import GroqClient
from integrations.storage import LocalStorage, R2Storage
from integrations.yolo import YOLODetector
from services.subtitles import (
    SUBTITLE_FORCE_STYLE,
    SUBTITLE_MAX_CHARS,
    SUBTITLE_MAX_WIDTH_EM,
    build_subtitle_chunks as _build_subtitle_chunks,
    subtitle_width_em as _subtitle_width_em,
)
from .context import PipelineContext
from .scoring import score_candidate
from .validation import clip_duration_range, remove_overlaps, validate_candidates


logger = logging.getLogger(__name__)
TRACK_REDETECTION_INTERVAL_SECONDS = 1.0
def _deepgram_word_segments(context: PipelineContext) -> list[dict[str, Any]]:
    raw = getattr(context, "_deepgram_raw", {})
    channels = raw.get("results", {}).get("channels", [])
    alternatives = channels[0].get("alternatives", []) if channels else []
    words = alternatives[0].get("words", []) if alternatives else []
    return [
        {
            "start": word.get("start"),
            "end": word.get("end"),
            "text": word.get("punctuated_word") or word.get("word") or "",
        }
        for word in words
        if word.get("start") is not None and word.get("end") is not None
    ]


def _resolve_tracker_factory(cv2_module: Any) -> Any:
    tracker_names = (
        "TrackerCSRT_create",
        "TrackerKCF_create",
        "TrackerMedianFlow_create",
        "TrackerMOSSE_create",
        "TrackerMIL_create",
        "TrackerGOTURN_create",
    )

    def _candidate(module: Any):
        for name in tracker_names:
            candidate = getattr(module, name, None)
            if callable(candidate):
                try:
                    candidate()
                except Exception:
                    logger.warning("OpenCV tracker factory %s is unavailable", name, exc_info=True)
                    continue
                return candidate
        return None

    factory = _candidate(cv2_module)
    if factory is not None:
        return factory

    legacy = getattr(cv2_module, "legacy", None)
    if legacy is not None:
        return _candidate(legacy)
    return None


def _person_bboxes(predictions: Any, clip_label: str) -> list[dict[str, Any]]:
    boxes = []
    for result in predictions or []:
        names = getattr(result, "names", {})
        class_names = names.items() if isinstance(names, dict) else enumerate(names or [])
        target_classes = {
            int(class_id)
            for class_id, class_name in class_names
            if str(class_name).lower() in {"person", "face"}
        }
        for detected_box in getattr(result, "boxes", None) or []:
            class_values = getattr(detected_box, "cls", None)
            class_id = int(class_values[0]) if class_values is not None else -1
            if class_id not in target_classes:
                continue
            coordinates = [float(value) for value in detected_box.xyxy[0].tolist()]
            if len(coordinates) != 4 or not all(math.isfinite(value) for value in coordinates):
                logger.warning("[PIPELINE] Ignoring invalid YOLO bbox for %s: %s", clip_label, coordinates)
                continue
            x1, y1, x2, y2 = coordinates
            if x2 <= x1 or y2 <= y1:
                logger.warning("[PIPELINE] Ignoring empty YOLO bbox for %s: %s", clip_label, coordinates)
                continue
            boxes.append({"xyxy": coordinates, "confidence": float(detected_box.conf[0])})
    return sorted(boxes, key=lambda box: box["confidence"], reverse=True)


def _value(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _response_dict(response: Any) -> dict:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="python")
    if hasattr(response, "dict"):
        return response.dict()
    if hasattr(response, "to_dict"):
        return response.to_dict()
    if hasattr(response, "model_dump_json"):
        return json.loads(response.model_dump_json())
    if hasattr(response, "to_json"):
        return json.loads(response.to_json())
    return {}


def _append_final_video_filter(filter_graph: str, video_filter: str) -> str:
    filter_graph = filter_graph.rstrip(" \t\r\n,;")
    return f"{filter_graph},{video_filter}" if filter_graph else video_filter


def _crop_offset(center: float, crop_extent: int, frame_extent: int) -> int:
    return max(0, min(frame_extent - crop_extent, int(center - crop_extent / 2)))


def _simplify_camera_points(points: list[tuple[int, int]], max_error: float = CAMERA_PATH_MAX_ERROR_PX) -> list[tuple[int, int]]:
    ordered_points = list(dict(sorted(points)).items())
    if len({value for _, value in ordered_points}) == 1:
        return [ordered_points[0]] if ordered_points else []
    if len(ordered_points) <= 2:
        return ordered_points

    keep = {0, len(ordered_points) - 1}
    pending = [(0, len(ordered_points) - 1)]
    while pending:
        left_index, right_index = pending.pop()
        left_frame, left_value = ordered_points[left_index]
        right_frame, right_value = ordered_points[right_index]
        frame_span = right_frame - left_frame
        if frame_span <= 1:
            continue
        max_deviation = max_error
        split_index = None
        for index in range(left_index + 1, right_index):
            frame, value = ordered_points[index]
            fraction = (frame - left_frame) / frame_span
            interpolated = left_value + (right_value - left_value) * fraction
            deviation = abs(value - interpolated)
            if deviation > max_deviation:
                max_deviation = deviation
                split_index = index
        if split_index is not None:
            keep.add(split_index)
            pending.extend(((left_index, split_index), (split_index, right_index)))
    return [ordered_points[index] for index in sorted(keep)]


def _crop_axis_expression(points: list[tuple[int, int]]) -> str:
    ordered_points = _simplify_camera_points(points)
    if not ordered_points:
        return "0"

    expression = str(ordered_points[-1][1])
    for (start_frame, start_offset), (end_frame, end_offset) in reversed(list(zip(ordered_points, ordered_points[1:]))):
        frame_delta = end_frame - start_frame
        if frame_delta <= 0:
            continue
        interpolation = f"({start_offset}+({end_offset}-{start_offset})*(n-{start_frame})/{frame_delta})"
        expression = f"if(lt(n\\,{end_frame})\\,{interpolation}\\,{expression})"
    return expression


def _smooth_camera_trajectory(
    tracking_entries: list[dict[str, Any]],
    *,
    crop_width: int,
    crop_height: int,
    frame_width: int,
    frame_height: int,
    center_crop: tuple[int, int],
    fps: float,
) -> list[dict[str, Any]]:
    dead_zone_x = CAMERA_DEAD_ZONE * crop_width / 1080
    dead_zone_y = CAMERA_DEAD_ZONE * crop_height / 1920
    scale_x = crop_width / 1080
    scale_y = crop_height / 1920
    max_velocity_x = min(CAMERA_MAX_VELOCITY, CAMERA_MAX_STEP) * scale_x
    max_velocity_y = min(CAMERA_MAX_VELOCITY, CAMERA_MAX_STEP) * scale_y
    acceleration_x = CAMERA_ACCELERATION * scale_x
    acceleration_y = CAMERA_ACCELERATION * scale_y
    look_ahead_max_x = CAMERA_LOOK_AHEAD_MAX * scale_x
    look_ahead_max_y = CAMERA_LOOK_AHEAD_MAX * scale_y
    return_velocity_x = min(CAMERA_RETURN_SPEED, CAMERA_MAX_VELOCITY) * scale_x
    return_velocity_y = min(CAMERA_RETURN_SPEED, CAMERA_MAX_VELOCITY) * scale_y
    camera_x = None
    camera_y = None
    velocity_x = 0.0
    velocity_y = 0.0
    target_x_filtered = None
    target_y_filtered = None
    previous_target_x = None
    previous_target_y = None
    last_valid_frame = None
    smoothed = []

    def advance_axis(current: float, target: float, velocity: float, dead_zone: float, max_velocity: float, acceleration: float) -> tuple[float, float]:
        delta = target - current
        if abs(delta) <= dead_zone:
            desired_velocity = 0.0
        else:
            remaining = delta - math.copysign(dead_zone, delta)
            desired_velocity = max(-max_velocity, min(max_velocity, remaining))
        velocity_delta = max(-acceleration, min(acceleration, desired_velocity - velocity))
        velocity += velocity_delta
        if desired_velocity == 0.0 and abs(velocity) < acceleration:
            velocity = 0.0
        next_position = current + velocity
        if (target - current) * (target - next_position) < 0:
            return target, 0.0
        return next_position, velocity

    def bounded_look_ahead(velocity: float, frames: float, maximum: float) -> float:
        return max(-maximum, min(maximum, velocity * frames))

    for fallback_frame, track in enumerate(tracking_entries):
        frame_number = int(track.get("frame", fallback_frame))
        bbox = track.get("box")
        is_valid = bool(track.get("ok") and bbox and len(bbox) == 4)
        if is_valid:
            box_x, box_y, box_width, box_height = (float(value) for value in bbox)
            target_x = _crop_offset(box_x + box_width / 2, crop_width, frame_width)
            target_y = _crop_offset(box_y + box_height / 2, crop_height, frame_height)
            if camera_x is None or camera_y is None:
                camera_x, camera_y = float(target_x), float(target_y)
                target_x_filtered, target_y_filtered = float(target_x), float(target_y)
                previous_target_x, previous_target_y = float(target_x), float(target_y)
            else:
                target_x_filtered = CAMERA_SMOOTHING_ALPHA * target_x + (1 - CAMERA_SMOOTHING_ALPHA) * target_x_filtered
                target_y_filtered = CAMERA_SMOOTHING_ALPHA * target_y + (1 - CAMERA_SMOOTHING_ALPHA) * target_y_filtered
                target_velocity_x = target_x_filtered - previous_target_x
                target_velocity_y = target_y_filtered - previous_target_y
                look_ahead_frames = CAMERA_LOOK_AHEAD_SECONDS * fps
                predicted_x = target_x_filtered + bounded_look_ahead(target_velocity_x, look_ahead_frames, look_ahead_max_x)
                predicted_y = target_y_filtered + bounded_look_ahead(target_velocity_y, look_ahead_frames, look_ahead_max_y)
                camera_x, velocity_x = advance_axis(
                    camera_x, predicted_x, velocity_x, dead_zone_x,
                    max_velocity_x, acceleration_x,
                )
                camera_y, velocity_y = advance_axis(
                    camera_y, predicted_y, velocity_y, dead_zone_y,
                    max_velocity_y, acceleration_y,
                )
                if fps > 0 and frame_number % max(1, int(fps * 10)) == 0:
                    logger.debug(
                        "[CAMERA] frame=%d raw_x=%.1f smoothed_x=%.1f velocity_x=%.2f target_x=%.1f camera_x=%.1f",
                        frame_number, target_x, target_x_filtered, velocity_x,
                        predicted_x, camera_x,
                    )
                previous_target_x = target_x_filtered
                previous_target_y = target_y_filtered
            last_valid_frame = frame_number
        elif camera_x is None or camera_y is None:
            camera_x, camera_y = map(float, center_crop)
        else:
            if last_valid_frame is not None and frame_number - last_valid_frame > TRACK_LOST_HOLD_FRAMES:
                camera_x, velocity_x = advance_axis(
                    camera_x, center_crop[0], velocity_x, 0.0,
                    return_velocity_x, acceleration_x,
                )
                camera_y, velocity_y = advance_axis(
                    camera_y, center_crop[1], velocity_y, 0.0,
                    return_velocity_y, acceleration_y,
                )
            else:
                camera_x, velocity_x = advance_axis(camera_x, camera_x, velocity_x, 0.0, max_velocity_x, acceleration_x)
                camera_y, velocity_y = advance_axis(camera_y, camera_y, velocity_y, 0.0, max_velocity_y, acceleration_y)

        camera_x = max(0.0, min(frame_width - crop_width, camera_x))
        camera_y = max(0.0, min(frame_height - crop_height, camera_y))
        smoothed.append({
            "frame": frame_number,
            "x": int(round(camera_x)),
            "y": int(round(camera_y)),
            "bbox": bbox if is_valid else None,
            "raw_center": (
                (box_x + box_width / 2, box_y + box_height / 2)
                if is_valid else None
            ),
            "tracked": is_valid,
        })

    logger.info(
        "[CAMERA] alpha=%.3f dead_zone=%.1fpx max_velocity=%.2fpx/frame acceleration=%.2fpx/frame2 look_ahead=%.3fs look_ahead_cap=%.1fpx lost_hold=%d frames return_speed=%.1fpx/frame",
        CAMERA_SMOOTHING_ALPHA, CAMERA_DEAD_ZONE, CAMERA_MAX_VELOCITY,
        CAMERA_ACCELERATION, CAMERA_LOOK_AHEAD_SECONDS, CAMERA_LOOK_AHEAD_MAX,
        TRACK_LOST_HOLD_FRAMES, CAMERA_RETURN_SPEED,
    )
    return smoothed


class PipelineStages:
    def __init__(self, db, storage, ffmpeg: FFmpeg | None = None):
        self.db = db
        self.storage = storage
        self.ffmpeg = ffmpeg or FFmpeg()
        self._yolo_detector = None

    def handlers(self) -> dict[str, Any]:
        return {
            "input_validation": self.input_validation,
            "audio_extraction": self.audio_extraction,
            "deepgram_transcription": self.deepgram_transcription,
            "transcript_validation": self.transcript_validation,
            "groq_selection": self.groq_selection,
            "candidate_scoring": self.candidate_scoring,
            "clip_extraction": self.clip_extraction,
            "yolo_detection": self.yolo_detection,
            "opencv_tracking": self.opencv_tracking,
            "reframing": self.reframing,
            "captions": self.captions,
            "audio_processing": self.audio_processing,
            "ffmpeg_rendering": self.ffmpeg_rendering,
            "output_validation": self.output_validation,
            "thumbnail_generation": self.thumbnail_generation,
            "export": self.export,
            "cleanup": self.cleanup,
        }

    def input_validation(self, context: PipelineContext) -> None:
        if not context.source_path or not context.source_path.is_file():
            raise FileNotFoundError("source video is unavailable")
        if context.source_path.stat().st_size == 0:
            raise ValueError("source video is empty")
        context.temp_dir = Path("/tmp") / "naijaclip" / context.job.id
        context.temp_dir.mkdir(parents=True, exist_ok=True)

    def audio_extraction(self, context: PipelineContext) -> None:
        context.audio_path = context.temp_dir / "audio.wav"
        self.ffmpeg.extract_audio(context.source_path, context.audio_path)

    def deepgram_transcription(self, context: PipelineContext) -> None:
        response = DeepgramClient().transcribe(str(context.audio_path))
        raw = _response_dict(response)
        result = raw.get("results", {})
        channels = result.get("channels", [])
        alternative = channels[0].get("alternatives", [{}])[0] if channels else {}
        context.transcript = alternative.get("transcript", "").strip()
        utterances = result.get("utterances") or []
        context.transcript_segments = [
            {"start": float(item.get("start", 0)), "end": float(item.get("end", 0)), "text": item.get("transcript", "").strip(), "confidence": item.get("confidence")}
            for item in utterances if item.get("transcript")
        ]
        if not context.transcript_segments:
            paragraphs = (alternative.get("paragraphs") or {}).get("paragraphs", [])
            for paragraph in paragraphs:
                for sentence in paragraph.get("sentences", []):
                    text = sentence.get("text", "").strip()
                    if text:
                        context.transcript_segments.append({"start": float(sentence.get("start", 0)), "end": float(sentence.get("end", 0)), "text": text, "confidence": None})
        if not context.transcript_segments and alternative.get("words"):
            words = alternative["words"]
            context.transcript_segments = [{"start": float(word.get("start", 0)), "end": float(word.get("end", 0)), "text": word.get("punctuated_word", word.get("word", "")), "confidence": word.get("confidence")} for word in words]
        context._deepgram_raw = raw

    def transcript_validation(self, context: PipelineContext) -> None:
        if not context.transcript or not context.transcript_segments:
            raise ValueError("Deepgram returned no timestamped transcript")
        existing = context.video.transcript
        if existing:
            self.db.delete(existing)
            self.db.flush()
        transcript = Transcript(video_id=context.video.id, provider="deepgram", status=TranscriptStatus.COMPLETED, full_text=context.transcript)
        transcript.segments = [TranscriptSegment(start_seconds=item["start"], end_seconds=item["end"], text=item["text"], confidence=item.get("confidence")) for item in context.transcript_segments if item["end"] >= item["start"]]
        self.db.add(transcript)
        self.db.flush()

    def groq_selection(self, context: PipelineContext) -> None:
        duration = float(context.media_info.get("format", {}).get("duration", 0))
        default_minimum, default_maximum = clip_duration_range(duration)
        configured_minimum = float(os.getenv("MIN_CLIP_DURATION", str(default_minimum)))
        configured_maximum = float(os.getenv("MAX_CLIP_DURATION", str(default_maximum)))
        minimum_duration = max(default_minimum, configured_minimum)
        maximum_duration = min(default_maximum, configured_maximum)
        if duration <= 0:
            raise ValueError("source video duration is invalid")
        result = GroqClient().evaluate_transcript(context.transcript, context.transcript_segments, duration)
        boundary_segments = _deepgram_word_segments(context) or context.transcript_segments
        context.groq_candidates = validate_candidates(result.candidates, duration, minimum_duration, maximum_duration, boundary_segments)
        context.groq_candidates = remove_overlaps(context.groq_candidates)
        if not context.groq_candidates:
            raise ValueError(f"Groq returned no valid clip candidates for a {duration:.2f}s source; target range was {minimum_duration:.2f}s to {maximum_duration:.2f}s")

    def candidate_scoring(self, context: PipelineContext) -> None:
        scored = []
        for item in context.groq_candidates:
            duration = item.duration
            overlap = [segment for segment in context.transcript_segments if segment["start"] < item.candidate.end and segment["end"] > item.candidate.start]
            speech_seconds = sum(max(0.0, min(segment["end"], item.candidate.end) - max(segment["start"], item.candidate.start)) for segment in overlap)
            confidence_values = [segment["confidence"] for segment in overlap if segment.get("confidence") is not None]
            scored.append(score_candidate(item, speech_density=min(10.0, speech_seconds / duration * 10.0), boundary_quality=min(10.0, len(overlap) + 5.0), confidence=(sum(confidence_values) / len(confidence_values) * 10.0 if confidence_values else None)))
        ranked = sorted(scored, key=lambda item: item.final_score, reverse=True)
        threshold = float(os.getenv("MIN_CLIP_SCORE", "5"))
        context.scored_candidates = [item for item in ranked if item.final_score >= threshold]
        limit = int(os.getenv("MAX_CLIPS_PER_VIDEO", "5"))
        context.selected_clips = context.scored_candidates[:limit]
        if not context.selected_clips:
            raise ValueError("no candidates met the minimum score")
        for rank, item in enumerate(context.selected_clips, 1):
            candidate = item.candidate.candidate
            clip = Clip(video_id=context.video.id, title=candidate.reason[:500], description=candidate.reason, start_seconds=candidate.start, end_seconds=candidate.end, status=ClipStatus.SELECTED, ai_score=item.final_score, hook_score=candidate.scores.hook, standalone_score=candidate.scores.context_independence, engagement_score=candidate.scores.engagement_potential, rank=rank, aspect_ratio=ClipAspectRatio.PORTRAIT, caption_style=CaptionStyle.BASIC, caption_text=context.transcript)
            self.db.add(clip)
            item.clip = clip
            logger.info("[PIPELINE] LLM selected clip-%d: %s", rank, clip.title)
        self.db.flush()

    def clip_extraction(self, context: PipelineContext) -> None:
        for index, item in enumerate(context.selected_clips, 1):
            logger.info("[PIPELINE] Extracting LLM-selected clip-%d", index)
            output = context.temp_dir / f"clip-{index}.mp4"
            self.ffmpeg.extract_clip(context.source_path, item.candidate.candidate.start, item.candidate.candidate.end, output)
            item.extracted_path = output
            context.extracted_clips.append(output)

    def yolo_detection(self, context: PipelineContext) -> None:
        try:
            import cv2
        except ImportError as error:
            logger.exception("[PIPELINE] OpenCV is unavailable for YOLO detection")
            raise RuntimeError("OpenCV is required for selected-clip person detection") from error

        model_path = os.getenv("YOLO_MODEL", "yolo26n.pt")
        try:
            detector = YOLODetector(model_path, confidence=float(os.getenv("YOLO_CONFIDENCE", "0.30")))
        except Exception as error:
            logger.exception("[PIPELINE] YOLO model initialization failed: %s", model_path)
            raise RuntimeError(f"Unable to initialize YOLO model: {model_path}") from error
        self._yolo_detector = detector

        for clip_index, item in enumerate(context.selected_clips, 1):
            clip_label = f"clip-{clip_index}"
            logger.info("[PIPELINE] Running YOLO person detection: %s", clip_label)
            if item.extracted_path is None or not item.extracted_path.is_file():
                raise FileNotFoundError(f"extracted video is unavailable for {clip_label}")
            capture = cv2.VideoCapture(str(item.extracted_path))
            boxes = []
            try:
                if not capture.isOpened():
                    raise RuntimeError(f"OpenCV could not open selected video {clip_label}")
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f"OpenCV could not read the first frame of {clip_label}")
                try:
                    predictions = detector.detect(frame)
                except Exception as error:
                    logger.exception("[PIPELINE] YOLO inference failed for %s", clip_label)
                    raise RuntimeError(f"YOLO inference failed for {clip_label}") from error

                boxes = _person_bboxes(predictions, clip_label)
            finally:
                capture.release()

            item.detections = boxes
            logger.info("[PIPELINE] YOLO detected %d person(s): %s", len(item.detections), clip_label)
            if item.detections:
                initial_detection = item.detections[0]
                logger.info("[PIPELINE] Initial speaker/person bbox for %s: %s", clip_label, initial_detection["xyxy"])
                context.detections.append(item.detections)
            else:
                logger.warning("[PIPELINE] No person detected in %s; tracking is unavailable and reframing will explicitly use center crop", clip_label)

    def opencv_tracking(self, context: PipelineContext) -> None:
        try:
            import cv2
        except ImportError as error:
            logger.exception("[PIPELINE] OpenCV is unavailable for tracking")
            raise RuntimeError("OpenCV is required to track detected speakers") from error

        for clip_index, item in enumerate(context.selected_clips, 1):
            clip_label = f"clip-{clip_index}"
            detections = getattr(item, "detections", [])
            initial_detection = detections[0] if detections else None
            if not detections:
                logger.warning("[TRACKING] No initial person for %s; decoding the full clip and retrying YOLO at the configured interval", clip_label)
            logger.info("[PIPELINE] Starting OpenCV tracking: %s", clip_label)
            capture = cv2.VideoCapture(str(item.extracted_path))
            try:
                if not capture.isOpened():
                    raise RuntimeError(f"OpenCV could not open selected video {clip_label} for tracking")
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f"OpenCV could not read the initial tracking frame for {clip_label}")
                height, width = frame.shape[:2]
                create_tracker = _resolve_tracker_factory(cv2)
                if initial_detection and create_tracker is None:
                    raise RuntimeError("No supported OpenCV object tracker is available")
                frame_number = 1
                fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
                if not math.isfinite(fps) or fps <= 0:
                    fps = 30.0
                raw_frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                frame_count = int(raw_frame_count) if math.isfinite(raw_frame_count) and raw_frame_count > 0 else 0
                box = None
                tracker = None
                if initial_detection:
                    x1, y1, x2, y2 = initial_detection["xyxy"]
                    left = max(0, min(width, int(x1)))
                    top = max(0, min(height, int(y1)))
                    right = max(0, min(width, int(x2)))
                    bottom = max(0, min(height, int(y2)))
                    box = (left, top, right - left, bottom - top)
                    if box[2] <= 0 or box[3] <= 0:
                        raise ValueError(f"Invalid YOLO bbox for {clip_label}: {initial_detection['xyxy']}")
                    try:
                        tracker = create_tracker()
                        initialized = tracker.init(frame, box)
                        if initialized is False:
                            raise RuntimeError("OpenCV tracker initialization returned false")
                    except Exception as error:
                        logger.exception("[PIPELINE] OpenCV tracker initialization failed for %s", clip_label)
                        raise RuntimeError(f"OpenCV tracker initialization failed for {clip_label}") from error

                tracks = [{"frame": 0, "box": box, "ok": bool(box)}]
                redetection_interval_frames = max(1, int(fps * TRACK_REDETECTION_INTERVAL_SECONDS))
                update_errors = 0
                first_update_error = None
                tracking_loss_frames = 0
                successful_redetections = 0
                last_redetection_frame = -redetection_interval_frames
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    if tracker is None:
                        tracked, tracked_box = False, None
                    else:
                        try:
                            tracked, tracked_box = tracker.update(frame)
                        except Exception as error:
                            update_errors += 1
                            if first_update_error is None:
                                first_update_error = str(error)
                            tracked, tracked_box = False, None
                    normalized_box = tuple(int(value) for value in tracked_box) if tracked and tracked_box else None
                    valid_box = normalized_box is not None and len(normalized_box) == 4 and normalized_box[2] > 0 and normalized_box[3] > 0
                    if tracked and not valid_box:
                        logger.warning("[PIPELINE] OpenCV returned an invalid bbox for %s frame=%d: %s", clip_label, frame_number, tracked_box)
                    if not valid_box:
                        tracking_loss_frames += 1
                        if self._yolo_detector is not None and frame_number - last_redetection_frame >= redetection_interval_frames:
                            last_redetection_frame = frame_number
                            try:
                                redetections = _person_bboxes(self._yolo_detector.detect(frame), clip_label)
                            except Exception:
                                logger.exception("[PIPELINE] YOLO re-detection failed after tracking loss: %s frame=%d", clip_label, frame_number)
                                redetections = []
                            if redetections:
                                redetected = redetections[0]
                                rx1, ry1, rx2, ry2 = redetected["xyxy"]
                                rleft = max(0, min(width, int(rx1)))
                                rtop = max(0, min(height, int(ry1)))
                                rright = max(0, min(width, int(rx2)))
                                rbottom = max(0, min(height, int(ry2)))
                                recovered_box = (rleft, rtop, rright - rleft, rbottom - rtop)
                                if recovered_box[2] > 0 and recovered_box[3] > 0:
                                    try:
                                        if create_tracker is not None:
                                            recovered_tracker = create_tracker()
                                            initialized = recovered_tracker.init(frame, recovered_box)
                                            if initialized is False:
                                                raise RuntimeError("OpenCV tracker initialization returned false")
                                            tracker = recovered_tracker
                                        normalized_box = recovered_box
                                        tracked = True
                                        valid_box = True
                                        successful_redetections += 1
                                        logger.info("[PIPELINE] YOLO reacquired person for %s frame=%d bbox=%s; OpenCV tracking %s", clip_label, frame_number, redetected["xyxy"], "resumed" if tracker else "will retry at the next interval")
                                    except Exception:
                                        logger.exception("[PIPELINE] Could not restart OpenCV tracker after YOLO reacquisition: %s frame=%d", clip_label, frame_number)
                                else:
                                    logger.warning("[PIPELINE] YOLO reacquisition returned an invalid bbox for %s frame=%d: %s", clip_label, frame_number, redetected["xyxy"])
                            else:
                                logger.debug("[PIPELINE] YOLO did not reacquire a person for %s frame=%d", clip_label, frame_number)
                    is_valid = bool(tracked and valid_box)
                    tracks.append({"frame": frame_number, "box": normalized_box if is_valid else None, "ok": is_valid})
                    frame_number += 1

                tracked_frames = frame_number
                total_frames = max(frame_count, tracked_frames)
                valid_tracking_points = sum(track["ok"] for track in tracks)
                coverage_percent = valid_tracking_points / total_frames * 100 if total_frames else 0.0
                clip_duration = total_frames / fps
                item.tracking = {
                    "box": box,
                    "tracks": tracks,
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "frame_count": frame_count,
                    "target_frames": total_frames,
                    "clip_duration": clip_duration,
                    "total_frames": total_frames,
                    "tracked_frames": tracked_frames,
                    "valid_tracking_points": valid_tracking_points,
                    "tracking_coverage_percent": coverage_percent,
                    "tracking_recovery_count": successful_redetections,
                    "tracking_loss_frames": tracking_loss_frames,
                    "confidence": initial_detection["confidence"] if initial_detection else None,
                }
                context.tracking_data.append(item.tracking)
                successful_updates = sum(track["ok"] for track in tracks[1:])
                if tracked_frames < total_frames:
                    logger.warning("[TRACKING] clip=%s decoder supplied %d of %d frames; remaining camera path will use controlled fallback", clip_label, tracked_frames, total_frames)
                if update_errors:
                    logger.warning("[PIPELINE] OpenCV tracking raised %d update error(s) for %s; first error: %s", update_errors, clip_label, first_update_error)
                if tracking_loss_frames:
                    logger.info("[PIPELINE] Tracking lost for %s on %d frame(s); YOLO reacquired %d time(s)", clip_label, tracking_loss_frames, successful_redetections)
                if coverage_percent < MIN_TRACKING_COVERAGE * 100:
                    logger.warning("[TRACKING] clip=%s coverage %.2f%% is below configured minimum %.2f%%; YOLO recovery was attempted and missing frames will use held/fallback camera positions", clip_label, coverage_percent, MIN_TRACKING_COVERAGE * 100)
                if not successful_updates:
                    logger.warning("[PIPELINE] No successful tracking updates for %s; reframing will use its initial detected position", clip_label)
                logger.info(
                    "[TRACKING] clip=%s clip_duration=%.2fs fps=%.3f total_frames=%d tracked_frames=%d valid_tracking_points=%d tracking_coverage_percent=%.2f tracking_recovery_count=%d tracking_loss_frames=%d",
                    clip_label, clip_duration, fps, total_frames, tracked_frames,
                    valid_tracking_points, coverage_percent, successful_redetections,
                    tracking_loss_frames,
                )
            finally:
                capture.release()

    def reframing(self, context: PipelineContext) -> None:
        video_stream = next((stream for stream in context.media_info.get("streams", []) if stream.get("codec_type") == "video"), {})
        probed_width = int(video_stream.get("width") or context.video.width or 0)
        probed_height = int(video_stream.get("height") or context.video.height or 0)
        if probed_width <= 0 or probed_height <= 0:
            raise ValueError("source video dimensions are unavailable for reframing")

        for clip_index, item in enumerate(context.selected_clips, 1):
            logger.info("[PIPELINE] Applying dynamic 9:16 reframing: clip-%d", clip_index)
            tracking = getattr(item, "tracking", None)
            width = int((tracking or {}).get("width") or probed_width)
            height = int((tracking or {}).get("height") or probed_height)
            if width <= 0 or height <= 0:
                raise ValueError(f"invalid source dimensions for clip-{clip_index}: {width}x{height}")
            crop_width = min(width, int(height * 9 / 16))
            crop_height = min(height, max(1, int(crop_width * 16 / 9)))
            centered_crop = {
                "x": max(0, (width - crop_width) // 2),
                "y": max(0, (height - crop_height) // 2),
                "width": crop_width,
                "height": crop_height,
            }
            tracking_entries = sorted((tracking or {}).get("tracks", []), key=lambda track: int(track.get("frame", 0)))
            target_frames = int((tracking or {}).get("target_frames") or 0)
            if target_frames <= 0 and tracking_entries:
                target_frames = int(tracking_entries[-1].get("frame", 0)) + 1
            if tracking_entries and target_frames:
                last_tracking_frame = int(tracking_entries[-1].get("frame", 0))
                if last_tracking_frame < target_frames - 1:
                    tracking_entries.extend(
                        {"frame": frame_number, "box": None, "ok": False}
                        for frame_number in range(last_tracking_frame + 1, target_frames)
                    )
            valid_tracks = [track for track in tracking_entries if track.get("ok") and track.get("box")]
            logger.info("[REFRAME] source=%dx%d clip=%d", width, height, clip_index)
            if tracking_entries and target_frames:
                fps = float((tracking or {}).get("fps") or 30.0)
                center_crop_position = (centered_crop["x"], centered_crop["y"])
                crop_points = _smooth_camera_trajectory(
                    tracking_entries,
                    crop_width=crop_width,
                    crop_height=crop_height,
                    frame_width=width,
                    frame_height=height,
                    center_crop=center_crop_position,
                    fps=fps,
                )
                item.smoothed_camera_path = crop_points

                x_points = [(point["frame"], point["x"]) for point in crop_points]
                y_points = [(point["frame"], point["y"]) for point in crop_points]
                item.reframe = {
                    "x": crop_points[0]["x"],
                    "y": crop_points[0]["y"],
                    "width": crop_width,
                    "height": crop_height,
                    "x_expression": _crop_axis_expression(x_points),
                    "y_expression": _crop_axis_expression(y_points),
                }
                representative_points = [point for point in crop_points if point["tracked"]]
                if representative_points:
                    sample_indexes = sorted({0, len(representative_points) // 2, len(representative_points) - 1})
                else:
                    sample_indexes = []
                for point_index in sample_indexes:
                    point = representative_points[point_index]
                    box_x, box_y, box_width, box_height = point["bbox"]
                    bbox_xyxy = (box_x, box_y, box_x + box_width, box_y + box_height)
                    center_x, center_y = point["raw_center"]
                    expected_output_x = (center_x - point["x"]) * 1080 / crop_width
                    logger.info(
                        "[REFRAME] frame=%d raw_bbox=%s person_center=(%.1f,%.1f) camera_crop=%dx%d crop_left=%d crop_top=%d final_subject_center_x=%.1f output=1080x1920",
                        point["frame"], bbox_xyxy, center_x, center_y, crop_width, crop_height,
                        point["x"], point["y"], expected_output_x,
                    )
                if not valid_tracks:
                    logger.warning("[REFRAME] clip=%d has no valid tracked position; using a full-duration centered camera path", clip_index)
                logger.info("[REFRAME] clip=%d raw_points=%d smoothed_points=%d dynamic_crop_expression_keyframes=%d max_path_error=%.2fpx", clip_index, len(valid_tracks), len(crop_points), len(_simplify_camera_points(x_points)), CAMERA_PATH_MAX_ERROR_PX)
            else:
                item.reframe = centered_crop.copy()
                item.smoothed_camera_path = [{
                    "frame": 0,
                    "x": centered_crop["x"],
                    "y": centered_crop["y"],
                    "bbox": None,
                    "raw_center": None,
                    "tracked": False,
                }]
                logger.warning("[REFRAME] clip=%d has no valid tracked position; using center crop: %s", clip_index, item.reframe)
            context.reframing_data.append(item.reframe)

    def captions(self, context: PipelineContext) -> None:
        source_segments = _deepgram_word_segments(context) or context.transcript_segments
        for index, item in enumerate(context.selected_clips, 1):
            srt = context.temp_dir / f"clip-{index}.srt"
            lines = []
            offset = item.candidate.candidate.start
            clip_end = item.candidate.candidate.end
            chunks = _build_subtitle_chunks(source_segments, clip_start=offset, clip_end=clip_end)
            for counter, chunk in enumerate(chunks, 1):
                lines.extend([
                    str(counter),
                    f"{self._srt_time(chunk['start'] - offset)} --> {self._srt_time(chunk['end'] - offset)}",
                    chunk["text"],
                    "",
                ])
            logger.info("[PIPELINE] Generated %d sequential subtitle chunk(s) for clip-%d", len(chunks), index)
            srt.write_text("\n".join(lines), encoding="utf-8")
            clip_duration = clip_end - offset
            relative_times = [(chunk["start"] - offset, chunk["end"] - offset) for chunk in chunks]
            timestamps_overlap_clip = all(0 <= start < end <= clip_duration for start, end in relative_times)
            first_entry = chunks[0] if chunks else None
            last_entry = chunks[-1] if chunks else None
            logger.info(
                "[SUBTITLES] clip=%d path=%s exists=%s entries=%d duration=%.3fs first=%s last=%s first_text=%r within_clip=%s",
                index, srt, srt.is_file(), len(chunks), clip_duration,
                (self._srt_time(relative_times[0][0]), self._srt_time(relative_times[0][1])) if first_entry else None,
                (self._srt_time(relative_times[-1][0]), self._srt_time(relative_times[-1][1])) if last_entry else None,
                first_entry["text"] if first_entry else None, timestamps_overlap_clip,
            )
            item.caption_path = srt
            context.captions.append(srt)

    @staticmethod
    def _srt_time(seconds: float) -> str:
        milliseconds = int(max(0, seconds) * 1000)
        hours, remainder = divmod(milliseconds, 3600000)
        minutes, remainder = divmod(remainder, 60000)
        seconds, milliseconds = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

    def audio_processing(self, context: PipelineContext) -> None:
        if not context.audio_path or not context.audio_path.is_file():
            raise ValueError("audio processing output is unavailable")

    def ffmpeg_rendering(self, context: PipelineContext) -> None:
        for index, item in enumerate(context.selected_clips, 1):
            logger.info("[PIPELINE] Starting FFmpeg rendering: clip-%d", index)
            output = context.temp_dir / f"rendered-{index}.mp4"
            editor_source = context.temp_dir / f"editor-source-{index}.mp4"
            crop = item.reframe
            logger.info("Selected reframe crop: %s", crop)
            subtitle = str(item.caption_path).replace("\\", "/").replace(":", "\\:")
            subtitle_style = SUBTITLE_FORCE_STYLE
            logger.info("[PIPELINE] Subtitle format=SRT force_style=%s", subtitle_style)
            crop_x = crop.get("x_expression", crop["x"])
            crop_y = crop.get("y_expression", crop["y"])
            reframed = f"crop={crop['width']}:{crop['height']}:{crop_x}:{crop_y},scale=1080:1920"
            caption_filter = f"subtitles='{subtitle}':original_size=1080x1920:force_style='{subtitle_style}'"
            video_filters = f"[0:v]{reframed},split=2[editor_source][caption_input];[caption_input]{caption_filter}[captioned]"
            logger.info("Final FFmpeg video filter: %s", video_filters)
            ffmpeg_args = [
                "-i", str(item.extracted_path),
                "-filter_complex", video_filters,
                "-map", "[editor_source]", "-map", "0:a?",
                "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(editor_source),
                "-map", "[captioned]", "-map", "0:a?",
                "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(output),
            ]
            self.ffmpeg.run(ffmpeg_args, output)
            if not editor_source.is_file() or editor_source.stat().st_size == 0:
                raise ValueError("FFmpeg did not produce a valid editor source")
            item.editor_source_path = editor_source
            item.rendered_path = output
            context.rendered_outputs.append(output)

    def output_validation(self, context: PipelineContext) -> None:
        for item in context.selected_clips:
            probe = self.ffmpeg.probe(item.rendered_path)
            video_stream = next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"), None)
            if not video_stream or int(video_stream.get("width", 0)) != 1080 or int(video_stream.get("height", 0)) != 1920:
                raise ValueError("rendered output is not a valid 9:16 1080x1920 video")
            if not any(stream.get("codec_type") == "audio" for stream in probe.get("streams", [])):
                raise ValueError("rendered output has no audio stream")

    def thumbnail_generation(self, context: PipelineContext) -> None:
        for index, item in enumerate(context.selected_clips, 1):
            thumbnail = context.temp_dir / f"thumbnail-{index}.jpg"
            self.ffmpeg.run(["-ss", "1", "-i", str(item.rendered_path), "-frames:v", "1", "-q:v", "2", str(thumbnail)], thumbnail)
            item.thumbnail_path = thumbnail
            context.thumbnails.append(thumbnail)

    def export(self, context: PipelineContext) -> None:
        for index, item in enumerate(context.selected_clips, 1):
            key = f"exports/{context.video.user_id}/{context.video.id}/clip-{index}.mp4"
            editor_source_key = f"exports/{context.video.user_id}/{context.video.id}/editor-source/{item.clip.id}.mp4"
            with item.editor_source_path.open("rb") as editor_source:
                if hasattr(self.storage, "client"):
                    self.storage.client.upload_fileobj(
                        editor_source,
                        self.storage.bucket,
                        editor_source_key,
                        ExtraArgs={"ContentType": "video/mp4"},
                    )
                else:
                    destination = self.storage.path_for(editor_source_key)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("wb") as destination_file:
                        shutil.copyfileobj(editor_source, destination_file)
            item.clip.editor_source_storage_key = editor_source_key
            with item.rendered_path.open("rb") as output:
                if hasattr(self.storage, "client"):
                    self.storage.client.upload_fileobj(output, self.storage.bucket, key, ExtraArgs={"ContentType": "video/mp4"})
                else:
                    destination = self.storage.path_for(key)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfileobj(output, destination.open("wb"))
            item.clip.status = ClipStatus.READY
            item.clip.output_storage_key = key
            item.clip.output_url = self.storage.url_for(key) if hasattr(self.storage, "url_for") else None
            item.clip.output_mime_type = "video/mp4"
            item.clip.output_size_bytes = item.rendered_path.stat().st_size
            thumbnail_key = f"exports/{context.video.user_id}/{context.video.id}/thumbnail-{index}.jpg"
            with item.thumbnail_path.open("rb") as thumbnail:
                if hasattr(self.storage, "client"):
                    self.storage.client.upload_fileobj(thumbnail, self.storage.bucket, thumbnail_key, ExtraArgs={"ContentType": "image/jpeg"})
                else:
                    destination = self.storage.path_for(thumbnail_key)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("wb") as destination_file:
                        shutil.copyfileobj(thumbnail, destination_file)
            item.clip.thumbnail_url = self.storage.url_for(thumbnail_key) if hasattr(self.storage, "url_for") else None
            item.clip.thumbnail_storage_key = thumbnail_key
            item.clip.completed_at = datetime.now(timezone.utc)
            export = ClipExport(clip_id=item.clip.id, status=ExportStatus.COMPLETED, output_storage_key=key, output_url=item.clip.output_url, file_size_bytes=item.clip.output_size_bytes, completed_at=datetime.now(timezone.utc))
            self.db.add(export)
            context.final_exports.append({"key": key, "url": item.clip.output_url})
        self.db.commit()

    def cleanup(self, context: PipelineContext) -> None:
        if context.temp_dir and context.temp_dir.exists():
            shutil.rmtree(context.temp_dir, ignore_errors=True)
