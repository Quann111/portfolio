"""Viết kịch bản bằng NotebookLM (Gemini Notebook) qua extension Chrome riêng 'AutoVideo Notebook Bridge':
app xếp prompt vào hàng đợi nb_tasks → extension (tab notebooklm đã đăng nhập) gõ prompt như người dùng, chờ
trả lời xong và gửi văn bản về. Không gọi API nội bộ, gặp giới hạn/captcha thì tạm dừng; worker chờ rồi thử lại (script.fallback_qwen)."""
import json
import re
import threading
import time
from datetime import datetime

_JOB_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_MUTEX = threading.Lock()

def _job_lock(jid: int) -> threading.Lock:
    with _LOCKS_MUTEX:
        if jid not in _JOB_LOCKS:
            _JOB_LOCKS[jid] = threading.Lock()
        return _JOB_LOCKS[jid]

import hashlib

from . import db, events, pipeline, planner
from .config import Cfg
from .visual_rules import WRITER_RULE_EN, WRITER_RULE_VI, strip_legacy

NOTEBOOK_URL_REGEX = r"^https://(?:notebooklm|notebook)\.google\.com/notebook/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

def is_real_url(url: str | None) -> bool:
    """Kiểm tra URL sổ NotebookLM hợp lệ (nhận cả domain mới notebook.google.com và cũ notebooklm.google.com).
    Bắt buộc UUID thật dạng 8-4-4-4-12 hex. Loại rõ 'creating', 'TODO…', rỗng."""
    if not url:
        return False
    u = url.strip()
    if not u or "/TODO" in u or "TODO_" in u or "/creating" in u:
        return False
    return bool(re.match(NOTEBOOK_URL_REGEX, u, re.IGNORECASE))


MAX_PROMPT = 3200            # giới hạn an toàn cho ô hỏi NotebookLM (đo 27/10: 3.292 gửi được, 3.924 bị khoá nút gửi)
CLAIM_TIMEOUT = 900          # câu trả lời dài có thể mất vài phút
MAX_TASK_ATTEMPTS = 3
START = "===BẮT ĐẦU==="
END = "===KẾT THÚC==="

def _hash_prompt(p: str) -> str:
    return hashlib.sha256(p.strip().encode("utf-8")).hexdigest()[:16]

# ------------------------------------------------------------------ quản lý slot
def _cfg_slots() -> tuple[int, list[str]]:
    from .config import load
    cfg = load()
    sc = cfg.get("script") or {}
    n = int(sc.get("nb_slots", 5))
    books = sc.get("nb_notebooks") or []
    return n, books

_STALE_CLEANED = False


def init_slots():
    """Khởi tạo hoặc đồng bộ bảng nb_slots từ config.yaml.
    Thứ tự ưu tiên URL: (a) config thật → (b) URL đã lưu DB → (c) no_notebook (extension tự tạo sổ).
    KHÔNG ghi đè URL đã lưu trong DB bằng rỗng/placeholder từ config."""
    n, books = _cfg_slots()
    now = time.time()
    global _STALE_CLEANED
    if not _STALE_CLEANED:   # chỉ dọn 1 lần khi khởi động
        # F2: task claimed/queued của job <= 0 (topic_scout dở) chuyển sang cancelled
        db.x("UPDATE nb_tasks SET status='cancelled' WHERE job_id <= 0 AND status IN ('queued', 'claimed')")
        # F2: nhả slot của job_id <= 0 và đặt state='idle' (nếu có URL)
        db.x("UPDATE nb_slots SET job_id=NULL, task_id=NULL, step=NULL, "
             "state=CASE WHEN notebook_url LIKE 'http%' THEN 'idle' ELSE 'no_notebook' END "
             "WHERE job_id <= 0")
        # nhả slot bị job đã xong kịch bản giữ lại (không còn task chờ) – trước đây bộ YouTube mượn slot không trả
        db.x("UPDATE nb_slots SET job_id=NULL, task_id=NULL, step=NULL, "
             "state=CASE WHEN notebook_url LIKE 'http%' THEN 'idle' ELSE 'no_notebook' END "
             "WHERE job_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nb_tasks t WHERE t.job_id=nb_slots.job_id AND t.status IN ('queued','claimed')) "
             "AND job_id NOT IN (SELECT id FROM jobs WHERE stage IN ('research','script') AND status IN ('running','preparing'))")
        _STALE_CLEANED = True
    for s in range(1, n + 1):
        cfg_url = (books[s - 1].strip() if s <= len(books) else "")
        cfg_real = is_real_url(cfg_url)
        existing = db.q("SELECT * FROM nb_slots WHERE slot=?", (s,))
        if not existing:
            if cfg_real:
                db.x("INSERT INTO nb_slots(slot, notebook_url, state, updated_at) VALUES(?,?,?,?)",
                     (s, cfg_url, "idle", now))
            else:
                db.x("INSERT INTO nb_slots(slot, notebook_url, state, updated_at) VALUES(?,?,?,?)",
                     (s, "", "no_notebook", now))
        else:
            cur = existing[0]
            db_url = (cur.get("notebook_url") or "").strip()
            db_real = is_real_url(db_url)
            # Dọn dữ liệu hỏng: nếu URL không hợp lệ theo chuẩn UUID (như /notebook/creating, TODO, rác)
            if db_url and not db_real:
                db.x("UPDATE nb_slots SET notebook_url='', state='no_notebook', updated_at=? WHERE slot=?", (now, s))
                # Trả các task đang queued/claimed ở slot đó về queued, giữ job
                db.x("UPDATE nb_tasks SET status='queued', claimed_at=NULL WHERE slot=? AND status IN ('queued', 'claimed')", (s,))
                events.push("log", {"msg": f"Slot #{s}: URL sổ không hợp lệ ({db_url}) – sẽ tạo lại"})
                print(f"[init_slots] Slot #{s}: URL sổ không hợp lệ ({db_url}) – sẽ tạo lại")
                db_url = ""
                db_real = False
                cur["state"] = "no_notebook" 
            if cfg_real:
                # Config có URL thật -> dùng URL config, cập nhật state nếu cần
                new_st = cur["state"] if cur.get("job_id") is not None else "idle"
                if db_url != cfg_url or cur["state"] != new_st:
                    db.x("UPDATE nb_slots SET notebook_url=?, state=?, updated_at=? WHERE slot=?",
                         (cfg_url, new_st, now, s))
            elif db_real:
                # Config rỗng/placeholder nhưng DB đã có URL thật -> GIỮ NGUYÊN URL DB
                new_st = cur["state"] if cur.get("job_id") is not None else "idle"
                if cur["state"] != new_st:
                    db.x("UPDATE nb_slots SET state=?, updated_at=? WHERE slot=?", (new_st, now, s))
            else:
                # Cả config lẫn DB đều không có URL thật -> giữ no_notebook
                if cur["state"] not in ("no_notebook", "creating", "busy", "paused"):
                    db.x("UPDATE nb_slots SET state='no_notebook', updated_at=? WHERE slot=?", (now, s))

