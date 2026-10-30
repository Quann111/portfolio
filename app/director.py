import copy
import json
import re
import threading
import time
from datetime import datetime

from . import planner, prompt_lib, story_quality, variety
from .config import Cfg
from . import llm as llm_mod, gemini_bridge
from .llm import LLM

BEATS = planner.BEATS
EMOTIONS = ["calm", "curious", "excited", "serious", "warm"]
CAMERAS = ["static", "zoom_in", "zoom_out", "pan_left", "pan_right"]
TRANSITIONS = ["cut", "fade", "draw_over", "chapter"]

HOOKS_SCHEMA = {
    "type": "object",
    "properties": {"hooks": {"type": "array", "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "score": {"type": "number"}, "why": {"type": "string"}},
        "required": ["text", "score", "why"]}}},
    "required": ["hooks"],
}

BIBLE_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {"type": "array", "items": {
            "type": "object", "properties": {"name": {"type": "string"}, "look": {"type": "string"}},
            "required": ["name", "look"]}},
        "objects": {"type": "array", "items": {
            "type": "object", "properties": {"name": {"type": "string"}, "look": {"type": "string"}},
            "required": ["name", "look"]}},
    },
    "required": ["characters", "objects"],
}

SCENE_PROPS = {
    "purpose": {"type": "string", "enum": BEATS},
    "narration": {"type": "string"},
    "visual_type": {"type": "string", "enum": planner.VISUAL_TYPES},
    "visual_concept": {"type": "string"},
    "image_prompt": {"type": "string"},
    "drawing_order": {"type": "array", "items": {"type": "string"}},
    "main_object": {"type": "string"},
    "secondary_objects": {"type": "array", "items": {"type": "string"}},
    "composition": {"type": "string"},
    "focus": {"type": "string"},
    "camera": {"type": "string", "enum": CAMERAS},
    "transition": {"type": "string", "enum": TRANSITIONS},
    "emphasis": {"type": "string"},
    "keywords": {"type": "array", "items": {"type": "string"}},
    "emotion": {"type": "string", "enum": EMOTIONS},
    "pause_before": {"type": "number"},
    "importance": {"type": "number"},
}


def scenes_schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {"scenes": {"type": "array", "minItems": n, "maxItems": n, "items": {
            "type": "object", "properties": SCENE_PROPS, "required": list(SCENE_PROPS)}}},
        "required": ["scenes"],
    }


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"boredom": {"type": "number"}, "repetition": {"type": "number"},
                   "information_density": {"type": "number"}, "retention": {"type": "number"},
                   "fix": {"type": "string"}},
    "required": ["boredom", "repetition", "information_density", "retention", "fix"],
}


def _words(text: str) -> int:
    return len(text.split())


def _sys(cfg: Cfg) -> str:
    ch = cfg.bible["channel"]
    return (f"You are the AI Director of a YouTube whiteboard-animation channel. Language: {cfg.language}. "
            f"Audience: {ch['audience']}. Tone: {ch['tone']}. Narration is spoken text only: no headings, "
            "no stage directions, no emojis, numbers written as words. Never use these openings: "
            f"{ch['forbidden_openings']}. Today is {datetime.now():%d/%m/%Y}. Use only facts from the fact sheet; "
            "never invent statistics, studies, quotes, product versions, features or events – if a detail is not "
            "in the fact sheet, leave it out. Prefer verified facts with the most recent 'published' date and "
            "mention when things happened ('tháng 10/2026', 'tuần trước') for news events. Facts marked "
            "phrasing=cautious or kind=hypothesis must be phrased carefully (e.g. 'nhiều người cho rằng…').")


def hook(topic, facts, cfg, llm, log):
    res = llm.chat([{"role": "system", "content": _sys(cfg)}, {"role": "user", "content":
        f"Topic: {topic}\nFacts: {json.dumps(facts[:12], ensure_ascii=False)}\n"
        "Write 5 different opening hooks (1-2 spoken sentences, max 35 words) that create curiosity in the "
        "first 5 seconds, e.g. a surprising fact, a bold question, or a 'what if'. No false clickbait. "
        "Score each 0-10 for curiosity, clarity and honesty."}], HOOKS_SCHEMA, temperature=0.9)
    hooks = sorted(res["hooks"], key=lambda h: -h["score"])
    log("Hook: " + " | ".join(f"{h['score']:.1f}" for h in hooks) + f" → chọn: {hooks[0]['text']}")
    return hooks[0]["text"]


def bible(topic, ol, cfg, llm):
    return llm.chat([{"role": "user", "content":
        f"Video: {ol['title']} ({topic}). Segments: {[c['title'] for c in ol['chapters']]}.\n"
        "List the recurring characters (max 4) and recurring objects (max 6) that will be drawn across "
        "scenes. For each, give a fixed visual description in ENGLISH for a simple whiteboard line drawing "
        "so every scene draws them identically. Each look at most 15 words, simple shapes only. Only concrete "
        "drawable things – never backgrounds, sky fills, gradients or colour areas: everything is dark line "
        "art on plain beige paper with at most a small red, orange or blue accent. "
        f"Always include the narrator: {cfg.bible['visual']['narrator_character']}."}],
        BIBLE_SCHEMA, temperature=0.3)


