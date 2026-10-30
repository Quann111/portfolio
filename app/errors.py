import json
import subprocess

import requests

TRANSIENT, REGENERABLE, LOGICAL, FATAL = "transient", "regenerable", "logical", "fatal"
MAX_ATTEMPTS = {TRANSIENT: 6, REGENERABLE: 3, LOGICAL: 3, FATAL: 1}


class FatalError(Exception):
    pass


def classify(e: BaseException) -> str:
    msg = str(e).lower()
    if isinstance(e, FatalError):
        return FATAL
    if (isinstance(e, OSError) and getattr(e, "errno", None) == 28) or "no space left" in msg \
            or "không thấy stable-diffusion" in msg or "not recognized" in msg \
            or (isinstance(e, FileNotFoundError) and ("ffmpeg" in msg or ".gguf" in msg or ".exe" in msg)):
        return FATAL
    if "out of memory" in msg or "cuda error" in msg or "oom" in msg or isinstance(e, (
            PermissionError, subprocess.TimeoutExpired, requests.RequestException, ConnectionError)) \
            or "llm thất bại" in msg or "lms load" in msg or "winerror 32" in msg:
        return TRANSIENT
    if isinstance(e, (json.JSONDecodeError, KeyError, IndexError, ValueError, TypeError)):
        return LOGICAL
    return REGENERABLE


LABEL = {TRANSIENT: "tạm thời", REGENERABLE: "tạo lại được", LOGICAL: "logic", FATAL: "NGHIÊM TRỌNG"}
