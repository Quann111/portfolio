"""plan.py — Biến cảnh của AutoVideo thành content.json cho opuscar engine.

LLM chỉ dùng để điền JSON theo lô 6-8 cảnh.
Mọi số liệu (stat, chart, events) được Python kiểm tra cứng đối chiếu với narration và facts.
Tách row thành các shot con ≤ 7s (≤ 4s với short) bám ranh giới câu từ asr_words.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

from . import film

# 8 loại shot hợp lệ được engine opuscar hỗ trợ
VALID_SHOTS = (
    "hero_image",
    "kinetic_text",
    "stat",
    "chart",
    "timeline",
    "quote_card",
    "chapter_card",
    "split_compare",
)

EMOTION_TO_MOOD = {
    "calm": "calm",
    "curious": "calm",
    "excited": "epic",
    "serious": "tense",
    "warm": "hopeful",
    "sad": "sad",
    "grief": "sad",
    "fear": "tense",
    "danger": "tense",
    "shock": "tense",
    "proud": "epic",
    "triumph": "epic",
    "hopeful": "hopeful",
    "tense": "tense",
    "epic": "epic",
}

MOOD_BPM = {
    "calm": 90,
    "hopeful": 100,
    "tense": 110,
    "epic": 120,
    "sad": 75,
}

CAMERA_LIST = ["push", "pull", "pan_left", "pan_right", "static"]

DIGIT_WORDS = {
    "một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5,
    "sáu": 6, "bảy": 7, "tám": 8, "chín": 9,
    "mốt": 1, "tư": 4, "lăm": 5,
}
TENS_MAP = {
    "hai": 20, "ba": 30, "bốn": 40, "năm": 50,
    "sáu": 60, "bảy": 70, "tám": 80, "chín": 90,
}

# Schema JSON cho từng cảnh gửi tới LLM
SCENE_SPEC = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "shot": {
                        "type": "string",
                        "enum": list(VALID_SHOTS),
                    },
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "use_image": {"type": "boolean"},
                    "stat": {
                        "anyOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "value": {"type": "number"},
                                    "unit": {"type": "string"},
                                    "label": {"type": "string"},
                                    "source": {"type": "string"},
                                },
                                "required": ["value", "unit", "label", "source"],
                                "additionalProperties": False,
                            },
                            {"type": "null"},
                        ]
                    },
                    "chart": {
                        "anyOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "kind": {"type": "string", "enum": ["bar", "line"]},
                                    "labels": {"type": "array", "items": {"type": "string"}},
                                    "values": {"type": "array", "items": {"type": "number"}},
                                    "unit": {"type": "string"},
                                },
                                "required": ["kind", "labels", "values", "unit"],
                                "additionalProperties": False,
                            },
                            {"type": "null"},
                        ]
                    },
                    "events": {
                        "anyOf": [
                            {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "year": {"type": "string"},
                                        "label": {"type": "string"},
                                    },
                                    "required": ["year", "label"],
                                    "additionalProperties": False,
                                },
                            },
                            {"type": "null"},
                        ]
                    },
                    "camera": {
                        "type": "string",
                        "enum": ["push", "static", "pull", "pan_left", "pan_right"],
                    },
                    "transition": {
                        "type": "string",
                        "enum": ["cut", "wipe", "slide", "zoom_through", "light_sweep", "fade"],
                    },
                    "emphasis": {
                        "type": "string",
                        "enum": ["low", "mid", "high"],
                    },
                },
                "required": [
                    "shot",
                    "title",
                    "text",
                    "use_image",
                    "stat",
                    "chart",
                    "events",
                    "camera",
                    "transition",
                    "emphasis",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scenes"],
    "additionalProperties": False,
}


def denormalize_vietnamese_numbers(text: str) -> str:
    """Chuyển chữ đọc số tiếng Việt thành chữ số viết gốc cho phụ đề."""
    if not text:
        return ""
    # Tên định danh khí tài
    text = re.sub(r"\bMiG-ba mươi lăm\b", "MiG-35", text, flags=re.I)
    text = re.sub(r"\bSu-ba mươi tư\b", "Su-34", text, flags=re.I)
    text = re.sub(r"\bSu-ba mươi lăm\b", "Su-35", text, flags=re.I)
    text = re.sub(r"\bSu-ba mươi\b", "Su-30", text, flags=re.I)
    text = re.sub(r"\bthế hệ bốn cộng cộng\b", "thế hệ 4++", text, flags=re.I)
    text = re.sub(r"\bbốn cộng cộng\b", "4++", text, flags=re.I)

    # Cụm số lượng với danh từ đếm
    counters = r"(?:chiếc|quả|máy bay|tiêm kích|con|bộ|đoàn|khẩu|tên lửa|đô la|USD|đồng|tỷ|triệu|nghìn|km|m|h|phút|giây)"
    for w, d in DIGIT_WORDS.items():
        if d > 0:
            text = re.sub(rf"\bvỏn vẹn {w} ({counters})\b", rf"vỏn vẹn {d} \1", text, flags=re.I)
            text = re.sub(rf"\bchỉ {w} ({counters})\b", rf"chỉ {d} \1", text, flags=re.I)
            text = re.sub(rf"\b{w} ({counters})\b", rf"{d} \1", text, flags=re.I)

    # Chữ số hàng mươi ghép: e.g. "ba mươi lăm" -> 35
    for tw, tv in TENS_MAP.items():
        for uw, uv in sorted(DIGIT_WORDS.items(), key=lambda x: -len(x[0])):
            text = re.sub(rf"\b{tw} mươi {uw}\b", str(tv + uv), text, flags=re.I)
        text = re.sub(rf"\b{tw} mươi\b", str(tv), text, flags=re.I)

    # mười ...
    for uw, uv in sorted(DIGIT_WORDS.items(), key=lambda x: -len(x[0])):
        text = re.sub(rf"\bmười {uw}\b", str(10 + uv), text, flags=re.I)
    text = re.sub(r"\bmười\b", "10", text, flags=re.I)

    # trăm
    text = re.sub(r"\bhàng trăm\b", "hàng 100", text, flags=re.I)
    for hw, hv in DIGIT_WORDS.items():
        if hv > 0:
            text = re.sub(rf"\b{hw} trăm\b", f"{hv}00", text, flags=re.I)
    text = re.sub(r"\btrăm\b", "100", text, flags=re.I)

    # dọn dẹp số tách rời nếu có (e.g. 40 5 -> 45)
    text = re.sub(r"\b(\d0)\s+([1-9])\b", lambda m: str(int(m[1]) + int(m[2])), text)
    return text


def _extract_numbers(text: str) -> set:
    """Trích xuất tất cả các số từ văn bản."""
    nums = set()
    for m in re.finditer(r"\d[\d.,]*", text):
        s = m.group(0).rstrip(".,")
        plain = s.replace(".", "").replace(",", "")
        if plain.isdigit():
            v = int(plain)
            nums.add(v)
            nums.add(float(v))
        for cand in [s.replace(",", "."), s]:
            try:
                v = float(cand)
                nums.add(v)
                if v.is_integer():
                    nums.add(int(v))
            except ValueError:
                pass
    return nums


GENERIC_WORDS = {
    "tại sao?", "tại sao", "chia sẻ", "mig-35", "sukhoi", "hồi", "rẽ mây", "bí ẩn",
    "chi tiết", "bắt đầu", "thực chiến", "bất ngờ", "so sánh", "áp lực", "đối phó",
    "hiếm hoi", "sự kiện", "điểm nóng", "bầy uav", "đánh sâu", "mưa uav", "bộc lộ",
    "giá trị", "chi phí", "người nói", "thiết bị bay", "lách địa hình", "bay thấp",
    "uav", "nga", "hồi hộp", "kỳ lạ", "số lượng", "giới hạn", "công nghệ", "săn uav",
    "tình hình", "tướng lĩnh", "chiến đấu", "bóng ma hiếm", "đánh chặn", "mạng lưới",
    "đa tầng", "tầm bay", "tầm xa", "s-300", "điểm nghẽn", "tiêm kích", "vỏn vẹn",
    "trị giá", "radar alert", "rare birds", "rare", "survival puzzle", "nhiệm vụ",
    "buồng lái", "không giống", "không thể", "triệu usd", "vài chục nghìn", "mưa máy bay",
    "hé lộ", "nguồn mở", "cổ máy", "giá rẻ", "lang thang", "tưởng", "thông thường",
    "bầu trời", "thế nhưng", "bàng hoàng", "chiến trường", "lãnh thổ nga", "cơ sở hạ tầng",
    "tần suất dồn dập", "lượng nổ", "liên tục", "hàng trăm km", "tiếng động cơ",
    "không cần tốc độ", "số lượng dồi dào", "thích thước nhỏ", "tên lửa đắt", "đối phó uav",
}


def partition_narration(text: str, num_chunks: int) -> list[str]:
    """Chia narration gốc thành num_chunks chuỗi con liên tiếp theo dấu câu và ranh giới từ."""
    text = text.strip()
    if num_chunks <= 1 or not text:
        return [text]

    # Tách mệnh đề theo các dấu câu
    raw_clauses = [c.strip() for c in re.split(r'(?<=[,;?!.—:])\s+', text) if c.strip()]
    if not raw_clauses:
        raw_clauses = [text]

    clauses = list(raw_clauses)
    # Nếu cần nhiều chunk hơn số clause hiện có, chia đôi clause dài nhất
    while len(clauses) < num_chunks:
        max_idx = max(range(len(clauses)), key=lambda i: len(clauses[i].split()))
        words = clauses[max_idx].split()
        if len(words) <= 3:
            break
        mid = len(words) // 2
        c1 = " ".join(words[:mid])
        c2 = " ".join(words[mid:])
        clauses = clauses[:max_idx] + [c1, c2] + clauses[max_idx + 1:]

    total_w = sum(len(c.split()) for c in clauses)
    target_per_chunk = total_w / num_chunks

    chunks = []
    c_idx = 0
    for k in range(num_chunks):
        if k == num_chunks - 1:
            chunks.append(" ".join(clauses[c_idx:]))
            break
        cur_clause = []
        cur_w = 0
        while c_idx < len(clauses) - (num_chunks - 1 - k):
            w_next = len(clauses[c_idx].split())
            if not cur_clause:
                cur_clause.append(clauses[c_idx])
                cur_w += w_next
                c_idx += 1
            elif abs((cur_w + w_next) - target_per_chunk) < abs(cur_w - target_per_chunk):
                cur_clause.append(clauses[c_idx])
                cur_w += w_next
                c_idx += 1
            else:
                break
        chunks.append(" ".join(cur_clause))
    return chunks


def resolve_title(llm_title: str, text_clause: str, keywords: list[str]) -> str:
    """Tạo hoặc chuẩn hóa tiêu đề là cụm ý chính 3-8 từ, cấm từ đơn chung chung."""
    if llm_title:
        clean_llm = llm_title.strip()
        words = clean_llm.split()
        if 3 <= len(words) <= 8 and clean_llm.lower() not in GENERIC_WORDS:
            return clean_llm

    # Tìm keyword 3-7 từ xuất hiện trong text_clause
    text_lower = text_clause.lower()
    for kw in keywords or []:
        kw_strip = kw.strip()
        kw_words = kw_strip.split()
        if 3 <= len(kw_words) <= 7 and kw_strip.lower() in text_lower:
            return kw_strip.capitalize()

    # Trích mệnh đề 3-7 từ từ text_clause
    clauses = [c.strip() for c in re.split(r'[,;?!.—:]\s*', text_clause) if c.strip()]
    for c in clauses:
        c_words = c.split()
        if 3 <= len(c_words) <= 7:
            return c.rstrip(".,;:?!")

    # Lấy 4-6 từ đầu của text_clause
    words = text_clause.strip().split()
    if len(words) >= 3:
        cut = min(6, len(words))
        return " ".join(words[:cut]).rstrip(".,;:?!")
    return " ".join(words) if words else "Chi tiết nổi bật"


def _truncate_words(text: str, max_words: int) -> str:
    """Cắt bớt chuỗi theo số từ tối đa."""
    if not text:
        return ""
    words = str(text).strip().split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


def _split_row_segments(dur: float, asr_words: list, max_dur: float) -> list[tuple[float, float, int, int]]:
    """Tách thời lượng row thành các đoạn con dài ≤ max_dur, ưu tiên ranh giới dấu câu trong asr_words."""
    min_dur = 3.0 if max_dur <= 4.0 else 3.5
    N = max(1, math.ceil(dur / max_dur))
    if N == 1 or not asr_words:
        if dur > max_dur:
            N = 2
        else:
            return [(0.0, round(dur, 2), 0, len(asr_words))]

    while dur / N > max_dur:
        N += 1

    step = dur / N
    segs = []
    cur_t = 0.0

    for k in range(N - 1):
        target_t = cur_t + step
        cand = [j for j, w in enumerate(asr_words) if cur_t + min_dur <= float(w[2] or w[1]) <= cur_t + max_dur]
        if cand:
            best_j = min(
                cand,
                key=lambda j: abs(float(asr_words[j][2] or asr_words[j][1]) - target_t)
                - (0.4 if any(asr_words[j][0].endswith(c) for c in ".!?;:,—") else 0),
            )
            t_end = round(float(asr_words[best_j][2] or asr_words[best_j][1]), 2)
        else:
            t_end = round(min(cur_t + max_dur, target_t), 2)

        rem_n = N - 1 - k
        if dur - t_end < rem_n * min_dur:
            t_end = round(dur - rem_n * min_dur, 2)
        if t_end - cur_t > max_dur:
            t_end = round(cur_t + max_dur, 2)

        segs.append((cur_t, t_end))
        cur_t = t_end

    if round(dur - cur_t, 2) > max_dur:
        mid = round(cur_t + (dur - cur_t) / 2.0, 2)
        segs.append((cur_t, mid))
        segs.append((mid, round(dur, 2)))
    else:
        segs.append((cur_t, round(dur, 2)))

    # Gán danh sách từ tương ứng cho từng đoạn
    final_res = []
    w_idx = 0
    for s0, s1 in segs:
        start_w = w_idx
        while w_idx < len(asr_words) and float(asr_words[w_idx][1]) < s1:
            w_idx += 1
        end_w = max(start_w + 1, w_idx)
        final_res.append((s0, s1, start_w, end_w))
    return final_res


def _build_batch_prompt(batch: list[dict], outline: dict, facts_text: str) -> str:
    """Prompt tiếng Việt ngắn gọn cho LLM điền JSON từng lô cảnh con."""
    scenes_desc = []
    for i, item in enumerate(batch):
        has_img = bool(item["img_src"])
        scenes_desc.append(
            f"- Cảnh {i + 1}: Lời đọc: \"{item.get('narration', '')}\". "
            f"Thời lượng: {item['dur']:.1f}s. Có ảnh: {has_img}"
        )

    topic = outline.get("title", "")
    return (
        f"Bạn là Đạo diễn Đồ họa Opuscar cho video: '{topic}'.\n"
        f"Nhiệm vụ: Điền cấu hình đồ họa (shot, title, text, stat/chart/events, camera, transition, emphasis, use_image) "
        f"cho {len(batch)} cảnh con dưới đây (mỗi cảnh dài 3-7s).\n\n"
        f"DANH SÁCH SHOT HỢP LỆ VÀ KHI DÙNG:\n"
        f"1. hero_image: Có ảnh minh họa, use_image=true.\n"
        f"2. kinetic_text: Chữ động typographic nhấn mạnh tuyên bố, use_image=false.\n"
        f"3. stat: Một con số thống kê khổng lồ (value, unit, label, source). BẮT BUỘC: Con số PHẢI có trong lời đọc hoặc facts. Không lặp cùng con số và đơn vị trong 60 giây.\n"
        f"4. chart: Biểu đồ cột/đường (kind='bar'|'line', labels, values, unit). BẮT BUỘC: Mọi số trong values PHẢI có trong lời đọc hoặc facts.\n"
        f"5. timeline: Mốc sự kiện (events: [{{year, label}}]). BẮT BUỘC: Năm PHẢI có trong lời đọc/facts.\n"
        f"6. quote_card: Trích dẫn câu nói (title: câu trích nguyên văn trong lời đọc ≤ 16 từ, text: nguồn/người nói ≤ 6 từ).\n"
        f"7. split_compare: Chỉ dùng khi có cả 2 ảnh đối sánh.\n\n"
        f"QUY TẮC BẮT BUỘC:\n"
        f"- 'title': BẮT BUỘC là cụm ý chính 3-8 từ; CẤM dùng 1 từ đơn chung chung (như 'Tại sao?', 'Chia sẻ', 'MiG-35'). Với quote_card, title là câu trích nguyên văn ≤ 16 từ.\n"
        f"- 'text': Khác title hoặc để rỗng (\"\"). Với quote_card, text là nguồn/người nói ≤ 6 từ.\n"
        f"- KHÔNG ĐƯỢC BỊA SỐ LIỆU: Nếu cảnh không có con số thật, đặt stat=null, chart=null, events=null.\n\n"
        f"DỮ KIỆN JOB (FACTS):\n{facts_text[:1200]}\n\n"
        f"DANH SÁCH CẢNH CẦN ĐIỀN:\n" + "\n".join(scenes_desc)
    )


def build_content(
    rows: list[dict],
    outline: dict,
    facts: list[dict] | str | None,
    cfg: Any,
    llm: Any,
    log: Any = print,
    fmt: str = "long",
    film_dir: Path | str = "_film",
) -> dict:
    """Biến các cảnh của AutoVideo thành content.json chuẩn opuscar engine."""
    film_dir = Path(film_dir).resolve()
    img_dir = film_dir / "img"
    img_dir.mkdir(parents=True, exist_ok=True)

    MAX_SHOT_DUR = 4.0 if fmt == "short" else 7.0

    # Chuẩn bị văn bản facts để đối chiếu số liệu
    facts_list = []
    if isinstance(facts, str):
        try:
            facts_list = json.loads(facts)
        except Exception:
            facts_list = [{"claim": facts}]
    elif isinstance(facts, list):
        facts_list = facts

    facts_text = " ".join(
        str(f.get("claim", "") or "") + " " + str(f.get("evidence", "") or "")
        for f in facts_list
        if isinstance(f, dict)
    )
    job_facts_numbers = _extract_numbers(facts_text)

    # 1. Tách từng row thành các shot con ≤ MAX_SHOT_DUR
    prepared_items = []
    current_time = 0.0
    seen_chapters = set()

    for row_idx, r in enumerate(rows):
        data = r.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        elif not isinstance(data, dict):
            data = {}

        narration = r.get("narration") or data.get("narration", "")
        duration_ms = r.get("duration_ms") or data.get("duration_ms") or 5000
        row_dur = max(1.0, float(duration_ms) / 1000.0)
        asr_words = data.get("asr_words") or r.get("asr_words") or []
        pause_before = float(r.get("pause_before") or data.get("pause_before") or 0.0)
        chapter_idx = int(r.get("chapter", 0))

        # Kiểm tra ảnh Flow nếu có
        img_src = r.get("image") or data.get("image")
        img_dst_rel = None
        if img_src and Path(img_src).is_file():
            src_p = Path(img_src)
            clean_name = f"row_{row_idx + 1:03d}_{src_p.name}"
            dst_p = img_dir / clean_name
            try:
                shutil.copy2(src_p, dst_p)
                img_dst_rel = f"img/{clean_name}"
            except Exception as e:
                log(f"[plan] Row {row_idx + 1}: Lỗi chép ảnh {src_p} -> {e}")

        # Kiểm tra xem có phải mở đầu chương mới không (mỗi chương tối đa 1 chapter_card)
        prev_chapter = rows[row_idx - 1].get("chapter", 0) if row_idx > 0 else 0
        is_new_chapter = (chapter_idx not in seen_chapters) and (
            (row_idx > 0 and chapter_idx > prev_chapter)
            or (row_idx == 1 and chapter_idx == 0)
            or (r.get("transition") == "chapter")
            or (row_idx > 0 and pause_before >= 1.0)
        )

        # Nếu là đầu chương và thời lượng ≥ 5.0s: tách 4.2s đầu làm chapter_card (yêu cầu: 3-5 giây)
        if is_new_chapter and row_dur >= 5.0:
            seen_chapters.add(chapter_idx)
            ch_dur = 4.0 if fmt == "short" else 4.2  # 3-5 giây, ≤4.0s với short và ≤7.0s với long, đủ thời gian cho readcheck
            t0 = round(current_time, 2)
            t1 = round(t0 + ch_dur, 2)
            current_time = t1

            chapters = outline.get("chapters", [])
            ch_title = chapters[chapter_idx].get("title", "") if chapter_idx < len(chapters) else ""
            title_text = f"Phần {chapter_idx + 1} — {ch_title}" if ch_title else f"Phần {chapter_idx + 1}"

            prepared_items.append({
                "row_idx": row_idx,
                "is_chapter_card": True,
                "chapter_title": title_text,
                "t0": t0,
                "t1": t1,
                "dur": ch_dur,
                "narration": "",
                "asr_words": [],
                "img_src": None,
                "img_rel": None,
                "cam_idx": 0,
                "keywords": r.get("keywords") or data.get("keywords") or [],
            })

            rem_dur = row_dur - ch_dur
            rem_words = asr_words
            # Phần còn lại của row tách thành các shot con ≤ MAX_SHOT_DUR
            segs = _split_row_segments(rem_dur, rem_words, MAX_SHOT_DUR)
            chunks = partition_narration(narration, len(segs))
            for seg_i, (s0, s1, w0, w1) in enumerate(segs):
                st0 = round(current_time, 2)
                st1 = round(st0 + (s1 - s0), 2)
                current_time = st1
                seg_words = rem_words[w0:w1] if rem_words else []
                seg_text = chunks[seg_i] if seg_i < len(chunks) else narration

                prepared_items.append({
                    "row_idx": row_idx,
                    "is_chapter_card": False,
                    "chapter_title": "",
                    "t0": st0,
                    "t1": st1,
                    "dur": round(s1 - s0, 2),
                    "narration": seg_text,
                    "asr_words": seg_words,
                    "img_src": img_src,
                    "img_rel": img_dst_rel,
                    "cam_idx": seg_i,
                    "keywords": r.get("keywords") or data.get("keywords") or [],
                })
        else:
            # Row bình thường: tách thành các shot con ≤ MAX_SHOT_DUR
            segs = _split_row_segments(row_dur, asr_words, MAX_SHOT_DUR)
            chunks = partition_narration(narration, len(segs))
            for seg_i, (s0, s1, w0, w1) in enumerate(segs):
                st0 = round(current_time, 2)
                st1 = round(st0 + (s1 - s0), 2)
                current_time = st1
                seg_words = asr_words[w0:w1] if asr_words else []
                seg_text = chunks[seg_i] if seg_i < len(chunks) else narration

                prepared_items.append({
                    "row_idx": row_idx,
                    "is_chapter_card": False,
                    "chapter_title": "",
                    "t0": st0,
                    "t1": st1,
                    "dur": round(s1 - s0, 2),
                    "narration": seg_text,
                    "asr_words": seg_words,
                    "img_src": img_src,
                    "img_rel": img_dst_rel,
                    "cam_idx": seg_i,
                    "keywords": r.get("keywords") or data.get("keywords") or [],
                })

    # Đánh số ID cho các cảnh con
    for idx, item in enumerate(prepared_items):
        item["id"] = f"{'q' if fmt == 'short' else 's'}{idx + 1:03d}"

    # 2. Gọi LLM theo lô 6-8 cảnh con (bỏ qua chapter_card vì đã cố định)
    BATCH_SIZE = 7
    llm_sub_items = [it for it in prepared_items if not it["is_chapter_card"]]
    llm_results_map: dict[str, dict | None] = {}
    so_lo_thanh_cong = 0
    so_lo_that_bai = 0

    for b_start in range(0, len(llm_sub_items), BATCH_SIZE):
        batch = llm_sub_items[b_start : b_start + BATCH_SIZE]
        if llm is None:
            so_lo_that_bai += 1
            for it in batch:
                llm_results_map[it["id"]] = None
            continue

        prompt = _build_batch_prompt(batch, outline, facts_text)
        messages = [
            {"role": "system", "content": "Bạn là Đạo diễn Đồ họa Opuscar. Trả về đúng JSON theo schema."},
            {"role": "user", "content": prompt},
        ]

        try:
            res = llm.chat(messages, schema=SCENE_SPEC, temperature=0.3, max_tokens=6000)
            sc_list = res.get("scenes") if isinstance(res, dict) else None
            if isinstance(sc_list, list) and len(sc_list) == len(batch):
                so_lo_thanh_cong += 1
                for j, item_spec in enumerate(sc_list):
                    llm_results_map[batch[j]["id"]] = item_spec
            else:
                so_lo_that_bai += 1
                log(f"[plan] LLM trả về số cảnh ({len(sc_list) if sc_list else 0}) != {len(batch)}, dùng fallback")
                for it in batch:
                    llm_results_map[it["id"]] = None
        except Exception as e:
            so_lo_that_bai += 1
            log(f"[plan] Lỗi gọi LLM ở lô {b_start}-{b_start + len(batch)}: {e} -> dùng fallback")
            for it in batch:
                llm_results_map[it["id"]] = None

    # 3. Lắp ráp và KIỂM TRA CỨNG bằng Python
    final_scenes = []
    rejected_stats_count = 0
    recent_stats = {}  # (val_str, unit_str) -> t1 (để tránh lặp stat trong 60s)

    for idx, item in enumerate(prepared_items):
        sua_gi = []
        is_llm = False
        shot_llm = None

        if item["is_chapter_card"]:
            shot = "chapter_card"
            title = item["chapter_title"]
            text = ""
            camera = "static"
            transition = "light_sweep"
            emphasis = "high"
            use_image = False
            stat = None
            chart = None
            events = None
            subtitles = []  # chapter_card không cần thanh phụ đề
            sua_gi.append("chapter_card_dau_chuong")
        else:
            spec = llm_results_map.get(item["id"])
            narration = item["narration"]
            has_image = bool(item["img_rel"])
            cam_fallback = CAMERA_LIST[item["cam_idx"] % len(CAMERA_LIST)]
            scene_numbers = _extract_numbers(narration) | job_facts_numbers

            if spec and isinstance(spec, dict):
                is_llm = True
                shot = spec.get("shot")
                shot_llm = shot
                if shot not in VALID_SHOTS or shot == "chapter_card":
                    shot = "hero_image" if has_image else "kinetic_text"
                    sua_gi.append("doi_shot_khong_hop_le")

                title = spec.get("title", "")
                text = spec.get("text", "")
                use_image = bool(spec.get("use_image")) if has_image else False
                stat = spec.get("stat")
                chart = spec.get("chart")
                events = spec.get("events")
                camera = spec.get("camera") or cam_fallback
                transition = spec.get("transition") or "cut"
                emphasis = spec.get("emphasis") or "mid"

                # split_compare chỉ chọn khi có đủ 2 ảnh
                if shot == "split_compare":
                    shot = "hero_image" if has_image else "kinetic_text"
                    use_image = has_image
                    sua_gi.append("split_compare_thieu_anh2")

                # quote_card: title là câu trích nguyên văn trong narration (≤ 16 từ), text là nguồn (≤ 6 từ)
                if shot == "quote_card":
                    title = _truncate_words(narration, 16)
                    src_cand = spec.get("text", "") or spec.get("title", "") or "Chuyên gia quân sự"
                    text = _truncate_words(src_cand, 6)
                    if text == title or text.strip() == "":
                        text = "Chuyên gia quân sự"
                    sua_gi.append("quote_card_chuan_hoa")

                # Kiểm tra số liệu stat/chart/timeline
                stat_rejected = False
                if stat and isinstance(stat, dict):
                    val = stat.get("value")
                    if val is None or (val not in scene_numbers and float(val) not in scene_numbers):
                        stat_rejected = True
                        sua_gi.append("loai_stat_so_bia")
                    else:
                        # Quy tắc: Không lặp cùng một stat (cùng value + unit) trong 60 giây
                        val_key = str(val).strip().lower()
                        unit_key = str(stat.get("unit", "")).strip().lower()
                        stat_key = (val_key, unit_key)
                        t0 = item["t0"]
                        if stat_key in recent_stats and (t0 - recent_stats[stat_key]) < 60.0:
                            stat_rejected = True
                            sua_gi.append("tranh_lap_stat_60s")
                        else:
                            recent_stats[stat_key] = item["t1"]

                if chart and isinstance(chart, dict):
                    vals = chart.get("values") or []
                    if not vals or any(v not in scene_numbers and float(v) not in scene_numbers for v in vals):
                        stat_rejected = True
                        sua_gi.append("loai_chart_so_bia")

                if events and isinstance(events, list):
                    if not events:
                        stat_rejected = True
                    for ev in events:
                        y = str(ev.get("year", ""))
                        ev_nums = _extract_numbers(y)
                        if not ev_nums or not ev_nums.issubset(scene_numbers):
                            stat_rejected = True
                            sua_gi.append("loai_timeline_nam_bia")
                            break

                if stat_rejected or (shot in ("stat", "chart", "timeline") and (stat_rejected or not (stat or chart or events))):
                    rejected_stats_count += 1
                    stat = None
                    chart = None
                    events = None
                    shot = "hero_image" if has_image else "kinetic_text"
                    use_image = has_image
            else:
                # Fallback tất định
                is_llm = False
                shot_llm = None
                shot = "hero_image" if (has_image and item["cam_idx"] % 2 == 0) else "kinetic_text"
                title = resolve_title("", narration, item.get("keywords", []))
                text = ""
                use_image = has_image and shot == "hero_image"
                stat = None
                chart = None
                events = None
                camera = cam_fallback
                transition = "cut"
                emphasis = "mid"
                sua_gi.append("fallback_tat_dinh")

            # Chuẩn hóa title và text cho các loại shot thông thường
            if shot in ("hero_image", "kinetic_text"):
                title = resolve_title(title, narration, item.get("keywords", []))
                text = ""  # text khác title hoặc để rỗng, tránh trùng nhau và đạt chuẩn readcheck
            elif shot == "stat":
                title = (stat.get("label") if stat else "") or resolve_title(title, narration, item.get("keywords", []))
                text = ""
            elif shot in ("chart", "timeline"):
                title = resolve_title(title, narration, item.get("keywords", []))
                text = ""

            # Phụ đề lấy trực tiếp từ narration gốc (tách câu của row)
            sub_text = item["narration"].strip()
            t0 = item["t0"]
            t1 = item["t1"]
            c_t0 = round(t0 + 0.1, 2)
            c_t1 = round(max(c_t0 + 1.8, t1 - 0.1), 2)
            if c_t1 > t1:
                c_t1 = round(t1, 2)
            if c_t1 - c_t0 < 1.8 and c_t1 - 1.8 >= t0:
                c_t0 = round(c_t1 - 1.8, 2)
            subtitles = [{"t0": c_t0, "t1": c_t1, "text": sub_text}] if sub_text else []

        # Cảnh đầu tiên (hook) luôn là hero_image hoặc kinetic_text
        if idx == 0:
            if shot not in ("hero_image", "kinetic_text"):
                shot = "hero_image" if item["img_rel"] else "kinetic_text"
                sua_gi.append("hook_canh_dau")
            use_image = bool(item["img_rel"]) and shot == "hero_image"
            stat = None
            chart = None
            events = None

        # Không 2 cảnh liền kề cùng loại shot
        if final_scenes:
            prev_shot = final_scenes[-1]["shot"]
            if shot == prev_shot:
                if shot == "hero_image":
                    shot = "kinetic_text"
                    use_image = False
                    title = resolve_title("", item["narration"], item.get("keywords", []))
                    text = ""
                elif shot == "kinetic_text":
                    if item["img_rel"]:
                        shot = "hero_image"
                        use_image = True
                        title = resolve_title("", item["narration"], item.get("keywords", []))
                        text = ""
                    else:
                        shot = "quote_card"
                        title = _truncate_words(item["narration"], 16)
                        text = "Chuyên gia quân sự"
                else:
                    shot = "hero_image" if item["img_rel"] else "kinetic_text"
                    use_image = bool(item["img_rel"]) and shot == "hero_image"
                    stat = None
                    chart = None
                    events = None
                    title = resolve_title("", item["narration"], item.get("keywords", []))
                    text = ""
                sua_gi.append("tranh_trung_shot")

        # Đảm bảo title != text ở mọi shot
        if text == title:
            text = ""

        # Với shot ngắn trong bản short (dur < 3.2s), giới hạn độ dài title để pass readcheck
        # Trừ hao ~0.45s do intro animation của engine
        if fmt == "short" and item["dur"] < 3.2 and shot != "quote_card":
            max_need = max(1.5, item["dur"] - 0.45)
            allowed_chars = max(4, int((max_need - 1.5) * 15))
            if len(title.replace(" ", "")) > allowed_chars:
                words = title.split()
                cur = ""
                for w in words:
                    if len((cur + " " + w).replace(" ", "")) <= allowed_chars:
                        cur = (cur + " " + w).strip()
                    else:
                        break
                title = cur if cur else title[:allowed_chars].rstrip(".,;:?! ")

        # Đổi camera liên tục cho các shot con
        if item["img_rel"] and shot == "hero_image":
            camera = CAMERA_LIST[item["cam_idx"] % len(CAMERA_LIST)]

        image_path = item["img_rel"] if (use_image and item["img_rel"]) else None

        scene_dict = {
            "id": item["id"],
            "row_idx": item["row_idx"],
            "t0": item["t0"],
            "t1": item["t1"],
            "shot": shot,
            "image": image_path,
            "title": title,
            "text": text,
            "camera": camera,
            "transition": transition,
            "emphasis": emphasis,
            "subtitle": subtitles,
            "stat": stat,
            "chart": chart,
            "events": events,
            "image2": None,
            "_debug": {
                "llm": is_llm,
                "shot_llm": shot_llm,
                "sua_gi": sua_gi,
            },
        }
        final_scenes.append(scene_dict)

    content = {
        "look": "dark-keynote",
        "fps": 24,
        "lang": "vi",
        "scenes": final_scenes,
    }
    if fmt == "short":
        content["format"] = "short"

    log(
        f"[plan] Đã tạo content.json ({len(final_scenes)} cảnh, {rejected_stats_count} số liệu bị loại). "
        f"LM Studio: {so_lo_thanh_cong} thành công, {so_lo_that_bai} thất bại. "
        f"Tổng thời lượng: {final_scenes[-1]['t1'] if final_scenes else 0:.2f}s"
    )
    return content


def timeline(content: dict, rows: list[dict], film_dir: Path | str | None = None) -> dict:
    """Tạo timeline.json cho phần âm thanh: {DUR, sections: [{t0, t1, mood, bpm}]}.

    Mood ánh xạ từ emotion của cảnh, gộp các cảnh liền kề cùng mood.
    Ghi vào film_dir/timeline.json nếu film_dir được chỉ định.
    """
    scenes = content.get("scenes", [])
    if not scenes:
        tl = {"DUR": 0.0, "sections": []}
        if film_dir:
            p = Path(film_dir) / "timeline.json"
            p.write_text(json.dumps(tl, ensure_ascii=False, indent=2), encoding="utf-8")
        return tl

    # Tạo timeline bám theo scenes của content
    raw_sections = []
    for sc in scenes:
        emotion = "calm"
        mood = EMOTION_TO_MOOD.get(emotion, "calm")
        bpm = MOOD_BPM.get(mood, 90)

        raw_sections.append({
            "t0": sc["t0"],
            "t1": sc["t1"],
            "mood": mood,
            "bpm": bpm,
        })

    # Gộp cảnh liền cùng mood
    merged_sections = []
    for sec in raw_sections:
        if merged_sections and merged_sections[-1]["mood"] == sec["mood"]:
            merged_sections[-1]["t1"] = sec["t1"]
        else:
            merged_sections.append({
                "t0": sec["t0"],
                "t1": sec["t1"],
                "mood": sec["mood"],
                "bpm": sec["bpm"],
            })

    total_dur = round(scenes[-1]["t1"], 2)
    tl = {
        "DUR": total_dur,
        "sections": merged_sections,
    }

    if film_dir:
        film_p = Path(film_dir)
        film_p.mkdir(parents=True, exist_ok=True)
        (film_p / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=2), encoding="utf-8")

    return tl


def write_film(content: dict, film_dir: Path | str) -> Path:
    """Ghi index.html, content.json và gọi film.prepare_film(film_dir) để chuẩn bị runtime."""
    film_p = Path(film_dir).resolve()
    film_p.mkdir(parents=True, exist_ok=True)

    is_short = content.get("format") == "short"
    w, h = (1080, 1920) if is_short else (1920, 1080)

    index_html = film_p / "index.html"
    index_html.write_text(
        f"<!doctype html>\n"
        f'<html lang="vi">\n'
        f"<head>\n"
        f'<meta charset="utf-8">\n'
        f"<title>opuscar film</title>\n"
        f"<style>\n"
        f"  html, body {{ margin: 0; padding: 0; height: 100%; background: #07080B; overflow: hidden; }}\n"
        f"  canvas#film {{ display: block; width: 100vw; height: 100vh; }}\n"
        f"</style>\n"
        f"</head>\n"
        f"<body>\n"
        f'<canvas id="film" width="{w}" height="{h}"></canvas>\n'
        f'<script type="module" src="engine/index.js"></script>\n'
        f"</body>\n"
        f"</html>\n",
        encoding="utf-8",
    )

    content_json = film_p / "content.json"
    content_json.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    film.prepare_film(film_p)
    return content_json
