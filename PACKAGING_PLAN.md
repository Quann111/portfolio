# Plan: Đóng gói AutoVideo – mang sang máy khác, bấm Setup là chạy

## Mục tiêu
Một bộ cài `AutoVideo_Setup.exe` duy nhất. Trên máy mới: chạy Setup → chờ cài → mở app → bấm **Bắt đầu**.
Phần không thể tự động (vì là tài khoản/bảo mật của Google & Chrome) được Setup dẫn từng bước, mỗi bước 1 cú nhấp.

## Những gì cần có trên máy mới (kiểm kê từ máy hiện tại)
| Thành phần | Kích thước | Nguồn khi cài | Ghi chú giấy phép |
|---|---|---|---|
| Code app (`app/`, `main.py`, config, channel.yaml, pronunciation.yaml) | < 5 MB | trong bộ cài | của bạn |
| Python 3.11 + `.venv` (torch cu128, PySide6, OmniVoice, vieneu, zerotts, ddgs, trafilatura…) | 8.5 GB | `uv` tải khi cài (không chép .venv – đường dẫn cứng) | các gói mở |
| Repo whiteboard (đã vá `_lay_ink`, `_wash_contour`) + venv Python 3.9 riêng | 0.3 GB + venv | trong bộ cài (bản đã vá) + `uv` | MIT |
| FFmpeg | ~150 MB | trong bộ cài (bản static) | LGPL/GPL – kèm giấy phép |
| `uv`, `yt-dlp` | ~50 MB | trong bộ cài | MIT / Unlicense |
| LM Studio (app + `lms` CLI) | ~0.6 GB | **tải bản cài chính thức** khi Setup (không nhúng lại – điều khoản LM Studio) | điều khoản LM Studio |
| Model LLM: `qwen/qwen3-vl-8b` (6.2 GB), `qwen3-30b-a3b-instruct-2507` IQ4_XS (16.4 GB) | 22.6 GB | `lms get` khi cài, hoặc gói offline | Apache-2.0 |
| Model giọng/ASR: ZeroTTS, VieNeu v3 Turbo, OmniVoice, whisper-large-v3-turbo | ~6 GB | Hugging Face khi cài, hoặc gói offline | MIT / Apache-2.0 / (kiểm OmniVoice) |
| Skills: `skills/nano-banana-pro-prompts`, `skills/last30days` | ~70 MB | trong bộ cài | MIT |
| Extension Chrome: `chrome_ext` (Flow Bridge), `chrome_ext_notebook` (Notebook Bridge) | < 1 MB | trong bộ cài | của bạn |
| Bionic Skill (`bionic_skill/SKILL.md`) | – | chép vào Bionic nếu máy có Bionic | – |
| Dữ liệu: giọng khoá `data/voice_ref.wav`, `voice.pt`, `pronunciation.yaml` | < 1 MB | trong bộ cài | – |
| **Không mang**: `data/session.db`, `output/`, `work/`, `exchange/`, cache web, HF cache thừa (53 GB nhiều model không dùng) | | | |

Tổng tải khi cài online ≈ **38 GB** (phần lớn là model). Bộ cài exe ≈ 0.5 GB.

## Hai kiểu bộ cài
1. **Online (mặc định)** – `AutoVideo_Setup.exe` ~0.5 GB, tự tải phần lớn từ nguồn chính thức. Cần mạng tốt (~38 GB).
2. **Offline pack** (ổ cứng ngoài/USB) – thư mục `AutoVideo_Offline/` thêm `models/` (GGUF + HF snapshot) và `wheels/`
   (pip wheel đã tải sẵn) → Setup thấy thư mục này thì cài không cần mạng (trừ LM Studio nếu không kèm bản cài).

## Setup làm gì (theo thứ tự, có thanh tiến độ, cài lại được – bước đã xong thì bỏ qua)
1. **Kiểm tra máy**: Windows 10/11 64-bit, GPU NVIDIA + driver ≥ CUDA 12.8, VRAM, RAM, ổ trống (cần ≥ 60 GB).
   - Chọn **hồ sơ phần cứng** tự động: ≥ 20 GB VRAM → như máy này (30B text + 8B VL); 12–20 GB → chỉ 8B cho mọi việc;
     < 12 GB / không GPU → 8B + giọng ZeroTTS (CPU) + cảnh báo chậm. Ghi vào `config.yaml`.
