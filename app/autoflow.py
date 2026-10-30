"""Cầu nối với extension Auto Flow qua FILE: xuất prompts.txt theo dự án, nhận ảnh từ thư mục Save folder.
App không điều khiển extension; người dùng Import TXT + START trong Auto Flow."""
import csv
import json
import re
import shutil
import time
from pathlib import Path

from . import artifacts, db
from .config import Cfg, path

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def cfg_of(cfg: Cfg) -> dict:
    a = dict(cfg.get("autoflow") or {})
    a.setdefault("inbox_dir", str(Path.home() / "Downloads" / "AutoFlow" / "Image"))
    a.setdefault("exchange_dir", "exchange")
    a.setdefault("wait_minutes", 60)
    a.setdefault("poll_seconds", 10)
    a.setdefault("fallback_local", True)
    a.setdefault("prompt_max_chars", 900)
    return a


def job_dir(cfg: Cfg, jid: int) -> Path:
    """Thư mục làm việc của job. Job cũ giữ exchange/job_N (đường dẫn ảnh đã lưu trong DB); job mới làm thẳng trong
    thư mục riêng projects/<id>_<tên>/_lam_viec – đóng gói xong mọi thứ nằm chung một chỗ."""
    legacy = path(cfg_of(cfg)["exchange_dir"]) / f"job_{jid}"
    if legacy.exists():
        return legacy
    try:
        from .packager import folder_of
        root = folder_of(jid)
        root.mkdir(parents=True, exist_ok=True)
        from . import db
        if not (db.q("SELECT folder FROM jobs WHERE id=?", (jid,))[0].get("folder")):
            db.update_job(jid, folder=str(root))     # chốt tên thư mục (chủ đề có thể được sửa chính tả sau đó)
        d = root / "_lam_viec"
    except KeyError:
        d = legacy
    d.mkdir(parents=True, exist_ok=True)
    return d


def state(jid: int) -> dict:
    return db.kv_get(f"autoflow_{jid}") or {}


def save_state(jid: int, st: dict):
    db.kv_set(f"autoflow_{jid}", st)


def one_line(prompt: str, limit: int) -> str:
    p = re.sub(r"\s+", " ", prompt).strip()
    return p if len(p) <= limit else p[: limit - 1].rsplit(" ", 1)[0] + "."


