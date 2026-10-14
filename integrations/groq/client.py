from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

from core.config import GROQ_MAX_INPUT_TOKENS, MAX_CLIPS_PER_VIDEO
from pipeline.validation import GroqCandidateOutput, GroqCandidates
from .rate_limit import GroqRateLimiter

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 14000
MAX_TRANSCRIPT_CHUNKS = 64
MAX_CANDIDATE_TARGET = 12
MAX_CANDIDATES_PER_CHUNK = MAX_CANDIDATE_TARGET
MAX_JSON_GENERATION_ATTEMPTS = 2
CHUNK_CHECKPOINT_VERSION = 1

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


class GroqRateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: float, details: dict[str, str] | None = None):
        self.status_code = 429
        self.retry_after_seconds = max(0.001, retry_after_seconds)
        self.details = details or {}
        super().__init__(
            "Groq rate limit exceeded (HTTP 429; "
            f"retry after {self.retry_after_seconds:.3f}s; {self.details})"
        )


class GroqClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        from groq import Groq

        self._api_key = api_key or os.getenv("GROQ_API_KEY")
        self.client = Groq(api_key=self._api_key)
        self.model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.rate_limiter = GroqRateLimiter(model=self.model)

    @staticmethod
    def _estimate_tokens(value: str) -> int:
        return max(1, math.ceil(len(value.encode("utf-8")) / 3))

    @staticmethod
    def _sentence_groups(segments: list[dict], max_duration: float = 600.0) -> list[list[dict]]:
        groups: list[list[dict]] = []
        current: list[dict] = []
        for segment in segments:
            text = " ".join(str(segment.get("text", segment.get("t", ""))).split())
            if not text:
                continue
            item = {
                "s": round(float(segment.get("start", segment.get("s", 0))), 2),
                "e": round(float(segment.get("end", segment.get("e", 0))), 2),
                "t": text,
            }
            if current and item["e"] - current[0]["s"] > max_duration:
                groups.append(current)
                current = []
            current.append(item)
            if text.rstrip("\"')]}").endswith((".", "!", "?")):
                groups.append(current)
                current = []
        if current:
            groups.append(current)
        return groups

    @staticmethod
    def _chunk_segments(
        segments: list[dict],
        limit: int = MAX_PROMPT_CHARS,
        max_duration: float = 600.0,
    ) -> list[list[dict]]:
        token_budget = min(GROQ_MAX_INPUT_TOKENS, max(500, limit // 3 - 400))
        chunks: list[list[dict]] = []
        current: list[dict] = []
        used_tokens = 0
        previous_sentence: list[dict] = []

        def estimated_group_tokens(group: list[dict]) -> int:
            encoded = json.dumps(group, separators=(",", ":"), ensure_ascii=False)
            return GroqClient._estimate_tokens(encoded)

        for sentence in GroqClient._sentence_groups(segments, max_duration):
            sentence_tokens = estimated_group_tokens(sentence)
            if sentence_tokens > token_budget:
                if current:
                    chunks.append(current)
                    previous_sentence = GroqClient._sentence_groups(current)[-1]
                    current = []
                    used_tokens = 0
                for item in sentence:
                    item_tokens = estimated_group_tokens([item])
                    if item_tokens > token_budget or len(json.dumps(item, ensure_ascii=False)) > limit:
                        raise ValueError("a timestamped transcript segment exceeds the prompt size limit")
                    elapsed = item["e"] - current[0]["s"] if current else 0
                    if current and (used_tokens + item_tokens > token_budget or elapsed > max_duration):
                        chunks.append(current)
                        previous_sentence = current[-1:]
                        current = []
                        used_tokens = 0
                    if not current and previous_sentence:
                        context_tokens = estimated_group_tokens(previous_sentence)
                        context_duration = item["e"] - previous_sentence[0]["s"]
                        if context_tokens + item_tokens <= token_budget and context_duration <= max_duration:
                            current = list(previous_sentence)
                            used_tokens = context_tokens
                        previous_sentence = []
                    current.append(item)
                    used_tokens += item_tokens
                continue

            elapsed = sentence[-1]["e"] - current[0]["s"] if current else 0
            if current and (used_tokens + sentence_tokens > token_budget or elapsed > max_duration):
                chunks.append(current)
                previous_sentence = GroqClient._sentence_groups(current)[-1]
                current = []
                used_tokens = 0

            if not current and previous_sentence:
                context_tokens = estimated_group_tokens(previous_sentence)
                if context_tokens + sentence_tokens <= token_budget:
                    context_duration = sentence[-1]["e"] - previous_sentence[0]["s"]
                    if context_duration <= max_duration:
                        current = list(previous_sentence)
                        used_tokens = context_tokens
                previous_sentence = []

            current.extend(sentence)
            used_tokens += sentence_tokens
        if current:
            chunks.append(current)
        if len(chunks) > MAX_TRANSCRIPT_CHUNKS:
            raise ValueError("transcript exceeds the bounded clip-selection window count")
        return chunks

    @staticmethod
    def _header(headers, name: str) -> str | None:
        if headers is None:
            return None
        value = headers.get(name) if hasattr(headers, "get") else None
        if value is None and hasattr(headers, "items"):
            value = next(
                (item for key, item in headers.items() if str(key).lower() == name.lower()),
                None,
            )
        return str(value).strip() if value is not None else None

    @staticmethod
    def _error_headers(error: Exception):
        response = getattr(error, "response", None)
        return getattr(response, "headers", None) or getattr(error, "headers", None)

    def _chunk_key(self, compact_segments: list[dict], source_duration: float) -> str:
        payload = json.dumps(
            {"source_duration": round(source_duration, 2), "timestamped_segments": compact_segments},
            separators=(",", ":"),
        )
        return hashlib.sha256(
            f"{CHUNK_CHECKPOINT_VERSION}\0{self.model}\0{payload}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _parse_delay(value: str | None) -> float | None:
        if not value:
            return None
        try:
            seconds = float(value)
            return seconds if math.isfinite(seconds) and 0 <= seconds <= 315360000 else None
        except ValueError:
            pass
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            seconds = max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            return seconds if seconds <= 315360000 else None
        except (TypeError, ValueError, OverflowError):
            pass

        factors = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
        matches = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value.lower())
        if not matches or "".join(number + unit for number, unit in matches) != value.lower().replace(" ", ""):
            return None
        seconds = sum(float(number) * factors[unit] for number, unit in matches)
        return seconds if math.isfinite(seconds) and seconds <= 315360000 else None

    @classmethod
    def _rate_limit_delay(cls, error: Exception, fallback: float = 60.0) -> float:
        headers = cls._error_headers(error)
        delays = [
            cls._parse_delay(cls._header(headers, "retry-after")),
            cls._parse_delay(cls._header(headers, "x-ratelimit-reset-requests")),
            cls._parse_delay(cls._header(headers, "x-ratelimit-reset-tokens")),
        ]
        usable = [delay for delay in delays if delay is not None]
        return max(1.0, max(usable)) if usable else fallback

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
    def _redact_provider_text(value: str, sensitive_texts: list[str]) -> str:
        text = value
        for sensitive_text in sorted(sensitive_texts, key=len, reverse=True):
            if not sensitive_text:
                continue
            text = text.replace(sensitive_text, "[redacted]")
            words = sensitive_text.split()
            if len(words) > 1:
                text = re.sub(r"\s+".join(map(re.escape, words)), "[redacted]", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:gsk_[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]+|Bearer\s+\S+)\b", "[redacted]", text)
        text = re.sub(
            r"(?i)\b(?:https?|redis(?:s)?)://[^/\s:@]+:[^@\s/]+@",
            "[redacted]://",
            text,
        )
        return text

    @staticmethod
    def _provider_error_code(
        error: Exception,
        sensitive_texts: list[str] | None = None,
    ) -> str | None:
        body = getattr(error, "body", None)
        if not isinstance(body, dict):
            return None
        details = body.get("error", body)
        if isinstance(details, dict):
            code = details.get("code")
            if code is None:
                return None
            return GroqClient._redact_provider_text(str(code), sensitive_texts or [])
        return None

    @staticmethod
    def _provider_error_details(
        error: Exception,
        sensitive_texts: list[str],
    ) -> dict[str, str]:
        body = getattr(error, "body", None)
        if not isinstance(body, dict):
            return {}
        details = body.get("error", body)
        if not isinstance(details, dict):
            return {}

        result = {}
        for field in ("message", "type", "param", "details", "validation_error", "errors"):
            value = details.get(field)
            if value is None:
                continue
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            text = GroqClient._redact_provider_text(text, sensitive_texts)
            result[f"provider_{field}"] = text[:1000]
        return result

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

    def evaluate_transcript(
        self,
        transcript: str,
        segments: list[dict],
        source_duration: float,
        *,
        cached_chunks: dict[str, list[dict]] | None = None,
        on_chunk_complete: Callable[[dict[str, list[dict]], int, int], None] | None = None,
    ) -> GroqCandidates:
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
        chunk_keys = {self._chunk_key(chunk, source_duration) for chunk in segment_chunks}
        successful_chunks = {
            key: value
            for key, value in (cached_chunks or {}).items()
            if key in chunk_keys
        }
        for chunk_index, compact_segments in enumerate(segment_chunks, 1):
            window_start = compact_segments[0]["s"]
            window_end = compact_segments[-1]["e"]
            payload = json.dumps(
                {"source_duration": round(source_duration, 2), "timestamped_segments": compact_segments},
                separators=(",", ":"),
            )
            chunk_key = self._chunk_key(compact_segments, source_duration)
            if chunk_key in successful_chunks:
                parsed = GroqCandidates.model_validate({"candidates": successful_chunks[chunk_key]})
                self._assert_candidate_grounding(parsed.candidates, compact_segments, MAX_CANDIDATES_PER_CHUNK)
                aggregated.extend(parsed.candidates)
                continue
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
                if attempt > 0 and self.model.lower() in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
                    response_format = {"type": "json_object"}

                request_tokens = (
                    self._estimate_tokens(messages[0]["content"])
                    + self._estimate_tokens(messages[1]["content"])
                    + token_budget
                )
                limiter = getattr(self, "rate_limiter", None)
                if limiter is not None:
                    wait_seconds = limiter.acquire(request_tokens)
                    if wait_seconds > 0:
                        raise GroqRateLimitError(wait_seconds, {"shared_limiter_wait_seconds": f"{wait_seconds:.3f}"})

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
                    sensitive_texts = [
                        transcript,
                        getattr(self, "_api_key", ""),
                        os.getenv("GROQ_API_KEY", ""),
                        *(str(segment.get("text", "")) for segment in segments),
                    ]
                    error_code = self._provider_error_code(error, sensitive_texts)
                    provider_details = self._provider_error_details(
                        error,
                        sensitive_texts,
                    )
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
                    diagnostics.update(provider_details)
                    logger.warning("Groq completion request failed diagnostics=%s", diagnostics)
                    last_diagnostics = diagnostics

                    if status == 429:
                        from core.config import GROQ_429_FALLBACK_SECONDS

                        delay = self._rate_limit_delay(error, GROQ_429_FALLBACK_SECONDS)
                        if limiter is not None:
                            limiter.defer_all_workers(delay)
                        details = {
                            key: value
                            for key, value in diagnostics.items()
                            if key.startswith("provider_") or key in {"http_status", "error_code"}
                        }
                        headers = self._error_headers(error)
                        for header in (
                            "retry-after",
                            "x-ratelimit-limit-requests",
                            "x-ratelimit-remaining-requests",
                            "x-ratelimit-reset-requests",
                            "x-ratelimit-limit-tokens",
                            "x-ratelimit-remaining-tokens",
                            "x-ratelimit-reset-tokens",
                        ):
                            value = self._header(headers, header)
                            if value is not None:
                                details[f"provider_header_{header}"] = value[:200]
                        raise GroqRateLimitError(delay, details) from None
                    if status == 413:
                        raise GroqOutputError(
                            f"Groq request payload too large (HTTP 413; {diagnostics})"
                        ) from error
                    if (
                        status == 400
                        and error_code == "json_validate_failed"
                        and attempt == 0
                        and response_format.get("type") == "json_schema"
                    ):
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
                successful_chunks[chunk_key] = [
                    candidate.model_dump(mode="json") for candidate in parsed.candidates
                ]
                if on_chunk_complete is not None:
                    on_chunk_complete(dict(successful_chunks), chunk_index, len(segment_chunks))
                chunk_succeeded = True
                break
            else:
                raise AssertionError("Groq response attempt loop completed unexpectedly")

            if not chunk_succeeded:
                raise GroqOutputError(
                    f"Groq output failure after bounded recovery ({last_diagnostics or {}})"
                )
        return GroqCandidates(candidates=aggregated)
