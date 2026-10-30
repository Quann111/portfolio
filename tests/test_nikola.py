"""Test kiểu Nikola: ghép màn, prompt hai đảo, QC bố cục, annotation 2 vùng (+ kiểm bằng script của repo),
khoá kiểu theo job, ASS chữ khoá, render một màn thật bằng engine Nikola và checklist khung. DB tạm."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import db  # noqa: E402

TMP = Path(tempfile.mkdtemp())
db.DB_PATH = TMP / "t.db"
db.init()
from app import styles  # noqa: E402
from app.config import load  # noqa: E402
from app.styles import nikola  # noqa: E402

cfg = load()
ok = fail = 0


def t(name, cond):
    global ok, fail
    ok += bool(cond)
    fail += not cond
    print(("PASS " if cond else "FAIL ") + name)


def sc(idx, ch, route=None):
    return {"idx": idx, "chapter": ch, "data": json.dumps({"route": route} if route else {})}


# ghép màn
t("chẵn → các cặp", nikola.acts([sc(0, 0), sc(1, 0), sc(2, 0), sc(3, 0)]) == [[0, 1], [2, 3]])
t("lẻ → màn một đảo cuối", nikola.acts([sc(0, 0), sc(1, 0), sc(2, 0)]) == [[0, 1], [2]])
t("một cảnh", nikola.acts([sc(0, 0)]) == [[0]])
t("không ghép khác chương", nikola.acts([sc(0, 0), sc(1, 1), sc(2, 1)]) == [[0], [1, 2]])
s4 = [sc(0, 0), sc(1, 0), sc(2, 0)]
t("đảo phải", nikola.is_partner(s4, 1) and not nikola.is_partner(s4, 0) and not nikola.is_partner(s4, 2))

# prompt
p = nikola.pair_prompt({"image_prompt": "a girl plants a seed"}, {"image_prompt": "a tall tree"}, {})
t("prompt hai đảo đủ luật", all(k in p for k in ("TWO separate", "gutter", "bottom 18%", "LEFT ISLAND",
                                                 "RIGHT ISLAND", "No text")))
t("prompt một đảo", "ONE single scene" in nikola.pair_prompt({"image_prompt": "x " * 10}, None, {}))


# ảnh mẫu
def make(path, two=True, bridge=False, bottom=False):
    img = np.full((1080, 1920, 3), 255, np.uint8)
    if two:
        cv2.circle(img, (450, 400), 220, (40, 40, 40), 8)
        cv2.rectangle(img, (1250, 200), (1650, 650), (40, 40, 40), 8)
    else:
        cv2.circle(img, (960, 400), 250, (40, 40, 40), 8)
    if bridge:
        cv2.line(img, (600, 450), (1300, 450), (40, 40, 40), 6)
    if bottom:
        cv2.rectangle(img, (100, 950), (1800, 1060), (40, 40, 40), -1)
    cv2.imwrite(str(path), img)
    return path


good = make(TMP / "good.png")
t("QC bố cục: ảnh chuẩn đạt", nikola.layout_check(good, True)["pass"])
t("QC: nét nối hai đảo bị bắt", not nikola.layout_check(make(TMP / "br.png", bridge=True), True)["pass"])
t("QC: đáy có mực bị bắt", not nikola.layout_check(make(TMP / "bt.png", bottom=True), True)["pass"])
t("QC: thiếu một đảo bị bắt", not nikola.layout_check(make(TMP / "one.png", two=False), True)["pass"])

# annotation
ann_p = TMP / "act.annotation.json"
ann = nikola.build_annotation(good, ann_p, "act-001", [{"narration": "a", "audio_ms": 5000, "lead_ms": 0},
                                                      {"narration": "b", "audio_ms": 4000, "lead_ms": 200}])
e1, e2 = ann["elements"]
r1, r2 = e1["region"], e2["region"]
t("hai vùng không chồng nhau", r1["x"] + r1["width"] < r2["x"])
t("tổng thời gian = tổng giọng", ann["sceneDurationMs"] == 9000)
t("đảo trái dừng ≥0.5s trước đảo phải", e1["reveal"]["startMs"] + e1["reveal"]["durationMs"] + 500 <= 5000)
t("đảo phải bắt đầu theo giọng cảnh 2", e2["reveal"]["startMs"] >= 5000)
okr, why = nikola.check_regions(cfg, good, ann_p, TMP / "reg.json")
t("annotation_schema + check_drawable_regions của repo đạt", okr)
if not okr:
    print("   ", why)

# khoá kiểu theo job
j = db.add_topic("thử nikola")
t("job mới theo mặc định", styles.of_job(j, cfg) == styles.default(cfg))
styles.lock(j, "nikola", cfg)
t("khoá nikola", styles.of_job(j, cfg) == "nikola")
styles.lock(j, "classic", cfg)
t("đã khoá thì không đổi", styles.of_job(j, cfg) == "nikola")

# ASS
ass = TMP / "k.ass"
nikola.write_ass([(0, 1500, "xin chào", "")], [{"text": "Hai đảo", "start": 1000, "end": 3000, "x": 480, "y": 40}], ass)
txt = ass.read_text(encoding="utf-8")
t("ASS có phụ đề + chữ khoá \\kf", "Dialogue: 0," in txt and "\\kf" in txt and "Style: Key" in txt)

# render thật một màn (engine trong repo Nikola) + checklist khung
if nikola.available(cfg) is None and "--render" in sys.argv:
    wav = []
    for k, ms in enumerate((5000, 4000)):
        w = TMP / f"a{k}.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={ms / 1000}", "-ar", "48000", str(w)], check=True)
        wav.append(w)
    audio = TMP / "act.wav"
    nikola.concat_audio(wav, audio)
    clip = TMP / "act.mp4"
    nikola.render_act(cfg, good, ann_p, audio, clip)
    rep = nikola.check_act(clip, good, ann)
    t("render màn thật + checklist (khung đầu sạch, đảo phải không lộ sớm, đảo trái giữ, khung cuối khớp)",
      rep["pass"])
    print("   ", rep)
    out = TMP / "final.mp4"
    nikola.finalize(cfg, TMP, [{"video": str(clip), "image": str(good), "durationMs": ann["sceneDurationMs"]}],
                    audio, ass, out)
    v = nikola.verify_final(out, [{"video": str(clip), "image": str(good), "durationMs": ann["sceneDurationMs"]}])
    t("finalize_stroke_video + kiểm tra cuối", out.exists() and v["pass"])
    print("   ", v)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
