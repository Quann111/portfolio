import json
import re
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .llm import NO_WINDOW

FRAME_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "issue": {"type": "string"}},
    "required": ["ok", "issue"],
}


def _ff(args, timeout=3600):
    r = subprocess.run(["ffmpeg", "-hide_banner", *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
    return r.stderr


def _streams(video: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration:format=duration",
                        "-of", "json", str(video)], capture_output=True, text=True, creationflags=NO_WINDOW)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}


def hard_gate(video: Path, scenes, manifest, expected_ms: int, tech: dict, outline: dict) -> list[dict]:
    """Lỗi chặn xuất video bất kể điểm tổng. Mỗi lỗi kèm loại để auto repair chọn đúng hành động."""
    fails = []
    info = _streams(video)
    kinds = [s.get("codec_type") for s in info.get("streams", [])]
    if "video" not in kinds:
        fails.append({"type": "video_invalid", "detail": "file không có stream video / không mở được"})
    if "audio" not in kinds:
        fails.append({"type": "audio_missing", "detail": "không có stream audio"})
    try:
        durs = {s["codec_type"]: float(s["duration"]) for s in info["streams"] if s.get("duration")}
        if "video" in durs and "audio" in durs and abs(durs["video"] - durs["audio"]) > 0.5:
            fails.append({"type": "av_desync", "detail": f"lệch audio/video {durs['video'] - durs['audio']:+.2f}s"})
    except (KeyError, ValueError):
        pass
    missing = [s["idx"] for s in scenes if not manifest.ok(s.get("clip"))]
    if missing:
        fails.append({"type": "missing_scene", "scenes": missing, "detail": f"clip thiếu/hỏng: {missing}"})
    bad_audio = [s["idx"] for s in scenes if not manifest.ok(s.get("audio"))]
    if bad_audio:
        fails.append({"type": "audio_broken", "scenes": bad_audio, "detail": f"wav thiếu/hỏng: {bad_audio}"})
    if tech.get("black_segments", 0) > 0:
        fails.append({"type": "black_frames", "detail": f"{tech['black_segments']} đoạn khung đen"})
    if tech.get("long_silences", 0) > 0:
        fails.append({"type": "unexpected_silence", "detail": f"{tech['long_silences']} đoạn im lặng > 3s"})
    if expected_ms and abs(tech.get("duration_ms", 0) - expected_ms) > max(3000, expected_ms * 0.05):
        fails.append({"type": "duration_mismatch", "detail": f"{tech.get('duration_ms')} ms vs {expected_ms} ms"})
    for issue in outline.get("blocking_issues", []):
        fails.append({"type": "content_severe", **issue})
    srt = video.with_suffix(".srt")
    if not srt.exists() or srt.stat().st_size == 0:
        fails.append({"type": "subtitle_missing", "detail": "thiếu file phụ đề"})
    return fails


# loại lỗi → hành động sửa (chỉ làm lại phần bị ảnh hưởng)
REPAIR_ACTION = {
    "semantic": "regen_image",           # hình sai nghĩa → sửa concept/prompt + tạo lại ảnh
    "too_complex": "simplify_image",     # quá phức tạp → đơn giản hoá
    "pronunciation": "redo_tts",         # sai thuật ngữ → từ điển + TTS lại
    "timing": "retime",                  # lệch timing → annotation/phụ đề lại
    "render": "rerender",                # chuyển cảnh/khung lỗi → render lại đoạn đó
    "missing_scene": "rerender",
    "audio_broken": "redo_tts",
    "loudness": "remix",                 # âm lượng → mix lại
    "repetition": "rewrite_chapter",     # lặp nội dung → viết lại chương
    "video_invalid": "reassemble",       # lỗi kỹ thuật → ghép lại
    "audio_missing": "reassemble",
    "av_desync": "reassemble",
    "black_frames": "rerender",
    "unexpected_silence": "redo_tts",
    "duration_mismatch": "reassemble",
    "subtitle_missing": "reassemble",
    "content_severe": "rewrite_chapter",
}


def technical(video: Path, expected_ms: int) -> dict:
    log = _ff(["-i", video, "-vf", "blackdetect=d=1:pix_th=0.10", "-af", "silencedetect=n=-45dB:d=3",
               "-f", "null", "-"])
    black = [b for b in re.findall(r"black_duration:([\d.]+)", log) if float(b) >= 1.0]
    silence = [s for s in re.findall(r"silence_duration: ([\d.]+)", log) if float(s) >= 3.0]
    loud = _ff(["-i", video, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", loud)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", loud)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", log)
    dur = (int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])) * 1000 if m else 0
    score = 100
    score -= min(40, 10 * len(black))
    score -= min(30, 5 * len(silence))
    if expected_ms and abs(dur - expected_ms) > max(3000, expected_ms * 0.05):
        score -= 20
    lufs_v = float(lufs[-1]) if lufs else None
    tp_v = float(tp[-1]) if tp else None
    if lufs_v is not None and abs(lufs_v - (-16)) > 3:
        score -= 10
    if tp_v is not None and tp_v > -1.0:
        score -= 10
    return {"score": max(0, score), "black_segments": len(black), "long_silences": len(silence),
            "duration_ms": int(dur), "lufs": lufs_v, "true_peak": tp_v}