def _scene_rules(cfg, words, ci, types):
    return (
        f"- narration: {words} to {words + 12} words in {cfg.language} (count them; about "
        f"{cfg.scene_seconds} seconds spoken). Never shorter. Continue naturally, no repetition"
        + (" – the hook scene continues after the hook sentence to reach this length" if ci == 0 else "") + "\n"
        "- purpose: the beat of this scene\n"
        f"- visual_type: one of {planner.VISUAL_TYPES}; prefer {types}; never the same type more than twice "
        "in a row\n"
        "- visual_concept: ENGLISH, the idea the picture must communicate, shown only with objects, "
        "arrows and gestures – NEVER words, labels or numbers\n"
        "- image_prompt: ENGLISH, 50-100 words describing ONE rich whiteboard illustration that shows "
        "EXACTLY what this narration says: 3-6 meaningful elements capturing all key points of the line. "
        "If a REAL person is mentioned (e.g. Obama, Musk, Putin), depict their recognizable sketch portrait. "
        "If countries/flags/symbols are mentioned (e.g. French flag, NATO star), draw the accurate flag/emblem. "
        "If specific stats, numbers, or time marks are mentioned, include visual meters, charts, or clear indicators. "
        "Arrange elements logically across foreground, midground, and background with clear focal point. "
        "Keep the whiteboard line-art style on beige paper, no filled backgrounds, no random unrelated text\n"
        + prompt_lib.guide().replace("\n", "\n    ") + "\n"
        "- drawing_order: 2-4 short ENGLISH object names in the order they are drawn, matching the order they "
        "are mentioned in the narration\n"
        "- main_object: the ONE thing drawn largest; secondary_objects: 0-2 smaller supporting things\n"
        "- composition: one short ENGLISH phrase placing them, e.g. 'main subject centre-left, small arrow to "
        "a secondary object on the right'; never put anything in the bottom fifth (subtitle area)\n"
        "- focus: the object the viewer should look at most\n"
        "- camera: static by DEFAULT. Use zoom_in only for a key reveal or importance ≥ 0.8, zoom_out only to "
        "reveal a bigger picture, pan_left/pan_right only for a comparison or journey. Never move the camera "
        "just for variety\n"
        "- transition into this scene: cut (default), fade (change of idea), draw_over (keeps building on the "
        "previous drawing, only when this scene directly continues the previous one), chapter (first scene of "
        "a segment only)\n"
        "- emphasis: the 2-6 word key phrase copied exactly from the narration; keywords: 1-3 words to "
        "highlight in subtitles\n"
        "- emotion; pause_before seconds (0 normally, 0.5-0.8 right before a big reveal); importance 0-1 "
        "(key takeaway ≥ 0.8)\n")


def _quality(rv: dict, todo: list) -> float:
    """So bản viết: retention cao, ít lặp/chán, ít lỗi mạch (lỗi high nặng gấp đôi)."""
    pen = sum(0.12 if i["severity"] == "high" else 0.06 for i in todo)
    return rv["retention"] - 0.3 * rv["boredom"] - 0.2 * rv["repetition"] - pen


def _covered(rows, ol, ci) -> str:
    """Ý đã nói ở các đoạn trước – đưa vào prompt để cấm lặp lại."""
    pts = [c["key_takeaway"] for c in ol["chapters"][:ci]]
    pts += [r["emphasis"] for r in rows if r.get("emphasis") and r.get("chapter", 99) < ci]
    seen, out = set(), []
    for p in pts:
        k = p.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(p.strip())
    return "; ".join(out[-40:])


def _chapter(ol, ci, n, hook_text, prev, facts, vb, cfg, llm, words, fix="", covered=""):
    ch = ol["chapters"][ci]
    plan = "\n".join(f"{i + 1}. [{c['beat']}] {c['title']}: {c['summary']}" for i, c in enumerate(ol["chapters"]))
    first = (f"The FIRST scene's narration must START with exactly this hook: \"{hook_text}\"\n" if ci == 0 else "")
    last = (f"End the segment with this call to action: {cfg.bible['channel']['cta']}\n"
            if ci == len(ol["chapters"]) - 1 else "")
    return llm.chat([{"role": "system", "content": _sys(cfg)}, {"role": "user", "content":
        f"Video title: {ol['title']}\nCore message: {ol.get('core_message', '')}\n"
        f"Interest curve: {ol.get('interest_curve', '')}\nBeatmap:\n{plan}\n\n"
        f"Facts: {json.dumps(facts, ensure_ascii=False)}\n"
        f"Visual bible (use these looks): {json.dumps(vb, ensure_ascii=False)}\n"
        f"Story so far (end): {prev[-1500:] or '(start)'}\n"
        + (f"Points ALREADY covered in earlier segments – do NOT repeat or rephrase them, bring NEW information: "
           f"{covered}\n" if covered else "") + "\n"
        f""
        f"Write segment {ci + 1} [{ch['beat']}]: {ch['title']} — {ch['summary']} "
        f"(key point: {ch.get('key_point', '')}; takeaway: {ch['key_takeaway']})\n{first}{last}"
        f"Write EXACTLY {n} scenes as a storyboard. Every 2-4 scenes add something new: a question, a "
        "concrete example, a surprise or a payoff. "
        + (f"Visual callback: the recurring symbol '{ol['callback_symbol']}' should appear in the hook, at the "
           "biggest insight and in the recap/ending scenes (nowhere else). " if ol.get("callback_symbol") else "")
        + "Per scene:\n"
        + _scene_rules(cfg, words, ci, ch.get("visual_types", []))
        + (f"\nIMPORTANT – previous draft was rejected, fix this: {fix}" if fix else "")}],
        scenes_schema(n), temperature=0.8, max_tokens=16000)["scenes"]


EXPAND_SCHEMA = {
    "type": "object",
    "properties": {"narrations": {"type": "array", "items": {"type": "string"}}},
    "required": ["narrations"],
}


def _resize(scenes, idx, words, cfg, llm, instruction):
    res = llm.chat([{"role": "user", "content":
        f"{instruction} Each result must be {words}-{words + 12} words in {cfg.language} (count them). Keep the "
        "meaning, the facts and the exact key phrase. Spoken text only. Return the same number of narrations "
        "in the same order.\n" + json.dumps([scenes[i]["narration"] for i in idx], ensure_ascii=False)}],
        {**EXPAND_SCHEMA, "properties": {"narrations": {"type": "array", "items": {"type": "string"},
                                                        "minItems": len(idx), "maxItems": len(idx)}}},
        temperature=0.5, max_tokens=8000)
    return res["narrations"]


