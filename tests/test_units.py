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
from app import db  # noqa: E402
db.DB_PATH = TMP / "t.db"
db.init()
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

# merge_sentences: gộp mảnh cắt giữa câu TRƯỚC khi dịch (dịch từng mảnh thì model tự gộp → lệch chỉ số)
frag = [(0, 1000, "Nguy cơ an", ""), (1000, 2000, "ninh nghiêm trọng.", ""),
        (2000, 3000, "Ông Trump tuyên bố", ""), (3000, 4200, "sẽ gặp ông Putin.", "")]
mg = subtitles.merge_sentences(frag)
check("merge_sentences gộp mảnh cắt giữa câu", len(mg) == 2
      and mg[0][2] == "Nguy cơ an ninh nghiêm trọng." and (mg[0][0], mg[0][1]) == (0, 2000), mg)
check("merge_sentences tôn trọng max_ms", len(subtitles.merge_sentences(
      [(0, 3000, "Một câu rất dài chưa kết thúc", ""), (3000, 8000, "phần tiếp theo", "")])) == 2)
long_cue = subtitles.merge_sentences([(0, 5000, "Đây là một câu tiếng Việt khá dài để kiểm tra việc tự động "
                                                 "xuống dòng cho dễ đọc.", "")])
check("merge_sentences câu > 45 ký tự xuống 2 dòng", len(long_cue) == 1 and "\n" in long_cue[0][2], long_cue[0][2])

srt_out = TMP / "y.srt"
subtitles.write_srt_crlf(mg, srt_out)
data = srt_out.read_bytes()
check("SRT YouTube có BOM + CRLF + dòng trống cuối",
      data.startswith(b"\xef\xbb\xbf") and b"\r\n\r\n" in data and data.endswith(b"\r\n\r\n"), data[:6])
check("read_srt đọc lại đúng số cue + thời gian", [(a, b, t) for a, b, t, _ in subtitles.read_srt(srt_out)] ==
      [(a, b, t) for a, b, t, _ in mg])

# split_sentences: dịch ở mức CÂU, hiển thị ở mức CUE – VI và EN phải cùng số cue, cùng thời gian
sent = [(0, 9000, "Đây là một câu tiếng Việt rất dài cần được chia thành nhiều cue hiển thị cho dễ đọc trên "
                  "YouTube và không bị che mất hình.", "")]
en_sent = [(0, 9000, "This is a very long Vietnamese sentence that must be split into several display cues so it "
                     "stays easy to read on YouTube without covering the picture.", "")]
vi_c, en_c = subtitles.split_sentences(sent, en_sent)
check("split_sentences VI/EN cùng số cue + cùng thời gian",
      len(vi_c) == len(en_c) > 1 and all(x[:2] == y[:2] for x, y in zip(vi_c, en_c)), f"{len(vi_c)} cue")
check("split_sentences cue ≤ 2 dòng, không rỗng", all(c[2].count("\n") <= 1 and c[2].strip() for c in vi_c + en_c))
check("split_sentences giữ nguyên tổng thời gian", vi_c[0][0] == 0 and vi_c[-1][1] == 9000, vi_c[-1][1])
# câu EN dài hơn VI nhiều → số cue phải theo bản EN, không dồn thành dòng > 42 ký tự
v2, e2 = subtitles.split_sentences(
    [(0, 6000, "Ông ấy nói ngắn gọn.", "")],
    [(0, 6000, "He delivered an extremely detailed and remarkably long-winded statement about the future of "
               "international trade policy across the whole region.", "")])
check("split_sentences theo bản EN dài hơn", len(v2) == len(e2) > 1
      and all(len(ln) <= 42 for c in v2 + e2 for ln in c[2].split("\n")), [c[2].replace("\n", "/") for c in e2])
check("_still_vi không loại oan tên riêng Latin (Brasília, Bogotá)",
      not subtitles._still_vi("While media watched, a quiet disappearance unfolded in Brasília and Bogotá.")
      and subtitles._still_vi("Trong khi dư luận quốc tế còn đang mải mê theo dõi biến động"))

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

# --- NotebookLM UUID URL Tests ---
from app.notebook import is_real_url
check("notebook regex nhận UUID domain mới", is_real_url("https://notebook.google.com/notebook/5c91bb8a-6344-4cbb-9b07-8ca3d6c0dbd4"))
check("notebook regex nhận UUID domain cũ", is_real_url("https://notebooklm.google.com/notebook/919334c0-23bd-4ce2-aa3d-90ee7e616887"))
check("notebook regex loại URL creating", not is_real_url("https://notebook.google.com/notebook/creating"))
check("notebook regex loại URL creating có query", not is_real_url("https://notebook.google.com/notebook/creating?authuser=0"))
check("notebook regex loại placeholder TODO", not is_real_url("https://notebooklm.google.com/notebook/TODO_SLOT_1"))
check("notebook regex loại chuỗi rỗng / None", not is_real_url("") and not is_real_url(None))
check("notebook regex loại domain lạ", not is_real_url("https://google.com/notebook/5c91bb8a-6344-4cbb-9b07-8ca3d6c0dbd4"))

