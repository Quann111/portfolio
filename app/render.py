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


def normalize_short_image(image: Path, ann: Path | None = None, log=None, target_size: tuple[int, int] = (1080, 1920)) -> bool:
    """
    Chuẩn hoá ảnh cho Shorts về khổ dọc 9:16 (mặc định 1080x1920):
    - Giữ nguyên tỉ lệ, không làm méo hình: scale theo tỉ lệ vừa khung (aspect fit).
    - Pad nền bằng màu giấy nếu ảnh sáng/tranh nét, hoặc blur ảnh gốc nếu ảnh tối.
    - Cập nhật toạ độ và canvas trong file annotation tương ứng nếu có.
    - Ghi log khi phải chuẩn hoá.
    Trả về True nếu đã chuẩn hoá, False nếu ảnh đã chuẩn 1080x1920.
    """
    import cv2
    import numpy as np

    target_w, target_h = target_size
    img_path = Path(image)
    if not img_path.is_file():
        return False

    img = cv2.imread(str(img_path))
    if img is None:
        return False

    h, w = img.shape[:2]
    if w == target_w and h == target_h:
        return False

    scale = min(target_w / w, target_h / h)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    pad_x = (target_w - new_w) // 2
    pad_y = (target_h - new_h) // 2

    # Scale ảnh gốc không méo hình
    if scale < 1.0:
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    else:
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)

    # Lấy mẫu màu viền mép để tìm màu nền giấy
    edge_pixels = np.concatenate([
        img[:max(4, h // 20), :].reshape(-1, 3),
        img[-max(4, h // 20):, :].reshape(-1, 3),
        img[:, :max(4, w // 20)].reshape(-1, 3),
        img[:, -max(4, w // 20):].reshape(-1, 3),
    ], axis=0)
    paper_color = np.median(edge_pixels, axis=0).astype(np.uint8)

    if float(np.mean(paper_color)) > 150:
        canvas = np.full((target_h, target_w, 3), paper_color, dtype=np.uint8)
    else:
        bg = cv2.resize(img, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        canvas = cv2.GaussianBlur(bg, (51, 51), 0)

    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    cv2.imwrite(str(img_path), canvas)

    log_msg = f"Chuẩn hoá ảnh {img_path.name}: {w}x{h} → {target_w}x{target_h} (scale={scale:.3f}, pad=({pad_x},{pad_y}))"
    if log:
        log(log_msg)
    else:
        print(log_msg)

    # Cập nhật toạ độ nét vẽ annotation nếu có
    if ann:
        ann_path = Path(ann)
        if ann_path.is_file():
            try:
                a_data = json.loads(ann_path.read_text(encoding="utf-8"))
                old_canvas = a_data.get("canvas") or {}
                old_w = old_canvas.get("width") or w
                old_h = old_canvas.get("height") or h
                c_scale_x = scale if old_w == w else (new_w / old_w)
                c_scale_y = scale if old_h == h else (new_h / old_h)

                a_data["canvas"] = {"width": target_w, "height": target_h}
                for el in a_data.get("elements", []):
                    reg = el.get("region")
                    if reg:
                        reg["x"] = int(round(reg.get("x", 0) * c_scale_x + pad_x))
                        reg["y"] = int(round(reg.get("y", 0) * c_scale_y + pad_y))
                        reg["width"] = int(round(reg.get("width", 0) * c_scale_x))
                        reg["height"] = int(round(reg.get("height", 0) * c_scale_y))
                    hp = el.get("handPath")
                    if hp:
                        if "start" in hp and len(hp["start"]) >= 2:
                            hp["start"] = [
                                int(round(hp["start"][0] * c_scale_x + pad_x)),
                                int(round(hp["start"][1] * c_scale_y + pad_y))
                            ]
                        if "end" in hp and len(hp["end"]) >= 2:
                            hp["end"] = [
                                int(round(hp["end"][0] * c_scale_x + pad_x)),
                                int(round(hp["end"][1] * c_scale_y + pad_y))
                            ]
                    rev = el.get("reveal")
                    if rev and "protectedRegions" in rev:
                        rev["protectedRegions"] = [
                            [int(round(pr[0] * c_scale_x + pad_x)), int(round(pr[1] * c_scale_y + pad_y)),
                             int(round(pr[2] * c_scale_x)), int(round(pr[3] * c_scale_y))]
                            for pr in rev["protectedRegions"] if len(pr) == 4
                        ]
                ann_path.write_text(json.dumps(a_data, ensure_ascii=False, indent=2), encoding="utf-8")
                if log:
                    log(f"Cập nhật annotation {ann_path.name} khớp khung {target_w}x{target_h}")
            except Exception as e:
                if log:
                    log(f"Cảnh báo: lỗi cập nhật annotation {ann_path.name}: {e}")

    return True


def get_hand_dimensions(hand_path: Path, target_h: int = 493) -> tuple[int, int]:
    """Tính kích thước (width, height) của bàn tay sau khi được renderer load và scale theo target_h."""
    import cv2
    import numpy as np
    hand_p = Path(hand_path)
    if not hand_p.is_file():
        return int(round(target_h * 0.62)), target_h
    raw = cv2.imread(str(hand_p), cv2.IMREAD_UNCHANGED)
    if raw is None:
        return int(round(target_h * 0.62)), target_h
    if raw.ndim == 3 and raw.shape[2] == 4:
        mask = raw[:, :, 3]
    else:
        gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY_INV)
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        return int(round(target_h * 0.62)), target_h
    box_w = int(xs.max() - xs.min() + 1)
    box_h = int(ys.max() - ys.min() + 1)
    scale = target_h / max(1, box_h)
    scaled_w = max(1, int(round(box_w * scale)))
    return scaled_w, target_h


def ensure_hand_ratio(hand_path: Path, frame_w: int = 1080, max_ratio: float = 0.5, work_dir: Path | None = None) -> Path:
    """
    Đảm bảo bàn tay vẽ không to quá nửa chiều ngang (frame_w * max_ratio).
    Nếu vượt quá, tạo bản bàn tay điều chỉnh có tỉ lệ phù hợp.
    """
    import math
    import cv2
    import numpy as np

    hand_p = Path(hand_path)
    if not hand_p.is_file():
        return hand_p
    w, _ = get_hand_dimensions(hand_p, target_h=493)
    max_w = int(frame_w * max_ratio)
    if w <= max_w:
        return hand_p

    raw = cv2.imread(str(hand_p), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.ndim != 3 or raw.shape[2] != 4:
        return hand_p
    mask = raw[:, :, 3]
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        return hand_p
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    box_w = x1 - x0 + 1
    box_h = y1 - y0 + 1
    needed_box_h = int(math.ceil(box_w * 493 / max_w))
    if needed_box_h <= box_h:
        return hand_p
    pad_total = needed_box_h - box_h
    pad_top = pad_total // 2
    pad_bottom = pad_total - pad_top
    new_h = raw.shape[0] + pad_total
    new_img = np.zeros((new_h, raw.shape[1], 4), dtype=np.uint8)
    new_img[pad_top:pad_top + raw.shape[0], :] = raw
    # Đặt 1 pixel alpha=1 ở mép trên và dưới để bbox nhận diện đúng needed_box_h
    mid_x = (x0 + x1) // 2
    new_img[pad_top + y0, mid_x, 3] = max(1, new_img[pad_top + y0, mid_x, 3])
    new_img[pad_top + y0 + needed_box_h - 1, mid_x, 3] = 1
    out_dir = Path(work_dir) if work_dir else hand_p.parent
    adjusted_hand = out_dir / f"adjusted_{hand_p.name}"
    cv2.imwrite(str(adjusted_hand), new_img)
    return adjusted_hand


def build_whiteboard_cmd(wb: Cfg, repo: Path, image: Path, ann: Path, silent: Path, hand: Path, is_short: bool = False) -> list[str]:
    cmd = [
        str(path(wb.python)),
        str(repo / "scripts" / "render_stream_whiteboard.py"),
        str(image),
        str(ann),
        str(silent),
        str(hand),
        "--ink-path", str(wb.ink_path),
        "--color-fill", str(wb.color_fill),
    ]
    if is_short:
        cmd += ["--cap-long-edge", "1920"]
    return cmd


def scene_clip(image: Path, ann: Path, audio: Path, out: Path, cfg: Cfg, fx: dict | None = None, is_short: bool = False):
    short_mode = is_short or bool(fx and fx.get("is_short"))
    if short_mode:
        normalize_short_image(image, ann)
    wb = cfg.whiteboard
    repo = path(wb.repo)
    hand = path(wb.hand_png) if wb.get("hand_png") else repo / "assets" / "drawing-hand.png"
    frame_w = 1080 if short_mode else 1920
    hand = ensure_hand_ratio(hand, frame_w=frame_w, max_ratio=0.5, work_dir=out.parent)
    silent = out.with_name(out.stem + "_silent.mp4")
    cmd = build_whiteboard_cmd(wb, repo, image, ann, silent, hand, is_short=short_mode)
    _run(cmd, timeout=3600, cwd=str(repo))
    if short_mode:
        w_s, h_s = video_dimensions(silent)
        if (w_s, h_s) != (1080, 1920):
            tmp_s = silent.with_name(silent.stem + "_norm.mp4")
            vf_scale = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-vf", vf_scale,
                  "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", str(tmp_s)], timeout=600)
            tmp_s.replace(silent)
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
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(audio),
          "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
          "-af", "apad", "-shortest", str(tmp)], timeout=600)
    tmp.replace(out)
    silent.unlink(missing_ok=True)
    out.with_name(out.stem + "_silent_raw.mp4").unlink(missing_ok=True)
    if short_mode:
        w_out, h_out = video_dimensions(out)
        if (w_out, h_out) != (1080, 1920):
            tmp_o = out.with_name(out.stem + "_norm.mp4")
            vf_scale = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-vf", vf_scale,
                  "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy", str(tmp_o)], timeout=600)
            tmp_o.replace(out)


def video_dimensions(media: Path) -> tuple[int, int]:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=width,height", "-of", "csv=p=0", media], timeout=120)
    lines = [line.strip() for line in out.strip().splitlines() if line.strip()]
    try:
        parts = lines[0].split(",")
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        raise RuntimeError(f"ffprobe không đọc được kích thước video của {media} (file hỏng/rỗng?)")


def build_youtube_encode_cmd(src: Path, dst: Path, cfg: Cfg, is_vertical: bool | None = None) -> tuple[list[str], Path, bool]:
    src = Path(src)
    dst = Path(dst)
    enc_cfg = (cfg.get("encode") or {}) if hasattr(cfg, "get") else {}
    aspect_mode = str(enc_cfg.get("aspect", "auto")).lower()

    if is_vertical is True:
        pass
    elif is_vertical is False:
        pass
    elif aspect_mode == "9:16":
        is_vertical = True
    elif aspect_mode == "16:9":
        is_vertical = False
    else:
        w, h = video_dimensions(src)
        is_vertical = (h > w)

    r_rate = "30" if is_vertical else "29.97"
    aspect_ratio = "9:16" if is_vertical else "16:9"

    tmp = dst.with_name(dst.stem + "_enc_tmp.mp4")

    vf_filters = []
    if is_vertical:
        vf_filters.append("scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1")
    else:
        vf_filters.append("scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1")

    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", ",".join(vf_filters),
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
    return cmd, tmp, bool(is_vertical)


def youtube_encode(src: Path, dst: Path, cfg: Cfg, is_vertical: bool | None = None):
    src = Path(src)
    dst = Path(dst)
    cmd, tmp, is_v = build_youtube_encode_cmd(src, dst, cfg, is_vertical=is_vertical)
    tmp.unlink(missing_ok=True)

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
    if is_v:
        w_enc, h_enc = video_dimensions(dst)
        if (w_enc, h_enc) != (1080, 1920):
            raise RuntimeError(f"Encode YouTube cho Short thất bại: kích thước {w_enc}x{h_enc} != 1080x1920")


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


def build_assemble_cmd(clips, out: Path, cfg: Cfg, bgm: Path | None = None, ass: Path | None = None, is_short: bool = False, lst_name: str | None = None) -> tuple[list[str], str]:
    target_lst = lst_name or out.with_suffix(".txt").name
    tmp_name = out.with_name(out.stem + "_tmp.mp4").name
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", target_lst]
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
        ass_name = ass.name if isinstance(ass, Path) else str(ass)
        vf = f"ass={ass_name}"
        if is_short:
            vf = f"scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,{vf}"
        cmd += ["-vf", vf, "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p"]
    elif is_short:
        vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"
        cmd += ["-vf", vf, "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp_name]
    return cmd, tmp_name


def assemble(clips, out: Path, cfg: Cfg, bgm: Path | None = None, ass: Path | None = None, is_short: bool = False):
    lst = out.with_suffix(".txt")
    lst.write_text("".join(_concat_line(Path(c).resolve().as_posix()) for c in clips), encoding="utf-8")
    if ass and ass.resolve().parent != out.parent.resolve():
        import shutil
        ass = Path(shutil.copy2(ass, out.parent / ass.name))
    cmd, tmp_name = build_assemble_cmd(clips, out, cfg, bgm=bgm, ass=ass, is_short=is_short, lst_name=lst.name)
    tmp = out.with_name(tmp_name)
    _run(cmd, timeout=14400, cwd=str(out.parent))
    tmp.replace(out)
    lst.unlink(missing_ok=True)
    if is_short:
        w_asm, h_asm = video_dimensions(out)
        if (w_asm, h_asm) != (1080, 1920):
            tmp2 = out.with_name(out.stem + "_norm1080.mp4")
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out),
                  "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
                  "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy", str(tmp2)], timeout=1800)
            tmp2.replace(out)
