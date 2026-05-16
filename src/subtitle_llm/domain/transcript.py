from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Word:
    word: str
    start: float
    end: float
    score: float


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word]


@dataclass
class Transcript:
    segments: list[Segment]