def bind(jid: int, log=print, check=lambda: None, deadline: float | None = None) -> int:
    """Gắn job vào một slot rảnh và giữ nguyên slot đó suốt kịch bản. Hết slot thì chờ + pipeline.add_wait.
    Nếu quá deadline thì raise Unavailable."""
    init_slots()
    if deadline is None:
        deadline = time.time() + 60.0
    # 1. Nếu job này đã được gắn vào slot nào đó rồi (vd: chạy tiếp sau khi restart):
    if jid > 0:
        assigned = db.q("SELECT slot, state FROM nb_slots WHERE job_id=?", (jid,))
        if assigned:
            s = assigned[0]["slot"]
            db.x("UPDATE nb_slots SET state='busy', updated_at=? WHERE slot=?", (time.time(), s))
            return s

    # 2. Tìm slot rảnh có URL sổ hợp lệ (state=idle).
    t_start = time.time()
    while True:
        check()
        if deadline is not None and time.time() >= deadline:
            raise Unavailable("Không có slot NotebookLM nào có sổ – xem dashboard")

        r = db.q("SELECT slot FROM nb_slots WHERE job_id IS NULL AND state='idle' ORDER BY slot LIMIT 1")
        if r:
            slot_id = r[0]["slot"]
            # Gán nguyên tử
            updated = db.xc("UPDATE nb_slots SET job_id=?, state='busy', updated_at=? WHERE slot=? AND job_id IS NULL",
                           (jid, time.time(), slot_id))
            if updated:
                if jid > 0:
                    db.kv_set(f"nb_main_{jid}", slot_id)
                return slot_id

        # Kiểm tra xem có tab fallback theo AGENTS.md 1.6 không
        s_seen = db.kv_get("notebook_seen") or {}
        if s_seen.get("tab_url") and is_real_url(s_seen.get("tab_url")):
            # Cập nhật slot 1 với URL này nếu slot 1 chưa có URL
            s1 = db.q("SELECT notebook_url, job_id FROM nb_slots WHERE slot=1")
            if s1 and not is_real_url(s1[0].get("notebook_url")) and s1[0]["job_id"] is None:
                db.x("UPDATE nb_slots SET notebook_url=?, job_id=?, state='busy', updated_at=? WHERE slot=1",
                     (s_seen["tab_url"], jid, time.time()))
                return 1

        # Chờ slot rảnh hoặc đang tạo sổ
        creating = db.q("SELECT COUNT(*) n FROM nb_slots WHERE state='creating'")
        if creating and creating[0]["n"] > 0:
            if jid > 0:
                log(f"Job {jid}: đang tạo sổ NotebookLM, chờ thêm…")
                pipeline.add_wait(jid, "script", 10, "đang tạo sổ NotebookLM")
            else:
                log("Đang tạo sổ NotebookLM, chờ thêm…")
        else:
            if jid > 0:
                log(f"Job {jid}: các slot NotebookLM đang bận, chờ slot rảnh…")
                pipeline.add_wait(jid, "script", 5, "chờ slot NotebookLM rảnh")
            else:
                log("Các slot NotebookLM đang bận, chờ slot rảnh…")
        time.sleep(5)

def release(jid: int):
    """Giải phóng slot khi job hoàn thành kịch bản, bị lỗi hoặc bị huỷ."""
    if jid > 0:
        slots = db.q("SELECT slot, notebook_url FROM nb_slots WHERE job_id=?", (jid,))
    else:
        slots = db.q("SELECT slot, notebook_url FROM nb_slots WHERE job_id <= 0")
    now = time.time()
    for row in slots:
        url = (row.get("notebook_url") or "").strip()
        has_url = is_real_url(url)
        st = "idle" if has_url else "no_notebook"
        db.x("UPDATE nb_slots SET job_id=NULL, task_id=NULL, step=NULL, state=?, updated_at=? WHERE slot=?",
             (st, now, row["slot"]))
    if jid > 0 and db.kv_get(f"nb_main_{jid}") is not None:
        db.kv_set(f"nb_main_{jid}", None)
    with _LOCKS_MUTEX:
        _JOB_LOCKS.pop(jid, None)
        for k in [k for k in _JOB_LOCKS if isinstance(k, tuple) and k[0] == jid]:
            _JOB_LOCKS.pop(k, None)

def get_job_slot(jid: int) -> int | None:
    """Slot CHÍNH của job (slot viết dàn ý); job có thể giữ thêm slot phụ khi viết chương song song."""
    r = [x["slot"] for x in db.q("SELECT slot FROM nb_slots WHERE job_id=? ORDER BY slot", (jid,))]
    if not r:
        return None
    main = db.kv_get(f"nb_main_{jid}") if jid > 0 else None
    return main if main in r else r[0]


def bind_extra(jid: int, n: int) -> list[int]:
    """Mượn thêm tối đa n slot đang rảnh cho cùng job (viết chương song song). Không chờ: hết slot thì trả ít hơn."""
    got = []
    for r in db.q("SELECT slot FROM nb_slots WHERE job_id IS NULL AND state='idle' ORDER BY slot"):
        if len(got) >= n:
            break
        if db.xc("UPDATE nb_slots SET job_id=?, state='busy', updated_at=? WHERE slot=? AND job_id IS NULL",
                 (jid, time.time(), r["slot"])):
            got.append(r["slot"])
    return got

# ------------------------------------------------------------------ hàng đợi
def enqueue(jid: int, step: str, prompt: str, kind: str = "ask", slot: int | None = None) -> int:
    ph = _hash_prompt(prompt)
    if slot is None:
        slot = get_job_slot(jid)
    tid = db.x("INSERT INTO nb_tasks(job_id,step,kind,prompt,prompt_hash,slot,created_at) VALUES(?,?,?,?,?,?,?)",
               (jid, step, kind, prompt, ph, slot, time.time()))
    if slot:
        db.x("UPDATE nb_slots SET task_id=?, step=?, updated_at=? WHERE slot=?", (tid, step, time.time(), slot))
    return tid


def claim_next(slot: int | None = None) -> dict | None:
    if paused():
        return None
    # Task claimed quá hạn thì trả về queued trong cùng slot
    db.x("UPDATE nb_tasks SET status='queued' WHERE status='claimed' AND claimed_at < ?",
         (time.time() - CLAIM_TIMEOUT,))
    if slot is not None:
        if slot_paused(slot):
            return None
        r = db.q("SELECT * FROM nb_tasks WHERE status='queued' AND slot=? ORDER BY id LIMIT 1", (slot,))
    else:
        r = db.q("SELECT * FROM nb_tasks WHERE status='queued' ORDER BY id LIMIT 1")
    if not r:
        return None
    t = r[0]
    db.x("UPDATE nb_tasks SET status='claimed', claimed_at=?, attempts=attempts+1 WHERE id=?", (time.time(), t["id"]))
    if t.get("slot"):
        db.x("UPDATE nb_slots SET task_id=?, step=?, state='busy', updated_at=? WHERE slot=?",
             (t["id"], t["step"], time.time(), t["slot"]))
    return {"id": t["id"], "kind": t["kind"], "prompt": t["prompt"], "job": t["job_id"], "step": t["step"], "slot": t.get("slot")}


