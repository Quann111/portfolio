import difflib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from . import db
from .config import ROOT, Cfg
from .llm import NO_WINDOW

L30 = ROOT / "skills" / "last30days"
OUT = ROOT / "data" / "topic_scout"
_SCOUT_LOCK = threading.Lock()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
FEEDS = [
    "https://news.google.com/rss/headlines/section/topic/WORLD?hl=vi&gl=VN&ceid=VN:vi",
    "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss?hl=vi&gl=VN&ceid=VN:vi",
    "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=vi&gl=VN&ceid=VN:vi",
]

DEFAULT_BANNED = [
    "việt nam", "viet nam", "vietnam", "vietnamese", "hà nội", "hanoi", "sài gòn", "saigon",
    "hồ chí minh", "ho chi minh", "điện biên", "dien bien", "mậu thân", "tet offensive",
    "biển đông", "hoàng sa", "trường sa", "paracel", "spratly", "đảng cộng sản việt",
    "quốc hội việt", "việt cộng", "viet cong", "vnch", "việt nam cộng hòa",
    "chiến tranh đông dương", "indochina war", "khe sanh", "1979 biên giới",
    "sino-vietnamese", "mỹ lai", "my lai", "võ nguyên giáp", "giap"
]

CAND_SCHEMA = {
    "type": "object",
    "properties": {"candidates": {"type": "array", "minItems": 12, "maxItems": 24, "items": {
        "type": "object",
        "properties": {"topic": {"type": "string"}, "query_en": {"type": "string"}, "niche": {"type": "string"},
                       "kind": {"type": "string", "enum": ["news", "evergreen"]},
                       "angle": {"type": "string"}, "appeal": {"type": "number"}},
        "required": ["topic", "query_en", "niche", "kind", "angle", "appeal"]}}},
    "required": ["candidates"],
}


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", s).replace("đ", "d").replace("Đ", "d")


def is_vietnam(text_or_cand, banned_keywords: list[str] | None = None) -> bool:
    banned = banned_keywords or DEFAULT_BANNED
    if isinstance(text_or_cand, dict):
        text = f"{text_or_cand.get('topic', '')} {text_or_cand.get('angle', '')} {text_or_cand.get('query_en', '')}"
    else:
        text = str(text_or_cand or "")
    text_lower = text.lower()
    text_no_accents = _strip_accents(text_lower)
    for b in banned:
        b_lower = b.lower().strip()
        if not b_lower:
            continue
        if b_lower in text_lower or _strip_accents(b_lower) in text_no_accents:
            return True
    return False


def acfg(cfg: Cfg) -> dict:
    a = dict(cfg.get("auto_topics") or {})
    a.setdefault("enabled", True)
    a.setdefault("engine", "notebooklm")
    a.setdefault("nb_wait_minutes", 15)
    a.setdefault("batch", 5)
    a.setdefault("min_queue", 1)
    a.setdefault("last30days", True)
    if a["last30days"] and not (shutil.which("uv") and shutil.which("yt-dlp")):
        a["last30days"] = False          # thiếu công cụ → bỏ phần đo độ hot, vẫn tìm chủ đề bình thường
    a.setdefault("niches", ["chiến tranh – quân sự", "địa chính trị – thời sự", "lịch sử – bí ẩn", "giải trí – người nổi tiếng"])
    a.setdefault("niche_weights", {"chiến tranh – quân sự": 4, "địa chính trị – thời sự": 3, "lịch sử – bí ẩn": 2, "giải trí – người nổi tiếng": 1})
    a.setdefault("banned_keywords", DEFAULT_BANNED)
    a.setdefault("reference_channels", [])
    kv_enabled = db.kv_get("auto_topics_enabled")
    if kv_enabled is not None:
        a["enabled"] = bool(kv_enabled)
    return a


