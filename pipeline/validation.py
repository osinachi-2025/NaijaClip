from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCORE_FIELDS = (
    "hook", "value", "emotional_impact", "clarity", "story_completeness",
    "quotability", "engagement_potential", "payoff", "context_independence",
    "clip_efficiency",
)
logger = logging.getLogger(__name__)
NATURAL_SPEECH_PAUSE_SECONDS = 1.5
MAX_SENTENCE_EXTENSION_GAP_SECONDS = 2.5


class CandidateScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook: float = Field(ge=0, le=10)
    value: float = Field(ge=0, le=10)
    emotional_impact: float = Field(ge=0, le=10)
    clarity: float = Field(ge=0, le=10)
    story_completeness: float = Field(ge=0, le=10)
    quotability: float = Field(ge=0, le=10)
    engagement_potential: float = Field(ge=0, le=10)
    payoff: float = Field(ge=0, le=10)
    context_independence: float = Field(ge=0, le=10)
    clip_efficiency: float = Field(ge=0, le=10)


class ClipCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    reason: str = Field(min_length=1)
    scores: CandidateScores

    @field_validator("scores", mode="before")
    @classmethod
    def normalize_scores(cls, value):
        if isinstance(value, list):
            if len(value) != len(SCORE_FIELDS):
                raise ValueError(f"scores array must contain exactly {len(SCORE_FIELDS)} values")
            return dict(zip(SCORE_FIELDS, value))
        return value

    @field_validator("end")
    @classmethod
    def end_after_start(cls, value: float, info):
        start = info.data.get("start")
        if start is not None and value <= start:
            raise ValueError("end must be greater than start")
        return value


class ClipSemanticMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_complete_thought: bool = Field(default=True)
    ends_on_sentence_boundary: bool = Field(default=True)
    contains_conclusion_or_answer: bool = Field(default=True)
    has_enough_context: bool = Field(default=True)
    natural_pause_detected: bool = Field(default=True)


class GroqCandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    reason: str = Field(min_length=1)
    scores: CandidateScores
    semantic_metadata: ClipSemanticMetadata | None = None

    @field_validator("scores", mode="before")
    @classmethod
    def normalize_scores(cls, value):
        if isinstance(value, list):
            if len(value) != len(SCORE_FIELDS):
                raise ValueError(f"scores array must contain exactly {len(SCORE_FIELDS)} values")
            return dict(zip(SCORE_FIELDS, value))
        return value


class GroqCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[GroqCandidateOutput]


class ClipSemanticMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: float
    end_time: float
    target_duration: float
    actual_duration: float
    reason: str
    is_complete_thought: bool = True
    ends_on_sentence_boundary: bool = True
    contains_conclusion_or_answer: bool = True
    has_enough_context: bool = True
    natural_pause_detected: bool = True


@dataclass(frozen=True)
class ValidatedCandidate:
    candidate: ClipCandidate
    duration: float
    semantic_metadata: ClipSemanticMetadata


def clip_duration_range(source_duration: float) -> tuple[float, float]:
    if source_duration <= 15:
        return 6.0, 12.0
    if source_duration <= 30:
        return 8.0, 15.0
    if source_duration <= 60:
        return 12.0, 22.0
    if source_duration <= 120:
        return 16.0, 28.0
    if source_duration <= 300:
        return 18.0, 35.0
    return 22.0, 45.0


def _sentence_complete(text: str | None) -> bool:
    if not text:
        return True
    cleaned = " ".join(str(text).strip().split())
    return bool(cleaned) and cleaned.rstrip("\"'”’)]}").endswith((".", "!", "?", "…"))


def _continuation_connector(text: str | None) -> bool:
    if not text:
        return False
    lowered = " ".join(str(text).strip().split()).lower()
    connectors = (
        "because", "so", "that means", "which", "therefore", "however",
        "but", "also", "and", "then", "or", "eventually", "finally",
        "in the end", "at first", "after that", "ultimately",
        "what happened was", "here's what happened", "this is why", "that is why",
    )
    return any(lowered == connector or lowered.startswith(connector + " ") or lowered.startswith(connector + ",") for connector in connectors)


