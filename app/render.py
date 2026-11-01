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
