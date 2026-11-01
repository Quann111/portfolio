"""Tự tìm chủ đề khi hàng đợi gần hết: tin nóng (Google News RSS) + ngách của các kênh vẽ tay tham khảo →
Qwen đề xuất ứng viên → đo độ "hot" trên YouTube 30 ngày qua bằng engine last30days (skill mvanhorn/last30days-skill,
MIT) → chọn N chủ đề tốt nhất, không trùng chủ đề cũ, thêm vào hàng đợi."""
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from . import db
from .config import ROOT, Cfg
from .llm import NO_WINDOW

L30 = ROOT / "skills" / "last30days"
OUT = ROOT / "data" / "topic_scout"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
FEEDS = [
    "https://news.google.com/rss?hl=vi&gl=VN&ceid=VN:vi",
    "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=vi&gl=VN&ceid=VN:vi",
    "https://news.google.com/rss/headlines/section/topic/WORLD?hl=vi&gl=VN&ceid=VN:vi",
    "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=vi&gl=VN&ceid=VN:vi",
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


def acfg(cfg: Cfg) -> dict:
    a = dict(cfg.get("auto_topics") or {})
    a.setdefault("enabled", True)
    a.setdefault("batch", 5)
    a.setdefault("min_queue", 1)
    a.setdefault("last30days", True)
    if a["last30days"] and not (shutil.which("uv") and shutil.which("yt-dlp")):
        a["last30days"] = False          # thiếu công cụ → bỏ phần đo độ hot, vẫn tìm chủ đề bình thường
    a.setdefault("niches", [])
    a.setdefault("reference_channels", [])
    return a


def headlines(limit: int = 40) -> list[str]:
    out = []
    for url in FEEDS:
        try:
            root = ET.fromstring(requests.get(url, headers=UA, timeout=20).content)
            out += [(it.findtext("title") or "").rsplit(" - ", 1)[0].strip() for it in root.findall(".//item")[:12]]
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
    """Video gần đây của các kênh vẽ tay tham khảo (trang Notion) – chỉ lấy video 'vượt trội' (lượt xem ≥ 1.2×
    trung vị của chính kênh đó) làm mẫu đề tài đã được khán giả kiểm chứng. yt-dlp, không cần key; cache 24h."""
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
    """Bỏ cụm mở đầu chung ("Tại sao…") và dấu câu – tiêu đề nào cũng mở bằng "Tại sao" thì so cả câu luôn > 0.62."""
    t = re.sub(r"[?!.:,\"'‘’“”]", " ", t.lower()).strip()
    return re.sub(r"\s+", " ", _LEAD.sub("", t))


def _bigrams(t: str) -> set:
    """Cặp âm tiết liền nhau – tiếng Việt tách theo âm tiết nên so từ lẻ ("giá", "thế", "giới") trùng quá dễ."""
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


def scout(cfg: Cfg, llm, log, n: int | None = None) -> list[str]:
    a = acfg(cfg)
    n = n or int(a["batch"])
    done = [j["topic"] for j in db.jobs()]
    # Cộng thêm danh sách các chủ đề đã từng bị người dùng xoá bỏ để không bao giờ đề xuất lại
    deleted = db.kv_get("deleted_topics") or []
    done = list(dict.fromkeys(done + deleted))
    news = headlines()
    refs = reference_videos(a)
    log(f"🔎 Tìm chủ đề mới: {len(news)} tin nóng, {len(refs)} video nổi bật của "
        f"{len(a['reference_channels'])} kênh vẽ tay tham khảo")
    ref_txt = "\n".join(f"- [{v['channel']}] {v['title']} ({v['views']:,} views, {v['ratio']}× usual)"
                        for v in refs[:30])
    schema = json.loads(json.dumps(CAND_SCHEMA))
    if a["niches"]:      # đúng MỘT ngách trong danh sách → luật "tối đa 2 chủ đề / ngách" mới có tác dụng
        schema["properties"]["candidates"]["items"]["properties"]["niche"] = {"type": "string", "enum": a["niches"]}
    res = llm.chat([{"role": "user", "content":
        f"Today is {datetime.now():%d/%m/%Y}. You plan topics for a Vietnamese YouTube channel of long hand-drawn "
        f"whiteboard explainer videos ({cfg.target_minutes} minutes). Viewers love curiosity, money, history, "
        "geopolitics, psychology, health myths, success stories and 'why does X happen' questions.\n"
        f"Proven formats – recent videos that beat their channel's usual views on whiteboard channels of the same "
        f"genre (learn the KIND of subject and title style; do not copy):\n{ref_txt}\n"
        f"Preferred niches: {', '.join(a['niches'])}.\n"
        f"Today's hot headlines (Vietnam/world):\n- " + "\n- ".join(news[:35]) + "\n"
        f"Already made (do NOT repeat or rephrase): {done[-60:]}\n\n"
        "Propose 18 DIFFERENT video topics: about half hooked to a current headline (explain the story behind it), "
        "half evergreen from the niches (kind = news / evergreen). NEVER: Vietnamese domestic politics, Vietnamese "
        "leaders or party/government officials, partisan comparisons of politicians, religion, tragedies of private "
        "people, medical advice, conspiracy theories, misleading or sensational claims, feuds between named "
        "people. Each candidate must be a DIFFERENT subject (never two about the same coin, country, company or "
        "event). Label kind honestly: news = built on one of today's headlines, evergreen = still interesting in "
        "a year. Prefer stories, mechanisms, money and history over opinions. topic = a punchy Vietnamese title that creates curiosity (question or "
        "surprising claim, max 12 words, no clickbait lies); query_en = 3-6 English words to search YouTube for "
        "the same subject; niche; angle = the one-sentence hook/insight; appeal 0-10 = how strongly a Vietnamese "
        "viewer would click AND watch to the end. Spread the 12 over at least 5 different niches."}], schema, temperature=0.9, max_tokens=5000)
    raw = res.get("candidates") or []
    cands = [c for c in raw if c["topic"].strip() and not _similar(c["topic"], done)]
    log(f"  LLM đề xuất {len(raw)} ứng viên, loại {len(raw) - len(cands)} vì trùng chủ đề đã làm/đã xoá")
    if a["last30days"]:
        log(f"  Đo độ hot YouTube 30 ngày cho {len(cands)} ứng viên (last30days)…")
        with ThreadPoolExecutor(max_workers=3) as ex:
            heats = list(ex.map(lambda c: youtube_heat(c["query_en"]), cands))
    else:
        heats = [{"heat": 0.0, "videos": 0}] * len(cands)
    top_heat = max([h["heat"] for h in heats] + [1.0])
    for c, h in zip(cands, heats):
        c["trend"] = h
        c["score"] = round(0.6 * min(10, c["appeal"]) / 10 + 0.4 * h["heat"] / top_heat, 3)
    cands.sort(key=lambda c: -c["score"])
    picked = []
    max_news = max(int(a.get("max_news", 2)), n // 2)
    for c in cands:
        if len(picked) >= n:
            break
        if c.get("kind") == "news" and sum(p.get("kind") == "news" for p in picked) >= max_news:
            continue          # cân bằng: tối đa max_news chủ đề bám tin nóng, còn lại là chủ đề lâu dài
        if sum(p["niche"] == c["niche"] for p in picked) >= max(2, (n + 3) // 4):
            continue          # đa dạng ngách
        words = set(re.findall(r"[a-z0-9]{4,}", c["query_en"].lower()))
        if any(len(words & set(re.findall(r"[a-z0-9]{4,}", p["query_en"].lower()))) >= 2 for p in picked):
            continue          # cùng một đề tài (vd hai chủ đề Bitcoin)
        if not _similar(c["topic"], done + [p["topic"] for p in picked]):
            picked.append(c)
    for c in picked:
        db.add_topic(c["topic"].strip().strip('"'))
        log(f"  + [{round(c['score'] * 100)}đ] {c['topic']}  ({c.get('kind')} · hấp dẫn {c['appeal']:.0f}/10 · hot {c['trend']['heat']:.1f} · {c['niche']})")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{datetime.now():%Y%m%d-%H%M}.json").write_text(
        json.dumps({"headlines": news, "candidates": cands, "picked": [p["topic"] for p in picked]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    db.kv_set("topic_scout_last", time.time())
    return [p["topic"] for p in picked]


def due(cfg: Cfg, pending: int = 0) -> bool:
    """C?n t?m th?m: b?t t? ??ng + s? ch? ?? ?ang ch? < target_queue (m?c ??nh 10)."""
    a = acfg(cfg)
    if not a["enabled"]:
        return False
    target = int(a.get("min_queue", 10))
    queued = sum(1 for j in db.jobs() if j["status"] in ("queued", "preparing")) - pending
    last = db.kv_get("topic_scout_last") or 0
    # N?u h?ng ??i ?t h?n 10 ch? ?? th? t? ??ng t?m th?m ngay (gi?n c?ch t?i thi?u 90s tr?nh spam)
    cooldown = 90 if queued < target else 1200
    return queued < target and (time.time() - last > cooldown)
