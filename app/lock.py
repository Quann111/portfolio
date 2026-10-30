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
            return d if psutil.pid_exists(d["pid"]) else None
        except Exception:
            return None

    def acquire(self) -> tuple[bool, str]:
        owner = self._owner()
        if owner and owner["pid"] != os.getpid():
            return False, f"đang có worker khác chạy (PID {owner['pid']}, từ {owner['since']})"
        self.path.write_text(json.dumps({"pid": os.getpid(), "since": time.strftime("%Y-%m-%d %H:%M:%S")}),
                             encoding="utf-8")
        self.held = True
        return True, ""

    def release(self):
        if self.held:
            owner = self._owner()
            if owner and owner["pid"] == os.getpid():
                self.path.unlink(missing_ok=True)
            self.held = False
