# Plan: Bảng "🔥 Chủ đề hot" trong UI web AutoVideo

## Mục tiêu
Trong web UI (`app/web/`) có 3 cách lấy chủ đề:
1. **Top 10 chủ đề hot**: bấm một nút, AI trả về 10 chủ đề hot kèm điểm. Người dùng tick chọn rồi thêm vào hàng đợi, hoặc thêm tất cả.
2. **Tìm theo từ khoá**: người dùng gõ một từ khoá (vd "bitcoin", "chiến tranh lạnh"). AI đề xuất 10 chủ đề quanh từ khoá đó, chấm điểm hot, rồi người dùng chọn như mục 1.
3. **Auto chọn chủ đề**: có công tắc bật/tắt ngay trên UI. Khi bật, worker tự tìm và tự thêm chủ đề lúc hàng đợi gần hết (đã có sẵn, nay chỉ thêm công tắc). Chạy tự động, không chờ duyệt.

## Hiện trạng (đã có, phải tái sử dụng, không viết lại)
- `app/topic_scout.py:177 scout(cfg, llm, log, n)`: lấy tin nóng (`headlines`), video tham khảo (`reference_videos`), gọi LLM theo `CAND_SCHEMA`, đo `youtube_heat` (last30days), tính `score`, chọn N chủ đề (cân bằng news/evergreen, đa dạng ngách, chống trùng bằng `_similar`) rồi **gọi `db.add_topic` luôn**.
- `topic_scout.acfg` đọc `config.yaml` → `auto_topics` (enabled, batch, min_queue, niches…). `topic_scout.due()` được worker gọi ở `worker.py:209`.
- API `app/api.py:456` `/topics/scout`: chạy `scout` trong thread nền, đẩy log qua `events.push`. API `/topics` (`:475`) thêm chủ đề kèm style/voice/target_minutes.
- UI: card "✨ Chủ đề mới" trong `web/index.html:~60`. Nút `#btnScout` và `#btnAdd` nằm ở `web/app.js:~572-595`.

## Thay đổi

### 1. `app/topic_scout.py`: tách "đề xuất" khỏi "thêm vào hàng đợi"
- Tách thân `scout()` thành 3 hàm, **giữ nguyên logic và prompt hiện có**:
  - `candidates(cfg, llm, log, query: str | None = None) -> list[dict]`: phần từ đầu đến khi sort theo `score`. Trả về danh sách đã chấm điểm, **không** gọi `db.add_topic`.
  - `select(cands, n, done) -> list[dict]`: copy y nguyên vòng lọc `picked` (max_news, đa dạng ngách, trùng query_en, `_similar`).
  - `scout(cfg, llm, log, n=None)` = `candidates` → `select` → `db.add_topic` → ghi json → `kv_set("topic_scout_last")`. Hành vi và chữ ký giữ nguyên, để `worker.py:213` và `/topics/scout` không bị ảnh hưởng.
- Khi `query` có giá trị:
  - Thêm vào prompt một đoạn: mọi ứng viên phải xoay quanh chủ đề người dùng nhập `{query}`, mỗi ứng viên một góc nhìn khác nhau.
  - Bỏ giới hạn `max_news` và luật đa dạng ngách (người dùng muốn đào sâu một đề tài).
  - Lọc `headlines` giữ những tin chứa từ khoá; nếu không còn tin nào thì vẫn dùng toàn bộ.
  - Giữ nguyên các luật cấm (chính trị VN, tôn giáo…).
- Thêm `suggest(cfg, llm, log, n=10, query=None) -> list[dict]`:
  - Gọi `candidates` rồi `select`, trả N mục dạng `{topic, kind, niche, angle, appeal, heat, score}`.
  - Lưu kết quả vào `db.kv_set("topic_suggestions", {"ts": time.time(), "query": query, "items": [...]})`. Ghi json vào `OUT` như `scout`.
  - Không thêm gì vào hàng đợi.
- Thêm khoá chống chạy trùng: `_SCOUT_LOCK = threading.Lock()`, dùng `acquire(blocking=False)` trong `scout` và `suggest`. Nếu đang bận thì raise `RuntimeError("Đang tìm chủ đề, đợi xong lượt trước")`. Lý do: worker auto và UI có thể cùng gọi LLM (LM Studio).
- `acfg()`: sau khi đọc config, nếu `db.kv_get("auto_topics_enabled")` khác `None` thì lấy giá trị đó ghi đè `a["enabled"]` (công tắc trên UI ưu tiên hơn file YAML).
- Lưu ý: comment trong `due()` đang lỗi mã hoá (`C?n t?m th?m`). Không sửa logic ở đó; muốn sửa comment thì phải giữ file ở UTF-8 (không thêm BOM).