def export_round(cfg: Cfg, jid: int, items: list[dict]) -> dict:
    """items: [{idx, uid, prompt}] theo thứ tự row (row = vị trí + 1)."""
    st = state(jid)
    n = st.get("round", 0) + 1
    d = job_dir(cfg, jid) / f"round_{n}"
    d.mkdir(parents=True, exist_ok=True)
    limit = cfg_of(cfg)["prompt_max_chars"]
    rows = [{"row": i + 1, "idx": it["idx"], "uid": it["uid"], "prompt": one_line(it["prompt"], limit)}
            for i, it in enumerate(items)]
    txt = d / "prompts.txt"
    txt.write_text("\n".join(r["prompt"] for r in rows) + "\n", encoding="utf-8")
    (d / "map.json").write_text(json.dumps({"round": n, "rows": rows}, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    st = {"round": n, "exported_at": time.time(), "txt": str(txt), "rows": rows, "processed": [],
          "received_rows": [], "warned_at": 0}
    save_state(jid, st)
    return st


def parse_name(stem: str) -> tuple[int | None, int]:
    """(row, slot) từ tên file Auto Flow: {row}_{prompt}_{slot}, {row}_{slot}, {prefix}{row}, {date}_{row}_..."""
    m = re.match(r"^\d{4}-?\d{2}-?\d{2}[_\- ]0*(\d+)", stem)        # date_prompt: {date}_{row}_...
    if m:
        return int(m.group(1)), 1
    m = re.match(r"^\d{10,}[_\- ]", stem)                             # timestamped: không có row
    if m:
        return None, 1
    m = re.match(r"^0*(\d+)(?:[_\- ]|$)", stem)
    slot = 1
    ms = re.search(r"[_\- ](\d{1,2})$", stem)
    if m:
        row = int(m.group(1))
        if ms and ms.start() >= m.end() - 1:
            slot = int(ms.group(1))
        return row, slot
    m = re.search(r"0*(\d+)$", stem)                                  # {prefix}{row}
    if m:
        return int(m.group(1)), 1
    return None, 1


def scan(cfg: Cfg, jid: int, extra_folder: str | None = None) -> list[dict]:
    """Ảnh mới (sau lúc xuất lượt hiện tại), đã ghi xong, chưa xử lý."""
    st = state(jid)
    if not st:
        return []
    af = cfg_of(cfg)
    roots = [Path(af["inbox_dir"]).expanduser()] + [Path(p).expanduser() for p in af.get("extra_inbox_dirs") or []] \
        + ([Path(extra_folder)] if extra_folder else [])
    since = st["exported_at"] - 5
    done = set(st.get("processed", []))
    out = []
    for root in roots:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if f.suffix.lower() not in IMG_EXT or str(f) in done:
                continue
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            if mt < since or time.time() - mt < 3:       # cũ, hoặc vừa ghi (có thể chưa xong)
                continue
            if not artifacts.valid(f) and f.suffix.lower() == ".png":
                continue
            row, slot = parse_name(f.stem)
            if row is None or not 1 <= row <= len(st["rows"]):
                continue
            out.append({"file": f, "row": row, "slot": slot, "mtime": mt})
    return sorted(out, key=lambda x: (x["row"], x["slot"], x["mtime"]))


def receive(cfg: Cfg, jid: int, item: dict) -> tuple[dict, Path]:
    """Copy ảnh vào exchange/received với tên theo cảnh; đánh dấu đã xử lý. Trả (row_info, đường dẫn mới)."""
    st = state(jid)
    r = st["rows"][item["row"] - 1]
    rec = job_dir(cfg, jid) / "received"
    rec.mkdir(exist_ok=True)
    dst = rec / f"scene_{r['idx']:03}_{r['uid']}_r{st['round']}_s{item['slot']}{item['file'].suffix.lower()}"
    if dst.suffix != ".png":
        import cv2
        import numpy as np
        img = cv2.imdecode(np.fromfile(str(item["file"]), dtype=np.uint8), cv2.IMREAD_COLOR)
        dst = dst.with_suffix(".png")
        cv2.imencode(".png", img)[1].tofile(str(dst))
    else:
        shutil.copyfile(item["file"], dst)
    st["processed"].append(str(item["file"]))
    if item["row"] not in st["received_rows"]:
        st["received_rows"].append(item["row"])
    save_state(jid, st)
    return r, dst


def reject(cfg: Cfg, jid: int, f: Path):
    d = job_dir(cfg, jid) / "rejected"
    d.mkdir(exist_ok=True)
    shutil.copyfile(f, d / f.name)


def write_report(cfg: Cfg, jid: int) -> Path:
    rows = []
    for s in db.scenes(jid):
        qc = json.loads(s["qc"]) if s.get("qc") else {}
        rows.append({"scene": s["idx"], "image": s.get("image") or "", "pass": qc.get("pass"),
                     "semantic": qc.get("semantic_score"), "visual": qc.get("visual_quality_score"),
                     "whiteboard": qc.get("whiteboard_suitability_score"), "has_text": qc.get("has_text"),
                     "tries": s.get("image_tries"), "issues": (qc.get("issues") or "")[:300]})
    d = job_dir(cfg, jid)
    (d / "report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    with (d / "report.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["scene"])
        w.writeheader()
        w.writerows(rows)
    return d / "report.csv"


def status(cfg: Cfg, jid: int) -> dict:
    st = state(jid)
    sc = db.scenes(jid)
    ok = sum(1 for s in sc if s.get("qc") and json.loads(s["qc"]).get("pass"))
    have = sum(1 for s in sc if s.get("image"))
    return {"job": jid, "round": st.get("round", 0), "prompts_txt": st.get("txt"),
            "rows_in_round": len(st.get("rows", [])), "received_in_round": len(st.get("received_rows", [])),
            "scenes": len(sc), "scenes_with_image": have, "scenes_passed": ok,
            "inbox_dir": cfg_of(cfg)["inbox_dir"], "exchange_dir": str(job_dir(cfg, jid)),
            "waiting_since": st.get("exported_at"), "missing_rows": [r["row"] for r in st.get("rows", [])
                                                                    if r["row"] not in st.get("received_rows", [])]}
