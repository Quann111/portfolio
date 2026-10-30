"""Kiểm tra ảnh bằng OpenCV trước khi render (nhanh, không cần LLM)."""
from pathlib import Path

import cv2
import numpy as np

SUB_BAND = 0.18      # dải dưới dành cho phụ đề


def _load(p: Path, long_edge=1080):
    img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
    s = long_edge / max(img.shape[:2])
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)


def _ink(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 10) > 0, gray


def similarity(a: Path, b: Path) -> float:
    """0..1 – độ giống nhau giữa 2 ảnh (bố cục nét + màu)."""
    ia, ib = _load(a, 256), _load(b, 256)
    ib = cv2.resize(ib, (ia.shape[1], ia.shape[0]))
    ka, _ = _ink(ia)
    kb, _ = _ink(ib)
    ka = cv2.dilate(ka.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    kb = cv2.dilate(kb.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    iou = (ka & kb).sum() / max(1, (ka | kb).sum())
    ha = cv2.calcHist([cv2.cvtColor(ia, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8], [0, 180, 0, 256])
    hb = cv2.calcHist([cv2.cvtColor(ib, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8], [0, 180, 0, 256])
    corr = max(0.0, cv2.compareHist(cv2.normalize(ha, ha), cv2.normalize(hb, hb), cv2.HISTCMP_CORREL))
    return round(float(0.7 * iou + 0.3 * corr), 3)


def check(image: Path, prev: Path | None = None) -> dict:
    img = _load(image)
    h, w = img.shape[:2]
    ink, gray = _ink(img)
    ratio = float(ink.mean())
    issues, score = [], 1.0

    if ratio < 0.004:
        issues.append("ảnh gần như trắng")
        score -= 0.6
    bg = np.median(np.concatenate([gray[:10].ravel(), gray[-10:].ravel(), gray[:, :10].ravel(), gray[:, -10:].ravel()]))
    if bg < 170 or gray.mean() < 140:
        issues.append("nền tối / không phải giấy sáng")
        score -= 0.4
    if ratio > 0.25:
        issues.append("quá nhiều mực/chi tiết")
        score -= 0.3

    dist = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
    stroke = float(dist[ink].mean() * 2) if ink.any() else 0
    if ink.any() and stroke < 1.3:
        issues.append(f"nét quá mảnh ({stroke:.1f}px)")
        score -= 0.15
    if stroke > 14:
        issues.append(f"nét quá dày/mảng đặc ({stroke:.1f}px)")
        score -= 0.15

    m = max(4, int(min(h, w) * 0.012))
    border = np.zeros_like(ink)
    border[:m], border[-m:], border[:, :m], border[:, -m:] = True, True, True, True
    edge_ink = (ink & border).sum() / max(1, ink.sum())
    if edge_ink > 0.02:
        issues.append("đối tượng sát/cắt mép khung")
        score -= 0.2

    ys, xs = np.where(ink)
    if xs.size:
        bbox_area = (xs.max() - xs.min()) * (ys.max() - ys.min()) / (w * h)
        if bbox_area > 0.9:
            issues.append("thiếu khoảng trắng quanh hình")
            score -= 0.1

    n, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    tiny = int(((stats[1:, 4] > 3) & (stats[1:, 4] < 40)).sum())
    if tiny > 250:
        issues.append(f"quá nhiều chi tiết nhỏ ({tiny})")
        score -= 0.15

    band = ink[int(h * (1 - SUB_BAND)):]
    sub_overlap = float(band.mean())
    if sub_overlap > 0.03:
        issues.append("hình đè vùng phụ đề phía dưới")
        score -= 0.1

    sim = similarity(image, prev) if prev and Path(prev).exists() else 0.0
    if sim > 0.85:
        issues.append(f"gần trùng cảnh trước ({sim:.2f})")
        score -= 0.3

    return {"cv_quality": round(max(0.0, score), 3), "ink_ratio": round(ratio, 4), "stroke_px": round(stroke, 1),
            "edge_ink": round(float(edge_ink), 3), "subtitle_overlap": round(sub_overlap, 3),
            "similarity_prev": sim, "cv_issues": issues}
