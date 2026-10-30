"""Đổi phong cách ảnh giữa chừng cho một job đang ở bước ảnh: viết lại phần hình (không đổi lời) cho các cảnh
CHƯA có ảnh đạt, huỷ các task Flow đang chờ của những cảnh đó để worker xếp lại bằng prompt mới.
Chạy khi app đã tắt:  .venv\\Scripts\\python.exe tests\\enrich_job.py <job_id>"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import db, director  # noqa: E402
from app.config import load  # noqa: E402
from app.llm import LLM  # noqa: E402

jid = int(sys.argv[1])
cfg = load()
llm = LLM(cfg, print)
llm.role = "text"
job = db.q("SELECT * FROM jobs WHERE id=?", (jid,))[0]
ol = json.loads(job["outline"] or "{}")
sc = db.scenes(jid)
rows = [{**db.scene_data(s), "narration": s["narration"]} for s in sc]
todo = [i for i, s in enumerate(sc) if not (s.get("image") and Path(s["image"]).exists())]
print(f"Job {jid}: {len(todo)}/{len(sc)} cảnh chưa có ảnh đạt → làm giàu hình")
n = director.enrich_visuals(rows, todo, ol, cfg, llm, print)
for i in todo:
    d = {k: v for k, v in rows[i].items() if k not in db.SCENE_COLS}
    db.update_scene(jid, sc[i]["idx"], visual=rows[i].get("image_prompt", ""),
                    data=json.dumps(d, ensure_ascii=False), qc=None, image_tries=0)
uids = [db.scene_uid(jid, sc[i]) for i in todo]
for u in uids:
    db.x("UPDATE flow_tasks SET status='cancelled' WHERE job_id=? AND uid=? AND status IN ('queued','claimed')",
         (jid, u))
print(f"Đã viết lại {n} cảnh, huỷ task Flow cũ của {len(uids)} cảnh")
for i in todo[:3]:
    print("-", sc[i]["narration"][:90], "\n  →", rows[i]["image_prompt"])