def headlines(limit: int = 40, banned_keywords: list[str] | None = None) -> list[str]:
    out = []
    for url in FEEDS:
        try:
            root = ET.fromstring(requests.get(url, headers=UA, timeout=20).content)
            for it in root.findall(".//item")[:12]:
                title = (it.findtext("title") or "").rsplit(" - ", 1)[0].strip()
                if title and not is_vietnam(title, banned_keywords):
                    out.append(title)
        except Exception:
            continue
    return list(dict.fromkeys(t for t in out if t))[:limit]


def youtube_heat(query: str, timeout: int = 120) -> dict:
    """Độ hot 30 ngày trên YouTube (last30days, nguồn youtube – không cần key, cần yt-dlp). Lỗi → heat 0."""
    if not (L30 / "skills" / "last30days" / "scripts" / "last30days.py").exists():
        return {"heat": 0.0, "videos": 0, "note": "thiếu skills/last30days"}
    try:
        r = subprocess.run(["uv", "run", "--python", "3.12", "python", "skills/last30days/scripts/last30days.py",
                            query, "--quick", "--emit=json", "--auto-resolve", "--search", "youtube"],
                           cwd=str(L30), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=NO_WINDOW,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "LAST30DAYS_ALLOW_ENGINE_PLAN": "1"})
        d = json.loads(r.stdout[r.stdout.find("{"):])
    except Exception as e:
        return {"heat": 0.0, "videos": 0, "note": str(e)[:120]}
    vids = [x for x in d.get("results", []) if x.get("source") == "youtube"]
    now = time.time()
    heat = 0.0
    for v in vids:
        e = v.get("engagement") or {}
        views = float(e.get("views") or 0)
        try:
            age = (now - datetime.fromisoformat(v["published_at"][:10]).timestamp()) / 86400
        except Exception:
            age = 365
        heat += math.log10(views + 1) * (1.0 if age <= 60 else 0.5 if age <= 365 else 0.25)
    return {"heat": round(heat, 2), "videos": len(vids),
            "top": [{"title": v.get("title", "")[:90], "views": (v.get("engagement") or {}).get("views")}
                    for v in vids[:3]]}


def _channels(a: dict) -> list[dict]:
    out = []
    for c in a["reference_channels"]:
        out.append(c if isinstance(c, dict) else {"name": str(c), "url": ""})
    return out