def complete(task_id: int, text: str):
    now = time.time()
    db.x("UPDATE nb_tasks SET status='done', done_at=?, result=? WHERE id=?", (now, text, task_id))
    t = db.q("SELECT slot FROM nb_tasks WHERE id=?", (task_id,))
    if t and t[0]["slot"]:
        db.x("UPDATE nb_slots SET task_id=NULL, step=NULL, state='idle', updated_at=? WHERE slot=?", (now, t[0]["slot"]))


def fail(task_id: int, msg: str, pause: bool = False, slot: int | None = None):
    t = db.q("SELECT attempts, slot FROM nb_tasks WHERE id=?", (task_id,))
    retry = t and t[0]["attempts"] < MAX_TASK_ATTEMPTS and not pause
    db.x("UPDATE nb_tasks SET status=?, error=? WHERE id=?", ("queued" if retry else "error", msg[:500], task_id))
    s = slot or (t[0]["slot"] if t else None)
    if pause:
        if s:
            set_slot_paused(s, msg)
        else:
            set_paused(msg)
    if s:
        db.x("UPDATE nb_slots SET updated_at=? WHERE slot=?", (time.time(), s))


def heartbeat(info: dict):
    db.kv_set("notebook_seen", {"at": time.time(), **(info or {})})


def connected(max_age: float = 90) -> bool:
    s = db.kv_get("notebook_seen") or {}
    if time.time() - s.get("at", 0) >= max_age or not s.get("enabled", True):
        return False
    # 1. Kiểm tra xem có slot nào sẵn sàng hoặc đã có tab NotebookLM đang mở không
    slots = db.q("SELECT COUNT(*) n FROM nb_slots WHERE (state != 'offline' AND state != 'no_notebook') OR (tab_id IS NOT NULL AND tab_id > 0)")
    if slots and slots[0]["n"] > 0:
        return True
    # 2. Fallback: nếu extension báo có tab mở trong /notebook/<id> hợp lệ
    tab_url = s.get("tab_url")
    if tab_url and is_real_url(tab_url):
        return True
    # 3. Fallback: nếu extension báo tab > 0
    return bool(s.get("tab") and str(s.get("tab")) != "0")


def paused() -> str | None:
    return db.kv_get("notebook_paused")


def set_paused(reason: str | None):
    db.kv_set("notebook_paused", reason)


def slot_paused(slot: int) -> str | None:
    p = db.kv_get(f"notebook_paused_slot_{slot}")
    return p


def set_slot_paused(slot: int, reason: str | None):
    db.kv_set(f"notebook_paused_slot_{slot}", reason)
    if reason:
        db.x("UPDATE nb_slots SET state='paused', updated_at=? WHERE slot=?", (time.time(), slot))
    else:
        cur = db.q("SELECT notebook_url, job_id FROM nb_slots WHERE slot=?", (slot,))
        if cur:
            st = "busy" if cur[0]["job_id"] is not None else ("idle" if cur[0]["notebook_url"] else "no_notebook")
            db.x("UPDATE nb_slots SET state=?, updated_at=? WHERE slot=?", (st, time.time(), slot))


def cancel_job(jid: int):
    db.x("UPDATE nb_tasks SET status='cancelled' WHERE job_id=? AND status IN ('queued','claimed')", (jid,))
    release(jid)


def summary() -> dict:
    rows = db.q("SELECT status, COUNT(*) n FROM nb_tasks GROUP BY status")
    seen = db.kv_get("notebook_seen") or {}
    slots = db.q("SELECT * FROM nb_slots ORDER BY slot")
    return {"tasks": {r["status"]: r["n"] for r in rows}, "extension_connected": connected(),
            "last_seen": seen.get("at"), "enabled": seen.get("enabled"), "tab": seen.get("tab"),
            "version": seen.get("version"), "paused": paused(),
            "slots": slots}


class Unavailable(Exception):
    pass


def ask(jid: int, step: str, prompt: str, deadline: float, check, kind: str = "ask", slot: int | None = None) -> str:
    if len(prompt) > MAX_PROMPT + 400:
        raise Unavailable(f"câu hỏi bước {step} dài {len(prompt)} ký tự – vượt giới hạn ô hỏi NotebookLM")

    # Không hỏi lại câu đã có kết quả (BỎ CACHE cho step topic_scout để luôn hỏi mới)
    ph = _hash_prompt(prompt)
    if step != "topic_scout":
        done_cached = db.q("SELECT result FROM nb_tasks WHERE job_id=? AND step=? AND prompt_hash=? AND status='done' ORDER BY id DESC LIMIT 1",
                           (jid, step, ph))
        if done_cached and done_cached[0]["result"]:
            return done_cached[0]["result"]

    if slot is not None:   # chỉ định slot (viết chương song song): chỉ huỷ task của riêng mình khi lỗi
        return _ask_in_slot(jid, step, prompt, deadline, check, kind, ph, slot, own=False)
    # Đảm bảo job đã được bind slot (truyền deadline vào bind)
    slot = get_job_slot(jid) if jid > 0 else None
    if not slot:
        slot = bind(jid, check=check, deadline=deadline)
        if jid > 0:   # slot mượn riêng cho bước này (vd: bộ YouTube) → trả lại ngay khi xong, không giữ mãi
            try:
                return _ask_in_slot(jid, step, prompt, deadline, check, kind, ph, slot)
            finally:
                release(jid)
    return _ask_in_slot(jid, step, prompt, deadline, check, kind, ph, slot)


def _ask_in_slot(jid, step, prompt, deadline, check, kind, ph, slot, own=True) -> str:
    # Lock theo từng job để các bước của 1 job tuần tự, nhưng các job khác nhau chạy song song
    # Với jid <= 0 (topic_scout): dùng lock riêng theo slot để không chặn các job khác
    # own=False (chương song song): lock theo (job, slot), lỗi chỉ huỷ task này – không nhả slot của các chương khác
    lock_id = (jid, slot) if not own else (jid if jid > 0 else -slot)

    def cancel_job(_jid):
        if own:
            globals()["cancel_job"](_jid)
        else:
            db.x("UPDATE nb_tasks SET status='cancelled' WHERE id=? AND status IN ('queued','claimed')", (tid,))

    tid = None
    with _job_lock(lock_id):
        # Kiểm tra lại một lần nữa phòng khi cache vừa xong (bỏ qua nếu topic_scout)
        if step != "topic_scout":
            done_cached = db.q("SELECT result FROM nb_tasks WHERE job_id=? AND step=? AND prompt_hash=? AND status='done' ORDER BY id DESC LIMIT 1",
                               (jid, step, ph))
            if done_cached and done_cached[0]["result"]:
                return done_cached[0]["result"]

        tid = enqueue(jid, step, prompt, kind, slot=slot)
        disconn_start = None
        while time.time() < deadline:
            check()
            t = db.q("SELECT status, result, error FROM nb_tasks WHERE id=?", (tid,))[0]
            if t["status"] == "done":
                return t["result"] or ""
            if t["status"] == "error":
                raise Unavailable(f"NotebookLM lỗi ở bước {step}: {t['error']}")
            if paused():
                cancel_job(jid)
                raise Unavailable(f"Notebook Bridge tạm dừng: {paused()}")
            if slot_paused(slot):
                cancel_job(jid)
                raise Unavailable(f"NotebookLM slot #{slot} tạm dừng: {slot_paused(slot)}")
            # F2: nếu mất kết nối extension quá 120s -> huỷ task, nhả slot
            if not connected(90):
                if disconn_start is None:
                    disconn_start = time.time()
                elif time.time() - disconn_start >= 120:
                    cancel_job(jid)
                    raise Unavailable(f"NotebookLM mất kết nối quá 120 giây ở bước {step}")
            else:
                disconn_start = None
            time.sleep(3)
        cancel_job(jid)
        raise Unavailable(f"NotebookLM quá thời gian chờ ở bước {step}")

