"""Bộ đăng YouTube cho mỗi job: Title + Description (song ngữ VI/EN, chuẩn SEO, khung giống các kênh mẫu) do
NotebookLM viết (Qwen dự phòng) + Thumbnail (nền Google Flow, chữ in bằng Pillow). Ghi THẲNG vào thư mục dự án
của job: projects/<id>_<tên>/youtube/. Chạy ở luồng phụ, song song với các bước làm video."""
import json
import re
import threading
import time
import traceback
from pathlib import Path

from . import db, flowbridge, notebook, packager, subtitles, thumbnail
from .config import Cfg
from .visual_rules import QC_TEXT_VI

SCHEMA_KEYS = ("titles", "thumb_text", "thumb_scene", "thumb_subject", "thumb_context", "hook", "intro", "key_points", "conclusion",
               "comment_question", "hashtags", "tags")
_threads: dict[int, threading.Thread] = {}


def folder(jid: int) -> Path:
    try:
        return packager.folder_of(jid) / "youtube"
    except KeyError:
        # Nếu job đã bị xoá khỏi bảng jobs (hoặc job cũ), fallback về thư mục chuẩn trong projects/
        return packager.PROJECTS / f"{jid:04}_job_{jid}" / "youtube"


def status(jid: int) -> dict:
    return db.kv_get(f"yt_{jid}") or {}


def _set(jid: int, **kw):
    db.kv_set(f"yt_{jid}", {**status(jid), **kw, "at": time.time()})


def conf(cfg: Cfg) -> dict:
    y = dict(cfg.get("youtube") or {})
    y.setdefault("handle", "@kienthucbangtrang")
    y.setdefault("channel_tags", ["#KienThucBangTrang", "#WhiteboardExplained"])
    y.setdefault("thumb_variants", 2)
    y.setdefault("wait_minutes", 10)
    y.setdefault("thumb_date", True)        # huy hiệu "THÁNG x / ngày" góc thumbnail như kênh tin tức
    y.setdefault("subtitles_en", True)      # phụ đề tiếng Anh (dịch từ phụ đề tiếng Việt)
    return y


# ------------------------------------------------------------------ prompt
def _prompt(job: dict, ol: dict, cfg: Cfg) -> str:
    facts = json.loads(job.get("facts") or "[]")
    chs = "\n".join(f"- {c.get('title', '')}: {c.get('key_takeaway') or c.get('summary', '')}"[:200]
                    for c in ol.get("chapters", []))
    lang = ("Trả về JSON có 2 khoá 'vi' và 'en', mỗi khoá là một object cùng cấu trúc. Bản 'en' KHÔNG dịch từng chữ "
            "mà viết lại tự nhiên kiểu kênh giải thích tiếng Anh (vd 'Yamaha Sirius on a $800 Budget: Are Gas-Powered "
            "Bikes Still Worth It in the Age of EVs?'), quy tiền VND sang USD gần đúng khi hợp lý.")
    head = (f"Viết bộ đăng YouTube cho video '{ol.get('title') or job['topic']}'. Thông điệp: {ol.get('core_message', '')}\n"
            f"Các chương:\n{chs}\nDữ kiện (chỉ được dùng số liệu có ở đây, không bịa):\n"
            f"{notebook._facts_text(facts, 900)}\n"
            "Yêu cầu: giật gân, tò mò, đánh vào mâu thuẫn/con số/câu hỏi như các kênh triệu view nhưng KHÔNG bịa, "
            "không chính trị Việt Nam, không thuyết âm mưu. Từ khoá chính phải nằm trong 60 ký tự đầu của tiêu đề và "
            "câu đầu mô tả.\n"
            "Mỗi object gồm: titles (5 tiêu đề, mỗi cái ≤ 70 ký tự); thumb_text (3 phương án chữ trên thumbnail kiểu "
            "kênh tin tức, 4–9 từ, VIẾT HOA, nêu đúng nhân vật/con số/mâu thuẫn GÂY TÒ MÒ của chính video này, vd "
            "'MBAPPÉ TUỔI GÌ KHI VOLVO, MERCEDES CÒN ĐANG BỊ THÂU TÓM.'); thumb_subject (tiếng Anh: NHÂN VẬT/VẬT THẬT "
            "trung tâm của câu chuyện – tên người nổi tiếng hoặc vật cụ thể + biểu cảm mạnh, vd 'Kylian Mbappé, both "
            "hands on head, shocked'); thumb_context (tiếng Anh: 2–3 vật/biểu tượng bối cảnh đúng nội dung – cờ quốc "
            "gia, sản phẩm, toà nhà, biểu đồ, đám đông…); thumb_scene (tiếng Anh: 1 câu tả cả cảnh ẢNH CHỤP THẬT kiểu "
            "báo chí, không chữ, không phải tranh vẽ); hook (1–2 câu hỏi mở đầu ≤ 160 ký tự); intro (đoạn 'Trong video này…' 2 câu); "
            "key_points (5 câu hỏi ngắn); conclusion (1–2 câu kiểu 'không có lựa chọn đúng cho tất cả…'); "
            "comment_question (câu hỏi rủ bình luận); hashtags (5 hashtag chủ đề, không dấu, có #); "
            "tags (8 thẻ tìm kiếm).\n" + lang + "\n")
    tail = f"Chỉ trả JSON (không khối code) giữa hai dòng {notebook.START} và {notebook.END}."
    return (head + tail)[: notebook.MAX_PROMPT]


