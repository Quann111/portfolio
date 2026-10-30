# AGENTS.md – Luật bắt buộc cho mọi AI agent sửa code AutoVideo

Đọc file này trước khi sửa code. Claude Code đọc qua `CLAUDE.md` (trỏ về đây); Codex/Cursor/Gemini CLI đọc `AGENTS.md`.

## 1. Luật hình ảnh chung (BẮT BUỘC – mọi style, kể cả style thêm sau này)

Nguồn duy nhất: **`app/visual_rules.py`**. Không viết lại luật chữ/số/cờ/chân dung ở chỗ khác.

| Được (và nên) vẽ | Cấm |
|---|---|
| Con số, mốc thời gian, đơn vị có trong lời đọc (`15 km`, `1945`, `26.000 đồng`) – ghi đúng nguyên văn | Watermark, logo không liên quan |
| Quốc kỳ, biểu trưng đúng mẫu (màu, sọc, sao, bố cục) | Chữ rác / chữ giả / chữ vô nghĩa, chữ sai chính tả |
| Chân dung nhận ra được của người thật có tên (mặt, tóc, kính, trang phục) – không người que, không mặt trống | Câu chú thích dài, chữ ngẫu nhiên không có trong lời đọc |

Cách áp dụng khi viết / sửa một nơi tạo ảnh:

1. **Prompt gửi model ảnh** (Flow, FLUX, style mới…): ghép `visual_rules.PROMPT_RULE` vào phần đầu prompt (phần
   không bị cắt khi prompt dài).
2. **Mô tả cảnh do LLM viết** (`image_prompt`, `visual_concept`, dữ liệu job cũ): chạy `visual_rules.strip_legacy()`
   trước khi ghép – xoá câu kiểu cũ "no text, no numbers…" mâu thuẫn với luật.
3. **Chỉ dẫn cho LLM viết prompt** (Gemini, NotebookLM, Qwen): dùng `WRITER_RULE_VI` / `WRITER_RULE_EN`.
4. **QC ảnh**: tiêu chí `has_text` dùng `QC_TEXT` / `QC_TEXT_VI` (chỉ trượt khi có chữ rác, KHÔNG trượt vì số liệu,
   cờ, chân dung đúng). Lượt tạo lại vì `has_text` dùng `FIX_TEXT`.
5. **Ngoại lệ duy nhất**: nền thumbnail YouTube (`app/thumbnail.py`) không có chữ vì app tự in tiêu đề lên – nhưng cờ
   và chân dung vẫn theo luật chung (`FLAG_RULE`, `PEOPLE_RULE`).
6. Không thêm "no text / no numbers / no letters / blank flag / stick figure / faceless" vào `config.yaml`,
   `channel.yaml` (`bible.visual.avoid`) hay prompt mới. Muốn đổi luật → sửa `app/visual_rules.py`, rồi cập nhật
   bảng trên.

Nơi đang dùng luật: `app/image.py` (Flow + local + QC), `app/director.py` (Qwen + Gemini), `app/notebook.py`
(NotebookLM), `app/styles/nikola.py`, `app/thumbnail.py`, `app/youtube.py` (QC thumbnail).

## 2. Đa luồng (worker)

- `Worker._prep_loop` chạy 2 bản sao `copy.copy(worker)`. Trạng thái dùng chung phải nằm trong đối tượng được chia sẻ
  (vd `self._shared["tts"]`), không gán thuộc tính mới trên bản sao.
- Bản sao luồng chuẩn bị (`_prep = True`) **không** được dọn tài nguyên toàn cục (`kill_stale`, `free_torch`,
  `tts.release`) – sẽ làm hỏng job luồng chính.
- Dùng TTS ngoài bước tts → bọc `with self._tts_lock`.
- Ghi `jobs.steps`: chỉ qua các hàm của `app/pipeline.py` (đã có khoá `_lock`).
- Gọi LM Studio: chỉ qua `LLM.chat/look` (đã giới hạn số request đồng thời = `lmstudio.parallel`).

## 3. Khác

- Đường dẫn trong config: tương đối với thư mục app hoặc `~/...` (qua `config.path`); không ghi cứng `C:/Users/<tên>`.
- Model LLM hiện tại: một model `qwen/qwen3-vl-4b` cho text + vision + light.
- Kiểm tra nhanh: `.venv\Scripts\python.exe tests\test_units.py` và `python -m pyflakes app`.