# ------------------------------------------------------------------ kịch bản
def _facts_text(facts: list, limit: int = 2600) -> str:
    out = []
    for f in facts:
        when = f.get("published") or ""
        tag = " (thận trọng)" if f.get("phrasing") == "cautious" or f.get("kind") == "hypothesis" else ""
        out.append(f"- {f.get('claim') or f.get('fact') or ''}{tag}" + (f" [{when[:10]}]" if when else ""))
    s = "\n".join(out)
    return s[:limit]


def _brief(topic: str, cfg: Cfg) -> str:
    ch = cfg.bible["channel"]
    return (f"Bạn là biên kịch số 1 của một kênh YouTube video hoạt hình bảng trắng tiếng Việt. Khán giả: "
            f"{ch['audience']}. Giọng: {ch['tone']}. Hôm nay là {datetime.now():%d/%m/%Y}.\n"
            f"Chủ đề: {topic}\n")


def _answer(text: str) -> str:
    """Phần giữa dấu mở/đóng của câu trả lời. Khung chat còn chứa cả câu hỏi (cũng có 2 dấu này) nên lấy dấu
    mở CUỐI CÙNG."""
    if START in text:
        head, tail = text.rsplit(START, 1)
        seg = tail.split(END, 1)[0]
        if "\n" not in seg and len(seg.strip()) < 15 and END in tail:   # "giữa S và E" của câu hỏi
            return tail.split(END, 1)[1]
        return seg
    return text


def _json_block(text: str) -> dict:
    seg = _answer(text)
    m = re.search(r"\{.*\}", seg, re.S)
    if not m:
        raise ValueError("không thấy JSON trong câu trả lời")
    raw = m.group(0).replace("“", '"').replace("”", '"')
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    return json.loads(raw)


def clean_narration(text: str) -> str:
    text = _answer(text)
    lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("#", "```", "---", "===")) or re.match(r"^\(?(cảnh|scene|chương|đoạn)\s*\d", ln, re.I):
            continue
        ln = re.sub(r"^[-*•>]\s+", "", ln)
        ln = re.sub(r"\*\*|__|\*", "", ln)
        ln = re.sub(r"\[[^\]]{0,40}\]|\([^)]*(?:nhạc|âm thanh|hình ảnh|SFX)[^)]*\)", "", ln, flags=re.I)
        lines.append(ln.strip())
    return re.sub(r"\s+", " ", " ".join(lines)).strip().lstrip(".,:;!? ")


def _words(t: str) -> int:
    return len(t.split())


OUTLINE_KEYS = ("title", "core_message", "hook", "chapters")


def _smooth_seams(jid, chapters, slot, deadline, check, log):
    """Chương viết song song có thể nối hụt: 1 lượt ở slot chính chỉ viết lại CÂU CUỐI mỗi chương cho khớp với đầu
    chương sau. Không viết lại cả chương; lỗi thì bỏ qua (chỉ cảnh báo)."""
    def split(t):
        return [s for s in re.split(r"(?<=[.!?…])\s+", t.strip()) if s]
    parts, seams = [], {}
    for k in range(len(chapters) - 1):
        a, b = split(chapters[k]["text"]), chapters[k + 1]["text"]
        if not a:
            continue
        seams[k + 1] = a[-1]
        parts.append(f"[{k + 1}] CUỐI chương {k + 1}: \"...{chapters[k]['text'][-260:]}\"\n"
                     f"    ĐẦU chương {k + 2}: \"{b[:220]}...\"\n")
    if not seams:
        return
    head = ("Các chương dưới đây được viết riêng nên chỗ nối có thể hụt. Với MỖI chỗ nối [k], viết lại DUY NHẤT câu "
            "cuối của chương k (1-2 câu, tiếng Việt nói tự nhiên, giữ ý câu treo) để dẫn mượt sang đầu chương sau. "
            "Không đổi dữ kiện, không viết lại cả chương.\n")
    tail = f"Chỉ trả về JSON dạng {{\"1\": \"câu thay\", \"2\": \"...\"}} giữa hai dòng {START} và {END}."
    body = ""
    for p in parts:
        if len(head) + len(body) + len(p) + len(tail) > MAX_PROMPT:
            break
        body += p
    try:
        fix = _json_block(ask(jid, "seams", head + body + tail, deadline, check, slot=slot))
    except Exception as e:
        log(f"  ⚠ Soát mạch nối lỗi ({e}) → giữ nguyên các chương")
        return
    n = 0
    for key, new in (fix or {}).items():
        try:
            k = int(key)
        except (TypeError, ValueError):
            continue
        old = seams.get(k)
        new = clean_narration(str(new or "")).strip()
        if old and new and chapters[k - 1]["text"].rstrip().endswith(old):
            t = chapters[k - 1]["text"].rstrip()
            chapters[k - 1]["text"] = t[: len(t) - len(old)] + new
            n += 1
    log(f"  Soát mạch nối: sửa {n}/{len(seams)} chỗ nối giữa các chương")


