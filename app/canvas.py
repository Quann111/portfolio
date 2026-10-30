"""Canvas state (thử nghiệm): cảnh draw_over vẽ tiếp lên hình cảnh trước thay vì xoá bảng."""
from pathlib import Path

import cv2
import numpy as np


def _read(p: Path):
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def empty_area(prev: Path, width: int, height: int) -> list[int]:
    """Chọn 1/3 khung (trái/giữa/phải) ít nét nhất để vẽ thêm. Trả về box theo kích thước ảnh mới."""
    img = cv2.resize(_read(prev), (width, height))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 10)
    thirds = [(0, width // 3), (width // 3, 2 * width // 3), (2 * width // 3, width)]
    x0, x1 = min(thirds, key=lambda t: int(ink[:, t[0]:t[1]].sum()))
    mx, my = width // 40, height // 10
    return [max(0, x0 - mx), my, min(width, x1 + mx), height - my]


def write_mask(box: list[int], width: int, height: int, out: Path) -> Path:
    m = np.zeros((height, width), np.uint8)
    x0, y0, x1, y1 = box
    m[y0:y1, x0:x1] = 255
    m = cv2.GaussianBlur(m, (31, 31), 0)
    cv2.imencode(".png", m)[1].tofile(str(out))
    return out


def prompt(new_prompt: str) -> str:
    return ("Continue the same whiteboard doodle on the same beige paper: keep every existing drawing unchanged, "
            f"and in the empty white space add: {new_prompt}")
