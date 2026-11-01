import json
import subprocess
from pathlib import Path

from .llm import NO_WINDOW

PAPER = "0xF5EBD7"


def _probe(video: Path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                          "-show_entries", "stream=width,height,r_frame_rate,nb_read_packets", "-of", "json",
                          str(video)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=120, creationflags=NO_WINDOW).stdout
    s = json.loads(out)["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return int(s["width"]), int(s["height"]), float(num) / float(den), int(s["nb_read_packets"])


def filters(video: Path, camera: str, focus: tuple[float, float], transition: str, draw_end_ratio: float) -> str:
    """Chuỗi -vf cho camera move (sau khi vẽ gần xong) + hiệu ứng vào cảnh."""
    w, h, fps, n = _probe(video)
    if n / max(fps, 1) < 6:
        camera = "static"     # cảnh ngắn: camera di chuyển chỉ gây rối mắt
    cx, cy = (min(max(v, 0.0), 1.0) for v in focus)
    f0 = int(n * min(max(draw_end_ratio, 0.3), 0.9))
    span = max(1, n - f0)
    vf = []
    if camera == "zoom_in":
        z = f"if(lt(on,{f0}),1,min(1.15,1+0.15*(on-{f0})/{span}))"
        vf.append(f"zoompan=z='{z}':x='(iw-iw/zoom)*{cx}':y='(ih-ih/zoom)*{cy}':d=1:s={w}x{h}:fps={fps:g}")
    elif camera == "zoom_out":
        z = f"if(lt(on,{f0}),1.15,max(1,1.15-0.15*(on-{f0})/{span}))"
        vf.append(f"zoompan=z='{z}':x='(iw-iw/zoom)*{cx}':y='(ih-ih/zoom)*{cy}':d=1:s={w}x{h}:fps={fps:g}")
    elif camera in ("pan_left", "pan_right"):
        p = f"(on/{n})" if camera == "pan_right" else f"(1-on/{n})"
        vf.append(f"zoompan=z=1.1:x='(iw-iw/zoom)*{p}':y='(ih-ih/zoom)*0.5':d=1:s={w}x{h}:fps={fps:g}")
    if transition == "fade":
        vf.append(f"fade=t=in:st=0:d=0.35:color={PAPER}")
    elif transition == "chapter":
        vf.append(f"fade=t=in:st=0:d=0.8:color={PAPER}")
    return ",".join(vf)


def apply(video: Path, out: Path, camera: str, focus, transition: str, draw_end_ratio: float) -> bool:
    vf = filters(video, camera or "static", focus or (0.5, 0.5), transition or "cut", draw_end_ratio)
    if not vf:
        return False
    from . import procs
    r = procs.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", vf, "-an",
                   "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", str(out)], timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"transition lỗi: {r.stderr[-500:]}")
    return True