QWEN_SCHEMA_ONE = {"type": "object", "properties": {
    "titles": {"type": "array", "items": {"type": "string"}},
    "thumb_text": {"type": "array", "items": {"type": "string"}},
    "thumb_scene": {"type": "string"}, "thumb_subject": {"type": "string"}, "thumb_context": {"type": "string"},
    "hook": {"type": "string"}, "intro": {"type": "string"},
    "key_points": {"type": "array", "items": {"type": "string"}}, "conclusion": {"type": "string"},
    "comment_question": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}},
    "tags": {"type": "array", "items": {"type": "string"}}},
    "required": list(SCHEMA_KEYS), "additionalProperties": False}
QWEN_SCHEMA = {"type": "object", "properties": {"vi": QWEN_SCHEMA_ONE, "en": QWEN_SCHEMA_ONE},
               "required": ["vi", "en"], "additionalProperties": False}


def _clean(d: dict) -> dict:
    out = {}
    for k in SCHEMA_KEYS:
        v = d.get(k)
        if isinstance(v, list):
            v = [re.sub(r"\s+", " ", str(x)).strip() for x in v if str(x).strip()]
        else:
            v = re.sub(r"\s+", " ", str(v or "")).strip()
        out[k] = v
    out["titles"] = [t if len(t) <= 100 else t[:97].rsplit(" ", 1)[0] + "…" for t in out["titles"]][:5]
    out["thumb_text"] = [t.upper() for t in out["thumb_text"] if 1 <= len(t.split()) <= 10][:3]
    out["hashtags"] = ["#" + re.sub(r"[^\w]", "", h.lstrip("#")) for h in out["hashtags"] if h.strip("#")][:6]
    return out


