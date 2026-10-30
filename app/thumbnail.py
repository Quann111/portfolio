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
         "C:/Windows/Fonts/segoeuib.ttf"]
VN_TEST = "ẠẢẤẦẨẪẬẮẰẲẴẶĐƠƯỆỎỐỒỞỠỢỨỪỬỮỰỲỴỶỸ"


def bg_prompt(scene: str) -> str:
    return ("REAL PHOTOGRAPH for a YouTube thumbnail, 16:9, shot on a DSLR camera, photojournalism / documentary "
            "photo, real people and real objects with natural skin and real textures – NOT an illustration, NOT a "
            "cartoon, NOT a drawing, NOT a sketch or whiteboard doodle, NOT line art, NOT anime, NOT a 3D render. "
            "Ultra realistic, bright saturated colors, high contrast, "
            "sharp focus, one big expressive main subject placed on ONE side (left or right third) and filling the "
            "frame height, the other side is simpler and less busy so big headline text can be placed there later, "
            "eye-catching, dramatic lighting, Vietnam context when relevant. " + THUMB_RULE + ". Scene: " + scene.strip()[:700])


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
    return FONTS[-1]


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


def compose(bg: Path, text: str, out: Path, accent: int | None = None) -> Path:
    """In chữ HOA lên ảnh nền. accent = chỉ số dòng tô đỏ nền (mặc định dòng dài nhất ở cuối)."""
    img = _cover(Image.open(bg))
    text = " ".join(text.upper().split())
    side = text_side(img)
    box_w = int(W * 0.6)
    fp = font_path()
    lines = _wrap(text, 3 if len(text) > 18 else 2 if len(text) > 8 else 1)
    size = 170
    while size > 40:
        ft = ImageFont.truetype(fp, size)
        if max(ft.getbbox(ln)[2] for ln in lines) <= box_w and size * 1.12 * len(lines) <= H * 0.8:
            break
        size -= 4
    ft = ImageFont.truetype(fp, size)
    # bóng tối nhẹ phía chữ cho dễ đọc
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    for i in range(W // 2):
        a = int(110 * (1 - i / (W / 2)))
        x = i if side == "left" else W - 1 - i
        sd.line([(x, 0), (x, H)], fill=a)
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade)
    d = ImageDraw.Draw(img)
    stroke = max(6, size // 11)
    acc = len(lines) - 1 if accent is None else accent
    total_h = int(size * 1.12 * len(lines))
    y = (H - total_h) // 2
    for i, ln in enumerate(lines):
        x0, y0, x1, y1 = d.textbbox((0, 0), ln, font=ft, stroke_width=stroke)
        tw = x1 - x0
        x = 40 if side == "left" else W - 40 - tw
        if i == acc and len(lines) > 1:
            pad = size // 10
            d.rounded_rectangle([x - pad, y + y0 - pad // 2, x + tw + pad, y + y1 + pad // 2], radius=pad,
                                fill=(220, 30, 30))
            d.text((x - x0, y), ln, font=ft, fill=(255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0))
        else:
            color = (255, 230, 0) if i == 0 and len(lines) > 1 else (255, 255, 255)
            d.text((x - x0, y), ln, font=ft, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += int(size * 1.12)
    out.parent.mkdir(parents=True, exist_ok=True)
    q = 92
    while True:
        img.save(out, "JPEG", quality=q, optimize=True)
        if out.stat().st_size < 2_000_000 or q <= 60:
            return out
        q -= 8
