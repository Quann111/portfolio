"""Test API Flow Bridge bằng 'extension giả' (server riêng cổng 8799, DB riêng):
  .venv\\Scripts\\python.exe tests\\test_flowbridge_api.py"""
import base64
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import api, config, db, flowbridge, packager  # noqa: E402

RUN = ROOT / "data" / "flowbridge_test"
RUN.mkdir(parents=True, exist_ok=True)
db.DB_PATH = RUN / "test.db"
db.DB_PATH.unlink(missing_ok=True)
db.init()
packager.PROJECTS = RUN / "projects"          # không ghi vào projects/ thật của app
base = config.load
config.load = lambda: {**base(), "autoflow": {**(base().get("autoflow") or {}), "exchange_dir": str(RUN / "exchange")}}
api.load = config.load


class W:
    running = False
    def start(self): pass
    def stop(self): pass


api.serve(8799, W(), lambda: None)
A = "http://127.0.0.1:8799"
ok = 0


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""))
    ok += bool(cond)


jid = db.add_topic("test")
flowbridge.enqueue(jid, 1, [{"idx": 0, "uid": "u0", "prompt": "a sun"}, {"idx": 1, "uid": "u1", "prompt": "a tree"}])
r = requests.get(A + "/flow/next?v=1&tab=1").json()
check("extension nhận task 1", r["task"] and r["task"]["prompt"] == "a sun", r)
check("heartbeat → app thấy extension kết nối", requests.get(A + "/flow/status").json()["extension_connected"])
img = np.full((768, 1344, 3), (215, 235, 245), np.uint8)
cv2.circle(img, (400, 380), 150, (40, 40, 40), 6)
b64 = "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", img)[1].tobytes()).decode()
r2 = requests.post(A + f"/flow/tasks/{r['task']['id']}/result", json={"data": b64, "mime": "image/jpeg"}).json()
check("gửi ảnh về → lưu PNG vào received", r2.get("ok") and Path(r2["saved"]).suffix == ".png"
      and Path(r2["saved"]).exists(), r2)
check("worker thấy ảnh mới cần QC", [t["uid"] for t in flowbridge.done_unprocessed(jid)] == ["u0"])
t2 = requests.get(A + "/flow/next").json()["task"]
requests.post(A + f"/flow/tasks/{t2['id']}/error", json={"message": "Flow hiện captcha", "pause": True})
check("captcha (extension cũ) → không dừng chung, task về hàng chờ", not requests.get(A + "/flow/status").json()["paused"])
requests.post(A + "/flow/resume", data="{}")
check("Tiếp tục → hết tạm dừng", not requests.get(A + "/flow/status").json()["paused"])
flowbridge.enqueue(jid, 2, [{"idx": 1, "uid": "u1", "prompt": "a tree v2"}])
t3 = requests.get(A + "/flow/next").json()["task"]
requests.post(A + f"/flow/tasks/{t3['id']}/error", json={"message": "quá 240s"})
t4 = requests.get(A + "/flow/next").json()["task"]
check("lỗi thường → tự xếp lại task (thử lại)", t4 and t4["id"] == t3["id"], t4)
bad = requests.post(A + f"/flow/tasks/{t4['id']}/result", json={"data": "data:image/png;base64,AAAA"})
check("ảnh hỏng bị từ chối (HTTP 400)", bad.status_code == 400)
db.x("UPDATE flow_tasks SET status='claimed', claimed_at=? WHERE id=?", (time.time() - 700, t4["id"]))
t5 = requests.get(A + "/flow/next").json()["task"]
check("task treo > 10 phút được giao lại", t5 and t5["id"] == t4["id"])
# ---- nhiều Chrome profile song song
jid2 = db.add_topic("song song")
flowbridge.enqueue(jid2, 1, [{"idx": i, "uid": f"m{i}", "prompt": f"p{i}"} for i in range(5)])
db.x("UPDATE flow_tasks SET status='cancelled' WHERE job_id=?", (jid,))
got = [requests.get(A + f"/flow/next?v=1.2.0&tab=1&w=pA{k}&name=P{k}").json()["task"] for k in range(3)]
ids = [g["id"] for g in got if g]
check("3 profile nhận 3 task KHÁC nhau cùng lúc", len(ids) == 3 and len(set(ids)) == 3, ids)
st = requests.get(A + "/flow/status").json()
check("status thấy 3 profile đang chạy", st["running"] == 3, st.get("workers"))
requests.post(A + f"/flow/tasks/{got[0]['id']}/error", json={"message": "captcha", "pause": True, "w": "pA0"})
r0 = requests.get(A + "/flow/next?v=1.2.0&tab=1&w=pA0").json()
r1 = requests.get(A + "/flow/next?v=1.2.0&tab=1&w=pA1").json()
check("captcha ở 1 profile → chỉ profile đó dừng", r0["task"] is None and r0["paused"] and r1["task"]
      and not requests.get(A + "/flow/status").json()["paused"], (r0, r1))
check("task của profile bị dừng trả về hàng chờ cho profile khác",
      db.q("SELECT status FROM flow_tasks WHERE id=?", (got[0]["id"],))[0]["status"] in ("queued", "claimed"))
saved = [requests.post(A + f"/flow/tasks/{t['id']}/result", json={"data": b64, "mime": "image/jpeg", "w": f"pA{k}"}
                       ).json() for k, t in ((1, got[1]), (2, got[2]))]
check("ảnh của nhiều profile gom về đúng thư mục job",
      all(str(packager.folder_of(jid2)) in s["saved"] or f"job_{jid2}" in s["saved"] for s in saved), saved)
requests.post(A + "/flow/resume", json={"w": "pA0"})
check("Tiếp tục profile → profile đó chạy lại", requests.get(A + "/flow/next?v=1.2.0&tab=1&w=pA0").json()["task"])
print(f"\n{ok}/15 PASS")
