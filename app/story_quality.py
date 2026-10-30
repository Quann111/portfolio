"""Narrative Arc Validator: lỗi cụ thể có bằng chứng + cách sửa, thay cho một điểm retention chung."""
import re

from .llm import LLM

REPAIRS = ["add_concrete_example", "remove_redundant", "add_transition", "strengthen_payoff",
           "answer_the_question", "add_surprise", "fix_fact", "none"]
ISSUE_TYPES = ["abstract_explanation", "repeated_information", "weak_link", "no_payoff", "unanswered_question",
               "no_example", "unsupported_claim", "contradiction", "broken_promise", "flat_ending"]

ISSUE = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ISSUE_TYPES},
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "evidence": {"type": "string"},
        "repair": {"type": "string", "enum": REPAIRS},
    },
    "required": ["type", "severity", "evidence", "repair"],
}

CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "viewer_question": {"type": "string"},
        "how_answered": {"type": "string"},
        "concrete_example": {"type": "string"},
        "surprise_or_consequence": {"type": "string"},
        "link_to_next": {"type": "string"},
        "issues": {"type": "array", "items": ISSUE, "maxItems": 8},
    },
    "required": ["viewer_question", "how_answered", "concrete_example", "surprise_or_consequence",
                 "link_to_next", "issues"],
}

VIDEO_SCHEMA = {
    "type": "object",
    "properties": {
        "promise": {"type": "string"},
        "promise_kept": {"type": "boolean"},
        "progression_ok": {"type": "boolean"},
        "climax_prepared": {"type": "boolean"},
        "memorable_ending": {"type": "boolean"},
        "contradictions": {"type": "array", "items": {"type": "string"}},
        "issues": {"type": "array", "maxItems": 10, "items": {**ISSUE, "properties": {**ISSUE["properties"],
                                                                                    "chapter": {"type": "integer"}},
                                                             "required": ISSUE["required"] + ["chapter"]}},
    },
    "required": ["promise", "promise_kept", "progression_ok", "climax_prepared", "memorable_ending",
                 "contradictions", "issues"],
}


def _fact_lines(facts: list) -> str:
    return "\n".join(f"- {f['claim']} ({'verified' if f.get('verified') else 'cautious'})" for f in facts)


def _in_text(evidence: str, text: str) -> bool:
    """Bằng chứng phải thật sự nằm trong lời đọc (LLM hay tự chế câu 'bằng chứng')."""
    e = re.sub(r"\W+", " ", evidence.lower()).strip()
    t = re.sub(r"\W+", " ", text.lower())
    if not e:
        return False
    if e[:50] in t:
        return True
    words = re.findall(r"\w{3,}", e)
    return bool(words) and sum(w in t for w in words) / len(words) >= 0.8


def check_chapter(ol: dict, ci: int, scenes: list, facts: list, llm: LLM) -> dict:
    text = "\n".join(f"[{i}] {s['narration']}" for i, s in enumerate(scenes))
    nxt = ol["chapters"][ci + 1]["title"] if ci + 1 < len(ol["chapters"]) else "(end of video)"
    res = llm.chat([{"role": "user", "content":
        f"You are a strict story editor. Video: {ol['title']}. Segment {ci + 1}: {ol['chapters'][ci]['title']}. "
        f"Next segment: {nxt}.\nFACT SHEET (everything here is allowed; 'cautious' ones need careful wording):\n"
        f"{_fact_lines(facts)}\n\nNarration:\n{text}\n\n"
        "Answer: what question is the viewer holding; how this segment answers it; the concrete example used "
        "(or 'none'); the surprise or consequence that makes them keep watching (or 'none'); how it leads into "
        "the next segment (or 'none'). Then list at most 8 REAL problems, each quoting one exact sentence FROM "
        "THE NARRATION above as evidence. unsupported_claim ONLY for a number, feature, event or quote that is "
        "NOT in the fact sheet at all – a claim that matches a fact-sheet line is supported. Do not invent "
        "problems; an empty list is fine."}], CHAPTER_SCHEMA, temperature=0.1, max_tokens=6000)
    res["issues"] = [i for i in res["issues"] if _in_text(i["evidence"], text)
                     and not (i["type"] == "unsupported_claim" and _matches_fact(i["evidence"], facts))]
    return res


