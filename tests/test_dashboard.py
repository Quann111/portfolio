"""Test dashboard: đồng hồ bước (pipeline), mốc 'dừng sau…', đóng gói thư mục job, xoá vào Thùng rác, API mới
(/ui, /status, /browsers, /jobs, /files không cho đọc ra ngoài). DB + thư mục tạm, cổng 8796."""
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import db  # noqa: E402

TMP = Path(tempfile.mkdtemp())
db.DB_PATH = TMP / "t.db"
db.init()
from app import api, autoflow, packager, pipeline  # noqa: E402
from app.config import load  # noqa: E402
from app.worker import Worker  # noqa: E402

packager.PROJECTS = TMP / "projects"
ok = fail = 0


def t(name, cond):
    global ok, fail
    ok += bool(cond)
    fail += not cond
    print(("PASS " if cond else "FAIL ") + name)


# ---- pipeline: thời gian cộng dồn, dừng không bị tính
jid = db.add_topic("Thử đồng hồ")
pipeline.start(jid, "research")
time.sleep(1.1)
pipeline.end(jid, "research")
pipeline.start(jid, "script")
time.sleep(0.6)
pipeline.pause_all(jid)                 # app dừng
time.sleep(1.0)                          # khoảng dừng không được cộng
pipeline.start(jid, "script")
time.sleep(0.5)
v = pipeline.view(jid)
st = {s["key"]: s for s in v["steps"]}
t("bước xong có thời gian ~1s", st["research"]["state"] == "done" and 1 <= st["research"]["active_sec"] <= 2)
t("dừng giữa chừng không cộng thời gian dừng", st["script"]["active_sec"] <= 1.6 and st["script"]["runs"] == 2)
t("tổng thời gian chạy thật < tổng kể cả chờ", v["active_sec"] <= v["wall_sec"])
t("thứ tự bước đủ 8 bước", [s["key"] for s in v["steps"]][-1] == "package" and len(v["steps"]) == 8)
pipeline.tick(jid, "image_wait", 3, 10)
t("tiến độ 'image_wait' ghi vào bước ảnh", {s["key"]: s for s in pipeline.view(jid)["steps"]}["image"]["done"] == 3)

# ---- mốc dừng
w = Worker(lambda m: None, lambda *a: None, lambda: None)
db.kv_set("stop_after", {"job_id": jid})
t("dừng sau chủ đề #id", w._stop_after_reached(jid) and not w._stop_after_reached(jid + 1))
db.kv_set("stop_after", {"count": 2})
t("dừng sau 2 chủ đề", not w._stop_after_reached(1) and w._stop_after_reached(2))
db.kv_set("stop_after", None)
t("không mốc → không dừng", not w._stop_after_reached(jid))

# ---- đóng gói + xoá
cfg = load()
cfg["autoflow"] = {**cfg.get("autoflow", {}), "exchange_dir": str(TMP / "exchange")}
ex = autoflow.job_dir(cfg, jid)
(ex / "script.md").write_text("# kịch bản", encoding="utf-8")
img = ex / "received" / "a.png"
img.parent.mkdir(parents=True)
img.write_bytes(b"\x89PNG fake")
vid = TMP / "out.mp4"
vid.write_bytes(b"video")
db.insert_scenes(jid, [{"chapter": 0, "narration": "Câu một.", "image_prompt": "p1"},
                       {"chapter": 0, "narration": "Câu hai.", "image_prompt": "p2"}])
db.update_scene(jid, 0, image=str(img))
db.update_job(jid, output=str(vid))
f = packager.package(cfg, jid, None, lambda m: None)
t("đóng gói: video, kịch bản, ảnh theo thứ tự cảnh, báo cáo",
  (f / "video" / "out.mp4").exists() and (f / "kich_ban" / "script.md").exists()
  and (f / "anh" / "canh_001.png").exists() and (f / "kich_ban" / "loi_doc.txt").exists()
  and (f / "bao_cao.html").exists() and json.loads((f / "kich_ban" / "canh.json").read_text("utf-8"))[1]["anh"] is None)

# ---- API
srv = api.serve(8796, type("W", (), {"running": False, "start": lambda s: 0, "stop": lambda s, **k: 0})(), lambda: None)
base = "http://127.0.0.1:8796"


def get(p):
    return urllib.request.urlopen(base + p, timeout=10)


t("/ui trả trang dashboard", b"AutoVideo" in get("/ui").read())
t("/jobs có thời gian + thư mục", "active_sec" in json.loads(get("/jobs").read())["jobs"][0])
t("/browsers chạy", "browsers" in json.loads(get("/browsers").read()))
t("/status chạy", "mode" in json.loads(get("/status").read()))
try:
    get("/files/../config.yaml")
    t("/files chặn đọc ra ngoài projects/exchange", False)
except urllib.error.HTTPError as e:
    t("/files chặn đọc ra ngoài projects/exchange", e.code == 404)
req = urllib.request.Request(base + "/stop_after", json.dumps({"job_id": 5}).encode(), {"Content-Type": "application/json"})
urllib.request.urlopen(req)
t("API đặt mốc dừng", db.kv_get("stop_after") == {"job_id": 5})
urllib.request.urlopen(urllib.request.Request(base + "/stop_after", method="DELETE"))
t("DELETE /stop_after huỷ mốc", db.kv_get("stop_after") is None)
a, b, c = db.add_topic("A"), db.add_topic("B"), db.add_topic("C")
urllib.request.urlopen(urllib.request.Request(base + f"/projects/{c}/move", json.dumps({"before": a}).encode(),
                                              {"Content-Type": "application/json"}))
order = [r["id"] for r in db.q("SELECT id FROM jobs WHERE status='queued' ORDER BY position")]
t("kéo thả: C lên trước A", order.index(c) < order.index(a) < order.index(b))
tm = json.loads(get(f"/projects/{jid}/timing").read())
t("timing có bước con + chỉ số", tm["steps"][0]["subs"] and "metrics" in tm and "so_canh" in tm["metrics"])
urllib.request.urlopen(urllib.request.Request(base + f"/projects/{b}", method="DELETE"))
t("DELETE /projects/{id} xoá job", not db.q("SELECT id FROM jobs WHERE id=?", (b,)))
# job mới: thư mục làm việc nằm trong thư mục riêng projects/<id>_<tên>/_lam_viec
cfg2 = load()
cfg2["autoflow"] = {**cfg2.get("autoflow", {}), "exchange_dir": str(TMP / "exchange_new")}
d = autoflow.job_dir(cfg2, a)
t("job mới làm thẳng trong projects/<id>/_lam_viec", d.name == "_lam_viec" and d.parent.parent == packager.PROJECTS)
# bước con + thời gian chờ tách khỏi chạy thật
pipeline.start(a, "image")
time.sleep(1.2)
pipeline.add_wait(a, "image", 1.0, "không có profile")
img_step = {s["key"]: s for s in pipeline.view(a)["steps"]}["image"]
t("thời gian chờ tách khỏi chạy thật", img_step["wait_sec"] == 1 and img_step["active_sec"] <= 1
  and img_step["subs"][0]["state"] == "running")
srv.shutdown()

r = packager.delete_job(cfg, jid)
t("xoá job: thư mục vào Thùng rác + DB sạch", not f.exists() and not db.q("SELECT id FROM jobs WHERE id=?", (jid,))
  and not db.scenes(jid) and r["trash"])
print(f"\n{ok} PASS, {fail} FAIL")
sys.exit(1 if fail else 0)
