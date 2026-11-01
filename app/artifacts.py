"""Artifact manifest: chỉ coi một file là xong khi đã kiểm tra hợp lệ và ghi checksum."""
import hashlib
import json
import subprocess
from pathlib import Path

import soundfile as sf

from .llm import NO_WINDOW

MANIFEST = "manifest.json"


def _sha(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe_video(p: Path) -> float | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                        "-of", "json", str(p)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120, creationflags=NO_WINDOW)
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout)
        return float(d["format"]["duration"]) if any(s["codec_type"] == "video" for s in d["streams"]) else None
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def valid(p) -> bool:
    """Kiểm tra nội dung: ảnh decode được, wav đọc được, video có stream video và thời lượng > 0."""
    if not p:
        return False
    p = Path(p)
    if not p.exists() or p.stat().st_size == 0:
        return False
    ext = p.suffix.lower()
    try:
        if ext == ".png":
            import cv2
            import numpy as np
            img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
            return img is not None and img.size > 0
        if ext == ".wav":
            info = sf.info(str(p))
            return info.frames > 0
        if ext == ".mp4":
            d = _probe_video(p)
            return bool(d and d > 0)
    except Exception:
        return False
    return True


class Manifest:
    def __init__(self, work: Path):
        self.path = Path(work) / MANIFEST
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, p, kind: str):
        p = Path(p)
        self.data[p.name] = {"kind": kind, "size": p.stat().st_size, "sha1": _sha(p), "path": str(p)}
        self._save()

    def ok(self, p) -> bool:
        """Đã ghi manifest, kích thước + checksum khớp và nội dung hợp lệ."""
        if not p:
            return False
        p = Path(p)
        e = self.data.get(p.name)
        if not e or not p.exists() or p.stat().st_size != e["size"]:
            return False
        return _sha(p) == e["sha1"] and valid(p)

    def check_all(self) -> list[str]:
        bad = []
        for name, e in list(self.data.items()):
            # ảnh Flow nằm ở exchange/…/received, không ở thư mục work → kiểm đúng đường dẫn đã ghi (trước đây
            # chỉ tìm trong work/ nên ảnh hợp lệ cũng bị báo "hỏng" mỗi lần khởi động lại)
            p = Path(e.get("path") or self.path.parent / name)
            if not p.exists() and not e.get("path"):
                continue          # bản ghi cũ không có đường dẫn, file ở thư mục khác → không kết luận được
            if not self.ok(p):
                bad.append(name)
        return bad
