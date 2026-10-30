import datetime as dt
import gzip
import hashlib
import json
import shutil
import time
from pathlib import Path

from . import db, resources
from .config import CHANNEL_PATH, CONFIG_PATH, ROOT, path

APP_VERSION = "3.0.0"
PROMPT_FILES = ["director.py", "planner.py", "image.py", "research.py", "video_qc.py", "websearch.py",
                "story_quality.py"]


def _hash_files(files) -> str:
    h = hashlib.sha1()
    for f in files:
        if Path(f).exists():
            h.update(Path(f).read_bytes())
    return h.hexdigest()[:10]


def versions(cfg) -> dict:
    app = ROOT / "app"
    return {
        "app": APP_VERSION,
        "prompts": _hash_files(app / f for f in PROMPT_FILES),
        "channel": _hash_files([CHANNEL_PATH]),
        "config": _hash_files([CONFIG_PATH]),
        "llm": cfg.lmstudio.model,
        "image_model": Path(cfg.image.diffusion_model).name,
        "tts_model": cfg.tts.model,
        "voice": _hash_files([ROOT / "data" / "voice.pt"]),
        "research_mode": (cfg.get("research") or {}).get("mode", "web"),
        "image_provider": cfg.image.get("provider", "local"),
    }


def _model_files(cfg):
    c = cfg.image
    return [path(c.diffusion_model), path(c.t5xxl), path(c.clip_l), path(c.vae)]


def daily(cfg, log) -> dict:
    t0 = time.time()
    report = {"date": dt.date.today().isoformat(), "issues": []}
    # 1. SQLite
    ok = db.q("PRAGMA integrity_check")[0]
    report["db_integrity"] = list(ok.values())[0]
    if report["db_integrity"] != "ok":
        report["issues"].append("database integrity lỗi")
    # 1b. sao lưu DB (giữ 7 bản) và thử mở lại bản sao lưu
    report["backup"] = backup_db()
    # 2. thư mục work mồ côi (job đã xong/bị xoá)
    active = {j["id"] for j in db.jobs() if j["status"] in ("queued", "running", "review")}
    removed = 0
    for d in path(cfg.work_dir).glob("job_*"):
        try:
            jid = int(d.name.split("_")[1])
        except ValueError:
            continue
        if jid not in active:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    report["orphan_work_dirs_removed"] = removed
    # 3. nén log > 5MB
    log_file = ROOT / "data" / "autovideo.log"
    if log_file.exists() and log_file.stat().st_size > 5 * 1024**2:
        arch = ROOT / "data" / "logs" / f"autovideo_{dt.datetime.now():%Y%m%d_%H%M}.log.gz"
        arch.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("rb") as src, gzip.open(arch, "wb") as dst:
            shutil.copyfileobj(src, dst)
        log_file.write_text("", encoding="utf-8")
        report["log_rotated"] = str(arch)
    # 4. model + output
    # model FLUX chỉ cần khi tạo ảnh local (đã chuyển sang Google Flow → không kiểm)
    local = (cfg.image.get("provider") or "local") == "local"
    missing = [str(p) for p in _model_files(cfg) if not p.exists()] if local else []
    if missing:
        report["issues"].append(f"thiếu model: {missing}")
    bad_out = [j["id"] for j in db.jobs() if j["status"] == "done" and j["output"] and not Path(j["output"]).exists()]
    if bad_out:
        report["issues"].append(f"video đã xong nhưng mất file: {bad_out}")
    # 5. thống kê 24h
    since = time.time() - 86400
    done = [j for j in db.jobs() if j["status"] == "done" and (j["updated_at"] or 0) >= since]
    scores = [json.loads(j["scores"])["total"] for j in done if j.get("scores")]
    report.update(
        videos_24h=len(done), avg_score_24h=round(sum(scores) / len(scores), 1) if scores else None,
        failed_total=sum(1 for j in db.jobs() if j["status"] == "failed"),
        resources=resources.snapshot(), seconds=round(time.time() - t0, 1))
    # 6. hằng tuần (Chủ nhật)
    if dt.date.today().weekday() == 6:
        db.x("VACUUM")
        db.x("REINDEX")
        report["weekly"] = {
            "failed_jobs": [(j["id"], j["topic"], (j["error"] or "")[:120]) for j in db.jobs() if j["status"] == "failed"],
            "models": {p.name: p.stat().st_size for p in _model_files(cfg) if p.exists()},
        }
    out = ROOT / "data" / "health" / f"{report['date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"🩺 Bảo trì xong: {out.name}" + (f" – vấn đề: {report['issues']}" if report["issues"] else " – ổn"))
    return report


def backup_db(keep: int = 7) -> dict:
    import sqlite3
    d = db.DB_PATH.parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"session_{dt.datetime.now():%Y%m%d_%H%M}.db"
    src = sqlite3.connect(db.DB_PATH)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)
    src.close()
    ok = out.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    jobs = out.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    out.close()
    for old in sorted(d.glob("session_*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return {"file": dst.name, "restorable": ok, "jobs": jobs}


def due(cfg) -> bool:
    hour = cfg.get("maintenance_hour", 3)
    now = dt.datetime.now()
    last = db.kv_get("maintenance_last")
    return now.hour >= hour and last != now.date().isoformat()


def mark_done():
    db.kv_set("maintenance_last", dt.date.today().isoformat())
