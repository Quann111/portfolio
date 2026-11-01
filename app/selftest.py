"""Test từng bước của quy trình (bấm khi app ĐÃ DỪNG): mỗi bước gọi lại đúng hàm mà job thật dùng, lưu file kết quả
vào exchange/selftest/ và kết quả vào db.kv "selftest" để giao diện hiển thị.

Thêm bước mới vào pipeline → thêm 1 hàm test ở đây và 1 dòng trong STEPS (xem AGENTS.md)."""
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from . import db, events
from .config import ROOT, load

OUT = ROOT / "exchange" / "selftest"
KEY = "selftest"
DEFAULT_TOPIC = "Giá xăng E10 lên 26.530 đồng/lít"
SENTENCE = "Xin chào, đây là bài kiểm tra giọng đọc của hệ thống AutoVideo, giá xăng hôm nay là hai mươi sáu nghìn đồng."
_run_lock = threading.Lock()


def _llm(cfg, role="light"):
    from .llm import LLM
    llm = LLM(cfg, _log)
    llm.role = role
    return llm


def _log(msg):
    events.push("log", {"msg": f"🧪 {msg}"})


def _test_image() -> Path | None:
    """Ảnh dùng cho bước vẽ/ghép/thumbnail: ảnh Flow test mới nhất, không có thì tự vẽ 1 ảnh đơn giản bằng OpenCV."""
    imgs = sorted((ROOT / "exchange" / "test_images").glob("*.png"), key=lambda p: p.stat().st_mtime)
    if imgs:
        return imgs[-1]
    import cv2
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / "sample.png"
    img = np.full((768, 1344, 3), (215, 235, 245), np.uint8)
    cv2.circle(img, (420, 360), 160, (40, 40, 40), 6)
    cv2.rectangle(img, (760, 220), (1060, 500), (40, 40, 40), 6)
    cv2.arrowedLine(img, (600, 360), (740, 360), (30, 60, 200), 6)
    cv2.imwrite(str(f), img)
    return f


# ------------------------------------------------------------------ từng bước
def t_preflight(cfg, topic):
    from . import preflight
    probs = preflight.check(cfg)
    if probs:
        return False, "Thiếu / lỗi: " + "; ".join(probs[:6]), None
    return True, "Máy đủ điều kiện (ffmpeg, GPU, ổ đĩa, model…)", None


def t_llm(cfg, topic):
    r = _llm(cfg).chat([{"role": "user", "content": "Return JSON {\"ok\": true}."}],
                       {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                        "additionalProperties": False}, temperature=0, max_tokens=50)
    return bool(r.get("ok")), f"LM Studio trả lời đúng JSON ({cfg.lmstudio.get('models', {}).get('light', '')})", None


def t_research(cfg, topic):
    from . import websearch
    pages = websearch.collect(topic, cfg, _llm(cfg), _log)
    ok = len(pages) > 0
    return ok, f"Đọc được {len(pages)} trang web" + (": " + ", ".join(p.get("domain") or p.get("url", "")[:40]
                                                                         for p in pages[:4]) if pages else ""), None


def t_notebook(cfg, topic):
    from . import notebook
    if not notebook.connected():
        return False, "Extension NotebookLM chưa kết nối (mở tab notebooklm.google.com ở profile có extension)", None
    ans = notebook.ask(0, "selftest", "Chỉ trả lời đúng một từ: OK", time.time() + 180, lambda: None)
    return "ok" in (ans or "").lower(), f"NotebookLM trả lời: {(ans or '')[:80]}", None


def t_gemini(cfg, topic):
    from . import gemini_bridge
    if not gemini_bridge.connected():
        return False, "Extension Gemini Web chưa kết nối (mở tab gemini.google.com ở profile có extension)", None
    ans = gemini_bridge.ask(0, "selftest", "Chỉ trả lời đúng một từ: OK", time.time() + 180)
    return "ok" in (ans or "").lower(), f"Gemini trả lời: {(ans or '')[:80]}", None