def _ensure_length(scenes, words, cfg, llm, log):
    """Giữ lời mỗi cảnh trong khoảng 80%–130% mục tiêu: ngắn → viết dài thêm, dài → rút gọn."""
    lo, hi = words * 0.8, words * 1.3
    short = [i for i, s in enumerate(scenes) if _words(s["narration"]) < lo]
    long_ = [i for i, s in enumerate(scenes) if _words(s["narration"]) > hi]
    if short:
        log(f"  {len(short)} cảnh lời quá ngắn → viết dài thêm")
        for i, text in zip(short, _resize(scenes, short, words, cfg, llm,
                                          "Expand each narration, adding a concrete detail or example.")):
            if _words(text) > _words(scenes[i]["narration"]):
                scenes[i]["narration"] = text.strip()
    if long_:
        log(f"  {len(long_)} cảnh lời quá dài ({', '.join(str(_words(scenes[i]['narration'])) for i in long_)} chữ)"
            f" → rút gọn về ~{words} chữ")
        for i, text in zip(long_, _resize(scenes, long_, words, cfg, llm,
                                          "Condense each narration: cut repetition and filler, keep the core "
                                          "idea and one concrete detail.")):
            if lo <= _words(text) < _words(scenes[i]["narration"]):
                scenes[i]["narration"] = text.strip()
    return scenes


REVISUAL_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object",
        "properties": {k: SCENE_PROPS[k] for k in ("visual_type", "visual_concept", "image_prompt",
                                                    "drawing_order", "focus", "camera")},
        "required": ["visual_type", "visual_concept", "image_prompt", "drawing_order", "focus", "camera"]}}},
    "required": ["scenes"],
}


def _fix_variety(rows, idx, cfg, llm, log):
    log(f"  Đa dạng hình: đổi kiểu hình cảnh {idx}")
    ctx = [{"i": i, "type": rows[i].get("visual_type"), "camera": rows[i].get("camera"),
            "narration": rows[i]["narration"]} for i in idx]
    res = llm.chat([{"role": "user", "content":
        "These whiteboard scenes repeat the same visual type or camera too often. Give each a DIFFERENT "
        f"visual_type from {planner.VISUAL_TYPES} than its neighbours, a non-static camera where possible, "
        "and a matching image_prompt (ENGLISH, 45-90 words, 3-6 elements from the narration, lightly sketched "
        "setting, expressive characters, plain looks, no names, no "
        f"text), drawing_order, focus, visual_concept.\nScenes: {json.dumps(ctx, ensure_ascii=False)}"}],
        {**REVISUAL_SCHEMA, "properties": {"scenes": {**REVISUAL_SCHEMA["properties"]["scenes"],
                                                      "minItems": len(idx), "maxItems": len(idx)}}},
        temperature=0.7, max_tokens=6000)
    for i, new in zip(idx, res["scenes"]):
        rows[i].update(new)


ENRICH_KEYS = ("visual_type", "visual_concept", "image_prompt", "drawing_order", "main_object", "secondary_objects",
               "composition", "focus")


def enrich_visuals(rows: list, idx: list, ol: dict, cfg, llm, log, batch: int = 6) -> int:
    """Viết lại phần HÌNH (không đụng lời) cho các cảnh idx theo luật prompt ảnh hiện hành – dùng khi đổi phong
    cách ảnh giữa chừng (job đang ở bước ảnh). Trả về số cảnh đã viết lại."""
    vb = ol.get("bible", {})
    rules = _visual_rules(cfg)
    done = 0
    for k in range(0, len(idx), batch):
        part = idx[k:k + batch]
        ctx = [{"i": i, "narration": rows[i]["narration"], "visual_type": rows[i].get("visual_type"),
                "old_image_prompt": rows[i].get("image_prompt", "")} for i in part]
        n = len(part)
        try:
            res = llm.chat([{"role": "system", "content": _sys(cfg)}, {"role": "user", "content":
                f"Video: {ol.get('title', '')}. Visual bible: {json.dumps(vb, ensure_ascii=False)}\n"
                f"Rewrite ONLY the visuals of these {n} scenes so each picture shows exactly what its narration "
                "says, richer than the old prompt (which was too simple). Keep the narration as is. Per scene:\n"
                + rules + f"\nScenes: {json.dumps(ctx, ensure_ascii=False)}"}],
                {"type": "object", "properties": {"scenes": {"type": "array", "minItems": n, "maxItems": n, "items": {
                    "type": "object", "properties": {key: SCENE_PROPS[key] for key in ENRICH_KEYS},
                    "required": list(ENRICH_KEYS)}}}, "required": ["scenes"]},
                temperature=0.6, max_tokens=8000)
        except RuntimeError as e:
            log(f"  Làm giàu hình cảnh {part}: lỗi {e} → giữ prompt cũ")
            continue
        for i, new in zip(part, res["scenes"]):
            rows[i].update({key: new[key] for key in ENRICH_KEYS})
            done += 1
        log(f"  Làm giàu hình: {min(k + batch, len(idx))}/{len(idx)} cảnh")
    return done


