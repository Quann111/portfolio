"""Giả lập Auto Flow end-to-end (không cần Chrome): .venv\\Scripts\\python.exe tests\\test_autoflow.py
Tạo job ở bước ảnh (đã có giọng), chạy worker với provider=autoflow trên DB/thư mục riêng; luồng giả lập
đọc prompts.txt app xuất ra rồi thả ảnh vào inbox với tên kiểu Auto Flow ({row}_{prompt}_{slot}.png)."""
import json
import shutil
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import autoflow, config, db, worker  # noqa: E402

RUN = ROOT / "data" / "autoflow_test"
shutil.rmtree(RUN, ignore_errors=True)
INBOX = RUN / "inbox"
INBOX.mkdir(parents=True)

# ---- parse tên file
cases = {"12_a_fox_on_hill_1": (12, 1), "3_2": (3, 2), "007": (7, 1), "J5R2_12": (12, 1),
         "2026-10-26_4_sun": (4, 1), "1761456000123_sun_1": (None, 1), "5_year_2025_2": (5, 2)}
for name, want in cases.items():
    got = autoflow.parse_name(name)
    print(("PASS " if got == want else "FAIL ") + f"parse {name} → {got} (muốn {want})")

# ---- job giả: 3 cảnh đã có giọng
db.DB_PATH = RUN / "test.db"
db.init()
db.kv_set("mode", "auto")
jid = db.add_topic("Vì sao bầu trời có màu xanh")
scenes = [
    {"chapter": 0, "narration": "Mặt trời chiếu ánh sáng trắng xuống Trái Đất mỗi ngày.",
     "image_prompt": "a small sun on the left sending rays to a round earth on the right",
     "visual_concept": "sunlight travels to earth", "drawing_order": ["sun", "earth"], "camera": "static"},
    {"chapter": 0, "narration": "Ánh sáng đi qua lớp không khí và bị tán xạ khắp bầu trời.",
     "image_prompt": "a light ray hitting tiny air molecules and scattering blue light in many directions",
     "visual_concept": "light scattering", "drawing_order": ["light ray", "molecules"], "camera": "static"},
    {"chapter": 0, "narration": "Vì vậy khi nhìn lên, chúng ta thấy bầu trời màu xanh.",
     "image_prompt": "a child looking up at the sky with a few clouds",
     "visual_concept": "people see a blue sky", "drawing_order": ["child", "sky"], "camera": "static"},
]
db.insert_scenes(jid, scenes)
work = RUN / "work" / f"job_{jid}"
work.mkdir(parents=True)
for i in range(3):
    w = work / f"voice_{i}.wav"
    t = np.arange(24000 * 6) / 24000
    sf.write(w, (0.2 * np.sin(2 * np.pi * 200 * t) * (np.sin(2 * np.pi * 0.7 * t) > 0)).astype(np.float32), 24000)
    d = db.scene_data(db.scenes(jid)[i])
    d["speech_ms"] = 5600
    db.update_scene(jid, i, audio=str(w), duration_ms=6000, data=json.dumps(d, ensure_ascii=False))
db.update_job(jid, stage="image", facts="[]",
              outline=json.dumps({"title": "test", "chapters": [{"beat": "explain", "title": "x"}],
                                  "reviews": [], "bible": {}}))

base = config.load


def load_test():
    c = base()
    c["work_dir"] = str(RUN / "work")
    c["output_dir"] = str(RUN / "output")
    c["keep_work"] = True
    c["maintenance_hour"] = 99
    c["image"]["provider"] = "autoflow"
    c["autoflow"] = {**(c.get("autoflow") or {}), "inbox_dir": str(INBOX), "exchange_dir": str(RUN / "exchange"),
                     "poll_seconds": 2, "wait_minutes": 30, "fallback_local": False}
    return c


worker.load = load_test
config.load = load_test

# ảnh mẫu: lấy ảnh FLUX đã có từ golden run (whiteboard thật)
SAMPLES = ROOT / "data" / "autoflow_samples"
SAMPLES.mkdir(parents=True, exist_ok=True)
samples = sorted(SAMPLES.glob("*.png"))
if len(samples) < 3:
    from app import image
    for i, sc in enumerate(scenes):
        out = SAMPLES / f"sample_{i}.png"
        image.generate(image.build_prompt(sc, {}, load_test()), out, load_test(), seed=100 + i)
    samples = sorted(SAMPLES.glob("*.png"))


def fake_autoflow():
    """Đợi prompts.txt mỗi lượt rồi thả ảnh vào inbox như Auto Flow (Subfolder per task)."""
    done_rounds = set()
    while True:
        st = autoflow.state(jid)
        if st and st["round"] not in done_rounds:
            done_rounds.add(st["round"])
            lines = Path(st["txt"]).read_text(encoding="utf-8").splitlines()
            print(f"[fake AutoFlow] lượt {st['round']}: {len(lines)} prompt")
            time.sleep(3)
            sub = INBOX / f"task_{st['round']}"
            sub.mkdir(exist_ok=True)
            for row, line in enumerate(lines, 1):
                # giống Auto Flow: chỉ giữ chữ/số/khoảng trắng, tối đa 30 ký tự
                import re
                slug = re.sub(r"[^\w ]", "", line)[:30].strip().replace(" ", "_") or "prompt"
                shutil.copyfile(samples[(row - 1) % len(samples)], sub / f"{row}_{slug}_1.png")
        if db.q("SELECT status FROM jobs WHERE id=?", (jid,))[0]["status"] in ("done", "failed"):
            return
        time.sleep(2)


t0 = time.time()
w = worker.Worker(lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True), lambda *a: None, lambda: None)
threading.Thread(target=fake_autoflow, daemon=True).start()
w.start()
while True:
    time.sleep(5)
    j = db.q("SELECT * FROM jobs WHERE id=?", (jid,))[0]
    if j["status"] in ("done", "failed") or (j["attempts"] or 0) >= 2:
        break
w.stop()
w.thread.join(120)
j = db.q("SELECT * FROM jobs WHERE id=?", (jid,))[0]
print("\nKẾT QUẢ:", j["status"], j["stage"], j["output"], (j["error"] or "")[:300])
ex = RUN / "exchange" / f"job_{jid}"
print("exchange:", sorted(str(p.relative_to(ex)) for p in ex.rglob("*") if p.is_file()))
print("report:", (ex / "report.csv").read_text(encoding="utf-8-sig") if (ex / "report.csv").exists() else "-")
