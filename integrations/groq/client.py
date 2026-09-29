from __future__ import annotations

import json
import os

from pipeline.validation import GroqCandidates


class GroqClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        from groq import Groq

        self.client = Groq(api_key=api_key or os.getenv("GROQ_API_KEY"))
        self.model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    @staticmethod
    def _compact_segments(segments: list[dict], limit: int = 16000) -> list[dict]:
        compact: list[dict] = []
        used = 0
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
            if compact and used + encoded_size > limit:
                break
            if not compact and encoded_size > limit:
                item["t"] = text[: max(20, limit - 80)]
                encoded_size = len(json.dumps(item, separators=(",", ":"))) + 1
            compact.append(item)
            used += encoded_size
        return compact

    def evaluate_transcript(self, transcript: str, segments: list[dict], source_duration: float) -> GroqCandidates:
        compact_segments = self._compact_segments(segments, limit=14000)
        score_fields = "hook,value,emotional_impact,clarity,story_completeness,quotability,engagement_potential,payoff,context_independence,clip_efficiency"
        if source_duration <= 15:
            target_window = "short videos: aim for about 6-12 seconds"
        elif source_duration <= 60:
            target_window = "medium videos: aim for about 12-22 seconds"
        elif source_duration <= 180:
            target_window = "longer videos: aim for about 18-35 seconds"
        else:
            target_window = "very long videos: aim for about 22-45 seconds"

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=2200,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": f"Return valid JSON only. Select no more than 3 self-contained clips. The source duration is {source_duration:.2f} seconds; every start/end must be within 0 and {source_duration:.2f}, and end must be greater than start. Use this exact shape: {{\"candidates\":[{{\"start\":0,\"end\":1,\"reason\":\"brief\",\"scores\":{{{score_fields}}},\"semantic_metadata\":{{\"is_complete_thought\":true,\"ends_on_sentence_boundary\":true,\"contains_conclusion_or_answer\":true,\"has_enough_context\":true,\"natural_pause_detected\":true}}}}]}}. Every score is a number from 0 to 10. Do not use arrays for scores. Keep each reason under 12 words. Use only supplied timestamps. {target_window}. Clip length should scale with source runtime: short videos get shorter clips, longer videos get longer clips. Never end a clip in the middle of a sentence or thought; if the sentence continues, extend the end time to finish the current sentence before closing the clip. Prefer natural sentence boundaries and complete thoughts over hard time limits. Include semantic_metadata for every candidate and mark whether it is a complete thought with a natural ending."},
                {"role": "user", "content": json.dumps({"source_duration": round(source_duration, 2), "timestamped_segments": compact_segments}, separators=(",", ":"))},
            ],
        )
        content = response.choices[0].message.content or ""
        if not content.strip():
            raise ValueError("Groq returned an empty JSON response")
        try:
            return GroqCandidates.model_validate_json(content)
        except Exception as error:
            raise ValueError(f"Groq returned invalid candidate JSON: {error}") from error
