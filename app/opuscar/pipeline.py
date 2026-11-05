"""pipeline.py — Pipeline hoàn chỉnh cho kiểu dựng opuscar:
Lập kế hoạch cảnh -> Chụp khung Chrome headless -> Soạn foley & nhạc -> Ghép voice -> Muxing -14 LUFS.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .. import packager, styles
from . import mux, plan
from .audio.build import build_audio


def export_srt_from_content(content: dict, out_srt: Path | str) -> Path:
    """Tạo file SRT từ các trường subtitle của content.json chuẩn opuscar."""
    cues = []
    for sc in content.get("scenes", []):
        for sub in sc.get("subtitle", []):
            txt = (sub.get("text") or "").strip()
            if txt:
                cues.append({
                    "t0": float(sub.get("t0", 0.0)),
                    "t1": float(sub.get("t1", 0.0)),
                    "text": txt,
                })

    cues.sort(key=lambda c: c["t0"])
    for k in range(len(cues) - 1):
        if cues[k]["t1"] > cues[k + 1]["t0"]:
            cues[k]["t1"] = cues[k + 1]["t0"]

    def _fmt(sec: float) -> str:
        ms = int(round(max(0.0, sec) * 1000))
        h = ms // 3600000
        m = (ms // 60000) % 60
        s = (ms // 1000) % 60
        rem_ms = ms % 1000
        return f"{h:02d}:{m:02d}:{s:02d},{rem_ms:03d}"

    lines = []
    for idx, cue in enumerate(cues):
        lines.append(f"{idx + 1}\n{_fmt(cue['t0'])} --> {_fmt(cue['t1'])}\n{cue['text']}\n")

    out_p = Path(out_srt).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text("\n".join(lines), encoding="utf-8")
    return out_p


def concat_voice(
    rows: list[dict],
    content: dict,
    out_voice: Path | str,
    sr: int = 48000,
    log: Any = print,
) -> Path:
    """Ghép các file wav của từng cảnh theo mốc thời gian t0 thành một file voice.wav duy nhất."""
    scenes = content.get("scenes", [])
    if not scenes:
        raise RuntimeError("Không có cảnh nào trong content.json để ghép giọng nói.")

    total_dur = float(scenes[-1]["t1"])
    total_samples = int(math.ceil(total_dur * sr))
    voice_data = np.zeros(total_samples, dtype=np.float32)

    for i, r in enumerate(rows):
        audio_src = r.get("audio")
        if not audio_src or not Path(audio_src).is_file():
            jid = r.get("job_id")
            if jid:
                try:
                    alt = packager.folder_of(jid) / "giong" / f"canh_{i + 1:03d}.wav"
                    if alt.is_file():
                        audio_src = str(alt)
                except Exception:
                    pass

        if not audio_src or not Path(audio_src).is_file():
            log(f"[opuscar] Cảnh {i + 1}: Không tìm thấy file âm thanh wav, bỏ qua giọng cảnh này.")
            continue

        row_scenes = [sc for sc in scenes if sc.get("row_idx") == i and sc.get("shot") != "chapter_card"]
        if not row_scenes:
            row_scenes = [sc for sc in scenes if sc.get("row_idx") == i]
        if not row_scenes:
            continue
        row_t0 = float(row_scenes[0]["t0"])

        try:
            data, file_sr = sf.read(str(audio_src))
            if data.ndim > 1:
                data = data.mean(axis=1)
            data = data.astype(np.float32)

            if file_sr != sr:
                from scipy.signal import resample
                target_len = int(round(len(data) * float(sr) / file_sr))
                data = resample(data, target_len).astype(np.float32)

            start_idx = int(round(row_t0 * sr))
            if start_idx >= total_samples:
                continue
            end_idx = min(start_idx + len(data), total_samples)
            chunk = data[: end_idx - start_idx]
            voice_data[start_idx:end_idx] = chunk
        except Exception as e:
            raise RuntimeError(f"Lỗi khi đọc hoặc xử lý file âm thanh {audio_src} của cảnh {i + 1}: {e}")

    out_p = Path(out_voice).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_p), voice_data, sr)
    return out_p


def _get_cfg_field(cfg: Any, section: str, field: str, default: Any = None) -> Any:
    sec = getattr(cfg, section, None)
    if sec is not None:
        if isinstance(sec, dict):
            return sec.get(field, default)
        return getattr(sec, field, default)
    if isinstance(cfg, dict):
        return (cfg.get(section) or {}).get(field, default)
    return default


def render_job(
    jid: int,
    rows: list[dict],
    outline: dict,
    facts: list[dict] | str | None,
    cfg: Any,
    llm: Any,
    log: Any = print,
    work_dir: Path | str | None = None,
    fmt: str | None = None,
) -> dict:
    """Thực thi toàn bộ luồng dựng video opuscar cho một job.

    1. Chuẩn bị thư mục film_dir = <thư mục project của job>/opuscar/ (hoặc work_dir nếu truyền vào).
    2. Gọi plan.build_content() tạo content.json và write_film().
    3. Chạy vendor events.mjs và video.mjs bằng subprocess với PLAYWRIGHT_CHROME.
    4. Ghép voice các cảnh theo t0 -> voice.wav.
    5. audio.build tạo soundtrack -> mix.wav.
    6. mux.mux kết hợp video và audio -> film.mp4 (-14 LUFS).
    7. Trả về dict: {"video": film_mp4, "srt": film_srt, "duration": dur}.
    """
    # 1. Xác định film_dir
    if work_dir is not None:
        w = Path(work_dir).resolve()
        film_dir = w if w.name == "opuscar" else w / "opuscar"
    else:
        film_dir = packager.folder_of(jid) / "opuscar"
    film_dir.mkdir(parents=True, exist_ok=True)

    if fmt is None:
        fmt = styles.format_of(jid) if hasattr(styles, "format_of") else "long"
    log(f"[opuscar] Bắt đầu render_job #{jid} (định dạng: {fmt}, film_dir: {film_dir})")

    # 2. Lập kế hoạch nội dung và tạo timeline, index.html, content.json
    try:
        content = plan.build_content(
            rows=rows,
            outline=outline,
            facts=facts,
            cfg=cfg,
            llm=llm,
            log=log,
            fmt=fmt,
            film_dir=film_dir,
        )
        plan.timeline(content, rows, film_dir=film_dir)
        plan.write_film(content, film_dir)
    except Exception as e:
        raise RuntimeError(f"Lỗi khi lập kế hoạch kịch bản opuscar (plan.build_content): {e}")

    # 3. Chạy vendor events.mjs và video.mjs
    vendor_dir = Path(__file__).resolve().parents[2] / "vendor" / "opuscar"
    if not vendor_dir.is_dir():
        raise RuntimeError(f"Không tìm thấy thư mục vendor opuscar tại {vendor_dir}")

    chrome_path = _get_cfg_field(cfg, "opuscar", "chrome")
    if not chrome_path or not Path(chrome_path).is_file():
        raise RuntimeError(f"Không tìm thấy file Chrome headless tại '{chrome_path}' (theo config opuscar.chrome)")

    env = os.environ.copy()
    env["PLAYWRIGHT_CHROME"] = str(chrome_path)

    if fmt == "short":
        size = _get_cfg_field(cfg, "opuscar", "size_short", "1080x1920")
    else:
        size = _get_cfg_field(cfg, "opuscar", "size_long", "1920x1080")

    workers = str(_get_cfg_field(cfg, "opuscar", "workers", 3))
    node_bin = str(_get_cfg_field(cfg, "opuscar", "node", "node") or "node")
    fps = str(content.get("fps") or _get_cfg_field(cfg, "opuscar", "fps", 24) or 24)

    # Chạy events.mjs
    events_cmd = [node_bin, "core/render/events.mjs", str(film_dir), "--size", size]
    log(f"[opuscar] Chạy events.mjs: {' '.join(events_cmd)}")
    p_ev = subprocess.run(
        events_cmd,
        cwd=str(vendor_dir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if p_ev.returncode != 0:
        tail = (p_ev.stderr or p_ev.stdout or "").strip()
        raise RuntimeError(f"Lỗi chạy events.mjs cho job {jid} (exit code {p_ev.returncode}):\n{tail}")

    events_json = film_dir / "events.json"
    if not events_json.is_file():
        raise RuntimeError(f"events.mjs kết thúc nhưng không tạo được file {events_json}")

    # Chạy video.mjs
    video_raw = film_dir / "video_raw.mp4"
    video_cmd = [
        node_bin,
        "core/render/video.mjs",
        str(film_dir),
        "--fps", fps,
        "--workers", workers,
        "--size", size,
        "--out", str(video_raw),
    ]
    log(f"[opuscar] Chạy video.mjs: {' '.join(video_cmd)}")
    p_vid = subprocess.run(
        video_cmd,
        cwd=str(vendor_dir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if p_vid.returncode != 0:
        tail = (p_vid.stderr or p_vid.stdout or "").strip()
        raise RuntimeError(f"Lỗi render video bằng video.mjs cho job {jid} (exit code {p_vid.returncode}):\n{tail}")

    if not video_raw.is_file():
        raise RuntimeError(f"video.mjs kết thúc nhưng không tìm thấy file video raw tại {video_raw}")

    # 4. Ghép voice các cảnh theo t0
    voice_wav = film_dir / "voice.wav"
    log(f"[opuscar] Ghép giọng các cảnh theo t0 -> {voice_wav.name}")
    concat_voice(rows, content, voice_wav, sr=48000, log=log)

    # 5. audio.build tạo soundtrack -> mix.wav
    timeline_json = film_dir / "timeline.json"
    if not timeline_json.is_file():
        raise RuntimeError(f"Không tìm thấy file timeline.json tại {timeline_json}")

    mix_wav = film_dir / "mix.wav"
    log(f"[opuscar] Soạn nhạc và foley (audio.build_audio) -> {mix_wav.name}")
    try:
        build_audio(
            events_path=str(events_json),
            timeline_path=str(timeline_json),
            out_mix_path=str(mix_wav),
            voice_path=str(voice_wav),
            palette="dark-keynote",
            sr=48000,
        )
    except Exception as e:
        raise RuntimeError(f"Lỗi khi tổng hợp âm thanh (audio.build_audio): {e}")

    if not mix_wav.is_file():
        raise RuntimeError(f"audio.build_audio hoàn tất nhưng không tạo được {mix_wav}")

    # 6. Muxing video và mix audio -> film.mp4 (-14 LUFS)
    film_mp4 = film_dir / "film.mp4"
    grain = float(_get_cfg_field(cfg, "opuscar", "grain", 0) or 0)
    log(f"[opuscar] Muxing video + audio (mux.mux) -> {film_mp4.name} (fps={fps}, grain={grain})")
    try:
        mux.mux(
            video=str(video_raw),
            audio=str(mix_wav),
            out=str(film_mp4),
            fps=float(fps),
            grain=grain,
        )
    except Exception as e:
        raise RuntimeError(f"Lỗi khi mux hoàn thiện phim opuscar (mux.mux): {e}")

    if not film_mp4.is_file():
        raise RuntimeError(f"mux.mux hoàn tất nhưng không tìm thấy file output {film_mp4}")

    # 7. Xuất phụ đề SRT và tính thời lượng
    srt_path = film_dir / "film.srt"
    export_srt_from_content(content, srt_path)

    dur = float(content["scenes"][-1]["t1"]) if content.get("scenes") else 0.0
    log(f"[opuscar] Hoàn tất render_job #{jid} thành công: {film_mp4} ({dur:.2f}s)")

    return {
        "video": str(film_mp4),
        "srt": str(srt_path),
        "duration": dur,
    }
