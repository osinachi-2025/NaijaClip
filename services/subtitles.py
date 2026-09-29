from __future__ import annotations

import logging
import math
import re
from typing import Any


logger = logging.getLogger(__name__)
SUBTITLE_MAX_CHARS = 40
SUBTITLE_MAX_WIDTH_EM = 18.0
SUBTITLE_FORCE_STYLE = (
    "Alignment=2,Fontname=DejaVu Sans,Fontsize=7,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
    "BorderStyle=1,Outline=1,Shadow=0,MarginL=14,MarginR=14,MarginV=55,WrapStyle=2"
)


def subtitle_width_em(text: str) -> float:
    width = 0.0
    narrow = "ilI.,'`:;!|()[]{}"
    wide = "MW@%&"
    for character in text:
        if character.isspace():
            width += 0.34
        elif character in narrow:
            width += 0.32
        elif character in wide:
            width += 0.9
        elif character.isupper() or character.isdigit():
            width += 0.65
        else:
            width += 0.55
    return width


def _is_phrase_boundary(text: str) -> bool:
    return text.rstrip("\"'”’)]}").endswith((",", ";", ":", "—", "–"))


def _is_sentence_boundary(text: str) -> bool:
    return text.rstrip("\"'”’)]}").endswith((".", "?", "!"))


def build_subtitle_chunks(
    segments: list[dict[str, Any]],
    *,
    clip_start: float = 0.0,
    clip_end: float = float("inf"),
    max_chars: int = SUBTITLE_MAX_CHARS,
    max_width_em: float = SUBTITLE_MAX_WIDTH_EM,
) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        tokens = re.findall(r"\S+", text)
        if not tokens:
            continue
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping subtitle segment without valid timestamps: %r", segment)
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            logger.warning("Skipping subtitle segment with invalid time range: %r", segment)
            continue

        weights = [max(len(token), 1) for token in tokens]
        total_weight = sum(weights)
        elapsed_weight = 0
        duration = end - start
        for token, weight in zip(tokens, weights):
            token_start = start + duration * elapsed_weight / total_weight
            elapsed_weight += weight
            token_end = start + duration * elapsed_weight / total_weight
            token_start = max(token_start, clip_start)
            token_end = min(token_end, clip_end)
            if token_end > token_start:
                words.append({"text": token, "start": token_start, "end": token_end})

    words.sort(key=lambda word: (word["start"], word["end"]))
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    previous_end = clip_start

    def emit(chunk_words: list[dict[str, Any]]) -> None:
        nonlocal previous_end
        if not chunk_words:
            return
        text = " ".join(word["text"] for word in chunk_words)
        start = max(float(chunk_words[0]["start"]), previous_end)
        end = min(clip_end, max(float(chunk_words[-1]["end"]), start + 0.04))
        if end <= start:
            logger.warning("Skipping subtitle chunk with no clip-relative duration: %r", text)
            return
        if len(text) > max_chars or subtitle_width_em(text) > max_width_em:
            logger.warning("Keeping long subtitle word intact: %r", text)
        chunks.append({"text": text, "start": start, "end": end})
        previous_end = end

    def fits(candidate: list[dict[str, Any]]) -> bool:
        text = " ".join(word["text"] for word in candidate)
        return len(text) <= max_chars and subtitle_width_em(text) <= max_width_em

    for word in words:
        if current and _is_sentence_boundary(current[-1]["text"]):
            emit(current)
            current = []
        if current and not fits([*current, word]):
            phrase_breaks = [index + 1 for index, part in enumerate(current) if _is_phrase_boundary(part["text"])]
            if phrase_breaks:
                split_at = phrase_breaks[-1]
                emit(current[:split_at])
                current = current[split_at:]
            else:
                emit(current)
                current = []
            if current and not fits([*current, word]):
                emit(current)
                current = []
        current.append(word)
        if _is_sentence_boundary(word["text"]):
            emit(current)
            current = []
    emit(current)
    return chunks


def build_srt(cues: list[dict[str, Any]]) -> str:
    blocks = []
    for index, cue in enumerate(cues, 1):
        text = str(cue["text"]).replace("\r", " ").replace("\n", " ").strip()
        width = max(subtitle_width_em(text), 1.0)
        horizontal_scale = max(25, min(100, int(SUBTITLE_MAX_WIDTH_EM / width * 100)))
        blocks.append(
            f"{index}\n{_srt_time(cue['start'])} --> {_srt_time(cue['end'])}\n"
            f"{{\\fscx{horizontal_scale}}}{text}\n"
        )
    return "\n".join(blocks)


def _srt_time(seconds: float) -> str:
    milliseconds = int(max(0, seconds) * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"