from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .validation import SCORE_FIELDS, ValidatedCandidate

SEMANTIC_WEIGHTS: Mapping[str, float] = {
    "hook": 0.15,
    "value": 0.15,
    "emotional_impact": 0.10,
    "clarity": 0.10,
    "story_completeness": 0.15,
    "quotability": 0.05,
    "engagement_potential": 0.15,
    "payoff": 0.10,
    "context_independence": 0.03,
    "clip_efficiency": 0.02,
}
SEMANTIC_WEIGHT = 0.75
TECHNICAL_WEIGHT = 0.25


@dataclass
class ScoredCandidate:
    candidate: ValidatedCandidate
    semantic_score: float
    technical_score: float
    final_score: float
    clip: object | None = None
    extracted_path: object | None = None
    detections: object | None = None
    tracking: object | None = None
    reframe: object | None = None
    caption_path: object | None = None
    rendered_path: object | None = None
    thumbnail_path: object | None = None


def semantic_score(scores) -> float:
    return sum(getattr(scores, field) * weight for field, weight in SEMANTIC_WEIGHTS.items())


def technical_score(*, duration: float, speech_density: float | None = None, boundary_quality: float | None = None, confidence: float | None = None) -> float:
    signals = [value for value in (speech_density, boundary_quality, confidence) if value is not None]
    efficiency = max(0.0, min(10.0, 10.0 - abs(duration - 45.0) / 9.0))
    signals.append(efficiency)
    return sum(max(0.0, min(10.0, value)) for value in signals) / len(signals)


def score_candidate(item: ValidatedCandidate, **technical_signals) -> ScoredCandidate:
    semantic = semantic_score(item.candidate.scores)
    technical = technical_score(duration=item.duration, **technical_signals)
    return ScoredCandidate(item, semantic, technical, semantic * SEMANTIC_WEIGHT + technical * TECHNICAL_WEIGHT)


def rank_candidates(candidates: list[ValidatedCandidate], **technical_signals) -> list[ScoredCandidate]:
    return sorted((score_candidate(item, **technical_signals) for item in candidates), key=lambda item: item.final_score, reverse=True)
