from dataclasses import dataclass
from typing import List


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
    words: List[Word]


@dataclass
class Transcript:
    segments: List[Segment]
