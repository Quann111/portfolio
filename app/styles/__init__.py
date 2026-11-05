"""Kiểu dựng video. classic = mỗi cảnh một ảnh/clip (luồng gốc); nikola = bảng hai đảo theo repo
hi-nikola/hand-drawn-explainer-video-nikola (xem app/styles/nikola.py); opuscar = dựng phim bằng code
theo repo lemo-opuscar, Chrome headless chụp khung + ffmpeg ghép (xem app/opuscar)."""
import json
import shutil
from pathlib import Path

from .. import db
from ..config import ROOT

STYLES = ("classic", "nikola", "opuscar")


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


def opuscar_available(cfg=None) -> bool:
    """True nếu có node, vendor\opuscar\node_modules và file Chrome theo cfg.opuscar.chrome."""
    if not shutil.which("node"):
        return False
    nm = ROOT / "vendor" / "opuscar" / "node_modules"
    if not nm.is_dir():
        return False
    if cfg is None:
        from ..config import load as _load
        cfg = _load()
    chrome = (cfg.get("opuscar") or {}).get("chrome")
    if not chrome or not Path(chrome).is_file():
        return False
    return True


def format_of(jid: int) -> str:
    """Định dạng video của job ('long' | 'short'), lưu trong versions.format; mặc định 'long'."""
    rows = db.q("SELECT versions FROM jobs WHERE id=?", (jid,))
    v = json.loads((rows[0]["versions"] if rows else None) or "{}")
    f = v.get("format")
    return f if f in ("long", "short") else "long"


def seconds_of(jid: int) -> int:
    """Số giây mục tiêu của job (versions.target_seconds); 0 = chưa có / dùng mặc định của định dạng."""
    rows = db.q("SELECT versions FROM jobs WHERE id=?", (jid,))
    v = json.loads((rows[0]["versions"] if rows else None) or "{}")
    try:
        return int(v.get("target_seconds") or 0)
    except (TypeError, ValueError):
        return 0


def set_format(jid: int, fmt: str, seconds: int | None = None) -> None:
    """Lưu định dạng + giây mục tiêu vào versions của job."""
    rows = db.q("SELECT versions FROM jobs WHERE id=?", (jid,))
    v = json.loads((rows[0]["versions"] if rows else None) or "{}")
    if fmt not in ("long", "short"):
        fmt = "long"
    v["format"] = fmt
    if seconds is not None:
        try:
            v["target_seconds"] = int(seconds)
        except (TypeError, ValueError):
            pass
    else:
        v.pop("target_seconds", None)
    db.update_job(jid, versions=json.dumps(v, ensure_ascii=False))
