import sys
import os
import faulthandler
import threading
import traceback
from datetime import datetime
from pathlib import Path

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

log_dir = Path(__file__).resolve().parent / "data"
log_dir.mkdir(parents=True, exist_ok=True)
log_path = log_dir / "autovideo.log"
crash_path = log_dir / "crash.log"

# Kích hoạt faulthandler để bắt SegFault hoặc C-level crash
try:
    crash_file = open(crash_path, "a", encoding="utf-8")
    faulthandler.enable(file=crash_file, all_threads=True)
except Exception as e:
    pass

def _log_unhandled(exc_type, exc_value, exc_traceback, thread_name="MainThread"):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    log_entry = f"\n[{now_str}] [CRITICAL_UNHANDLED] Thread: {thread_name}\n{msg}\n"
    sys.stderr.write(log_entry)
    sys.stderr.flush()
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
            f.flush()
    except Exception:
        pass

def sys_excepthook(exc_type, exc_value, exc_traceback):
    _log_unhandled(exc_type, exc_value, exc_traceback, "MainThread")
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

def thread_excepthook(args):
    _log_unhandled(args.exc_type, args.exc_value, args.exc_traceback, getattr(args.thread, "name", "UnknownThread"))

sys.excepthook = sys_excepthook
threading.excepthook = thread_excepthook

from app.gui import run

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        _log_unhandled(*sys.exc_info(), "MainEntry")
        raise
