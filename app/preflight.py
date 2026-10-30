"""Kiểm tra trước khi chạy: thiếu gì thì báo rõ, không chạy nửa vời."""
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import path
from .llm import NO_WINDOW
from .resources import disk_free_gb

MIN_SIZES_MB = {"diffusion_model": 3000, "t5xxl": 1000, "clip_l": 50, "vae": 100}


def _run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def check(cfg) -> list[str]:
    problems = []
    c = cfg.image
    af = cfg.get("autoflow") or {}
    use_flow = (c.get("provider") or "local") in ("autoflow", "flowbridge")
    if use_flow:
        inbox = Path(af.get("inbox_dir") or Path.home() / "Downloads" / "AutoFlow" / "Image")
        try:
            inbox.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            problems.append(f"Không tạo được thư mục nhận ảnh Auto Flow {inbox}: {e}")
    need_local = not use_flow or af.get("fallback_local", True)
    for key, mb in (MIN_SIZES_MB.items() if need_local else []):
        p = path(c[key])
        if not p.exists():
            problems.append(f"Thiếu model ảnh {key}: {p} (chạy setup.bat)")
        elif p.stat().st_size < mb * 1024**2:
            problems.append(f"Model {p.name} quá nhỏ ({p.stat().st_size // 1024**2} MB) – có thể tải dở")
    exe = path(c.sd_exe)
    exe = exe if exe.exists() else exe.parent / "sd.exe"
    if need_local and not exe.exists():
        problems.append(f"Thiếu stable-diffusion.cpp: {exe}")
    elif need_local:
        rc, out = _run([str(exe), "--help"])
        if "--diffusion-model" not in out:
            problems.append(f"sd.cpp không chạy được: {out[-200:]}")
    if (cfg.get("research") or {}).get("mode", "web") != "offline":
        try:
            import ddgs  # noqa: F401  (chỉ kiểm có cài)
            import trafilatura  # noqa: F401  (chỉ kiểm có cài)
        except ImportError:
            problems.append("Thiếu thư viện web research (ddgs, trafilatura) – chạy setup.bat")
    # công cụ tuỳ chọn – thiếu chỉ ghi chú (không chặn chạy): yt-dlp đọc video kênh tham khảo, uv chạy last30days
    if (cfg.get("auto_topics") or {}).get("enabled", True):
        miss = [t for t in ("uv", "yt-dlp") if not shutil.which(t)]
        if miss:
            print(f"[preflight] tự tìm chủ đề: thiếu {miss} → bỏ phần đo độ hot / video tham khảo")
    for tool in ("ffmpeg", "ffprobe", "lms"):
        if not shutil.which(tool):
            problems.append(f"Không tìm thấy {tool} trong PATH")
    if shutil.which("lms"):
        rc, out = _run(["lms", "ls"], 60)
        for m in {cfg.lmstudio.model, *(cfg.lmstudio.get("models") or {}).values()}:
            if m not in out:
                problems.append(f"LM Studio chưa có model {m}")
    wb = cfg.whiteboard
    repo, py = path(wb.repo), path(wb.python)
    if not (repo / "scripts" / "render_stream_whiteboard.py").exists():
        problems.append(f"Thiếu repo whiteboard: {repo}")
    if not py.exists():
        problems.append(f"Thiếu Python của repo whiteboard: {py}")
    else:
        rc, out = _run([str(py), "-c", "import cv2, numpy, av"], 60)
        if rc != 0:
            problems.append(f"Môi trường whiteboard thiếu thư viện: {out[-200:]}")
    for d in (cfg.work_dir, cfg.output_dir, "data"):
        p = path(d)
        try:
            p.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=p):
                pass
        except OSError as e:
            problems.append(f"Không ghi được vào {p}: {e}")
    stop_gb = (cfg.get("resources") or {}).get("disk_stop_new_gb", 10)
    if disk_free_gb() < stop_gb:
        problems.append(f"Ổ đĩa còn {disk_free_gb():.1f} GB (< {stop_gb} GB)")
    return problems
