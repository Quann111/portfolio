import re

from .config import Cfg, path
from .llm import LLM

FACTS_SCHEMA = {
    "type": "object",
    # giới hạn số mục: không có maxItems thì Qwen 30B có lúc liệt kê mãi tới khi bị cắt (~9000 token)
    "properties": {"facts": {"type": "array", "minItems": 10, "maxItems": 25, "items": {
        "type": "object",
        "properties": {"claim": {"type": "string"}, "confidence": {"type": "number"},
                       "importance": {"type": "number"}, "source": {"type": "string"},
                       "kind": {"type": "string", "enum": ["fact", "explanation", "hypothesis", "opinion"]},
                       "evidence": {"type": "string"}},
        "required": ["claim", "confidence", "importance", "source", "kind", "evidence"]}}},
    "required": ["facts"],
}


def _local_passages(topic: str, cfg: Cfg, limit_chars=8000) -> str:
    d = path(cfg.get("knowledge_dir") or "knowledge")
    if not d.exists():
        return ""
    words = {w for w in re.findall(r"\w{3,}", topic.lower())}
    scored = []
    for f in list(d.rglob("*.txt")) + list(d.rglob("*.md")):
        for para in re.split(r"\n\s*\n", f.read_text(encoding="utf-8", errors="ignore")):
            hits = sum(1 for w in re.findall(r"\w{3,}", para.lower()) if w in words)
            if hits:
                scored.append((hits / (1 + len(para) / 500), f.name, para.strip()))
    scored.sort(reverse=True)
    out, n = [], 0
    for _, name, para in scored:
        if n + len(para) > limit_chars:
            break
        out.append(f"[{name}] {para}")
        n += len(para)
    return "\n\n".join(out)


TOPIC_SCHEMA = {
    "type": "object",
    "properties": {"corrected": {"type": "string"}, "changed": {"type": "boolean"},
                   "meaning": {"type": "string"}},
    "required": ["corrected", "changed", "meaning"],
}


def clean_topic(topic: str, cfg: Cfg, llm: LLM, log) -> str:
    """Sửa lỗi chính tả/gõ sai trong chủ đề trước khi lập kế hoạch."""
    res = llm.chat([{"role": "user", "content":
        f"A user typed this video topic in {cfg.language}: \"{topic}\". Fix obvious typos or missing/wrong "
        "diacritics so it means what the user most likely intended (e.g. Vietnamese 'khoả' meant 'khoẻ'). "
        "Do not change the meaning or add words. Return corrected topic, changed, and a one-sentence meaning."}],
        TOPIC_SCHEMA, temperature=0.1)
    fixed = res["corrected"].strip() or topic
    if res["changed"] and fixed != topic:
        log(f"Sửa chủ đề: \"{topic}\" → \"{fixed}\"")
    log(f"Hiểu chủ đề là: {res['meaning']}")
    return fixed


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _supported(evidence: str, page: str) -> bool:
    """Câu trích có thật trong trang: khớp nguyên văn, hoặc ≥ 70% từ (≥ 3 ký tự) xuất hiện trong trang
    (LLM hay sửa nhẹ/đổi dấu câu khi chép)."""
    ev, pg = _norm(evidence), _norm(page)
    if not ev:
        return False
    if ev[:60] in pg:
        return True
    words = [w for w in re.findall(r"\w{3,}", ev)]
    if len(words) < 4:
        return False
    vocab = set(re.findall(r"\w{3,}", pg))
    return sum(w in vocab for w in words) / len(words) >= 0.7


def fact_sheet(topic: str, cfg: Cfg, llm: LLM, log, pages: list[dict] | None = None) -> list[dict]:
    """pages: đoạn trích web [{n, url, title, source, date, text}] – nguồn chính khi research.mode = web."""
    from datetime import datetime
    docs = _local_passages(topic, cfg)
    pages = pages or []
    mode = (cfg.get("research") or {}).get("mode", "web")
    log(f"Research: {len(pages)} nguồn web, {'có' if docs else 'không có'} tài liệu local")
    web = "\n\n".join(f"[{p['n']}] {p['url']} | {p.get('source', '')} | published: {p.get('date') or 'unknown'}\n"
                      f"{p['title']}\n{p['text']}" for p in pages)
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    rules = ("Base EVERY fact on the web sources above (they are newer than your training data). source = the "
             "exact URL, evidence = one sentence copied verbatim from that source, published = its date. Only add "
             "background from your own knowledge when no source covers it, with source 'model_knowledge', "
             "evidence '' and confidence ≤ 0.85. When sources disagree prefer the most recent one and say so. "
             if pages else
             "No web sources are available: use documents or general knowledge only; source = document file name "
             "or 'model_knowledge'; evidence = exact sentence from a document or ''. ")
    res = llm.chat([
        {"role": "system", "content": "You are a careful fact-checker. Only state facts you are sure about. "
                                      "Never invent statistics, dates, names, product versions or quotes."},
        {"role": "user", "content":
            f"Current date/time: {now}. Topic: {topic}\n\n"
            + (f"WEB SOURCES (fetched now):\n{web}\n\n" if web else "")
            + (f"Reference documents (source = file name):\n{docs}\n\n" if docs else "")
            + "List 15-25 DIFFERENT key points (no duplicates; evidence at most 30 words) needed to make an "
              "accurate, up-to-date, interesting video on this topic, "
              "including the latest developments, surprising facts and concrete examples. For each: kind = fact "
              "(checkable), explanation (how/why), hypothesis (not settled) or opinion; confidence 0-1; importance "
              "0-1; " + rules + "evidence must be copied in the ORIGINAL language of the source, word for word "
              "(do not translate it). Company marketing claims (speed, '100+ languages'…) must be written as "
              "'X cho biết…'. Write claims in " + cfg.language + "."},
    ], FACTS_SCHEMA, temperature=0.2, max_tokens=9000)
    urls = {p["url"]: p for p in pages}
    local_files = {p.name for p in path(cfg.get("knowledge_dir") or "knowledge").rglob("*")} \
        if path(cfg.get("knowledge_dir") or "knowledge").exists() else set()
    facts, seen = [], set()
    for f in res["facts"]:
        if f["confidence"] < 0.8 or f["kind"] == "opinion":
            continue
        key = re.sub(r"\W+", " ", f["claim"].lower()).strip()[:90]
        if key in seen:
            continue
        seen.add(key)
        src = urls.get(f["source"])
        ev = f["evidence"].strip()
        # chỉ coi là đã xác minh khi câu trích thật sự nằm trong trang web đã đọc / file tài liệu có thật
        f["verified"] = bool(src and _supported(ev, src["text"])) or (bool(ev) and f["source"] in local_files)
        if not src and f["source"].startswith("http"):
            f["source"] = "model_knowledge"     # URL không nằm trong các trang đã đọc → không tin
        if src:
            f["published"] = src.get("date")
        if f["kind"] == "hypothesis" or not f["verified"]:
            f["phrasing"] = "cautious"
        if mode == "web" and pages and f["source"] == "model_knowledge" and f["importance"] > 0.7:
            f["importance"] = 0.7          # ở chế độ web, kiến thức model chỉ làm bối cảnh
        facts.append(f)
    facts.sort(key=lambda f: (-f["verified"], -f["importance"]))
    n_ver = sum(f["verified"] for f in facts)
    log(f"Fact sheet ({now}): giữ {len(facts)}/{len(res['facts'])} ý; {n_ver} đã xác minh theo nguồn, "
        f"{len(facts) - n_ver} diễn đạt thận trọng")
    return facts
