"""Test Notebook Bridge không cần Chrome: parse câu trả lời, chia câu, hàng đợi + API với extension giả.
Dùng DB tạm (không đụng data/session.db)."""
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import db  # noqa: E402

db.DB_PATH = Path(tempfile.mkdtemp()) / "t.db"
db.init()
from app import director, notebook  # noqa: E402
from app.config import load as load_cfg  # noqa: E402

ok = fail = 0


def t(name, cond):
    global ok, fail
    ok += bool(cond)
    fail += not cond
    print(("PASS " if cond else "FAIL ") + name)


S, E = notebook.START, notebook.END
echo = f"Viết lời đọc... Chỉ trả về lời đọc nằm giữa hai dòng {S} và {E}."
ans = (f"{echo}\nThoughts\n{S}\n**Bạn có biết** rằng mỗi ngày có hàng triệu người dùng?\n\n- Điều đó thay đổi tất cả. "
       f"[Nhạc nền]\n{E}\nLưu vào ghi chú")
t("clean_narration lấy đúng câu trả lời (bỏ câu hỏi lặp lại)",
  notebook.clean_narration(ans) == "Bạn có biết rằng mỗi ngày có hàng triệu người dùng? Điều đó thay đổi tất cả.")
t("trả lời không có dấu → lấy phần sau câu hỏi",
  notebook.clean_narration(f"{echo}\nĐây là lời đọc không có dấu.") .startswith("Đây là lời đọc"))
js = f'{echo} {{"title": "mẫu"}}\n{S}\n{{"title": "Bí mật", "core_message": "x", "hook": "h", "chapters": [{{"title": "a", "beat": "hook",}}]}}\n{E}'
o = notebook._json_block(js)
t("JSON dàn ý lấy đúng khối của câu trả lời (sửa dấu phẩy thừa)", o["title"] == "Bí mật" and o["chapters"][0]["beat"] == "hook")

# NotebookLM viết prompt ảnh theo lô: lấy đúng cảnh, bỏ prompt quá ngắn, lô không vượt MAX_PROMPT
asked = []
_orig_ask = notebook.ask
notebook.ask = lambda jid, step, prompt, *a, **k: (asked.append(prompt), (
    f"{prompt}\n{S}\n" + json.dumps({"prompts": [
        {"i": 0, "image_prompt": "A determined young footballer in a plain kit sprints across a lightly sketched "
                                 "pitch on the left, ball at his feet, a coach in the centre watching with folded "
                                 "arms, faint stadium stands far behind",
         "drawing_order": ["footballer", "ball", "coach"]},
        {"i": 1, "image_prompt": "too short"}]}) + f"\n{E}"))[1]
rows = [{"narration": "Cầu thủ trẻ chạy nhanh trên sân. " * 30} for _ in range(3)]
n = notebook.image_prompts(1, {"title": "t"}, rows, load_cfg(), lambda m: None, batch=7)
notebook.ask = _orig_ask
t("prompt ảnh NotebookLM gán đúng cảnh, bỏ prompt quá ngắn", n == 1 and rows[0].get("prompt_by") == "notebooklm"
  and rows[0]["drawing_order"][0] == "footballer" and "prompt_by" not in rows[1])
t("mỗi câu hỏi prompt ảnh ≤ giới hạn ô hỏi", all(len(p) <= notebook.MAX_PROMPT for p in asked))

ss = director.sentences("Xin chào các bạn. Hôm nay là ngày 26/10! Bạn có tin không? Tôi thì không…  Thật đấy.")
t("tách câu theo dấu câu", len(ss) == 5 and ss[1] == "Hôm nay là ngày 26/10!")

# hàng đợi + API với extension giả
from app import api  # noqa: E402


class W:
    running = False


port = 8798
srv = api.serve(port, W(), lambda: None)
if srv is None:
    print("SKIP API (không có api.make_server)")
else:
    base = f"http://127.0.0.1:{port}"

    def get(p):
        return json.loads(urllib.request.urlopen(base + p).read())

    def post(p, body):
        r = urllib.request.Request(base + p, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(r).read())

    get("/nb/next?v=test&tab=1")
    t("heartbeat → connected", notebook.connected())

    def fake_ext():
        for _ in range(40):
            task = get("/nb/next?v=test&tab=1")["task"]
            if task:
                post(f"/nb/tasks/{task['id']}/result", {"text": task["prompt"] + f"\n{S}\nTrả lời {task['step']}.\n{E}"})
                return
            time.sleep(0.2)
    threading.Thread(target=fake_ext, daemon=True).start()
    out = notebook.ask(1, "chapter_1", f"Hỏi thử. Trả lời giữa {S} và {E}.", time.time() + 20, lambda: None)
    t("ask → nhận câu trả lời qua API", notebook.clean_narration(out) == "Trả lời chapter_1.")
    tid = notebook.enqueue(1, "x", "p")
    get("/nb/next")
    post(f"/nb/tasks/{tid}/error", {"message": "giới hạn", "pause": True})
    t("lỗi pause → tạm dừng", notebook.paused() == "giới hạn")
    t("tạm dừng → không giao task", get("/nb/next")["task"] is None)
    post("/nb/resume", {})
    t("resume", notebook.paused() is None)
    try:
        notebook.ask(1, "y", "p", time.time() + 1, lambda: None)
        t("hết giờ → Unavailable", False)
    except notebook.Unavailable:
        t("hết giờ → Unavailable", True)
    srv.shutdown()
print(f"\n{ok} PASS, {fail} FAIL")
sys.exit(1 if fail else 0)
