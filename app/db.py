import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import ROOT

DB_PATH = ROOT / "data" / "session.db"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed
  stage TEXT NOT NULL DEFAULT 'script',    -- script | tts | image | render | assemble | done
  position REAL NOT NULL,
  outline TEXT,
  output TEXT,
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS scenes(
  job_id INTEGER NOT NULL,
  idx INTEGER NOT NULL,
  chapter INTEGER,
  narration TEXT,
  visual TEXT,
  audio TEXT, duration_ms INTEGER,
  image TEXT, image_tries INTEGER NOT NULL DEFAULT 0,
  clip TEXT,
  PRIMARY KEY(job_id, idx)
);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS flow_tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER, idx INTEGER, uid TEXT, round INTEGER,
  prompt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued | claimed | done | error | cancelled
  claimed_at REAL, done_at REAL, image TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS nb_tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER, step TEXT,
  kind TEXT NOT NULL DEFAULT 'ask',        -- new (tạo sổ ghi chú mới) | ask (gửi prompt, lấy câu trả lời)
  prompt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued | claimed | done | error | cancelled
  claimed_at REAL, done_at REAL, result TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS gemini_tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER, step TEXT,
  kind TEXT NOT NULL DEFAULT 'ask',
  prompt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued | claimed | done | error | cancelled
  claimed_at REAL, done_at REAL, result TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL
);
"""

MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN facts TEXT",
    "ALTER TABLE jobs ADD COLUMN scores TEXT",
    "ALTER TABLE jobs ADD COLUMN repairs INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scenes ADD COLUMN data TEXT",
    "ALTER TABLE scenes ADD COLUMN qc TEXT",
    "ALTER TABLE jobs ADD COLUMN versions TEXT",
    "ALTER TABLE scenes ADD COLUMN audio_qc TEXT",
    "ALTER TABLE scenes ADD COLUMN tts_tries INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scenes ADD COLUMN render_tries INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scenes ADD COLUMN lead_ms INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE flow_tasks ADD COLUMN worker TEXT",          # Chrome profile nào đang/đã tạo ảnh này
    "ALTER TABLE jobs ADD COLUMN steps TEXT",                 # pipeline: trạng thái + thời gian từng bước
    "ALTER TABLE jobs ADD COLUMN folder TEXT",                # thư mục riêng của job (projects/…)
    "ALTER TABLE jobs ADD COLUMN target_minutes REAL",       # thời lượng mục tiêu (phút) của job
]


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=FULL")
    return c


def init():
    with _lock, _conn() as c:
        c.executescript(SCHEMA)
        for m in MIGRATIONS:
            try:
                c.execute(m)
            except sqlite3.OperationalError:
                pass
        # job bị ngắt giữa chừng (tắt app/crash) -> trả về hàng đợi để làm tiếp
        c.execute("UPDATE jobs SET status='queued' WHERE status IN ('running','preparing')")


def q(sql, args=()):
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def x(sql, args=()):
    with _lock, _conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def add_topic(topic: str, target_minutes: float | None = None) -> int:
    topic = topic.strip()
    now = time.time()
    with _lock, _conn() as c:   # tính position + INSERT trong cùng 1 giao dịch (không trùng vị trí)
        return c.execute("INSERT INTO jobs(topic,stage,position,created_at,updated_at,target_minutes) "
                         "VALUES(?,?,(SELECT COALESCE(MAX(position),0)+1 FROM jobs),?,?,?)",
                         (topic, "research", now, now, target_minutes)).lastrowid


def jobs():
    return q("SELECT * FROM jobs ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'review' THEN 1 "
             "WHEN 'preparing' THEN 2 WHEN 'queued' THEN 3 WHEN 'failed' THEN 4 ELSE 5 END, position")


def next_job():
    r = q("SELECT * FROM jobs WHERE status='queued' ORDER BY position LIMIT 1")
    return r[0] if r else None


def claim_job(job_id, to: str, frm: str = "queued") -> bool:
    """Nhận job nguyên tử (luồng chính / luồng chuẩn bị không giành cùng 1 job)."""
    with _lock, _conn() as c:
        return c.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=? AND status=?",
                         (to, time.time(), job_id, frm)).rowcount == 1


JOB_COLS = {"topic", "status", "stage", "position", "outline", "output", "error", "attempts", "created_at",
            "updated_at", "facts", "scores", "repairs", "versions", "steps", "folder", "target_minutes"}


def claim_task(table: str, select_sql: str, args=(), worker: str | None = None) -> dict | None:
    """Nhận 1 task nguyên tử: SELECT + UPDATE trong cùng khoá/giao dịch, chỉ nhận khi vẫn còn 'queued'
    (2 tab/profile poll cùng lúc không nhận trùng). select_sql trả về cột id."""
    with _lock, _conn() as c:
        r = c.execute(select_sql, args).fetchone()
        if not r:
            return None
        sets = "status='claimed', claimed_at=?, attempts=attempts+1" + (", worker=?" if worker is not None else "")
        vals = (time.time(),) + ((worker,) if worker is not None else ())
        if c.execute(f"UPDATE {table} SET {sets} WHERE id=? AND status='queued'", (*vals, r["id"])).rowcount != 1:
            return None
        return dict(c.execute(f"SELECT * FROM {table} WHERE id=?", (r["id"],)).fetchone())


def update_job(job_id, **kw):
    bad = set(kw) - JOB_COLS
    if bad:
        raise ValueError(f"cột jobs không hợp lệ: {bad}")
    kw["updated_at"] = time.time()
    cols = ",".join(f"{k}=?" for k in kw)
    x(f"UPDATE jobs SET {cols} WHERE id=?", (*kw.values(), job_id))


def move_job(job_id, delta):
    rows = q("SELECT id,position FROM jobs WHERE status='queued' ORDER BY position")
    ids = [r["id"] for r in rows]
    if job_id not in ids:
        return
    i = ids.index(job_id)
    j = i + delta
    if 0 <= j < len(ids):
        a, b = rows[i], rows[j]
        with _lock, _conn() as c:
            c.execute("UPDATE jobs SET position=? WHERE id=?", (b["position"], a["id"]))
            c.execute("UPDATE jobs SET position=? WHERE id=?", (a["position"], b["id"]))


def move_job_before(job_id, before_id=None):
    """Kéo thả: đặt job_id (đang chờ) ngay trước before_id; None = xuống cuối hàng chờ."""
    ids = [r["id"] for r in q("SELECT id FROM jobs WHERE status='queued' ORDER BY position")]
    if job_id not in ids:
        return
    ids.remove(job_id)
    ids.insert(ids.index(before_id) if before_id in ids else len(ids), job_id)
    with _lock, _conn() as c:
        c.executemany("UPDATE jobs SET position=? WHERE id=?", [(float(i), k) for i, k in enumerate(ids, 1)])


def delete_job(job_id):
    x("DELETE FROM scenes WHERE job_id=?", (job_id,))
    x("DELETE FROM jobs WHERE id=?", (job_id,))


def retry_job(job_id) -> bool:
    """Chỉ cho chạy lại job đã dừng (không đụng job đang chạy/đang chuẩn bị → tránh chạy đôi)."""
    with _lock, _conn() as c:
        return c.execute("UPDATE jobs SET status='queued', error=NULL, attempts=0, updated_at=? "
                         "WHERE id=? AND status NOT IN ('running','preparing')",
                         (time.time(), job_id)).rowcount == 1


def scenes(job_id):
    return q("SELECT * FROM scenes WHERE job_id=? ORDER BY idx", (job_id,))


def insert_scenes(job_id, rows):
    with _lock, _conn() as c:
        c.execute("DELETE FROM scenes WHERE job_id=?", (job_id,))
        c.executemany("INSERT INTO scenes(job_id,idx,chapter,narration,visual,data) VALUES(?,?,?,?,?,?)",
                      [(job_id, i, r["chapter"], r["narration"], r["image_prompt"],
                        json.dumps(r, ensure_ascii=False)) for i, r in enumerate(rows)])


SCENE_COLS = {"job_id", "idx", "chapter", "narration", "visual", "audio", "duration_ms", "image", "image_tries",
              "clip", "data", "qc", "audio_qc", "tts_tries", "render_tries", "lead_ms"}


def scene_uid(job_id, s) -> str:
    """Mã cố định của cảnh (không đổi khi đánh số lại) – dùng đặt tên file."""
    d = json.loads(s["data"]) if s.get("data") else {}
    if not d.get("uid"):
        import uuid
        with _lock, _conn() as c:   # đọc lại trong khoá: luồng khác có thể vừa tạo uid
            r = c.execute("SELECT data FROM scenes WHERE job_id=? AND idx=?", (job_id, s["idx"])).fetchone()
            d = json.loads(r["data"]) if r and r["data"] else d
            if not d.get("uid"):
                d["uid"] = uuid.uuid4().hex[:8]
                c.execute("UPDATE scenes SET data=? WHERE job_id=? AND idx=?",
                          (json.dumps(d, ensure_ascii=False), job_id, s["idx"]))
        s["data"] = json.dumps(d, ensure_ascii=False)
    return d["uid"]


def replace_scenes(job_id, full_rows):
    """Ghi lại toàn bộ cảnh (đã đánh số lại idx) – giữ mọi cột hiện có (audio, ảnh, qc...)."""
    with _lock, _conn() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(scenes)")]
        c.execute("DELETE FROM scenes WHERE job_id=?", (job_id,))
        for i, r in enumerate(full_rows):
            row = {k: r.get(k) for k in cols}
            row.update(job_id=job_id, idx=i)
            for k in ("image_tries", "tts_tries", "render_tries", "lead_ms"):
                row[k] = row.get(k) or 0
            c.execute(f"INSERT INTO scenes({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      [row[k] for k in cols])


def scene_data(s) -> dict:
    d = json.loads(s["data"]) if s.get("data") else {}
    d["narration"], d["image_prompt"] = s["narration"], s["visual"]
    return d


def update_scene(job_id, idx, **kw):
    bad = set(kw) - SCENE_COLS
    if bad:
        raise ValueError(f"cột scenes không hợp lệ: {bad}")
    cols = ",".join(f"{k}=?" for k in kw)
    x(f"UPDATE scenes SET {cols} WHERE job_id=? AND idx=?", (*kw.values(), job_id, idx))


def kv_get(k, default=None):
    r = q("SELECT v FROM kv WHERE k=?", (k,))
    if not r or r[0]["v"] is None:
        return default
    try:
        return json.loads(r[0]["v"])
    except Exception:
        return default


def kv_set(k, v):
    x("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


def file_ok(p) -> bool:
    return bool(p) and Path(p).exists() and Path(p).stat().st_size > 0