def write_script(jid: int, topic: str, facts: list, cfg: Cfg, log, check=lambda: None,
                 progress=lambda d, t: None) -> dict:
    """Trả về {title, core_message, hook, callback_symbol, chapters:[{title, beat, summary, key_takeaway,
    open_loop, text}]}. Lỗi/không kết nối → raise Unavailable (worker thử lại sau, hoặc dùng Qwen nếu bật fallback_qwen)."""
    sc = cfg.get("script") or {}
    if not connected():
        # chờ một lúc (extension có thể đang được Reload / service worker vừa ngủ)
        log("NotebookLM: chờ extension Notebook Bridge kết nối (tối đa 3 phút)…")
        until = time.time() + float(sc.get("connect_wait_minutes", 3)) * 60
        t_wait = time.time()
        while not connected() and time.time() < until:
            check()
            time.sleep(5)
    if not connected():
        raise Unavailable("extension Notebook Bridge chưa kết nối hoặc đang tắt (mở tab NotebookLM, bật tự động)")
    if paused():
        raise Unavailable(f"Notebook Bridge đang tạm dừng: {paused()}")
    deadline = time.time() + float(sc.get("wait_minutes", 20)) * 60
    # Lấy target_minutes riêng của job nếu có, fallback về cấu hình chung
    from . import styles
    is_short = styles.format_of(jid) == "short"
    if is_short:
        sec = styles.seconds_of(jid) or 60
        target_m = sec / 60
        total_words = round(sec * cfg.words_per_minute / 60)
        n_ch = 1
    else:
        j_row = db.q("SELECT target_minutes FROM jobs WHERE id=?", (jid,))
        target_m = (j_row and j_row[0]["target_minutes"]) or cfg.target_minutes
        total_words = round(target_m * cfg.words_per_minute)
        n_ch = max(3, min(12, round(target_m / 2.5)))
    ch = cfg.bible["channel"]
    facts_txt = _facts_text(facts)
    brief = _brief(topic, cfg)
    if not is_short and styles.of_job(jid, cfg) == "nikola":
        # semantic-island-storytelling.md §6: móc câu (mâu thuẫn kiểm chứng được) → điểm xuất phát → bước ngoặt → kết quả
        brief += ("Nhịp kể: mở bằng một mâu thuẫn có thật, kiểm chứng được (không giật tít) → quay về điểm xuất phát → "
                  "bước ngoặt → kết quả/giá trị. Mỗi sự kiện gói trong 1–2 câu (4–8 giây đọc), hai sự kiện liền nhau "
                  "phải nối tiếp nhau để vẽ thành hai cảnh cạnh nhau trên cùng một trang.\n")

    # Gắn slot cho job này
    slot_id = bind(jid, log, check, deadline=deadline)   # chờ slot rảnh tới hạn của job, không bỏ cuộc sau 60s
    if sc.get("new_notebook", True):
        try:
            ask(jid, "new", "", deadline, check, kind="new")
        except Unavailable as e:
            log(f"NotebookLM: không tạo được sổ mới ({e}) → viết tiếp trong sổ hiện tại")
            if paused():
                raise

    if "t_wait" in locals():
        pipeline.add_wait(jid, "script", time.time() - t_wait, "chờ extension NotebookLM kết nối")
    pipeline.sub(jid, "nb_outline")
    if is_short:
        log(f"NotebookLM: lập dàn ý short ({n_ch} chương, ~{total_words} chữ)")
        progress(0, n_ch + 1)
        p_outline = (
            brief + "Dữ kiện đã kiểm chứng từ web (ưu tiên dùng; không bịa số liệu/trích dẫn/sự kiện). Dữ kiện nào lạc đề thì bỏ qua. Nếu dữ kiện thiếu, được dùng kiến thức phổ biến đã được xác nhận rộng rãi về chủ đề, diễn đạt thận trọng, KHÔNG hỏi lại – luôn trả về dàn ý:\n"
            f"{facts_txt}\n\n"
            f"Hãy suy nghĩ kỹ và thiết kế DÀN Ý cho video ngắn YouTube Shorts {round(sec)} giây (~{total_words} chữ lời đọc) "
            "nhịp nhanh, ngắn gọn, cuốn hút:\n"
            "- hook: 1-2 câu mở đầu nằm ngay 2 câu đầu gây tò mò cực mạnh trong 3-5 giây đầu\n"
            "- kết bằng một câu đọng lại sâu sắc\n"
            f"Đúng 1 chương duy nhất bao quát toàn bộ nội dung.\n"
            f"Chỉ trả về JSON (không dùng khối code) nằm giữa hai dòng {START} và {END}, dạng:\n"
            '{"title": "...", "core_message": "...", "hook": "...", "callback_symbol": "một hình ảnh biểu tượng lặp lại", '
            f'"chapters": [{{"title": "...", "beat": "hook", "summary": "nội dung chính", "open_loop": "", "key_takeaway": "...", "minutes": {round(sec / 60, 2)}}}]}}'
        )
    else:
        log(f"NotebookLM: lập dàn ý giữ chân ({n_ch} chương, ~{total_words} chữ)")
        progress(0, n_ch + 1)
        p_outline = (
            brief + "Dữ kiện đã kiểm chứng từ web (ưu tiên dùng; không bịa số liệu/trích dẫn/sự kiện). Dữ kiện nào lạc đề thì bỏ qua. Nếu dữ kiện thiếu, được dùng kiến thức phổ biến đã được xác nhận rộng rãi về chủ đề, diễn đạt thận trọng, KHÔNG hỏi lại – luôn trả về dàn ý:\n"
            f"{facts_txt}\n\n"
            f"Hãy suy nghĩ kỹ và thiết kế DÀN Ý cho video {target_m} phút (~{total_words} chữ lời đọc) "
            "khiến người xem KHÔNG THỂ BỎ DỞ:\n"
            "- hook: 1-2 câu mở đầu gây tò mò cực mạnh trong 5 giây đầu (sự thật gây sốc, câu hỏi táo bạo, hoặc "
            "nghịch lý), trung thực, không câu view rẻ tiền\n"
            "- mở các 'vòng lặp tò mò' (open loop) sớm và chỉ trả lời ở chương sau; mỗi chương kết thúc bằng một câu "
            "treo khiến người xem muốn nghe tiếp\n"
            "- có cao trào, ít nhất một cú twist/điều bất ngờ, ví dụ cụ thể, so sánh dễ hình dung\n"
            f"- chương cuối gói lại thông điệp và kêu gọi: {ch['cta']}\n"
            f"Đúng {n_ch} chương. beat của mỗi chương chọn trong: {', '.join(planner.BEATS)}.\n"
            f"Chỉ trả về JSON (không dùng khối code) nằm giữa hai dòng {START} và {END}, dạng:\n"
            '{"title": "...", "core_message": "...", "hook": "...", "callback_symbol": "một hình ảnh biểu tượng lặp '
            'lại", "chapters": [{"title": "...", "beat": "...", "summary": "nội dung chính", "open_loop": "câu hỏi '
            'treo cuối chương", "key_takeaway": "...", "minutes": 2.5}]}')
    # ô hỏi NotebookLM khoá nút gửi khi câu hỏi quá dài (3.292 ký tự gửi được, 3.924 thì không) → cắt bớt dữ kiện
    over = len(p_outline) - MAX_PROMPT
    if over > 0:
        short = _facts_text(facts, limit=max(600, len(facts_txt) - over - 20))
        p_outline = p_outline.replace(facts_txt, short)
        log(f"  Câu hỏi dài {len(p_outline) + len(facts_txt) - len(short)} ký tự → rút dữ kiện còn {len(short)} ký tự")
    ol = None
    for attempt in range(2):
        try:
            ol = _json_block(ask(jid, "outline", p_outline if attempt == 0 else
                                 "Bạn chưa trả về dàn ý. Không hỏi lại, không cần tìm thêm nguồn: hãy lập ngay dàn "
                                 "ý theo yêu cầu ở trên (dùng dữ kiện liên quan + kiến thức phổ biến đã xác nhận), "
                                 f"dạng JSON hợp lệ (dấu nháy kép thẳng, không khối code) giữa hai dòng {START} và {END}.",
                                 deadline, check))
            if all(k in ol for k in OUTLINE_KEYS) and ol["chapters"]:
                break
        except ValueError as e:
            log(f"NotebookLM: dàn ý lỗi ({e}) → hỏi lại")
            ol = None
    if not ol:
        raise Unavailable("NotebookLM không trả dàn ý hợp lệ")
    chapters = ol["chapters"]
    if is_short:
        chapters = chapters[:1]
        ol["chapters"] = chapters
    for c in chapters:
        if c.get("beat") not in planner.BEATS:
            c["beat"] = "explain"
        c.setdefault("summary", "")
        c.setdefault("key_takeaway", c.get("summary", ""))
    chapters[0]["beat"] = "hook" if chapters[0]["beat"] not in ("hook", "promise") else chapters[0]["beat"]
    if is_short:
        mins = [target_m]
        scale = 1.0
    else:
        mins = [max(0.3, float(c.get("minutes") or 0) or cfg.target_minutes / len(chapters)) for c in chapters]
        scale = cfg.target_minutes / sum(mins)
    log("Dàn ý: " + ol["title"] + " | " + " → ".join(f"[{c['beat']}] {c['title']}" for c in chapters))

    pipeline.sub(jid, "nb_outline", "done", note=f"{len(chapters)} chương")
    if is_short:
        STYLE = ("Yêu cầu: ngắn gọn, nhịp nhanh, câu ngắn; tiếng Việt nói tự nhiên; "
                 "không tiêu đề, không gạch đầu dòng, không ghi chú cảnh/âm thanh, không emoji; con số viết bằng chữ. "
                 "Kết thúc bằng một câu đọng lại.\n"
                 f"Chỉ trả về lời đọc nằm giữa hai dòng {START} và {END}.")
    else:
        STYLE = ("Yêu cầu: tiếng Việt nói tự nhiên như kể chuyện cho bạn nghe, câu ngắn, nhịp nhanh, nhiều hình ảnh cụ "
                 "thể, câu hỏi tu từ, chi tiết bất ngờ; không lặp ý chương trước; không tiêu đề, không gạch đầu dòng, "
                 "không ghi chú cảnh/âm thanh, không emoji; con số viết bằng chữ. Từ viết tắt/tên tổ chức nước ngoài: lần "
                 "đầu viết tên tiếng Việt đầy đủ (vd 'khối quân sự NATO'), sau đó dùng tên tiếng Việt ngắn; không dùng ký "
                 "hiệu + / x / °. Trước khi trả lời hãy tự đọc lại, cắt "
                 "chỗ nhàm và làm câu mở đầu, câu kết thật cuốn.\n"
                 f"Chỉ trả về lời đọc nằm giữa hai dòng {START} và {END}.")

    def words_of(i):
        if is_short:
            return round(sec * cfg.words_per_minute / 60)
        return max(60, round(mins[i] * scale * cfg.words_per_minute))

    def head_tail(i):
        if is_short:
            first = f"Hook phải nằm trong 2 câu đầu: \"{ol['hook']}\" gây tò mò tức thì.\n"
            last = "Kết thúc video bằng một câu đọng lại.\n"
            return first, last
        first = (f"Chương này PHẢI mở đầu bằng hook: \"{ol['hook']}\"\n" if i == 0 else "")
        last = (f"Kết thúc video bằng lời kêu gọi: {ch['cta']}\n" if i == len(chapters) - 1 else
                f"Kết chương bằng câu treo dẫn sang chương {i + 2}.\n")
        return first, last

    def prompt_seq(i, story):
        # ô hỏi NotebookLM giới hạn độ dài (prompt ~5200 ký tự không gửi được): dữ kiện đã nằm trong cuộc trò
        # chuyện (lượt dàn ý) nên chỉ nhắc lại, không gửi lại
        first, last = head_tail(i)
        if is_short:
            return (f"Tiếp tục video '{ol['title']}' theo dàn ý 1 chương vừa lập. Dùng các dữ kiện ở lượt đầu (bỏ qua dữ kiện lạc "
                    "đề; thiếu thì dùng kiến thức phổ biến đã xác nhận, diễn đạt thận trọng, không bịa số liệu).\n"
                    f"Viết LỜI ĐỌC đầy đủ cho video ngắn YouTube Shorts: khoảng {words_of(i)} chữ (khoảng {round(sec)} giây).\n"
                    f"{first}{last}" + STYLE)
        return (f"Tiếp tục video '{ol['title']}' theo dàn ý bạn vừa lập. Dùng các dữ kiện ở lượt đầu (bỏ qua dữ kiện lạc "
                "đề; thiếu thì dùng kiến thức phổ biến đã xác nhận, diễn đạt thận trọng, không bịa số liệu).\n"
                + (f"Đoạn cuối phần đã viết: \"...{story[-400:]}\"\n" if story else "")
                + f"Viết LỜI ĐỌC đầy đủ cho chương {i + 1}: {chapters[i]['title']} — khoảng {words_of(i)} chữ.\n"
                f"{first}{last}" + STYLE)

    def prompt_solo(i):
        # slot phụ không có cuộc trò chuyện dàn ý → câu hỏi phải tự đủ: dàn ý quanh chương + dữ kiện rút gọn
        first, last = head_tail(i)
        def line(k, tag):
            c = chapters[k]
            return f"- {tag} chương {k + 1} [{c['beat']}] {c['title']}: {c.get('summary', '')}" + \
                   (f" (câu treo: {c['open_loop']})" if c.get("open_loop") else "") + "\n"
        around = "".join(line(k, t) for k, t in ((i - 1, "Trước:"), (i, "CHƯƠNG NÀY:"), (i + 1, "Sau:"))
                         if 0 <= k < len(chapters))
        def build(facts_part):
            return (f"Bỏ qua các cuộc trò chuyện trước trong sổ này. Video YouTube '{ol['title']}' – thông điệp chính: "
                    f"{ol.get('core_message', '')}. Tổng {len(chapters)} chương, chương đã có người khác viết, bạn CHỈ "
                    f"viết chương {i + 1}.\nDàn ý quanh chương:\n{around}"
                    f"Dữ kiện đã kiểm chứng (bỏ qua dữ kiện lạc đề; thiếu thì dùng kiến thức phổ biến đã xác nhận, "
                    f"diễn đạt thận trọng, không bịa số liệu):\n{facts_part}\n"
                    f"Viết LỜI ĐỌC đầy đủ cho chương {i + 1}: {chapters[i]['title']} — khoảng {words_of(i)} chữ. "
                    "Không chào hỏi, không tóm tắt lại chương trước, vào thẳng nội dung chương này.\n"
                    f"{first}{last}" + STYLE)
        p = build(_facts_text(facts, limit=1400))
        if len(p) > MAX_PROMPT:
            p = build(_facts_text(facts, limit=max(300, 1400 - (len(p) - MAX_PROMPT) - 20)))
        return p

    def write_one(i, slot=None, solo=False, story=""):
        w = words_of(i)
        prompt = prompt_solo(i) if solo else prompt_seq(i, story)
        text = clean_narration(ask(jid, f"chapter_{i + 1}", prompt, deadline, check, slot=slot))
        if _words(text) < w * 0.5:
            log(f"  Chương {i + 1} quá ngắn ({_words(text)}/{w} chữ) → yêu cầu viết đầy đủ hơn")
            more = clean_narration(ask(jid, f"chapter_{i + 1}_more",
                                       f"Lời đọc chương {i + 1} mới có {_words(text)} chữ, cần khoảng {w} chữ. Viết lại "
                                       f"đầy đủ hơn với thêm ví dụ cụ thể từ dữ kiện. Chỉ trả về lời đọc giữa {START} "
                                       f"và {END}.", deadline, check, slot=slot))
            if _words(more) > _words(text):
                text = more
        if not text:
            raise Unavailable(f"NotebookLM trả lời rỗng ở chương {i + 1}")
        if i == 0 and not check_hook(text, ol["title"]):
            # B1: hook (75 chữ đầu) phải có câu hỏi và từ khoá tiêu đề → viết lại đúng 2 câu mở đầu
            from .director import sentences
            ss = sentences(text)
            log("  Hook chưa nêu câu hỏi trung tâm → viết lại 2 câu mở đầu")
            new_head = clean_narration(ask(jid, "hook_fix", (
                f"Viết lại CHỈ 2 câu mở đầu của lời đọc. Câu hỏi trung tâm của video là: \"{ol['title']}\". "
                "Câu đầu phải đặt đúng câu hỏi đó (có dấu ?), câu sau đưa một dữ kiện kiểm chứng được. "
                f"Chỉ trả về 2 câu giữa {START} và {END}.\nHai câu hiện tại: \"{' '.join(ss[:2])}\""), deadline, check))
            if new_head and check_hook(new_head + " " + " ".join(ss[2:]), ol["title"]):
                text = new_head + " " + " ".join(ss[2:])
                log("  ✔ Hook đã nêu câu hỏi")
            else:
                log("  ⚠ Hook vẫn chưa nêu câu hỏi trung tâm (ghi nhận, không dừng job)")
        return text

    if is_short:
        extra = []
    else:
        n_par = int(sc.get("parallel_chapters", 4))
        extra = bind_extra(jid, min(n_par, len(chapters) - 1)) if n_par > 0 and len(chapters) > 1 else []
    story = ""
    if not extra:
        for i, c in enumerate(chapters):
            progress(i + 1, len(chapters) + 1)
            pipeline.sub(jid, "nb_chapters", done=i, total=len(chapters))
            c["text"] = write_one(i, story=story)
            story += " " + c["text"]
            log(f"  Chương {i + 1}/{len(chapters)}: {_words(c['text'])} chữ")
    else:
        # chương 1 viết ở slot chính (có sẵn dàn ý + dữ kiện trong cuộc trò chuyện); các chương khác chia đều cho
        # slot chính + slot phụ, mỗi slot 1 luồng làm lần lượt các chương của mình
        slots = [slot_id] + extra
        log(f"NotebookLM: viết {len(chapters)} chương song song trên {len(slots)} slot {slots}")
        queue = list(range(len(chapters)))
        q_lock = threading.Lock()
        results, errs, done_n = {}, {}, [0]

        def worker(slot, main):
            while True:
                with q_lock:
                    if not queue:
                        return
                    i = queue.pop(0)
                db.x("UPDATE nb_slots SET step=?, updated_at=? WHERE slot=?", (f"chapter_{i + 1}", time.time(), slot))
                try:
                    results[i] = write_one(i, slot=slot, solo=not (main and i == 0))
                    with q_lock:
                        done_n[0] += 1
                        progress(done_n[0], len(chapters) + 1)
                        pipeline.sub(jid, "nb_chapters", done=done_n[0], total=len(chapters))
                    log(f"  Chương {i + 1}/{len(chapters)} (slot {slot}): {_words(results[i])} chữ")
                except Exception as e:      # Stopped/lỗi khác cũng phải về luồng chính, không chết lặng trong thread
                    errs[i] = e
                    log(f"  Chương {i + 1} lỗi trên slot {slot}: {e}")
                    if not main or not isinstance(e, Unavailable):
                        return   # slot phụ hỏng → các chương còn lại để slot khác làm

        ths = [threading.Thread(target=worker, args=(s, s == slot_id), daemon=True) for s in slots]
        for t in ths:
            t.start()
        for t in ths:
            while t.is_alive():
                check()
                t.join(3)
        for e in errs.values():
            if not isinstance(e, Unavailable):
                raise e
        for i in sorted(errs):     # chương lỗi: làm lại tuần tự ở slot chính
            if i not in results:
                log(f"  Làm lại chương {i + 1} ở slot chính {slot_id}")
                results[i] = write_one(i, slot=slot_id, solo=True)
        for i in queue:            # chương chưa ai nhận (mọi slot phụ đã hỏng)
            results[i] = write_one(i, slot=slot_id, solo=True)
        for i, c in enumerate(chapters):
            c["text"] = results[i]
        if not is_short:
            _smooth_seams(jid, chapters, slot_id, deadline, check, log)
        story = " ".join(c["text"] for c in chapters)
    pipeline.sub(jid, "nb_chapters", "done", done=len(chapters), total=len(chapters))
    got = _words(story)
    log(f"NotebookLM: kịch bản {got} chữ ≈ {got / cfg.words_per_minute:.1f} phút (mục tiêu {target_m if is_short else cfg.target_minutes})")
    ol["chapters"] = chapters
    ol["engine"] = "notebooklm"
    # KHÔNG nhả slot ở đây: bước chia cảnh (split_scenes) vẫn dùng đúng slot này; worker sẽ release sau bước đó
    return ol


