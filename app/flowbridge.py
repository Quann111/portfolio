"""Cầu nối với extension Chrome riêng 'AutoVideo Flow Bridge':
app xếp prompt vào hàng đợi → extension (trên tab flow.google.com đã đăng nhập) lấy từng task, tạo ảnh trên giao
diện Flow như người dùng, gửi ảnh về → app lưu vào exchange/job_<id>/received và chạy QC như cũ."""
import base64
import time
from pathlib import Path

from . import autoflow, db
from .config import Cfg

CLAIM_TIMEOUT = 180          # task bị nhận quá 3 phút không trả → tự động thu hồi đưa lại hàng đợi cho profile khác
MAX_TASK_ATTEMPTS = 3


def enqueue(jid: int, rnd: int, items: list[dict]) -> int:
    now = time.time()
    for it in items:
        db.x("UPDATE flow_tasks SET status='cancelled' WHERE job_id=? AND uid=? AND status IN ('queued','claimed')",
             (jid, it["uid"]))
        db.x("INSERT INTO flow_tasks(job_id,idx,uid,round,prompt,created_at) VALUES(?,?,?,?,?,?)",
             (jid, it["idx"], it["uid"], rnd, it["prompt"], now))
    return len(items)


def claim_next(worker: str = "") -> dict | None:
    """Giao 1 task cho một extension (mỗi Chrome profile = 1 worker). Nhiều profile gọi cùng lúc → mỗi profile một
    task khác nhau (UPDATE nguyên tử), ảnh vẫn gom về đúng job/cảnh."""
    if paused() or worker_paused(worker):
        return None
    db.x("UPDATE flow_tasks SET status='queued' WHERE status='claimed' AND claimed_at < ?",
         (time.time() - CLAIM_TIMEOUT,))
    if worker:
        # task cũ của chính profile này mà chưa trả (vd service worker khởi động lại) → trả về hàng chờ
        db.x("UPDATE flow_tasks SET status='queued' WHERE status='claimed' AND worker=?", (worker,))
    now = time.time()
    # Ưu tiên job đang chạy (status='running') trước, sau đó mới tới id thấp
    db.x("UPDATE flow_tasks SET status='claimed', claimed_at=?, worker=?, attempts=attempts+1 "
         "WHERE id=(SELECT id FROM flow_tasks WHERE status='queued' ORDER BY "
         "CASE WHEN job_id=(SELECT id FROM jobs WHERE status='running' LIMIT 1) THEN 0 ELSE 1 END, "
         "job_id ASC, id ASC LIMIT 1) AND status='queued'",
         (now, worker))
    r = db.q("SELECT * FROM flow_tasks WHERE status='claimed' AND worker=? AND claimed_at=? ORDER BY id DESC LIMIT 1",
             (worker, now))
    if not r:
        return None
    t = r[0]
    return {"id": t["id"], "prompt": t["prompt"], "job": t["job_id"], "scene": t["idx"]}


# ------------------------------------------------------------------ nhiều Chrome profile
def _workers() -> dict:
    return db.kv_get("flowbridge_workers") or {}


def _set_worker(w: str, **kw):
    if not w:
        return
    ws = _workers()
    ws[w] = {**ws.get(w, {}), **kw}
    db.kv_set("flowbridge_workers", ws)


LIMIT_RETRY_SEC = 1800       # profile dừng vì GIỚI HẠN/HẾT LƯỢT → tự thử lại sau 30 phút (không áp dụng cho captcha)
LIMIT_RE = ("giới hạn/hết lượt", "hết lượt", "limit reached", "quota")


def worker_paused(w: str) -> str | None:
    if not w:
        return None
    info = _workers().get(w, {})
    reason = info.get("paused")
    if reason and any(k in reason for k in LIMIT_RE) and "captcha" not in reason.lower() \
            and time.time() - info.get("paused_at", 0) > LIMIT_RETRY_SEC:
        set_worker_paused(w, None)       # giới hạn thường tự hết – cho thử lại; còn bị chặn thì extension báo lại
        return None
    return reason