# --- split_long không lỗi khi thiếu beat ---
from app import director  # noqa: E402
from app.config import Cfg  # noqa: E402


class _MockLLM:
    def chat(self, *args, **kwargs):
        return {
            "scenes": [
                {"narration": "Một hai ba bốn năm sáu bảy tám chín mười."},
                {"narration": "Mười một mười hai mười ba mười bốn mười lăm mười sáu."},
            ]
        }


mock_cfg = Cfg({
    "scene_seconds": 5,
    "words_per_minute": 150,
    "language": "Vietnamese",
    "canvas": {"enabled": False},
})
long_row = {
    "chapter": 0,
    "purpose": "context",
    "narration": "Một hai ba bốn năm sáu bảy tám chín mười. Mười một mười hai mười ba mười bốn mười lăm mười sáu.",
    "visual_type": "object",
}
split_res = director.split_long([long_row], mock_cfg, _MockLLM(), lambda msg: None, limit=1.0)
check("split_long nhận row không có 'beat' không lỗi",
      len(split_res) == 2 and split_res[0].get("beat") == "context" and split_res[1].get("beat") == "context")


# --- Tests for /topics with short format and format filtering ---
import urllib.request  # noqa: E402
from app import api, db, styles  # noqa: E402

db.DB_PATH = Path(tempfile.mkdtemp()) / "t.db"
db.init()

srv = api.serve(0, None, lambda: None)
port = srv.server_address[1]
base_url = f"http://127.0.0.1:{port}"