SPLIT_BLOCK_CHARS = 1700     # lời đọc gửi mỗi lượt chia cảnh (+ luật ~1000 ký tự < MAX_PROMPT)
STYLE_WHITEBOARD = ("minimalist hand-drawn sketch, notion-style doodle, dark grey pencil line art on warm beige paper "
                    "background #F5EBD7, simple clean composition")


def check_hook(text: str, title: str) -> bool:
    """B1/B2: 75 chữ đầu có dấu hỏi và có ít nhất một từ khoá của tiêu đề (câu hỏi trung tâm khớp tiêu đề)."""
    head = " ".join(text.split()[:75]).lower()
    keys = {w.lower() for w in re.findall(r"\w{4,}", title or "")}
    return "?" in head and any(k in head for k in keys)


def _sentence_blocks(sents: list[str]) -> list[tuple[int, int]]:
    out, a, size = [], 0, 0
    for i, s in enumerate(sents):
        if i > a and size + len(s) > SPLIT_BLOCK_CHARS:
            out.append((a, i))
            a, size = i, 0
        size += len(s)
    out.append((a, len(sents)))
    return out


def _check_cover(data, a: int, b: int, hook: bool) -> list[dict] | None:
    """Khoảng câu từ NotebookLM phải liền nhau, phủ kín [a, b), không chồng; hook (chương 1) đứng riêng câu 0."""
    raw = data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return None
    out, nxt = [], a
    for s in raw:
        try:
            f, t = int(s["from"]), int(s["to"])
        except (KeyError, TypeError, ValueError):
            return None
        if f != nxt or t < f or t >= b:
            return None
        out.append({"from": f, "to": t, "visual_concept": str(s.get("visual_concept") or "")[:300],
                    "image_prompt": strip_legacy(str(s.get("image_prompt") or "")).strip()})
        nxt = t + 1
    if nxt != b:
        return None
    if hook and not (out[0]["from"] == 0 and out[0]["to"] == 0):
        return None
    return out