def t_tts(cfg, topic):
    from . import audio_qc
    from .tts import TTS
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / "voice.wav"
    t = TTS(cfg, _log)
    want = t.engine
    total, speech, _ = t.speak(SENTENCE, f)
    heard = t.transcribe(f)
    err = audio_qc.asr_error(SENTENCE, heard, cfg.language)
    t.release()
    used = t.engine + (f" (⚠ {want} lỗi, đã chuyển sang {t.engine})" if t.engine != want else "")
    ok = err < 0.25
    return ok, f"Giọng {used}, {total / 1000:.1f}s; Whisper nghe sai {err:.0%}: \"{heard[:70]}\"", f


def t_image(cfg, topic):
    from . import flowbridge, image, visual_rules
    if not flowbridge.active_workers():
        return False, "Không có profile Flow nào đang kết nối", None
    prompt = image.build_prompt({"image_prompt": f"One whiteboard explainer illustration about: {topic}. "
                                                  + visual_rules.WRITER_RULE_EN}, {}, cfg, target="flow")
    now = time.time()
    uid = f"test_{int(now * 1000)}"
    db.x("INSERT INTO flow_tasks(job_id, idx, uid, round, prompt, created_at, status) VALUES(0, 1, ?, 1, ?, ?, 'queued')",
         (uid, prompt, now))
    tid = db.q("SELECT id FROM flow_tasks WHERE uid=?", (uid,))[0]["id"]
    _log(f"Đã gửi prompt test vào Flow (task #{tid}), chờ tối đa 10 phút…")
    while time.time() - now < 600:
        t = db.q("SELECT status, image, error FROM flow_tasks WHERE id=?", (tid,))[0]
        if t["status"] == "done" and t["image"]:
            f = Path(t["image"])
            qc = image.check(f, {"image_prompt": topic, "visual_concept": topic, "narration": topic,
                              "drawing_order": []}, None,
                             _llm(cfg, "vision"), cfg)
            bad = qc.get("failed_gates") or []
            return not bad, (f"Ảnh về sau {time.time() - now:.0f}s; QC " +
                             ("đạt" if not bad else "trượt: " + ", ".join(bad))), f
        if t["status"] in ("error", "cancelled"):
            return False, f"Flow lỗi: {t['error']}", None
        time.sleep(3)
    db.x("UPDATE flow_tasks SET status='cancelled' WHERE id=?", (tid,))
    return False, "Hết 10 phút chưa có ảnh – kiểm tra profile Flow đang tạm dừng (x2? captcha?)", None


def t_render(cfg, topic):
    from . import annotate, render
    from .tts import TTS
    OUT.mkdir(parents=True, exist_ok=True)
    img, audio = _test_image(), OUT / "voice.wav"
    if not audio.exists():
        TTS(cfg, _log).speak(SENTENCE, audio)
    ms = render.duration_ms(audio)
    ann = OUT / "scene.annotation.json"
    clip = OUT / "scene-whiteboard.mp4"
    annotate.build(img, ann, "selftest", SENTENCE, ms, cfg.whiteboard.max_regions, None)
    render.scene_clip(img, ann, audio, clip, cfg)
    ok = clip.exists() and clip.stat().st_size > 10_000
    return ok, f"Vẽ whiteboard {ms / 1000:.1f}s từ ảnh {img.name}", clip if ok else None


def t_assemble(cfg, topic):
    from . import render, subtitles
    clip = OUT / "scene-whiteboard.mp4"
    if not clip.exists():
        return False, "Chưa có clip – bấm test \"Vẽ whiteboard\" trước", None
    ms = render.duration_ms(clip)
    ass = OUT / "subs.ass"
    subtitles.write_ass(subtitles.cues([{"narration": SENTENCE, "duration_ms": ms}], [0]), ass)
    out = OUT / "video.mp4"
    render.assemble([clip], out, cfg, None, ass)
    ok = out.exists() and render.duration_ms(out) > 1000
    return ok, f"Ghép video + phụ đề {render.duration_ms(out) / 1000 if out.exists() else 0:.1f}s", out if ok else None


def t_thumbnail(cfg, topic):
    from . import thumbnail
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "thumbnail.jpg"
    thumbnail.compose(_test_image(), topic[:40], out)
    return out.exists(), "In chữ tiêu đề lên ảnh nền", out


