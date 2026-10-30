import json

from .config import Cfg
from .llm import LLM

BEATS = ["hook", "promise", "context", "question", "explain", "example", "revelation", "surprise",
         "counter_intuitive", "case_study", "compare", "mistake", "insight", "recap", "cta"]
VISUAL_TYPES = ["diagram", "timeline", "comparison", "flowchart", "character", "map", "process",
                "before_after", "cause_effect", "list", "metaphor", "quote", "number_stat", "closeup",
                "wide_scene"]


def plan_schema(n_chapters: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "goal": {"type": "string"},
            "audience": {"type": "string"},
            "knowledge_level": {"type": "string"},
            "core_message": {"type": "string"},
            "key_points": {"type": "array", "items": {"type": "string"}, "minItems": 8, "maxItems": 15},
            "interest_curve": {"type": "string"},
            "visual_strategy": {"type": "string"},
            "callback_symbol": {"type": "string"},
            "beatmap": {"type": "array", "minItems": n_chapters, "maxItems": n_chapters, "items": {
                "type": "object",
                "properties": {
                    "start_sec": {"type": "integer"},
                    "beat": {"type": "string", "enum": BEATS},
                    "title": {"type": "string"},
                    "key_point": {"type": "string"},
                    "summary": {"type": "string"},
                    "key_takeaway": {"type": "string"},
                    "visual_types": {"type": "array", "items": {"type": "string", "enum": VISUAL_TYPES}},
                },
                "required": ["start_sec", "beat", "title", "key_point", "summary", "key_takeaway",
                             "visual_types"]}},
        },
        "required": ["title", "goal", "audience", "knowledge_level", "core_message", "key_points",
                     "interest_curve", "visual_strategy", "callback_symbol", "beatmap"],
    }


def plan(topic: str, facts: list, hook_text: str, n_chapters: int, sys_prompt: str, cfg: Cfg, llm: LLM, target_minutes: float | None = None) -> dict:
    t_min = target_minutes or cfg.target_minutes
    total = t_min * 60
    p = llm.chat([{"role": "system", "content": sys_prompt}, {"role": "user", "content":
        f"Topic: {topic}\nChosen hook: {hook_text}\nFacts: {json.dumps(facts, ensure_ascii=False)}\n\n"
        f"Plan the WHOLE {t_min}-minute video ({total} seconds) before any script is written:\n"
        "- goal, audience, knowledge_level, core_message (one sentence)\n"
        "- 8-15 key_points in the order they will be revealed\n"
        "- interest_curve: how curiosity/tension rises and pays off across the video (where the biggest "
        "insight lands, where surprises are placed)\n"
        "- visual_strategy: how visuals stay varied (which visual types where), chosen for explanation, not "
        "for variety's sake\n"
        "- callback_symbol: ONE simple drawable symbol (ENGLISH, e.g. 'a small glowing lightbulb') that "
        "represents the core message, shown at the hook and brought back at the biggest insight and ending\n"
        f"- beatmap: exactly {n_chapters} segments with increasing start_sec from 0, the last ending before "
        f"{total}s. Use rhythm like hook → promise → context → revelation → example → counter_intuitive → "
        "case_study → insight → recap → cta (adapt to length). Each segment: one key_point, summary, "
        "key_takeaway, and 2-4 visual_types suited to it. Segments must not overlap in content and each "
        "ends with a tease for the next."}], plan_schema(n_chapters), temperature=0.7, max_tokens=10000)
    bm = sorted(p["beatmap"], key=lambda b: b["start_sec"])
    bm[0]["start_sec"] = 0
    last = bm[-1]["start_sec"]
    if len(bm) > 1 and last < total * 0.6:
        # LLM hay ghi mốc theo phút/thang sai (0→9 cho video 20') → co giãn để trải đều cả video
        scale = (total * (len(bm) - 1) / len(bm)) / max(1, last)
        for b in bm:
            b["start_sec"] = int(b["start_sec"] * scale)
    for i, b in enumerate(bm):
        end = bm[i + 1]["start_sec"] if i + 1 < len(bm) else total
        b["duration_sec"] = max(20, end - b["start_sec"])
    p["beatmap"] = bm
    return p


def scenes_per_segment(bm: list, n_scenes: int) -> list[int]:
    """Số cảnh mỗi đoạn theo thời lượng beatmap; mỗi đoạn ≤ 2× trung bình, recap/cta ≤ trung bình."""
    total = sum(b["duration_sec"] for b in bm)
    avg = n_scenes / len(bm)
    caps = [max(1, round(avg if b["beat"] in ("recap", "cta") else 2 * avg)) for b in bm]
    raw = [min(caps[i], max(1, round(n_scenes * b["duration_sec"] / total))) for i, b in enumerate(bm)]
    while sum(raw) > n_scenes:
        raw[raw.index(max(raw))] -= 1
    while sum(raw) < n_scenes:
        room = [i for i in range(len(raw)) if raw[i] < caps[i]] or list(range(len(raw)))
        i = min(room, key=lambda k: raw[k] / max(1, caps[k]))
        raw[i] += 1
    return raw
