"""Small shared data structures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Segment:
    start: float
    end: float
    text: str
