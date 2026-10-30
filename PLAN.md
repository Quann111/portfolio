# AutoVideo – Plan & Kiến trúc tổng hợp (Cập nhật 2026-10-30)

## 1. Mục tiêu & Định hướng hệ thống
Một hệ sinh thái desktop hoàn chỉnh (`D:\AI\truyen\autovideo`) tự động hóa toàn bộ quy trình sản xuất video Whiteboard Animation chất lượng cao từ ý tưởng đến video hoàn chỉnh, vận hành liên tục 24/7 với chi phí tài nguyên tối ưu và ổn định cao nhất trên máy trạm RTX 4090.

---

## 2. Công nghệ & Phân vai hệ thống (Workflow mới nhất)

| Phân hệ / Vai trò | Công nghệ / Công cụ phụ trách | Cơ chế vận hành & Đặc điểm nổi bật |
|---|---|---|
| **Lập kịch bản & Đạo diễn phân cảnh** | Dàn ý + lời đọc: **NotebookLM Bridge**. Chia cảnh + prompt ảnh: **Gemini Web Bridge** (ưu tiên), **LM Studio Qwen3-VL 4B** (fallback) | Tự động nghiên cứu tài liệu qua NotebookLM + Web Search, chia cảnh bám sát thời lượng, nhận diện thực thể và mô tả bố cục giàu chi tiết. |
| **Đạo diễn hình ảnh (Prompt Visual)** | **Director Engine (app/director.py)** | **Quy tắc mới**: Vẽ nhân vật có thật với đặc trưng nhận diện chân thực, hiển thị quốc kỳ/biểu tượng chính xác, vẽ số liệu/mốc thời gian cụ thể, bố cục 3-5 lớp chi tiết. |
| **Tạo hình ảnh minh hoạ** | **Google Flow Extension Bridge** (8 Profiles Chrome song song) | Tự động cân bằng tải và luân chuyển prompt tạo ảnh qua 8 profile Flow; tự động phát hiện lỗi và phục hồi profile (đóng tab/mở lại tab sạch). |
| **Giọng đọc & Phát âm (TTS)** | **ZeroTTS (CPU ONNX, 48 kHz)** | Chạy mặc định với giọng **Bảo Trang** (`baotrang`), tốc độ cực nhanh (~50x realtime), hoàn toàn không chiếm VRAM GPU; fallback sang VieNeu-TTS khi cần. |
| **Đánh giá âm thanh & Khớp chữ (ASR)** | **OpenAI Whisper-large-v3-turbo** | Tự động nghe lại Audio QC, so khớp từ ngữ, sinh timestamp phụ đề theo từ chính xác tới từng mili-giây. |
| **Dựng hoạt họa Whiteboard** | **Nikola / SRT-Whiteboard Animation** | Render vẽ tay nét mực thực tế, hỗ trợ draw-over, chuyển cảnh mượt mà và camera zoom/pan. |
| **Hậu kỳ, Ghép nhạc & Phụ đề** | **FFmpeg + Soundfile + Librosa** | Tự động lồng phụ đề nhảy chữ theo nhịp giọng, ducking nhạc nền theo cảm xúc đoạn, chuẩn hóa âm thanh theo tiêu chuẩn YouTube (-16 LUFS). |
| **Điều khiển & Giám sát** | **PySide6 Desktop GUI + WebBridge Local API** | Giao diện quản lý hàng đợi, theo dõi trực tiếp trạng thái 8 profiles, tự động săn tìm chủ đề khi hàng đợi cạn, khôi phục tác vụ thông minh. |

---

## 3. Quy trình Pipeline chi tiết (Workflow 2026-10-30)