def split_scenes(jid: int, nb: dict, cfg: Cfg, log, check, out_dir) -> list[list[dict]]:
    """Chia cảnh bằng NotebookLM trên ĐÚNG slot đã viết kịch bản. App đánh số câu (director.sentences); NotebookLM chỉ
    trả khoảng câu {from,to} + ý hình + prompt ảnh tiếng Anh; lời đọc luôn lấy từ câu gốc (không sửa chữ).
    Lỗi/không hợp lệ sau 1 lần hỏi lại → Unavailable (KHÔNG quay về Qwen). Kết quả lưu notebook_scenes.json."""
    from .director import sentences
    cache = out_dir / "notebook_scenes.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))["chapters"]
        except (OSError, ValueError, KeyError):
            pass
    sc = cfg.get("script") or {}
    deadline = time.time() + float(sc.get("wait_minutes", 20)) * 60
    if get_job_slot(jid) is None:
        bind(jid, log, check, deadline=deadline)          # giữ slot suốt bước chia cảnh
    out = []
    for ci, c in enumerate(nb["chapters"]):
        sents = sentences(c["text"])
        if not sents:
            raise Unavailable(f"chương {ci + 1} không có câu để chia cảnh")
        groups = []
        for a, b in _sentence_blocks(sents):
            hook = ci == 0 and a == 0
            got = None
            for attempt in range(2):
                numbered = "\n".join(f"[{i}] {sents[i]}" for i in range(a, b))
                prompt = (
                    f"Chia LỜI ĐỌC dưới đây thành các CẢNH hình ảnh cho video whiteboard. Các câu đánh số từ [{a}] đến "
                    f"[{b - 1}].\n{numbered}\n\n"
                    "Quy tắc:\n"
                    f"- Các khoảng from..to phải liền nhau, phủ kín từ {a} đến {b - 1}, không chồng lấn, không bỏ câu nào.\n"
                    "- Mỗi cảnh đọc khoảng 7 giây (tối đa 18 chữ). Một cảnh gồm 1 hoặc nhiều câu liền nhau; câu quá dài "
                    "thì vẫn để riêng một cảnh.\n"
                    + ("- Câu [0] là hook: đứng riêng một cảnh (from=0, to=0).\n" if hook else "")
                    + "- visual_concept: ý hình ảnh bằng tiếng Việt, bám đúng câu đang đọc.\n"
                    f"- image_prompt: TIẾNG ANH, 45-90 từ, bám đúng câu, vẽ đúng nhân vật/cờ/số liệu có trong câu. "
                    f"Kết thúc bằng: {STYLE_WHITEBOARD}. {WRITER_RULE_EN}\n"
                    "- KHÔNG viết lại lời đọc, không trích lại lời đọc.\n"
                    f"Chỉ trả về JSON giữa hai dòng {START} và {END}, dạng: "
                    '{"scenes":[{"from":0,"to":0,"visual_concept":"...","image_prompt":"..."}]}'
                    + ("" if attempt == 0 else "\nLần trước khoảng câu chưa phủ kín/chồng lấn. Làm lại đúng quy tắc."))
                try:
                    data = _json_block(ask(jid, f"scenes_{ci + 1}_{a}", prompt, deadline, check))
                except ValueError:
                    data = None
                got = _check_cover(data, a, b, hook)
                if got:
                    break
            if not got:
                raise Unavailable(f"NotebookLM chia cảnh chương {ci + 1} (câu {a}–{b - 1}) không hợp lệ")
            groups += got
        out.append(groups)
        log(f"  NotebookLM chia cảnh chương {ci + 1}/{len(nb['chapters'])}: {len(groups)} cảnh")
    out_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"chapters": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


