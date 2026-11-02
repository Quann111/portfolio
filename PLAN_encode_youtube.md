# Plan cho Gemini: thêm bước "Encode YouTube" vào AutoVideo (app truyện)

## Bối cảnh
Hiện người dùng phải chạy tay tool `C:\Users\ADMIN\Downloads\1\Encode video Youtube\Encode video Youtube\COVER CHUAN *.bat` để re-encode video trước khi up YouTube. Yêu cầu: app `D:\AI\truyen\autovideo` tự làm việc này **sau khi QC, trước khi đóng gói** (`package`), để file trong `projects/<id>_<slug>/video/` đã là bản encode chuẩn. Chạy tự động hoàn toàn, không chờ duyệt.

## Thông số encode cần sao chép đúng (từ file .bat)
Lệnh gốc (16:9). Bản 9:16 chỉ khác `-r 30` và `-aspect 9:16`:
```
ffmpeg -y -i IN -c:v libx264 -profile:v high -level:v 4.2 -preset medium -crf 18
 -b:v 16.6M -maxrate 16.6M -bufsize 33.2M -g 30 -keyint_min 30 -sc_threshold 0
 -bf 3 -refs 3 -r 29.97 -aspect 16:9 -pix_fmt yuv420p
 -color_primaries 1 -color_trc 1 -colorspace 1 -color_range 1 -movflags +faststart
 -c:a aac -profile:a aac_low -b:a 317k -maxrate:a 538k -ar 48000 -ac 2 -cutoff 18000 OUT
```
Giữ nguyên các tham số này, không "tối ưu" hay bỏ bớt.

## Các thay đổi

### 1. `app/render.py`: thêm hàm `youtube_encode(src, dst, cfg)`
- Dùng `render.duration_ms` hoặc ffprobe để lấy width/height. Nếu `h > w` thì dùng preset **9:16** (`-r 30`, `-aspect 9:16`), ngược lại dùng **16:9** (`-r 29.97`, `-aspect 16:9`). Cho phép `cfg.encode.aspect` ghi đè: `auto` / `16:9` / `9:16`.
- Dựng danh sách args như lệnh trên, rồi gọi qua helper sẵn có `render._run` (dùng `procs.run`: không bật cửa sổ, nút Stop kill được, có timeout). **Không** dùng `subprocess.run` trực tiếp.
- Ghi ra `dst` tạm (`*_enc_tmp.mp4`). Chạy xong thì kiểm tra ffprobe: file có tồn tại, size > 0 và duration lệch không quá 1s so với bản gốc. Đạt thì `os.replace` đè lên file output gốc, để `jobs.output` và đường dẫn giữ nguyên.
- Timeout gợi ý: `max(600, duration_s * 3)` giây.

### 2. `app/worker.py`: thêm stage `encode`
- `STAGES` (dòng ~19): chèn `"encode"` giữa `"qc"` và `"package"`.
- Thêm method `_st_encode(self, job)`, đặt cạnh `_st_qc` (~dòng 1327):
  - Nếu `cfg.encode.enabled` là false thì return luôn (bỏ qua bước).
  - Lấy đường dẫn video bằng `self._output(job)` hoặc `job["output"]`, giống cách `_st_package` đang làm.
  - Gọi `render.youtube_encode(...)`, báo tiến độ bằng `pipeline.sub` hoặc `self.progress`, theo đúng pattern của các stage khác.
  - Nếu lỗi: raise để job sang `failed`, giống các stage khác. **Không** để job ở trạng thái chờ duyệt.
- Kiểm tra vòng lặp stage (dòng 381-403) để chắc rằng job cũ đang dở ở `qc` hoặc `package` vẫn chạy tiếp được: resume theo `stage` đã lưu, nên chỉ cần chèn vào giữa danh sách.
- Style Nikola (`_nikola_assemble`) cũng ra một file mp4 cuối, nên bước encode áp dụng chung. Kiểm tra lại là Nikola cũng đi qua qc → encode → package.

### 3. `app/pipeline.py`: tên bước hiển thị
- Thêm `"encode": "🎞️ Encode chuẩn YouTube"` vào bảng tên/icon (~dòng 12), đặt trước `package`. Kiểm tra phần tính ETA (`:198-222`) xem có danh sách stage cứng không; nếu có thì thêm `encode` vào.

### 4. `config.yaml`: section mới
```yaml
encode:
  enabled: true
  aspect: auto        # auto | 16:9 | 9:16
```
Đọc qua `cfg.get("encode", {})`, mặc định `enabled: true` khi thiếu key, để config cũ vẫn chạy.

### 5. (Tùy chọn) UI web
Không bắt buộc. Nếu làm thì thêm một toggle `encode` qua kv (`db.kv_set`) + route POST trong `api.py`, theo mẫu `/mode` (`api.py:729`, `web/app.js:610`). Giá trị trong kv ưu tiên hơn config.yaml.

## Không làm
- Không đụng `packager.py`. Packager vẫn chỉ move file, nhưng lúc đó file đã là bản encode.
- Không thêm hậu tố `_converted` vào tên file. Tên giữ nguyên `{jid:04}_{slug}.mp4`.

## Kiểm thử
1. Unit nhanh: gọi `render.youtube_encode` trên một mp4 ngắn (ngang và dọc). Dùng ffprobe kiểm tra: `h264 High@4.2`, `r_frame_rate` là 30000/1001 (ngang) hoặc 30/1 (dọc), DAR 16:9 / 9:16, `yuv420p`, `bt709`, audio AAC 48k stereo, bitrate video ~16.6M.
2. Chạy một job AutoVideo ngắn ở chế độ Auto: UI phải hiện bước "Encode chuẩn YouTube", job phải về `done`, và file trong `projects/.../video/` phải đạt các thông số ở mục 1.
3. Đặt `encode.enabled: false` thì bước bị bỏ qua, job vẫn `done`.
4. Bấm Stop trong lúc đang encode: ffmpeg phải bị kill, không còn sót file `_enc_tmp.mp4` gây lỗi lần chạy sau (xóa file tmp ở đầu `_st_encode`).
5. `python -m py_compile app/render.py app/worker.py app/pipeline.py`.