```
[CHỦ ĐỀ MỚI / AUTO SCOUT TOPICS]
       ↓
[RESEARCH & NOTEBOOKLM BRIDGE] (Tổng hợp fact sheet, dàn ý câu chuyện)
       ↓
[ĐẠO DIỄN PHÂN CẢNH (DIRECTOR)]
   • Bám sát thời lượng chính xác
   • Nhận diện nhân vật thật (chân dung chuẩn)
   • Biểu tượng, quốc kỳ, biểu đồ số liệu thực tế
   • Bố cục tiền cảnh - trung cảnh - hậu cảnh (3-5 chi tiết)
       ↓
[TẠO GIỌNG ĐỌC (ZEROTTS - BẢO TRANG)]
   • Chuẩn hóa số, ngày, tiền, đơn vị đo
   • Sinh audio 48 kHz trên CPU siêu tốc
   • Whisper QC kiểm tra khớp lời & lấy timestamp từng từ
       ↓
[TẠO ẢNH PHÂN CẢNH (GOOGLE FLOW - 8 PROFILES)]
   • Đẩy prompt vào hàng đợi phân tán
   • Tự động phục hồi tab khi gặp profile lỗi / nghẽn
   • Tự động tải ảnh chất lượng cao về thư mục cảnh
       ↓
[RENDER HOẠT HỌA WHITEBOARD (NIKOLA / WHITEBOARD)]
   • Vẽ từng nét theo thứ tự logic của cảnh
   • Tự động zoom, pan, hiệu ứng máy quay
       ↓
[HẬU KỲ & ĐÓNG GÓI VIDEO HOÀN CHỈNH]
   • Ghép audio giọng đọc + nhạc nền tự giảm âm lượng khi có lời (ducking)
   • Thêm phụ đề nổi bật từ khóa
   • Final QC chấm điểm & xuất bản video hoàn tất
```

---

## 4. Các cải tiến cốt lõi vừa triển khai

### A. Tối ưu Đạo diễn hình ảnh (app/director.py):
1. **Nhận diện nhân vật có tên tuổi cụ thể**:
   - Khi kịch bản nhắc đến người nổi tiếng (Barack Obama, Elon Musk, Putin, Biden, nhân vật lịch sử...): Đạo diễn tự động chỉ định prompt phác họa chân dung đặc trưng ("recognizable portrait sketch of [Tên nhân vật] with their iconic facial features and appearance"), loại bỏ người que vô danh.
2. **Hiển thị biểu tượng & Quốc kỳ chính xác** (luật cấm chữ trong prompt Flow chỉ cấm chữ rác/watermark, chừa cờ, biểu trưng và con số then chốt):
   - Nhận diện các quốc gia/tổ chức để vẽ đúng cờ và biểu trưng (ví dụ: cờ Pháp 3 sọc xanh-trắng-đỏ, cờ Mỹ, biểu tượng sao la bàn NATO...).
3. **Hiển thị số liệu & mốc thời gian thực tế**:
   - Bắt các dữ kiện định lượng trong câu thoại (ví dụ: 15 km, 26.000 đồng, 17 phút, 114 vị trí...) để vẽ thông số, đồng hồ đo, thước kẻ hoặc biểu đồ trực quan.
4. **Bố cục khung hình đa lớp (3–5 yếu tố)**:
   - Phân tầng Tiền cảnh (Foreground) - Trung cảnh (Midground) - Hậu cảnh (Background) bám sát các danh từ then chốt của câu thoại.

### B. Nâng cấp Engine Âm thanh (ZeroTTS):
1. **Chuyển dịch toàn diện sang ZeroTTS**:
   - Xóa bỏ hoàn toàn OmniVoice và Voicebox cồng kềnh; mặc định 100% sử dụng **ZeroTTS** với giọng **Bảo Trang** (`zerotts|baotrang`).
   - Tối ưu ONNX CPU Execution Provider, không tiêu hao VRAM của GPU dành toàn bộ sức mạnh GPU cho Qwen-VL và dựng video.
   - Sửa dứt điểm lỗi phiên bản `tokenizers` và tương thích Windows console UTF-8.

### C. Cơ chế tự sửa lỗi Profile Flow:
1. Tự động phát hiện khi profile Chrome bị treo hoặc không bấm được nút tạo ảnh.
2. Cơ chế tự phục hồi thông minh: tự động đóng tab lỗi và mở lại tab sạch với URL chính xác như ban đầu, giúp hàng đợi tạo ảnh không bao giờ bị nghẽn.

### D. Tối ưu bộ nhớ & Lưu trữ hệ thống:
1. Đã dọn dẹp sạch sẽ hơn **79 GB** dữ liệu và mô hình AI cũ không dùng (HuggingFace Hub cache + LM Studio models cũ + vixtts-server).
2. Hệ thống gọn gàng, tăng tốc độ load ứng dụng và bảo đảm không gian lưu trữ cho việc sản xuất video dài.
