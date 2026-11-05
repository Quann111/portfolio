# VENDORED.md — nguồn gốc thư mục `opuscar/`

## Nguồn

- Repo gốc: https://github.com/lemomo-ai/lemo-opuscar (nhánh mặc định, clone `--depth 1`)
- Commit: `bebbbe1aa92a3cb4bb6f83500e61b23a17d1aa00`
- Ngày vendor: 2026-11-04
- Giấy phép: MIT (giữ nguyên tại `LICENSE`)
- Thư mục này là bản copy một phần của repo gốc; toàn bộ render chạy offline qua Playwright + ffmpeg.

## Đã chép vào đây

- `core/` — bộ tool render dùng chung (`render/`, `audio/`, `fonts/`, `post/`, `three/`, `assets/` trừ phần đã bỏ, `lib.js`, `README.md`)
- `LICENSE`, `package.json`, `package-lock.json`, `TECHNIQUE.md`, `DIRECTOR.md`

## Đã bỏ (không vendor)

- `core/tts/` (toàn bộ: `tts.py`, `tts_zh.py`, `asr_check.py`) — TTS/ASR Python, cần tải model riêng
- `core/assets/polyhaven/` — HDRIs/models CC0 (dung lượng lớn, không cần cho render 2D)
- Toàn bộ phần còn lại của repo gốc không liên quan render: `.claude-plugin/`, `.github/`, `docs/`, `plugin/`, `styleboard/`, `styles/`, `tools/`, `AGENTS.md`, `CLAUDE.md`, `MAINTAINING.md`, `MY-STYLES.md`, `README.md`, `README.zh-CN.md`, `requirements*.txt`, `.gitignore`
- `node_modules/` — cài lại bằng `npm install` (chỉ 2 dependency gốc: `playwright-core`, `three`)

## Sửa lỗi Windows trong `core/`

**Không sửa file nào trong `core/`.** `still.mjs` và `video.mjs` chạy nguyên bản trên Windows (PowerShell, đường dẫn có dấu `\`).

Ghi chú môi trường (không phải sửa code):

- Trình duyệt headless: cài theo cách của package là `node node_modules/playwright-core/cli.js install chromium-headless-shell`, nhưng trên máy này download từ `cdn.playwright.dev` lẫn mirror `cdn.npmmirror.com` đều timeout. Đã dùng biến env `PLAYWRIGHT_CHROME` (do chính `core/render/browser.mjs` hỗ trợ sẵn, dòng 5) trỏ tới Chrome hệ thống: `C:\Program Files\Google\Chrome\Application\chrome.exe`.
- Lưu ý: nếu trỏ `PLAYWRIGHT_CHROME` vào một `chrome-headless-shell.exe` nằm **bên trong** thư mục cache của Playwright (`%LOCALAPPDATA%\ms-playwright\...`), `chromium.launch` tự ghi đè path và báo "executable doesn't exist" (xảy ra ở `video.mjs`, trong khi `still.mjs` lại qua). Dùng path bên ngoài thư mục cache (như Chrome hệ thống) là chạy ổn định.

## Lệnh smoke test (đã chạy, kết quả đạt)

Chạy từ `vendor/opuscar/`, máy cần Node ≥ 20, ffmpeg/ffprobe trong PATH:

```powershell
npm install
# PLAYWRIGHT_CHROME='<đường dẫn chrome.exe hoặc chrome-headless-shell.exe ngoài cache Playwright>'
node core/render/still.mjs _smoke 0 1
node core/render/video.mjs _smoke --fps 24 --workers 1 --out _smoke/out/video.mp4
ffprobe -v error -show_entries format=duration -show_entries stream=codec_name,width,height,r_frame_rate _smoke/out/video.mp4
```

Kết quả: 2 ảnh still (`_smoke/stills/t_0.jpg`, `t_1.jpg`, đúng nội dung bất định); video H.264 1920x1080, 24 fps, duration 2.000000 giây, 48 frame (`_smoke/out/video.mp4`).

## Trang smoke

`_smoke/index.html` — canvas 1920x1080, `window.DUR=2`, `window.render(t)` vẽ nền đen + hình tròn chạy ngang theo `t` + chữ `SMOKE t=…`, `window.READY=true`. Bất định tuyệt đối: không dùng `Date.now` hay `Math.random`.
