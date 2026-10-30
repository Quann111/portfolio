"""Test bộ đăng YouTube: làm sạch dữ liệu, mô tả đủ khung + mốc chương, lưu thẳng vào thư mục dự án của job,
thumbnail in chữ tiếng Việt (1280×720, < 2 MB), task thumb_* không bị nhận nhầm, Flow trả ảnh nền → thumbnail. DB tạm."""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import db  # noqa: E402

TMP = Path(tempfile.mkdtemp())
db.DB_PATH = TMP / "t.db"
db.init()
from app import flowbridge, packager, thumbnail, youtube  # noqa: E402
from app.config import load  # noqa: E402

packager.PROJECTS = TMP / "projects"
cfg = load()
ok = fail = 0


def t(name, cond):
    global ok, fail
    ok += bool(cond)
    fail += not cond
    print(("PASS " if cond else "FAIL ") + name)


one = {"titles": ["Xe điện có thật sự rẻ hơn Sirius?"] * 5, "thumb_text": ["xe điện khó thắng sirius?"],
       "thumb_scene": "red motorbike", "hook": "Sirius 20 triệu còn đáng mua?", "intro": "Trong video này…",
       "key_points": ["a?", "b?", "c?", "d?", "e?"], "conclusion": "Không có lựa chọn đúng cho tất cả.",
       "comment_question": "Bạn chọn xe nào?", "hashtags": ["#Xe Dien", "Sirius"], "tags": ["xe điện", "sirius"]}
c = youtube._clean(one)
t("thumb_text viết hoa", c["thumb_text"][0] == "XE ĐIỆN KHÓ THẮNG SIRIUS?")
t("hashtag chuẩn", c["hashtags"] == ["#XeDien", "#Sirius"])

jid = db.add_topic("Xe điện vs Sirius")
db.update_job(jid, outline=json.dumps({"title": "x", "chapters": [{"title": "Mở đầu"}, {"title": "Chi phí"},
                                                                  {"title": "Kết luận"}]}))
db.insert_scenes(jid, [{"chapter": ch, "narration": "n", "image_prompt": "v"} for ch in (0, 0, 1, 2)])
data = {"vi": c, "en": youtube._clean(one), "by": "test", "topic": "Xe điện vs Sirius"}
youtube.save(jid, data, cfg, [0, 30000, 65000, 130000])
f = packager.folder_of(jid) / "youtube"
t("lưu thẳng vào thư mục dự án của job", f.parent.name.startswith(f"{jid:04}_") and (f / "youtube.json").exists())
d = (f / "description_vi.txt").read_text(encoding="utf-8")
order = ["Sirius 20 triệu", "Trong video này", "🔎", "Không có lựa chọn", "💬", "⏱", "🔔", "#XeDien", "hội viên",
         "thumbnail mang tính minh hoạ"]
pos = [d.find(k) for k in order]
t("mô tả đủ khung, đúng thứ tự", all(p >= 0 for p in pos) and pos == sorted(pos))
t("mốc chương thật 00:00/01:05/02:10", "00:00 Mở đầu" in d and "01:05 Chi phí" in d and "02:10 Kết luận" in d)
t("có bản EN + title + tags", all((f / n).exists() for n in ("description_en.txt", "title_vi.txt", "title_en.txt",
                                                            "tags.txt", "titles_vi.txt")))
t("tiêu đề ≤ 100 ký tự", all(len(x) <= 100 for x in c["titles"]))

# thumbnail
bg = TMP / "bg.png"
a = np.zeros((900, 1600, 3), np.uint8)
a[:, :800] = (30, 120, 220)
a[200:700, 1000:1400] = (250, 40, 40)
Image.fromarray(a).save(bg)
out = thumbnail.compose(bg, "Xe điện khó thắng Sirius?", TMP / "th.jpg")
im = Image.open(out)
t("thumbnail 1280×720, < 2 MB", im.size == (1280, 720) and out.stat().st_size < 2_000_000)
t("chữ đặt ở nửa ít chi tiết (trái)", thumbnail.text_side(thumbnail._cover(Image.open(bg))) == "left")
t("font có đủ dấu tiếng Việt", Path(thumbnail.font_path()).exists())

# task thumb_* đi qua Flow, không lẫn vào ảnh cảnh, không bị huỷ theo job
flowbridge.enqueue(jid, 901, [{"idx": -1, "uid": youtube.thumb_uid(jid, 0), "prompt": thumbnail.bg_prompt("x")}])
flowbridge.cancel_job(jid)
st = db.q("SELECT status FROM flow_tasks WHERE uid=?", (youtube.thumb_uid(jid, 0),))[0]["status"]
t("cancel_job không huỷ task thumbnail", st == "queued")
task = flowbridge.claim_next("w1")
import base64, io  # noqa: E401,E402
buf = io.BytesIO()
Image.fromarray(np.full((720, 1280, 3), 20, np.uint8)).save(buf, "PNG")     # ảnh nền tối – không bị coi là UI
p = flowbridge.complete(cfg, task["id"], base64.b64encode(buf.getvalue()).decode())
t("ảnh nền tối của thumbnail được nhận", p.exists())
cfg2 = load()
cfg2["youtube"]["thumb_variants"] = 1
outs = youtube.make_thumbnails(jid, data, cfg2, log=lambda *a: None, llm=None, timeout_min=0.3)
t("Flow trả ảnh nền → thumbnail.jpg + phương án", (f / "thumbnail.jpg").exists() and len(outs) == 1)
v = youtube.view(jid)
t("view trả dữ liệu cho dashboard", v["data"] and v["thumbs"] and v["description"]["en"])

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
