import re
from pathlib import Path

MAX_WORDS = 10


def _chunks(text: str) -> list[str]:
    out = []
    for sent in re.split(r"(?<=[.!?…])\s+", text.strip()):
        parts = re.split(r"(?<=[,;:—])\s+", sent)
        buf = []
        for p in parts:
            for w in p.split():
                buf.append(w)
                if len(buf) >= MAX_WORDS:
                    out.append(" ".join(buf))
                    buf = []
            if len(buf) >= MAX_WORDS // 2:
                out.append(" ".join(buf))
                buf = []
        if buf:
            if out and len(buf) < 4 and len(out[-1].split()) + len(buf) <= MAX_WORDS + 3:
                out[-1] += " " + " ".join(buf)
            else:
                out.append(" ".join(buf))
    return [c for c in out if c]


MAX_CHARS = 42
MAX_CPS = 17


def _chunks_chars(text: str) -> list[str]:
    """Tách dòng ≤ MAX_CHARS, ưu tiên sau dấu câu, không ngắt giữa hai từ viết hoa liền nhau (tên riêng)."""
    words = text.split()
    out, cur = [], []
    for i, w in enumerate(words):
        cand = " ".join(cur + [w])
        if cur and len(cand) > MAX_CHARS:
            prev = cur[-1]
            if prev[:1].isupper() and w[:1].isupper() and len(cur) > 1:
                out.append(" ".join(cur[:-1]))
                cur = [prev, w]
                continue
            out.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
        if re.search(r"[.!?…;:]$", w) and len(" ".join(cur)) >= MAX_CHARS * 0.5:
            out.append(" ".join(cur))
            cur = []
    if cur:
        if out and len(cur) <= 2 and len(out[-1]) + len(" ".join(cur)) <= MAX_CHARS + 8:
            out[-1] += " " + " ".join(cur)
        else:
            out.append(" ".join(cur))
    return out


def cues_from_words(scenes, offsets_ms):
    """Thời gian lấy từ timestamp từng từ (Whisper) của audio cuối cùng; thiếu thì chia theo ký tự."""
    res = []
    for s, off in zip(scenes, offsets_ms):
        words = s.get("asr_words") or []
        parts = _chunks_chars(s["narration"])
        if len(words) < 2:
            res += cues([s], [off + (s.get("lead_ms") or 0)])
            continue
        total = sum(len(p) for p in parts) or 1
        n = len(words)
        pos = 0
        for p in parts:
            a = min(n - 1, int(pos / total * n))
            pos += len(p)
            b = min(n - 1, max(a, int(pos / total * n) - 1))
            res.append((off + int(words[a][1] * 1000), off + int((words[b][2] or words[b][1]) * 1000), p,
                        s.get("emphasis", "")))
    return _fix_timing(res)


def _fix_timing(cs):
    out = []
    for i, (a, b, t, e) in enumerate(cs):
        nxt = cs[i + 1][0] if i + 1 < len(cs) else b + 3000
        need = int(len(t) / MAX_CPS * 1000)
        b = max(b, min(a + need, nxt - 40))
        if out and a < out[-1][1]:
            a = out[-1][1] + 10
        out.append((a, max(b, a + 300), t, e))
    return out


def cues(scenes, offsets_ms):
    """Chia lời mỗi cảnh thành dòng phụ đề ngắn, thời gian tỉ lệ theo số ký tự trong phần lời nói."""
    res = []
    for s, off in zip(scenes, offsets_ms):
        speech = s.get("speech_ms") or s["duration_ms"]
        parts = _chunks(s["narration"])
        total = sum(len(p) for p in parts) or 1
        t = off
        for p in parts:
            d = int(speech * len(p) / total)
            res.append((t, t + d, p, s.get("emphasis", "")))
            t += d
    return res


def _ts(ms, sep=","):
    h, ms = divmod(int(ms), 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02}{sep}{ms:03}"


def write_srt(cs, out: Path):
    lines = []
    for i, (a, b, text, _) in enumerate(cs):
        lines += [str(i + 1), f"{_ts(a)} --> {_ts(b)}", text, ""]
    out.write_text("\n".join(lines), encoding="utf-8")


def write_ass(cs, out: Path, w=1920, h=1080):
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, "
            "Outline, Shadow, Alignment, MarginV\n"
            "Style: Default,Arial,54,&H00303030,&H00F5EBD7,&H80F5EBD7,1,1,3,0,2,60\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Text\n")

    def ts(ms):
        h_, ms = divmod(int(ms), 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h_}:{m:02}:{s:02}.{ms // 10:02}"

    rows = []
    for a, b, text, emph in cs:
        t = text.replace("{", "(").replace("}", ")")
        if emph and emph.lower() in t.lower():
            i = t.lower().index(emph.lower())
            t = t[:i] + "{\\c&H2A5BE0&}" + t[i:i + len(emph)] + "{\\c&H303030&}" + t[i + len(emph):]
        rows.append(f"Dialogue: 0,{ts(a)},{ts(b)},Default,{t}")
    out.write_text(head + "\n".join(rows) + "\n", encoding="utf-8-sig")
