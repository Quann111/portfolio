"""CLI and build pipeline for assembling opuscar soundtrack.

Usage:
    python -m app.opuscar.audio.build <events.json> <timeline.json> <out_mix.wav> [--voice voice.wav]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import soundfile as sf

from .foley import make_foley
from .mix import mix
from .score import make_score, measure_spectral_balance


def build_audio(
    events_path: str,
    timeline_path: str,
    out_mix_path: str,
    voice_path: str | None = None,
    pauses: list[float] | None = None,
    palette: str = "dark-keynote",
    sr: int = 48000,
) -> dict:
    """Run full score -> foley -> mix pipeline."""
    with open(timeline_path, "r", encoding="utf-8") as f:
        timeline_data = json.load(f)

    target_dur = float(timeline_data.get("DUR", timeline_data.get("dur", 0.0)))

    # Pauses can be passed in CLI or loaded from timeline
    if pauses is None:
        pauses = [float(p) for p in timeline_data.get("pauses", [])]

    out_dir = Path(out_mix_path).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Use sibling temp wavs for score and foley
    base_name = Path(out_mix_path).stem
    score_wav = out_dir / f"{base_name}_score.wav"
    foley_wav = out_dir / f"{base_name}_foley.wav"

    try:
        # Step 1: Score
        make_score(timeline_path, score_wav, sr=sr, palette=palette)
        score_audio, _ = sf.read(str(score_wav))
        spectral_ratio_db, e_low, e_high = measure_spectral_balance(score_audio, sr=sr)

        # Step 2: Foley
        make_foley(events_path, foley_wav, sr=sr)

        # Step 3: Mix
        mix_stats = mix(
            voice_wav=voice_path,
            score_wav=score_wav,
            foley_wav=foley_wav,
            out_wav=out_mix_path,
            pauses=pauses,
        )

        out_dur = mix_stats["dur"]
        peak_dbfs = mix_stats["peak_dbfs"]
        rms_diff_db = mix_stats["rms_diff_db"]

        print("=== BUILD SOUNDTRACK RESULTS ===")
        print(f"Target DUR: {target_dur:.3f} s")
        print(f"Output duration: {out_dur:.3f} s (diff: {abs(out_dur - target_dur):.3f} s)")
        print(f"Output peak: {peak_dbfs:.2f} dBFS (< -1 dBFS: {peak_dbfs < -1.0})")
        if rms_diff_db is not None:
            print(f"RMS voice - RMS music: {rms_diff_db:.2f} dB (target ~ 10 dB)")
        else:
            print("RMS voice - RMS music: N/A (no voice)")
        print(f"Score 20-120Hz vs 120Hz-8kHz energy: {spectral_ratio_db:.2f} dB (target ~ -3 dB)")
        print(f"Output written to: {out_mix_path}")

        return {
            "out_mix": out_mix_path,
            "target_dur": target_dur,
            "out_dur": out_dur,
            "dur_diff": abs(out_dur - target_dur),
            "peak_dbfs": peak_dbfs,
            "rms_diff_db": rms_diff_db,
            "spectral_ratio_db": spectral_ratio_db,
        }
    finally:
        # Cleanup temporary files if they exist
        for p in (score_wav, foley_wav):
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass


def main():
    parser = argparse.ArgumentParser(
        description="Build complete soundtrack (score + foley + voice -> mix.wav)"
    )
    parser.add_argument("events", help="Path to events.json")
    parser.add_argument("timeline", help="Path to timeline.json")
    parser.add_argument("out_mix", help="Path to output mix.wav")
    parser.add_argument("--voice", default=None, help="Path to voice.wav (optional)")
    parser.add_argument("--pauses", nargs="*", type=float, default=None, help="Timestamps of pauses")
    parser.add_argument("--palette", default="dark-keynote", help="Sound palette (default: dark-keynote)")
    parser.add_argument("--sr", type=int, default=48000, help="Sample rate (default: 48000)")

    args = parser.parse_args()
    build_audio(
        events_path=args.events,
        timeline_path=args.timeline,
        out_mix_path=args.out_mix,
        voice_path=args.voice,
        pauses=args.pauses,
        palette=args.palette,
        sr=args.sr,
    )


if __name__ == "__main__":
    main()
