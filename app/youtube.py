"""Bộ đăng YouTube cho mỗi job: Title + Description (song ngữ VI/EN, chuẩn SEO, khung giống các kênh mẫu) do
NotebookLM viết (Qwen dự phòng) + Thumbnail (nền Google Flow, chữ in bằng Pillow). Ghi THẲNG vào thư mục dự án
của job: projects/<id>_<tên>/youtube/. Chạy ở luồng phụ, song song với các bước làm video."""
import json
import re
import threading
import time
import traceback
from pathlib import Path

from . import db, flowbridge, notebook, packager, thumbnail
from .config import Cfg

SCHEMA_KEYS = ("titles", "thumb_text", "thumb_scene", "hook", "intro", "key_points", "conclusion",
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
            "Mỗi object gồm: titles (5 tiêu đề, mỗi cái ≤ 70 ký tự); thumb_text (3 phương án chữ trên thumbnail, "
            "2–5 từ, VIẾT HOA, kết bằng ? hoặc !); thumb_scene (tiếng Anh: cảnh ẢNH CHỤP THẬT kiểu báo chí cho thumbnail – "
            "người/vật thật, chủ thể to, biểu cảm mạnh, không chữ, tuyệt đối không phải tranh vẽ); hook (1–2 câu hỏi mở đầu ≤ 160 ký tự); intro (đoạn 'Trong video này…' 2 câu); "
            "key_points (5 câu hỏi ngắn); conclusion (1–2 câu kiểu 'không có lựa chọn đúng cho tất cả…'); "
            "comment_question (câu hỏi rủ bình luận); hashtags (5 hashtag chủ đề, không dấu, có #); "
            "tags (8 thẻ tìm kiếm).\n" + lang + "\n")
    tail = f"Chỉ trả JSON (không khối code) giữa hai dòng {notebook.START} và {notebook.END}."
    return (head + tail)[: notebook.MAX_PROMPT]


QWEN_SCHEMA_ONE = {"type": "object", "properties": {
    "titles": {"type": "array", "items": {"type": "string"}},
    "thumb_text": {"type": "array", "items": {"type": "string"}},
    "thumb_scene": {"type": "string"}, "hook": {"type": "string"}, "intro": {"type": "string"},
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
    out["thumb_text"] = [t.upper() for t in out["thumb_text"] if 1 <= len(t.split()) <= 7][:3]
    out["hashtags"] = ["#" + re.sub(r"[^\w]", "", h.lstrip("#")) for h in out["hashtags"] if h.strip("#")][:6]
    return out


def write_texts(jid: int, cfg: Cfg, log=print, llm=None) -> dict:
    job = db.q("SELECT * FROM jobs WHERE id=?", (jid,))[0]
    ol = json.loads(job.get("outline") or "{}")
    data, by = None, "notebooklm"
    try:
        txt = notebook.ask(jid, "youtube", _prompt(job, ol, cfg), time.time() + 25 * 60, lambda: None)
        raw = notebook._json_block(txt)
        if raw.get("vi") and raw.get("en"):
            data = {"vi": _clean(raw["vi"]), "en": _clean(raw["en"])}
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
    texts = data["vi"]["thumb_text"] or [data["vi"]["titles"][0][:30].upper()]
    got: dict[int, Path] = {}
    tries = {k: 0 for k in range(n)}

    def send(k):
        tries[k] += 1
        flowbridge.enqueue(jid, 900 + tries[k], [{"idx": -1, "uid": thumb_uid(jid, k),
                                                  "prompt": thumbnail.bg_prompt(scene)}])

    for k in range(n):
        send(k)
    log(f"📺 Thumbnail: xếp {n} ảnh nền vào Flow (song song với ảnh cảnh)")
    until = time.time() + timeout_min * 60
    while len(got) < n and time.time() < until:
        time.sleep(10)
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
                if llm is not None and not _bg_ok(Path(t["image"]), llm) and tries[k] < 3:
                    log(f"📺 Ảnh nền thumbnail {k + 1} không phải ảnh thật / có chữ / méo → tạo lại")
                    send(k)
                    continue
                got[k] = Path(t["image"])
    outs = []
    for k, bg in sorted(got.items()):
        for ti, text in enumerate(texts[:3]):
            name = f"thumbnail_{chr(65 + k)}{ti + 1}.jpg"
            outs.append(thumbnail.compose(bg, text, f / name))
    if outs:
        (f / "thumbnail.jpg").write_bytes(outs[0].read_bytes())
    return outs


THUMB_QC = {"type": "object", "properties": {"is_photo": {"type": "boolean"},
                                             "has_text": {"type": "boolean"}, "distorted": {"type": "boolean"},
                                             "attractive": {"type": "number"}},
            "required": ["is_photo", "has_text", "distorted", "attractive"], "additionalProperties": False}


def _bg_ok(img: Path, llm) -> bool:
    try:
        r = llm.look(img, "Ảnh nền thumbnail YouTube. is_photo: đây có phải ẢNH CHỤP THẬT (không phải tranh vẽ, "
                          "biếm hoạ, nét vẽ, hoạt hình, 3D)? has_text: có chữ/ký tự/logo nào không? distorted: người/vật "
                          "có bị méo, thừa ngón, mặt hỏng không? attractive: 0–1 mức bắt mắt.", THUMB_QC)
        return bool(r.get("is_photo")) and not r.get("has_text") and not r.get("distorted")
    except Exception:
        return True


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
    except Exception as e:
        _set(jid, state="error", error=str(e)[:300])
        log(f"📺 Bộ YouTube job {jid} lỗi: {e}\n{traceback.format_exc()[-400:]}")


def start(jid: int, cfg: Cfg, log=print, llm=None, force=False) -> bool:
    """Chạy ở luồng phụ (không chặn workflow). Đã xong / đang chạy thì bỏ qua (trừ force)."""
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
    return {"status": status(jid), "folder": str(f), "data": data, "description": desc, "thumbs": thumbs,
            "tags": (f / "tags.txt").read_text(encoding="utf-8") if (f / "tags.txt").exists() else ""}