IMG_RULES = (
    "Mỗi prompt bằng TIẾNG ANH, 45-90 từ, cho MỘT tranh minh hoạ vẽ tay kiểu whiteboard (nét mực xám đậm trên giấy "
    "be, tinh thần tranh biếm hoạ New Yorker): thể hiện ĐÚNG điều lời đọc nói – ai, đang làm gì, ở đâu; 3-6 yếu tố "
    "lấy từ danh từ/hành động trong lời đọc; một điểm nhấn chính; nhân vật có biểu cảm; bối cảnh phác nhẹ; ẩn dụ "
    "hình ảnh khi câu trừu tượng; nói rõ vị trí (trái/giữa/phải, gần/xa). " + WRITER_RULE_VI + " Không tô nền màu; chừa "
    "trống 1/5 phía dưới.")


def image_prompts(jid: int, ol: dict, rows: list, cfg: Cfg, log, check=lambda: None, idx: list | None = None,
                  batch: int = 7) -> int:
    """NotebookLM viết image_prompt cho các cảnh (theo lô, mỗi câu hỏi < MAX_PROMPT ký tự). Qwen chỉ giữ phần
    cấu trúc (visual_type, drawing_order…) và QC. Trả về số cảnh đã viết; lỗi → giữ prompt cũ của Qwen."""
    idx = list(range(len(rows))) if idx is None else idx
    deadline = time.time() + float((cfg.get("script") or {}).get("wait_minutes", 25)) * 60 * 2
    done = 0
    k = 0
    while k < len(idx):
        part = idx[k:k + batch]
        head = (f"Video '{ol.get('title', '')}'. Viết prompt tạo ảnh (Google Flow / Nano Banana) cho từng cảnh dưới "
                f"đây. {IMG_RULES}\n")
        tail = (f"\nChỉ trả về JSON (không khối code) giữa hai dòng {START} và {END}, dạng "
                '{"prompts": [{"i": số cảnh, "image_prompt": "...", "drawing_order": ["2-4 vật tiếng Anh theo thứ tự lời đọc nhắc tới"]}]}')
        body = "\n".join(f"Cảnh {i}: {rows[i]['narration']}" for i in part)
        while len(head + body + tail) > MAX_PROMPT and len(part) > 1:      # lô quá dài → bớt cảnh
            part = part[:-1]
            body = "\n".join(f"Cảnh {i}: {rows[i]['narration']}" for i in part)
        k += len(part)
        try:
            res = _json_block(ask(jid, f"img_{part[0]}", head + body + tail, deadline, check))
        except (Unavailable, ValueError) as e:
            log(f"  NotebookLM prompt ảnh cảnh {part[0]}–{part[-1]} lỗi ({e}) → giữ prompt của Qwen")
            if isinstance(e, Unavailable) and paused():
                break
            continue
        for p in res.get("prompts", []):
            try:
                i = int(p.get("i"))
            except (TypeError, ValueError):
                continue
            txt = re.sub(r"\s+", " ", str(p.get("image_prompt") or "")).strip()
            if i in part and len(txt.split()) >= 15:
                rows[i]["image_prompt"] = txt
                rows[i]["prompt_by"] = "notebooklm"
                # QC (Qwen) chấm theo ý chính + thứ tự vẽ → lấy từ chính prompt của NotebookLM cho khớp
                rows[i]["visual_concept"] = re.split(r"(?<=[.!?])\s", txt, 1)[0][:300]
                order = [str(x).strip() for x in (p.get("drawing_order") or []) if str(x).strip()]
                if order:
                    rows[i]["drawing_order"] = order[:4]
                done += 1
        log(f"  NotebookLM viết prompt ảnh: {min(k, len(idx))}/{len(idx)} cảnh")
    return done


def script_md(ol: dict) -> str:
    out = [f"# {ol.get('title', '')}", "", f"**Hook:** {ol.get('hook', '')}", ""]
    for i, c in enumerate(ol.get("chapters", [])):
        out += [f"## {i + 1}. [{c.get('beat')}] {c.get('title')}", "", c.get("text", ""), ""]
    return "\n".join(out)
