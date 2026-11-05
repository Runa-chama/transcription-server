"""Subtitle / text / JSON exporters."""

from __future__ import annotations

import json
from pathlib import Path

from .models import Segment


def _split_ms(seconds: float) -> tuple[int, int, int, int]:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, total_ms = divmod(total_ms, 3_600_000)
    minutes, total_ms = divmod(total_ms, 60_000)
    secs, millis = divmod(total_ms, 1000)
    return hours, minutes, secs, millis


def srt_timestamp(seconds: float) -> str:
    hours, minutes, secs, millis = _split_ms(seconds)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def vtt_timestamp(seconds: float) -> str:
    hours, minutes, secs, millis = _split_ms(seconds)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def clock_timestamp(seconds: float) -> str:
    hours, minutes, secs, _ = _split_ms(seconds)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def to_srt(segments: list[Segment]) -> str:
    blocks = []
    index = 0
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        index += 1
        blocks.append(
            f"{index}\n"
            f"{srt_timestamp(segment.start)} --> {srt_timestamp(segment.end)}\n"
            f"{text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_vtt(segments: list[Segment]) -> str:
    blocks = ["WEBVTT"]
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        blocks.append(
            f"{vtt_timestamp(segment.start)} --> {vtt_timestamp(segment.end)}\n{text}"
        )
    return "\n\n".join(blocks) + "\n"


def to_txt(segments: list[Segment], timestamps: bool = False) -> str:
    lines = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if timestamps:
            lines.append(f"[{clock_timestamp(segment.start)}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines) + ("\n" if lines else "")


def to_json(result: dict, segments: list[Segment]) -> str:
    payload = dict(result)
    payload["segments"] = [
        {"start": round(segment.start, 3), "end": round(segment.end, 3), "text": segment.text}
        for segment in segments
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_exports(
    segments: list[Segment],
    result: dict,
    formats: list[str] | tuple[str, ...],
    out_dir: Path,
    stem: str,
    txt_timestamps: bool = False,
) -> list[str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    writers = {
        "srt": lambda: to_srt(segments),
        "vtt": lambda: to_vtt(segments),
        "txt": lambda: to_txt(segments, txt_timestamps),
        "json": lambda: to_json(result, segments),
    }
    for fmt in formats:
        writer = writers.get(fmt)
        if writer is None:
            continue
        path = out_dir / f"{stem}.{fmt}"
        path.write_text(writer(), encoding="utf-8", newline="\n")
        written.append(fmt)
    return written