def _parse_text_output(text: str) -> dict | None:
    """Nếu NotebookLM trả về văn bản có cấu trúc thay vì JSON (vd danh sách có 1. TIÊU ĐỀ...,
    2. CHỮ TRÊN THUMBNAIL...), tự động bóc tách thành dict chuẩn."""
    if not text or ("TIÊU ĐỀ" not in text.upper() and "TITLES" not in text.upper()):
        return None
    vi = {}
    # 1. Titles
    m_tit = re.search(r"(?:1\.\s*)?TIÊU ĐỀ[^\n]*\n([\s\S]*?)(?=(?:2\.\s*)?CHỮ TRÊN THUMBNAIL|\Z)", text, re.IGNORECASE)
    titles = []
    if m_tit:
        for ln in m_tit.group(1).split("\n"):
            ln = ln.strip()
            if not ln or ln.startswith("(") or "phương án" in ln.lower():
                continue
            ln = re.sub(r"^\d+[\.\)]\s*", "", ln)
            ln = re.sub(r"\s*\(\d+\s*ký tự\)$", "", ln).strip()
            if ln:
                titles.append(ln)
    vi["titles"] = titles[:5]
    if not vi["titles"]:
        return None

    # 2. Thumb text
    m_th = re.search(r"(?:2\.\s*)?CHỮ TRÊN THUMBNAIL[^\n]*\n([\s\S]*?)(?=(?:3\.\s*)?CHỦ THỂ|\Z)", text, re.IGNORECASE)
    thumb_text = []
    if m_th:
        for ln in m_th.group(1).split("\n"):
            ln = ln.strip()
            if not ln or ln.startswith("(") or ("phương án" in ln.lower() and ":" not in ln):
                continue
            if ":" in ln:
                ln = ln.split(":", 1)[1].strip()
            ln = re.sub(r"\s*\(\d+\s*từ\)$", "", ln).strip()
            if ln:
                thumb_text.append(ln)
    vi["thumb_text"] = thumb_text[:3]

    # 3. Subject / Scene
    m_sub = re.search(r"(?:3\.\s*)?CHỦ THỂ HÌNH ẢNH[^\n]*\n([\s\S]*?)(?=(?:4\.\s*)?MÔ TẢ|\Z)", text, re.IGNORECASE)
    thumb_scene, thumb_subject = "", ""
    if m_sub:
        for ln in m_sub.group(1).split("\n"):
            ln = ln.strip()
            if not ln or ln.startswith("("):
                continue
            if ln.lower().startswith("thumb subject:"):
                thumb_subject = ln.split(":", 1)[1].strip()
            elif not thumb_scene:
                thumb_scene = ln
    vi["thumb_subject"] = thumb_subject or thumb_scene
    vi["thumb_scene"] = thumb_scene or thumb_subject
    vi["thumb_context"] = ""

    # 4. Description
    m_desc = re.search(r"(?:4\.\s*)?MÔ TẢ VIDEO[^\n]*\n([\s\S]*?)(?=(?:5\.\s*)?THẺ TAG|\Z)", text, re.IGNORECASE)
    if m_desc:
        desc_lines = [l for l in m_desc.group(1).split("\n") if l.strip() and not l.strip().startswith("(")]
        vi["hook"] = desc_lines[0] if len(desc_lines) > 0 else ""
        vi["intro"] = desc_lines[1] if len(desc_lines) > 1 else ""
        vi["hashtags"] = re.findall(r"#[A-Za-z0-9_À-ỹ]+", m_desc.group(1))[:6]
    vi.setdefault("key_points", [])
    vi.setdefault("conclusion", "")
    vi.setdefault("comment_question", "Bạn nghĩ sao về thông tin này? Hãy để lại bình luận bên dưới!")

    # 5. Tags
    m_tags = re.search(r"(?:5\.\s*)?THẺ TAG[^\n]*\n([\s\S]*?)(?=👉|Bạn có cần|\Z)", text, re.IGNORECASE)
    if m_tags:
        tags = [t.strip() for t in m_tags.group(1).split(",") if t.strip()]
        vi["tags"] = tags[:15]

    en = {
        "titles": [vi["titles"][0]],
        "thumb_text": [vi["thumb_text"][0]] if vi["thumb_text"] else [vi["titles"][0]],
        "thumb_subject": vi["thumb_subject"],
        "thumb_context": vi["thumb_context"],
        "thumb_scene": vi["thumb_scene"],
        "hook": vi.get("hook", ""),
        "intro": vi.get("intro", ""),
        "key_points": vi.get("key_points", []),
        "conclusion": vi.get("conclusion", ""),
        "comment_question": "What are your thoughts on this? Leave a comment below!",
        "hashtags": [h for h in vi.get("hashtags", []) if h.isascii()],
        "tags": [t for t in vi.get("tags", []) if t.isascii()] or ["News", "Analysis", "Documentary"]
    }
    return {"vi": _clean(vi), "en": _clean(en)}


def write_texts(jid: int, cfg: Cfg, log=print, llm=None) -> dict:
    job = db.q("SELECT * FROM jobs WHERE id=?", (jid,))[0]
    ol = json.loads(job.get("outline") or "{}")
    data, by = None, "notebooklm"
    try:
        txt = notebook.ask(jid, "youtube", _prompt(job, ol, cfg), time.time() + 25 * 60, lambda: None)
        try:
            raw = notebook._json_block(txt)
            if raw.get("vi") and raw.get("en"):
                data = {"vi": _clean(raw["vi"]), "en": _clean(raw["en"])}
        except Exception:
            raw = _parse_text_output(txt)
            if raw and raw.get("vi"):
                data = raw
    except Exception as e:
        log(f"📺 NotebookLM viết bộ YouTube lỗi ({str(e)[:120]}) → Qwen viết thay")
    if not data or not data["vi"]["titles"]:
        if llm is None:
            raise RuntimeError("không có NotebookLM lẫn Qwen để viết bộ YouTube")
        by = "qwen"
        # Rút gọn prompt cho Qwen fallback và đặt max_tokens vừa đủ (~2500) để không bao giờ tràn context
        qwen_prompt = _prompt(job, ol, cfg).rsplit("Chỉ trả JSON", 1)[0] + "Trả JSON."
        raw = llm.chat([{"role": "user", "content": qwen_prompt}], schema=QWEN_SCHEMA, temperature=0.5, max_tokens=2500)
        data = {"vi": _clean(raw["vi"]), "en": _clean(raw["en"])}
    data["by"] = by
    data["topic"] = job["topic"]
    save(jid, data, cfg)
    return data


