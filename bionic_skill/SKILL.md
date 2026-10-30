---
name: autovideo
description: Gửi chủ đề video vào app AutoVideo (tạo video whiteboard dài tự động), xem hàng đợi, bắt đầu hoặc dừng. Dùng khi người dùng nói "làm video về...", "thêm chủ đề", "tiến độ video", "dừng/chạy AutoVideo".
---

# AutoVideo

App AutoVideo chạy trên máy này, API tại `http://127.0.0.1:8765`. App phải đang mở.

- Thêm chủ đề (một hoặc nhiều):
  `curl -s -X POST http://127.0.0.1:8765/topics -H "Content-Type: application/json" -d "{\"topics\": [\"Chủ đề 1\", \"Chủ đề 2\"]}"`
- Xem hàng đợi và tiến độ: `curl -s http://127.0.0.1:8765/jobs`
- Bắt đầu chạy liên tục: `curl -s -X POST http://127.0.0.1:8765/start`
- Dừng (lưu session, lần sau làm tiếp): `curl -s -X POST http://127.0.0.1:8765/stop`

Tạo ảnh bằng Auto Flow (Google Flow) – theo từng dự án (job):
- Trạng thái ảnh mọi dự án: `curl -s http://127.0.0.1:8765/projects`
- Chi tiết một dự án (lượt, file prompt, cảnh thiếu): `curl -s http://127.0.0.1:8765/projects/3`
- Lấy file prompt lượt hiện tại để Import TXT vào Auto Flow: `curl -s http://127.0.0.1:8765/projects/3/prompts.txt`
- Quét ngay thư mục ảnh (tuỳ chọn thư mục khác): `curl -s -X POST http://127.0.0.1:8765/projects/3/import -d "{\"folder\": \"D:/anh\"}"`
- Báo cáo QC từng ảnh: `curl -s http://127.0.0.1:8765/projects/3/report`
- Bỏ chờ, tạo phần còn thiếu bằng FLUX local: `curl -s -X POST http://127.0.0.1:8765/projects/3/fallback`

Khi người dùng đưa chủ đề: thêm vào hàng đợi rồi gọi /start, sau đó báo lại ID job.
Khi hỏi tiến độ: gọi /jobs và tóm tắt trạng thái, bước hiện tại, đường dẫn video đã xong.
