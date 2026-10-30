"""LUẬT HÌNH ẢNH CHUNG cho toàn dự án – mọi prompt ảnh (mọi style: classic, nikola, thumbnail, style thêm sau này),
mọi chỉ dẫn cho LLM viết prompt (Gemini / NotebookLM / Qwen) và mọi QC ảnh PHẢI lấy luật từ đây, không tự viết lại.

Nội dung luật:
- ĐƯỢC vẽ: con số / mốc thời gian / đơn vị có trong lời đọc (vd "15 km", "1945", "26.000 đồng"), quốc kỳ và biểu
  trưng đúng mẫu, chân dung nhận ra được của người thật có tên trong lời đọc.
- CẤM: watermark, logo thương hiệu không liên quan, chữ rác / chữ giả / chữ vô nghĩa, câu chú thích dài, chữ ngẫu
  nhiên không do lời đọc yêu cầu.
- QC chỉ đánh trượt chữ rác/ngẫu nhiên – KHÔNG đánh trượt số liệu, cờ, chân dung đúng.

Style mới: ghép `PROMPT_RULE` vào phần đầu prompt (phần không bị cắt), chạy `strip_legacy()` lên mô tả cảnh do LLM
viết, dùng `QC_TEXT` cho tiêu chí has_text và `FIX_TEXT` khi tạo lại vì has_text. Xem AGENTS.md."""
import re

# ---- ghép vào prompt gửi model ảnh (tiếng Anh)
TEXT_RULE = ("NO RANDOM TEXT: no watermark, no unrelated logos, no captions or sentences, no gibberish or pseudo-text, "
             "no random letters; signs, screens and papers stay blank unless the scene names a figure for them. "
             "ALLOWED and encouraged: the key numbers, dates and units named in the scene (e.g. '15 km', '1945'), "
             "drawn large, clean and spelled exactly")
FLAG_RULE = ("National flags, emblems and symbols named in the scene are drawn accurately (correct colours, stripes, "
             "stars and layout)")
PEOPLE_RULE = ("A real, named person is drawn as a clear, recognizable portrait with their characteristic face, hair, "
               "glasses and clothing – never a faceless, generic or stick figure; keep the faces clean and well drawn")
PROMPT_RULE = ". ".join([TEXT_RULE, FLAG_RULE, PEOPLE_RULE])

# ---- chỉ dẫn cho LLM viết prompt ảnh (Gemini / NotebookLM / Qwen)
WRITER_RULE_VI = ("Được và nên đưa vào tranh: con số / mốc thời gian / đơn vị có trong lời đọc (ghi đúng nguyên văn, "
                  "vd '15 km', '1945'), quốc kỳ và biểu trưng đúng mẫu, chân dung nhận ra được của người thật có tên "
                  "(tả đặc điểm khuôn mặt, tóc, kính, trang phục). Chỉ cấm: watermark, logo không liên quan, chữ rác / "
                  "chữ giả, câu chú thích dài, chữ ngẫu nhiên không có trong lời đọc.")
WRITER_RULE_EN = ("Include the key numbers, dates and units from the narration (spelled exactly), accurate flags/emblems "
                  "of named countries or organisations, and recognizable portraits of named real people. Forbidden "
                  "only: watermarks, unrelated logos, captions/sentences, gibberish or random text.")

# ---- QC ảnh
QC_TEXT = ("has_text: true ONLY for unwanted writing – watermark, unrelated logo, gibberish/pseudo-text, misspelled or "
           "random words, long captions. Numbers, dates and units that match the narration, accurate flags/emblems "
           "and a tiny signature in a corner are NOT has_text")
QC_TEXT_VI = ("has_text: CHỈ true khi có chữ không mong muốn – watermark, logo lạ, chữ rác/chữ giả, chữ sai chính tả, "
              "câu chú thích dài. Con số / mốc thời gian đúng nội dung, cờ / biểu trưng đúng mẫu KHÔNG tính")
FIX_TEXT = ("remove the watermark and any gibberish or random writing; keep only the exact key figures, flags and "
            "emblems named in the scene")

# ---- dọn câu cấm chữ kiểu cũ trong mô tả cảnh do LLM viết / dữ liệu job cũ (mâu thuẫn với luật trên)
_LEGACY = re.compile(r"(?i)[,;.]?\s*\b(?:absolutely\s+)?no\s+(?:writing|text|letters|numbers|labels|words)\b"
                     r"(?:\s*(?:,|or|and)\s*(?:no\s+)?(?:writing|text|letters|numbers|labels|words|symbols|dates))*"
                     r"(?:\s+anywhere)?")


def strip_legacy(p: str) -> str:
    return re.sub(r"\s{2,}", " ", _LEGACY.sub("", p or "")).strip(" ,;")
