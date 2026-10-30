"""Research thời gian thực: tìm web (DuckDuckGo, không cần key) → đọc trang → đoạn trích có URL + ngày đăng."""
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import Cfg, ROOT
from .llm import LLM

CACHE = ROOT / "data" / "webcache"
BLOCK = ("facebook.com", "tiktok.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "reddit.com",
         "pinterest.", "voz.vn", "tinhte.vn/thread", "quora.com", "shopee.", "lazada.")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36"}

QUERY_SCHEMA = {
    "type": "object",
    "properties": {"entities": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
                   "queries": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 6}},
    "required": ["entities", "queries"],
}
PICK_SCHEMA = {
    "type": "object",
    "properties": {"relevant": {"type": "array", "items": {"type": "integer"}}},
    "required": ["relevant"],
}


def rcfg(cfg: Cfg) -> dict:
    r = dict(cfg.get("research") or {})
    r.setdefault("mode", "web")
    r.setdefault("max_pages", 8)
    r.setdefault("news_timelimit", "d")
    r.setdefault("region", "vn-vi")
    r.setdefault("trusted_domains", [])
    r.setdefault("cache_hours", 6)
    return r


def queries(topic: str, cfg: Cfg, llm: LLM) -> tuple[list[str], list[str]]:
    """(entities, queries) – entities: tên riêng chính để tìm tin theo đúng cụm từ."""
    today = datetime.now().strftime("%d/%m/%Y")
    res = llm.chat([{"role": "user", "content":
        f"Today is {today}. Video topic: \"{topic}\".\n- entities: 1-3 exact proper names this topic is about, "
        "as written in the news (e.g. 'Cốc Cốc', 'Trường Giang', 'Saudi Aramco'); the first one is the main.\n"
        "- queries: 3-6 NEUTRAL web search queries using those names (e.g. '<name> tin tức mới nhất', '<name> "
        "lịch sử phát triển', '<name> 2026', '<name> news'). NEVER put guesses or assumed events in a query "
        "(no 'bị cấm', 'bị phạt', 'đóng cửa', 'thu hồi' unless those words are in the topic itself)."}],
        QUERY_SCHEMA, temperature=0.2)
    ents = [e.strip() for e in res["entities"] if e.strip()][:3]
    # chủ đề ghép nhiều thực thể (vd "Nhật Bản" + "Hàn Quốc" + "Premier League"): tìm theo CẢ cụm, không tách
    # riêng từng tên – tìm riêng "Nhật Bản" chỉ ra tin thời sự Nhật không liên quan
    main = " ".join(ents) if len(ents) > 1 else (ents[0] if ents else topic)
    year = datetime.now().year
    base = [topic, f"{main} tin tức mới nhất", f"{main} {year}", f"{main} news {year}"]
    qs = base + [q.strip() for q in res["queries"] if q.strip()]
    return ents, list(dict.fromkeys(qs))[:8]


