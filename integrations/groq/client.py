from __future__ import annotations

import json
import math
import os

from core.config import MAX_CLIPS_PER_VIDEO
from pipeline.validation import GroqCandidates

MAX_PROMPT_CHARS = 14000
MAX_TRANSCRIPT_CHUNKS = 64
MAX_CANDIDATE_TARGET = 12
MAX_CANDIDATES_PER_CHUNK = MAX_CANDIDATE_TARGET


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
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=min(6000, max(2200, 1800 + candidates_per_chunk * 450)),
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": f"Return valid JSON only. Select no more than {candidates_per_chunk} self-contained clips from transcript window {chunk_index} ({window_start:.2f}-{window_end:.2f}s). The full source duration is {source_duration:.2f} seconds; every start/end must be within 0 and {source_duration:.2f}, and candidates must come from the supplied window. Use this exact shape: {{\"candidates\":[{{\"start\":0,\"end\":1,\"reason\":\"brief\",\"scores\":{{{score_fields}}},\"semantic_metadata\":{{\"is_complete_thought\":true,\"ends_on_sentence_boundary\":true,\"contains_conclusion_or_answer\":true,\"has_enough_context\":true,\"natural_pause_detected\":true}}}}]}}. Every score is a number from 0 to 10. Do not use arrays for scores. Keep each reason under 12 words. Use only supplied timestamps. {target_window}. Clip length should scale with source runtime: short videos get shorter clips, longer videos get longer clips. Never end a clip in the middle of a sentence or thought; if the sentence continues, extend the end time to finish the current sentence before closing the clip. Prefer natural sentence boundaries and complete thoughts over hard time limits. Include semantic_metadata for every candidate. Prefer distinct moments with a strong hook, complete thought, context, and payoff; do not split one moment into repetitions."},
                    {"role": "user", "content": json.dumps({"source_duration": round(source_duration, 2), "timestamped_segments": compact_segments}, separators=(",", ":"))},
                ],
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                raise ValueError("Groq returned an empty JSON response")
            try:
                aggregated.extend(GroqCandidates.model_validate_json(content).candidates)
            except Exception as error:
                raise ValueError(f"Groq returned invalid candidate JSON: {error}") from error
        return GroqCandidates(candidates=aggregated)
