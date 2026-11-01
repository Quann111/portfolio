# AGENTS.md – Luật Phát Triển & Quy Chuẩn Hệ Thống AutoVideo (Single Source of Truth)

> **LƯU Ý QUAN TRỌNG CHO TẤT CẢ AGENT / AI / DEVELOPER**:
> Toàn bộ quy chuẩn kiến trúc, luật hình ảnh, kiểm soát đa luồng và các nguyên tắc bảo vệ hệ thống của dự án `autovideo` được tập trung duy nhất tại file này. Mọi thay đổi mã nguồn phải tuân thủ nghiêm ngặt các điều khoản dưới đây.

---

## 1. QUY ĐỊNH BẤT DI BẤT DỊCH (CẤM ĐỘNG VÀO / CẤM PHÁ VỠ)

Khối tính năng mở trình duyệt tự động và hệ thống Extension Chrome (Google Flow, NotebookLM, Gemini) đã được tối ưu hóa hoàn chỉnh và hoạt động ổn định 100% (bản backup lưu tại `backup_extensions/`). **CẤM TỰ Ý SỬA ĐỔI, TỐI ƯU HOÁ HOẶC THAY THẾ** các cơ chế sau:

1. **Khởi động Chrome tự động (`Open_Chrome_Profiles.bat`)**:
   - Cả 2 tệp `D:\AI\truyen\Open_Chrome_Profiles.bat` và `autovideo\Open_Chrome_Profiles.bat` PHẢI luôn nạp đủ cả 3 extension:  
     `--load-extension="...chrome_ext,...chrome_ext_notebook,...chrome_ext_gemini"`.
   - Các profile nghiên cứu kịch bản (VR - Profile 3, qqkun2 - Profile 33) PHẢI mở đồng thời cả 3 tab: `FLOW_NEW_URL`, `NOTEBOOK_URL` (`notebooklm.google.com`), và `GEMINI_URL` (`gemini.google.com`).

2. **Cơ chế tự sửa lỗi Flow khi gặp Captcha (`chrome_ext/background.js` & `app/api.py`)**:
   - Khi bấm **Tự sửa lỗi (repair)** trên profile Flow: Sau khi reload trang, extension gọi `diagnose()`. Nếu phát hiện `captcha` hoặc xác minh `not a robot`:
     - **BẮT BUỘC** dừng lại ở trạng thái **Tạm dừng (`paused`)** với thông báo yêu cầu người dùng tự giải captcha trên tab Chrome.
     - **CẤM** tự ý xóa cờ `paused` hay tự động gửi prompt tạo ảnh tiếp khi chưa giải.
     - **BẮT BUỘC** người dùng phải bấm nút **"Tiếp tục profile này"** sau khi đã vượt qua Captcha thì profile mới được phép hoạt động lại.

3. **Cơ chế tự động tạo sổ mới trên NotebookLM (`chrome_ext_notebook/content.js` & `background.js`)**:
   - **Regex nút sổ mới (`NEW_NB`)**: PHẢI luôn chứa `/(sổ ghi chú mới|tạo sổ ghi chú|tạo mới|create new|new notebook|create notebook|\+\s*sổ ghi chú)/i` để khớp đúng giao diện Google NotebookLM tiếng Việt (`+ Sổ ghi chú mới`). CẤM xóa `sổ ghi chú mới`.
   - **Auto-Clicker (`initAutoClicker`)**: Tự động kiểm tra URL, nếu ở trang chủ chưa có ID sổ `/notebook/`, sau 1.5s PHẢI tự click nút `+ Sổ ghi chú mới` để tab luôn sẵn sàng vào thẳng sổ ghi chú mà không cần thao tác tay.
   - **Sửa lỗi NotebookLM (`autoRepairNb`)**: Khi reload trang xong, nếu tab đang ở trang chủ, PHẢI tự động gọi `newNotebook(tab)` để mở sẵn một sổ mới.

4. **Cơ chế tự sửa lỗi Gemini (`chrome_ext_gemini/background.js`)**:
   - Duy trì handler `action === 'repair'` qua `/gemini/ping` và hàm `autoRepairGemini(tab.id)` để tự nạp lại tab và kết nối lại khi có lỗi.

5. **Giám sát tự động Resume (`auto_resume_watcher.py` & `Chay_Auto_Resume.bat`)**:
   - Tự động kiểm tra định kỳ tình trạng captcha, tự động kích hoạt tiếp tục khi người dùng đã vượt qua xác minh mà không làm gián đoạn hàng đợi.

---

## 2. LUẬT HÌNH ẢNH CHUNG (VISUAL RULES)