def google_news(entity: str, days: int, r: dict) -> list[dict]:
    """Google News RSS (miễn phí, không key) – tốt cho tin tiếng Việt; trả tiêu đề + nguồn + ngày đăng."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    from urllib.parse import quote
    hl, gl = ("vi", "VN") if r["region"].startswith("vn") else ("en", "US")
    q = entity if entity.startswith('"') else chr(34) + entity + chr(34)
    url = (f"https://news.google.com/rss/search?q={quote(q + f' when:{days}d')}"
           f"&hl={hl}&gl={gl}&ceid={gl}:{hl}")
    try:
        root = ET.fromstring(requests.get(url, headers=UA, timeout=20).content)
    except Exception:
        return []
    out = []
    for it in root.findall(".//item")[:20]:
        try:
            date = parsedate_to_datetime(it.findtext("pubDate")).isoformat()
        except Exception:
            date = None
        title = (it.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
        out.append({"url": it.findtext("link") or "", "title": title, "snippet": re.sub(r"\s+", " ", desc)[:300],
                    "date": date, "source": it.findtext("source") or "Google News", "kind": "news"})
    return out


def news_by_entity(entities: list[str], r: dict) -> list[dict]:
    """Tin theo đúng cụm từ "tên" từ Google News + DuckDuckGo – 24h → 7 ngày → 30 ngày tới khi có ≥ 5 tin."""
    out = []
    # nhiều thực thể → một truy vấn ghép (mỗi tên trong ngoặc kép, Google/DDG hiểu là AND)
    terms = [" ".join(f'"{e}"' for e in entities)] if len(entities) > 1 else [f'"{e}"' for e in entities]
    for tl, days in (("d", 1), ("w", 7), ("m", 30)):
        for t in terms:
            out += google_news(t, days, r)
            out += _search(t, "news", tl, r["region"], 15)
        if len({x["url"] for x in out}) >= 5:
            break
    return out


def _search(q: str, kind: str, timelimit, region: str, n: int) -> list[dict]:
    from ddgs import DDGS
    try:
        d = DDGS()
        if kind == "news":
            rows = d.news(q, region=region, timelimit=timelimit, max_results=n)
            return [{"url": r.get("url"), "title": r.get("title", ""), "snippet": r.get("body", ""),
                     "date": r.get("date"), "source": r.get("source", ""), "kind": "news"} for r in rows]
        rows = d.text(q, region=region, timelimit=timelimit, max_results=n)
        return [{"url": r.get("href"), "title": r.get("title", ""), "snippet": r.get("body", ""),
                 "date": None, "source": urlparse(r.get("href", "")).netloc, "kind": "web"} for r in rows]
    except Exception:
        return []


def search_all(qs: list[str], r: dict) -> list[dict]:
    jobs = []
    for q in qs:
        jobs.append((q, "news", r["news_timelimit"]))
        jobs.append((q, "web", None))
    with ThreadPoolExecutor(max_workers=4) as ex:
        out = list(ex.map(lambda j: _search(j[0], j[1], j[2], r["region"], 8), jobs))
    news = sum(1 for rows in out for x in rows if x["kind"] == "news")
    if news < 5 and r["news_timelimit"] == "d":          # ít tin 24h → nới ra 7 ngày
        with ThreadPoolExecutor(max_workers=4) as ex:
            out += list(ex.map(lambda q: _search(q, "news", "w", r["region"], 8), qs))
    seen, rows = set(), []
    for x in (y for rs in out for y in rs):
        u = x.get("url") or ""
        if not u.startswith("http") or u in seen or any(b in u for b in BLOCK):
            continue
        seen.add(u)
        rows.append(x)
    return rows


def pick(topic: str, rows: list[dict], r: dict, llm: LLM, entities: list[str] | None = None) -> list[dict]:
    """Qwen lọc kết quả thật sự liên quan (tránh trùng tên: 'Cốc Cốc' trình duyệt ≠ 'hồ Núi Cốc')."""
    if not rows:
        return []
    rows[:] = rows[:45]
    listing = "\n".join(f"[{i}] {x['title']} — {x['snippet'][:160]} ({x['source']}, {(x.get('date') or 'n/a')[:10]})"
                        for i, x in enumerate(rows))
    res = llm.chat([{"role": "user", "content":
        f"Topic: \"{topic}\". Which results are really about this topic (not a different thing with a similar "
        f"name, and not articles that only mention it in passing)? Include ALL recent news items whose main "
        f"subject is the topic (they carry the latest developments) plus the most informative background pages. "
        f"Return their numbers.\n{listing}"}],
        PICK_SCHEMA, temperature=0.0)
    chosen = [rows[i] for i in dict.fromkeys(res["relevant"]) if 0 <= i < len(rows)]
    if len(chosen) < 3 and entities:
        # LLM lọc quá tay → giữ các kết quả có đúng tên thực thể trong tiêu đề/đoạn trích
        names = [e.lower() for e in entities]
        # nhiều thực thể → phải có ĐỦ các tên (tránh tin chỉ nhắc "Nhật Bản")
        extra = [x for x in rows if x not in chosen
                 and all(n in (x["title"] + " " + x["snippet"]).lower() for n in names[:2])]
        chosen += extra[: max(0, 6 - len(chosen))]
    trusted = r["trusted_domains"]
    week = time.time() - 7 * 86400
    # giữ chỗ cho tối đa 3 tin MỚI NHẤT (≤ 7 ngày) – thông tin trong ngày không bị bài tổng quan đẩy ra ngoài
    fresh = sorted([x for x in chosen if (_ts(x.get("date")) or 0) >= week], key=lambda x: -(_ts(x["date"]) or 0))[:3]
    rest = [x for x in chosen if x not in fresh]
    rest.sort(key=lambda x: (not any(t in x["url"] for t in trusted), x["kind"] != "news",
                             -(_ts(x.get("date")) or 0)))
    return (fresh + rest)[: r["max_pages"]]


def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fetch(url: str, cache_hours: float) -> dict | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".json")
    if key.exists() and time.time() - key.stat().st_mtime < cache_hours * 3600:
        return json.loads(key.read_text(encoding="utf-8"))
    try:
        import trafilatura
        resp = requests.get(url, headers=UA, timeout=20)
        if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", ""):
            return None
        if "charset" not in resp.headers.get("Content-Type", "").lower():
            resp.encoding = resp.apparent_encoding or "utf-8"   # tránh lỗi font kiểu 'Cá»c'
        data = trafilatura.extract(resp.text, url=url, output_format="json", with_metadata=True,
                                   include_comments=False, include_tables=False)
        if not data:
            return None
        d = json.loads(data)
        text = (d.get("text") or "").strip()
        if len(text) < 300:
            return None
        out = {"url": url, "title": d.get("title") or "", "date": d.get("date"), "text": text[:2500],
               "fetched_at": datetime.now(timezone.utc).isoformat()}
        key.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    except Exception:
        return None


def collect(topic: str, cfg: Cfg, llm: LLM, log) -> list[dict]:
    """Trả về danh sách đoạn trích [{n, url, title, date, text}] – rỗng nếu không có mạng/kết quả."""
    r = rcfg(cfg)
    if r["mode"] == "offline":
        return []
    ents, qs = queries(topic, cfg, llm)
    log(f"Web research: thực thể {ents}; {len(qs)} truy vấn – " + " | ".join(qs))
    news = news_by_entity(ents, r)
    rows = search_all(qs, r)
    seen = {x["url"] for x in rows}
    rows = [x for x in news if x["url"] not in seen and not any(b in x["url"] for b in BLOCK)] + rows
    chosen = pick(topic, rows, r, llm, ents)
    log(f"Web research: {len(rows)} kết quả, chọn {len(chosen)} nguồn liên quan để đọc")
    with ThreadPoolExecutor(max_workers=4) as ex:
        pages = list(ex.map(lambda x: fetch(x["url"], r["cache_hours"]), chosen))
    out = []
    for x, p in zip(chosen, pages):
        if p:
            out.append({"url": x["url"], "title": p["title"] or x["title"], "source": x["source"],
                        "date": x.get("date") or p.get("date"), "text": p["text"]})
        elif x.get("snippet"):
            out.append({"url": x["url"], "title": x["title"], "source": x["source"], "date": x.get("date"),
                        "text": x["snippet"]})
    for i, o in enumerate(out, 1):
        o["n"] = i
    dated = sorted((o["date"] for o in out if o.get("date")), reverse=True)
    log(f"Web research: đọc được {len(out)} nguồn" + (f", mới nhất {dated[0][:16]}" if dated else ""))
    return out


def sources_md(topic: str, pages: list[dict], facts: list[dict], path: Path):
    used = {f.get("source") for f in facts}
    lines = [f"# Nguồn tham khảo – {topic}", f"_Tra cứu lúc {datetime.now():%d/%m/%Y %H:%M}_", ""]
    for p in pages:
        mark = "✅ dùng" if p["url"] in used else "—"
        lines.append(f"{p['n']}. [{p['title'] or p['url']}]({p['url']}) · {p.get('source', '')} · "
                     f"{(p.get('date') or 'không rõ ngày')[:16]} · {mark}")
    lines += ["", "## Fact sheet", ""]
    for f in facts:
        lines.append(f"- {f['claim']}  \n  _{f.get('kind')} · nguồn: {f.get('source')} · "
                     f"{'đã xác minh' if f.get('verified') else 'thận trọng'}_")
    path.write_text("\n".join(lines), encoding="utf-8")
