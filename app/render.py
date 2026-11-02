import json
import os
import random
from pathlib import Path

from . import procs, transition
from .config import Cfg, path

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


UTF8_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _concat_line(p) -> str:
    """Dòng cho danh sách concat của ffmpeg – escape dấu ' trong đường dẫn."""
    return "file '" + str(p).replace("'", "'\\''") + "'\n"


def _run(cmd, timeout=3600, cwd=None):
    r = procs.run(cmd, timeout=timeout, cwd=cwd, env=UTF8_ENV)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-1000:])
    return r.stdout + r.stderr


def scene_clip(image: Path, ann: Path, audio: Path, out: Path, cfg: Cfg, fx: dict | None = None):
    wb = cfg.whiteboard
    repo = path(wb.repo)
    hand = path(wb.hand_png) if wb.get("hand_png") else repo / "assets" / "drawing-hand.png"
    silent = out.with_name(out.stem + "_silent.mp4")
    _run([path(wb.python), repo / "scripts" / "render_stream_whiteboard.py", image, ann, silent, hand,
          "--ink-path", wb.ink_path, "--color-fill", wb.color_fill], timeout=3600, cwd=str(repo))
    if fx:
        a = json.loads(ann.read_text(encoding="utf-8"))
        end = max((e["reveal"]["startMs"] + e["reveal"]["durationMs"] for e in a.get("elements") or []), default=0)
        moved = out.with_name(out.stem + "_fx.mp4")
        if transition.apply(silent, moved, fx.get("camera"), fx.get("focus"), fx.get("transition"),
                            end / max(1, a["sceneDurationMs"])):
            moved.replace(silent)
    v_ms, a_ms = duration_ms(silent), duration_ms(audio)
    if v_ms < a_ms - 150:
        raise RuntimeError(f"timing: clip hình {v_ms} ms ngắn hơn lời đọc {a_ms} ms – lời sẽ bị cắt")
    tmp = out.with_name(out.stem + "_tmp.mp4")
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", silent, "-i", audio,
          "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
          "-af", "apad", "-shortest", tmp], timeout=600)
    tmp.replace(out)
    silent.unlink(missing_ok=True)
    out.with_name(out.stem + "_silent_raw.mp4").unlink(missing_ok=True)


def video_dimensions(media: Path) -> tuple[int, int]:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=width,height", "-of", "csv=p=0", media], timeout=120)
    lines = [line.strip() for line in out.strip().splitlines() if line.strip()]
    try:
        parts = lines[0].split(",")
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        raise RuntimeError(f"ffprobe không đọc được kích thước video của {media} (file hỏng/rỗng?)")


def youtube_encode(src: Path, dst: Path, cfg: Cfg):
    src = Path(src)
    dst = Path(dst)
    enc_cfg = (cfg.get("encode") or {}) if hasattr(cfg, "get") else {}
    aspect_mode = str(enc_cfg.get("aspect", "auto")).lower()

    if aspect_mode == "9:16":
        is_vertical = True
    elif aspect_mode == "16:9":
        is_vertical = False
    else:
        w, h = video_dimensions(src)
        is_vertical = (h > w)

    r_rate = "30" if is_vertical else "29.97"
    aspect_ratio = "9:16" if is_vertical else "16:9"

    tmp = dst.with_name(dst.stem + "_enc_tmp.mp4")
    tmp.unlink(missing_ok=True)

    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level:v", "4.2",
        "-preset", "medium",
        "-crf", "18",
        "-b:v", "16.6M",
        "-maxrate", "16.6M",
        "-bufsize", "33.2M",
        "-g", "30",
        "-keyint_min", "30",
        "-sc_threshold", "0",
        "-bf", "3",
        "-refs", "3",
        "-r", r_rate,
        "-aspect", aspect_ratio,
        "-pix_fmt", "yuv420p",
        "-color_primaries", "1",
        "-color_trc", "1",
        "-colorspace", "1",
        "-color_range", "1",
        "-movflags", "+faststart",
        "-c:a", "aac",
        "-profile:a", "aac_low",
        "-b:a", "317k",
        "-maxrate:a", "538k",
        "-ar", "48000",
        "-ac", "2",
        "-cutoff", "18000",
        str(tmp),
    ]

    dur_ms = duration_ms(src)
    dur_s = max(1.0, dur_ms / 1000.0)
    timeout = max(600, int(dur_s * 3))

    _run(cmd, timeout=timeout, cwd=str(src.parent))

    if not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Encode YouTube thất bại: file kết quả tạm {tmp} không tồn tại hoặc rỗng")

    enc_dur_ms = duration_ms(tmp)
    if abs(enc_dur_ms - dur_ms) > 1000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Encode YouTube sai lệch thời lượng: gốc {dur_ms}ms, sau encode {enc_dur_ms}ms (> 1s)")

    os.replace(str(tmp), str(dst))


