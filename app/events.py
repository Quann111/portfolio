"""Luồng sự kiện trực tiếp cho dashboard web (Server-Sent Events): log, tiến độ, bước, job xong.
Bộ đệm vòng trong bộ nhớ; trang web nhận sự kiện mới qua /events (không phải hỏi lại liên tục)."""
import threading
import time
from collections import deque

_buf: deque = deque(maxlen=800)
_cond = threading.Condition()
_seq = 0


def push(kind: str, data: dict):
    global _seq
    with _cond:
        _seq += 1
        _buf.append({"id": _seq, "t": time.time(), "kind": kind, "data": data})
        _cond.notify_all()


def since(last_id: int, timeout: float = 15) -> list[dict]:
    """Sự kiện có id > last_id; chưa có thì chờ tối đa timeout giây."""
    with _cond:
        if not _buf or _buf[-1]["id"] <= last_id:
            _cond.wait(timeout)
        return [e for e in _buf if e["id"] > last_id]


def recent(kind: str | None = None, n: int = 300) -> list[dict]:
    with _cond:
        items = [e for e in _buf if kind is None or e["kind"] == kind]
    return items[-n:]