def set_worker_paused(w: str | None, reason: str | None):
    """w=None → mọi profile."""
    ws = _workers()
    for k in ([w] if w else list(ws)):
        if k in ws:
            ws[k]["paused"] = reason
            ws[k]["paused_at"] = time.time() if reason else None
    db.kv_set("flowbridge_workers", ws)


def active_workers(max_age: float = 360) -> list[dict]:
    now = time.time()
    return [{"id": k, **v} for k, v in _workers().items() if now - v.get("at", 0) < max_age]


def complete(cfg: Cfg, task_id: int, data_b64: str, mime: str = "image/png") -> Path:
    t = db.q("SELECT * FROM flow_tasks WHERE id=?", (task_id,))[0]
    raw = base64.b64decode(data_b64.split(",", 1)[-1])
    if str(t.get("uid", "")).startswith("test_") or t["job_id"] == 0:
        from .config import ROOT
        rec = ROOT / "exchange" / "test_images"
    else:
        rec = autoflow.job_dir(cfg, t["job_id"]) / "received"
    rec.mkdir(parents=True, exist_ok=True)
    ext = ".jpg" if "jpeg" in mime else ".webp" if "webp" in mime else ".png"
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None
    if img is None or img.shape[0] < 200 or img.shape[1] < 200:
        raise ValueError("ảnh gửi về không đọc được hoặc quá nhỏ")
    thumb = str(t["uid"]).startswith("thumb_")     # ảnh nền thumbnail YouTube: ảnh thật, không phải giấy vẽ
    if not thumb and ui_capture(img):
        raise ValueError("ảnh chụp dính giao diện Flow (khung prompt/khung chat tối đè lên ảnh)")
    f = rec / f"scene_{t['idx']:03}_{t['uid']}_r{t['round']}_t{task_id}.png"
    cleaned = None if thumb else clean_capture(img)
    if cleaned is not None:
        img, ext = cleaned, ".jpg"          # buộc ghi lại bằng cv2
    if ext == ".png":
        f.write_bytes(raw)
    else:
        cv2.imencode(".png", img)[1].tofile(str(f))
    db.x("UPDATE flow_tasks SET status='done', done_at=?, image=? WHERE id=?", (time.time(), str(f), task_id))
    if t.get("worker"):
        _set_worker(t["worker"], done=_workers().get(t["worker"], {}).get("done", 0) + 1)
    return f


