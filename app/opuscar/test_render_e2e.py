"""Kiểm thử e2e độc lập pipeline.render_job trên 12 row đầu của job 1053.
Chỉ ĐỌC dữ liệu job 1053, ghi kết quả vào app/opuscar/_e2e_test/ (KHÔNG ghi vào projects/ hay DB thật).
Kiểm tra cả bản long (1920x1080) và short (1080x1920):
- Ra film.mp4
- ffprobe: đúng kích thước, có audio + video, độ dài ±2s
- ebur128 ≈ -14 LUFS
- blackdetect sạch không có khung đen
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Thêm thư mục gốc autovideo vào sys.path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, db, packager
from app.opuscar import pipeline


def get_job_1053_data():
    """Đọc dữ liệu 12 row đầu của job 1053 (chỉ đọc)."""
    rows = db.q("SELECT outline, facts FROM jobs WHERE id=1053")
    if not rows:
        raise RuntimeError("Không tìm thấy job 1053 trong database")

    j = rows[0]
    outline = json.loads(j["outline"] or "{}")
    facts = json.loads(j["facts"] or "[]")

    sc = db.scenes(1053)[:12]
    giong_dir = packager.folder_of(1053) / "giong"

    test_rows = []
    for i, s in enumerate(sc):
        r = db.scene_data(s)
        r["idx"] = i
        r["job_id"] = 1053
        r["chapter"] = s["chapter"]
        r["narration"] = s["narration"]
        r["duration_ms"] = s["duration_ms"]
        r["image"] = s["image"]
        # File âm thanh thật lưu trong giong/
        wav = giong_dir / f"canh_{i + 1:03d}.wav"
        r["audio"] = str(wav) if wav.is_file() else s["audio"]
        test_rows.append(r)

    return test_rows, outline, facts


def probe_video(video_path: Path):
    """Kiểm tra video bằng ffprobe: stream codec, kích thước WxH, thời lượng."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height:format=duration",
        "-of", "json", str(video_path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe lỗi: {r.stderr}")
    data = json.loads(r.stdout)
    streams = data.get("streams", [])
    codecs = [s.get("codec_type") for s in streams]

    vid = next((s for s in streams if s.get("codec_type") == "video"), {})
    w = int(vid.get("width") or 0)
    h = int(vid.get("height") or 0)
    dur = float(data.get("format", {}).get("duration") or 0.0)

    return {
        "has_video": "video" in codecs,
        "has_audio": "audio" in codecs,
        "width": w,
        "height": h,
        "duration": dur,
    }


def measure_ebur128(video_path: Path) -> dict:
    """Đo độ lớn âm thanh chuẩn EBU R128 (Integrated LUFS, True Peak)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video_path), "-af", "ebur128=peak=true", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    meas = {"i": None, "lra": None, "peak": None}
    for line in (r.stderr or "").splitlines():
        m = re.match(r"\s+(I|LRA|Peak):\s+(-?[\d.]+|-inf)", line)
        if m:
            meas[{"I": "i", "LRA": "lra", "Peak": "peak"}[m.group(1)]] = float(m.group(2))
    return meas


def check_blackdetect(video_path: Path) -> list:
    """Kiểm tra khung đen bằng filter blackdetect của ffmpeg."""
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(video_path),
        "-vf", "blackdetect=d=0.5:pix_th=0.10",
        "-f", "null", "-"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    blacks = re.findall(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", r.stderr or "")
    return blacks


def test_independent_render():
    cfg = config.load()
    rows, outline, facts = get_job_1053_data()
    e2e_root = ROOT / "app" / "opuscar" / "_e2e_test"
    e2e_root.mkdir(parents=True, exist_ok=True)

    results = {}

    for mode in ("long", "short"):
        print(f"\n=======================================================")
        print(f"BẮT ĐẦU TEST E2E PIPELINE OPUSCAR: {mode.upper()}")
        print(f"=======================================================")

        mode_work_dir = e2e_root / mode
        res = pipeline.render_job(
            jid=1053,
            rows=rows,
            outline=outline,
            facts=facts,
            cfg=cfg,
            llm=None,  # Fallback tất định, không phụ thuộc dịch vụ ngoài
            log=print,
            work_dir=mode_work_dir,
            fmt=mode,
        )

        video_path = Path(res["video"])
        srt_path = Path(res["srt"])
        planned_dur = res["duration"]

        print(f"--- Kiểm tra kết quả {mode} ---")
        assert video_path.is_file(), f"Không có file video: {video_path}"
        assert srt_path.is_file(), f"Không có file srt: {srt_path}"

        probe = probe_video(video_path)
        print(f"ffprobe: {probe}")
        assert probe["has_video"], "Lỗi: Không có stream video"
        assert probe["has_audio"], "Lỗi: Không có stream audio"

        expected_w, expected_h = (1080, 1920) if mode == "short" else (1920, 1080)
        assert probe["width"] == expected_w and probe["height"] == expected_h, (
            f"Kích thước sai: {probe['width']}x{probe['height']} (kỳ vọng {expected_w}x{expected_h})"
        )

        dur_diff = abs(probe["duration"] - planned_dur)
        print(f"Độ dài: thực tế {probe['duration']:.2f}s, kế hoạch {planned_dur:.2f}s (lệch {dur_diff:.2f}s)")
        assert dur_diff <= 2.0, f"Độ dài lệch quá ±2s: {dur_diff:.2f}s"

        ebur = measure_ebur128(video_path)
        print(f"EBU R128: I={ebur['i']} LUFS, Peak={ebur['peak']} dBFS")
        assert ebur["i"] is not None and abs(ebur["i"] - (-14.0)) <= 1.0, (
            f"Loudness không đạt -14 LUFS ± 1 LU: đo được {ebur['i']} LUFS"
        )

        blacks = check_blackdetect(video_path)
        print(f"Blackdetect: {len(blacks)} đoạn khung đen")
        assert len(blacks) == 0, f"Phát hiện khung đen: {blacks}"

        results[mode] = {
            "video": str(video_path),
            "size": f"{probe['width']}x{probe['height']}",
            "duration": probe["duration"],
            "lufs": ebur["i"],
            "peak": ebur["peak"],
            "black_segments": len(blacks),
        }

    print("\n=======================================================")
    print("TẤT CẢ CÁC KIỂM THỬ E2E (LONG + SHORT) ĐÃ PASS 100%!")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print("=======================================================")
    return results


if __name__ == "__main__":
    test_independent_render()