def t_subtitles(cfg, topic):
    """Đúng luồng production: gộp câu (đơn vị dịch) → dịch VI→EN → cắt lại thành cue hiển thị."""
    from . import subtitles
    OUT.mkdir(parents=True, exist_ok=True)
    raw = [(0, 1200, f"Hôm nay chúng ta tìm hiểu {topic[:40]}", ""), (1200, 2500, "về an ninh khu vực.", ""),
           (2600, 4000, "Giá xăng tăng 660 đồng", ""), (4000, 5200, "mỗi lít từ 15 giờ chiều nay.", "")]
    sents = subtitles.merge_sentences(raw, max_chars=600, max_ms=40000)
    en_sents = subtitles.translate_cues(sents, _llm(cfg, "text"), _log)
    cs, en = subtitles.split_sentences(sents, en_sents)
    out = OUT / "subtitles_en.srt"
    subtitles.write_srt_crlf(en, out)
    ok = (len(sents) == 2 and len(cs) == len(en) > 0
          and all(a[:2] == b[:2] for a, b in zip(cs, en))
          and all(not subtitles._still_vi(c[2]) for c in en))
    return ok, f"Gộp {len(raw)} mảnh → {len(sents)} câu → {len(cs)} cue EN: " + \
        " / ".join(c[2].replace("\n", " ") for c in en), out


STEPS = [  # (key, tên hiển thị, hàm)
    ("preflight", "🖥 Kiểm tra máy", t_preflight),
    ("llm", "🧠 LM Studio (Qwen)", t_llm),
    ("research", "🔎 Nghiên cứu web", t_research),
    ("notebook", "📒 NotebookLM", t_notebook),
    ("gemini", "✨ Gemini Web", t_gemini),
    ("tts", "🎙 Giọng đọc + Whisper", t_tts),
    ("image", "🖼 Tạo ảnh Flow + QC", t_image),
    ("render", "✏️ Vẽ whiteboard", t_render),
    ("assemble", "🎬 Ghép video + phụ đề", t_assemble),
    ("thumbnail", "📺 Thumbnail", t_thumbnail),
    ("subtitles", "💬 Phụ đề Việt + Anh", t_subtitles),
]
FUNCS = {k: f for k, _, f in STEPS}


def results() -> dict:
    r = db.kv_get(KEY) or {}
    return {"steps": [{"key": k, "name": n, **r.get(k, {"state": "idle"})} for k, n, _ in STEPS],
            "busy": _run_lock.locked()}


def _set(key, **kw):
    r = db.kv_get(KEY) or {}
    r[key] = kw
    db.kv_set(KEY, r)
    events.push("selftest", {"key": key, **kw})


def _rel(f: Path | None) -> str | None:
    """Đường dẫn cho /files/… (chỉ phục vụ file trong exchange/)."""
    if not f:
        return None
    try:
        return "/files/" + Path(f).resolve().relative_to((ROOT / "exchange").resolve()).as_posix()
    except ValueError:
        return None


def run_one(key: str, topic: str) -> bool:
    cfg = load()
    _set(key, state="running", at=time.time())
    _log(f"Test {key}: bắt đầu")
    t0 = time.time()
    try:
        ok, msg, f = FUNCS[key](cfg, topic)
    except Exception as e:
        ok, msg, f = False, f"{type(e).__name__}: {e}", None
        _log(traceback.format_exc(limit=4))
    _set(key, state="ok" if ok else "error", msg=str(msg)[:500], sec=round(time.time() - t0, 1),
         file=str(f) if f else None, url=_rel(f), at=time.time())
    _log(f"Test {key}: {'✅' if ok else '❌'} {msg}")
    return ok


def start(step: str, topic: str = "") -> str | None:
    """Chạy nền 1 bước hoặc 'all' (lần lượt, dừng ở bước lỗi đầu tiên). Trả lý do từ chối, None nếu đã chạy."""
    if step != "all" and step not in FUNCS:
        return "bước không hợp lệ"
    if not _run_lock.acquire(blocking=False):
        return "đang có test khác chạy"
    topic = (topic or DEFAULT_TOPIC).strip()

    def go():
        try:
            for k in ([s for s, _, _ in STEPS] if step == "all" else [step]):
                if not run_one(k, topic) and step == "all":
                    _log(f"Dừng 'Test tất cả' ở bước lỗi: {k}")
                    break
        finally:
            _run_lock.release()
    threading.Thread(target=go, name="selftest", daemon=True).start()
    return None