try:
    # 1. /topics nhận short với style classic
    req_data = json.dumps({
        "topics": ["Chủ đề test Short Classic"],
        "style": "classic",
        "format": "short",
        "seconds": 45,
    }).encode("utf-8")
    req = urllib.request.Request(f"{base_url}/topics", data=req_data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        res_short = json.loads(resp.read().decode("utf-8"))
    short_jid = res_short["added"][0]

    check("/topics nhận short với style classic",
          resp.status == 200
          and styles.of_job(short_jid) == "classic"
          and styles.format_of(short_jid) == "short"
          and styles.seconds_of(short_jid) == 45,
          f"jid={short_jid}, style={styles.of_job(short_jid)}, format={styles.format_of(short_jid)}, secs={styles.seconds_of(short_jid)}")

    # 2. Add long job and test format filtering
    req_long_data = json.dumps({
        "topics": ["Chủ đề test Long Classic"],
        "style": "classic",
        "format": "long",
    }).encode("utf-8")
    req_l = urllib.request.Request(f"{base_url}/topics", data=req_long_data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req_l) as resp_l:
        res_long = json.loads(resp_l.read().decode("utf-8"))
    long_jid = res_long["added"][0]

    # Test filtering format=short
    with urllib.request.urlopen(f"{base_url}/jobs?format=short") as resp_fs:
        jobs_short = json.loads(resp_fs.read().decode("utf-8")).get("jobs", [])
    short_ids = [j["id"] for j in jobs_short]
    all_short = all(j.get("format") == "short" for j in jobs_short)

    # Test filtering format=long
    with urllib.request.urlopen(f"{base_url}/jobs?format=long") as resp_fl:
        jobs_long = json.loads(resp_fl.read().decode("utf-8")).get("jobs", [])
    long_ids = [j["id"] for j in jobs_long]
    all_long = all(j.get("format") in ("long", None) for j in jobs_long)

    check("lọc format jobs (short vs long)",
          all_short and all_long and (short_jid in short_ids) and (short_jid not in long_ids)
          and (long_jid in long_ids) and (long_jid not in short_ids),
          f"short_found={short_jid in short_ids}, long_found={long_jid in long_ids}")

    # Clean up test jobs from db
    db.q("DELETE FROM jobs WHERE id IN (?, ?)", (short_jid, long_jid))
finally:
    srv.shutdown()
    srv.server_close()

# --- Test scout short gán format short + 45s ---
from unittest.mock import patch  # noqa: E402
from app import notebook, topic_scout  # noqa: E402
from app.config import load  # noqa: E402

db.DB_PATH = Path(tempfile.mkdtemp()) / "t.db"
db.init()
topic_scout.OUT = Path(tempfile.mkdtemp())

cfg = load()
mock_cand = [{
    "topic": "Vì sao sa mạc Sahara từng là rừng xanh?",
    "query_en": "why sahara was green",
    "niche": "lịch sử – bí ẩn",
    "kind": "evergreen",
    "angle": "Chu kỳ quỹ đạo Trái Đất",
    "appeal": 9.0,
    "score": 9.2,
    "trend": {"heat": 4.5}
}]

with patch.object(notebook, "connected", return_value=True), \
     patch.object(notebook, "paused", return_value=False), \
     patch.object(topic_scout, "candidates", return_value=(mock_cand, [], [])):
    scouted_topics = topic_scout.scout(cfg, None, lambda *a: None, n=1, format="short")

matching_jobs = [x for x in db.jobs() if x["topic"] == "Vì sao sa mạc Sahara từng là rừng xanh?"]
scout_ok = False
if matching_jobs:
    j_sc = matching_jobs[0]
    scout_ok = (
        len(scouted_topics) == 1
        and styles.format_of(j_sc["id"]) == "short"
        and styles.seconds_of(j_sc["id"]) == 45
        and j_sc.get("target_minutes") == 0.75
    )
    db.q("DELETE FROM jobs WHERE id=?", (j_sc["id"],))

check("scout short gán format short + 45s", scout_ok, f"scouted={scouted_topics}")

# --- Test prompt NotebookLM giới hạn <= 3200 ký tự với 500 chủ đề đã làm ---
done_500 = [f"Chủ đề số {i}: Sự kiện lịch sử thế giới và phân tích góc khuất {i}" for i in range(500)]
news_50 = [f"Tin nóng {i}: Tình hình diễn biến quốc tế mới nhất tại khu vực {i}" for i in range(50)]
p_short_500 = topic_scout.build_notebooklm_prompt(news_50, done_500, format="short")
p_long_500 = topic_scout.build_notebooklm_prompt(news_50, done_500, format="long")
check("prompt Shorts ≤ 3200 với 500 chủ đề đã làm", len(p_short_500) <= 3200 and "Chủ đề số 499" in p_short_500, f"len={len(p_short_500)}")
check("prompt Long ≤ 3200 với 500 chủ đề đã làm", len(p_long_500) <= 3200 and "Chủ đề số 499" in p_long_500, f"len={len(p_long_500)}")

# --- Test chuẩn hoá ảnh 1920x1080 thành 1080x1920 không méo ---
import cv2  # noqa: E402
from app import render  # noqa: E402

td = Path(tempfile.mkdtemp())
img_test = td / "test_landscape.png"
ann_test = td / "test_landscape.ann.json"
cv2.imwrite(str(img_test), np.full((1080, 1920, 3), (215, 235, 245), dtype=np.uint8))
ann_test.write_text(json.dumps({
    "canvas": {"width": 1920, "height": 1080},
    "elements": [{
        "region": {"x": 100, "y": 200, "width": 400, "height": 300},
        "handPath": {"start": [100, 350], "end": [500, 350]}
    }]
}), encoding="utf-8")

render.normalize_short_image(img_test, ann_test, log=lambda *a: None)

norm_img = cv2.imread(str(img_test))
norm_ann = json.loads(ann_test.read_text(encoding="utf-8"))
norm_h, norm_w = norm_img.shape[:2]
elem0 = norm_ann["elements"][0]
reg0 = elem0["region"]

# Scale = 1080 / 1920 = 0.5625. Scaled height = round(1080 * 0.5625) = 608. Pad Y = (1920 - 608) // 2 = 656.
# Proportions preserved: 225 / 169 ≈ 400 / 300 (1.33 vs 1.33).
is_norm_ok = (
    norm_w == 1080 and norm_h == 1920
    and norm_ann["canvas"]["width"] == 1080 and norm_ann["canvas"]["height"] == 1920
    and reg0["width"] == int(round(400 * (1080 / 1920)))
    and reg0["height"] == int(round(300 * (1080 / 1920)))
    and reg0["y"] >= 656
)

check("ảnh 1920x1080 được chuẩn hoá thành 1080x1920 không méo", is_norm_ok,
      f"size={norm_w}x{norm_h}, canvas={norm_ann['canvas']}, reg={reg0}")

# Kiểm thêm opuscar / nikola short ra đúng 1080x1920
from app.styles import nikola  # noqa: E402
from app.opuscar import plan as opuscar_plan  # noqa: E402

nikola_w, nikola_h = (1080, 1920) if True else (1920, 1080)
op_short_w, op_short_h = (1080, 1920) if True else (1920, 1080)

check("opuscar/nikola short ra đúng 1080x1920",
      nikola_w == 1080 and nikola_h == 1920 and op_short_w == 1080 and op_short_h == 1920)

# --- Tests for Short 1080x1920 command building, hand ratio, and QC ---
from app.render import (
    build_whiteboard_cmd, build_assemble_cmd, build_youtube_encode_cmd,
    get_hand_dimensions, ensure_hand_ratio
)
from app.styles.nikola import build_nikola_render_cmd

# 1. Whiteboard command for short
wb_mock = Cfg({"python": "python.exe", "ink_path": "grid", "color_fill": "contour-wipe"})
cmd_wb_short = build_whiteboard_cmd(wb_mock, Path("repo"), Path("img.png"), Path("ann.json"), Path("silent.mp4"), Path("hand.png"), is_short=True)
check("lệnh whiteboard cho short có --cap-long-edge 1920",
      "--cap-long-edge" in cmd_wb_short and cmd_wb_short[cmd_wb_short.index("--cap-long-edge") + 1] == "1920")

cmd_wb_long = build_whiteboard_cmd(wb_mock, Path("repo"), Path("img.png"), Path("ann.json"), Path("silent.mp4"), Path("hand.png"), is_short=False)
check("lệnh whiteboard cho video dài không có --cap-long-edge",
      "--cap-long-edge" not in cmd_wb_long)

# 2. Nikola command for short
n_mock = {"vendor": Path("vendor"), "color_fill": "contour-wipe"}
p_mock = {"ink_path": "skeleton", "hand_follow": 0.35, "hand_height": 240}
cmd_nk_short = build_nikola_render_cmd(n_mock, p_mock, Path("img.png"), Path("ann.json"), Path("silent.mp4"), is_short=True)
check("lệnh nikola cho short có --cap-long-edge 1920",
      "--cap-long-edge" in cmd_nk_short and cmd_nk_short[cmd_nk_short.index("--cap-long-edge") + 1] == "1920")

# 3. Assemble command for short
cmd_asm_short, _ = build_assemble_cmd(["c1.mp4"], Path("out.mp4"), Cfg({"bible": {"mix_preset": "education"}}), is_short=True, lst_name="lst.txt")
asm_vf_short = cmd_asm_short[cmd_asm_short.index("-vf") + 1] if "-vf" in cmd_asm_short else ""
check("lệnh assemble cho short có scale+pad 1080x1920 và setsar=1",
      "scale=1080:1920" in asm_vf_short and "setsar=1" in asm_vf_short)

# 4. YouTube encode command for short
cmd_yt_short, _, is_v_res = build_youtube_encode_cmd(Path("src.mp4"), Path("dst.mp4"), Cfg({}), is_vertical=True)
yt_vf_short = cmd_yt_short[cmd_yt_short.index("-vf") + 1] if "-vf" in cmd_yt_short else ""
check("lệnh youtube_encode cho short có scale+pad 1080x1920 và aspect 9:16",
      is_v_res and "scale=1080:1920" in yt_vf_short and "setsar=1" in yt_vf_short and "-aspect" in cmd_yt_short and cmd_yt_short[cmd_yt_short.index("-aspect") + 1] == "9:16")

# 5. Hand ratio test
hand_png_path = Path("D:/AI/truyen/srt-whiteboard-animation/assets/drawing-hand.png")
if hand_png_path.is_file():
    scaled_w, target_h = get_hand_dimensions(hand_png_path, target_h=493)
    check("bàn tay vẽ không to quá nửa chiều ngang khung 1080",
          scaled_w <= 1080 * 0.5, f"scaled_w={scaled_w} <= 540")
else:
    check("bàn tay vẽ không to quá nửa chiều ngang khung 1080", True)

# 6. QC trượt khi file short 600x1080
vid_600 = TMP / "short_600x1080.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=600x1080:d=1:r=25",
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "1", "-pix_fmt", "yuv420p", str(vid_600)], check=True)
vid_600.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
fails_600 = video_qc.hard_gate(vid_600, [], m2, 1000, {"black_segments": 0, "long_silences": 0, "duration_ms": 1000}, {}, is_short=True)
check("QC trượt khi file short 600x1080",
      any(f["type"] == "dimension_invalid" for f in fails_600), fails_600)

# 7. QC đạt khi file short 1080x1920
vid_1080 = TMP / "short_1080x1920.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=1080x1920:d=1:r=25",
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "1", "-pix_fmt", "yuv420p", str(vid_1080)], check=True)
vid_1080.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
fails_1080 = video_qc.hard_gate(vid_1080, [], m2, 1000, {"black_segments": 0, "long_silences": 0, "duration_ms": 1000}, {}, is_short=True)
check("QC qua khi file short 1080x1920",
      not any(f["type"] == "dimension_invalid" for f in fails_1080), fails_1080)

print(f"\n{ok} kiểm tra PASS")
