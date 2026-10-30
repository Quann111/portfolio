from pathlib import Path

import cv2
import numpy as np

COMFORT_PX_PER_SEC = 1800     # tốc độ vẽ dễ theo dõi (độ dài nét ở 1080p)
REGION_OVERHEAD_S = 0.6


def calibrate(path) -> None:
    """Nếu đã có ≥ 30 mẫu trong data/calibration.jsonl: tốc độ vẽ dễ theo dõi = trung vị (nét / thời gian vẽ)
    của các cảnh đạt QC – cập nhật hệ số thay vì dùng số đoán."""
    global COMFORT_PX_PER_SEC
    import json
    try:
        rows = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
    except OSError:
        return
    speeds = sorted(r["stroke_length_px"] / (r["draw_ms"] / 1000) for r in rows
                    if r.get("stroke_length_px") and r.get("draw_ms"))
    if len(speeds) >= 30:
        COMFORT_PX_PER_SEC = max(800, min(4000, speeds[len(speeds) // 2]))


def measure(image: Path, speech_ms: int) -> dict:
    img = cv2.imdecode(np.fromfile(str(image), dtype=np.uint8), cv2.IMREAD_COLOR)
    scale = 1080 / max(img.shape[:2])
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 10) > 0
    ink_px = int(ink.sum())
    dist = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
    stroke_w = max(1.0, float(dist[ink].mean()) * 2) if ink_px else 1.0
    stroke_len = ink_px / stroke_w
    k = max(9, w // 30) | 1
    blobs = cv2.dilate(ink.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(blobs, 8)
    objects = int(sum(1 for a in stats[1:, 4] if a >= w * h * 0.004))
    est = stroke_len / COMFORT_PX_PER_SEC + REGION_OVERHEAD_S * min(objects, 6)
    avail = max(0.5, speech_ms / 1000 - 0.9)
    return {
        "objects": objects,
        "line_density": round(ink_px / (w * h), 4),
        "stroke_length_px": int(stroke_len),
        "estimated_draw_time": round(est, 1),
        "available_time": round(avail, 1),
        "suitability": round(min(1.0, avail / est) if est else 1.0, 2),
    }
