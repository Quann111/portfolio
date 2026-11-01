"""Chạy tiến trình con có theo dõi, để Dừng/thoát app có thể kết thúc an toàn cả cây tiến trình."""
import subprocess
import threading

from .llm import NO_WINDOW

_live: set[subprocess.Popen] = set()
_lock = threading.Lock()


class Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def run(cmd, timeout=None, cwd=None, env=None) -> Result:
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", cwd=cwd, env=env, creationflags=NO_WINDOW)
    with _lock:
        _live.add(p)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        try:
            out, err = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:      # taskkill không giết được (vd quyền) – không treo mãi
            p.kill()
            out, err = "", ""
        raise subprocess.TimeoutExpired(cmd, timeout, out, err)
    finally:
        with _lock:
            _live.discard(p)
    return Result(p.returncode, out, err)


def kill_tree(p: subprocess.Popen):
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=20,
                       creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        pass
    if p.poll() is None:
        try:
            p.kill()
        except OSError:
            pass


def active() -> int:
    with _lock:
        return len(_live)


def terminate_all():
    with _lock:
        procs = list(_live)
    for p in procs:
        kill_tree(p)
