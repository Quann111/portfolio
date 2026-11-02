# Plan: Tìm chủ đề hot qua NotebookLM thay cho model local (Qwen)

## Bối cảnh
- Hiện `topic_scout.candidates()` gọi `llm.chat(...)`, tức Qwen trên LM Studio, để nghĩ ra ứng viên chủ đề. Model nhỏ hay bịa và viết sai. Ví dụ ảnh chụp ngày 02/11:
  - "Tại sao người Hy Lạp cổ đại không xây đền thờ cho các vị thần nữ?": sai sự thật, đền Athena/Hera/Artemis có đầy.
  - "Cái gì khiến các nhà khảo cổ không tìm thấy mộ của pharaoh Tutankhamun?": sai, mộ được tìm thấy năm 1922.
  - "Đền thờ không có mặt trời?": vô nghĩa.
- Người dùng muốn **việc tìm chủ đề đi qua NotebookLM**, giống việc viết kịch bản (`script.engine: notebooklm`), không dùng model local.

## Cơ chế có sẵn để dùng lại (không viết mới)
- `app/notebook.py`:
  - `ask(jid, step, prompt, deadline, check, kind="ask")`: xếp prompt vào bảng `nb_tasks`, rồi extension Chrome "AutoVideo Notebook Bridge" gõ vào tab NotebookLM và trả về văn bản
  - `_ASK_LOCK` chống hai luồng gõ đè nhau
  - `MAX_PROMPT = 3200` ký tự
  - `START`/`END` đánh dấu đầu/cuối câu trả lời
  - `_json_block(text)` tách JSON từ câu trả lời
  - `Unavailable` khi extension tắt/captcha/quá giờ
  - `paused()` và `connected()` để biết trạng thái bridge
- `nb_tasks.job_id`: việc tìm chủ đề không thuộc job nào → dùng `jid = 0`. Kiểm tra `cancel_job(0)` không huỷ nhầm task của job khác; nếu `job_id` không cho NULL/0 thì xử lý cho đúng.

## Thay đổi

### 1. `config.yaml` → `auto_topics`
```yaml
  engine: notebooklm        # notebooklm | qwen – ai nghĩ ra chủ đề
  nb_wait_minutes: 15       # chờ NotebookLM trả lời tối đa
```
**Không** tự quay về Qwen khi NotebookLM lỗi (giống `script.fallback_qwen: false`): báo lỗi rõ ràng và để lượt sau thử lại.

### 2. `app/topic_scout.py`
- Tách phần sinh ứng viên trong `candidates()` thành `_gen_qwen(prompt, schema, llm)` (code cũ giữ nguyên) và `_gen_notebooklm(prompt, cfg, log, check)`. `candidates()` chọn theo `acfg(cfg)["engine"]`.
- `_gen_notebooklm`:
  - Dựng prompt **tiếng Việt, ≤ 3.000 ký tự** (`assert len(prompt) <= notebook.MAX_PROMPT`). Rút gọn danh sách headline (tối đa ~15 tin đã lọc Việt Nam) và danh sách "đã làm" (~25 tiêu đề gần nhất) để vừa giới hạn.
  - Nội dung prompt:
    - 4 ngách và tỉ lệ (~7/5/4/2), hoặc một ngách nếu có `niche`; hoặc từ khoá nếu có `query`
    - luật NEVER, gồm **cấm mọi chủ đề về Việt Nam**
    - yêu cầu **chỉ đề xuất chủ đề có thật, kiểm chứng được**; mỗi mục kèm một câu "sự thật nền" (fact) có thể kiểm chứng
    - trả **đúng một khối JSON** nằm giữa `===BẮT ĐẦU===` và `===KẾT THÚC===`, theo đúng các trường của `CAND_SCHEMA` (`topic, query_en, niche, kind, angle, appeal`) + `fact`
  - Gọi `notebook.ask(0, "topic_scout", prompt, time.time() + nb_wait_minutes*60, check)` rồi `notebook._json_block(...)`.
  - JSON hỏng → hỏi lại **1 lần** với câu "Chỉ trả JSON hợp lệ…". Vẫn hỏng thì raise.
  - Kiểm tra từng ứng viên trong Python:
    - đủ trường
    - `niche` thuộc danh sách cho phép (sai thì bỏ)
    - `appeal` ép về số 0-10
