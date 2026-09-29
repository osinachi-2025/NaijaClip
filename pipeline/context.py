from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PipelineContext:
    job: Any
    video: Any = None
    source_path: Path | None = None
    media_info: dict[str, Any] = field(default_factory=dict)
    audio_path: Path | None = None
    transcript: str = ""
    transcript_segments: list[dict[str, Any]] = field(default_factory=list)
    groq_candidates: list[Any] = field(default_factory=list)
    scored_candidates: list[Any] = field(default_factory=list)
    selected_clips: list[Any] = field(default_factory=list)
    extracted_clips: list[Path] = field(default_factory=list)
    detections: list[Any] = field(default_factory=list)
    tracking_data: list[Any] = field(default_factory=list)
    reframing_data: list[Any] = field(default_factory=list)
    captions: list[Any] = field(default_factory=list)
    rendered_outputs: list[Path] = field(default_factory=list)
    thumbnails: list[Path] = field(default_factory=list)
    final_exports: list[dict[str, Any]] = field(default_factory=list)
    temp_dir: Path | None = None
