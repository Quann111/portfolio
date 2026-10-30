"""Kiểu dựng Nikola – bảng hai đảo (semantic islands), theo repo hi-nikola/hand-drawn-explainer-video-nikola.

Giữ cách của AutoVideo: kịch bản NotebookLM, ảnh Google Flow (Flow Bridge), giọng ZeroTTS. Phần lấy từ Nikola:
- hai cảnh liên tiếp = một "màn" = MỘT ảnh 16:9 có đảo trái + đảo phải, rãnh giữa trắng, đáy 18% trống;
- vẽ đảo trái → dừng ≥0.5 s → đảo phải, đảo trái giữ nguyên trên giấy; thời gian lấy từ giọng đọc thật;
- engine nét bản trong repo Nikola (skeleton + contour-wipe + hand-follow), không camera/zoom;
- chữ khoá là lớp ASS \\kf (không để model ảnh vẽ chữ); check_drawable_regions / annotation_schema trước render;
- màn thử trước khi render cả loạt; finalize_stroke_video.py ghép; kiểm khung theo checklist của Nikola.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .. import procs
from ..config import Cfg, path
from ..visual_rules import PROMPT_RULE, strip_legacy

UTF8 = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
GUTTER = (0.40, 0.60)          # vùng tìm rãnh giữa (tỉ lệ bề ngang)
BOTTOM = 0.18                  # đáy để trống cho phụ đề (semantic-island-storytelling.md §1)
HOLD_MS = 500                  # dừng sau khi một đảo xong (stroke-story-workflow.md §7)


# ------------------------------------------------------------------ cấu hình / tham số từ repo Nikola
def conf(cfg: Cfg) -> dict:
    n = dict(cfg.get("nikola") or {})
    repo = path(n.get("repo") or "../nikola")
    n["repo"] = repo
    n["python"] = path(n.get("python") or str(repo / ".venv" / "Scripts" / "python.exe"))
    n["vendor"] = repo / "vendor" / "srt-whiteboard-animation"
    ref = _ref_params(repo)
    n.setdefault("hand_follow", ref.get("hand_follow", 0.3))
    n.setdefault("hand_height", ref.get("hand_height", 240))
    n.setdefault("ink_path", "skeleton")
    n.setdefault("color_fill", "contour-wipe")
    n.setdefault("parallel_renders", 2)
    return n


def _ref_params(repo: Path) -> dict:
    """Đọc tham số khởi đầu cho bố cục hai đảo trực tiếp từ references/semantic-island-storytelling.md."""
    f = repo / "references" / "semantic-island-storytelling.md"
    try:
        t = f.read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    m = re.search(r"--hand-follow\s+([\d.]+)\s*(?:至|-|–|to)\s*([\d.]+)", t)
    if m:
        out["hand_follow"] = round((float(m.group(1)) + float(m.group(2))) / 2, 2)
    m = re.search(r"--hand-height\s+(\d+)\s*(?:至|-|–|to)\s*(\d+)", t)
    if m:
        out["hand_height"] = (int(m.group(1)) + int(m.group(2))) // 2
    return out


def available(cfg: Cfg) -> str | None:
    """None nếu engine Nikola dùng được, ngược lại là lý do."""
    n = conf(cfg)
    if not (n["vendor"] / "scripts" / "render_stream_whiteboard.py").exists():
        return f"chưa có repo Nikola ở {n['repo']} (git clone hi-nikola/hand-drawn-explainer-video-nikola)"
    if not Path(n["python"]).exists():
        return f"chưa có Python riêng cho Nikola ({n['python']})"
    return None


def preflight(cfg: Cfg, report: Path) -> dict:
    """stroke_story_preflight.py của repo (chỉ đọc, không cài, không gọi dịch vụ trả phí)."""
    n = conf(cfg)
    r = _py(n, [n["repo"] / "scripts" / "stroke_story_preflight.py", "--backend-python", n["python"],
                "--report", report], timeout=600)
    try:
        return json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"passed": False, "error": (r.stderr or r.stdout)[-500:]}


def _py(n, args, timeout=3600, cwd=None):
    import os
    return procs.run([n["python"], *[str(a) for a in args]], timeout=timeout, cwd=cwd,
                     env={**os.environ, **UTF8})


# ------------------------------------------------------------------ ghép cảnh thành màn
def acts(scenes: list) -> list[list[int]]:
    """Hai cảnh liên tiếp CÙNG chương = một màn hai đảo; cảnh lẻ = màn một đảo (vẽ giữa)."""
    out, i = [], 0
    while i < len(scenes):
        a = scenes[i]
        if (i + 1 < len(scenes) and scenes[i + 1].get("chapter") == a.get("chapter")
                and not _program(a) and not _program(scenes[i + 1])):
            out.append([a["idx"], scenes[i + 1]["idx"]])
            i += 2
        else:
            out.append([a["idx"]])
            i += 1
    return out


def _program(s) -> bool:
    try:
        d = json.loads(s.get("data") or "{}") if isinstance(s.get("data"), str) else (s.get("data") or {})
    except ValueError:
        d = {}
    return d.get("route") == "program"


def act_of(scenes: list, idx: int) -> list[int]:
    for a in acts(scenes):
        if idx in a:
            return a
    return [idx]


def is_partner(scenes: list, idx: int) -> bool:
    a = act_of(scenes, idx)
    return len(a) == 2 and a[1] == idx


# ------------------------------------------------------------------ prompt ảnh (khung §2 semantic-island-storytelling.md)
NO_TEXT = PROMPT_RULE + ". No dense hatching, no tiny parts, no photographic texture"   # luật chung visual_rules
STYLE = ("Natural-skin chibi humans about 3.5 heads tall, clean hand-drawn editorial storybook style, sparse dark "
         "ink outlines, limited flat color, low texture, a few accent colors, white paper, generous negative space")


def _subject(d: dict) -> str:
    p = re.sub(r"\s+", " ", d.get("image_prompt") or d.get("visual_concept") or "").strip()
    p = strip_legacy(p)
    return p[:520]


def pair_prompt(da: dict, db_: dict | None, vb: dict, fix: str = "", simplify: bool = False,
                limit: int = 2400) -> str:
    refs = [f"{x['name']}: {x['look']}" for x in (vb or {}).get("characters", [])][:3]
    if db_ is None:
        head = (f"Landscape 16:9 hand-drawn story illustration with ONE single scene island centered on one sheet of "
                f"paper. {STYLE}. Keep the bottom 18% of the paper completely clean for subtitles. {NO_TEXT}. ")
        body = f"SCENE: {_subject(da)}"
    else:
        head = (f"Landscape 16:9 hand-drawn story illustration: ONE sheet of paper with exactly TWO separate scene "
                f"islands that continue one story. Keep a clean empty vertical gutter of 6-10% of the width in the "
                f"middle; nothing connects the two islands – no ground line, wire, table edge, shadow, arrow, ribbon "
                f"or shared outline. Keep the bottom 18% of the paper completely clean for subtitles. {STYLE}. "
                f"{NO_TEXT}. ")
        body = f"LEFT ISLAND (first): {_subject(da)} RIGHT ISLAND (second): {_subject(db_)}"
    if simplify:
        body += " Simplify: fewer objects, bigger simple shapes, more empty paper."
    elif fix:
        body += " Fix: " + fix[:240]
    if refs and not simplify:
        body += " Consistent character designs – " + "; ".join(refs)
    p = head + body
    return p if len(p) <= limit else p[:limit].rsplit(" ", 1)[0]


# ------------------------------------------------------------------ QC bố cục (bổ sung cho QC ảnh hiện có)
def _ink(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    sat = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1] if img.ndim == 3 else np.zeros_like(g)
    return (g < 200) | (sat > 60)


def gutter_x(img: np.ndarray) -> int:
    """Cột ít mực nhất trong khoảng 40–60% bề ngang (cửa sổ trượt rộng 4%)."""
    ink = _ink(img)
    h, w = ink.shape
    col = ink[: int(h * (1 - BOTTOM))].mean(axis=0)
    k = max(3, int(w * 0.04))
    sm = np.convolve(col, np.ones(k) / k, mode="same")
    a, b = int(w * GUTTER[0]), int(w * GUTTER[1])
    return a + int(np.argmin(sm[a:b]))


def layout_check(image: Path, two: bool) -> dict:
    img = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if img is None:
        return {"pass": False, "issues": ["không đọc được ảnh"]}
    ink = _ink(img)
    h, w = ink.shape
    issues = []
    bottom = float(ink[int(h * (1 - BOTTOM)):].mean())
    if bottom > 0.02:
        issues.append(f"đáy 18% không trống ({bottom:.1%} mực)")
    res = {"bottom_ink": round(bottom, 4)}
    if two:
        gx = gutter_x(img)
        half = max(2, int(w * 0.03))
        band = ink[: int(h * (1 - BOTTOM)), gx - half: gx + half]
        g_ink = float(band.mean())
        # nét vắt qua rãnh: thành phần liên thông chạm cả hai bên rãnh
        n, lab = cv2.connectedComponents(ink[: int(h * (1 - BOTTOM))].astype(np.uint8))
        left = set(np.unique(lab[:, : gx - half])) - {0}
        right = set(np.unique(lab[:, gx + half:])) - {0}
        bridges = len(left & right)
        left_ink = float(ink[:, : gx - half].mean())
        right_ink = float(ink[:, gx + half:].mean())
        res.update(gutter_x=gx, gutter_ink=round(g_ink, 4), bridges=bridges,
                   left_ink=round(left_ink, 4), right_ink=round(right_ink, 4))
        if g_ink > 0.015:
            issues.append(f"rãnh giữa không trắng ({g_ink:.1%} mực)")
        if bridges:
            issues.append(f"{bridges} nét nối hai đảo qua rãnh giữa")
        if min(left_ink, right_ink) < 0.01:
            issues.append("thiếu một trong hai đảo")
    res["issues"] = issues
    res["pass"] = not issues
    return res


# ------------------------------------------------------------------ annotation (định dạng của engine srt-whiteboard)
def _bbox(ink: np.ndarray, x0: int, x1: int, pad: int):
    sub = ink[:, x0:x1]
    ys, xs = np.nonzero(sub)
    if len(xs) == 0:
        return None
    return [max(x0, x0 + int(xs.min()) - pad), max(0, int(ys.min()) - pad),
            min(x1, x0 + int(xs.max()) + 1 + pad), min(ink.shape[0], int(ys.max()) + 1 + pad)]


def _el(i, label, subtitle, b, start, dur):
    x0, y0, x1, y1 = b
    return {"id": f"island-{i}", "label": label, "sequence": i, "narrativeRole": "đảo ngữ nghĩa",
            "subtitle": subtitle, "type": "story_island",
            "region": {"x": int(x0), "y": int(y0), "width": int(x1 - x0), "height": int(y1 - y0)},
            "reveal": {"direction": "top_to_bottom", "startMs": int(start), "durationMs": int(dur),
                       "maskPaddingPx": 20, "protectedRegions": []},
            "handPath": {"start": [int((x0 + x1) / 2), int(y0)], "end": [int((x0 + x1) / 2), int(y1)],
                         "easing": "easeInOut"}}


def build_annotation(image: Path, out: Path, scene_id: str, parts: list[dict], shrink: float = 0.0) -> dict:
    """parts: [{narration, audio_ms, lead_ms}] – 1 hoặc 2 đảo. Mỗi đảo vẽ trong đúng khung giọng của cảnh mình,
    chừa HOLD_MS dừng lại; đảo phải bắt đầu đúng lúc giọng cảnh sau bắt đầu."""
    img = cv2.imread(str(image), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    ink = _ink(img)
    ink[int(h * (1 - BOTTOM)):] = False
    pad = max(8, w // 100)
    if len(parts) == 2:
        gx = gutter_x(img)
        gap = max(int(w * 0.02), int(w * 0.02 + w * shrink))
        boxes = [_bbox(ink, 0, gx - gap, pad) or [0, 0, gx - gap, h],
                 _bbox(ink, gx + gap, w, pad) or [gx + gap, 0, w, h]]
    else:
        boxes = [_bbox(ink, 0, w, pad) or [0, 0, w, h]]
    els, t = [], 0
    for i, (p, b) in enumerate(zip(parts, boxes), start=1):
        start = t + int(p.get("lead_ms") or 0) + 150
        dur = max(1200, int(p["audio_ms"]) - (start - t) - HOLD_MS)
        els.append(_el(i, f"đảo {'trái' if i == 1 and len(parts) == 2 else 'phải' if i == 2 else 'giữa'}",
                       p["narration"], b, start, dur))
        t += int(p["audio_ms"])
    ann = {"sceneId": scene_id, "canvas": {"width": w, "height": h},
           "storyBasis": " ".join(p["narration"] for p in parts), "sceneDurationMs": int(t), "elements": els}
    out.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
    return ann


def check_regions(cfg: Cfg, image: Path, ann: Path, report: Path) -> tuple[bool, str]:
    """annotation_schema.py (lỗi chặn) + check_drawable_regions.py (khi ≥2 vùng) của repo Nikola."""
    n = conf(cfg)
    r = _py(n, [n["vendor"] / "scripts" / "annotation_schema.py", ann], timeout=120)
    if r.returncode != 0:
        return False, "annotation_schema: " + (r.stdout + r.stderr)[-400:]
    if len(json.loads(ann.read_text(encoding="utf-8"))["elements"]) >= 2:
        r = _py(n, [n["repo"] / "scripts" / "check_drawable_regions.py", image, ann, "--report", report],
                timeout=300)
        if r.returncode != 0:
            try:
                rep = json.loads(report.read_text(encoding="utf-8"))
                return False, "drawable_regions: " + json.dumps(rep.get("errors", rep), ensure_ascii=False)[:400]
            except (OSError, ValueError):
                return False, "drawable_regions: " + (r.stdout + r.stderr)[-400:]
    return True, ""


# ------------------------------------------------------------------ render một màn
def concat_audio(audios: list[Path], out: Path):
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{Path(a).resolve().as_posix()}'\n" for a in audios), encoding="utf-8")
    r = procs.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                   "-ar", "48000", "-ac", "1", str(out)], timeout=600)
    lst.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError("nối giọng màn lỗi: " + (r.stderr or "")[-300:])


def render_act(cfg: Cfg, image: Path, ann: Path, audio: Path, out: Path, params: dict | None = None):
    """Engine nét của repo Nikola, KHÔNG camera/chuyển cảnh; ghép giọng của màn vào clip."""
    n = conf(cfg)
    p = {"ink_path": n["ink_path"], "hand_follow": n["hand_follow"], "hand_height": n["hand_height"],
         **(params or {})}
    silent = out.with_name(out.stem + "_silent.mp4")
    r = _py(n, [n["vendor"] / "scripts" / "render_stream_whiteboard.py", image, ann, silent,
                "--ink-path", p["ink_path"], "--color-fill", n["color_fill"],
                "--hand-follow", p["hand_follow"], "--hand-height", p["hand_height"]],
            timeout=3600, cwd=str(n["vendor"]))
    if r.returncode != 0 or not silent.exists():
        raise RuntimeError("render Nikola lỗi: " + (r.stderr or r.stdout)[-600:])
    r = procs.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(audio),
                   "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                   "-af", "apad", "-shortest", str(out)], timeout=600)
    silent.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError("ghép giọng vào màn lỗi: " + (r.stderr or "")[-300:])


# ------------------------------------------------------------------ kiểm khung (stroke-story §10, semantic-island §7)
def frame_at(video: Path, ms: int) -> np.ndarray | None:
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{max(0, ms) / 1000:.3f}", "-i", str(video),
                        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)


def _sim(a: np.ndarray, b: np.ndarray) -> float:
    a = cv2.cvtColor(cv2.resize(a, (320, 180)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    b = cv2.cvtColor(cv2.resize(b, (320, 180)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(1 - np.abs(a - b).mean() / 255)


def check_act(clip: Path, image: Path, ann: dict) -> dict:
    """Màn thử / màn bất kỳ: khung đầu sạch, đảo phải không lộ sớm, đảo trái còn sau khi đảo phải bắt đầu,
    0.3–0.5 s cuối khớp ảnh gốc."""
    src = cv2.imread(str(image), cv2.IMREAD_COLOR)
    issues, res = [], {}
    total = ann["sceneDurationMs"]
    f0 = frame_at(clip, 0)
    if f0 is None:
        return {"pass": False, "issues": ["không giải mã được clip"]}
    first_ink = float(_ink(f0).mean())
    res["first_ink"] = round(first_ink, 4)
    if first_ink > 0.01:
        issues.append(f"khung đầu không sạch ({first_ink:.1%} mực)")
    els = ann["elements"]
    if len(els) == 2:
        r = els[1]["region"]
        t_r = els[1]["reveal"]["startMs"]
        before = frame_at(clip, t_r - 200)
        after = frame_at(clip, t_r + 200)
        W, H = ann["canvas"]["width"], ann["canvas"]["height"]
        if before is not None:
            sl = (slice(r["y"], r["y"] + r["height"]), slice(r["x"], r["x"] + r["width"]))
            leak = float(_ink(cv2.resize(before, (W, H)))[sl].mean())
            res["right_leak"] = round(leak, 4)
            if leak > 0.01:
                issues.append(f"đảo phải lộ sớm ({leak:.1%})")
        if after is not None:
            l_ = els[0]["region"]
            sl = (slice(l_["y"], l_["y"] + l_["height"]), slice(l_["x"], l_["x"] + l_["width"]))
            kept = _sim(cv2.resize(after, (W, H))[sl], cv2.resize(src, (W, H))[sl]) if src is not None else 1
            res["left_kept"] = round(kept, 3)
            if kept < 0.9:
                issues.append(f"đảo trái không còn nguyên khi vẽ đảo phải ({kept:.2f})")
    last = frame_at(clip, total - 400)
    if last is not None and src is not None:
        s = _sim(last, src)
        res["last_vs_source"] = round(s, 3)
        if s < 0.9:
            issues.append(f"khung cuối khác ảnh gốc ({s:.2f})")
    res["issues"] = issues
    res["pass"] = not issues
    return res


# ------------------------------------------------------------------ chữ khoá (lớp ASS chuẩn)
KW_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {"i": {"type": "integer"}, "keyword": {"type": "string"}},
    "required": ["i", "keyword"], "additionalProperties": False}}}, "required": ["items"],
    "additionalProperties": False}


def keywords(llm, rows: list[dict]) -> dict[int, str]:
    """Qwen trích 2–6 chữ khoá cho mỗi cảnh (chính xác theo lời đọc; số/tên riêng giữ nguyên)."""
    out = {}
    for k in range(0, len(rows), 25):
        part = rows[k:k + 25]
        body = "\n".join(f"{r['i']}: {r['narration'][:300]}" for r in part)
        try:
            res = llm.chat([{"role": "user", "content":
                             "Với mỗi cảnh, chọn MỘT cụm chữ khoá tiếng Việt 2–6 từ, lấy nguyên văn từ lời đọc "
                             "(ưu tiên con số, tên riêng, khái niệm chính). Trả JSON.\n" + body}],
                           schema=KW_SCHEMA, temperature=0.2, max_tokens=2000)
        except Exception:
            continue
        for it in res.get("items", []):
            kw = re.sub(r"\s+", " ", str(it.get("keyword") or "")).strip()
            if kw and len(kw.split()) <= 6:
                out[int(it["i"])] = kw
    return out


def _ass_ts(ms):
    h, ms = divmod(int(ms), 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h}:{m:02}:{s:02}.{ms // 10:02}"


def write_ass(cues: list, kws: list[dict], out: Path, w=1920, h=1080):
    """Phụ đề (đáy) + chữ khoá hiện từng chữ bằng \\kf (rãnh giữa / phía trên), sau khi đảo tương ứng vẽ xong."""
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 2\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            "Style: Default,Arial,52,&H00303030,&H00303030,&H00F5EBD7,&H80F5EBD7,1,1,3,0,2,80,80,50\n"
            "Style: Key,Arial,62,&H002A5BE0,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,1,1,4,0,8,60,60,60\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    rows = []
    for a, b, text, _e in cues:
        t = text.replace("{", "(").replace("}", ")")
        rows.append(f"Dialogue: 0,{_ass_ts(a)},{_ass_ts(b)},Default,,0,0,0,,{t}")
    for k in kws:
        words = k["text"].replace("{", "(").replace("}", ")").split()
        if not words:
            continue
        step = max(8, int(k.get("reveal_ms", 700) / 10 / len(words)))
        kf = "".join(f"{{\\kf{step}}}{wd} " for wd in words).strip()
        pos = f"{{\\an8\\pos({int(k['x'])},{int(k['y'])})}}"
        rows.append(f"Dialogue: 1,{_ass_ts(k['start'])},{_ass_ts(k['end'])},Key,,0,0,0,,{pos}{kf}")
    out.write_text(head + "\n".join(rows) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ ghép cuối bằng finalize_stroke_video.py
def finalize(cfg: Cfg, root: Path, act_rows: list[dict], narration: Path, ass: Path | None, out: Path):
    """act_rows: [{video, image, durationMs}] – đường dẫn tuyệt đối; manifest đặt trong root."""
    n = conf(cfg)
    manifest = {"width": 1920, "height": 1080, "fps": 30, "narration": _rel(narration, root),
                "scenes": [{"video": _rel(Path(a["video"]), root), "image": _rel(Path(a["image"]), root),
                            "durationMs": int(a["durationMs"])} for a in act_rows]}
    if ass:
        manifest["ass"] = _rel(ass, root)
    mf = root / "stroke-finalize.json"
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    ff = shutil.which("ffmpeg") or "ffmpeg"
    r = _py(n, [n["repo"] / "scripts" / "finalize_stroke_video.py", "--manifest", mf, "--ffmpeg", ff,
                "--source-overlay", "never", "--output", out], timeout=14400)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("finalize_stroke_video lỗi: " + (r.stderr or r.stdout)[-600:])
    return mf


def _rel(p: Path, root: Path) -> str:
    try:
        return Path(p).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(p).resolve().as_posix()


def verify_final(video: Path, act_rows: list[dict]) -> dict:
    """Checklist cuối của Nikola: khung quanh mỗi điểm cắt (±3 khung), khung đen, 0.3–0.5 s cuối khớp ảnh màn
    cuối, giải mã hết file."""
    issues, t = [], 0
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or r.stderr.strip():
        issues.append("giải mã có lỗi: " + r.stderr.strip()[-200:])
    for i, a in enumerate(act_rows):
        t += int(a["durationMs"])
        if i == len(act_rows) - 1:
            break
        for off in (-100, -33, 33, 100):
            f = frame_at(video, t + off)
            if f is not None and float(f.mean()) < 20:
                issues.append(f"khung đen quanh điểm cắt màn {i + 1}")
                break
    if act_rows:
        last = frame_at(video, t - 400)
        src = cv2.imread(str(act_rows[-1]["image"]), cv2.IMREAD_COLOR)
        if last is not None and src is not None and _sim(last, src) < 0.85:
            issues.append(f"0.4 s cuối khác ảnh màn cuối ({_sim(last, src):.2f})")
    return {"pass": not issues, "issues": issues, "acts": len(act_rows), "duration_ms": t}
