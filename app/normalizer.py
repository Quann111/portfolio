import re

import yaml

from .config import ROOT

DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
SCALES = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]


def _three(n: int, full: bool) -> str:
    h, r = divmod(n, 100)
    t, u = divmod(r, 10)
    out = []
    if h or full:
        out.append(f"{DIGITS[h]} trăm")
    if t > 1:
        out.append(f"{DIGITS[t]} mươi")
        if u == 1:
            out.append("mốt")
        elif u == 4:
            out.append("tư")
        elif u == 5:
            out.append("lăm")
        elif u:
            out.append(DIGITS[u])
    elif t == 1:
        out.append("mười")
        if u == 5:
            out.append("lăm")
        elif u:
            out.append(DIGITS[u])
    elif u:
        if h or full:
            out.append("lẻ")
        out.append(DIGITS[u])
    return " ".join(out)


def vi_number(n: int) -> str:
    if n == 0:
        return "không"
    if n < 0:
        return "âm " + vi_number(-n)
    groups = []
    while n:
        n, g = divmod(n, 1000)
        groups.append(g)
    words = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if not g:
            continue
        part = _three(g, full=i < len(groups) - 1)
        words.append(f"{part} {SCALES[i]}".strip())
    return " ".join(words)


def _vi_decimal(s: str) -> str:
    whole, _, frac = s.partition(",") if "," in s else s.partition(".")
    out = vi_number(int(whole))
    if frac:
        out += " phẩy " + (vi_number(int(frac)) if not frac.startswith("0") else " ".join(DIGITS[int(c)] for c in frac))
    return out


MONTHS = {str(i): f"tháng {vi_number(i) if i != 4 else 'tư'}" for i in range(1, 13)}


def _vi(text: str) -> str:
    # ngày dd/mm/yyyy
    text = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b",
                  lambda m: f"ngày {vi_number(int(m[1]))} {MONTHS.get(str(int(m[2])), m[2])} năm {vi_number(int(m[3]))}",
                  text)
    # tiền
    num = r"(\d(?:[\d.,]*\d)?)"
    text = re.sub(r"\$\s?" + num, lambda m: f"{m[1]} đô la", text)
    text = re.sub(num + r"\s?(đ|VND|VNĐ)(?!\w)", lambda m: f"{m[1]} đồng", text)
    # khoảng số 1990-1995 → "… đến …" (sau ngày dd/mm/yyyy, trước khi đổi số)
    text = re.sub(r"\b(\d{1,4})\s?[-–]\s?(\d{1,4})\b", r"\1 đến \2", text)
    # ký hiệu: °C, °, +, gấp x2
    text = re.sub(r"\s?°\s?C\b", " độ xê", text)
    text = text.replace("°", " độ")
    text = re.sub(r"(\d)\s?\+\s?(\d)", r"\1 cộng \2", text)
    text = re.sub(r"(?<![\w])\+\s?(\d)", r"hơn \1", text)
    text = re.sub(r"(?<!\w)[xX×]\s?(\d+)\b", r"gấp \1", text)
    # thập niên 1990s
    text = re.sub(r"(những năm\s+)?\b(\d{4})s\b", lambda m: f"những năm {vi_number(int(m[2]))}", text)
    # số có dấu phân cách nghìn 1.000.000 hoặc 1,000,000
    text = re.sub(r"\b\d{1,3}(?:[.,]\d{3})+\b", lambda m: re.sub(r"[.,]", "", m[0]), text)
    # số thập phân và số nguyên
    text = re.sub(r"\b\d+[.,]\d+\b", lambda m: _vi_decimal(m[0]), text)
    text = re.sub(r"\b\d+\b", lambda m: vi_number(int(m[0])), text)
    return text


def _load_dict(lang: str) -> dict:
    p = ROOT / "pronunciation.yaml"
    if not p.exists():
        return {}
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return d.get(lang, {}) or {}