def ui_capture(img) -> bool:
    """Ảnh của kênh là nét tối trên giấy be sáng; giao diện Flow là mảng tối lớn liền khối. Ô 32×32 gần đen chiếm
    > 6% ảnh (bỏ 4% viền – góc bo) → là ảnh chụp dính giao diện."""
    import cv2
    h, w = img.shape[:2]
    m = int(min(h, w) * 0.04)
    g = cv2.cvtColor(img[m:h - m, m:w - m], cv2.COLOR_BGR2GRAY)
    small = cv2.resize(g, (max(1, g.shape[1] // 32), max(1, g.shape[0] // 32)), interpolation=cv2.INTER_AREA)
    return float((small < 60).mean()) > 0.06


def clean_capture(img):
    """Ảnh lấy bằng cách chụp vùng hiển thị của Flow: có góc bo đen + biểu tượng ✦ Gemini góc dưới phải.
    Cắt viền (nếu góc tối) rồi phủ góc dưới phải bằng màu giấy – vùng đáy vốn phải trống cho phụ đề.
    Trả về ảnh đã sửa, hoặc None nếu không cần sửa."""
    import cv2
    import numpy as np
    h, w = img.shape[:2]
    corners = [img[2, 2], img[2, w - 3], img[h - 3, 2], img[h - 3, w - 3]]
    changed = False
    if np.mean([c.mean() for c in corners]) < 80:
        m = max(4, int(min(h, w) * 0.03))
        img = cv2.resize(img[m:h - m, m:w - m], (w, h), interpolation=cv2.INTER_CUBIC)
        changed = True
    # nền giấy = trung vị dải mép trên; phủ ô góc dưới phải (biểu tượng ✦) nếu ô đó gần như trống
    paper = np.median(img[: max(4, h // 30)].reshape(-1, 3), axis=0).astype(np.uint8)
    y0, x0 = int(h * 0.82), int(w * 0.88)
    box = img[y0:, x0:]
    if (np.abs(box.astype(int) - paper.astype(int)).sum(axis=2) > 60).mean() < 0.08:
        img = img.copy()
        img[y0:, x0:] = paper
        changed = True
    return img if changed else None


PROFILE_ERRORS = ("Không bấm được nút gửi", "Gõ prompt vào ô không thành công", "Không gắn được DevTools",
                  "không liên lạc được với tab Flow")


def fail(task_id: int, msg: str, pause: bool = False, worker: str = ""):
    # lỗi do tab/profile (không phải do prompt/cảnh) → dừng riêng profile đó, không trừ lượt của cảnh
    pause = pause or any(k in msg for k in PROFILE_ERRORS)
    if pause:
        # captcha / giới hạn / tab chưa mở project ở MỘT profile → chỉ profile đó dừng; task trả về cho profile khác.
        # Không bao giờ tự dừng CHUNG vì lỗi của một profile (dừng chung chỉ khi bấm tay).
        db.x("UPDATE flow_tasks SET status='queued', error=?, attempts=MAX(0, attempts-1) WHERE id=?",
             (msg[:500], task_id))
        if worker:
            set_worker_paused(worker, msg)
        return
    t = db.q("SELECT attempts FROM flow_tasks WHERE id=?", (task_id,))
    retry = t and t[0]["attempts"] < MAX_TASK_ATTEMPTS and not pause
    db.x("UPDATE flow_tasks SET status=?, error=? WHERE id=?", ("queued" if retry else "error", msg[:500], task_id))
    if worker:
        _set_worker(worker, errors=_workers().get(worker, {}).get("errors", 0) + 1)


def heartbeat(info: dict, worker: str = ""):
    db.kv_set("flowbridge_seen", {"at": time.time(), **(info or {})})
    if worker:
        _set_worker(worker, at=time.time(), **{k: v for k, v in (info or {}).items() if v not in (None, "")})


def connected(max_age: float = 360) -> bool:
    # extension chỉ gọi /flow/next giữa hai ảnh; đang tạo một ảnh (tới 4 phút) thì im lặng – không tính là mất kết nối
    s = db.kv_get("flowbridge_seen") or {}
    return time.time() - s.get("at", 0) < max_age or bool(active_workers(max_age))


def paused() -> str | None:
    return db.kv_get("flowbridge_paused")


def set_paused(reason: str | None):
    db.kv_set("flowbridge_paused", reason)


def done_unprocessed(jid: int) -> list[dict]:
    return db.q("SELECT * FROM flow_tasks WHERE job_id=? AND status='done' AND (error IS NULL OR error!='used') "
                "ORDER BY id", (jid,))


def mark_used(task_id: int):
    db.x("UPDATE flow_tasks SET error='used' WHERE id=?", (task_id,))


def open_tasks(jid: int) -> list[dict]:
    return db.q("SELECT * FROM flow_tasks WHERE job_id=? AND status IN ('queued','claimed') ORDER BY id", (jid,))


def failed_tasks(jid: int, rnd: int) -> list[dict]:
    return db.q("SELECT * FROM flow_tasks WHERE job_id=? AND round=? AND status='error'", (jid, rnd))


def cancel_job(jid: int):
    db.x("UPDATE flow_tasks SET status='cancelled' WHERE job_id=? AND status IN ('queued','claimed') "
         "AND uid NOT LIKE 'thumb_%'", (jid,))


def summary(jid: int | None = None) -> dict:
    where, args = ("WHERE job_id=?", (jid,)) if jid else ("", ())
    rows = db.q(f"SELECT status, COUNT(*) n FROM flow_tasks {where} GROUP BY status", args)
    act = active_workers()
    return {"tasks": {r["status"]: r["n"] for r in rows}, "extension_connected": connected(),
            "last_seen": (db.kv_get("flowbridge_seen") or {}).get("at"), "paused": paused(),
            "workers": [{"id": w["id"], "name": w.get("name") or w["id"][:6], "done": w.get("done", 0),
                         "errors": w.get("errors", 0), "paused": w.get("paused"), "version": w.get("version")}
                        for w in act],
            "running": sum(1 for w in act if not w.get("paused"))}
