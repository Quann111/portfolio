"""Audio mixer for voice, background score, and foley.

Features:
- Compresses voice with sfx.compress.
- Smoothly ducks background music during speech.
- Applies deep music drop (~1.5s) at pause markers.
- Balances voice RMS ~10 dB above music during speech segments.
- Peak limited to < -1 dBFS (no clipping, no loudnorm; mux.py handles final loudnorm).
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
from scipy.ndimage import uniform_filter1d
import soundfile as sf

# Setup import path for vendor sfx
_VENDOR_AUDIO = Path(__file__).resolve().parents[3] / "vendor" / "opuscar" / "core" / "audio"
if str(_VENDOR_AUDIO) not in sys.path and _VENDOR_AUDIO.is_dir():
    sys.path.insert(0, str(_VENDOR_AUDIO))

import sfx  # noqa: E402


def _resample_if_needed(data: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample audio array if sample rates differ."""
    if orig_sr == target_sr:
        return data
    from scipy.signal import resample
    num_samples = int(round(len(data) * float(target_sr) / orig_sr))
    return resample(data, num_samples).astype(data.dtype)


def mix(
    voice_wav: Optional[Union[str, os.PathLike]],
    score_wav: Union[str, os.PathLike],
    foley_wav: Union[str, os.PathLike],
    out_wav: Union[str, os.PathLike],
    pauses: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Mix score, foley, and optional voice into a balanced soundtrack.

    Args:
        voice_wav: path to voice WAV file, or None if instrumental.
        score_wav: path to score WAV file.
        foley_wav: path to foley WAV file.
        out_wav: path to output WAV file.
        pauses: list of timestamps (seconds) where music drops deeply (~1.5s).

    Returns:
        Dict with measurements: dur, peak_dbfs, rms_voice_db, rms_music_db, rms_diff_db.
    """
    score_wav = str(score_wav)
    foley_wav = str(foley_wav)
    out_wav = str(out_wav)
    if pauses is None:
        pauses = []

    # 1. Load score (defines the master length)
    score, sr = sf.read(score_wav, dtype='float32')
    if score.ndim == 1:
        score = np.column_stack([score, score])
    n_samples = len(score)

    # 2. Load foley
    foley, sr_f = sf.read(foley_wav, dtype='float32')
    if sr_f != sr:
        foley = _resample_if_needed(foley, sr_f, sr)
    if foley.ndim == 1:
        foley = np.column_stack([foley, foley])

    # Align foley length to score
    if len(foley) < n_samples:
        foley = np.pad(foley, ((0, n_samples - len(foley)), (0, 0)))
    elif len(foley) > n_samples:
        foley = foley[:n_samples]

    # 3. Build pause attenuation envelope
    pause_gain = np.ones(n_samples, dtype=np.float32)
    for p in pauses:
        s_start = int(round(p * sr))
        s_end = min(n_samples, int(round((p + 1.5) * sr)))
        if s_start < n_samples and s_end > s_start:
            p_len = s_end - s_start
            fade_len = min(int(round(0.12 * sr)), p_len // 4)
            # Create smooth dip down to 0.03 (-30 dB)
            dip = np.full(p_len, 0.03, dtype=np.float32)
            if fade_len > 0:
                fade_down = 0.03 + 0.97 * (0.5 * (1.0 + np.cos(np.pi * np.arange(fade_len) / fade_len)))
                fade_up = 0.03 + 0.97 * (0.5 * (1.0 - np.cos(np.pi * np.arange(fade_len) / fade_len)))
                dip[:fade_len] = fade_down
                dip[-fade_len:] = fade_up
            pause_gain[s_start:s_end] = np.minimum(pause_gain[s_start:s_end], dip)

    # 4. Handle Voice (compression + ducking + 10 dB RMS balance)
    voice_active_mask = np.zeros(n_samples, dtype=bool)
    has_voice = voice_wav is not None and os.path.isfile(str(voice_wav))
    voice_comp = None

    if has_voice:
        voice, sr_v = sf.read(str(voice_wav), dtype='float32')
        if sr_v != sr:
            voice = _resample_if_needed(voice, sr_v, sr)
        if voice.ndim > 1:
            voice = np.mean(voice, axis=1)

        # Pad or trim voice
        if len(voice) < n_samples:
            voice = np.pad(voice, (0, n_samples - len(voice)))
        elif len(voice) > n_samples:
            voice = voice[:n_samples]

        # Compress voice with sfx.compress
        voice_comp = sfx.compress(voice, thr=0.25, ratio=3.5, att=0.004, rel=0.08)

        # Detect active voice regions
        win = int(round(0.04 * sr))  # 40ms smoothing
        env_voice = uniform_filter1d(np.abs(voice_comp), size=win)
        voice_active_mask = env_voice > 0.012

    # Ducking envelope
    duck_gain = np.ones(n_samples, dtype=np.float32)
    if has_voice and np.any(voice_active_mask):
        att_samples = int(0.04 * sr)
        rel_samples = int(0.28 * sr)
        att_coeff = math.exp(-1.0 / max(1, att_samples))
        rel_coeff = math.exp(-1.0 / max(1, rel_samples))
        target_duck = 0.32  # ~ -10 dB ducking during speech
        g = 1.0
        for i in range(n_samples):
            tgt = target_duck if voice_active_mask[i] else 1.0
            if tgt < g:
                g = att_coeff * g + (1.0 - att_coeff) * tgt
            else:
                g = rel_coeff * g + (1.0 - rel_coeff) * tgt
            duck_gain[i] = g

    # Combined music gain: ducking * pauses
    music_gain = duck_gain * pause_gain
    ducked_score = score * music_gain[:, None]

    # Measure RMS and balance: Voice ≈ +10 dB above music in active regions
    rms_v_db = None
    rms_m_db = None
    rms_diff_db = None

    if has_voice and np.any(voice_active_mask) and voice_comp is not None:
        active_idx = np.where(voice_active_mask)[0]
        v_act = voice_comp[active_idx]
        m_act = np.mean(np.abs(ducked_score[active_idx]), axis=1)

        rms_v = float(np.sqrt(np.mean(v_act ** 2)))
        rms_m = float(np.sqrt(np.mean(m_act ** 2)))

        if rms_v > 1e-9 and rms_m > 1e-9:
            current_diff_db = 20.0 * math.log10(rms_v / rms_m)
            target_diff_db = 10.0
            scale = 10.0 ** ((target_diff_db - current_diff_db) / 20.0)
            voice_comp = voice_comp * scale

            rms_v_scaled = float(np.sqrt(np.mean((voice_comp[active_idx]) ** 2)))
            rms_v_db = 20.0 * math.log10(rms_v_scaled)
            rms_m_db = 20.0 * math.log10(rms_m)
            rms_diff_db = rms_v_db - rms_m_db

        voice_stereo = np.column_stack([voice_comp, voice_comp])
        mixed = ducked_score + 0.85 * foley + voice_stereo
    else:
        mixed = ducked_score + 0.85 * foley

    # 5. Peak limiting: strictly < -1 dBFS (target -1.2 dBFS = ~0.871)
    peak = float(np.max(np.abs(mixed)))
    ceiling = 10.0 ** (-1.2 / 20.0)  # ~0.87096 (-1.2 dBFS)

    if peak > ceiling:
        mixed = mixed * (ceiling / peak)
        peak = float(np.max(np.abs(mixed)))

    peak_dbfs = 20.0 * math.log10(max(1e-9, peak))

    # Save output
    os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
    sf.write(out_wav, mixed.astype(np.float32), sr, subtype='PCM_16')

    return {
        "out_wav": out_wav,
        "dur": n_samples / sr,
        "peak_dbfs": peak_dbfs,
        "rms_voice_db": rms_v_db,
        "rms_music_db": rms_m_db,
        "rms_diff_db": rms_diff_db,
    }
