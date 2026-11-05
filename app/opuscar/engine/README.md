# opuscar engine — dựng phim từ content.json

Engine render JS thuần (canvas 2D, module ES). LLM chỉ điền `content.json`;
engine tự dựng toàn bộ hình ảnh theo look `dark-keynote`. NGOÀI phạm vi này
không sản sinh code (TECHNIQUE.md/DIRECTOR.md của vendor mô tả ngữ pháp).

## Cấu trúc
- `index.html` + `index.js` — bootstrap: nạp `content.json` (mặc định cùng thư
  mục với trang, hoặc `?content=…`), fonts OFL trong `fonts/`, ảnh của cảnh;
  đặt xong mới tới `window.READY`. Cửa sổ công khai: `window.DUR`,
  `window.render(t)`, `window.EV`, `window.TEXTS(t)`, `window.READY`.
- `core.js` — timeline/chuyển cảnh/camera, đo chữ `fitLines` (co + ngắt dòng
  tiếng Việt), phụ đề dưới khung (chip tối đa 2 dòng, không vào TEXTS theo hợp
  đồng readcheck), TEXTS/EV. Hỗ trợ khung DỌC (Shorts 1080x1920): mọi bố cục dọc
  suy từ tỉ lệ khung thực (`isPortrait = H > W`, content.json chỉ cần trường
  tuỳ chọn `"format": "short"` để tag), hằng số vùng an toàn Shorts
  (`SHORT_SAFE`: 12% trên / 20% dưới / 10% phải — phụ đề đặt ngay trên vạch 20%)
  định nghĩa trong `looks/dark-keynote.js` và re-export qua core dùng chung.
  Khi kiểm tra still có thể thêm `?safe=1` để phác khung vùng an toàn lên ảnh
  DEBUG (không bao giờ dùng khi render video thật).
- `looks/dark-keynote.js` — palette, timing, `minFont(W,H,cls)` (sàn cỡ chữ phụ:
  ≥0.026·S và nhãn trục ≥0.030·S với S=min(W,H)), `stage/scrim/panel/fallback`.
- `shots/*` — 8 shot: `hero_image, kinetic_text, stat, chart, timeline,
  quote_card, chapter_card, split_compare`; `_common.js` dựng TEXTS từ plan.

## Thư mục phim & cách vendor tìm file (serve.mjs / page.mjs)

Mọi tool kiểm thử (`core/render/still.mjs`, `readcheck.mjs`, `events.mjs`,
`video.mjs`) chạy theo cấu trúc "demo = thư mục chứa `index.html`":

1. `page.mjs → pageURL(root, port, dir)`: nếu `dir` nằm NGOÀI repo vendor thì
   `serve.mjs` mount chính dir đó dưới `/@film/` (biến `filmDir`). Mọi URL
   `/@film/…` đọc trực tiếp từ film_dir; URL khác (`/core/lib.js`) đọc từ repo
   vendor — vì vậy không cần (và không được) copy vendor sang chỗ khác.
2. Vì vậy một "thư mục phim" (ví dụ `projects/<job>/opuscar/`, hoặc
   `engine/_sample/`) chứa:
   - `index.html` ở gốc, nạp `<script type="module" src="engine/index.js">`;
   - `content.json` + ảnh (`img/…`) — đường dẫn tương đối tới content.json;
   - `engine/` — bản chép cục bộ engine runtime.
3. Dùng `app/opuscar/film.py → prepare_film(film_dir)` để chép engine vào
   `film_dir/engine/` (copy thật, KHÔNG symlink/junction — junction trỏ tới
   thư mục tổ tiên tạo vòng lặp vô hạn cho mọi công cụ quét thư mục; từng gây
   hỏng `_sample/engine` và đã bị cấm). Chạy lại `prepare_film` mỗi lần engine
   thay đổi. Font nạp theo `import.meta.url` (`engine/fonts/…`) nên chép đủ
   `fonts/` mới đúng.

Đang serve demo kiểu này: `node core/render/still.mjs "…/engine/_sample" …`.
