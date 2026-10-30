import json
from pathlib import Path

import cv2
import numpy as np

GROUND_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "found": {"type": "boolean"},
                       "box": {"type": "array", "items": {"type": "number"}}},
        "required": ["name", "found", "box"]}}},
    "required": ["items"],
}


def _read(image: Path):
    return cv2.imdecode(np.fromfile(str(image), dtype=np.uint8), cv2.IMREAD_COLOR)


def _components(img: np.ndarray, max_regions: int):
    h, w = img.shape[:2]
    m = max(3, min(h, w) // 50)
    corners = np.concatenate([img[:m, :m], img[:m, -m:], img[-m:, :m], img[-m:, -m:]]).reshape(-1, 3)
    bg = np.median(corners, axis=0)
    diff = np.abs(img.astype(np.int16) - bg.astype(np.int16)).sum(axis=2)
    ink = (diff > 60).astype(np.uint8)
    k = max(9, w // 30) | 1
    ink = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    _, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    min_area = w * h * 0.004
    boxes = [[int(x), int(y), int(x + bw), int(y + bh)] for x, y, bw, bh, a in stats[1:] if a >= min_area]
    if not boxes:
        return [[0, 0, w, h]]

    def gap(a, b):
        return (max(0, max(a[0], b[0]) - min(a[2], b[2])) + max(0, max(a[1], b[1]) - min(a[3], b[3])))

    while len(boxes) > 1:
        g, i, j = min((gap(boxes[i], boxes[j]), i, j) for i in range(len(boxes)) for j in range(i + 1, len(boxes)))
        if len(boxes) <= max_regions and g > 0:
            break
        a, b = boxes[i], boxes.pop(j)
        boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
    return sorted(boxes, key=lambda b: ((b[0] + b[2]) / 2, b[1]))


def _covered(box, others, thr=0.6):
    area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
    for o in others:
        ix = max(0, min(box[2], o[2]) - max(box[0], o[0]))
        iy = max(0, min(box[3], o[3]) - max(box[1], o[1]))
        if ix * iy / area >= thr:
            return True
    return False


def ground(image: Path, names: list[str], llm) -> list[tuple[str, list[int]]]:
    """Qwen3-VL trả bbox theo thang 0-1000 cho từng đối tượng theo thứ tự vẽ."""
    if not names:
        return []
    img = _read(image)
    h, w = img.shape[:2]
    res = llm.look(image,
        f"Locate each of these objects in the image, in this order: {names}. For each return found and its "
        "bounding box [x1, y1, x2, y2] in relative coordinates from 0 to 1000 covering the whole object.",
        GROUND_SCHEMA)
    out = []
    for it in res["items"]:
        b = it.get("box") or []
        if not it.get("found") or len(b) != 4:
            continue
        x1, y1, x2, y2 = (int(v / 1000 * s) for v, s in zip(b, (w, h, w, h)))
        x1, x2 = sorted((max(0, x1), min(w, x2)))
        y1, y2 = sorted((max(0, y1), min(h, y2)))
        if (x2 - x1) * (y2 - y1) >= w * h * 0.003:
            out.append((it["name"], [x1, y1, x2, y2]))
    return out


def build(image: Path, out: Path, scene_id: str, narration: str, duration_ms: int, max_regions: int,
          grounded: list | None = None, new_area: list | None = None, lead_ms: int = 0):
    """new_area: cảnh draw_over – phần ngoài vùng này là nét cũ, hiện ngay; chỉ vẽ trong new_area.
    lead_ms: khoảng im lặng đầu (pause_before) – bắt đầu vẽ sau đó."""
    img = _read(image)
    h, w = img.shape[:2]
    if new_area:
        named = [(n, b) for n, b in (grounded or []) if _covered(b, [new_area], 0.5)] or [("phần mới", new_area)]
        if not any(_covered(new_area, [b], 0.9) for _, b in named):
            named.append(("phần mới", new_area))
        return _write(out, scene_id, narration, duration_ms, w, h, named, predrawn=True, lead_ms=lead_ms)
    comps = _components(img, max_regions)
    named = list(grounded or [])
    if named:
        # phần nét không thuộc đối tượng nào được vẽ sau cùng để không bị "hiện ra" đột ngột
        named += [("chi tiết phụ", b) for b in comps if not _covered(b, [nb for _, nb in named])]
    else:
        named = [(f"vùng {i + 1}", b) for i, b in enumerate(comps)]
    return _write(out, scene_id, narration, duration_ms, w, h, named, lead_ms=lead_ms)


def _element(i, name, b, start, dur, narration):
    x0, y0, x1, y1 = b
    return {
        "id": f"r{i + 1}", "label": name, "sequence": i + 1,
        "narrativeRole": "minh hoạ lời dẫn", "subtitle": narration, "type": "object",
        "region": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
        "reveal": {"direction": "left_to_right", "startMs": start, "durationMs": dur,
                   "maskPaddingPx": 0, "protectedRegions": []},
        "handPath": {"start": [x0, (y0 + y1) // 2], "end": [x1, (y0 + y1) // 2], "easing": "easeInOut"},
    }


def _visible_fraction(i, named, w, h):
    """Phần diện tích vùng i còn lại sau khi trừ các vùng vẽ sau (renderer trừ chúng khỏi vùng i)."""
    x0, y0, x1, y1 = named[i][1]
    if x1 <= x0 or y1 <= y0:
        return 0.0
    m = np.zeros((h, w), bool)
    m[y0:y1, x0:x1] = True
    for _, b in named[i + 1:]:
        m[b[1]:b[3], b[0]:b[2]] = False
    return m.sum() / ((x1 - x0) * (y1 - y0))


def _merge_hidden(named, w, h, min_visible=0.35):
    """Gộp vùng bị vùng vẽ sau che gần hết vào vùng che nó – tránh vùng rỗng (giấy trắng rồi dồn nét)."""
    named = list(named)
    changed = True
    while changed and len(named) > 1:
        changed = False
        for i in range(len(named) - 1):
            if _visible_fraction(i, named, w, h) < min_visible:
                (n1, a) = named[i]
                j = max(range(i + 1, len(named)), key=lambda k: _overlap(a, named[k][1]))
                n2, b = named[j]
                named[j] = (f"{n1} + {n2}", [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])])
                named.pop(i)
                changed = True
                break
    return named


def _overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _write(out, scene_id, narration, duration_ms, w, h, named, predrawn=False, lead_ms=0):
    pad = max(10, w // 80)
    named = [(n, [max(0, b[0] - pad), max(0, b[1] - pad), min(w, b[2] + pad), min(h, b[3] + pad)])
             for n, b in named]
    named = _merge_hidden(named, w, h)
    elements, t = [], 200 + lead_ms
    if predrawn:
        # nét cũ của cảnh trước: lộ ra gần như tức thì (vùng = cả khung trừ các vùng vẽ mới phía sau)
        elements.append(_element(0, "nét cũ", [0, 0, w, h], 0, 80, narration))
        t = max(t, 200)
    gap_ms, tail = 150, 900
    budget = max(1500, duration_ms - t - tail - gap_ms * (len(named) - 1))
    weights = [((b[2] - b[0]) * (b[3] - b[1])) ** 0.5 * (0.5 if n == "chi tiết phụ" else 1.0) for n, b in named]
    total_w = sum(weights)
    for (name, b), wt in zip(named, weights):
        dur = int(budget * wt / total_w)
        elements.append(_element(len(elements), name, b, t, dur, narration))
        t += dur + gap_ms
    ann = {"sceneId": scene_id, "canvas": {"width": w, "height": h}, "storyBasis": narration,
           "sceneDurationMs": max(duration_ms, t + 500), "elements": elements}
    out.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
    return ann