2. Giải nén app vào `%LOCALAPPDATA%\AutoVideo` (hoặc thư mục người dùng chọn).
3. Cài `uv` → tạo `.venv` (Python 3.11) + gói từ `requirements.lock` (khoá phiên bản đúng như máy này) → venv whiteboard.
4. FFmpeg, yt-dlp vào `tools/` + thêm vào PATH của app (không đụng PATH hệ thống).
5. LM Studio: chưa có → tải & chạy bản cài chính thức (im lặng nếu được) → `lms bootstrap` → `lms server start`.
6. Tải model theo hồ sơ phần cứng (`lms get …`, Hugging Face `snapshot_download`), có tiếp tục khi đứt mạng (đã gặp: tải 16 GB bị ngắt 2 lần).
7. Chạy **preflight** + test nhanh (`tests/test_units.py`) → báo kết quả.
8. Tạo shortcut Desktop/Start Menu "AutoVideo", tuỳ chọn chạy cùng Windows.
9. **Trợ lý lần đầu** (màn hình trong app, 3 bước có nút): 
   - Mở `chrome://extensions` + thư mục extension → bạn bật Developer mode, kéo 2 thư mục vào (Chrome **không cho**
     phần mềm tự cài extension ngoài Chrome Web Store trên máy cá nhân).
   - Mở NotebookLM và Google Flow → bạn đăng nhập tài khoản Google, mở 1 project Flow.
   - App tự kiểm tra 2 extension đã kết nối (đèn xanh) → xong, bấm Bắt đầu.
10. Bionic: nếu có, chép `bionic_skill` vào thư mục skill của Bionic.

## Công cụ đóng gói
- **Inno Setup** tạo `AutoVideo_Setup.exe` (chữ ký số tuỳ chọn để Windows SmartScreen không chặn).
- Script `build/make_release.ps1`: copy file cần mang (bỏ dữ liệu cá nhân/cache), xuất `requirements.lock`
  (`uv pip freeze`), chép repo whiteboard đã vá, đóng gói extension (zip), tạo `version.json`, gọi Inno Setup.
- Script `build/make_offline_pack.ps1`: chép GGUF từ `~/.lmstudio/models`, snapshot HF của 4 model giọng/ASR, tải wheels.
- Cài đặt lần đầu được log ra `setup.log`; Setup chạy lại = sửa chữa (repair).

## Thay đổi code cần làm trước khi đóng gói
- Bỏ mọi đường dẫn cứng `D:\AI\truyen\…`, `C:/Users/ADMIN/…` (vd `autoflow.inbox_dir`, repo whiteboard `../`) → tương đối
  với thư mục cài hoặc `%USERPROFILE%`.
- `config.yaml` tách: `config.default.yaml` (đi theo bộ cài) + `config.local.yaml` (máy đó, Setup sinh ra theo phần cứng).
- `main.py`: thêm `--first-run` (trợ lý lần đầu) và trang "Tình trạng" (LM Studio, model, 2 extension, GPU).
- Extension: `API` cố định `127.0.0.1:8765` – giữ nguyên; thêm icon + đóng gói zip.
- Cập nhật: app kiểm tra `version.json` trên GitHub Releases (tuỳ chọn) – cập nhật code không phải tải lại model.

## Rủi ro / giới hạn cần biết
- Đăng nhập Google, cài extension, captcha của Flow: **luôn cần bạn thao tác** – không thể/không nên tự động hoá.
- Máy yếu (VRAM < 12 GB): chạy được nhưng chậm, ảnh vẫn do Flow tạo nên không ảnh hưởng.
- Dung lượng tải lớn → nên dùng Offline pack khi cài nhiều máy.
- Kiểm tra giấy phép OmniVoice trước khi đưa model vào offline pack để phát cho người khác.

## Kiểm chứng
- Máy ảo/ máy thứ 2 sạch (Windows 11 + GPU): chạy Setup online → mở app → trợ lý 3 bước → bấm Bắt đầu → ra 1 video 3 phút.
- Chạy Setup lần 2 (repair) không tải lại thứ đã có. Offline pack cài được khi rút mạng (trừ đăng nhập Google).
