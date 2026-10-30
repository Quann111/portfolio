"""Các bước của một job theo thứ tự + đồng hồ thời gian từng bước (lưu ở jobs.steps).

Mỗi bước: {state: pending|running|done|error, active_sec (cộng dồn qua các lần chạy lại), started_at, ended_at,
done, total, note, runs}. Thời gian chạy thật chỉ tính lúc bước đang chạy – dừng app / tắt máy không bị cộng."""
import json
import time

from . import db

STEPS = [
    ("research", "🔎", "Nghiên cứu web + fact sheet"),
    ("script", "✍️", "Kịch bản NotebookLM + Qwen chia cảnh + prompt ảnh"),
    ("tts", "🎙", "Tạo giọng đọc + QC giọng"),
    ("image", "🖼", "Tạo ảnh Flow + QC ảnh"),
    ("render", "✏️", "Vẽ whiteboard"),
    ("assemble", "🎬", "Ghép video, nhạc, phụ đề"),
    ("qc", "✅", "Kiểm tra cuối + chấm điểm"),
    ("package", "📦", "Đóng gói thư mục job"),
]
NAMES = {k: n for k, _, n in STEPS}
ICONS = {k: i for k, i, _ in STEPS}
ORDER = [k for k, _, _ in STEPS]
# tiến độ con (progress) → bước
SUB = {"image_wait": "image", "review_wait": "script"}


def _load(jid: int) -> dict:
    r = db.q("SELECT steps FROM jobs WHERE id=?", (jid,))
    try:
        return json.loads(r[0]["steps"]) if r and r[0]["steps"] else {}
    except (ValueError, TypeError):
        return {}


def _save(jid: int, s: dict):
    db.update_job(jid, steps=json.dumps(s, ensure_ascii=False))


def _close(st: dict, now: float):
    if st.get("state") == "running" and st.get("since"):
        st["active_sec"] = round(st.get("active_sec", 0) + now - st["since"], 1)
        st["since"] = None


def start(jid: int, step: str):
    s, now = _load(jid), time.time()
    for k, st in s.items():                 # bước khác đang "running" (bị ngắt) → chốt thời gian
        if k != step:
            _close(st, now)
            if st.get("state") == "running":
                st["state"] = "pending"
    if step in ORDER:                        # bước trước đó chưa có dữ liệu (job làm trước khi có đồng hồ) → đã xong
        for k in ORDER[:ORDER.index(step)]:
            if k not in s:
                s[k] = {"state": "done", "active_sec": 0, "runs": 0, "note": "đã xong (chưa đo thời gian)"}
    st = s.setdefault(step, {"active_sec": 0, "runs": 0})
    st.update(state="running", since=now, runs=st.get("runs", 0) + 1)
    st.setdefault("started_at", now)
    s.setdefault("_job", {}).setdefault("started_at", now)
    _save(jid, s)
    if step in AUTO_SUB:
        sub(jid, AUTO_SUB[step])


def tick(jid: int, step: str, done: int, total: int, note: str = ""):
    src = step
    step = SUB.get(step, step)
    if step not in NAMES:
        return
    s = _load(jid)
    st = s.setdefault(step, {"active_sec": 0, "runs": 0})
    # bước ảnh có 2 nguồn tiến độ: vòng chờ Flow ("image_wait" = số cảnh đã có ảnh đạt) và QC từng ảnh ("image" =
    # số thứ tự cảnh vừa chấm). Chỉ lấy nguồn đầu – trộn hai nguồn làm thanh tiến độ giật lùi (101 → 46).
    if src == "image" and st.get("src") == "image_wait":
        return
    st["src"] = src
    st.update(done=done, total=total)
    if note:
        st["note"] = note[:200]
    _save(jid, s)
    if step in AUTO_SUB and total:
        sub(jid, AUTO_SUB[step], done=done, total=total)


def end(jid: int, step: str, ok: bool = True, note: str = ""):
    s, now = _load(jid), time.time()
    st = s.setdefault(step, {"active_sec": 0, "runs": 0})
    _close(st, now)
    st.update(state="done" if ok else "error", ended_at=now)
    if note:
        st["note"] = note[:300]
    if ok and step == ORDER[-1]:
        s.setdefault("_job", {})["finished_at"] = now
    _save(jid, s)
    if step in AUTO_SUB:
        sub(jid, AUTO_SUB[step], "done" if ok else "error")
    if step == "tts":
        s = _load(jid)
        e = (s.get("tts", {}).get("subs") or {}).get("extend")
        if e and e.get("state") == "running":
            sub(jid, "extend", "done")