def duration_ms(media: Path) -> int:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", media], timeout=120)
    lines = out.strip().splitlines()
    try:
        return int(float(lines[0]) * 1000)
    except (IndexError, ValueError):
        raise RuntimeError(f"ffprobe không đọc được thời lượng của {media} (file hỏng/rỗng?)")


def _pick(music_dir: Path, mood: str):
    files = [f for f in (music_dir / mood).glob("*") if f.suffix.lower() in AUDIO_EXT]
    if not files:
        files = [f for f in music_dir.rglob("*") if f.suffix.lower() in AUDIO_EXT]
    return random.choice(files) if files else None


def build_bgm(plan, work: Path, cfg: Cfg):
    """plan = [(mood, ms), ...] theo chương. Mỗi chương một bản nhạc hợp mood, fade đầu/cuối."""
    md = path(cfg.get("music_dir") or "music")
    if not md.exists():
        return None
    segs = []
    fade = mix_preset(cfg)["fade"]
    for i, (mood, ms) in enumerate(plan):
        f = _pick(md, mood)
        if f is None:
            return None
        sec = ms / 1000
        fd = min(fade, sec / 3)
        seg = work / f"bgm_{i:02}.wav"
        _run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", f, "-t", f"{sec:.3f}",
              "-af", f"afade=t=in:d={fd:.2f},afade=t=out:st={max(0, sec - fd):.3f}:d={fd:.2f}",
              "-ar", "48000", "-ac", "2", seg], timeout=600)
        segs.append(seg)
    lst = work / "bgm.txt"
    lst.write_text("".join(_concat_line(s.as_posix()) for s in segs), encoding="utf-8")
    bgm = work / "bgm.wav"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", bgm])
    return bgm


def mix_preset(cfg: Cfg) -> dict:
    b = cfg.bible
    p = dict((b.get("mix_presets") or {}).get(b.get("mix_preset", "education"), {}))
    p.setdefault("lufs", -16)
    p.setdefault("true_peak", -1.5)
    p.setdefault("music_volume", cfg.get("music_volume", 0.18))
    p.setdefault("duck_ratio", 8)
    p.setdefault("fade", 1.5)
    return p


def assemble(clips, out: Path, cfg: Cfg, bgm: Path | None = None, ass: Path | None = None):
    lst = out.with_suffix(".txt")
    lst.write_text("".join(_concat_line(Path(c).resolve().as_posix()) for c in clips), encoding="utf-8")
    tmp = out.with_name(out.stem + "_tmp.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst.name]
    mp = mix_preset(cfg)
    loud = f"loudnorm=I={mp['lufs']}:TP={mp['true_peak']}:LRA=11"
    if bgm:
        cmd += ["-i", str(bgm), "-filter_complex",
                f"[0:a]asplit=2[sc][vo];[1:a]volume={mp['music_volume']}[m];"
                f"[m][sc]sidechaincompress=threshold=0.03:ratio={mp['duck_ratio']}:attack=20:release=500[duck];"
                f"[vo][duck]amix=inputs=2:duration=first:normalize=0,{loud}[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-af", loud]
    if ass:
        # chạy trong thư mục chứa file để tránh lỗi escape đường dẫn Windows trong filter ass
        if ass.resolve().parent != out.parent.resolve():
            import shutil
            ass = Path(shutil.copy2(ass, out.parent / ass.name))
        cmd += ["-vf", f"ass={ass.name}", "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp.name]
    _run(cmd, timeout=14400, cwd=str(out.parent))
    tmp.replace(out)
    lst.unlink(missing_ok=True)