def reference_videos(a: dict, per_channel: int = 15, cache_hours: float = 24) -> list[dict]:
    """Video gần đây của các kênh vẽ tay tham khảo – chỉ lấy video 'vượt trội'."""
    cache = OUT / "reference_videos.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < cache_hours * 3600:
        return json.loads(cache.read_text(encoding="utf-8"))

    def one(ch):
        if not ch.get("url"):
            return []
        url = ch["url"].rstrip("/")
        if not url.endswith("/videos"):
            url += "/videos"
        try:
            r = subprocess.run(["yt-dlp", "--flat-playlist", "-J", "--playlist-end", str(per_channel), url],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                               creationflags=NO_WINDOW)
            vids = [{"channel": ch["name"], "title": e.get("title", ""), "views": e.get("view_count") or 0}
                    for e in json.loads(r.stdout).get("entries", []) if e.get("title")]
        except Exception:
            return []
        views = sorted(v["views"] for v in vids if v["views"])
        med = views[len(views) // 2] if views else 0
        for v in vids:
            v["ratio"] = round(v["views"] / med, 2) if med else 0
        return [v for v in vids if v["ratio"] >= 1.2]

    with ThreadPoolExecutor(max_workers=4) as ex:
        out = [v for vs in ex.map(one, _channels(a)) for v in vs]
    out.sort(key=lambda v: -v["ratio"])
    OUT.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


_LEAD = re.compile(r"^(tại sao|vì sao|vì đâu|điều gì|chuyện gì|bí mật|sự thật|làm sao|làm thế nào|có phải)\s+",
                   re.I)


def _core(t: str) -> str:
    """Bỏ cụm mở đầu chung ("Tại sao…") và dấu câu."""
    t = re.sub(r"[?!.:,\"'‘’“”]", " ", t.lower()).strip()
    return re.sub(r"\s+", " ", _LEAD.sub("", t))


def _bigrams(t: str) -> set:
    """Cặp âm tiết liền nhau."""
    w = re.findall(r'\w+', t)
    return set(zip(w, w[1:]))


def _similar(a: str, done: list[str]) -> bool:
    a_core = _core(a)
    if not a_core:
        return False
    a_clean = re.sub(r'[\-–—\s]+', ' ', a_core).strip()
    a_bi = _bigrams(a_clean)
    for b in done:
        b_core = _core(b)
        if not b_core:
            continue
        b_clean = re.sub(r'[\-–—\s]+', ' ', b_core).strip()
        if a_clean in b_clean or b_clean in a_clean:
            return True
        if difflib.SequenceMatcher(None, a_core, b_core).ratio() > 0.55:
            return True
        b_bi = _bigrams(b_clean)
        common = a_bi & b_bi
        if len(common) >= 3 and len(common) >= 0.50 * min(len(a_bi), len(b_bi)):
            return True
    return False


def _gen_qwen(prompt: str, schema: dict, llm) -> list[dict]:
    res = llm.chat([{"role": "user", "content": prompt}], schema, temperature=0.9, max_tokens=5000)
    return res.get("candidates") or []


def _gen_notebooklm(cfg: Cfg, log, check, news: list[str], done: list[str], query: str | None = None, niche: str | None = None) -> list[dict]:
    from . import notebook

    if not notebook.connected():
        raise notebook.Unavailable("Notebook Bridge chưa kết nối – mở Chrome profile NotebookLM")
    if notebook.paused():
        raise notebook.Unavailable(f"Notebook Bridge tạm dừng: {notebook.paused()}")

    a = acfg(cfg)
    valid_niches = a.get("niches") or [
        "chiến tranh – quân sự", "địa chính trị – thời sự", "lịch sử – bí ẩn", "giải trí – người nổi tiếng"
    ]

    if query:
        body = f"Đề xuất 20 góc nhìn khác nhau xoay quanh: '{query}'. Khai thác lịch sử, chiến thuật, góc khuất, bí ẩn. Không lặp góc nhìn."
    elif niche:
        body = f"Đề xuất 20 chủ đề thuộc đúng ngách '{niche}'. Nửa bám tin tức quốc tế gần đây, nửa câu chuyện chuyên sâu (evergreen)."
    else:
        body = "Đề xuất 20 chủ đề theo 4 ngách: ~8 quân sự, ~6 địa chính trị, ~4 lịch sử bí ẩn, ~2 người nổi tiếng. Nửa bám tin nóng, nửa evergreen."

    # Keep headlines concise (up to 12 items, max 70 chars each)
    news_lines = [f"- {h[:70]}" for h in news[:12]]
    news_txt = "\n".join(news_lines) if news_lines else "- (không có tin)"

    # Keep done list to recent ~20 items, max 50 chars each
    done_items = [f'"{t[:50]}"' for t in done[-20:]]
    done_txt = ", ".join(done_items) if done_items else "(chưa có)"

    prompt = (
        "Kênh YouTube hoạt hình bảng trắng tiếng Việt (15–20p/video).\n"
        f"{body}\n\n"
        f"Tin nóng thế giới tham khảo:\n{news_txt}\n\n"
        f"Đã làm (KHÔNG LẶP LẠI):\n{done_txt}\n\n"
        "NGUYÊN TẮC BẮT BUỘC:\n"
        "1. CẤM TUYỆT ĐỐI MỌI CHỦ ĐỀ VỀ VIỆT NAM (chính trị, lãnh đạo, lịch sử VN, các cuộc chiến có VN, nhân vật/xã hội VN). Tiêu đề tiếng Việt nhưng 100% nội dung ngoài VN.\n"
        "2. CHỈ ĐỀ XUẤT CHỦ ĐỀ CÓ THẬT, KIỂM CHỨNG ĐƯỢC. Không bịa đặt, không hỏi sai sự thật. Bắt buộc kèm 1 câu 'fact' sự thật nền tảng.\n"
        "3. Quân sự/chiến tranh: trung lập, chiến thuật, không cổ vũ bạo lực.\n"
        "4. Người nổi tiếng: chỉ cột mốc sự nghiệp thật, không drama đời tư.\n"
        "5. Mỗi chủ đề là 1 sự kiện/nhân vật khác nhau.\n\n"
        "Trả về ĐÚNG MỘT khối JSON giữa ===BẮT ĐẦU=== và ===KẾT THÚC===:\n"
        "===BẮT ĐẦU===\n"
        '{"candidates":[{"topic":"Tiêu đề tiếng Việt tò mò (câu hỏi/khẳng định, <=12 từ)","query_en":"3-6 từ tiếng Anh tìm kiếm","niche":"chiến tranh – quân sự|địa chính trị – thời sự|lịch sử – bí ẩn|giải trí – người nổi tiếng","kind":"news|evergreen","angle":"1 câu góc nhìn phân tích","appeal":8,"fact":"1 câu sự thật nền tảng để kiểm chứng"}]}\n'
        "===KẾT THÚC==="
    )

    assert len(prompt) <= notebook.MAX_PROMPT, f"Prompt vượt giới hạn NotebookLM ({len(prompt)} > {notebook.MAX_PROMPT})"

    wait_mins = float(a.get("nb_wait_minutes", 15))
    deadline = time.time() + wait_mins * 60

    log(f"  📓 Đang gửi yêu cầu tìm 20 ứng viên qua NotebookLM (chờ tối đa {int(wait_mins)} phút)...")
    parsed = None
    try:
        ans_text = notebook.ask(0, "topic_scout", prompt, deadline, check)
        try:
            parsed = notebook._json_block(ans_text)
        except Exception as e:
            log(f"  ⚠ JSON lần 1 từ NotebookLM bị lỗi ({e}), đang yêu cầu sửa lại...")
            retry_prompt = (
                "Câu trả lời trước không đúng định dạng JSON hợp lệ hoặc thiếu ngoặc. "
                "YÊU CẦU: Chỉ trả về duy nhất một khối JSON hợp lệ nằm giữa ===BẮT ĐẦU=== và ===KẾT THÚC=== theo đúng schema candidates."
            )
            ans_text_retry = notebook.ask(0, "topic_scout", retry_prompt, time.time() + 300, check)
            parsed = notebook._json_block(ans_text_retry)
    finally:
        # Giải phóng slot của jid=0 ngay lập tức sau khi xong (hoặc lỗi), không giữ slot
        notebook.release(0)

    raw_list = parsed.get("candidates") if isinstance(parsed, dict) else []
    if not isinstance(raw_list, list):
        raise ValueError("NotebookLM không trả về danh sách candidates")

    valid_cands = []
    for it in raw_list:
        if not isinstance(it, dict):
            continue
        top = str(it.get("topic") or "").strip().strip('"')
        if not top:
            continue
        n_val = str(it.get("niche") or "").strip()
        if n_val not in valid_niches:
            matched_n = next((vn for vn in valid_niches if vn in n_val or n_val in vn), None)
            if matched_n:
                n_val = matched_n
            elif niche and niche in valid_niches:
                n_val = niche
            else:
                continue

        kind_val = str(it.get("kind") or "evergreen").strip().lower()
        if kind_val not in ("news", "evergreen"):
            kind_val = "evergreen"

        try:
            appeal_val = float(it.get("appeal", 7))
            appeal_val = max(0.0, min(10.0, appeal_val))
        except (ValueError, TypeError):
            appeal_val = 7.0

        valid_cands.append({
            "topic": top,
            "query_en": str(it.get("query_en") or "").strip(),
            "niche": n_val,
            "kind": kind_val,
            "angle": str(it.get("angle") or "").strip(),
            "appeal": appeal_val,
            "fact": str(it.get("fact") or "").strip(),
        })

    return valid_cands


def candidates(cfg: Cfg, llm, log, query: str | None = None, niche: str | None = None, extra_done: list[str] | None = None, check=None) -> tuple[list[dict], list[str], list[str]]:
    a = acfg(cfg)
    done = [j["topic"] for j in db.jobs()]
    deleted = db.kv_get("deleted_topics") or []
    if extra_done:
        done = done + extra_done
    done = list(dict.fromkeys(done + deleted))

    banned = a.get("banned_keywords") or DEFAULT_BANNED
    all_news = headlines(limit=40, banned_keywords=banned)
    if query:
        q_lower = query.lower()
        matched = [h for h in all_news if any(w in h.lower() for w in q_lower.split())]
        news = matched if matched else all_news
    else:
        news = all_news

    engine = (a.get("engine") or "notebooklm").strip().lower()
    query_log = f" theo từ khoá '{query}'" if query else ""
    niche_log = f" trong ngách '{niche}'" if niche else ""
    log(f"🔎 Tìm chủ đề mới ({engine}){query_log}{niche_log}: {len(news)} tin nóng")

    if check is None:
        check = lambda: None

    if engine == "notebooklm":
        raw = _gen_notebooklm(cfg, log, check, news, done, query=query, niche=niche)
    else:
        refs = reference_videos(a)
        ref_txt = "\n".join(f"- [{v['channel']}] {v['title']} ({v['views']:,} views, {v['ratio']}× usual)"
                            for v in refs[:30])

        schema = json.loads(json.dumps(CAND_SCHEMA))
        if niche:
            schema["properties"]["candidates"]["items"]["properties"]["niche"] = {"type": "string", "enum": [niche]}
        elif a["niches"]:
            schema["properties"]["candidates"]["items"]["properties"]["niche"] = {"type": "string", "enum": a["niches"]}

        if query:
            body_instruction = (
                f"All 18 proposed candidates MUST be centered around the keyword or subject: '{query}'. "
                "Propose 18 DIFFERENT, unique angles or sub-topics exploring its history, mechanisms, financial impacts, "
                f"battles, mysteries, or global significance. Do not repeat the same angle.\n"
            )
        elif niche:
            body_instruction = (
                f"Propose 18 DIFFERENT video topics strictly belonging to the niche '{niche}'. "
                "About half hooked to a recent international event/headline, half evergreen depth stories.\n"
            )
        else:
            body_instruction = (
                "Propose 18 DIFFERENT video topics distributed across the 4 prioritized niches proportionally: "
                "~7 for 'chiến tranh – quân sự', ~5 for 'địa chính trị – thời sự', ~4 for 'lịch sử – bí ẩn', and ~2 for 'giải trí – người nổi tiếng'. "
                "About half hooked to a current headline (explain the story behind it), half evergreen from the niches (kind = news / evergreen).\n"
            )

        prompt = (
            f"Today is {datetime.now():%d/%m/%Y}. You plan topics for a Vietnamese YouTube channel of long hand-drawn "
            f"whiteboard explainer videos ({cfg.target_minutes} minutes).\n"
            "The channel focuses EXCLUSIVELY on 4 prioritized niches in this order of priority:\n"
            "1. 'chiến tranh – quân sự': battles, military hardware, tactics, strategy, historic campaigns, conflict maps.\n"
            "2. 'địa chính trị – thời sự': why conflict X happens, who gains and who loses, international power shifts, resources.\n"
            "3. 'lịch sử – bí ẩn': mysterious historical events, fallen empires, unsolved historical enigmas, ancient secrets.\n"
            "4. 'giải trí – người nổi tiếng': true career journeys, triumphs and downfalls of global prominent icons (entertainment, tech, visionary figures).\n\n"
            f"Proven formats from reference channels:\n{ref_txt}\n\n"
            f"Today's hot headlines:\n- " + "\n- ".join(news[:35]) + "\n\n"
            f"Already made (do NOT repeat or rephrase): {done[-60:]}\n\n"
            f"{body_instruction}\n"
            "STRICT CONTENT RULES:\n"
            "- NEVER ANY TOPIC ABOUT VIETNAM IN ANY FORM: No Vietnamese politics, leaders, Communist Party, government officials, "
            "Vietnamese history, ANY war involving Vietnam (Indochina wars, Vietnam War, 1979 border war, South China Sea / Biển Đông disputes involving Vietnam), "
            "Vietnamese historical figures, Vietnamese celebrities, Vietnamese society. All subjects must be completely outside Vietnam. "
            "The video titles and narrations are in Vietnamese, but the subject matters must be 100% international / world.\n"
            "- WAR / MILITARY RULE: Must be neutral, explanatory, analytical. No glorification of violence, no gore/grotesque details, no fabricated casualties.\n"
            "- CELEBRITY / ENTERTAINMENT RULE: Focus only on verified career trajectory and milestones. NO personal gossip, private domestic tragedies, or malicious rumors.\n"
            "- NO religion, partisan comparison of living politicians, conspiracy theories, or clickbait lies.\n"
            "- Each candidate must be a DIFFERENT subject (never two about the same weapon, battle, celebrity, or event).\n"
            "- Format: topic = punchy Vietnamese title that creates curiosity (question or surprising claim, max 12 words); "
            "query_en = 3-6 English search words for YouTube; niche = exact niche name; angle = one-sentence hook/insight; "
            "appeal 0-10 = audience interest rating."
        )

        raw = _gen_qwen(prompt, schema, llm)

    # Filter out Vietnam-related topics immediately
    non_vn = []
    vn_count = 0
    for c in raw:
        if not c.get("topic", "").strip():
            continue
        if is_vietnam(c, banned):
            vn_count += 1
            log(f"  [Lọc] Loại '{c.get('topic')}' vì liên quan Việt Nam")
        else:
            non_vn.append(c)

    if vn_count > 0:
        log(f"  Đã loại {vn_count} ứng viên vì dính từ khoá Việt Nam")

    # Extra safety pass on topic text
    valid_cands = [c for c in non_vn if not is_vietnam(c["topic"], banned)]
    extra_vn = len(non_vn) - len(valid_cands)
    if extra_vn > 0:
        log(f"  Đã loại thêm {extra_vn} ứng viên sau kiểm tra tiêu đề tiếng Việt")

    cands = [c for c in valid_cands if not _similar(c["topic"], done)]
    log(f"  Đề xuất {len(raw)} ứng viên, còn {len(cands)} ứng viên hợp lệ sau khi lọc VN và trùng lặp")

    if a["last30days"]:
        log(f"  Đo độ hot YouTube 30 ngày cho {len(cands)} ứng viên (last30days)…")
        with ThreadPoolExecutor(max_workers=3) as ex:
            heats = list(ex.map(lambda c: youtube_heat(c["query_en"]), cands))
    else:
        heats = [{"heat": 0.0, "videos": 0}] * len(cands)

    top_heat = max([h["heat"] for h in heats] + [1.0])
    weights = a.get("niche_weights") or {}
    for c, h in zip(cands, heats):
        c["trend"] = h
        base_score = 0.6 * min(10, c["appeal"]) / 10 + 0.4 * h["heat"] / top_heat
        w = float(weights.get(c.get("niche"), 1))
        coeff = 1.0 + 0.05 * max(0.0, w - 1.0)
        c["score"] = round(base_score * coeff, 3)

    cands.sort(key=lambda c: -c["score"])
    return cands, news, done


def select(cands: list[dict], n: int, done: list[str], max_news: int | None = None, is_query: bool = False, niche: str | None = None, niche_weights: dict | None = None) -> list[dict]:
    picked = []
    weights = niche_weights or {
        "chiến tranh – quân sự": 4,
        "địa chính trị – thời sự": 3,
        "lịch sử – bí ẩn": 2,
        "giải trí – người nổi tiếng": 1
    }

    for c in cands:
        if len(picked) >= n:
            break
        c_niche = c.get("niche", "")
        if not is_query and not niche:
            if max_news is not None and c.get("kind") == "news" and sum(p.get("kind") == "news" for p in picked) >= max_news:
                continue
            if n <= 10:
                cap_map = {
                    "chiến tranh – quân sự": 4,
                    "địa chính trị – thời sự": 3,
                    "lịch sử – bí ẩn": 3,
                    "giải trí – người nổi tiếng": 2
                }
                cap = cap_map.get(c_niche, max(2, (n + 3) // 4))
            else:
                total_w = sum(weights.values()) or 10
                cap = max(2, int(round(n * weights.get(c_niche, 2) / total_w)))
            if sum(p.get("niche") == c_niche for p in picked) >= cap:
                continue
            words = set(re.findall(r"[a-z0-9]{4,}", c.get("query_en", "").lower()))
            if any(len(words & set(re.findall(r"[a-z0-9]{4,}", p.get("query_en", "").lower()))) >= 2 for p in picked):
                continue
        if not _similar(c["topic"], done + [p["topic"] for p in picked]):
            picked.append(c)

    if len(picked) < n:
        for c in cands:
            if len(picked) >= n:
                break
            if c in picked:
                continue
            if not is_query and not niche:
                words = set(re.findall(r"[a-z0-9]{4,}", c.get("query_en", "").lower()))
                if any(len(words & set(re.findall(r"[a-z0-9]{4,}", p.get("query_en", "").lower()))) >= 2 for p in picked):
                    continue
            if not _similar(c["topic"], done + [p["topic"] for p in picked]):
                picked.append(c)

    return picked


def scout(cfg: Cfg, llm, log, n: int | None = None, check=None) -> list[str]:
    a = acfg(cfg)
    engine = (a.get("engine") or "notebooklm").strip().lower()

    if engine == "notebooklm":
        from . import notebook
        if not notebook.connected() or notebook.paused():
            log("NotebookLM chưa sẵn sàng – bỏ qua lượt tự tìm chủ đề")
            db.kv_set("topic_scout_last", time.time())
            return []

    if not _SCOUT_LOCK.acquire(blocking=False):
        raise RuntimeError("Đang tìm chủ đề, đợi xong lượt trước")
    try:
        n = n or int(a["batch"])
        try:
            cands, news, done = candidates(cfg, llm, log, query=None, niche=None, check=check)
        except Exception as e:
            db.kv_set("topic_scout_last", time.time())
            from . import notebook
            notebook.cancel_job(0)
            log(f"  ⚠ Tìm chủ đề qua NotebookLM không thành công ({e}) – bỏ qua lượt này để làm tiếp job")
            return []
        max_news = max(int(a.get("max_news", 4)), n // 2)
        picked = select(cands, n, done, max_news=max_news, is_query=False, niche_weights=a.get("niche_weights"))
        # B3: chỉ giữ chủ đề thuộc series (auto_topics.focus – do người dùng tự đặt, app không tự bịa)
        focus = [str(f).strip().lower() for f in (a.get("focus") or []) if str(f).strip()]
        if focus:
            inside = [p for p in picked if any(f in p["topic"].lower() for f in focus)]
            if len(inside) < len(picked):
                log(f"  Lọc theo series {focus}: giữ {len(inside)}/{len(picked)} chủ đề")
            if len(inside) < 0.7 * len(picked):
                log("  ⚠ Dưới 70% chủ đề thuộc series – nên sửa auto_topics.focus hoặc thêm chủ đề thủ công")
            picked = inside
        for c in picked:
            db.add_topic(c["topic"].strip().strip('"'))
            log(f"  + [{round(c['score'] * 100)}đ] {c['topic']}  ({c.get('kind')} · hấp dẫn {c['appeal']:.0f}/10 · hot {c['trend']['heat']:.1f} · {c['niche']})")
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{datetime.now():%Y%m%d-%H%M}.json").write_text(
            json.dumps({"headlines": news, "candidates": cands, "picked": [p["topic"] for p in picked]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        db.kv_set("topic_scout_last", time.time())
        return [p["topic"] for p in picked]
    finally:
        _SCOUT_LOCK.release()


def suggest(cfg: Cfg, llm, log, n: int = 10, query: str | None = None, niche: str | None = None, check=None) -> list[dict]:
    if not _SCOUT_LOCK.acquire(blocking=False):
        raise RuntimeError("Đang tìm chủ đề, đợi xong lượt trước")
    try:
        a = acfg(cfg)
        banned = a.get("banned_keywords") or DEFAULT_BANNED
        if query and is_vietnam(query, banned):
            raise ValueError("Kênh không làm chủ đề về Việt Nam")

        engine = (a.get("engine") or "notebooklm").strip().lower()
        max_rounds = 2 if engine == "notebooklm" else 3

        is_query = bool(query and query.strip())
        max_news = None if is_query else max(int(a.get("max_news", 4)), n // 2)

        collected_picked = []
        extra_done = []
        all_cands = []
        news_record = []

        for round_idx in range(max_rounds):
            cands, news, done = candidates(cfg, llm, log, query=query, niche=niche, extra_done=extra_done, check=check)
            news_record = news
            all_cands.extend(cands)
            round_picked = select(cands, n - len(collected_picked), done + [p["topic"] for p in collected_picked],
                                  max_news=max_news, is_query=is_query, niche=niche, niche_weights=a.get("niche_weights"))
            collected_picked.extend(round_picked)
            extra_done.extend([p["topic"] for p in round_picked])
            if len(collected_picked) >= n:
                break
            log(f"  Lượt {round_idx + 1}: được {len(collected_picked)}/{n} chủ đề, tìm thêm lượt tiếp theo...")

        if len(collected_picked) < n:
            log(f"  ⚠ chỉ tìm được {len(collected_picked)}/{n} chủ đề phù hợp")

        items = []
        for c in collected_picked[:n]:
            items.append({
                "topic": c["topic"].strip().strip('"'),
                "kind": c.get("kind", "evergreen"),
                "niche": c.get("niche", ""),
                "angle": c.get("angle", ""),
                "appeal": c.get("appeal", 0),
                "heat": round((c.get("trend") or {}).get("heat", 0.0), 2),
                "score": round(c.get("score", 0.0), 3),
                "fact": c.get("fact", ""),
            })
            log(f"  💡 Gợi ý: [{round(c['score'] * 100)}đ] {c['topic']} ({c.get('kind')} · {c.get('niche')} · hot {c.get('trend', {}).get('heat', 0):.1f})")

        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{datetime.now():%Y%m%d-%H%M}_suggest.json").write_text(
            json.dumps({"query": query, "niche": niche, "headlines": news_record, "candidates": all_cands, "picked": items},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        db.kv_set("topic_suggestions", {"ts": time.time(), "query": query, "niche": niche, "items": items})
        return items
    finally:
        _SCOUT_LOCK.release()


def due(cfg: Cfg, pending: int = 0) -> bool:
    """Cần tìm thêm: bật tự động + số chủ đề đang chờ < target_queue (mặc định 10)."""
    a = acfg(cfg)
    if not a["enabled"]:
        return False
    target = int(a.get("min_queue", 10))
    queued = sum(1 for j in db.jobs() if j["status"] in ("queued", "preparing")) - pending
    last = db.kv_get("topic_scout_last") or 0
    cooldown = 90 if queued < target else 1200
    return queued < target and (time.time() - last > cooldown)