# ---- bước con (13 bước chi tiết của plan) nằm trong bước lớn: research → web, factsheet; script → nb_outline,
# nb_chapters, split, nb_img; tts → voice, extend; image → flow; render → draw; assemble → mix; qc → final; package
SUBS = {
    "web": ("research", "Tìm nguồn web"), "factsheet": ("research", "Fact sheet"),
    "nb_outline": ("script", "NotebookLM dàn ý"), "nb_chapters": ("script", "NotebookLM viết chương"),
    "split": ("script", "Qwen chia cảnh + soát dữ kiện"), "nb_img": ("script", "NotebookLM prompt ảnh"),
    "voice": ("tts", "Giọng đọc"), "flow_parallel": ("tts", "⚡ Tạo ảnh Flow song song"), "extend": ("tts", "Đo thời lượng / bổ sung"),
    "flow": ("image", "Tạo ảnh Flow + QC"), "draw": ("render", "Vẽ whiteboard"),
    "mix": ("assemble", "Ghép video + nhạc + phụ đề"), "final": ("qc", "Kiểm tra cuối / điểm"),
    "pack": ("package", "Đóng gói thư mục"),
}


AUTO_SUB = {"tts": "voice", "image": "flow", "render": "draw", "assemble": "mix", "qc": "final", "package": "pack"}


def sub(jid: int, key: str, state: str = "running", done=None, total=None, note: str = ""):
    """Ghi bước con: state running|done|error; thời gian bước con = từ lần đầu running tới done."""
    if key not in SUBS:
        return
    s, now = _load(jid), time.time()
    parent = s.setdefault(SUBS[key][0], {"active_sec": 0, "runs": 0})
    sb = parent.setdefault("subs", {}).setdefault(key, {})
    if state == "running" and sb.get("state") != "running":
        sb["since"] = now
        sb.setdefault("started_at", now)
    if state in ("done", "error") and sb.get("since"):
        sb["active_sec"] = round(sb.get("active_sec", 0) + now - sb["since"], 1)
        sb["since"] = None
    sb["state"] = state
    if done is not None:
        sb["done"] = done
    if total is not None:
        sb["total"] = total
    if note:
        sb["note"] = note[:200]
    _save(jid, s)


def add_wait(jid: int, step: str, sec: float, why: str = ""):
    """Thời gian CHỜ trong một bước (không có profile Flow nào chạy, NotebookLM chưa sẵn sàng, chờ duyệt…) –
    tách khỏi thời gian chạy thật."""
    if sec <= 0:
        return
    s = _load(jid)
    st = s.setdefault(step, {"active_sec": 0, "runs": 0})
    st["wait_sec"] = round(st.get("wait_sec", 0) + sec, 1)
    if why:
        st["wait_why"] = why[:120]
    _save(jid, s)


def set_metrics(jid: int, **kw):
    s = _load(jid)
    s.setdefault("_job", {}).setdefault("metrics", {}).update(kw)
    _save(jid, s)


def pause_all(jid: int):
    """App dừng / lỗi giữa chừng: chốt thời gian bước đang chạy (không cộng lúc app tắt)."""
    s, now = _load(jid), time.time()
    for k, st in s.items():
        if not k.startswith("_"):
            _close(st, now)
            if st.get("state") == "running":
                st["state"] = "pending"
    _save(jid, s)


def view(jid: int, current_stage: str | None = None) -> dict:
    """Timeline cho giao diện: danh sách bước theo thứ tự + tổng thời gian + ước tính còn lại."""
    s, now = _load(jid), time.time()
    rows, total, waits = [], 0.0, 0.0
    for k, icon, name in STEPS:
        st = dict(s.get(k, {}))
        spent = st.get("active_sec", 0) + ((now - st["since"]) if st.get("state") == "running" and st.get("since")
                                           else 0)
        wait = min(spent, st.get("wait_sec", 0))
        active = spent - wait                 # thời gian chạy thật = thời gian ở bước trừ thời gian chờ
        total += active
        waits += wait
        eta = None
        if st.get("state") == "running" and st.get("done") and st.get("total") and st["done"] < st["total"]:
            eta = round(spent / st["done"] * (st["total"] - st["done"]))
        subs = []
        for key, (parent, sname) in SUBS.items():
            if parent != k:
                continue
            sb = (st.get("subs") or {}).get(key, {})
            sa = sb.get("active_sec", 0) + ((now - sb["since"]) if sb.get("state") == "running" and sb.get("since")
                                            else 0)
            subs.append({"key": key, "name": sname, "state": sb.get("state", st.get("state") if st.get("state") == "done"
                                                                         else "pending"),
                         "active_sec": round(sa), "done": sb.get("done"), "total": sb.get("total"),
                         "note": sb.get("note", "")})
        rows.append({"key": k, "icon": icon, "name": name, "state": st.get("state", "pending"),
                     "active_sec": round(active), "wait_sec": round(wait), "wait_why": st.get("wait_why", ""),
                     "done": st.get("done"), "total": st.get("total"), "subs": subs,
                     "note": st.get("note", ""), "runs": st.get("runs", 0), "eta_sec": eta})
    job = s.get("_job", {})
    if not total and job.get("active_est"):     # job cũ: chỉ có ước tính từ nhật ký
        total = job["active_est"]
    wall = ((job.get("finished_at") or now) - job["started_at"]) if job.get("started_at") else 0
    return {"steps": rows, "active_sec": round(total), "wall_sec": round(wall),
            "wait_sec": round(max(waits, wall - total)), "finished": bool(job.get("finished_at")),
            "estimated": bool(job.get("estimated")), "metrics": job.get("metrics", {})}


