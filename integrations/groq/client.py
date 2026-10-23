from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

from core.config import (
    GROQ_429_FALLBACK_SECONDS,
    LLM_TIMEOUT_SECONDS,
    GROQ_SELECTION_BATCH_SIZE,
    GROQ_SELECTION_CHUNK_OVERLAP_SECONDS,
    GROQ_SELECTION_MAX_INPUT_TOKENS,
    GROQ_SELECTION_MAX_OUTPUT_TOKENS,
    GROQ_SELECTION_MAX_RETRIES,
    GROQ_SELECTION_MIN_DURATION_SECONDS,
    GROQ_SELECTION_MAX_DURATION_SECONDS,
    GROQ_SELECTION_MODEL,
)
from pipeline.validation import GroqCandidateOutput, GroqCandidates, clip_duration_range
from .rate_limit import GroqRateLimiter

logger = logging.getLogger(__name__)

MAX_SELECTION_ATTEMPTS = GROQ_SELECTION_MAX_RETRIES + 1
CHECKPOINT_VERSION = 2

GROQ_CANDIDATE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "window_id": {"type": "string"},
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
                            "hook", "value", "emotional_impact", "clarity",
                            "story_completeness", "quotability", "engagement_potential",
                            "payoff", "context_independence", "clip_efficiency",
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
                            "is_complete_thought", "ends_on_sentence_boundary",
                            "contains_conclusion_or_answer", "has_enough_context",
                            "natural_pause_detected",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required": ["window_id", "start", "end", "reason", "scores", "semantic_metadata"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


class GroqOutputError(ValueError):
    """A bounded, non-retryable structured-output failure."""


class GroqRateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: float, details: dict[str, str] | None = None):
        self.status_code = 429
        self.retry_after_seconds = max(0.001, retry_after_seconds)
        self.details = details or {}
        super().__init__(
            f"Groq rate limit exceeded (HTTP 429; retry after {self.retry_after_seconds:.3f}s)"
        )


class GroqClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        from groq import Groq

        self._api_key = api_key or os.getenv("GROQ_API_KEY")
        self.client = Groq(api_key=self._api_key, timeout=LLM_TIMEOUT_SECONDS)
        self.model = model or GROQ_SELECTION_MODEL
        self.rate_limiter = GroqRateLimiter(model=self.model)

    @staticmethod
    def _estimate_tokens(value: str) -> int:
        return max(1, math.ceil(len(value.encode("utf-8")) / 2))

    @staticmethod
    def _normalize_segments(
        segments: list[dict],
        source_duration: float | None = None,
    ) -> list[dict]:
        normalized = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            try:
                start = float(segment.get("start", segment.get("s")))
                end = float(segment.get("end", segment.get("e")))
            except (TypeError, ValueError):
                continue
            text = " ".join(str(segment.get("text", segment.get("t", ""))).split())
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or end <= start
                or (source_duration is not None and end > source_duration)
                or not text
            ):
                continue
            item = {"s": round(start, 3), "e": round(end, 3), "t": text}
            speaker = segment.get("speaker", segment.get("speaker_id"))
            if speaker is not None:
                item["speaker"] = str(speaker)[:100]
            normalized.append(item)
        normalized.sort(key=lambda item: (item["s"], item["e"]))
        return normalized

    @staticmethod
    def _sentence_groups(segments: list[dict], max_duration: float = 600.0) -> list[list[dict]]:
        groups: list[list[dict]] = []
        current: list[dict] = []
        for item in GroqClient._normalize_segments(segments):
            previous = current[-1] if current else None
            pause = item["s"] - previous["e"] if previous else 0.0
            changed_speaker = bool(
                previous
                and previous.get("speaker") is not None
                and item.get("speaker") is not None
                and previous["speaker"] != item["speaker"]
            )
            if current and (
                item["e"] - current[0]["s"] > max_duration
                or pause >= 1.5
                or changed_speaker
            ):
                groups.append(current)
                current = []
            current.append(item)
            if item["t"].rstrip("\"')]}”’").endswith((".", "!", "?", "…")):
                groups.append(current)
                current = []
        if current:
            groups.append(current)
        return groups

    @classmethod
    def _candidate_windows(cls, segments: list[dict], source_duration: float) -> list[dict]:
        normalized = cls._normalize_segments(segments, source_duration)
        if not normalized or source_duration <= 0:
            return []

        default_minimum, _ = clip_duration_range(source_duration)
        minimum = min(
            source_duration,
            max(GROQ_SELECTION_MIN_DURATION_SECONDS, min(default_minimum, source_duration)),
        )
        maximum = min(source_duration, max(minimum, GROQ_SELECTION_MAX_DURATION_SECONDS))
        _, default_maximum = clip_duration_range(source_duration)
        window_target = min(maximum, max(minimum, min(45.0, default_maximum)))
        sentences = cls._sentence_groups(normalized, max_duration=maximum)
        windows = []
        index = 0
        while index < len(sentences):
            end_index = index
            start_time = sentences[index][0]["s"]
            while end_index + 1 < len(sentences):
                current_end = sentences[end_index][-1]["e"]
                next_sentence = sentences[end_index + 1]
                gap = next_sentence[0]["s"] - current_end
                if current_end - start_time >= minimum and gap >= 1.5:
                    break
                if current_end - start_time >= window_target:
                    break
                next_end = sentences[end_index + 1][-1]["e"]
                if next_end - start_time > maximum and end_index > index:
                    break
                end_index += 1
                if next_end - start_time >= window_target:
                    break

            selected = [
                segment
                for sentence in sentences[index : end_index + 1]
                for segment in sentence
            ]
            if selected:
                window_start = selected[0]["s"]
                window_end = selected[-1]["e"]
                identity = f"{window_start:.3f}:{window_end:.3f}"
                windows.append({
                    "id": f"w{len(windows) + 1:06d}",
                    "identity": identity,
                    "start": window_start,
                    "end": window_end,
                    "segments": selected,
                })

            if end_index >= len(sentences) - 1:
                break
            window_duration = max(0.0, sentences[end_index][-1]["e"] - start_time)
            overlap = min(GROQ_SELECTION_CHUNK_OVERLAP_SECONDS, window_duration * 0.25)
            next_start = sentences[end_index][-1]["e"] - overlap
            next_index = next(
                (candidate for candidate in range(index + 1, end_index + 1)
                 if sentences[candidate][-1]["e"] > next_start),
                end_index + 1,
            )
            index = max(index + 1, next_index)

        unique = []
        seen = set()
        for window in windows:
            if window["identity"] not in seen:
                seen.add(window["identity"])
                unique.append(window)
        return unique

    @staticmethod
    def _header(headers, name: str) -> str | None:
        if headers is None or not hasattr(headers, "items"):
            return None
        return next(
            (str(value).strip() for key, value in headers.items() if str(key).lower() == name.lower()),
            None,
        )

    @classmethod
    def _retry_after(cls, error: Exception) -> float:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None) or getattr(error, "headers", None)
        delays = []
        for name in (
            "retry-after",
            "x-ratelimit-reset-requests",
            "x-ratelimit-reset-tokens",
        ):
            value = cls._header(headers, name)
            if not value:
                continue
            try:
                seconds = float(value)
                if math.isfinite(seconds) and seconds >= 0:
                    delays.append(seconds)
                    continue
            except ValueError:
                pass
            match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value.lower())
            if match:
                factor = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[match.group(2)]
                delays.append(float(match.group(1)) * factor)
                continue
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                delays.append((parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                continue
        return max(1.0, max(delays)) if delays else float(GROQ_429_FALLBACK_SECONDS)

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

    def _messages(self, windows: list[dict]) -> list[dict[str, str]]:
        instructions = (
            "Evaluate each independent, timestamped transcript window. Return JSON only using the supplied schema. "
            "For each window, return zero or one strongest genuinely viable, standalone clip; do not invent clips or "
            "fill a quota. A complete observation, joke, revelation, answer, or explanation may qualify without a "
            "formal conclusion. Reject incomplete thoughts, context-dependent fragments, questions missing necessary "
            "answers, and clips that only have an attention-grabbing opening. Judge hook, informational/entertainment "
            "value, emotional impact, clarity, completeness, quotability, engagement, payoff, context independence, "
            "efficiency, and natural start/end. Every timestamp must exactly match supplied segment boundaries and "
            "remain on the original video timeline. Scores are 0-10. Use concise reasons. Empty candidates are valid."
        )
        payload = {
            "windows": [
                {
                    "id": window["id"],
                    "start": window["start"],
                    "end": window["end"],
                    "segments": window["segments"],
                }
                for window in windows
            ],
        }
        return [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(payload, separators=(",", ":"), ensure_ascii=False)},
        ]

    def _output_budget(self, window_count: int) -> int:
        return min(GROQ_SELECTION_MAX_OUTPUT_TOKENS, max(256, 300 + window_count * 350))

    def _request_token_estimate(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, object],
        output_tokens: int,
    ) -> int:
        request = {
            "model": self.model,
            "messages": messages,
            "response_format": response_format,
            "max_tokens": output_tokens,
        }
        return self._estimate_tokens(json.dumps(request, separators=(",", ":"), ensure_ascii=False)) + output_tokens

    def _batch_windows(self, windows: list[dict]) -> list[list[dict]]:
        batches: list[list[dict]] = []
        current: list[dict] = []
        response_format = self._response_format()
        pending = list(windows)
        bounded_windows = []
        while pending:
            window = pending.pop(0)
            output_budget = self._output_budget(1)
            estimated_tokens = self._request_token_estimate(
                self._messages([window]),
                response_format,
                output_budget,
            )
            if estimated_tokens <= GROQ_SELECTION_MAX_INPUT_TOKENS:
                bounded_windows.append(window)
                continue

            sentences = self._sentence_groups(
                window["segments"],
                max_duration=GROQ_SELECTION_MAX_DURATION_SECONDS,
            )
            if len(sentences) < 2:
                logger.warning(
                    "Groq candidate window skipped because one transcript sentence exceeds the request budget window_id=%s estimated_tokens=%d limit=%d",
                    window["id"],
                    estimated_tokens,
                    GROQ_SELECTION_MAX_INPUT_TOKENS,
                )
                continue

            split_at = len(sentences) // 2
            right_start = sentences[split_at][0]["s"]
            overlap_start = right_start - GROQ_SELECTION_CHUNK_OVERLAP_SECONDS
            right_start_index = split_at
            while (
                right_start_index > 0
                and sentences[right_start_index - 1][-1]["e"] > overlap_start
            ):
                right_start_index -= 1
            if right_start_index == 0:
                right_start_index = split_at

            left_segments = [segment for group in sentences[:split_at] for segment in group]
            right_segments = [segment for group in sentences[right_start_index:] for segment in group]
            left = {
                **window,
                "id": f"{window['id']}.a",
                "identity": f"{left_segments[0]['s']:.3f}:{left_segments[-1]['e']:.3f}",
                "start": left_segments[0]["s"],
                "end": left_segments[-1]["e"],
                "segments": left_segments,
            }
            right = {
                **window,
                "id": f"{window['id']}.b",
                "identity": f"{right_segments[0]['s']:.3f}:{right_segments[-1]['e']:.3f}",
                "start": right_segments[0]["s"],
                "end": right_segments[-1]["e"],
                "segments": right_segments,
            }
            pending[0:0] = [left, right]
            logger.info(
                "splitting oversized transcript window at sentence boundary window_id=%s left_segments=%d right_segments=%d",
                window["id"],
                len(left_segments),
                len(right_segments),
            )

        for window in bounded_windows:
            proposed = current + [window]
            messages = self._messages(proposed)
            output_budget = self._output_budget(len(proposed))
            estimated_tokens = self._request_token_estimate(messages, response_format, output_budget)
            if current and (
                len(proposed) > GROQ_SELECTION_BATCH_SIZE
                or estimated_tokens > GROQ_SELECTION_MAX_INPUT_TOKENS
            ):
                batches.append(current)
                current = [window]
            else:
                current = proposed

            if not current:
                continue
            single_messages = self._messages(current)
            single_output_budget = self._output_budget(len(current))
            single_estimate = self._request_token_estimate(
                single_messages,
                response_format,
                single_output_budget,
            )
            if len(current) == 1 and single_estimate > GROQ_SELECTION_MAX_INPUT_TOKENS:
                logger.warning(
                    "Groq candidate window skipped because its bounded request is too large window_id=%s estimated_tokens=%d limit=%d",
                    window["id"],
                    single_estimate,
                    GROQ_SELECTION_MAX_INPUT_TOKENS,
                )
                current = []
        if current:
            batches.append(current)
        return batches

    def _batch_key(self, windows: list[dict], source_duration: float) -> str:
        payload = {
            "version": CHECKPOINT_VERSION,
            "model": self.model,
            "source_duration": round(source_duration, 3),
            "windows": [
                {
                    "identity": window["identity"],
                    "segments": window["segments"],
                }
                for window in windows
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _validate_grounding(
        candidates: list[GroqCandidateOutput],
        windows: list[dict],
        source_duration: float,
    ) -> None:
        by_id = {window["id"]: window for window in windows}
        seen_window_ids = set()
        for candidate in candidates:
            window = by_id.get(candidate.window_id)
            if window is None:
                raise ValueError("candidate referenced an unknown transcript window")
            if candidate.window_id in seen_window_ids:
                raise ValueError("response returned multiple candidates for one transcript window")
            seen_window_ids.add(candidate.window_id)
            valid_starts = {segment["s"] for segment in window["segments"]}
            valid_ends = {segment["e"] for segment in window["segments"]}
            if (
                candidate.start not in valid_starts
                or candidate.end not in valid_ends
                or candidate.start < window["start"]
                or candidate.end > window["end"]
                or candidate.end > source_duration
                or candidate.end <= candidate.start
            ):
                raise ValueError("candidate timestamps are not grounded in the supplied original timeline")

    def _invoke(
        self,
        windows: list[dict],
        source_duration: float,
        batch_index: int,
    ) -> GroqCandidates:
        messages = self._messages(windows)
        response_format = self._response_format()
        output_budget = self._output_budget(len(windows))
        estimated_tokens = self._request_token_estimate(messages, response_format, output_budget)
        if estimated_tokens > GROQ_SELECTION_MAX_INPUT_TOKENS:
            raise GroqOutputError("Groq selection request has an invalid configured token budget")

        limiter = getattr(self, "rate_limiter", None)
        if limiter is not None:
            wait_seconds = limiter.acquire(estimated_tokens)
            if wait_seconds > 0:
                raise GroqRateLimitError(wait_seconds, {"shared_limiter_wait_seconds": f"{wait_seconds:.3f}"})

        for attempt in range(MAX_SELECTION_ATTEMPTS):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    max_tokens=output_budget,
                    response_format=response_format,
                    messages=messages,
                )
            except Exception as error:
                status = getattr(error, "status_code", None)
                if status == 429:
                    delay = self._retry_after(error)
                    if limiter is not None:
                        limiter.defer_all_workers(delay)
                    logger.warning(
                        "Groq rate limit response batch=%d retry_after_seconds=%.2f estimated_tokens=%d",
                        batch_index,
                        delay,
                        estimated_tokens,
                    )
                    raise GroqRateLimitError(delay) from None
                if status == 413:
                    raise GroqOutputError("Groq rejected an invalid oversized request (HTTP 413)") from error

                transient = (
                    status in {408, 409, 425}
                    or (isinstance(status, int) and status >= 500)
                    or isinstance(error, (TimeoutError, ConnectionError))
                    or type(error).__name__ in {"APIConnectionError", "APITimeoutError"}
                )
                logger.warning(
                    "Groq selection request failed batch=%d attempt=%d status=%s transient=%s estimated_tokens=%d",
                    batch_index,
                    attempt + 1,
                    status or "unknown",
                    transient,
                    estimated_tokens,
                )
                body = getattr(error, "body", None)
                details = body.get("error", body) if isinstance(body, dict) else {}
                provider_code = details.get("code") if isinstance(details, dict) else None
                if (
                    status == 400
                    and provider_code == "json_validate_failed"
                    and response_format.get("type") == "json_schema"
                    and attempt + 1 < MAX_SELECTION_ATTEMPTS
                ):
                    response_format = {"type": "json_object"}
                    continue
                if status == 400 and provider_code == "json_validate_failed":
                    raise GroqOutputError(
                        "Groq rejected the structured selection response format"
                    ) from error
                if transient and attempt + 1 < MAX_SELECTION_ATTEMPTS:
                    time.sleep(min(0.5 * (2**attempt), 4.0) + random.uniform(0.0, 0.25))
                    continue
                raise

            try:
                content = response.choices[0].message.content or ""
                parsed = GroqCandidates.model_validate_json(content)
                self._validate_grounding(parsed.candidates, windows, source_duration)
                return parsed
            except (AttributeError, IndexError, TypeError, ValueError) as error:
                logger.warning(
                    "Groq structured output validation failed batch=%d attempt=%d code=malformed_or_ungrounded_output",
                    batch_index,
                    attempt + 1,
                )
                if attempt + 1 >= MAX_SELECTION_ATTEMPTS:
                    raise GroqOutputError(
                        f"Groq returned malformed or ungrounded structured output after {attempt + 1} attempts"
                    ) from error
                time.sleep(min(0.25 * (2**attempt), 2.0) + random.uniform(0.0, 0.15))
        raise AssertionError("bounded Groq attempt loop completed unexpectedly")

    def evaluate_transcript(
        self,
        transcript: str,
        segments: list[dict],
        source_duration: float,
        *,
        job_id: str | None = None,
        cached_chunks: dict[str, list[dict]] | None = None,
        on_chunk_complete: Callable[[dict[str, list[dict]], int, int], None] | None = None,
    ) -> GroqCandidates:
        started = time.monotonic()
        if not math.isfinite(source_duration) or source_duration <= 0:
            raise ValueError("source video duration must be a finite positive number")
        valid_segments = self._normalize_segments(segments, source_duration)
        windows = self._candidate_windows(valid_segments, source_duration)
        batches = self._batch_windows(windows)
        logger.info(
            "Groq clip selection started job_id=%s source_duration_seconds=%.3f transcript_segments=%d transcript_windows=%d batches=%d",
            job_id or "unknown",
            source_duration,
            len(valid_segments),
            len(windows),
            len(batches),
        )
        if not batches:
            logger.info(
                "Groq clip selection completed job_id=%s viable_candidates=0 reason=no_timestamped_candidate_windows duration_seconds=%.3f",
                job_id or "unknown",
                time.monotonic() - started,
            )
            return GroqCandidates(candidates=[])

        successful_batches = {
            key: value
            for key, value in (cached_chunks or {}).items()
            if isinstance(key, str) and isinstance(value, list)
        }
        candidates: list[GroqCandidateOutput] = []
        successful_count = 0
        failed_count = 0

        def process_batch(batch: list[dict], batch_index: int) -> list[GroqCandidateOutput]:
            nonlocal successful_count, failed_count
            key = self._batch_key(batch, source_duration)
            if key in successful_batches:
                cached = GroqCandidates.model_validate({"candidates": successful_batches[key]})
                self._validate_grounding(cached.candidates, batch, source_duration)
                successful_count += 1
                return cached.candidates
            if len(batch) > 1:
                midpoint = len(batch) // 2
                left_key = self._batch_key(batch[:midpoint], source_duration)
                right_key = self._batch_key(batch[midpoint:], source_duration)
                if left_key in successful_batches and right_key in successful_batches:
                    return (
                        process_batch(batch[:midpoint], batch_index)
                        + process_batch(batch[midpoint:], batch_index)
                    )

            try:
                parsed = self._invoke(batch, source_duration, batch_index)
            except GroqOutputError as error:
                if "oversized request" in str(error).lower() and len(batch) > 1:
                    midpoint = len(batch) // 2
                    logger.warning(
                        "splitting oversized Groq selection batch job_id=%s batch=%d windows=%d",
                        job_id or "unknown",
                        batch_index,
                        len(batch),
                    )
                    return (
                        process_batch(batch[:midpoint], batch_index)
                        + process_batch(batch[midpoint:], batch_index)
                    )
                failed_count += 1
                raise

            serialized_candidates = [item.model_dump(mode="json") for item in parsed.candidates]
            successful_batches[key] = serialized_candidates
            successful_count += 1
            if on_chunk_complete is not None:
                on_chunk_complete(dict(successful_batches), successful_count, len(batches))
            logger.info(
                "Groq selection batch completed job_id=%s batch=%d windows=%d candidates=%d estimated_tokens=%d",
                job_id or "unknown",
                batch_index,
                len(batch),
                len(parsed.candidates),
                self._request_token_estimate(
                    self._messages(batch),
                    self._response_format(),
                    self._output_budget(len(batch)),
                ),
            )
            return parsed.candidates

        try:
            for batch_index, batch in enumerate(batches, 1):
                candidates.extend(process_batch(batch, batch_index))
        except Exception:
            logger.error(
                "Groq clip selection batch failed job_id=%s successful_batches=%d failed_batches=%d total_batches=%d",
                job_id or "unknown",
                successful_count,
                failed_count + 1,
                len(batches),
            )
            raise

        logger.info(
            "Groq clip selection completed job_id=%s transcript_segments=%d transcript_windows=%d batches=%d successful_batches=%d failed_batches=%d proposed_candidates=%d duration_seconds=%.3f",
            job_id or "unknown",
            len(valid_segments),
            len(windows),
            len(batches),
            successful_count,
            failed_count,
            len(candidates),
            time.monotonic() - started,
        )
        return GroqCandidates(candidates=candidates)