def _trailing_continuation(text: str | None) -> bool:
    if not text:
        return False
    cleaned = " ".join(str(text).strip().split()).lower().rstrip(".,;:—–- ")
    return any(cleaned.endswith(connector) for connector in (
        "because", "and", "but", "so", "which", "that", "or", "although", "if", "when",
    ))


def _continues_thought(current_text: str | None, next_text: str | None) -> bool:
    return _trailing_continuation(current_text) or _continuation_connector(next_text)


def _candidate_has_complete_thought(candidate: ClipCandidate, segments: list[dict[str, Any]]) -> bool:
    if not segments:
        return True
    overlapping = [segment for segment in segments if segment["start"] < candidate.end and segment["end"] > candidate.start]
    if not overlapping:
        return True
    last_segment = overlapping[-1]
    if _sentence_complete(last_segment.get("text")):
        return True

    next_segment = next((segment for segment in segments if segment["start"] >= last_segment["end"]), None)
    if next_segment is None:
        return True

    gap = next_segment["start"] - last_segment["end"]
    return gap >= NATURAL_SPEECH_PAUSE_SECONDS and not _continues_thought(
        last_segment.get("text"), next_segment.get("text")
    )


def _semantic_completion_metadata(candidate: ClipCandidate, source_duration: float, target_duration: float, transcript_segments: Iterable[dict[str, Any]] | None = None) -> ClipSemanticMetadata:
    if not transcript_segments:
        return ClipSemanticMetadata(
            start_time=candidate.start,
            end_time=candidate.end,
            target_duration=target_duration,
            actual_duration=candidate.end - candidate.start,
            reason=candidate.reason,
            is_complete_thought=True,
            ends_on_sentence_boundary=True,
            contains_conclusion_or_answer=True,
            has_enough_context=True,
            natural_pause_detected=True,
        )

    segments = sorted(
        [
            {"start": float(segment.get("start", 0)), "end": float(segment.get("end", 0)), "text": str(segment.get("text", "")).strip()}
            for segment in transcript_segments
            if float(segment.get("end", 0)) > float(segment.get("start", 0))
        ],
        key=lambda item: item["start"],
    )

    overlapping = [segment for segment in segments if segment["start"] < candidate.end and segment["end"] > candidate.start]
    last_segment = overlapping[-1] if overlapping else {"start": candidate.start, "end": candidate.end, "text": candidate.reason}
    next_segment = next((segment for segment in segments if segment["start"] >= last_segment["end"]), None)
    reason = last_segment.get("text") or candidate.reason or "complete thought"
    clean_reason = " ".join(str(reason).split())

    complete_thought = _candidate_has_complete_thought(candidate, segments)
    next_gap = next_segment["start"] - last_segment["end"] if next_segment else float("inf")
    natural_break = next_segment is None or (
        next_gap >= NATURAL_SPEECH_PAUSE_SECONDS
        and not _continues_thought(last_segment.get("text"), next_segment.get("text"))
    )
    ends_on_sentence_boundary = _sentence_complete(last_segment.get("text")) or natural_break
    contains_conclusion_or_answer = complete_thought and (ends_on_sentence_boundary or any(keyword in clean_reason.lower() for keyword in ["because", "therefore", "finally", "result", "answer", "means", "this is why", "in the end", "ultimately", "this happened", "here's what happened"]))
    has_enough_context = bool(overlapping) or candidate.start == 0
    natural_pause_detected = ends_on_sentence_boundary or natural_break

    return ClipSemanticMetadata(
        start_time=candidate.start,
        end_time=candidate.end,
        target_duration=target_duration,
        actual_duration=candidate.end - candidate.start,
        reason=clean_reason,
        is_complete_thought=complete_thought,
        ends_on_sentence_boundary=ends_on_sentence_boundary,
        contains_conclusion_or_answer=contains_conclusion_or_answer,
        has_enough_context=has_enough_context,
        natural_pause_detected=natural_pause_detected,
    )