# ------------------------------------------------------------------ description (khung giống kênh mẫu)
def _ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02}:{sec % 60:02}" if sec >= 3600 else f"{sec // 60:02}:{sec % 60:02}"


def chapters(jid: int, offsets_ms: list | None) -> list[tuple[float, str]]:
    """Mốc chương THẬT trong video (offset cảnh đầu tiên của mỗi chương). YouTube cần ≥3 mốc, mốc đầu 00:00,
    mỗi chương ≥10 s."""
    if not offsets_ms:
        return []
    ol = json.loads((db.q("SELECT outline FROM jobs WHERE id=?", (jid,))[0]["outline"]) or "{}")
    names = [c.get("title", "") for c in ol.get("chapters", [])]
    first = {}
    for s, off in zip(db.scenes(jid), offsets_ms):
        first.setdefault(s["chapter"], off)
    out = [(first[c] / 1000, names[c] if c < len(names) else f"Phần {c + 1}") for c in sorted(first)]
    if out:
        out[0] = (0.0, out[0][1])
    out = [o for i, o in enumerate(out) if i == 0 or o[0] - out[i - 1][0] >= 10]
    return out if len(out) >= 3 else []


def description(d: dict, lang: str, cfg: Cfg, chaps: list | None = None) -> str:
    y = conf(cfg)
    vi = lang == "vi"
    kp_head = "🔎 Nội dung chính:" if vi else "🔎 Key points:"
    parts = [d["hook"], d["intro"], kp_head + "\n" + "\n".join(f"• {k}" for k in d["key_points"]), d["conclusion"],
             "💬 " + d["comment_question"]]
    if chaps:
        parts.append(("⏱ Mốc thời gian:" if vi else "⏱ Chapters:") + "\n"
                     + "\n".join(f"{_ts(s)} {t}" for s, t in chaps))
    sub = y.get("subscribe_vi" if vi else "subscribe_en") or (
        "Đăng ký kênh để hiểu kinh tế, đời sống, công nghệ qua những bức vẽ dễ hiểu." if vi else
        "Subscribe for simple whiteboard explainers on money, life and technology.")
    parts.append(sub)
    parts.append(" ".join((d["hashtags"][:4] + y["channel_tags"])[:6]))
    member = y.get("member_vi" if vi else "member_en") or (
        f"Tham gia hội viên để nhận đặc quyền: youtube.com/{y['handle']}/join" if vi else
        f"Join this channel as a member to get perks: youtube.com/{y['handle']}/join")
    parts.append(member)
    parts.append(y.get("disclaimer_vi" if vi else "disclaimer_en") or (
        "- Ảnh thumbnail mang tính minh hoạ, có thể không xuất hiện y hệt trong video." if vi else
        "- Thumbnails are illustrative and may not appear exactly in the video."))
    return "\n\n".join(p for p in parts if p and p.strip())


def save(jid: int, data: dict, cfg: Cfg, offsets_ms: list | None = None):
    f = folder(jid)
    f.mkdir(parents=True, exist_ok=True)
    chaps = chapters(jid, offsets_ms)
    for lang in ("vi", "en"):
        d = data[lang]
        (f / f"title_{lang}.txt").write_text(d["titles"][0] if d["titles"] else "", encoding="utf-8")
        (f / f"titles_{lang}.txt").write_text("\n".join(d["titles"]), encoding="utf-8")
        (f / f"description_{lang}.txt").write_text(description(d, lang, cfg, chaps), encoding="utf-8")
        tags, n = [], 0
        for t in d.get("tags", []):
            if n + len(t) + 1 > 480:
                break
            tags.append(t)
            n += len(t) + 1
        d["tags"] = tags
    (f / "tags.txt").write_text(", ".join(dict.fromkeys(data["vi"]["tags"] + data["en"]["tags"])), encoding="utf-8")
    (f / "title.txt").write_text((data["vi"]["titles"] or [""])[0], encoding="utf-8")
    (f / "description.txt").write_text((f / "description_vi.txt").read_text(encoding="utf-8"), encoding="utf-8")
    (f / "youtube.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _job_srt(jid: int, srt: Path | None = None) -> Path | None:
    """File phụ đề tiếng Việt của video đã ghép (cạnh mp4, hoặc trong thư mục dự án sau khi đóng gói)."""
    cands = [srt] if srt else []
    job = db.q("SELECT output FROM jobs WHERE id=?", (jid,))
    if job and job[0].get("output"):
        cands.append(Path(job[0]["output"]).with_suffix(".srt"))
    try:
        # sau khi đóng gói: projects/<job>/video/<tên>.srt (bỏ qua chính thư mục youtube/)
        cands += sorted(p for p in packager.folder_of(jid).rglob("*.srt") if p.parent.name != "youtube")
    except KeyError:
        pass
    return next((p for p in cands if p and p.exists() and p.stat().st_size), None)


def _sent_path(jid: int) -> Path:
    return folder(jid) / "subtitles_sentences.json"


def _write_from_sents(jid: int, sents: list, en_sents: list, log=print) -> dict:
    """Cắt câu → cue hiển thị rồi ghi 2 file .srt (dùng chung cho make_subtitles và resplit)."""
    f = folder(jid)
    cs, en = subtitles.split_sentences(sents, en_sents)
    subtitles.write_srt_crlf(cs, f / "subtitles_vi.srt")
    res = {"sent": len(sents), "vi": len(cs)}
    if en_sents is not sents:
        subtitles.write_srt_crlf(en, f / "subtitles_en.srt")
        res["en"] = sum(1 for a, b in zip(cs, en) if a[2] != b[2])
        if res["en"] < len(cs):
            log(f"⚠ Còn {len(cs) - res['en']}/{len(cs)} cue phụ đề EN chưa dịch được")
    return res


def resplit(jid: int, cfg: Cfg, log=print, llm=None, srt: Path | None = None) -> dict:
    """Cắt lại cue từ CÂU ĐÃ DỊCH SẴN (subtitles_sentences.json) – không gọi lại LLM.

    Dùng khi chỉ đổi cách chia cue (độ dài dòng, số dòng). Chưa có cache → chạy make_subtitles như thường.
    """
    p = _sent_path(jid)
    if p.exists():
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
            sents = [(r["a"], r["b"], r["vi"], r.get("emph", "")) for r in rows]
            en = [(r["a"], r["b"], r.get("en") or r["vi"], r.get("emph", "")) for r in rows]
            if len(sents) == len(en) and sents:
                res = _write_from_sents(jid, sents, en, log)
                _set(jid, subs="done", subs_lines=res)
                log(f"💬 Cắt lại phụ đề job {jid} từ {len(rows)} câu đã dịch: "
                    f"{res['vi']} cue VI / {res.get('en', 0)} cue EN")
                return res
        except (ValueError, KeyError, OSError) as e:
            log(f"⚠ Cache câu phụ đề hỏng ({e}) → làm lại từ đầu")
    return make_subtitles(jid, cfg, log, llm, srt)


def make_subtitles(jid: int, cfg: Cfg, log=print, llm=None, srt: Path | None = None) -> dict:
    """youtube/subtitles_vi.srt + subtitles_en.srt (cùng thời gian với video) để tải lên mục "Phụ đề" của YouTube.

    Phụ đề nguồn bị cắt theo độ dài nên phải gộp lại thành câu trọn vẹn TRƯỚC khi dịch – dịch từng mảnh thì model
    tự gộp/viết lại, lệch chỉ số và sinh dòng lặp hoặc còn nguyên tiếng Việt.
    """
    src = _job_srt(jid, srt)
    if not src:
        return {}
    raw = subtitles.read_srt(src)
    if not raw:
        return {}
    # Đơn vị DỊCH = câu trọn vẹn (câu tiếng Việt ở đây dài ~160 ký tự; cắt ở 90 ký tự vẫn rơi giữa câu nên
    # model lại tự gộp/viết lại). Đơn vị HIỂN THỊ = cue ≤ 2 dòng, cắt lại sau khi dịch.
    sents = subtitles.merge_sentences(raw, max_chars=600, max_ms=40000)
    f = folder(jid)
    f.mkdir(parents=True, exist_ok=True)
    en_sents = sents
    if conf(cfg).get("subtitles_en", True) and llm is not None:
        _set(jid, subs="translating")
        en_sents = subtitles.translate_cues(sents, llm, log)
    res = _write_from_sents(jid, sents, en_sents, log)
    res["raw"] = len(raw)
    _sent_path(jid).write_text(json.dumps(
        [{"a": s[0], "b": s[1], "vi": s[2], "en": e[2], "emph": s[3]} for s, e in zip(sents, en_sents)],
        ensure_ascii=False, indent=1), encoding="utf-8")
    _set(jid, subs="done", subs_lines=res)
    log(f"💬 Phụ đề YouTube job {jid}: {res['vi']} cue VI / {res.get('en', 0)} cue EN "
        f"(gộp {res['raw']} mảnh → {res['sent']} câu) → {f}")
    return res


def refresh(jid: int, cfg: Cfg, offsets_ms: list | None = None):
    """Viết lại description (thêm mốc thời gian chương khi video đã ghép xong)."""
    j = folder(jid) / "youtube.json"
    if j.exists():
        save(jid, json.loads(j.read_text(encoding="utf-8")), cfg, offsets_ms)


# ------------------------------------------------------------------ thumbnail (task Flow riêng)
def thumb_uid(jid: int, k: int) -> str:
    return f"thumb_{jid}_{k}"


def make_thumbnails(jid: int, data: dict, cfg: Cfg, log=print, llm=None, timeout_min: float = 60) -> list[Path]:
    y = conf(cfg)
    f = folder(jid)
    n = max(1, int(y["thumb_variants"]))
    scene = data["en"].get("thumb_scene") or data["vi"].get("thumb_scene") or data["topic"]
    subject = data["en"].get("thumb_subject") or ""
    context = data["en"].get("thumb_context") or ""
    side = {k: "right" if k % 2 == 0 else "left" for k in range(n)}   # biến thể A: nhân vật phải, B: trái
    import datetime as _dt
    date = _dt.date.today() if y.get("thumb_date", True) else None
    texts = data["vi"].get("thumb_text") or [((data["vi"].get("titles") or [data.get("topic") or ""])[0])[:30].upper()]
    got: dict[int, Path] = {}
    tries = {k: 0 for k in range(n)}

    def send(k):
        tries[k] += 1
        flowbridge.enqueue(jid, 900 + tries[k], [{"idx": -1, "uid": thumb_uid(jid, k),
                                                  "prompt": thumbnail.bg_prompt(scene, subject, context,
                                                                                side[k])}])

    for k in range(n):
        send(k)
    log(f"📺 Thumbnail: xếp {n} ảnh nền vào Flow (song song với ảnh cảnh)")
    until = time.time() + timeout_min * 60
    while len(got) < n and time.time() < until:
        time.sleep(10)
        if not db.q("SELECT id FROM jobs WHERE id=?", (jid,)):
            log(f"📺 Job {jid} đã bị xoá – ngừng chờ thumbnail")
            flowbridge.cancel_job(jid)
            return []
        for k in range(n):
            if k in got:
                continue
            for t in db.q("SELECT * FROM flow_tasks WHERE job_id=? AND uid=? AND status IN ('done','error','cancelled') "
                          "AND (error IS NULL OR error!='used') ORDER BY id DESC LIMIT 1", (jid, thumb_uid(jid, k))):
                flowbridge.mark_used(t["id"])
                if t["status"] in ("error", "cancelled") or not t.get("image"):
                    if tries[k] < 3:
                        send(k)
                    continue
                ok, real_side = _bg_check(Path(t["image"]), llm) if llm is not None else (True, None)
                if real_side in ("left", "right"):
                    side[k] = real_side            # vị trí nhân vật thật trong ảnh → chữ đặt phía còn lại
                if not ok and tries[k] < 3:
                    log(f"📺 Ảnh nền thumbnail {k + 1} không phải ảnh thật / có chữ / méo → tạo lại")
                    send(k)
                    continue
                got[k] = Path(t["image"])
    outs = []
    for k, bg in sorted(got.items()):
        for ti, text in enumerate(texts[:3]):
            name = f"thumbnail_{chr(65 + k)}{ti + 1}.jpg"
            outs.append(thumbnail.compose(bg, text, f / name, subject_side=side.get(k), date=date))
    if not outs:
        # Dự phòng: nếu Flow chưa kịp tạo ảnh nền hoặc timeout, lấy ảnh bìa video hoặc ảnh cảnh đầu làm nền thumbnail
        fallback_bg = None
        cover = packager.folder_of(jid) / "video" / "anh_bia.jpg"
        if cover.exists():
            fallback_bg = cover
        else:
            for cand in (packager.folder_of(jid) / "anh").glob("canh_*.png"):
                fallback_bg = cand
                break
        if fallback_bg and fallback_bg.exists():
            log(f"📺 Thumbnail: dùng ảnh thay thế ({fallback_bg.name}) để đảm bảo luôn có thumbnail.jpg")
            for ti, text in enumerate(texts[:3]):
                name = f"thumbnail_A{ti + 1}.jpg"
                outs.append(thumbnail.compose(fallback_bg, text, f / name, subject_side="right", date=date))
    if outs:
        (f / "thumbnail.jpg").write_bytes(outs[0].read_bytes())
    return outs


THUMB_QC = {"type": "object", "properties": {"is_photo": {"type": "boolean"},
                                             "has_text": {"type": "boolean"}, "distorted": {"type": "boolean"},
                                             "attractive": {"type": "number"},
                                             "subject_side": {"type": "string", "enum": ["left", "right", "center"]}},
            "required": ["is_photo", "has_text", "distorted", "attractive", "subject_side"],
            "additionalProperties": False}


def _bg_check(img: Path, llm) -> tuple[bool, str | None]:
    """(đạt?, nhân vật chính ở bên nào) – vị trí dùng để đặt chữ phía còn lại, không che mặt."""
    try:
        r = llm.look(img, "Ảnh nền thumbnail YouTube. is_photo: đây có phải ẢNH CHỤP THẬT (không phải tranh vẽ, "
                          "biếm hoạ, nét vẽ, hoạt hình, 3D)? " + QC_TEXT_VI + ". distorted: người/vật "
                          "có bị méo, thừa ngón, mặt hỏng không? attractive: 0–1 mức bắt mắt. subject_side: nhân vật/"
                          "khuôn mặt chính nằm ở nửa trái, nửa phải hay giữa ảnh?", THUMB_QC)
        ok = bool(r.get("is_photo")) and not r.get("has_text") and not r.get("distorted")
        return ok, r.get("subject_side")
    except Exception:
        return True, None


def _bg_ok(img: Path, llm) -> bool:
    return _bg_check(img, llm)[0]


# ------------------------------------------------------------------ chạy song song
def run(jid: int, cfg: Cfg, log=print, llm=None):
    try:
        _set(jid, state="running", error=None)
        data = write_texts(jid, cfg, log, llm)
        log(f"📺 Bộ YouTube job {jid}: tiêu đề \"{data['vi']['titles'][0]}\" ({data['by']})")
        _set(jid, state="thumbs")
        outs = make_thumbnails(jid, data, cfg, log, llm)
        _set(jid, state="done" if outs else "partial", thumbs=len(outs),
             error=None if outs else "chưa có ảnh nền thumbnail từ Flow")
        log(f"📺 Bộ YouTube job {jid} xong: {len(outs)} thumbnail → {folder(jid)}")
        if _job_srt(jid):            # bấm "Tạo lại" khi video đã xong → làm lại cả phụ đề
            make_subtitles(jid, cfg, log, llm)
    except Exception as e:
        _set(jid, state="error", error=str(e)[:300])
        log(f"📺 Bộ YouTube job {jid} lỗi: {e}\n{traceback.format_exc()[-400:]}")


_START_LOCK = threading.Lock()


def start(jid: int, cfg: Cfg, log=print, llm=None, force=False) -> bool:
    """Chạy ở luồng phụ (không chặn workflow). Đã xong / đang chạy thì bỏ qua (trừ force)."""
    with _START_LOCK:      # worker gọi 2 lần + API force cùng lúc → chỉ 1 luồng ghi file
        t = _threads.get(jid)
        if t and t.is_alive():
            return False
        if not force and status(jid).get("state") in ("done",):
            return False
        t = threading.Thread(target=run, args=(jid, cfg, log, llm), daemon=True, name=f"youtube-{jid}")
        _threads[jid] = t
        t.start()
        return True


def wait(jid: int, seconds: float):
    t = _threads.get(jid)
    if t and t.is_alive():
        t.join(seconds)


def view(jid: int) -> dict:
    f = folder(jid)
    j = f / "youtube.json"
    data = json.loads(j.read_text(encoding="utf-8")) if j.exists() else None
    desc = {lang: (f / f"description_{lang}.txt").read_text(encoding="utf-8")
            for lang in ("vi", "en") if (f / f"description_{lang}.txt").exists()}
    thumbs = sorted(p.name for p in f.glob("thumbnail_*.jpg")) if f.exists() else []
    subs = {}
    for lang in ("vi", "en"):
        p = f / f"subtitles_{lang}.srt"
        if p.exists():
            cs = subtitles.read_srt(p)
            subs[lang] = {"file": p.name, "lines": len(cs), "preview": [c[2] for c in cs[:5]]}
    return {"status": status(jid), "folder": str(f), "data": data, "description": desc, "thumbs": thumbs, "subs": subs,
            "tags": (f / "tags.txt").read_text(encoding="utf-8") if (f / "tags.txt").exists() else ""}



def audit_and_fix_all(cfg: Cfg, log=print, llm=None) -> dict:
    """Quét tất cả các job có video hoàn thành hoặc đã đóng gói:
    Kiểm tra xem có thiếu title.txt, description.txt, thumbnail.jpg hoặc phụ đề không,
    nếu thiếu thì tự động bổ sung ngay lập tức."""
    fixed = []
    # Lấy danh sách job hoàn thành từ database hoặc thư mục projects
    jobs = db.q("SELECT id, topic, status, folder FROM jobs ORDER BY id ASC")
    for job in jobs:
        jid = job["id"]
        try:
            f = folder(jid)
        except Exception:
            continue
        # Kiểm tra xem job có video chưa (nếu chưa hoàn thành bước video thì bỏ qua)
        vid_dir = packager.folder_of(jid) / "video" if packager.folder_of(jid).exists() else None
        has_video = bool(vid_dir and list(vid_dir.glob("*.mp4")))
        if not has_video and job["status"] not in ("done", "review"):
            continue

        missing = []
        if not (f / "title.txt").exists() or not (f / "youtube.json").exists():
            missing.append("texts")
        if not (f / "thumbnail.jpg").exists():
            missing.append("thumbnail")
        if not (f / "subtitles_vi.srt").exists() or not (f / "subtitles_en.srt").exists():
            missing.append("subtitles")

        if missing:
            log(f"🩺 Phát hiện job {jid} ({job['topic'][:40]}...) thiếu: {', '.join(missing)} → Đang bổ sung...")
            # 1. Bổ sung texts nếu thiếu
            data = None
            if (f / "youtube.json").exists():
                try:
                    data = json.loads((f / "youtube.json").read_text(encoding="utf-8"))
                except Exception:
                    data = None
            if not data or "texts" in missing:
                # Thử tìm kết quả từ nb_tasks cũ trước khi gọi LLM
                old_nb = db.q("SELECT result FROM nb_tasks WHERE job_id=? AND step='youtube' AND status='done' ORDER BY id DESC LIMIT 1", (jid,))
                if old_nb and old_nb[0]["result"]:
                    try:
                        data = notebook._json_block(old_nb[0]["result"])
                    except Exception:
                        data = _parse_text_output(old_nb[0]["result"])
                if not data:
                    data = write_texts(jid, cfg, log, llm)
                else:
                    data["by"] = "notebooklm"
                    data["topic"] = job["topic"]
                    save(jid, data, cfg)

            # 2. Bổ sung thumbnail nếu thiếu
            if "thumbnail" in missing or not (f / "thumbnail.jpg").exists():
                if data:
                    make_thumbnails(jid, data, cfg, log, llm, timeout_min=0.1)

            # 3. Bổ sung subtitles nếu thiếu
            if "subtitles" in missing or not (f / "subtitles_vi.srt").exists():
                try:
                    make_subtitles(jid, cfg, log, llm)
                except Exception as e:
                    log(f"⚠ Bổ sung phụ đề job {jid} lỗi: {e}")

            fixed.append({"id": jid, "topic": job["topic"], "fixed": missing})
            _set(jid, state="done")

    log(f"🩺 Hoàn tất kiểm tra: đã bổ sung hoàn thiện {len(fixed)} job!")
    return {"ok": True, "count": len(fixed), "fixed": fixed}
