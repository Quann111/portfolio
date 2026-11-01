"""Khoá worker: GUI, API, golden test, service không được chạy trùng trên cùng một session DB."""
import json
import os
import time
from pathlib import Path

import psutil


class WorkerLock:
    def __init__(self, db_path: Path):
        self.path = Path(db_path).with_suffix(".lock")
        self.held = False

    def _owner(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if not psutil.pid_exists(d["pid"]):
                return None
            # PID có thể đã bị Windows cấp lại cho tiến trình khác → chỉ tính là chủ khoá nếu là python
            if d["pid"] != os.getpid() and "python" not in psutil.Process(d["pid"]).name().lower():
                return None
            return d
        except Exception:
            return None

    def acquire(self) -> tuple[bool, str]:
        owner = self._owner()
        if owner and owner["pid"] != os.getpid():
            return False, f"đang có worker khác chạy (PID {owner['pid']}, từ {owner['since']})"
        if not owner:
            self.path.unlink(missing_ok=True)          # khoá cũ của tiến trình đã chết
        data = json.dumps({"pid": os.getpid(), "since": time.strftime("%Y-%m-%d %H:%M:%S")})
        try:
            # tạo file độc quyền: 2 tiến trình khởi động cùng lúc thì chỉ 1 bên giành được khoá
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
        except FileExistsError:
            owner = self._owner()
            if owner and owner["pid"] != os.getpid():
                return False, f"đang có worker khác chạy (PID {owner['pid']}, từ {owner['since']})"
            self.path.write_text(data, encoding="utf-8")
        self.held = True
        return True, ""

    def release(self):
        if self.held:
            owner = self._owner()
            if owner and owner["pid"] == os.getpid():
                self.path.unlink(missing_ok=True)
            self.held = False