def metrics(jid: int, v: dict | None = None) -> dict:
    """Chỉ số phụ: giây/cảnh cho giọng, ảnh, vẽ; tỉ lệ ảnh đạt QC ngay lần đầu; từng profile Flow;
    phút làm cho mỗi phút video."""
    v = v or view(jid)
    sc = db.scenes(jid)
    n = max(1, len(sc))
    by = {s["key"]: s for s in v["steps"]}
    video_sec = sum((s.get("duration_ms") or 0) for s in sc) / 1000
    first = sum(1 for s in sc if s.get("image") and (s.get("image_tries") or 0) <= 1)
    with_img = sum(1 for s in sc if s.get("image"))
    prof = {}
    for t in db.q("SELECT worker, claimed_at, done_at FROM flow_tasks WHERE job_id=? AND status='done' "
                  "AND claimed_at IS NOT NULL AND done_at IS NOT NULL", (jid,)):
        p = prof.setdefault(t["worker"] or "(extension bản cũ)", {"anh": 0, "giay": 0.0})
        p["anh"] += 1
        p["giay"] += max(0, t["done_at"] - t["claimed_at"])
    names = {k: (w.get("name") or k[:6]) for k, w in (db.kv_get("flowbridge_workers") or {}).items()}
    return {
        "so_canh": len(sc), "phut_video": round(video_sec / 60, 1),
        "giay_moi_canh": {k: round(by[k]["active_sec"] / n, 1) for k in ("tts", "image", "render")
                          if k in by and by[k]["active_sec"]},
        "qc_dat_lan_dau_pct": round(100 * first / with_img) if with_img else None,
        "profile": [{"ten": names.get(k, k), "anh": p["anh"], "giay_moi_anh": round(p["giay"] / p["anh"])}
                    for k, p in sorted(prof.items(), key=lambda x: -x[1]["anh"])],
        "phut_lam_moi_phut_video": round(v["active_sec"] / video_sec, 1) if video_sec else None,
    }


def estimate_from_log(jid: int, topic: str, log_path) -> bool:
    """Job làm trước khi có đồng hồ: ước tính từ nhật ký – mỗi lần '=== Job N' tới dòng kế tiếp của job khác /
    '✔ Xong' / 'Đã dừng' là một đoạn chạy; cộng các đoạn = thời gian chạy thật ước tính."""
    import re
    from datetime import datetime
    s = _load(jid)
    if s.get("_job", {}).get("started_at") or not log_path.exists():
        return False
    pat = re.compile(r"^\[(\d\d-\d\d \d\d:\d\d:\d\d)\] (.*)")
    year = datetime.now().year
    runs, start, last, first, end = [], None, None, None, None
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.match(line)
        if not m:
            continue
        t = datetime.strptime(f"{year}-{m.group(1)}", "%Y-%m-%d %H:%M:%S").timestamp()
        msg = m.group(2)
        if msg.startswith(f"=== Job {jid}:"):
            if start is not None and last:
                runs.append(last - start)
            start, first = t, first or t
        elif start is not None:
            if msg.startswith("=== Job ") or "Đã dừng" in msg or msg.startswith("▶ Bắt đầu"):
                runs.append((last or t) - start)
                start = None
            elif msg.startswith("✔ Xong") and topic[:20] in msg:
                runs.append(t - start)
                end, start = t, None
            else:
                last = t
    if not first or not runs:
        return False
    s.setdefault("_job", {}).update(started_at=first, finished_at=end or last, active_est=round(sum(runs)),
                                    estimated=True)
    for k in ORDER:
        s.setdefault(k, {"state": "done", "active_sec": 0, "runs": 0, "note": "ước tính (job làm trước khi có đồng hồ)"})
    _save(jid, s)
    return True


def fmt(sec: float) -> str:
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h} giờ {m} phút" if h else (f"{m} phút {s} giây" if m else f"{s} giây")