def extend(ol, chapter_rows: dict, missing_sec: float, facts, vb, cfg, llm, log) -> dict:
    """Thiếu thời lượng thật → thêm cảnh có giá trị (ví dụ, tình huống) vào các đoạn mỏng nhất.
    chapter_rows: {chapter: [scene dicts]}. Trả về {chapter: [cảnh mới]} để chèn vào cuối đoạn đó."""
    words = round(cfg.scene_seconds * cfg.words_per_minute / 60)
    n_new = max(1, round(missing_sec / cfg.scene_seconds))
    thin = sorted(chapter_rows, key=lambda c: sum(_words(s["narration"]) for s in chapter_rows[c]))
    plan = {}
    for k in range(n_new):
        c = thin[k % len(thin)]
        plan[c] = plan.get(c, 0) + 1
    out = {}
    for c, n in plan.items():
        ch = ol["chapters"][c]
        existing = " ".join(s["narration"] for s in chapter_rows[c])
        log(f"  Thiếu thời lượng → thêm {n} cảnh vào đoạn {c + 1}")
        res = llm.chat([{"role": "system", "content": _sys(cfg)}, {"role": "user", "content":
            f"Segment '{ch['title']}' of video '{ol['title']}' currently says:\n{existing}\n\n"
            f"Facts: {json.dumps(facts, ensure_ascii=False)}\nVisual bible: {json.dumps(vb, ensure_ascii=False)}\n"
            f"Write EXACTLY {n} NEW scenes to insert at the end of this segment that add real value – a concrete "
            "example, a case, a comparison or a consequence NOT already said. Never repeat existing points, never "
            "pad. They must flow from the last sentence. Per scene:\n"
            + _scene_rules(cfg, words, 1, ch.get("visual_types", []))}],
            scenes_schema(n), temperature=0.8, max_tokens=8000)
        # kịch bản NotebookLM không giới hạn chữ/cảnh → không ép về ~38 chữ (cảnh quá dài đã có split_long)
        new = res["scenes"] if ol.get("engine") == "notebooklm" else _ensure_length(res["scenes"], words, cfg, llm, log)
        for s in new:
            s["chapter"], s["beat"] = c, ch["beat"]
            if s.get("transition") in ("chapter", "draw_over"):
                s["transition"] = "cut"
        out[c] = new
    return out


