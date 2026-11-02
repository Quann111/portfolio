# Plan bổ sung: 4 ngách ưu tiên + sửa màu card "🔥 Chủ đề hot"

## Bối cảnh
- Gợi ý hiện tại lệch hướng: chủ yếu là tài chính, Bitcoin, Trump/thuế…, vì `config.yaml` → `auto_topics.niches` vẫn là 8 ngách cũ (tài chính, sức khỏe, thể thao…).
- Người dùng muốn **chỉ** 4 ngách, theo thứ tự ưu tiên:
  1. Chiến tranh – quân sự (câu chuyện, bản đồ, khí tài, sự kiện)
  2. Địa chính trị – thời sự (chủ đề mới liên tục)
  3. Lịch sử – bí ẩn (thư viện video dài, ít phụ thuộc tin nóng)
  4. Giải trí – người nổi tiếng (bắt xu hướng nhanh)
- Màu card chưa hợp theme: UI đang dùng theme **retro** (nền xám #c0c0c0, title xanh navy), nhưng CSS của card hot (`style.css` cuối file) hard-code màu cho nền tối:
  - chữ tiêu đề `#f1f5f9` (gần trắng)
  - angle `#94a3b8`
  - input `#0006`, chữ trắng
  - badge chữ màu nhạt
  → Trên nền xám thì chữ trắng/nhạt gần như không đọc được (đúng như ảnh chụp).

## Thay đổi

### 1. `config.yaml` → `auto_topics`
```yaml
  niches: ["chiến tranh – quân sự", "địa chính trị – thời sự", "lịch sử – bí ẩn", "giải trí – người nổi tiếng"]
  niche_weights: {"chiến tranh – quân sự": 4, "địa chính trị – thời sự": 3, "lịch sử – bí ẩn": 2, "giải trí – người nổi tiếng": 1}
  max_news: 4          # 2 ngách đầu hay bám thời sự
```
- Sửa `reference_channels` (giữ cái nào còn hợp, vd CaspianReport). Thêm kênh quân sự / lịch sử – bí ẩn nếu biết URL thật. **Không bịa URL**; không chắc thì ghi chú để người dùng tự điền.

### 2. `app/topic_scout.py`
- Prompt trong `candidates()`:
  - Thay câu "Viewers love curiosity, money, history, geopolitics, psychology, health myths…" bằng mô tả 4 ngách và thứ tự ưu tiên, kèm ví dụ góc khai thác:
    - quân sự: trận đánh, khí tài, chiến thuật, bản đồ
    - địa chính trị: vì sao xung đột X, ai được ai mất
    - lịch sử – bí ẩn: sự kiện bí ẩn, đế chế sụp đổ, bí ẩn chưa giải
    - giải trí: câu chuyện phía sau sự nghiệp, thăng trầm của người nổi tiếng
  - Phân bổ 18 ứng viên theo trọng số: khoảng 7 quân sự, 5 địa chính trị, 4 lịch sử – bí ẩn, 2 giải trí.
  - Giữ nguyên các luật NEVER (chính trị nội bộ VN, tôn giáo, thuyết âm mưu…). Với ngách giải trí giữ luật "không bi kịch đời tư, không drama cá nhân, không bịa": chỉ kể câu chuyện sự nghiệp có thật.
  - Thêm luật: nội dung chiến tranh phải trung lập, mang tính giải thích; không cổ vũ bạo lực, không hình ảnh ghê rợn, không bịa số liệu thương vong.
- Chấm điểm: `score = 0.6*appeal/10 + 0.4*heat/top_heat`, rồi **nhân thêm hệ số ngách** `1 + 0.05*(weight-1)` (quân sự ×1.15 … giải trí ×1.0), để ngách ưu tiên lên đầu khi điểm gần bằng nhau.
- `select()`: giới hạn mỗi ngách dựa theo trọng số thay vì `max(2,(n+3)//4)` đều nhau. Với n=10: quân sự ≤4, địa chính trị ≤3, lịch sử ≤3, giải trí ≤2. Thiếu thì lấy thêm từ các ngách còn chỗ.
- `suggest()` và `candidates()` nhận thêm `niche: str | None`:
  - có `niche` → mọi ứng viên thuộc đúng ngách đó (schema `enum` chỉ một giá trị, prompt nói rõ)
  - không có `niche` → trộn 4 ngách theo trọng số

### 3. `app/api.py`
- `POST /topics/suggest` nhận thêm `niche` và truyền vào `suggest`. Kiểm tra `niche` có nằm trong `acfg(cfg)["niches"]`, sai thì trả 400.
- `GET /topics/niches` → trả danh sách ngách (theo đúng thứ tự trong config) để UI dựng nút.

### 4. UI: `app/web/index.html` + `app.js`
- Hàng chip chọn ngách ngay dưới hàng nút: `[Tất cả] [⚔️ Quân sự] [🌍 Địa chính trị] [🏛️ Lịch sử – bí ẩn] [🎬 Giải trí]`.
  - Dùng class `.chip` có sẵn, chip đang chọn có `.chip.active`. Chip dựng từ `/topics/niches`.
  - Lưu lựa chọn vào `localStorage` (bọc try/catch).
- "Top 10" và "Tìm" gửi kèm `niche` đang chọn (Tất cả = không gửi).
- Badge ngách trên mỗi dòng hiện icon tương ứng.

### 5. Màu sắc: `app/web/style.css` (khối `/* Card Chủ đề hot */`)
- Bỏ màu hard-code, dùng biến theme: tiêu đề `color: var(--txt)`, angle `color: var(--mut)`, viền `var(--line)`; nền item `var(--glass)`.
- Input `#hotQuery`: dùng cùng style với `<textarea id="topics">` sẵn có (tìm rule của nó trong style.css và áp cho input hot). Không tự đặt `#0006` / `#fff`.
- Thêm override cho theme retro, theo kiểu các rule `body[data-theme=retro]` ở dòng ~247-280:
  - `.hot-item`: nền `#fff`, viền lõm `border-color:#808080 #fff #fff #808080`, chữ `#000`; hover nền `#e8e8e8`
  - `.hot-item-title`: `#000`, đậm
  - `.hot-item-angle`: `#404040`
  - `#hotQuery`: nền `#fff`, chữ `#000`, viền lõm như textarea retro
  - badge: nền đặc, chữ đậm, đủ tương phản trên nền trắng:
    - news: nền `#800000`, chữ trắng
    - evergreen: nền `#008000`, chữ trắng
    - niche: nền `#000080`, chữ trắng
    - score: nền `#ffff00`, chữ `#000`
    - heat: nền `#ff8000`, chữ `#000`
    - bo góc 0
  - `.hot-status-box`: nền `#ffffe1`, chữ `#000`, viền `#000`
  - thanh cuộn `.hot-list`: giữ mặc định
- Kiểm tra cả theme mặc định ("hacker" trong `index.html:9`) và mọi theme khác có trong style.css (grep `data-theme=`): chữ phải đọc được, tương phản ≥ 4.5:1.
- Thêm newline cuối file (diff đang báo "No newline at end of file").

### 6. Cấm mọi chủ đề về Việt Nam (bắt buộc)
Kênh **không làm bất cứ chủ đề nào về Việt Nam**: chính trị VN, lãnh đạo/đảng/nhà nước VN, lịch sử và chiến tranh VN (chống Pháp, chống Mỹ, biên giới 1979, Biển Đông, Điện Biên Phủ, Mậu Thân…), nhân vật lịch sử VN, người nổi tiếng VN, sự kiện xã hội VN. Áp dụng cho cả 4 ngách.
Chủ đề vẫn viết bằng tiếng Việt cho người xem Việt, nhưng nội dung phải là thế giới.

Chặn ở 3 lớp (prompt LLM không đủ tin cậy):
1. **Prompt** (`candidates()`): thêm luật NEVER rõ ràng: "No topic about Vietnam in any form: Vietnamese politics, leaders, the Communist Party, Vietnamese history, ANY war involving Vietnam (Indochina wars, Vietnam War, 1979 border war, South China Sea/Biển Đông disputes involving Vietnam), Vietnamese historical figures, Vietnamese celebrities, Vietnamese society. All subjects must be outside Vietnam."
2. **Lọc tin nóng**: `headlines()` lấy RSS `hl=vi&gl=VN`, nên nhiều tin là tin trong nước. Trước khi đưa vào prompt, loại các headline khớp blacklist ở lớp 3. Cân nhắc đổi feed WORLD sang bản quốc tế (`hl=en-US&gl=US&ceid=US:en`, section WORLD) để có thêm tin quân sự/địa chính trị thế giới.
3. **Bộ lọc cứng bằng code**: thêm hàm `_is_vietnam(c: dict) -> bool` trong `topic_scout.py`. Hàm kiểm tra `topic`, `angle`, `query_en` (không phân biệt hoa thường, so cả bản đã bỏ dấu) với danh sách từ khoá đặt trong config `auto_topics.banned_keywords`, mặc định gồm:
   `việt nam, viet nam, vietnam, vietnamese, hà nội, hanoi, sài gòn, saigon, hồ chí minh, ho chi minh, điện biên, dien bien, mậu thân, tet offensive, biển đông, hoàng sa, trường sa, paracel, spratly, đảng cộng sản việt, quốc hội việt, việt cộng, viet cong, vnch, việt nam cộng hòa, chiến tranh đông dương, indochina war, khe sanh, 1979 biên giới, sino-vietnamese, mỹ lai, my lai, võ nguyên giáp, giap`
   Lọc ngay sau khi LLM trả ứng viên, trước khi đo heat, ở cả `scout()` và `suggest()`. Log số mục bị loại "loại X vì liên quan Việt Nam". Việc lọc này cũng làm thiếu ứng viên → đã có vòng lặp 3 lần để bù đủ n.
   Lớp này cũng áp dụng cho `query` người dùng gõ: nếu chính query dính blacklist thì API trả 400 "Kênh không làm chủ đề về Việt Nam".

## Kiểm thử
1. `py_compile app/topic_scout.py app/api.py`.
2. Gọi thật `suggest(n=10)`: ≥7/10 mục thuộc 4 ngách mới (thực tế là cả 10), không còn chủ đề tài chính/sức khỏe; ngách quân sự nhiều nhất.
3. `suggest(n=10, niche="lịch sử – bí ẩn")`: cả 10 mục đều là lịch sử – bí ẩn.
4. UI thật, theme retro **và** theme hacker:
   - bấm từng chip → Top 10
   - chụp màn hình, chữ tiêu đề/badge/input phải đọc rõ
   - so sánh với ảnh chụp cũ (chữ trắng trên nền xám)
5. `scout()` của worker (auto) cũng dùng 4 ngách mới.
6. Không có chủ đề nào về Việt Nam:
   - chạy `suggest(n=10)` 2 lần và `suggest(n=10, niche="chiến tranh – quân sự")` → 0 mục dính blacklist; in log số mục bị loại
   - unit test `_is_vietnam`: True cho "Trận Điện Biên Phủ", "Vietnam War", "Biển Đông", "Ho Chi Minh trail"; False cho "Trận Stalingrad", "Chiến tranh Triều Tiên"
   - POST `/topics/suggest {query:"chiến tranh Việt Nam"}` → 400
7. Mục nào không chạy được thì ghi CHƯA KIỂM.
