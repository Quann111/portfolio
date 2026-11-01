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


# ------------------------------------------------------------------ phụ đề song ngữ cho YouTube
_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def read_srt(f: Path) -> list:
    """Đọc lại file .srt → [(ms_bắt_đầu, ms_kết_thúc, chữ, "")] (cùng dạng cue với write_srt)."""
    text = f.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        i = next((k for k, ln in enumerate(lines) if "-->" in ln), None)
        if i is None:
            continue
        t = _SRT_TIME.findall(lines[i])
        if len(t) < 2:
            continue
        ms = [((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(x.ljust(3, "0")) for h, m, s, x in t[:2]]
        body = " ".join(ln.strip() for ln in lines[i + 1:] if ln.strip())
        if body:
            out.append((ms[0], ms[1], body, ""))
    return out


SENT_END = ".?!:…"
LINE_CHARS = 42              # 1 dòng phụ đề
CUE_CHARS = 84               # 1 cue = 2 dòng
CUE_MS = 6000
WRAP_CHARS = 45              # ngưỡng gộp câu (merge_sentences)


def _wrap2(text: str, limit: int = LINE_CHARS) -> str:
    """Chia thành 2 dòng CÂN bằng, mỗi dòng ≤ limit nếu chia được (không bao giờ cắt giữa từ)."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return text
    fits = [i for i in spaces if len(text[:i]) <= limit and len(text[i + 1:]) <= limit]
    cut = min(fits or spaces, key=lambda i: (max(len(text[:i]), len(text[i + 1:])), abs(i - len(text) / 2)))
    a, b = text[:cut].strip(), text[cut + 1:].strip()
    return f"{a}\n{b}" if a and b else text


def merge_sentences(cs: list, max_chars: int = 90, max_ms: int = 7000) -> list:
    """Gộp các mảnh phụ đề bị cắt giữa câu thành câu trọn vẹn.

    Phụ đề của video cắt theo độ dài ("…nguy cơ an / ninh nghiêm trọng"), nên dịch từng mảnh là bất khả thi –
    LLM sẽ tự gộp/viết lại và lệch chỉ số. Gộp trước rồi mới dịch.
    Gộp khi: chưa hết câu (cue trước không kết thúc bằng . ? ! : …), tổng chữ ≤ max_chars, tổng thời lượng ≤ max_ms.
    Thời gian = đầu cue đầu → cuối cue cuối. Văn bản > 42 ký tự xuống 2 dòng.
    """
    out, buf = [], []

    def flush():
        if buf:
            out.append((buf[0][0], buf[-1][1], _wrap2(" ".join(c[2] for c in buf).strip()), buf[0][3]))
            buf.clear()

    for c in cs:
        a, b, text, emph = c[0], c[1], (c[2] or "").strip(), (c[3] if len(c) > 3 else "")
        if not text:
            continue
        if buf and (len(" ".join(x[2] for x in buf)) + 1 + len(text) > max_chars or b - buf[0][0] > max_ms):
            flush()
        buf.append((a, b, text, emph))
        if re.search(r"[.?!:…]$", text):
            flush()
    flush()
    return out


# ký tự chỉ có trong tiếng Việt – dùng để phát hiện dòng "dịch" mà vẫn còn nguyên tiếng Việt
VI_CHARS = set("ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ")
NUM_LINE = re.compile(r"^\s*(\d+)\s*[:.)\-]\s*(.+?)\s*$")


def _still_vi(text: str) -> bool:
    """Dòng "dịch" mà vẫn là tiếng Việt. Không thể chỉ kiểm tra ký tự có dấu: tên riêng Latin cũng có dấu
    (Brasília, Bogotá). Nên: chữ chỉ tiếng Việt mới có (ă â đ ơ ư) hoặc mật độ dấu cao."""
    t = (text or "").lower()
    if any(ch in "ăâđơư" for ch in t):
        return True
    hits = sum(1 for ch in t if ch in VI_CHARS)
    return hits >= max(3, len(t) * 0.06)


def _ask_translate(llm, items: list, log) -> dict:
    """items = [(chỉ số, câu tiếng Việt)] → {chỉ số: câu tiếng Anh}. Chỉ nhận dòng đúng chỉ số, không rỗng,
    không còn tiếng Việt. Trả về ít hơn `items` nghĩa là lô đó hỏng, nơi gọi phải thử lại.

    KHÔNG dùng response_format json_schema: qwen3-vl-4b trên LM Studio trả {"lines": []} với schema này
    (đo 31/10) trong khi văn bản thuần thì dịch đúng – nên hỏi dạng "i: câu" rồi tự tách chỉ số.
    """
    need = {i for i, _ in items}
    src_map = dict(items)
    src = "\n".join(f"{i}: {t.replace(chr(10), ' ')}" for i, t in items)
    prompt = ("Translate each numbered Vietnamese subtitle line into natural, concise English for a YouTube "
              "explainer channel. Each input line is ONE complete sentence: translate it on its own and return "
              "exactly one output line per input line, starting with the SAME index and a colon. Do NOT merge, "
              "split, drop, reorder or invent lines. Do NOT copy the Vietnamese source. Keep numbers, names and "
              "units correct.\n\n" + src + "\n\nOutput (one line per input, same indices):")
    text = llm.chat([{"role": "user", "content": prompt}], temperature=0.2,
                    max_tokens=400 if len(items) <= 2 else 2000)
    got = {}
    for line in (text or "").split("\n"):
        m = NUM_LINE.match(line)
        if not m:
            continue
        i, en = int(m.group(1)), m.group(2).strip().strip('"').strip()
        if i in need and en and not _still_vi(en) and en != " ".join(src_map[i].split()):
            got[i] = en
    return got


def translate_cues(cs: list, llm, log=print, batch: int = 12) -> list:
    """Dịch phụ đề tiếng Việt → tiếng Anh, GIỮ NGUYÊN thời gian từng dòng.

    `cs` phải là các CÂU TRỌN VẸN (merge_sentences) – dịch mảnh bị cắt giữa câu thì model tự gộp/viết lại.
    Lô nhỏ 12 câu; chỉ nhận lô khi trả đủ đúng tập chỉ số (thiếu/lạ chỉ số → thử lại, tối đa 3 lần);
    còn sót thì dịch từng câu một. Dòng nào vẫn hỏng thì giữ tiếng Việt và ghi log.
    """
    out = list(cs)
    for b0 in range(0, len(cs), batch):
        part = [(b0 + k, cs[b0 + k][2]) for k in range(min(batch, len(cs) - b0))]
        need = {i for i, _ in part}
        got = {}
        for attempt in range(3):
            try:
                res = _ask_translate(llm, part, log)
            except Exception as e:
                log(f"💬 Dịch phụ đề lỗi (lô {b0 // batch + 1}, lần {attempt + 1}): {e}")
                res = {}
            if set(res) == need:            # đủ đúng tập chỉ số → nhận cả lô
                got = res
                break
            got = res                       # giữ phần đúng để đỡ phải dịch lại
            if attempt < 2:
                log(f"💬 Lô {b0 // batch + 1} trả {len(res)}/{len(need)} dòng đúng chỉ số – thử lại")
        for i, en in got.items():
            a, b, _, e = cs[i]
            out[i] = (a, b, en, e)
        for i, text in [(i, cs[i][2]) for i in sorted(need - set(got))]:
            for _ in range(2):              # dịch riêng từng câu: model không còn cớ gộp dòng
                try:
                    one = _ask_translate(llm, [(i, text)], log)
                except Exception as e:
                    log(f"💬 Dịch câu {i} lỗi: {e}")
                    one = {}
                if i in one:
                    a, b, _, e = cs[i]
                    out[i] = (a, b, one[i], e)
                    break
            else:
                log(f"⚠ Dòng phụ đề {i} chưa dịch được – giữ tiếng Việt")
    return out


# ------------------------------------------------------------------ cắt câu dài thành cue hiển thị
def _split_n(text: str, n: int) -> list:
    """Chia 1 câu thành n phần gần bằng nhau, ưu tiên cắt sau dấu phẩy/chấm phẩy/gạch nối.

    `consumed` đếm TÍCH LUỸ từ đầu câu: cắt sớm ở dấu phẩy mà reset bộ đếm thì phần cuối sẽ phình to
    (đo được phần 166 ký tự → dòng phụ đề 89 ký tự).
    """
    words = text.split()
    if n <= 1 or len(words) < n:
        return [text]
    total = sum(len(w) + 1 for w in words)
    parts, cur, consumed, done = [], [], 0, 0
    for k, w in enumerate(words):
        cur.append(w)
        consumed += len(w) + 1
        if done < n - 1 and len(words) - k - 1 >= n - done - 1:
            target = total * (done + 1) / n
            if consumed >= target or (consumed >= target * 0.85 and re.search(r"[,;:–—]$", w)):
                parts.append(" ".join(cur))
                cur, done = [], done + 1
    if cur:
        parts.append(" ".join(cur))
    while len(parts) > n:                # cắt quá tay → gộp phần cuối
        parts[-2] += " " + parts.pop()
    return parts


def _parts_needed(text: str, max_chars: int) -> int:
    """Số phần tối thiểu để không phần nào vượt max_chars (bản EN thường dài hơn VI ~20%)."""
    n = max(1, -(-len(text) // max_chars))
    for _ in range(4):
        parts = _split_n(text, n)
        if len(parts) == n and all(len(p) <= max_chars for p in parts):
            return n
        n += 1
    return n


def split_sentences(sents: list, en: list, max_chars: int = CUE_CHARS, max_ms: int = CUE_MS) -> tuple[list, list]:
    """Cắt mỗi câu (VI + EN) thành các cue hiển thị ≤ 2 dòng × 42 ký tự.

    Dịch ở mức CÂU (model hiểu đúng, không lặp), hiển thị ở mức CUE.
    Số cue của một câu = max(số phần VI cần, số phần EN cần, số phần theo thời lượng) – chỉ tính theo VI thì
    câu EN dài hơn sẽ bị dồn thành dòng > 42 ký tự. VI và EN chia thành đúng n phần đó, **cùng số cue, cùng
    thời gian**; thời gian chia theo tỉ lệ ký tự bản VI (khớp giọng đọc).
    """
    vi_out, en_out = [], []
    for (a, b, vi_text, emph), (_, _, en_text, _) in zip(sents, en):
        vi_text = " ".join(vi_text.split())      # bỏ xuống dòng do _wrap2 của bước gộp để lại
        en_text = " ".join(en_text.split())
        dur = max(1, b - a)
        n = max(_parts_needed(vi_text, max_chars), _parts_needed(en_text, max_chars),
                -(-dur // max_ms), 1)
        n = min(n, len(vi_text.split()), len(en_text.split())) or 1   # không chia nhiều hơn số từ
        for _ in range(3):                  # _split_n ưu tiên dấu phẩy nên có thể lệch → thêm phần cho vừa
            vi_parts, en_parts = _split_n(vi_text, n), _split_n(en_text, n)
            if len(vi_parts) == len(en_parts) == n and all(len(p) <= max_chars for p in vi_parts + en_parts):
                break
            n += 1
        n = min(len(vi_parts), len(en_parts))
        while len(vi_parts) > n:
            vi_parts[-2] += " " + vi_parts.pop()
        while len(en_parts) > n:
            en_parts[-2] += " " + en_parts.pop()
        total = sum(len(p) for p in vi_parts) or 1
        t = a
        for k, (vp, ep) in enumerate(zip(vi_parts, en_parts)):
            d = b - t if k == n - 1 else max(400, int(dur * len(vp) / total))
            d = min(d, b - t)
            vi_out.append((t, t + d, _wrap2(vp), emph))
            en_out.append((t, t + d, _wrap2(ep), emph))
            t += d
    return vi_out, en_out


def write_srt_crlf(cs, out: Path):
    """SRT cho YouTube: UTF-8 **có BOM**, xuống dòng CRLF, kết thúc bằng dòng trống.

    Thiếu BOM là một trong các nguyên nhân YouTube báo "Không thể phân tích cú pháp phụ đề đã chọn".
    """
    lines = []
    for i, (a, b, text, _) in enumerate(cs):
        lines += [str(i + 1), f"{_ts(a)} --> {_ts(b)}"]
        lines += [ln.strip() for ln in str(text).split("\n") if ln.strip()]
        lines.append("")
    out.write_bytes(b"\xef\xbb\xbf" + ("\r\n".join(lines) + "\r\n").encode("utf-8"))