def split_long(rows, cfg, llm, log, limit=None):
    """Cảnh có lời đọc ước tính > limit (mặc định 1.6× thời lượng mục tiêu) → tách thành 2-3 cảnh nối tiếp."""
    limit = limit or cfg.scene_seconds * 1.6
    words = round(cfg.scene_seconds * cfg.words_per_minute / 60)
    out = []
    for s in rows:
        est = _words(s["narration"]) / cfg.words_per_minute * 60
        if est <= limit:
            out.append(s)
            continue
        k = min(3, max(2, round(est / cfg.scene_seconds)))
        log(f"  Tách cảnh dài {est:.0f}s thành {k} cảnh")
        res = llm.chat([{"role": "user", "content":
            f"Split this whiteboard scene into exactly {k} consecutive scenes. Split the narration at natural "
            "sentence boundaries WITHOUT changing or dropping any words. Each part gets its own simple visual "
            "that continues the previous part's drawing (first part keeps the original transition, later parts "
            "use transition draw_over).\nOriginal scene: " + json.dumps(s, ensure_ascii=False)
            + "\nPer scene:\n" + _scene_rules(cfg, max(10, words // 2), 1, [s.get("visual_type", "")])}],
            scenes_schema(k), temperature=0.4, max_tokens=8000)
        parts = res["scenes"]
        if abs(sum(_words(p["narration"]) for p in parts) - _words(s["narration"])) > _words(s["narration"]) * 0.2:
            out.append(s)
            continue
        cont = "draw_over" if (cfg.get("canvas") or {}).get("enabled") else "cut"
        for j, p in enumerate(parts):
            p["chapter"], p["beat"] = s["chapter"], s["beat"]
            p["transition"] = s.get("transition", "cut") if j == 0 else cont
            out.append(p)
    return out


VIS_PROPS = {k: v for k, v in SCENE_PROPS.items() if k != "narration"}


def _group_schema(n: int) -> dict:
    """Đúng n cảnh; giới hạn độ dài chữ và mảng con – Qwen hay lặp một trường tới khi bị cắt output."""
    props = {}
    for k, v in VIS_PROPS.items():
        v = dict(v)
        if v["type"] == "string" and "enum" not in v:
            v["maxLength"] = 700 if k == "image_prompt" else 300
        elif v["type"] == "array":
            v["maxItems"] = 12
            v["items"] = {"type": "string", "maxLength": 120}
        props[k] = v
    return {"type": "object", "properties": {"scenes": {
        "type": "array", "minItems": n, "maxItems": n,
        "items": {"type": "object", "properties": props, "required": list(props)}}},
        "required": ["scenes"]}


def _chunk(sents, ci, cfg) -> list[int]:
    """Gom câu liên tiếp thành cảnh ~scene_seconds lời đọc (model nhỏ chọn ranh giới không đáng tin).
    Câu đầu của đoạn mở (hook) đứng riêng một cảnh."""
    target = cfg.scene_seconds * cfg.words_per_minute / 60
    starts, acc = [0], 0
    for i, sent in enumerate(sents):
        if i and (acc >= target or (ci == 0 and i == 1)):
            starts.append(i)
            acc = 0
        acc += _words(sent)
    if len(starts) > 1 and sum(_words(x) for x in sents[starts[-1]:]) < target / 3:
        starts.pop()                    # đuôi quá ngắn → gộp vào cảnh trước
    return starts


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+(?=[\"“'(]?[A-ZÀ-Ỹ0-9])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _visual_rules(cfg) -> str:
    """Luật hình của _scene_rules nhưng bỏ phần độ dài lời (lời đã có sẵn, không được sửa)."""
    return _scene_rules(cfg, 0, 1, []).split("\n", 1)[1].replace("prefer []; ", "")


def _group(ch, ci, sents, ol, vb, cfg, llm):
    starts = _chunk(sents, ci, cfg)
    ends = starts[1:] + [len(sents)]
    numbered = "\n".join(f"Scene {k + 1}: " + " ".join(sents[a:b]) for k, (a, b) in enumerate(zip(starts, ends)))
    scenes = llm.chat([{"role": "system", "content": _sys(cfg)}, {"role": "user", "content":
        f"Video: {ol['title']}. Segment {ci + 1} [{ch['beat']}]: {ch['title']}.\n"
        f"Visual bible (use these looks): {json.dumps(vb, ensure_ascii=False)}\n"
        + (f"Recurring symbol '{ol['callback_symbol']}' appears only at the hook, the biggest insight and the "
           "ending.\n" if ol.get("callback_symbol") else "")
        + f"The narration is FINAL and already split into {len(starts)} scenes:\n{numbered}\n\n"
        f"Give exactly {len(starts)} visuals, in order, one per scene. One picture must cover everything its "
        "narration says. Never rewrite the narration. Per scene:\n" + _visual_rules(cfg)}],
        _group_schema(len(starts)), temperature=0.5, max_tokens=12000)["scenes"]
    return [{**sc, "first_sentence": s0} for sc, s0 in zip(scenes, starts)]


def _group_all(ol, all_sents, vb, cfg, llm, log, progress):
    """Chia cảnh mọi đoạn. Mỗi khe model đang bật (bảng điều khiển) là 1 luồng lấy đoạn kế tiếp khi rảnh;
    khe bị tắt giữa chừng → đoạn trả lại hàng đợi. Hết hàng đợi mà khe CPU còn làm → khe GPU rảnh làm luôn đoạn đó,
    lấy kết quả xong trước. 1 khe bật = tuần tự như cũ."""
    n = len(all_sents)
    chs = ol["chapters"]
    done, errs, busy = {}, {}, {}
    lk = threading.Lock()
    queue = list(range(n))

    def run(slot, ci):
        l2 = copy.copy(llm)
        l2.slot, l2.role = slot, "text"
        dev = llm_mod.slots(cfg.lmstudio)[slot]["device"].upper()
        log(f"Chia cảnh đoạn {ci + 1}/{n} [{chs[ci]['beat']}]: {len(all_sents[ci])} câu (khe {slot + 1} {dev})")
        return _group(chs[ci], ci, all_sents[ci], ol, vb, cfg, l2)

    def worker(slot):
        while True:
            if slot not in llm_mod.active(cfg.lmstudio):
                return
            with lk:
                if len(done) == n:
                    return
                if queue:
                    ci = queue.pop(0)
                else:
                    sl = llm_mod.slots(cfg.lmstudio)
                    # GPU rảnh, còn đoạn đang chạy trên CPU → làm song song, ai xong trước lấy
                    if sl[slot]["device"] != "gpu":
                        return
                    cand = [c for c, ss in busy.items() if c not in done and
                            all(sl[x]["device"] == "cpu" for x in ss)]
                    if not cand:
                        return
                    ci = cand[0]
                busy.setdefault(ci, set()).add(slot)
            try:
                g = run(slot, ci)
                with lk:
                    if ci not in done:
                        done[ci] = g
                        progress(len(done), n)
            except Exception as e:      # noqa: BLE001
                with lk:
                    busy[ci].discard(slot)
                    if ci not in done and not busy[ci]:
                        errs[ci] = e
                        if slot not in llm_mod.active(cfg.lmstudio) and llm_mod.active(cfg.lmstudio):
                            queue.insert(0, ci)          # khe vừa bị tắt → khe khác làm lại
                            errs.pop(ci)
                if slot not in llm_mod.active(cfg.lmstudio):
                    return
                if ci in errs:
                    return

    # đoạn 1 chạy trước trên khe đầu (chắc chắn model đã nạp), sau đó mọi khe cùng chạy
    first = llm_mod.active(cfg.lmstudio)
    first = sorted(first, key=lambda i: llm_mod.slots(cfg.lmstudio)[i]["device"] != "gpu")[0]
    queue.pop(0)
    done[0] = run(first, 0)
    progress(1, n)
    while len(done) < n:
        act = llm_mod.active(cfg.lmstudio)
        ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in act]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        if errs:
            raise next(iter(errs.values()))
        if len(done) < n:
            # mọi luồng đã thoát (khe đổi giữa chừng) mà còn đoạn → chạy lại với các khe đang bật
            with lk:
                queue[:] = [c for c in range(n) if c not in done]
                busy.clear()
            time.sleep(1)
    if len(llm_mod.active(cfg.lmstudio)) > 1:
        log(f"  chia cảnh xong {n} đoạn trên {len(llm_mod.active(cfg.lmstudio))} khe model")
    return done


def _fix_claims(rows, ol, facts, cfg, llm, log):
    """Kịch bản từ NotebookLM: chỉ soát chi tiết không có trong fact sheet → viết lại câu đó thận trọng."""
    review_ = story_quality.check_video(ol, rows, llm)
    ol["story_review"] = review_
    bad = [i for i in review_["issues"] if i["severity"] == "high"
           and i["type"] in ("unsupported_claim", "contradiction")]
    idx = sorted({k for i in bad for k, r in enumerate(rows)
                  if i.get("evidence") and i["evidence"][:40].lower() in r["narration"].lower()})
    if not idx:
        return
    log(f"  {len(idx)} cảnh có chi tiết không có nguồn → viết lại thận trọng")
    res = llm.chat([{"role": "system", "content": _sys(cfg)}, {"role": "user", "content":
        f"Facts: {json.dumps(facts, ensure_ascii=False)}\nProblems: "
        f"{json.dumps([{'type': i['type'], 'evidence': i['evidence']} for i in bad], ensure_ascii=False)}\n"
        "Rewrite each narration below so it only states what the facts support (drop or soften the unsupported "
        "detail), keeping the same style, rhythm and roughly the same length. Return them in order.\n"
        + json.dumps([rows[k]["narration"] for k in idx], ensure_ascii=False)}],
        {**EXPAND_SCHEMA, "properties": {"narrations": {"type": "array", "items": {"type": "string"},
                                                        "minItems": len(idx), "maxItems": len(idx)}}},
        temperature=0.3, max_tokens=8000)
    for k, text in zip(idx, res["narrations"]):
        if text.strip():
            rows[k]["narration"] = text.strip()


def from_narration(nb: dict, facts: list, cfg: Cfg, llm: LLM, log, progress=lambda d, t: None):
    """Kịch bản đã viết sẵn (NotebookLM) → chia cảnh theo ý hình ảnh, giữ nguyên lời đọc."""
    ol = {"title": nb["title"], "core_message": nb.get("core_message", ""), "hook": nb.get("hook", ""),
          "callback_symbol": nb.get("callback_symbol", ""), "engine": "notebooklm", "reviews": []}
    ol["chapters"] = [{"beat": c["beat"], "title": c["title"], "summary": c.get("summary", ""),
                       "key_point": c.get("summary", ""), "key_takeaway": c.get("key_takeaway", ""),
                       "visual_types": [], "start_sec": 0} for c in nb["chapters"]]
    log("Tạo bible nhân vật/đồ vật")
    vb = bible(nb.get("topic", ol["title"]), ol, cfg, llm)
    ol["bible"] = vb
    rules = cfg.bible.get("visual_variety", {})
    canvas_on = (cfg.get("canvas") or {}).get("enabled")
    rows = []
    n = len(nb["chapters"])
    all_sents = [sentences(c["text"]) for c in nb["chapters"]]
    done = None
    # Ưu tiên chia cảnh 1 nhịp siêu tốc qua Gemini Web Bridge nếu extension đang kết nối
    if gemini_bridge.connected():
        try:
            job_topic = nb.get("topic", ol["title"])
            log(f"✨ [Gemini Web] Kích hoạt đạo diễn chia cảnh & prompt ảnh cho dự án: '{job_topic}'...")
            style_rule = (
                "Style: minimalist hand-drawn sketch, notion-style doodle, dark grey pencil line art on warm beige paper "
                "background #F5EBD7, simple clean composition, separated objects, no text, no letters, no numbers, no labels"
            )
            gemini_prompt = (
                f"### [THÔNG BÁO DỰ ÁN MỚI TỪ HỆ THỐNG AUTOVIDEO]\n"
                f"Dự án hiện tại: '{job_topic}'. Mọi hội thoại trước đây đã kết thúc, bạn hãy tập trung 100% vào dự án này.\n"
                f"Vai trò: Bạn là Tổng Đạo Diễn Hình Ảnh & Biên Kịch Video Whiteboard Animation chuyên nghiệp.\n\n"
                f"Nhiệm vụ: Kịch bản gồm {len(nb['chapters'])} chương sau:\n"
                + "\n".join([f"--- CHƯƠNG {i+1} [{ch['beat']} - {ch['title']}]:\n{ch['text']}" for i, ch in enumerate(nb["chapters"])])
                + f"\n\nQUY TẮC ĐẠO DIỄN HÌNH ẢNH BẮT BUỘC (CRITICAL):\n"
                f"1. GIỮ NGUYÊN 100% LỜI ĐỌC: Chia thành các cảnh 15-25 từ tiếng Việt (6-10s đọc), không cắt xén, không tóm tắt.\n"
                f"2. BÁM SÁT THỰC TẾ & NHÂN VẬT THẬT:\n"
                f"   - Nếu lời thoại nhắc đến nhân vật cụ thể (ví dụ: Barack Obama, Elon Musk, Putin, Biden, tướng lĩnh...), 'image_prompt' PHẢI mô tả chân dung đặc trưng nhận diện chân thực của nhân vật đó (ví dụ: 'recognizable portrait doodle of Barack Obama with his iconic features', 'caricature sketch of Elon Musk').\n"
                f"   - Nếu lời thoại nhắc đến quốc gia / tổ chức: PHẢI vẽ cờ chuẩn (ví dụ: 'French flag with distinct vertical blue-white-red stripes', 'NATO compass star emblem', 'Ukrainian flag with blue and yellow bands').\n"
                f"   - Nếu có số liệu/thời gian cụ thể (ví dụ: '15 km', '17 phút', '26.000 đồng', '114 lỗ ngầm'): PHẢI đưa các con số hoặc biểu đồ cột/đồng hồ chỉ rõ số liệu đó vào bố cục tranh trực quan.\n"
                f"3. ĐA DẠNG NỘI DUNG TRONG MỘT FRAME: Mỗi cảnh PHẢI có từ 3 đến 5 chi tiết liên đới với nhau (tiền cảnh, trung cảnh, hậu cảnh) minh họa đầy đủ ý tứ của câu thoại, không vẽ 1 đồ vật đơn điệu.\n"
                f"4. TUÂN THỦ PHONG CÁCH WHITEBOARD: Tất cả mô tả trong 'image_prompt' phải là tiếng Anh và kết thúc với: '{style_rule}'.\n"
                f"5. Bắt buộc trả về duy nhất một khối JSON thuần hợp lệ (bắt đầu bằng {gemini_bridge.START} và kết thúc bằng {gemini_bridge.END}), không kèm lời chào:\n"
                f"{{\n"
                f'  "project_confirmed": "{job_topic}",\n'
                f'  "chapters": [\n'
                f'    {{\n'
                f'      "chapter": 1,\n'
                f'      "scenes": [\n'
                f'        {{\n'
                f'          "narration": "câu lời đọc...",\n'
                f'          "visual_concept": "ý tưởng vẽ chi tiết bám sát thực tế...",\n'
                f'          "image_prompt": "English detailed prompt with real figures/flags/numbers, {style_rule}"\n'
                f'        }}\n'
                f'      ]\n'
                f'    }}\n'
                f'  ]\n'
                f"}}"
            )
            raw = gemini_bridge.ask(nb.get("job_id", 0), "split_scenes", gemini_prompt, deadline=time.time() + 240)
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                data = json.loads(m.group(0))
                gem_rows = []
                for ch_data in data.get("chapters", []):
                    ci = ch_data.get("chapter", 1) - 1
                    ch_info = ol["chapters"][min(ci, len(ol["chapters"]) - 1)]
                    for sc in ch_data.get("scenes", []):
                        narr = sc.get("narration", "").strip()
                        if narr:
                            p_txt = sc.get("image_prompt", "").strip()
                            if p_txt and len(p_txt.split()) < 12:
                                p_txt = f"{p_txt}, {style_rule}"
                            gem_rows.append({
                                "chapter": ci,
                                "purpose": ch_info.get("beat", "point"),
                                "narration": narr,
                                "visual_type": "object",
                                "visual_concept": sc.get("visual_concept", narr[:60]),
                                "image_prompt": p_txt,
                                "prompt_by": "gemini",
                                "camera": "static",
                                "transition": "cut",
                                "emotion": "calm",
                                "pause_before": 0.0,
                                "data": json.dumps({"visual_concept": sc.get("visual_concept", narr[:60]),
                                                   "image_prompt": p_txt}, ensure_ascii=False)
                            })
                if len(gem_rows) >= len(nb["chapters"]) * 3:
                    log(f"  ✔ Gemini Web đã xác nhận dự án '{data.get('project_confirmed', job_topic)}' và hoàn thành {len(gem_rows)} cảnh + prompt ảnh trong 1 nhịp!")
                    _fix_claims(gem_rows, ol, facts, cfg, llm, log)
                    return ol, gem_rows
        except Exception as e:
            log(f"⚠ Gemini Web chia cảnh lỗi ({e}) → chuyển về LM Studio chia cảnh")

    done = _group_all(ol, all_sents, vb, cfg, llm, log, progress)
    for ci, c in enumerate(nb["chapters"]):
        ch = ol["chapters"][ci]
        sents = all_sents[ci]
        groups = done[ci]
        starts, scenes = [], []
        for g in sorted(groups, key=lambda g: g["first_sentence"]):
            s0 = int(g["first_sentence"])
            if 0 <= s0 < len(sents) and (not starts or s0 > starts[-1]):
                starts.append(s0)
                scenes.append(g)
        if not starts:
            starts, scenes = [0], [groups[0]]
        starts[0] = 0
        # cảnh quá vụn (< ~nửa thời lượng chuẩn) → gộp vào cảnh trước, giữ hình của phần dài hơn
        min_w = round(float((cfg.get("script") or {}).get("min_scene_seconds", cfg.scene_seconds * 0.6))
                      * cfg.words_per_minute / 60)
        wc = lambda j: _words(" ".join(sents[starts[j]:(starts[j + 1] if j + 1 < len(starts) else len(sents))]))  # noqa: E731
        def merge(a):        # gộp cảnh a+1 vào cảnh a
            if wc(a + 1) > wc(a):
                scenes[a] = scenes[a + 1]
            del starts[a + 1], scenes[a + 1]
        j = 1 if ci == 0 else 0      # câu hook được đứng riêng
        while j < len(starts) - 1:
            if wc(j) < min_w:
                merge(j)
            else:
                j += 1
        if len(starts) > 1 and wc(len(starts) - 1) < min_w:
            merge(len(starts) - 2)
        for j, g in enumerate(scenes):
            end = starts[j + 1] if j + 1 < len(starts) else len(sents)
            s = {k: g[k] for k in VIS_PROPS}
            s["narration"] = " ".join(sents[starts[j]:end])
            s["chapter"], s["beat"] = ci, ch["beat"]
            if j == 0 and ci > 0:
                s["transition"] = "chapter"
            elif s.get("transition") in ("chapter", "draw_over") and not canvas_on:
                s["transition"] = "cut"
            rows.append(s)
        log(f"  → {len(scenes)} cảnh")
        bad_v = [i for i in variety.violations(rows, rules, cfg.scene_seconds) if i >= len(rows) - len(scenes)]
        if bad_v:
            _fix_variety(rows, bad_v, cfg, llm, log)
    if facts:
        log("Soát dữ kiện so với fact sheet")
        _fix_claims(rows, ol, facts, cfg, llm, log)
    # cảnh quá dài (> limit giây) → nhiều hình nối tiếp, tách theo câu, không đổi chữ
    rows = split_long(rows, cfg, llm, log, limit=float((cfg.get("script") or {}).get("max_scene_seconds", 45)))
    total_words = sum(_words(r["narration"]) for r in rows)
    log(f"Kịch bản: {len(rows)} cảnh, {total_words} chữ ≈ {total_words / cfg.words_per_minute:.1f} phút")
    return ol, rows


def review(ol, ci, scenes, cfg, llm):
    text = "\n".join(s["narration"] for s in scenes)
    res = llm.chat([{"role": "user", "content":
        f"You are a YouTube retention analyst. Video: {ol['title']}. Segment {ci + 1}: "
        f"{ol['chapters'][ci]['title']}.\nNarration:\n{text}\n\nScore 0-1: boredom (higher = more boring), "
        "repetition (higher = more repeated ideas/phrases), information_density, retention (likelihood a "
        "viewer keeps watching). In 'fix' give concrete instructions to improve (e.g. insert a concrete "
        "example, cut exposition, add a question)."}], REVIEW_SCHEMA, temperature=0.1)
    # Qwen hay chấm thang 0-10 (5.00, 6.00…) dù đề yêu cầu 0-1 → đưa về 0-1
    for k in ("boredom", "repetition", "information_density", "retention"):
        v = float(res.get(k, 0) or 0)
        res[k] = round(v / 10 if v > 1 else v, 3)
    return res


def build(topic: str, facts: list, cfg: Cfg, llm: LLM, log, progress=lambda done, total: None):
    q = cfg.bible["quality"]
    n_scenes = max(3, round(cfg.target_minutes * 60 / cfg.scene_seconds))
    n_chapters = max(1, min(14, round(n_scenes / 8)))
    words = round(cfg.scene_seconds * cfg.words_per_minute / 60)
    hook_text = hook(topic, facts, cfg, llm, log)
    log(f"Video Planner: {n_chapters} đoạn, ~{n_scenes} cảnh, ~{words} chữ/cảnh")
    vp = planner.plan(topic, facts, hook_text, n_chapters, _sys(cfg), cfg, llm)
    ol = {k: v for k, v in vp.items() if k != "beatmap"}
    ol["chapters"] = vp["beatmap"]
    n_chapters = len(ol["chapters"])
    log("Beatmap: " + " → ".join(f"{b['start_sec'] // 60}:{b['start_sec'] % 60:02} {b['beat']}"
                                 for b in ol["chapters"]))
    log("Tạo bible nhân vật/đồ vật")
    vb = bible(topic, ol, cfg, llm)
    ol["hook"], ol["bible"], ol["reviews"] = hook_text, vb, []

    per = planner.scenes_per_segment(ol["chapters"], n_scenes)
    rules = cfg.bible.get("visual_variety", {})
    rows, prev = [], ""
    canvas_on = (cfg.get("canvas") or {}).get("enabled")
    for ci in range(n_chapters):
        progress(ci, n_chapters)
        ch = ol["chapters"][ci]
        log(f"Viết đoạn {ci + 1}/{n_chapters} [{ch['beat']}] ({per[ci]} cảnh): {ch['title']}")
        covered = _covered(rows, ol, ci)
        scenes = _chapter(ol, ci, per[ci], hook_text, prev, facts, vb, cfg, llm, words, covered=covered)
        rv = review(ol, ci, scenes, cfg, llm)
        arc = story_quality.check_chapter(ol, ci, scenes, facts, llm)
        todo = story_quality.actionable(arc["issues"])
        recap = ch["beat"] in ("recap", "cta")      # tóm tắt vốn nhắc lại ý cũ – không phạt lặp
        bad = (rv["retention"] < q["retention_min"] or rv["boredom"] > q["max_boredom"]
               or (rv["repetition"] > q["max_repetition"] and not recap) or bool(todo))
        log(f"  retention {rv['retention']:.2f} · boredom {rv['boredom']:.2f} · lặp {rv['repetition']:.2f}"
            f" · lỗi mạch {len(todo)}" + (" → VIẾT LẠI" if bad else " ✓"))
        for i in todo:
            log(f"    - [{i['severity']}] {i['type']} → {i['repair']}: \"{i['evidence'][:100]}\"")
        budget = int((cfg.bible.get("retry") or {}).get("script", 2))
        for _ in range(budget if bad else 0):
            fix = (rv["fix"] + " " + story_quality.repair_instruction(todo)).strip()
            scenes2 = _chapter(ol, ci, per[ci], hook_text, prev, facts, vb, cfg, llm, words, fix=fix,
                               covered=covered)
            rv2 = review(ol, ci, scenes2, cfg, llm)
            arc2 = story_quality.check_chapter(ol, ci, scenes2, facts, llm)
            todo2 = story_quality.actionable(arc2["issues"])
            better = _quality(rv2, todo2) >= _quality(rv, todo)
            log(f"  sau viết lại: retention {rv2['retention']:.2f} · lỗi mạch {len(todo2)}"
                + (" → dùng bản mới" if better else " → giữ bản cũ (tốt hơn)"))
            if better:
                scenes, rv, arc, todo = scenes2, rv2, arc2, todo2
            if not todo and rv["retention"] >= q["retention_min"]:
                break
        rv["arc"] = arc
        scenes = _ensure_length(scenes, words, cfg, llm, log)
        ol["reviews"].append(rv)
        for j, s in enumerate(scenes):
            s["chapter"] = ci
            s["beat"] = ch["beat"]
            if j == 0 and ci > 0:
                s["transition"] = "chapter"
            elif s.get("transition") == "draw_over" and not canvas_on:
                s["transition"] = "cut"
            rows.append(s)
            prev += " " + s["narration"]
        bad_v = [i for i in variety.violations(rows, rules, cfg.scene_seconds) if i >= len(rows) - len(scenes)]
        if bad_v:
            _fix_variety(rows, bad_v, cfg, llm, log)
    log("Soát mạch toàn video")
    whole = story_quality.check_video(ol, rows, llm)
    ol["story_review"] = whole
    high = [i for i in whole["issues"] if i["severity"] == "high"]
    for ci in sorted({i["chapter"] - 1 for i in high if 0 < i["chapter"] <= n_chapters}):
        mine = [i for i in high if i["chapter"] - 1 == ci]
        log(f"  Đoạn {ci + 1} lỗi nặng ({', '.join(i['type'] for i in mine)}) → viết lại")
        before = " ".join(r["narration"] for r in rows if r["chapter"] < ci)
        new = _chapter(ol, ci, per[ci], hook_text, before, facts, vb, cfg, llm, words,
                       fix=story_quality.repair_instruction(story_quality.actionable(mine) or mine),
                       covered=_covered(rows, ol, ci))
        new = _ensure_length(new, words, cfg, llm, log)
        for j, s in enumerate(new):
            s["chapter"], s["beat"] = ci, ol["chapters"][ci]["beat"]
            if j == 0 and ci > 0:
                s["transition"] = "chapter"
            elif s.get("transition") == "draw_over" and not canvas_on:
                s["transition"] = "cut"
        rows = [r for r in rows if r["chapter"] < ci] + new + [r for r in rows if r["chapter"] > ci]
    if high:
        whole2 = story_quality.check_video(ol, rows, llm)
        ol["story_review"] = whole2
        ol["blocking_issues"] = [{"chapter": i["chapter"], "detail": f"{i['type']}: {i['evidence'][:200]}"}
                                 for i in whole2["issues"] if i["severity"] == "high"
                                 and i["type"] in ("unsupported_claim", "contradiction")]
        if ol["blocking_issues"]:
            log(f"  Còn {len(ol['blocking_issues'])} lỗi nội dung nghiêm trọng → Hard Gate sẽ chặn xuất")
    rows = split_long(rows, cfg, llm, log)
    total_words = sum(_words(r["narration"]) for r in rows)
    log(f"Kịch bản: {len(rows)} cảnh, {total_words} chữ ≈ {total_words / cfg.words_per_minute:.1f} phút")
    return ol, rows
