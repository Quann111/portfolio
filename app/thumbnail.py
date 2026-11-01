"""Thumbnail YouTube: Flow tạo ảnh nền (ảnh thật, không chữ) → app in chữ tiếng Việt cực lớn (Pillow) như các
kênh mẫu ("XE ĐIỆN KHÓ THẮNG SIRIUS?"): chữ trắng/vàng, viền đen dày, dòng nhấn đỏ, đặt vào nửa ảnh ít chi tiết."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .visual_rules import FLAG_RULE, PEOPLE_RULE

# nền thumbnail: app tự in tiêu đề lên nửa trống → nền không chữ, nhưng cờ / chân dung theo luật chung
THUMB_RULE = ("No writing of any kind (the headline is added later), no watermark, no logos. " + FLAG_RULE + ". "
              + PEOPLE_RULE)

W, H = 1280, 720
FONTS = ["C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/arialbd.ttf",
         "C:/Windows/Fonts/segoeuib.ttf",
         # máy không phải Windows (hoặc thiếu font trên) → font Linux/macOS có đủ dấu tiếng Việt
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
         "/Library/Fonts/Arial Bold.ttf"]
VN_TEST = "ẠẢẤẦẨẪẬẮẰẲẴẶĐƠƯỆỎỐỒỞỠỢỨỪỬỮỰỲỴỶỸ"


def bg_prompt(scene: str, subject: str = "", context: str = "", side: str = "right") -> str:
    """Ảnh nền kiểu thumbnail thời sự (như các kênh tin triệu view): nhân vật/vật chính CẬN CẢNH, biểu cảm mạnh, ở
    một bên (side); nửa còn lại là bối cảnh đúng nội dung job (cờ, sản phẩm, toà nhà, biểu đồ…) để app in chữ lên."""
    other = "left" if side == "right" else "right"
    main = (f"MAIN SUBJECT on the {side.upper()} side: {subject.strip()[:300]}, close-up from chest up, very strong "
            f"facial expression / emotion, sharp, filling the full frame height and about 40-50% of the width. "
            if subject.strip() else
            f"one big expressive main subject on the {side.upper()} side filling the frame height. ")
    ctx = (f"BACKGROUND on the {other} side and behind: {context.strip()[:300]}, slightly blurred, dramatic, "
           "clearly related to the story. " if context.strip() else "")
    return ("REAL PHOTOGRAPH for a news-style YouTube thumbnail, 16:9, photojournalism collage look like a big "
            "news channel thumbnail, real people and real objects with natural skin and real textures – NOT an "
            "illustration, NOT a cartoon, NOT a drawing, NOT a sketch or whiteboard doodle, NOT line art, NOT anime, "
            "NOT a 3D render. Ultra realistic, bright saturated colors, very high contrast, dramatic lighting, "
            + main + ctx +
            f"Keep the lower {other} part of the image calmer (no faces there) because a big headline box will be "
            "placed there later. Vietnam context when relevant. " + THUMB_RULE + ". Story: " + scene.strip()[:500])


@lru_cache(maxsize=1)
def font_path() -> str:
    """Font đậm đầu tiên có đủ dấu tiếng Việt (glyph thiếu sẽ vẽ ra ô vuông giống ký tự lạ U+FFFF)."""
    for f in FONTS:
        if not Path(f).exists():
            continue
        ft = ImageFont.truetype(f, 60)
        missing = ft.getmask("\uffff").getbbox()
        if all(ft.getmask(ch).getbbox() != missing for ch in VN_TEST):
            return f
    found = [f for f in FONTS if Path(f).exists()]
    if found:                               # không font nào đủ dấu → dùng font đầu tiên có thật
        return found[0]
    raise FileNotFoundError("Không tìm thấy font đậm nào để in chữ thumbnail: " + ", ".join(FONTS))


def _cover(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    s = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * s + 0.5), int(img.height * s + 0.5)), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def text_side(img: Image.Image) -> str:
    """Nửa ít chi tiết hơn (mật độ cạnh Canny) → đặt chữ ở đó."""
    g = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    e = cv2.Canny(g, 80, 160)
    return "left" if e[:, : W // 2].mean() <= e[:, W // 2:].mean() else "right"


def _wrap(text: str, n_lines: int) -> list[str]:
    words = text.split()
    if len(words) <= 1 or n_lines <= 1:
        return [text]
    best, best_cost = [text], 1e9
    # chia đều theo số ký tự (dòng ngắn nhất ít chênh nhất)
    import itertools
    for cuts in itertools.combinations(range(1, len(words)), min(n_lines, len(words)) - 1):
        parts = [" ".join(words[a:b]) for a, b in zip((0, *cuts), (*cuts, len(words)))]
        cost = max(len(p) for p in parts)
        if cost < best_cost:
            best, best_cost = parts, cost
    return best


def _fit(lines: list[str], fp: str, box_w: int, box_h: int, start: int = 150):
    size = start
    while size > 36:
        ft = ImageFont.truetype(fp, size)
        if max(ft.getbbox(ln)[2] for ln in lines) <= box_w and size * 1.15 * len(lines) <= box_h:
            break
        size -= 4
    return size, ImageFont.truetype(fp, size)


def _busy(edges, rect) -> float:
    x0, y0, x1, y1 = (max(0, int(v)) for v in rect)
    r = edges[y0:y1, x0:x1]
    return float(r.mean()) if r.size else 1e9


def _date_badge(d: ImageDraw.ImageDraw, fp: str, x: int, y: int, date) -> tuple:
    """Huy hiệu ngày kiểu báo: dải đỏ "THÁNG 10" + ô trắng số ngày to."""
    w, h1, h2 = 130, 34, 92
    d.rounded_rectangle([x, y, x + w, y + h1 + h2], radius=10, fill=(255, 255, 255), outline=(0, 0, 0), width=4)
    d.rounded_rectangle([x, y, x + w, y + h1], radius=10, fill=(220, 30, 30))
    f1, f2 = ImageFont.truetype(fp, 24), ImageFont.truetype(fp, 72)
    t1, t2 = f"THÁNG {date.month}", f"{date.day:02}"
    b1, b2 = d.textbbox((0, 0), t1, font=f1), d.textbbox((0, 0), t2, font=f2)
    d.text((x + (w - b1[2] + b1[0]) // 2 - b1[0], y + (h1 - b1[3] + b1[1]) // 2 - b1[1]), t1, font=f1, fill=(255, 255, 255))
    d.text((x + (w - b2[2] + b2[0]) // 2 - b2[0], y + h1 + (h2 - b2[3] + b2[1]) // 2 - b2[1]), t2, font=f2, fill=(20, 20, 20))
    return x, y, x + w, y + h1 + h2


def compose(bg: Path, text: str, out: Path, accent: int | None = None, subject_side: str | None = None,
            date=None) -> Path:
    """In chữ HOA lên ảnh nền, KHÔNG che chủ thể: thử nhiều ô (dưới/trên/nửa ảnh) phía không có chủ thể, chọn ô ít chi
    tiết nhất (cạnh Canny); chữ vàng trên khối đen mờ, dòng nhấn trắng nền đỏ; tuỳ chọn huy hiệu ngày.
    subject_side = "left"/"right" (vị trí nhân vật chính – từ prompt/QC ảnh) → chữ luôn ở phía còn lại."""
    img = _cover(Image.open(bg))
    text = " ".join(text.upper().split())
    g = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    edges = cv2.GaussianBlur(cv2.Canny(g, 80, 160).astype(np.float32), (0, 0), 9)
    sides = ([{"left": "right", "right": "left"}[subject_side]] if subject_side in ("left", "right")
             else ["left", "right"])
    fp = font_path()
    n = 3 if len(text) > 18 else 2 if len(text) > 8 else 1
    if len(text) > 34:
        n = 4
    lines = _wrap(text, n)
    m = 28
    best = None
    for sd in sides:
        for vpos, bw, bh in (("bottom", 0.62, 0.52), ("middle", 0.55, 0.80), ("top", 0.62, 0.48)):
            size, ft = _fit(lines, fp, int(W * bw) - 2 * m, int(H * bh) - 2 * m)
            tw = max(ft.getbbox(ln)[2] for ln in lines) + 2 * m
            th = int(size * 1.15 * len(lines)) + 2 * m
            x0 = m if sd == "left" else W - m - tw
            y0 = {"bottom": H - m - th - 40, "middle": (H - th) // 2, "top": m + 140}[vpos]
            cost = _busy(edges, (x0, y0, x0 + tw, y0 + th)) - size * 0.15     # ưu tiên ô trống + chữ to
            if best is None or cost < best[0]:
                best = (cost, sd, x0, y0, tw, th, size, ft)
    _, side, bx, by, bw_, bh_, size, ft = best
    # khối nền đen mờ sau chữ (như kênh mẫu) – chữ đọc được trên mọi nền, không cần làm tối nửa ảnh
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle([bx, by, bx + bw_, by + bh_], radius=18, fill=(0, 0, 0, 165))
    img = Image.alpha_composite(img.convert("RGBA"), panel).convert("RGB")
    d = ImageDraw.Draw(img)
    stroke = max(5, size // 12)
    acc = len(lines) - 1 if accent is None else accent
    y = by + m
    for i, ln in enumerate(lines):
        x0, y0, x1, y1 = d.textbbox((0, 0), ln, font=ft, stroke_width=stroke)
        tw = x1 - x0
        x = bx + m if side == "left" else bx + bw_ - m - tw
        if i == acc and len(lines) > 1:
            pad = size // 10
            d.rounded_rectangle([x - pad, y + y0 - pad // 2, x + tw + pad, y + y1 + pad // 2], radius=pad,
                                fill=(220, 30, 30))
            d.text((x - x0, y), ln, font=ft, fill=(255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0))
        else:
            d.text((x - x0, y), ln, font=ft, fill=(255, 230, 0), stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += int(size * 1.15)
    if date is not None:          # huy hiệu ngày: góc trên cùng phía với chữ (không đè chủ thể)
        _date_badge(d, fp, 24 if side == "left" else W - 24 - 130, 20, date)
    out.parent.mkdir(parents=True, exist_ok=True)
    q = 92
    while True:
        img.save(out, "JPEG", quality=q, optimize=True)
        if out.stat().st_size < 2_000_000 or q <= 60:
            return out
        q -= 8