### 2. `app/api.py`: các route mới (theo mẫu `/topics/scout`)
- `POST /topics/suggest` `{count?: 10, query?: str}`:
  - Thread nền gọi `topic_scout.suggest` với `LLM(load(), print)` và `role="text"`.
  - Lúc bắt đầu: `events.push("log", …)`. Khi xong: `events.push("suggestions", {"query": q, "items": [...]})`. Khi lỗi: push log ❌.
  - Trả ngay `{"ok": True}`.
- `GET /topics/suggestions` → trả cache trong kv (`{ts, query, items}` hoặc rỗng). Đánh dấu thêm `added: true` cho mục đã có trong `db.jobs()` (so khớp topic).
- `POST /topics/auto` `{enabled: bool}` → `db.kv_set("auto_topics_enabled", bool)`. Gọi `changed()`.
- `/status` (hoặc GET riêng) trả thêm `auto_topics: topic_scout.acfg(load())["enabled"]` để UI hiện đúng trạng thái công tắc.
- Thêm đúng vào `do_GET` / `do_POST` theo cách các route khác đang làm.

### 3. `app/web/index.html` + `app.js` + `style.css`: card "🔥 Chủ đề hot"
Đặt ngay dưới card "✨ Chủ đề mới" (`col-left`), dùng các class sẵn có (`card glass`, `row`, `btn ghost`).
- Hàng điều khiển:
  - Nút **"🔥 Top 10 chủ đề hot"** → gọi `/topics/suggest {count:10}`.
  - Ô `input#hotQuery` (placeholder "Tìm chủ đề theo từ khoá…") và nút **"🔍 Tìm"** → gọi `{count:10, query}`. Bấm Enter cũng tìm.
  - Công tắc **"🤖 Auto chọn chủ đề"** (checkbox) → gọi `/topics/auto`.
- Trạng thái đang tìm: hiện spinner "AI đang tìm… (có thể 1–3 phút)" và disable các nút. Bước đo hot qua last30days chậm, nên **không** dùng `setTimeout` đoán thời gian: lắng nghe event SSE `suggestions` qua listener SSE hiện có, rồi render.
- Danh sách kết quả, mỗi dòng gồm:
  - checkbox, tiêu đề topic
  - badge `kind` (📰 tin nóng / 🌲 lâu dài), `niche`, điểm `score×100`, 🔥 heat
  - `angle` dạng chữ nhỏ
  - mục đã thêm thì làm mờ và ghi "✓ đã thêm"
- Hàng cuối:
  - "☑ Chọn tất cả"
  - **"＋ Thêm đã chọn"** và **"＋ Thêm tất cả"**: gọi API `/topics` có sẵn, **dùng lại** các select `#addStyle`, `#addVoice`, `#addDuration` của card trên (tách logic build body trong `#btnAdd` thành một hàm `addTopics(list)` để hai nơi cùng dùng; xử lý `custom` duration như cũ).
- Khi mở trang thì gọi `GET /topics/suggestions` để hiện lại kết quả lần trước (kèm thời gian "x phút trước").
- Nút `#btnScout` cũ giữ nguyên.
- Bố cục phải chạy được ở màn hẹp. Danh sách dài thì cho cuộn (`max-height` + `overflow:auto`).

### 4. `app/gui.py` (Qt)
Không bắt buộc. Nếu Qt chỉ là khung bọc web UI thì không cần sửa gì.

## Không làm
- Không đổi prompt và cách chấm điểm hiện có, ngoài phần thêm cho `query`.
- Không đổi luồng auto của worker, ngoài việc `acfg` đọc công tắc trong kv.
- Không thêm thư viện mới.

## Kiểm thử
1. `.venv\Scripts\python.exe -m py_compile app/topic_scout.py app/api.py`
2. Gọi `topic_scout.suggest(cfg, llm, print, n=10)` trong Python:
   - trả 10 mục, có đủ các trường
   - hàng đợi (`db.jobs()`) **không** tăng
   - kv `topic_suggestions` đã có dữ liệu
3. `suggest(..., query="bitcoin")` → mọi mục đều liên quan bitcoin.
4. Gọi `scout()` vẫn thêm đúng N chủ đề như trước (hồi quy).
5. Gọi đồng thời 2 lần `suggest` → lần 2 báo "Đang tìm chủ đề…", không crash.
6. Trên UI (mở web, có thể thu hẹp cửa sổ):
   - Top 10 → danh sách hiện ra sau khi event về
   - tick 3 mục → "Thêm đã chọn" → 3 job mới có đúng style/voice/độ dài
   - các mục đó hiện "✓ đã thêm"
   - tìm theo từ khoá hoạt động
   - tắt "Auto" → `acfg()["enabled"]` là False và worker không tự tìm. Bật lại → tự tìm khi hàng đợi < `min_queue`.
   - F5 trang → kết quả cũ vẫn còn.
7. Báo cáo kèm output thật. Bước nào chưa kiểm thử được thì nói rõ.
