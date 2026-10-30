import gc
import shutil
import subprocess
import time
from pathlib import Path

from .config import ROOT, path
from .errors import FatalError
from .llm import NO_WINDOW


def gpu() -> dict:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,memory.total,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
                             creationflags=NO_WINDOW).stdout.strip().splitlines()[0]
        t, used, total, util = (float(x) for x in out.split(","))
        return {"temp": t, "vram_used_gb": used / 1024, "vram_total_gb": total / 1024,
                "vram_free_gb": (total - used) / 1024, "util": util}
    except Exception:
        return {}


def disk_free_gb(p: Path = ROOT) -> float:
    return shutil.disk_usage(p).free / 1024**3


def ram() -> dict:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {"ram_used_pct": vm.percent, "ram_free_gb": vm.available / 1024**3}
    except Exception:
        return {}


def snapshot() -> dict:
    return {**gpu(), **ram(), "disk_free_gb": round(disk_free_gb(), 1)}


def kill_stale():
    subprocess.run(["taskkill", "/F", "/IM", "sd-cli.exe"], capture_output=True, creationflags=NO_WINDOW)
    subprocess.run(["taskkill", "/F", "/IM", "sd.exe"], capture_output=True, creationflags=NO_WINDOW)


def kill_orphans(log=None) -> int:
    """Dọn tiến trình con mồ côi của app (sd-cli, python render whiteboard) không còn cha."""
    try:
        import psutil
    except ImportError:
        return 0
    repo_marker = "render_stream_whiteboard.py"
    killed = 0
    for p in psutil.process_iter(["pid", "name", "cmdline", "ppid"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or [])
            ours = name in ("sd-cli.exe", "sd.exe") or repo_marker in cmd
            if ours and not psutil.pid_exists(p.info["ppid"] or 0):
                p.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if killed and log:
        log(f"Dọn {killed} tiến trình mồ côi")
    return killed


def free_torch():
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


class Guard:
    """Bảo vệ phần cứng & ổ đĩa khi chạy 24/7."""

    def __init__(self, cfg, log, stop_evt):
        self.c = cfg.get("resources", {}) or {}
        self.log = log
        self.stop_evt = stop_evt
        self.throttled = False

    def _cleanup(self):
        work = path("work")
        freed = 0
        for d in work.glob("job_*"):
            for f in list(d.glob("scene_*_t*.png")) + list(d.glob("*_raw.mp4")) + list(d.glob("qc_*.png")):
                freed += f.stat().st_size
                f.unlink(missing_ok=True)
        self.log(f"Dọn ổ đĩa: giải phóng {freed / 1024**3:.2f} GB")

    def check_disk(self, new_job=False):
        free = disk_free_gb()
        if free < self.c.get("disk_emergency_gb", 5):
            raise FatalError(f"Ổ đĩa chỉ còn {free:.1f} GB – dừng khẩn cấp")
        if free < self.c.get("disk_cleanup_gb", 20):
            self._cleanup()
            free = disk_free_gb()
        if new_job and free < self.c.get("disk_stop_new_gb", 10):
            raise FatalError(f"Ổ đĩa còn {free:.1f} GB – ngừng nhận job mới")

    def check_thermal(self):
        g = gpu()
        t = g.get("temp", 0)
        pause_at = self.c.get("gpu_pause_c", 95)
        throttle_at = self.c.get("gpu_throttle_c", 90)
        if t >= pause_at:
            self.log(f"GPU {t:.0f}°C ≥ {pause_at}°C → tạm dừng cho nguội")
            while t >= pause_at - 10 and not self.stop_evt.is_set():
                self.stop_evt.wait(30)
                t = gpu().get("temp", 0)
            self.log(f"GPU nguội còn {t:.0f}°C → chạy tiếp")
        was = self.throttled
        self.throttled = t >= throttle_at
        if self.throttled and not was:
            self.log(f"GPU {t:.0f}°C → giảm tải (render 1 luồng)")

    def ensure_vram(self, need_gb: float, llm=None, tts=None):
        """Đảm bảo đủ VRAM trống trước bước nặng; không đủ thì giải phóng theo thứ tự."""
        steps = [("chờ", lambda: time.sleep(5)),
                 ("dọn torch", free_torch),
                 ("kill sd.cpp treo", kill_stale)]
        if tts is not None:
            steps.append(("unload Voicebox/TTS", tts.release))
        if llm is not None:
            steps.append(("unload LLM", llm.unload))
        for name, fn in [("kiểm tra", lambda: None)] + steps:
            fn()
            free = gpu().get("vram_free_gb", need_gb)
            if free >= need_gb:
                if name != "kiểm tra":
                    self.log(f"VRAM trống {free:.1f} GB sau khi {name}")
                return
        self.log(f"Cảnh báo: VRAM chỉ trống {gpu().get('vram_free_gb', 0):.1f} GB (cần {need_gb})")
