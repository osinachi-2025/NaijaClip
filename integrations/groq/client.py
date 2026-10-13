from __future__ import annotations

import json
import logging
import math
import os

from core.config import MAX_CLIPS_PER_VIDEO
from pipeline.validation import GroqCandidateOutput, GroqCandidates

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 14000
MAX_TRANSCRIPT_CHUNKS = 64
MAX_CANDIDATE_TARGET = 12
MAX_CANDIDATES_PER_CHUNK = MAX_CANDIDATE_TARGET
MAX_JSON_GENERATION_ATTEMPTS = 2

GROQ_CANDIDATE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "reason": {"type": "string"},
                    "scores": {
                        "type": "object",
                        "properties": {
                            "hook": {"type": "number"},
                            "value": {"type": "number"},
                            "emotional_impact": {"type": "number"},
                            "clarity": {"type": "number"},
                            "story_completeness": {"type": "number"},
                            "quotability": {"type": "number"},
                            "engagement_potential": {"type": "number"},
                            "payoff": {"type": "number"},
                            "context_independence": {"type": "number"},
                            "clip_efficiency": {"type": "number"},
                        },
                        "required": [
                            "hook",
                            "value",
                            "emotional_impact",
                            "clarity",
                            "story_completeness",
                            "quotability",
                            "engagement_potential",
                            "payoff",
                            "context_independence",
                            "clip_efficiency",
                        ],
                        "additionalProperties": False,
                    },
                    "semantic_metadata": {
                        "type": "object",
                        "properties": {
                            "is_complete_thought": {"type": "boolean"},
                            "ends_on_sentence_boundary": {"type": "boolean"},
                            "contains_conclusion_or_answer": {"type": "boolean"},
                            "has_enough_context": {"type": "boolean"},
                            "natural_pause_detected": {"type": "boolean"},
                        },
                        "required": [
                            "is_complete_thought",
                            "ends_on_sentence_boundary",
                            "contains_conclusion_or_answer",
                            "has_enough_context",
                            "natural_pause_detected",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required": ["start", "end", "reason", "scores", "semantic_metadata"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


class GroqOutputError(ValueError):
    """A bounded Groq response-generation failure with transcript-free diagnostics."""


class GroqClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        from groq import Groq

        self.client = Groq(api_key=api_key or os.getenv("GROQ_API_KEY"))
        self.model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    @staticmethod
    def _chunk_segments(
        segments: list[dict],
        limit: int = MAX_PROMPT_CHARS,
        max_duration: float = 600.0,
    ) -> list[list[dict]]:
        chunks: list[list[dict]] = []
        current: list[dict] = []
        used = 2
        chunk_start = 0.0
        for segment in segments:
            text = " ".join(str(segment.get("text", "")).split())
            if not text:
                continue
            item = {
                "s": round(float(segment.get("start", 0)), 2),
                "e": round(float(segment.get("end", 0)), 2),
                "t": text,
            }
            encoded_size = len(json.dumps(item, separators=(",", ":"))) + 1
            if encoded_size > limit:
                raise ValueError("a timestamped transcript segment exceeds the prompt size limit")
            elapsed = item["e"] - chunk_start if current else 0.0
            if current and (used + encoded_size > limit or elapsed > max_duration):
                chunks.append(current)
                current = []
                used = 2
            if not current:
                chunk_start = item["s"]
            current.append(item)
            used += encoded_size
        if current:
            chunks.append(current)
        if len(chunks) > MAX_TRANSCRIPT_CHUNKS:
            raise ValueError("transcript exceeds the bounded clip-selection window count")
        return chunks

    @staticmethod
    def _candidate_target(source_duration: float) -> int:
        if source_duration <= 60:
            return min(MAX_CLIPS_PER_VIDEO, 3)
        return min(MAX_CANDIDATE_TARGET, MAX_CLIPS_PER_VIDEO, max(5, math.ceil(source_duration / 300)))

    @staticmethod
    def _output_token_budget(candidate_count: int) -> int:
        return min(6000, max(2200, 1800 + candidate_count * 450))

    def _response_format(self) -> dict[str, object]:
        if self.model.lower() in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "groq_clip_candidates",
                    "strict": True,
                    "schema": GROQ_CANDIDATE_JSON_SCHEMA,
                },
            }
        return {"type": "json_object"}

    @staticmethod
    def _provider_error_code(error: Exception) -> str | None:
        body = getattr(error, "body", None)
        if not isinstance(body, dict):
            return None
        details = body.get("error", body)
        if isinstance(details, dict):
            code = details.get("code")
            return str(code) if code is not None else None
        return None

    @staticmethod
    def _request_diagnostics(
        *,
        model: str,
        chunk_index: int,
        messages: list[dict[str, str]],
        response_format: dict[str, object],
        candidate_count: int,
        output_token_budget: int,
        http_status: int | None,
        error_code: str | None,
    ) -> dict[str, object]:
        serialized_size = len(
            json.dumps(
                {"model": model, "messages": messages, "response_format": response_format},
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        return {
            "model": model,
            "chunk_index": chunk_index,
            "serialized_input_bytes": serialized_size,
            "estimated_input_tokens": math.ceil(serialized_size / 4),
            "candidate_count": candidate_count,
            "output_token_budget": output_token_budget,
            "http_status": http_status if http_status is not None else "unknown",
            "error_code": error_code or "unknown",
        }

    @staticmethod
    def _assert_candidate_grounding(
        candidates: list[GroqCandidateOutput],
        compact_segments: list[dict],
        candidate_limit: int,
    ) -> None:
        if len(candidates) > candidate_limit:
            raise ValueError("response exceeded the requested candidate count")
        valid_starts = {segment["s"] for segment in compact_segments}
        valid_ends = {segment["e"] for segment in compact_segments}
        for candidate in candidates:
            if candidate.end <= candidate.start:
                raise ValueError("candidate end must be greater than start")
            if round(candidate.start, 2) not in valid_starts:
                raise ValueError("candidate start is not a supplied transcript timestamp")
            if round(candidate.end, 2) not in valid_ends:
                raise ValueError("candidate end is not a supplied transcript timestamp")
            if candidate.semantic_metadata is None:
                raise ValueError("candidate is missing required semantic metadata")

    def evaluate_transcript(self, transcript: str, segments: list[dict], source_duration: float) -> GroqCandidates:
        segment_chunks = self._chunk_segments(segments)
        if not segment_chunks:
            raise ValueError("transcript contains no timestamped segments")
        target_count = self._candidate_target(source_duration)
        candidates_per_chunk = min(
            MAX_CANDIDATES_PER_CHUNK,
            max(1, math.ceil(target_count / len(segment_chunks))),
        )
        score_fields = "hook,value,emotional_impact,clarity,story_completeness,quotability,engagement_potential,payoff,context_independence,clip_efficiency"
        example_shape = json.dumps(
            {
                "candidates": [{
                    "start": 0,
                    "end": 1,
                    "reason": "brief",
                    "scores": {field: 0 for field in score_fields.split(",")},
                    "semantic_metadata": {
                        "is_complete_thought": True,
                        "ends_on_sentence_boundary": True,
                        "contains_conclusion_or_answer": True,
                        "has_enough_context": True,
                        "natural_pause_detected": True,
                    },
                }],
            },
            separators=(",", ":"),
        )
        if source_duration <= 15:
            target_window = "short videos: aim for about 6-12 seconds"
        elif source_duration <= 60:
            target_window = "medium videos: aim for about 12-22 seconds"
        elif source_duration <= 180:
            target_window = "longer videos: aim for about 18-35 seconds"
        else:
            target_window = "very long videos: aim for about 22-60 seconds"

        aggregated = []
        for chunk_index, compact_segments in enumerate(segment_chunks, 1):
            window_start = compact_segments[0]["s"]
            window_end = compact_segments[-1]["e"]
            payload = json.dumps(
                {"source_duration": round(source_duration, 2), "timestamped_segments": compact_segments},
                separators=(",", ":"),
            )
            last_diagnostics: dict[str, object] | None = None
            chunk_succeeded = False

            for attempt in range(MAX_JSON_GENERATION_ATTEMPTS):
                request_candidate_count = (
                    candidates_per_chunk
                    if attempt == 0
                    else max(1, math.ceil(candidates_per_chunk / 2))
                )
                if attempt == 0:
                    prompt = (
                        f"Return valid JSON only. Select no more than {request_candidate_count} self-contained, "
                        f"distinct clips from transcript window {chunk_index} ({window_start:.2f}-{window_end:.2f}s). "
                        f"The full source duration is {source_duration:.2f} seconds; every start/end must be within "
                        f"0 and {source_duration:.2f}, and candidates must come from the supplied window. Use this "
                        f"exact shape: {example_shape}. "
                        f"Every score is a number from 0 to 10. Do not use arrays for scores. Keep each reason "
                        f"under 12 words. Use only supplied timestamps. {target_window}. Clip length should scale "
                        "with source runtime. Never end a clip in the middle of a sentence or thought; extend to "
                        "finish the thought when the supplied window permits. Prefer natural sentence boundaries, "
                        "complete thoughts, context, a strong hook, and payoff."
                    )
                else:
                    prompt = (
                        f"Return JSON only with a candidates array. Select at most {request_candidate_count} "
                        f"distinct, high-quality clips from window {chunk_index} ({window_start:.2f}-"
                        f"{window_end:.2f}s), within source duration {source_duration:.2f}s. Use only supplied "
                        "timestamps; start must equal a supplied segment start and end a supplied segment end. "
                        f"Each candidate needs start, end, a concise reason, scores with exactly these numeric "
                        f"0-10 fields: {score_fields}, and semantic_metadata with exactly these five boolean fields: "
                        "is_complete_thought, ends_on_sentence_boundary, contains_conclusion_or_answer, "
                        "has_enough_context, natural_pause_detected. Prefer complete, contextual moments with a strong "
                        "hook and payoff, ending at a natural sentence boundary. "
                        f"{target_window}. Do not repeat moments."
                    )
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": payload},
                ]
                token_budget = self._output_token_budget(request_candidate_count)
                response_format = self._response_format()

                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        temperature=0,
                        max_tokens=token_budget,
                        response_format=response_format,
                        messages=messages,
                    )
                except Exception as error:
                    status = getattr(error, "status_code", None)
                    error_code = self._provider_error_code(error)
                    diagnostics = self._request_diagnostics(
                        model=self.model,
                        chunk_index=chunk_index,
                        messages=messages,
                        response_format=response_format,
                        candidate_count=request_candidate_count,
                        output_token_budget=token_budget,
                        http_status=status,
                        error_code=error_code,
                    )
                    logger.warning("Groq completion request failed diagnostics=%s", diagnostics)
                    last_diagnostics = diagnostics

                    if status == 413:
                        raise GroqOutputError(
                            f"Groq request payload too large (HTTP 413; {diagnostics})"
                        ) from error
                    if status == 400 and error_code == "json_validate_failed" and attempt == 0:
                        continue
                    if status == 400 and error_code == "json_validate_failed":
                        break
                    raise

                content = response.choices[0].message.content or ""
                try:
                    parsed = GroqCandidates.model_validate_json(content)
                    self._assert_candidate_grounding(
                        parsed.candidates,
                        compact_segments,
                        request_candidate_count,
                    )
                except (ValueError, TypeError) as error:
                    validation_error_code = "malformed_or_invalid_candidate_json"
                    diagnostics = self._request_diagnostics(
                        model=self.model,
                        chunk_index=chunk_index,
                        messages=messages,
                        response_format=response_format,
                        candidate_count=request_candidate_count,
                        output_token_budget=token_budget,
                        http_status=200,
                        error_code=validation_error_code,
                    )
                    logger.warning("Groq response validation failed diagnostics=%s", diagnostics)
                    last_diagnostics = diagnostics
                    if attempt == 0:
                        continue
                    break

                aggregated.extend(parsed.candidates)
                chunk_succeeded = True
                break
            else:
                raise AssertionError("Groq response attempt loop completed unexpectedly")

            if not chunk_succeeded:
                raise GroqOutputError(
                    f"Groq output failure after bounded recovery ({last_diagnostics or {}})"
                )
        return GroqCandidates(candidates=aggregated)