- Các phần còn lại **giữ nguyên**: lọc `is_vietnam`, `_similar`, đo heat bằng last30days, nhân hệ số ngách, `select()`, vòng lặp 3 lần của `suggest()`.
  - Mỗi vòng là một câu hỏi NotebookLM (chậm), nên với engine notebooklm giảm còn tối đa **2 vòng** và yêu cầu 20 ứng viên/lần.
- `suggest()` trả thêm trường `fact` để UI hiện.
- Worker auto (`scout()`) cũng đi qua NotebookLM. Trước khi hỏi, nếu `not notebook.connected()` hoặc `notebook.paused()` thì log "NotebookLM chưa sẵn sàng – bỏ qua lượt tự tìm chủ đề" và return `[]`, không raise làm hỏng vòng worker. Trong worker phải đặt `topic_scout_last` để không spam lại liên tục.
- **Tranh chấp**: `notebook.ask` dùng `_ASK_LOCK` chung với việc viết kịch bản. Khi job đang viết kịch bản, việc tìm chủ đề sẽ chờ, và ngược lại. Chấp nhận được, nhưng:
  - UI phải báo "Đang chờ NotebookLM rảnh (đang viết kịch bản job X)…"
  - Không gọi NotebookLM từ trong luồng giữ `_SCOUT_LOCK` quá lâu mà không có timeout. Dùng `nb_wait_minutes`.
  - `check` truyền vào: hàm no-op hoặc kiểm tra cờ huỷ; không dùng `self._check` của worker trong luồng API.

### 3. `app/api.py`
- `/topics/suggest`: nếu engine là notebooklm mà `not notebook.connected()` → trả 503 `{"ok": false, "msg": "Notebook Bridge chưa kết nối – mở Chrome profile NotebookLM"}` ngay.
- `/status` trả thêm `topic_engine`.

### 4. UI (`app.js`, `index.html`)
- Dưới tiêu đề card hot hiện dòng nhỏ: "Nguồn: 📓 NotebookLM" hoặc "🤖 Qwen".
- Spinner ghi "NotebookLM đang tìm chủ đề… (2–10 phút)".
- Mỗi dòng hiện thêm `fact` (dòng chữ nhỏ "📌 …") để người dùng thấy chủ đề có căn cứ.
- Xử lý mã 503 giống 409: tắt spinner, hiện toast.

## Không làm
- Không sửa extension Chrome nếu kind `"ask"` đã chạy được. Chỉ sửa khi test thật cho thấy cần.
- Không sửa luồng viết kịch bản `_st_script` / `write_script`.
- Không xoá đường Qwen: giữ làm tuỳ chọn trong config.

## Kiểm thử (thật, không mock)
1. `py_compile app/topic_scout.py app/api.py app/notebook.py`.
2. Độ dài prompt: in `len(prompt)` cho 3 trường hợp (chung / niche / query), cả ba đều ≤ 3200.
3. Mở Chrome profile NotebookLM, có bridge. Gọi thật `suggest(n=10)`:
   - in 10 tiêu đề + niche + fact
   - trong bảng `nb_tasks` có task `step='topic_scout'` ở trạng thái done
   - **không** có request nào tới LM Studio trong lượt này (log LLM không có dòng mới)
4. `suggest(n=10, niche="lịch sử – bí ẩn")`: tiêu đề đúng sự thật. Tự rà 10 mục, đánh dấu mục nào sai sự thật.
5. Tắt extension → POST `/topics/suggest` trả 503. Worker `scout()` log "bỏ qua" và không crash.
6. Đang chạy job ở bước script (NotebookLM bận) → bấm Top 10 → UI báo chờ, xong sau khi kịch bản xong. Không gõ đè.
7. Web UI thật: chụp màn hình card hot có dòng "Nguồn: 📓 NotebookLM" và các dòng 📌 fact.
8. Hồi quy: đặt `engine: qwen` thì vẫn chạy như cũ.
9. Mục nào không chạy được → ghi CHƯA KIỂM + lý do.