LETTERS = {"A": "a", "B": "bê", "C": "xê", "D": "đê", "Đ": "đê", "E": "e", "F": "ép", "G": "giê", "H": "hát",
           "I": "i", "J": "gi", "K": "ca", "L": "lờ", "M": "mờ", "N": "nờ", "O": "ô", "P": "pê", "Q": "quy",
           "R": "rờ", "S": "ét", "T": "tê", "U": "u", "V": "vê", "W": "vê kép", "X": "ích", "Y": "i", "Z": "dét"}
UNITS = {"km": "ki lô mét", "kg": "ki lô gam"}


def _spell(tok: str) -> str:
    """UAV → 'u a vê', F35 → 'ép ba mươi lăm' (chữ cái đọc kiểu Việt, cụm số đọc thành số)."""
    out = []
    for part in re.findall(r"\d+|\D", tok):
        out.append(vi_number(int(part)) if part.isdigit() else LETTERS.get(part, part))
    return " ".join(out)


def _sub_dict(text: str, d: dict, flags=0) -> str:
    if not d:
        return text
    alts = []
    for word in sorted(d, key=len, reverse=True):
        w = re.escape(word)
        alts.append(rf"(?<!\w){w}(?!\w)" if re.match(r"\w", word) else w)
    key = (lambda s: s.lower()) if flags else (lambda s: s)
    return re.sub("|".join(alts), lambda m: d[key(m[0])], text, flags=flags)


def _vi_codes(text: str, known: set) -> str:
    # tên mã Chữ-Số / ChữSố: B-2, MiG-35, Su-57, S-400, F35 → phần chữ (đã qua từ điển) + số đọc thành chữ
    def code(m):
        head, num = m[1], m[2]
        if head.isupper():
            head = _spell(head)
        return f"{head} {vi_number(int(num))}"
    text = re.sub(r"(?<![\w-])([A-Za-zĐ]{1,4})-?(\d{1,4})(?![\w.,]\d)(?!\w)",
                  lambda m: code(m) if (m[1][0].isupper() and m[1] not in known) else m[0], text)
    # phần chữ đã được từ điển đổi (MiG-35 → "mích-35") → bỏ gạch nối để số đọc tách riêng
    text = re.sub(r"([^\W\d_])-(\d)", r"\1 \2", text)
    # từ viết tắt 2–6 chữ HOA chưa có trong từ điển → đánh vần kiểu Việt (UAV, EEZ, IRGC)
    return re.sub(r"(?<!\w)[A-ZĐ]{2,6}(?!\w)", lambda m: m[0] if m[0] in known else _spell(m[0]), text)


def normalize(text: str, language: str) -> str:
    lang = language.lower()
    raw = {str(k): str(v) for k, v in _load_dict(lang).items()}
    # khoá có chữ HOA (AI, NASA, WiFi, CO2) khớp ĐÚNG kiểu chữ – "ai" (đại từ) không bị đọc thành "ây ai";
    # khoá chữ thường / ký hiệu (blockchain, %, &) khớp mọi kiểu chữ như trước
    exact = {k: v for k, v in raw.items() if any(ch.isupper() for ch in k)}
    loose = {k.lower(): v for k, v in raw.items() if k not in exact and k.lower() not in UNITS}
    text = _sub_dict(text, exact)
    text = _sub_dict(text, loose, flags=re.IGNORECASE)
    if lang == "vietnamese":
        # đơn vị chỉ đọc khi đứng sau số: "70 kg" → "70 ki lô gam"
        units = {**UNITS, **{k.lower(): v for k, v in raw.items() if k.lower() in UNITS}}
        text = re.sub(r"(\d)\s?(" + "|".join(units) + r")(?!\w)", lambda m: f"{m[1]} {units[m[2].lower()]}", text,
                      flags=re.IGNORECASE)
        text = _vi_codes(text, set(exact))
        text = _vi(text)
    text = re.sub(r"[“”\"‘’]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
