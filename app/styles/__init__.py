"""Kiểu dựng video. classic = mỗi cảnh một ảnh/clip (luồng gốc); nikola = bảng hai đảo theo repo
hi-nikola/hand-drawn-explainer-video-nikola (xem app/styles/nikola.py)."""
import json

from .. import db

STYLES = ("classic", "nikola")


def default(cfg) -> str:
    s = (db.kv_get("style_default") or (cfg.get("style") or {}).get("default") or "classic").lower()
    return s if s in STYLES else "classic"


def of_job(jid: int, cfg=None) -> str:
    """Kiểu của job (khoá trong versions.style); job chưa khoá → kiểu mặc định."""
    rows = db.q("SELECT versions FROM jobs WHERE id=?", (jid,))
    v = json.loads((rows[0]["versions"] if rows else None) or "{}")
    if v.get("style") in STYLES:
        return v["style"]
    return default(cfg) if cfg is not None else "classic"


def lock(jid: int, style: str | None, cfg) -> str:
    """Ghi kiểu vào versions.style nếu chưa có (job đã bắt đầu thì không đổi kiểu nữa)."""
    rows = db.q("SELECT versions FROM jobs WHERE id=?", (jid,))
    v = json.loads((rows[0]["versions"] if rows else None) or "{}")
    if v.get("style") in STYLES:
        return v["style"]
    v["style"] = style if style in STYLES else default(cfg)
    db.update_job(jid, versions=json.dumps(v, ensure_ascii=False))
    return v["style"]


def change(jid: int, style: str) -> str | None:
    """Đổi kiểu của job chưa chạy và chưa có dàn ý/kịch bản. Trả về lý do nếu không đổi được."""
    rows = db.q("SELECT status, outline, versions FROM jobs WHERE id=?", (jid,))
    if not rows:
        return "không có job"
    j = rows[0]
    if j["status"] == "running":
        return "job đang chạy"
    if j["outline"] or db.scenes(jid):
        return "job đã có kịch bản – bấm 🔁 Chạy lại nếu muốn làm lại theo kiểu khác"
    v = json.loads(j["versions"] or "{}")
    v["style"] = style
    db.update_job(jid, versions=json.dumps(v, ensure_ascii=False))
    return None
