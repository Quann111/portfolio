"""Danh sách giọng đọc cho người dùng chọn: mỗi lựa chọn là "engine|voice".
- zerotts|<tên>: giọng có sẵn của ZeroTTS (mặc định baotrang)
- vieneu|u:<file>: clone từ giọng mẫu người dùng tải lên (data/voices/)"""
import json
from pathlib import Path

from . import db
from .config import ROOT

VOICES_DIR = ROOT / "data" / "voices"
ZEROTTS = ("baotrang", "giahuy", "hamy", "huuduc", "kimoanh", "maichi", "quangminh", "tiendat")
MAX_REF_SEC = 15


def uploads() -> list[str]:
    return sorted(f.name for f in VOICES_DIR.glob("*.wav")) if VOICES_DIR.exists() else []


def options() -> list[dict]:
    opts = [{"id": f"zerotts|{v}", "label": f"ZeroTTS – {v}{' (Mặc định)' if v == 'baotrang' else ''}"} for v in ZEROTTS]
    for f in uploads():
        opts += [{"id": f"vieneu|u:{f}", "label": f"VieNeu – giọng tải lên {f}"}]
    return opts


def valid(vid: str) -> bool:
    return any(o["id"] == vid for o in options())


def default(cfg) -> str:
    v = db.kv_get("voice_default")
    if v and valid(v):
        return v
    t = cfg.get("tts") or {}
    eng = (t.get("engine") or "zerotts").lower()
    if eng == "zerotts":
        return f"zerotts|{t.get('voice') or 'baotrang'}"
    if eng == "vieneu":
        up = uploads()
        return f"vieneu|u:{up[0]}" if up else "zerotts|baotrang"
    return "zerotts|baotrang"


def _versions(jid):
    rows = db.q("SELECT status, outline, versions FROM jobs WHERE id=?", (jid,))
    return (rows[0], json.loads(rows[0]["versions"] or "{}")) if rows else (None, None)


def of_job(jid: int, cfg) -> str:
    _, v = _versions(jid)
    if v and v.get("voice") and valid(v["voice"]):
        return v["voice"]
    return default(cfg)


def change(jid: int, voice_id: str):
    if not valid(voice_id):
        raise ValueError(f"Giọng '{voice_id}' không hợp lệ")
    row, v = _versions(jid)
    if not row:
        return
    v["voice"] = voice_id
    db.x("UPDATE jobs SET versions=? WHERE id=?", (json.dumps(v), jid))


def preview(voice_id: str, cfg, log) -> tuple[bytes, str]:
    """Tạo mẫu giọng 1 câu ngắn: trả về (bytes wav, mime)."""
    from .tts import TTS
    eng = voice_id.split("|")[0]
    sub = voice_id.split("|")[1] if "|" in voice_id else ""
    tts = TTS(cfg, log, engine=eng, voice=sub)
    text = "Xin chào, đây là giọng đọc thử nghiệm để bạn kiểm tra chất lượng âm thanh."
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        p = Path(tmp.name)
    try:
        tts.speak(text, p)
        data = p.read_bytes()
        return data, "audio/wav"
    finally:
        p.unlink(missing_ok=True)
