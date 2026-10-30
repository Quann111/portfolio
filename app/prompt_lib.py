"""Thư viện prompt Nano Banana Pro (skill YouMind-OpenLab/nano-banana-pro-prompts-recommend-skill, MIT) –
lấy vài prompt mẫu hợp phong cách whiteboard/ink làm VÍ DỤ cho Qwen khi viết image_prompt (few-shot), để ảnh
giàu ý và khớp lời hơn. Không gọi mạng; thiếu thư viện thì dùng mẫu dựng sẵn."""
import json
import re
from functools import lru_cache

from .config import ROOT

REF = ROOT / "skills" / "nano-banana-pro-prompts" / "references"
FILES = ("comic-storyboard.json", "infographic-edu-visual.json", "others.json")
STYLE_RE = re.compile(r"whiteboard|new yorker|ink sketch|pen and ink|line art|hand-drawn|doodle|marker sketch", re.I)
BAD_RE = re.compile(r"reference image|photo|photoreal|3d|chinese text|text \"|title|label|logo|product|dense|"
                    r"ultra-detailed|cross-hatch|intricate|obsessive|coloring page|mandala", re.I)
PREFER_RE = re.compile(r"whiteboard|new yorker|explainer|metaphor|educational", re.I)

# nguyên tắc rút ra từ các mẫu tốt nhất của thư viện (Clean Whiteboard Animation, New Yorker cartoon)
PRINCIPLES = ("Tell the idea with ONE clear visual metaphor or a concrete moment, simple but not simplistic; "
              "characters show emotion through face and pose; a lightly sketched setting tells where we are; "
              "foreground subject + midground action + faint background; small details that match the narration's "
              "nouns (e.g. football kit, stadium stands, ball, trophy); motion lines or arrows only when something "
              "moves or changes")

FALLBACK = [
    "Clean whiteboard animation illustration: a determined young footballer mid-stride on a sketched pitch, "
    "ball at his feet, motion lines behind him, faint stadium stands in the background, a coach on the left "
    "pointing forward with a proud smile, bold hand-drawn outlines with slight sketch imperfections, red accent "
    "on the jersey only",
    "New Yorker style ink cartoon metaphor: a tall lonely mountain peak holding a single star, while to the right a "
    "wide staircase of many small steps leads a crowd of climbers steadily upward, relaxed confident lines, black "
    "and white with a single vermilion accent on the star",
]


@lru_cache(maxsize=1)
def _library() -> list[str]:
    out = []
    for f in FILES:
        p = REF / f
        if not p.exists():
            continue
        try:
            for x in json.loads(p.read_text(encoding="utf-8")):
                c = re.sub(r"\{argument name=\"[^\"]*\" default=\"([^\"]*)\"\}", r"\1", x.get("content") or "")
                c = re.sub(r"\s+", " ", c).strip()
                if 150 < len(c) < 900 and STYLE_RE.search(c) and not BAD_RE.search(c):
                    out.append(c)
        except (OSError, ValueError):
            continue
    return out


TEMPLATE_IDS = (17504, 2478)    # "Clean Whiteboard Animation Illustration", "New Yorker Cartoon Style" – hợp kênh nhất


@lru_cache(maxsize=1)
def templates() -> list[str]:
    """Phần phong cách của 2 mẫu chọn lọc (bỏ dòng về chữ/tiêu đề/ảnh tham chiếu)."""
    out = []
    for f in FILES:
        p = REF / f
        if not p.exists():
            continue
        for x in json.loads(p.read_text(encoding="utf-8")):
            if x.get("id") in TEMPLATE_IDS and x.get("id") not in [o[0] for o in out]:
                c = re.sub(r"\{argument name=\"[^\"]*\" default=\"([^\"]*)\"\}", r"\1", x.get("content") or "")
                parts = re.split(r"(?<=[.!?])\s+|\n+", c.replace("**", ""))
                keep = [s.strip(" *#") for s in parts if s.strip(" *#")
                        and not re.search(r"reference image|person.s|title|font|chinese|2k|ppt", s, re.I)]
                txt = re.sub(r"\s+", " ", " ".join(keep)).strip()
                if txt:
                    out.append((x["id"], txt[:500]))
    return [t for _, t in out]


def examples(k: int = 2) -> list[str]:
    """k prompt cảnh mẫu kiểu whiteboard/ink, không chữ."""
    return FALLBACK[:k]


def guide() -> str:
    ex = "\n".join(f"  • {e}" for e in templates() + examples(2))
    return (f"Image prompt craft ({PRINCIPLES}). Examples of strong prompts in the right spirit (adapt the "
            f"craft, not the subject):\n{ex}\n")