Nguồn chân lý duy nhất trong code: **[`app/visual_rules.py`](file:///D:/AI/truyen/autovideo/app/visual_rules.py)**. Không viết lại luật chữ/số/cờ/chân dung ở bất kỳ module nào khác.

| ĐƯỢC (VÀ NÊN) VẼ | CẤM TUYỆT ĐỐI |
|---|---|
| **Con số, mốc thời gian, đơn vị đo** có trong lời đọc (`15 km`, `1945`, `26.000 đồng`, `80%`) – vẽ to, sạch sẽ, chuẩn xác | Watermark, logo không liên quan |
| **Quốc kỳ, biểu trưng** đúng chuẩn (chuẩn màu sắc, tỉ lệ, sọc, sao, bố cục) | Chữ rác, chữ giả (pseudo-text), chữ vô nghĩa, sai chính tả |
| **Chân dung người thật có tên** (khuôn mặt, tóc, kính, trang phục nhận diện được) – không vẽ người que, không để mặt trống vô danh | Câu chú thích dài dòng, chữ ngẫu nhiên không có trong lời đọc |

### Nguyên tắc áp dụng khi viết/sửa module tạo ảnh:
1. **Prompt gửi model ảnh (Google Flow, FLUX, style mới)**: Ghép `visual_rules.PROMPT_RULE` vào phần đầu prompt (tránh bị cắt cụt khi prompt dài).
2. **Mô tả cảnh do LLM viết (`image_prompt`, `visual_concept`)**: Luôn chạy `visual_rules.strip_legacy()` trước khi ghép để loại bỏ các câu cấm đoán kiểu cũ ("no text, no numbers...") gây xung đột.
3. **Chỉ dẫn cho LLM viết prompt (NotebookLM, Gemini, Qwen)**: Dùng chuẩn `WRITER_RULE_VI` / `WRITER_RULE_EN`.
4. **QC ảnh**: Tiêu chí `has_text` chỉ đánh trượt chữ rác/watermark (`QC_TEXT`, `QC_TEXT_VI`), KHÔNG trượt vì số liệu, cờ, chân dung đúng. Tạo lại do lỗi chữ dùng `FIX_TEXT`.
5. **Ngoại lệ thumbnail YouTube**: Nền thumbnail YouTube (`app/thumbnail.py`) tạo không có chữ sẵn vì app sẽ tự in chữ hoa cực lớn bằng Pillow, nhưng các yếu tố cờ và chân dung vẫn tuân theo `FLAG_RULE` và `PEOPLE_RULE`.

---

## 3. NGUYÊN TẮC ĐA LUỒNG & TÀI NGUYÊN (WORKER & RESOURCES)

1. **Luồng chuẩn bị trước (`Worker._prep_loop`)**:
   - Chạy 2 luồng song song chuẩn bị trước kịch bản cho 2-3 job tiếp theo trong hàng đợi.
   - Luồng chuẩn bị là bản sao nông (`copy.copy(worker)`), mang cờ `_prep = True`.
   - **CẤM**: Luồng chuẩn bị không được giải phóng tài nguyên toàn cục (`kill_stale`, `free_torch`, `tts.release`) vì sẽ phá hủy job đang render ở luồng chính.
2. **TTS Lock**: Bất kỳ module nào gọi TTS ngoài bước `tts` bắt buộc bọc trong `with self._tts_lock:`.
3. **Đồng bộ trạng thái**:
   - Ghi tiến trình `jobs.steps` chỉ qua các hàm của `app/pipeline.py` (đã có khoá bảo vệ `_lock`).
   - Gọi LM Studio duy nhất qua `LLM.chat/look` để kiểm soát số request song song theo cấu hình `lmstudio.parallel`.
4. **Bảo vệ dữ liệu & Xóa file**:
   - Khi xóa job (`packager.delete_job`), đưa thư mục vào Thùng rác hệ thống (Send2Trash) nếu có thể, kiểm tra file khóa, không xóa nhầm project khác.

---

## 4. XUẤT BẢN YOUTUBE & KIỂM TRA TOÀN DIỆN (YOUTUBE PACKAGE & AUDIT)

1. **Cấu trúc thư mục `projects/<id>_<slug>/youtube/` bắt buộc phải có đủ**:
   - Tiêu đề: `title.txt`, `title_vi.txt`, `title_en.txt`, `titles_vi.txt`, `titles_en.txt`
   - Mô tả: `description.txt`, `description_vi.txt`, `description_en.txt` (đầy đủ mốc chương thật 00:00, hashtag, disclaimer)
   - Thẻ & metadata: `tags.txt`, `youtube.json`
   - Thumbnail: `thumbnail.jpg` (1280x720, < 2 MB, typography chữ vàng viền đen nền đỏ) kèm các biến thể `thumbnail_A1..B3.jpg`.
   - Phụ đề: `subtitles_vi.srt`, `subtitles_en.srt`, `subtitles_sentences.json`
2. **Cơ chế tự sửa & Fallback an toàn**:
   - Khi NotebookLM trả về văn bản dạng danh sách có số mục thay vì JSON, module `app/youtube.py` tự động dùng `_parse_text_output()` để bóc tách thành công 100%.
   - Nếu Flow chưa tạo xong nền thumbnail, app tự động fallback lấy ảnh bìa hoặc ảnh cảnh đầu tiên để in typography, đảm bảo **không bao giờ thiếu `thumbnail.jpg`**.
3. **Nút "Kiểm tra & Bổ sung thiếu" (`POST /projects/audit_missing`)**:
   - Cung cấp nút bấm trên giao diện Web UI và API để tự động quét toàn bộ thư mục `projects/`, tìm kiếm các job đã xong video nhưng thiếu bất kỳ file xuất bản nào và tự động bổ sung ngay lập tức.

---

## 5. KIỂM THỬ & VẬN HÀNH

- **Kiểm tra đơn vị & chức năng**: Chạy lệnh `.venv\Scripts\python.exe -X utf8 tests/test_units.py` và `.venv\Scripts\python.exe -X utf8 tests/test_youtube.py`.
- **Đường dẫn**: Tuyệt đối không hardcode đường dẫn người dùng (`C:/Users/...`), sử dụng `config.path` hoặc đường dẫn tương đối với `ROOT`.
