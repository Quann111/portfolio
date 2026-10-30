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


def normalize(text: str, language: str) -> str:
    lang = language.lower()
    d = {str(k).lower(): str(v) for k, v in _load_dict(lang).items()}
    if d:
        alts = []
        for word in sorted(d, key=len, reverse=True):
            w = re.escape(word)
            alts.append(rf"(?<!\w){w}(?!\w)" if re.match(r"\w", word) else w)
        text = re.sub("|".join(alts), lambda m: d[m[0].lower()], text, flags=re.IGNORECASE)
    if lang == "vietnamese":
        text = _vi(text)
    text = re.sub(r"[“”\"‘’]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
