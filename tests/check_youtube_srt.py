"""Kiểm tra cặp phụ đề YouTube của một job: số cue VI = EN, thời gian khớp, EN không còn tiếng Việt.

    .venv\\Scripts\\python.exe tests\\check_youtube_srt.py 73
"""
import re
import sys
from collections import Counter
from pathlib import Path

for _s in (sys.stdout, sys.stderr):          # console Windows mặc định cp1252 → vỡ khi in tiếng Việt
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import subtitles, youtube  # noqa: E402

BOM = b"\xef\xbb\xbf"
LINE_MAX = 50                                # 1 dòng phụ đề YouTube nên ≤ 50 ký tự


def parse(p: Path) -> list:
    t = p.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    out = []
    for blk in re.split(r"\n\s*\n", t.strip()):
        lines = blk.split("\n")
        i = next((k for k, ln in enumerate(lines) if "-->" in ln), None)
        if i is None:
            continue
        out.append((lines[i].strip(), "\n".join(x.strip() for x in lines[i + 1:] if x.strip())))
    return out


def ms(stamp: str) -> int:
    h, m, rest = stamp.split(":")
    s, frac = rest.split(",")
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac)


def main(jid: int) -> int:
    f = youtube.folder(jid)
    vi, en = parse(f / "subtitles_vi.srt"), parse(f / "subtitles_en.srt")
    checks = []

    def ck(name, cond, extra=""):
        checks.append(cond)
        print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""))

    print(f"{f}\ncue VI={len(vi)}  EN={len(en)}\n")
    ck("số cue VI = EN", len(vi) == len(en), f"{len(vi)}/{len(en)}")
    ck("thời gian giống hệt", all(a[0] == b[0] for a, b in zip(vi, en)))
    bad = [i for i, (_, s) in enumerate(en) if subtitles._still_vi(s)]
    ck("EN không còn là tiếng Việt", not bad, f"{len(bad)} dòng {bad[:8]}")
    starts = [ms(t.split(" --> ")[0]) for t, _ in vi]
    ck("thời gian tăng dần", all(a <= b for a, b in zip(starts, starts[1:])))
    ck("không cue rỗng", all(s.strip() for _, s in vi + en))
    ck("cue ≤ 2 dòng", all(len(s.split("\n")) <= 2 for _, s in vi + en))
    too_long = [(i, ln) for i, (_, s) in enumerate(vi + en) for ln in s.split("\n") if len(ln) > LINE_MAX]
    worst = max((len(ln) for _, s in vi + en for ln in s.split("\n")), default=0)
    ck(f"mỗi dòng ≤ {LINE_MAX} ký tự", not too_long, f"{len(too_long)} dòng, dài nhất {worst}")
    dup = [(s, n) for s, n in Counter(s for _, s in en).items() if n > 1]
    ck("không câu EN nào lặp", not dup, dup[:3])
    ck("EN đã dịch hết (không trùng VI)", all(a[1] != b[1] for a, b in zip(vi, en)))
    for name in ("subtitles_vi.srt", "subtitles_en.srt"):
        raw = (f / name).read_bytes()
        plain_lf = raw.count(b"\n") - raw.count(b"\r\n")
        ck(f"{name}: UTF-8 BOM + CRLF + dòng trống cuối",
           raw.startswith(BOM) and plain_lf == 0 and raw.endswith(b"\r\n\r\n"),
           f"BOM={raw.startswith(BOM)} LF_trơn={plain_lf}")
    print("\n--- 3 cue mẫu ---")
    for (t, a), (_, b) in list(zip(vi, en))[40:43]:
        print(t, "|", a.replace("\n", " / "))
        print(" " * 30, b.replace("\n", " / "))
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 73))