def _matches_fact(sentence: str, facts: list) -> bool:
    """Câu bị chê 'không có nguồn' nhưng thật ra khớp một ý trong fact sheet → không phải lỗi.
    Điều kiện: mọi con số trong câu có trong fact đó, và ≥ 55% từ 'đặc trưng' trùng (bỏ từ có ở > 40% fact,
    như tên chủ đề, 'người dùng')."""
    texts = [(f.get("claim", "") + " " + f.get("evidence", "")).lower() for f in facts]
    sets = [set(re.findall(r"\w{3,}", t)) for t in texts]
    if not sets:
        return False
    common = {w for w in set().union(*sets) if sum(w in s for s in sets) > 0.4 * len(sets)}
    words = set(re.findall(r"\w{3,}", sentence.lower())) - common
    nums = re.findall(r"\d+(?:[.,]\d+)?", sentence)
    if len(words) < 2:
        return False
    for t, fw in zip(texts, sets):
        if nums and not all(n in t for n in nums):
            continue
        if len(words & fw) / len(words) >= 0.55:
            return True
    return False


def check_video(ol: dict, rows: list, llm: LLM) -> dict:
    per = {}
    for r in rows:
        per.setdefault(r["chapter"], []).append(r["narration"])
    text = "\n".join(f"## Segment {c + 1}: " + " ".join(v) for c, v in sorted(per.items()))
    res = llm.chat([{"role": "user", "content":
        f"Review the whole script of '{ol['title']}' (core message: {ol.get('core_message', '')}).\n{text}\n\n"
        "What does the opening promise? Is it kept? Does each segment move forward? Is the biggest insight "
        "prepared before it lands? Is the ending memorable? List contradictions between segments. List only "
        "real issues (at most 10), each quoting one exact sentence from the script as evidence and its segment "
        "number (1-based)."}], VIDEO_SCHEMA, temperature=0.1, max_tokens=6000)
    res["issues"] = [i for i in res["issues"]
                     if _in_text(i["evidence"], " ".join(per.get(i["chapter"] - 1, [])) or text)]
    return res


FORCED_REPAIR = {"unsupported_claim": "fix_fact", "contradiction": "fix_fact", "repeated_information":
                 "remove_redundant"}


def actionable(issues: list) -> list:
    """Lỗi đáng sửa: bỏ low/none, ép đúng cách sửa theo loại lỗi, gộp lỗi trùng câu bằng chứng."""
    seen, out = set(), []
    for i in issues:
        if i["severity"] not in ("medium", "high"):
            continue
        i = {**i, "repair": FORCED_REPAIR.get(i["type"], i["repair"])}
        if i["repair"] == "none":
            continue
        key = (i["type"], i["evidence"].strip().lower()[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out


REPAIR_TEXT = {
    "fix_fact": "remove this claim or rephrase it cautiously using ONLY the fact sheet – never keep invented details",
    "remove_redundant": "delete the repetition and replace it with a NEW point",
    "add_concrete_example": "add one concrete, real example from the fact sheet",
    "add_transition": "add a clear bridge to the next segment",
    "strengthen_payoff": "make the payoff / answer explicit",
    "answer_the_question": "answer the question that was raised",
    "add_surprise": "add a surprising but true detail from the fact sheet",
}


def repair_instruction(issues: list) -> str:
    return " ".join(f"[{REPAIR_TEXT.get(i['repair'], i['repair'])}] ({i['type']}) at: \"{i['evidence'][:160]}\"."
                    for i in issues)
