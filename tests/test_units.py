"""Kiểm tra nhanh không cần GPU/LLM: .venv\\Scripts\\python.exe tests\\test_units.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import annotate, complexity, errors, normalizer, resources, subtitles, transition, variety  # noqa: E402

TMP = Path(tempfile.mkdtemp())
ok = 0


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""))
    ok += bool(cond)
    return cond


# normalizer
n = normalizer.normalize("GPU giá $1.599, tăng 12,5% năm 2025; API và AI.", "Vietnamese")
check("normalizer số/tiền/%/từ điển", "một nghìn năm trăm chín mươi chín đô la" in n and "phần trăm" in n
      and "ây pi ai" in n and "ây pi ây ai" not in n, n)
check("vi_number 21/105/1005", [normalizer.vi_number(x) for x in (21, 105, 1005)] ==
      ["hai mươi mốt", "một trăm lẻ năm", "một nghìn không trăm lẻ năm"])

# variety
rows = [{"visual_type": "diagram", "camera": "static"}] * 4
check("variety bắt lặp kiểu hình", variety.violations(rows, {"max_same_type": 2, "max_static_scenes": 2}, 15) != [])
rows = [{"visual_type": t, "camera": c} for t, c in
        [("diagram", "zoom_in"), ("timeline", "static"), ("character", "pan_left"), ("map", "zoom_out")]]
check("variety cho qua khi đa dạng", variety.violations(rows, {"max_same_type": 2, "max_static_scenes": 2}, 15) == [])

# errors
check("errors OOM=transient", errors.classify(RuntimeError("CUDA out of memory")) == errors.TRANSIENT)
check("errors JSON=logical", errors.classify(json.JSONDecodeError("x", "y", 0)) == errors.LOGICAL)
check("errors disk=fatal", errors.classify(OSError(28, "No space left on device")) == errors.FATAL)
check("errors mạng=transient", errors.classify(requests.ConnectionError("x")) == errors.TRANSIENT)
check("errors ảnh xấu=regenerable", errors.classify(RuntimeError("sd.cpp lỗi")) == errors.REGENERABLE)

# image fixture
img = np.full((768, 1344, 3), (215, 235, 245), np.uint8)
cv2.circle(img, (250, 380), 140, (60, 60, 60), 6)
cv2.rectangle(img, (600, 250), (800, 520), (60, 60, 60), 6)
png = TMP / "s.png"
cv2.imwrite(str(png), img)
busy = img.copy()
for i in range(0, 1344, 12):
    cv2.line(busy, (i, 0), (1343 - i, 767), (40, 40, 40), 2)
busy_png = TMP / "busy.png"
cv2.imwrite(str(busy_png), busy)

c1, c2 = complexity.measure(png, 12000), complexity.measure(busy_png, 4000)
check("complexity ảnh đơn giản phù hợp", c1["suitability"] >= 0.9, c1)
check("complexity ảnh rối bị hạ điểm", c2["suitability"] < 0.6, c2)

# annotate predrawn + lead
ann = annotate.build(png, TMP / "a.json", "s", "x", 8000, 5, None, new_area=[560, 70, 1344, 700], lead_ms=500)
check("annotate draw_over có 'nét cũ' trước", ann["elements"][0]["label"] == "nét cũ"
      and ann["elements"][0]["reveal"]["durationMs"] <= 100)
ann2 = annotate.build(png, TMP / "b.json", "s", "x", 8000, 5, lead_ms=600)
check("annotate lead_ms dời thời điểm vẽ", ann2["elements"][0]["reveal"]["startMs"] >= 800)

# subtitles
cs = subtitles.cues([{"narration": "Một hai ba bốn năm sáu bảy tám chín mười mười một. Câu hai.",
                      "duration_ms": 6000, "speech_ms": 5000, "emphasis": "câu hai"}], [400])
check("subtitles bắt đầu sau lead", cs[0][0] == 400 and cs[-1][1] <= 5400)

# transition trên clip thật ngắn
clip = TMP / "c.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0xF5EBD7:s=640x360:d=7:r=25",
                "-vf", "drawbox=x=100:y=100:w=80:h=80:c=black", "-pix_fmt", "yuv420p", str(clip)], check=True)
for cam, tr in (("zoom_in", "fade"), ("pan_left", "chapter"), ("zoom_out", "cut")):
    out = TMP / f"{cam}.mp4"
    transition.apply(clip, out, cam, (0.2, 0.3), tr, 0.5)
    w, h, fps, nf = transition._probe(out)
    check(f"transition {cam}/{tr}", (w, h) == (640, 360) and abs(nf - 175) <= 2, f"{w}x{h} {nf} frames")
short = TMP / "short.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=320x180:d=3:r=25",
                "-pix_fmt", "yuv420p", str(short)], check=True)
check("cảnh ngắn: camera đứng yên", not transition.apply(short, TMP / "s2.mp4", "zoom_in", (0.5, 0.5), "cut", 0.5))

# resources
snap = resources.snapshot()
check("resources đọc GPU/đĩa", "disk_free_gb" in snap and "temp" in snap, snap)

# ---- v3: artifacts / lock / visual_qc / subtitles / annotate merge / hard gate / audio consistency ----
import os  # noqa: E402

import soundfile as sf  # noqa: E402

from app import artifacts, audio_qc, story_quality, video_qc, visual_qc  # noqa: E402
from app.lock import WorkerLock  # noqa: E402

wav = TMP / "a.wav"
sf.write(wav, (0.1 * np.sin(np.arange(24000 * 3) / 10)).astype(np.float32), 24000)
man = artifacts.Manifest(TMP)
man.record(png, "image")
man.record(wav, "audio")
man.record(clip, "clip")
check("artifact hợp lệ được nhận", man.ok(png) and man.ok(wav) and man.ok(clip))
broken = TMP / "broken.mp4"
broken.write_bytes(clip.read_bytes()[:3000])
check("mp4 cắt dở bị coi là hỏng", not artifacts.valid(broken))
with open(wav, "ab") as f:
    f.write(b"x")
check("checksum phát hiện file bị đổi", not man.ok(wav))

lk = WorkerLock(TMP / "x.db")
check("lock lấy được lần đầu", lk.acquire()[0])
(TMP / "x.lock").write_text(json.dumps({"pid": os.getppid(), "since": "t"}), encoding="utf-8")
check("lock chặn worker thứ hai", not WorkerLock(TMP / "x.db").acquire()[0])

blank = TMP / "blank.png"
cv2.imwrite(str(blank), np.full((768, 1344, 3), (215, 235, 245), np.uint8))
check("visual_qc bắt ảnh trắng", "ảnh gần như trắng" in visual_qc.check(blank)["cv_issues"])
check("visual_qc bắt ảnh trùng cảnh trước", visual_qc.check(png, png)["similarity_prev"] > 0.85)
low = img.copy()
cv2.rectangle(low, (300, 680), (1000, 760), (40, 40, 40), -1)
lowp = TMP / "low.png"
cv2.imwrite(str(lowp), low)
check("visual_qc bắt hình đè vùng phụ đề", "hình đè vùng phụ đề phía dưới" in visual_qc.check(lowp)["cv_issues"])

parts = subtitles._chunks_chars("Hôm nay Nguyễn Văn An đi học rất sớm vì trường tổ chức một buổi lễ khai giảng thật lớn.")
check("phụ đề ≤ ~42 ký tự/dòng, không tách tên riêng", all(len(p) <= 50 for p in parts)
      and not any(p.endswith("Nguyễn") or p.endswith("Văn") for p in parts), parts)
words = [("Hôm", 0.1, 0.3), ("nay", 0.3, 0.5), ("trời", 0.6, 0.9), ("đẹp", 0.9, 1.2)]
cw = subtitles.cues_from_words([{"narration": "Hôm nay trời đẹp.", "asr_words": words, "emphasis": ""}], [5000])
check("phụ đề theo timestamp từng từ", cw[0][0] == 5100, cw)

dup = [("a", [400, 200, 1000, 500]), ("b", [400, 200, 1000, 500]), ("c", [400, 200, 1000, 500])]
merged = annotate._merge_hidden(dup, 1344, 768)
check("annotate gộp vùng trùng (không còn vùng rỗng)", len(merged) == 1, merged)

stats = [{"features": {"dbfs": -20, "mfcc": [1] * 13}, "words_per_sec": 3.0} for _ in range(5)]
stats.append({"features": {"dbfs": -32, "mfcc": [1] * 13}, "words_per_sec": 3.0})
check("audio consistency bắt cảnh lệch âm lượng", audio_qc.consistency(stats) == [5])

iss = [{"type": "no_example", "severity": "high", "evidence": "x", "repair": "add_concrete_example"},
       {"type": "weak_link", "severity": "low", "evidence": "y", "repair": "add_transition"}]
check("story_quality chỉ giữ lỗi đáng sửa", len(story_quality.actionable(iss)) == 1)

vid = TMP / "v.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=320x180:d=3:r=25",
                "-f", "lavfi", "-i", "sine=f=300:d=3", "-shortest", "-pix_fmt", "yuv420p", str(vid)], check=True)
vid.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
m2 = artifacts.Manifest(TMP / "m2") if (TMP / "m2").mkdir() is None else None
fails = video_qc.hard_gate(vid, [{"idx": 0, "clip": str(clip), "audio": str(wav)}], m2, 3000,
                           {"black_segments": 0, "long_silences": 0, "duration_ms": 3000}, {})
check("hard gate chặn khi thiếu artifact", any(f["type"] == "missing_scene" for f in fails), [f["type"] for f in fails])
m2.record(clip, "clip")
sf.write(wav, (0.1 * np.sin(np.arange(24000 * 3) / 10)).astype(np.float32), 24000)
m2.record(wav, "audio")
fails = video_qc.hard_gate(vid, [{"idx": 0, "clip": str(clip), "audio": str(wav)}], m2, 3000,
                           {"black_segments": 0, "long_silences": 0, "duration_ms": 3000}, {})
check("hard gate cho qua video hợp lệ", fails == [], fails)

print(f"\n{ok} kiểm tra PASS")
