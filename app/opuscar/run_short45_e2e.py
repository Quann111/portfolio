"""Chạy e2e pipeline.render_job cho short 45s với 6 row đầu của job 1053.
Ghi vào app/opuscar/_e2e_test/short45/.
Kiểm tra:
- ffprobe: 1080x1920
- độ dài <= 48s
- ebur128 ≈ -14 LUFS (trong khoảng -15 đến -13)
- blackdetect sạch (0 khung đen)
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import soundfile as sf

from app import config, db, packager
from app.opuscar import pipeline
from app.opuscar.test_render_e2e import check_blackdetect, measure_ebur128, probe_video


def run_short45_e2e():
    cfg = config.load()

    rows = db.q("SELECT outline, facts FROM jobs WHERE id=1053")
    if not rows:
        raise RuntimeError("Không tìm thấy job 1053 trong database")

    j = rows[0]
    outline = json.loads(j["outline"] or "{}")
    facts = json.loads(j["facts"] or "[]")

    sc = db.scenes(1053)[:6]
    giong_dir = packager.folder_of(1053) / "giong"

    short45_root = ROOT / "app" / "opuscar" / "_e2e_test" / "short45"
    short45_root.mkdir(parents=True, exist_ok=True)
    wavs_dir = short45_root / "_wavs"
    wavs_dir.mkdir(parents=True, exist_ok=True)

    # 6 row tổng thời lượng gốc ~100.9s -> cắt/scale còn đúng ~45s
    orig_durations = [s["duration_ms"] for s in sc]
    total_orig = sum(orig_durations)
    target_total_ms = 45000  # 45.0s

    test_rows = []
    accum = 0
    for i, s in enumerate(sc):
        r = db.scene_data(s)
        r["idx"] = i
        r["job_id"] = 1053
        r["chapter"] = 0  # short gộp thành 1 chương
        r["narration"] = s["narration"]
        r["image"] = s["image"]

        if i == len(sc) - 1:
            dur_ms = target_total_ms - accum
        else:
            dur_ms = round(s["duration_ms"] * target_total_ms / total_orig)
            accum += dur_ms
        r["duration_ms"] = dur_ms

        # Cắt wav gốc theo dur_ms
        orig_wav = giong_dir / f"canh_{i + 1:03d}.wav"
        if not orig_wav.is_file():
            orig_wav = Path(s["audio"])
        dst_wav = wavs_dir / f"canh_{i + 1:03d}.wav"

        data, sr = sf.read(str(orig_wav))
        needed_samples = int(round((dur_ms / 1000.0) * sr))
        if len(data) > needed_samples:
            trimmed_data = data[:needed_samples]
        else:
            trimmed_data = data
        sf.write(str(dst_wav), trimmed_data, sr)

        r["audio"] = str(dst_wav)
        test_rows.append(r)

    print(f"Tổng thời lượng 6 cảnh sau khi cắt: {sum(r['duration_ms'] for r in test_rows)} ms (~45s)")

    # Chạy render_job
    res = pipeline.render_job(
        jid=1053,
        rows=test_rows,
        outline=outline,
        facts=facts,
        cfg=cfg,
        llm=None,
        log=print,
        work_dir=short45_root,
        fmt="short",
    )

    video_path = Path(res["video"])
    print(f"Render hoàn tất: {video_path}")

    # Copy video ra short45_root/film.mp4 nếu đang ở thư mục con opuscar
    root_film = short45_root / "film.mp4"
    if video_path != root_film and video_path.is_file():
        shutil.copy2(video_path, root_film)

    # 1. ffprobe
    probe = probe_video(video_path)
    print(f"ffprobe kết quả: {probe}")
    assert probe["width"] == 1080 and probe["height"] == 1920, (
        f"Kích thước sai: {probe['width']}x{probe['height']} (kỳ vọng 1080x1920)"
    )
    assert probe["duration"] <= 48.0, f"Độ dài {probe['duration']:.2f}s vượt quá 48s!"

    # 2. ebur128
    ebur = measure_ebur128(video_path)
    print(f"EBU R128: I={ebur['i']} LUFS, Peak={ebur['peak']} dBFS")
    assert ebur["i"] is not None and abs(ebur["i"] - (-14.0)) <= 1.0, (
        f"Loudness không đạt -14 LUFS ± 1 LU: đo được {ebur['i']} LUFS"
    )

    # 3. blackdetect
    blacks = check_blackdetect(video_path)
    print(f"Blackdetect: {len(blacks)} đoạn khung đen")
    assert len(blacks) == 0, f"Phát hiện khung đen: {blacks}"

    print("\n✅ KIỂM THỬ SHORT45 E2E THÀNH CÔNG RỰC RỠ:")
    print(f"- File: {video_path}")
    print(f"- Kích thước: {probe['width']}x{probe['height']}")
    print(f"- Thời lượng: {probe['duration']:.2f}s (<= 48s)")
    print(f"- Âm lượng: {ebur['i']} LUFS (chuẩn -14 LUFS)")
    print(f"- Khung đen: {len(blacks)}")

    return {
        "video": str(video_path),
        "size": f"{probe['width']}x{probe['height']}",
        "duration": probe["duration"],
        "lufs": ebur["i"],
        "black_segments": len(blacks),
    }


if __name__ == "__main__":
    run_short45_e2e()
