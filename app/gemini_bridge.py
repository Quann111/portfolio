"""Cầu nối giữa AutoVideo và Google Gemini Web (gemini.google.com) qua Chrome Extension 'AutoVideo Gemini Bridge':
app xếp prompt vào hàng đợi gemini_tasks → extension gõ prompt qua CDP, chờ trả lời xong và gửi kết quả về.
Được dùng cho: chia cảnh (One-shot Scene Splitting), viết metadata YouTube, và rà soát kịch bản."""
import threading
import time

from . import db

_GEMINI_ASK_LOCK = threading.Lock()

CLAIM_TIMEOUT = 600
MAX_TASK_ATTEMPTS = 3
START = "===BẮT ĐẦU==="
END = "===KẾT THÚC==="


def enqueue(jid: int, step: str, prompt: str, kind: str = "ask") -> int:
    return db.x("INSERT INTO gemini_tasks(job_id,step,kind,prompt,created_at) VALUES(?,?,?,?,?)",
                (jid, step, kind, prompt, time.time()))


def claim_next() -> dict | None:
    if paused():
        return None
    db.x("UPDATE gemini_tasks SET status='queued' WHERE status='claimed' AND claimed_at < ?",
         (time.time() - CLAIM_TIMEOUT,))
    r = db.q("SELECT * FROM gemini_tasks WHERE status='queued' ORDER BY id LIMIT 1")
    if not r:
        return None
    t = r[0]
    db.x("UPDATE gemini_tasks SET status='claimed', claimed_at=?, attempts=attempts+1 WHERE id=?",
         (time.time(), t["id"]))
    return {"id": t["id"], "kind": t["kind"], "prompt": t["prompt"], "job": t["job_id"], "step": t["step"]}


def complete(task_id: int, text: str):
    db.x("UPDATE gemini_tasks SET status='done', done_at=?, result=? WHERE id=?", (time.time(), text, task_id))


def fail(task_id: int, msg: str, pause: bool = False):
    t = db.q("SELECT attempts FROM gemini_tasks WHERE id=?", (task_id,))
    retry = t and t[0]["attempts"] < MAX_TASK_ATTEMPTS and not pause
    db.x("UPDATE gemini_tasks SET status=?, error=? WHERE id=?",
         ("queued" if retry else "error", msg[:500], task_id))
    if pause:
        set_paused(msg)


def paused() -> str | None:
    return db.kv_get("gemini_paused")


def set_paused(reason: str | None):
    db.kv_set("gemini_paused", reason)


def connected() -> bool:
    seen = db.kv_get("gemini_seen") or {}
    return bool(seen.get("at") and time.time() - seen["at"] < 35 and seen.get("tab") and seen.get("enabled"))


def cancel_job(jid: int):
    db.x("UPDATE gemini_tasks SET status='cancelled' WHERE job_id=? AND status IN ('queued','claimed')", (jid,))


class Unavailable(Exception):
    pass


def ask(jid: int, step: str, prompt: str, deadline: float, check=None, kind: str = "ask") -> str:
    """Gửi câu hỏi sang tab Gemini Web an toàn thread-safe bằng _GEMINI_ASK_LOCK."""
    wrapped_prompt = (
        f"{prompt}\n\n"
        f"LƯU Ý QUAN TRỌNG: Hãy định dạng câu trả lời bắt đầu bằng {START} và kết thúc bằng {END}."
    )
    with _GEMINI_ASK_LOCK:
        tid = enqueue(jid, step, wrapped_prompt, kind)
        while time.time() < deadline:
            if check:
                check()
            t = db.q("SELECT status, result, error FROM gemini_tasks WHERE id=?", (tid,))[0]
            if t["status"] == "done":
                res = t["result"] or ""
                if START in res:
                    res = res.split(START, 1)[1]
                if END in res:
                    res = res.rsplit(END, 1)[0]
                return res.strip()
            if t["status"] == "error":
                raise Unavailable(f"Gemini Web lỗi ở bước {step}: {t['error']}")
            if paused():
                cancel_job(jid)
                raise Unavailable(f"Gemini Bridge tạm dừng: {paused()}")
            time.sleep(2.5)
        cancel_job(jid)
        raise Unavailable(f"Gemini Web quá thời gian chờ ở bước {step}")