def _frame(video: Path, at_sec: float, out: Path):
    _ff(["-y", "-loglevel", "error", "-ss", f"{max(0, at_sec):.2f}", "-i", video, "-frames:v", "1", out], 120)
    return out if out.exists() else None


def render_ok(clip: Path, image: Path, work: Path) -> bool:
    """Khung cuối của clip phải trùng ảnh gốc (đã vẽ xong hết)."""
    f = work / f"{clip.stem}_last.png"
    _ff(["-y", "-loglevel", "error", "-sseof", "-0.3", "-i", clip, "-frames:v", "1", f], 120)
    if not f.exists():
        return False
    a = cv2.imread(str(f))
    b = cv2.imdecode(np.fromfile(str(image), dtype=np.uint8), cv2.IMREAD_COLOR)
    f.unlink(missing_ok=True)
    if a is None or b is None:
        return False
    b = cv2.resize(b, (a.shape[1], a.shape[0]))
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(ga - gb).mean()) < 25


def visual_spot_check(scenes, offsets_ms, video: Path, work: Path, llm, n=8) -> list[int]:
    """Qwen soi khung giữa của một số cảnh; trả về idx cảnh lỗi."""
    if not scenes:
        return []
    step = max(1, len(scenes) // n)
    bad = []
    for s, off in list(zip(scenes, offsets_ms))[::step][:n]:
        at = (off + s["duration_ms"] * 0.9) / 1000
        f = _frame(video, at, work / f"qc_{s['idx']:03}.png")
        if not f:
            bad.append(s["idx"])
            continue
        res = llm.look(f, "This is a frame from a whiteboard animation (a hand drawing a sketch on beige "
                          f"paper) while this is narrated: \"{s['narration'][:300]}\". ok=false only if the frame "
                          "is technically broken: blank paper, black, glitched or corrupted, or objects cut in "
                          "half. Simple, crude or partially drawn sketches are normal (ok=true).",
                       FRAME_SCHEMA)
        if not res["ok"]:
            f.rename(f.with_name(f"{f.stem}_FAIL.png"))
        else:
            f.unlink(missing_ok=True)
        if not res["ok"]:
            bad.append(s["idx"])
    return bad


PARTS = ("content", "retention", "visual", "alignment", "continuity", "audio", "render", "technical")
WEIGHTS = {"content": 0.12, "retention": 0.18, "visual": 0.18, "alignment": 0.12, "continuity": 0.05,
           "audio": 0.12, "render": 0.11, "technical": 0.12}


def score(outline: dict, scenes, tech: dict, bad_render: list, bad_frames: list) -> dict:
    qcs = [json.loads(s["qc"]) for s in scenes if s.get("qc")]
    avg = lambda k: (sum(q.get(k, 0) for q in qcs if k in q) / max(1, sum(1 for q in qcs if k in q)) * 100) \
        if any(k in q for q in qcs) else 70
    reviews = outline.get("reviews") or []
    retention = (sum(r["retention"] for r in reviews) / len(reviews) * 100) if reviews else 70
    beats = [c.get("beat") for c in outline.get("chapters", [])]
    body = [r for i, r in enumerate(reviews) if i >= len(beats) or beats[i] not in ("recap", "cta")] or reviews
    content = (sum(1 - r["repetition"] for r in body) / len(body) * 100) if body else 70
    sr = outline.get("story_review") or {}
    if sr:   # cộng điểm mạch truyện toàn video (lời hứa, tiến triển, cao trào, kết)
        arc = sum(bool(sr.get(k)) for k in ("promise_kept", "progression_ok", "climax_prepared",
                                             "memorable_ending")) / 4 * 100
        content = 0.6 * content + 0.4 * arc
    aqs = [json.loads(s["audio_qc"]) for s in scenes if s.get("audio_qc")]
    audio = (sum(100 * (1 - min(1, a.get("asr_error", 0) * 2)) - 15 * len(a.get("issues", [])) for a in aqs)
             / len(aqs)) if aqs else 70
    n = max(1, len(scenes))
    render_score = 100 * (1 - (len(set(bad_render) | set(bad_frames)) / n))
    parts = {
        "content": round(content), "retention": round(retention),
        "visual": round(avg("semantic") * 0.5 + avg("style") * 0.3 + avg("composition") * 0.2),
        "alignment": round(avg("alignment")), "continuity": round(avg("continuity")),
        "audio": round(max(0, audio)), "render": round(render_score), "technical": tech["score"],
    }
    parts["total"] = round(sum(parts[k] * WEIGHTS[k] for k in WEIGHTS), 1)
    return parts


def repair_targets(scenes, bad_render, bad_frames, max_ratio=0.2) -> tuple[list, list]:
    """(cảnh cần tạo lại ảnh, cảnh chỉ cần render lại)."""
    qcs = [(json.loads(s["qc"]).get("overall", 1) if s.get("qc") else 1, s["idx"]) for s in scenes]
    weak = [i for o, i in sorted(qcs) if o < 0.8][: max(1, int(len(scenes) * max_ratio))]
    redo_img = sorted(set(weak))
    rerender = sorted((set(bad_render) | set(bad_frames)) - set(redo_img))
    return redo_img, rerender