def _expand_to_sentence_boundary(candidate: ClipCandidate, source_duration: float, transcript_segments: Iterable[dict[str, Any]] | None = None) -> ClipCandidate:
    if not transcript_segments:
        return candidate

    segments = sorted(
        [
            {
                "start": float(segment.get("start", 0)),
                "end": float(segment.get("end", 0)),
                "text": str(segment.get("text", "")).strip(),
            }
            for segment in transcript_segments
            if float(segment.get("end", 0)) > float(segment.get("start", 0))
        ],
        key=lambda item: item["start"],
    )
    if not segments:
        return candidate

    overlapping = [segment for segment in segments if segment["start"] < candidate.end and segment["end"] > candidate.start]
    if not overlapping:
        return candidate

    start_index = min(i for i, segment in enumerate(segments) if segment["start"] < candidate.end and segment["end"] > candidate.start)
    end_index = max(i for i, segment in enumerate(segments) if segment["start"] < candidate.end and segment["end"] > candidate.start)

    new_start = overlapping[0]["start"]
    new_end = overlapping[-1]["end"]

    while start_index > 0:
        previous_segment = segments[start_index - 1]
        current_segment = segments[start_index]
        gap = current_segment["start"] - previous_segment["end"]
        if _sentence_complete(previous_segment.get("text")):
            break
        if gap >= NATURAL_SPEECH_PAUSE_SECONDS and not _continues_thought(
            previous_segment.get("text"), current_segment.get("text")
        ):
            break
        new_start = previous_segment["start"]
        start_index -= 1

    current_end = overlapping[-1]
    next_segment = segments[end_index + 1] if end_index + 1 < len(segments) else None
    while current_end is not None and not _sentence_complete(current_end.get("text")):
        if next_segment and (
            next_segment["start"] - current_end["end"] <= MAX_SENTENCE_EXTENSION_GAP_SECONDS
            or _continues_thought(current_end.get("text"), next_segment.get("text"))
        ):
            new_end = next_segment["end"]
            current_end = next_segment
            end_index += 1
            next_segment = segments[end_index + 1] if end_index + 1 < len(segments) else None
            if _sentence_complete(current_end.get("text")):
                break
            continue
        break

    if _candidate_has_complete_thought(candidate.model_copy(update={"start": new_start, "end": new_end}), segments):
        return candidate.model_copy(update={"start": max(0.0, new_start), "end": min(source_duration, max(new_end, candidate.end))})

    return candidate.model_copy(update={"start": max(0.0, new_start), "end": min(source_duration, max(overlapping[-1]["end"], candidate.end))})


