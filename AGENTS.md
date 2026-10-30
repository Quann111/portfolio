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

## 3. Test từng bước

- Bảng "🧪 Test từng bước" (chỉ hiện khi app đã Dừng) gọi `app/selftest.py`; mỗi bước gọi lại đúng hàm production.
- Thêm / đổi một bước trong pipeline → thêm hoặc sửa hàm `t_<bước>` và dòng tương ứng trong `selftest.STEPS`.
- Test không chạy khi worker đang chạy (API trả 409) và ngược lại; file kết quả ở `exchange/selftest/`.

## 4. Khác

- Đường dẫn trong config: tương đối với thư mục app hoặc `~/...` (qua `config.path`); không ghi cứng `C:/Users/<tên>`.
- Model LLM hiện tại: một model `qwen/qwen3-vl-4b` cho text + vision + light.
- Kiểm tra nhanh: `.venv\Scripts\python.exe tests\test_units.py` và `python -m pyflakes app`.


## 5. Quy định BẤT DI BẤT DỊCH: Hệ thống Extension (Flow, NotebookLM, Gemini) & Mở Chrome tự động (CẤM ĐỘNG VÀO)

> **CẢNH BÁO TUYỆT ĐỐI**: Khối tính năng này đã hoạt động chính xác và hoàn hảo 100% theo đúng yêu cầu người dùng (đã backup tại `autovideo/backup_extensions/`). **CẤM TỰ Ý SỬA ĐỔI, TỐI ƯU HOÁ HOẶC THAY THẾ** các cơ chế dưới đây:

1. **Khởi động Chrome tự động (`Open_Chrome_Profiles.bat`)**:
   - Cả 2 file tại `D:\AI\truyen\Open_Chrome_Profiles.bat` và `autovideo\Open_Chrome_Profiles.bat` PHẢI luôn nạp đủ cả 3 extension: `--load-extension="...chrome_ext,...chrome_ext_notebook,...chrome_ext_gemini"`.
   - Các profile nghiên cứu kịch bản (VR - Profile 3, qqkun2 - Profile 33) PHẢI mở đồng thời cả 3 tab: `FLOW_NEW_URL`, `NOTEBOOK_URL` (`notebooklm.google.com`), và `GEMINI_URL` (`gemini.google.com`).

2. **Cơ chế tự sửa lỗi Flow khi dính Captcha (`chrome_ext/background.js` & `app/api.py`)**:
   - Khi bấm **Tự sửa lỗi (repair)** trên profile Flow: Sau khi reload trang, extension gọi `diagnose()`. Nếu phát hiện `captcha` hoặc xác minh `not a robot`:
     - **BẮT BUỘC** dừng lại ở trạng thái **Tạm dừng (`paused`)** với thông báo yêu cầu người dùng tự giải captcha trên tab Chrome.
     - **CẤM** tự ý xóa cờ `paused` hay tự động gửi prompt tạo ảnh tiếp.
     - **BẮT BUỘC** người dùng phải tự tay bấm nút **"Tiếp tục profile này"** sau khi đã vượt qua Captcha thì profile mới được phép hoạt động lại.

3. **Cơ chế tự động tạo sổ mới trên NotebookLM (`chrome_ext_notebook/content.js` & `background.js`)**:
   - **Regex nút sổ mới (`NEW_NB`)**: PHẢI luôn chứa `/(sổ ghi chú mới|tạo sổ ghi chú|tạo mới|create new|new notebook|create notebook|\+\s*sổ ghi chú)/i` để khớp đúng giao diện Google NotebookLM tiếng Việt thực tế (`+ Sổ ghi chú mới`). CẤM xóa `sổ ghi chú mới`.
   - **Auto-Clicker (`initAutoClicker`)**: Tự động kiểm tra URL, nếu đang ở trang chủ (`notebook.google.com` hoặc `notebooklm.google.com` chưa có ID sổ `/notebook/`), sau 1.5s PHẢI tự click nút `+ Sổ ghi chú mới` để tab luôn sẵn sàng vào thẳng sổ ghi chú mà không cần người dùng thao tác tay.
   - **Sửa lỗi NotebookLM (`autoRepairNb`)**: Khi reload trang xong, nếu tab đang ở trang chủ, PHẢI tự động gọi `newNotebook(tab)` để mở sẵn một sổ mới.

4. **Cơ chế tự sửa lỗi Gemini (`chrome_ext_gemini/background.js`)**:
   - Bắt buộc duy trì handler `action === 'repair'` qua `/gemini/ping` và hàm `autoRepairGemini(tab.id)` để tự nạp lại tab và kết nối lại khi có lỗi.
