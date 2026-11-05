"""Audio synthesis, foley, and mixing package for opuscar.

Exports:
    make_score: original soundtrack according to timeline sections and moods
    make_foley: sound effects aligned to events
    mix: voice + score + foley mix with ducking, compression and pause handling
"""
from __future__ import annotations

from .score import make_score
from .foley import make_foley
from .mix import mix

__all__ = ["make_score", "make_foley", "mix"]