def _pad_short_candidate(candidate: ClipCandidate, source_duration: float, minimum_duration: float, transcript_segments: Iterable[dict[str, Any]] | None = None) -> ClipCandidate:
    candidate = _expand_to_sentence_boundary(candidate, source_duration, transcript_segments)
    duration = candidate.end - candidate.start
    if duration >= minimum_duration:
        return candidate
    if transcript_segments:
        segments = sorted(transcript_segments, key=lambda item: float(item.get("start", 0)))
        if _candidate_has_complete_thought(candidate, segments):
            return candidate

    new_start = candidate.start
    new_end = candidate.end

    if transcript_segments:
        segments = sorted(
            [
                {"start": float(segment.get("start", 0)), "end": float(segment.get("end", 0)), "text": str(segment.get("text", "")).strip()}
                for segment in transcript_segments
                if float(segment.get("end", 0)) > float(segment.get("start", 0))
            ],
            key=lambda item: item["start"],
        )
        if segments:
            overlapping = [segment for segment in segments if segment["start"] < candidate.end and segment["end"] > candidate.start]
            if overlapping:
                new_start = min(segment["start"] for segment in overlapping)
                new_end = max(segment["end"] for segment in overlapping)

            previous_segment = max((segment for segment in segments if segment["end"] <= candidate.start), default=None, key=lambda item: item["end"])
            next_segment = min((segment for segment in segments if segment["start"] >= candidate.end), default=None, key=lambda item: item["start"])

            if previous_segment is not None:
                new_start = min(new_start, previous_segment["start"])
            if next_segment is not None:
                new_end = max(new_end, next_segment["end"])

            remaining = max(0.0, minimum_duration - (new_end - new_start))
            if remaining > 0 and next_segment is not None and source_duration - new_end > 0:
                extend_after = min(remaining, source_duration - new_end)
                new_end += extend_after
                remaining -= extend_after
            if remaining > 0 and previous_segment is not None and new_start > previous_segment["start"]:
                extend_before = min(remaining, new_start - previous_segment["start"])
                new_start -= extend_before
                remaining -= extend_before
            if remaining > 0 and new_end < source_duration:
                new_end = min(source_duration, new_end + remaining)

    if new_end - new_start < minimum_duration:
        pad = minimum_duration - (new_end - new_start)
        if new_end < source_duration:
            new_end = min(source_duration, new_end + pad)

    return candidate.model_copy(update={"start": max(0.0, new_start), "end": min(source_duration, new_end)})


def validate_candidates(candidates: Iterable[ClipCandidate], source_duration: float, minimum_duration: float = 20.0, maximum_duration: float = 180.0, transcript_segments: Iterable[dict[str, Any]] | None = None) -> list[ValidatedCandidate]:
    if source_duration <= 0:
        raise ValueError("source duration must be positive")
    default_min, _ = clip_duration_range(source_duration)
    minimum_duration = max(default_min, float(minimum_duration))
    maximum_duration = max(float(maximum_duration), minimum_duration)
    valid: list[ValidatedCandidate] = []
    for candidate in candidates:
        if candidate.end > source_duration:
            continue

        original_candidate = candidate
        padded = _pad_short_candidate(candidate, source_duration, minimum_duration, transcript_segments=transcript_segments)
        duration = padded.end - padded.start

        semantic_ok = True
        if transcript_segments:
            segments = sorted(
                [
                    {"start": float(segment.get("start", 0)), "end": float(segment.get("end", 0)), "text": str(segment.get("text", "")).strip()}
                    for segment in transcript_segments
                    if float(segment.get("end", 0)) > float(segment.get("start", 0))
                ],
                key=lambda item: item["start"],
            )
            semantic_ok = _candidate_has_complete_thought(padded, segments)

        completion = _semantic_completion_metadata(padded, source_duration, minimum_duration, transcript_segments)
        semantic_ok = completion.is_complete_thought and completion.has_enough_context and completion.natural_pause_detected

        if padded.start != original_candidate.start or padded.end != original_candidate.end:
            boundary_reason = "sentence_boundary" if completion.ends_on_sentence_boundary else "natural_pause" if completion.natural_pause_detected else "minimum_duration"
            logger.info(
                "[CLIP BOUNDARY] candidate_start=%.3f candidate_end=%.3f final_start=%.3f final_end=%.3f reason=%s",
                original_candidate.start, original_candidate.end,
                padded.start, padded.end, boundary_reason,
            )

        if duration < minimum_duration and not (transcript_segments and completion.is_complete_thought):
            continue

        if transcript_segments and not completion.is_complete_thought:
            continue

        if duration > maximum_duration and not semantic_ok:
            continue

        if padded != candidate:
            candidate = padded

        valid.append(ValidatedCandidate(candidate, duration, completion))
    return valid


def remove_overlaps(candidates: Iterable[ValidatedCandidate]) -> list[ValidatedCandidate]:
    result: list[ValidatedCandidate] = []
    for item in candidates:
        if any(item.candidate.start < other.candidate.end and item.candidate.end > other.candidate.start for other in result):
            continue
        result.append(item)
    return result
