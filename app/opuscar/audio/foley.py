"""Foley generator placing procedural sound effects aligned with visual events.

Uses autovideo/vendor/opuscar/core/audio/sfx.py directly without modifying vendor.
Handles standard opuscar event cues: whoosh, tick, paper, pop, and others.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Union

import numpy as np
import soundfile as sf

# Setup import path for vendor sfx
_VENDOR_AUDIO = Path(__file__).resolve().parents[3] / "vendor" / "opuscar" / "core" / "audio"
if str(_VENDOR_AUDIO) not in sys.path and _VENDOR_AUDIO.is_dir():
    sys.path.insert(0, str(_VENDOR_AUDIO))

import sfx  # noqa: E402


def _synth_paper(d: float = 0.12, v: float = 0.35) -> np.ndarray:
    """Synthesize a crisp, soft paper rustle/turn using sfx primitives."""
    n = sfx.noise(d)
    filt = sfx.bp(n, 1200, 5500)
    tt = sfx.t_(d)
    env = np.sin(np.pi * tt / d) ** 1.8
    return sfx.norm(filt * env) * v


def _synth_tick(v: float = 0.35) -> np.ndarray:
    """Synthesize a subtle clock/counter tick."""
    return sfx.click(pitch=2.2, v=v)


def make_foley(
    events: Union[Dict[str, Any], str, os.PathLike],
    out_wav: Union[str, os.PathLike],
    sr: int = 48000,
) -> str:
    """Generate stereo foley track matching visual events.

    Args:
        events: dict containing "dur" (or "DUR") and "ev" (or "EV") list, or path to events.json.
        out_wav: path to output WAV file.
        sr: sample rate (default 48000).

    Returns:
        Path to generated wav file.
    """
    if isinstance(events, (str, os.PathLike)):
        with open(events, "r", encoding="utf-8") as f:
            ev_data = json.load(f)
    else:
        ev_data = events

    dur = float(ev_data.get("DUR", ev_data.get("dur", 0.0)))
    ev_list = ev_data.get("EV", ev_data.get("ev", []))

    if dur <= 0.0 and ev_list:
        dur = max(float(e.get("t", 0.0)) for e in ev_list) + 1.0
    if dur <= 0.0:
        dur = 10.0

    out_wav = str(out_wav)
    os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)

    n_samples = int(round(dur * sr))
    buf = np.zeros((n_samples, 2), dtype=np.float32)

    for i, e in enumerate(ev_list):
        t = float(e.get("t", 0.0))
        ev_type = str(e.get("type", "")).strip().lower()
        if not ev_type:
            continue

        # Slight pan variation based on event index or ID
        pan = ((i % 5) - 2) * 0.08

        snd = None
        if ev_type == "whoosh":
            snd_d = float(e.get("d", 0.35))
            snd = sfx.whoosh(d=snd_d, v=0.70)
        elif ev_type == "pop":
            snd = sfx.pop(v=0.50)
        elif ev_type == "tick":
            snd = _synth_tick(v=0.35)
        elif ev_type == "paper":
            snd = _synth_paper(d=0.10, v=0.32)
        elif hasattr(sfx, ev_type) and callable(getattr(sfx, ev_type)):
            # Direct match in sfx (e.g. click, clack, thump, ding, crash, rumble, etc.)
            fn = getattr(sfx, ev_type)
            try:
                snd = fn(v=0.5)
            except TypeError:
                snd = fn()
        else:
            # Fallback subtle click
            snd = sfx.click(pitch=1.8, v=0.30)

        if snd is not None and len(snd) > 0:
            sfx.add(buf, snd, at=t, gain=1.0, pan=pan)

    # Soft limiting on foley to ensure no individual spikes cause distortion
    pk = float(np.max(np.abs(buf)))
    if pk > 0.85:
        buf[:, 0] = sfx.limit(buf[:, 0], ceil=0.85)
        buf[:, 1] = sfx.limit(buf[:, 1], ceil=0.85)

    sf.write(out_wav, buf, sr, subtype='PCM_16')
    return out_wav
