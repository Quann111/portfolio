# AutoVideo – Plan tổng hợp (cập nhật 2026-10-24)

## Mục tiêu
Một app desktop duy nhất (`D:\AI\truyen\autovideo`) nhận chủ đề và tự sản xuất video whiteboard dài, chạy liên tục 24/7, chỉ dùng model local. Bấm **Bắt đầu** là chạy, bấm **Dừng** thì lưu session, mở lại chạy tiếp đúng chỗ.
Nguyên tắc chất lượng: **tạo → đo → phát hiện lỗi → sửa đúng lỗi → đo lại → chỉ xuất khi đạt Quality Gate**.

| Vai trò | Công cụ |
|---|---|
| Lập kế hoạch, viết, chấm điểm | Qwen3-VL 8B trong LM Studio (`lms`, API `localhost:1234`) |
| Tạo ảnh | stable-diffusion.cpp + FLUX.1-schnell Q4 (GGUF) |
| Giọng đọc + ASR | OmniVoice (k2-fsa) + Whisper đi kèm |
| Dựng whiteboard | `geeklee/srt-whiteboard-animation` (`.venv` Python 3.9 riêng) |
| Đo hình / media | OpenCV, FFmpeg |
| Trạng thái | SQLite (`data/session.db`) |
| Điều khiển | GUI PySide6, API `127.0.0.1:8765`, Bionic Skill |

Máy: RTX 4090 24GB, Windows 11, Python 3.11 (`.venv`). Cài đặt: `setup.bat` (đã xong).

**Ký hiệu:** ✅ đã làm + đã chạy thật/kiểm tra · 🟡 đã code + kiểm tra nhanh, chờ kiểm chứng với video thật · ⬜ chưa làm

## Pipeline
```
TOPIC → SỬA CHÍNH TẢ → RESEARCH (fact sheet) → HOOK → VIDEO PLANNER / BEATMAP → BIBLE
 → DIRECTOR: SCRIPT + STORYBOARD → RETENTION QC (viết lại) → ĐỦ ĐỘ DÀI → VISUAL VARIETY → TÁCH CẢNH DÀI
 → [Quality mode: chờ duyệt]
 → TEXT NORMALIZER → TTS (pause/speed theo cảm xúc) → AUDIO QC (tín hiệu + Whisper) → đọc lại nếu lệch
 → ẢNH (FLUX; draw-over tuỳ chọn) → IMAGE QC + KHỚP HÌNH–LỜI + ĐỘ PHỨC TẠP → tạo lại / đơn giản hoá
 → GROUNDING (thứ tự vẽ) → WHITEBOARD RENDER → CAMERA + CHUYỂN CẢNH → ghép tiếng
 → PHỤ ĐỀ + NHẠC NỀN DUCKING → FFmpeg → FINAL QC → điểm ≥ 80 ? XUẤT : AUTO REPAIR → QC
```

## 1. Nội dung
| Thành phần | File | Trạng thái |
|---|---|---|
| Sửa chính tả chủ đề trước khi làm ("khoả" → "khỏe") | `app/research.py` | ✅ |
| Research offline: fact sheet từ `knowledge/*.txt|md` + kiến thức model, chỉ giữ confidence ≥ 0.8 | `app/research.py` | ✅ |
| Hook: 5 phương án, tự chấm, chọn cao nhất; cấm câu mở đầu nhàm | `app/director.py` | ✅ |
| Video Planner: mục tiêu, khán giả, mức kiến thức, thông điệp, 8–15 key points, interest curve, visual strategy → beatmap theo mốc thời gian | `app/planner.py` | 🟡 |
| Storyboard mỗi cảnh: purpose, narration, visual_type (15 loại), visual_concept, image_prompt, drawing_order, focus, camera, transition, emphasis, keywords, emotion, pause_before, importance | `app/director.py` | 🟡 |
| Ép đủ số cảnh (schema min/max) và đủ số chữ (viết dài thêm cảnh < 80%) | `app/director.py` | 🟡 (sửa lỗi video 20' ra 8.6') |
| Retention gate: boredom/repetition/retention mỗi đoạn, dưới ngưỡng → viết lại, giữ bản tốt hơn | `app/director.py` | ✅ |
| Visual Variety: `max_same_type 2`, `max_static_scenes 2`, `force_change_every_seconds 8` → đổi kiểu hình/camera | `app/variety.py`, `channel.yaml` | 🟡 |
| Tự tách cảnh dài > 1.6× mục tiêu thành 2–3 cảnh nối tiếp (draw_over) | `app/director.py` | 🟡 |
| Bible nhân vật/đồ vật theo video + Channel Bible | `app/director.py`, `channel.yaml` | ✅ |
| Prompt ảnh đơn giản: ≤ 3 đối tượng, tả hình dạng, không tên riêng/nhãn | `app/director.py`, `app/image.py` | ✅ |
| Canvas State (thử nghiệm, **tắt** mặc định): cảnh draw_over vẽ tiếp lên hình cảnh trước bằng img2img + mask | `app/canvas.py` | 🟡 |
| Chế độ Auto / Quality (duyệt, sửa lời/prompt, viết lại) | `app/worker.py`, `app/gui.py` | ✅ |

## 2. Âm thanh
| Thành phần | File | Trạng thái |
|---|---|---|
| OmniVoice: giọng tạo 1 lần rồi khoá (`data/voice.pt`) hoặc clone `ref_audio` | `app/tts.py` | ✅ |
| Text normalizer: số/ngày/tiền/%/đơn vị → chữ tiếng Việt + từ điển `pronunciation.yaml` | `app/normalizer.py` | ✅ |
| Audio Director: speed/pause theo cảm xúc, `pause_before` trước câu quan trọng, nghỉ dài sau ý chính | `app/tts.py` | 🟡 |
| Audio QC: clipping, độ to, im lặng dài, tốc độ đọc; Whisper nghe lại, lệch > 25% → đọc lại (tối đa 2), ghi từ khó vào `data/pronunciation_log.txt` | `app/audio_qc.py` | 🟡 |
| Nhạc nền theo mood từng đoạn (`music/<mood>/`), fade, ducking, loudnorm −16 LUFS | `app/render.py` | ✅ |

## 3. Hình & dựng
| Thành phần | File | Trạng thái |
|---|---|---|
| Image QC: semantic, style, composition, continuity, has_text; điểm tổng do code tính | `app/image.py` | ✅ |
| Khớp hình–lời: `timing_match`, `alignment` ≥ 0.82 | `app/image.py` | 🟡 |
| Độ phức tạp nét vẽ (OpenCV) → `estimated_draw_time`, `suitability` < 0.6 → tạo lại đơn giản hơn | `app/complexity.py` | ✅ (kiểm tra nhanh) |
| Hết lượt → dùng ảnh điểm cao nhất; lượt cuối tự đơn giản hoá | `app/worker.py` | 🟡 |
| Thứ tự vẽ: Qwen3-VL grounding theo `drawing_order`, fallback OpenCV | `app/annotate.py` | ✅ |
| Render whiteboard (UTF-8 cho tiến trình con; vá lỗi `_lay_ink` trong repo geeklee), song song 3 | `app/render.py` | ✅ |
| Camera zoom_in/zoom_out/pan về đối tượng chính sau khi vẽ xong; fade, chuyển chương | `app/transition.py` | ✅ (kiểm tra nhanh) |
| Phụ đề SRT tách dòng ngắn, dời theo khoảng nghỉ đầu; tuỳ chọn in lên video, tô màu từ khoá | `app/subtitles.py` | ✅ |

## 4. Kiểm tra cuối & sửa tự động
| Thành phần | File | Trạng thái |
|---|---|---|
| Kỹ thuật: khung đen, im lặng, độ dài | `app/video_qc.py` | ✅ |
| Khung cuối mỗi clip trùng ảnh gốc; Qwen soi khung lỗi | `app/video_qc.py` | ✅ |
| Điểm tổng 8 phần: content, retention, visual, alignment, continuity, audio, render, technical | `app/video_qc.py` | 🟡 |
| Auto repair: dưới 80 điểm → tạo lại ảnh cảnh kém / render lại | `app/worker.py` | ✅ (job 1: 75.8 → 79.0) |
| Ngân sách thử lại: script 2, tts 2, image 3, whiteboard 2, final_scene 1 | `channel.yaml`, `app/worker.py` | 🟡 |

## 5. Vận hành 24/7
| Thành phần | File | Trạng thái |
|---|---|---|
| Session SQLite, resume đúng cảnh sau Dừng/crash/mất điện | `app/db.py` | ✅ |
| Nút Dừng dừng ngay cả khi đang viết kịch bản; tiến độ theo đoạn | `app/worker.py` | ✅ |
| Tự bật LM Studio server + load model | `app/llm.py` | ✅ |
| Phân loại lỗi: tạm thời (OOM, mạng, khoá file) / tạo lại được / logic / nghiêm trọng (thiếu model, đĩa đầy) – nghiêm trọng dừng cả hàng đợi + banner đỏ | `app/errors.py`, `app/worker.py` | ✅ (kiểm tra nhanh) |
| Resource Manager: nhiệt GPU ≥ 90°C giảm tải, ≥ 95°C tạm dừng; đĩa < 20GB dọn, < 10GB ngừng nhận job, < 5GB dừng khẩn; xác minh VRAM trước bước nặng | `app/resources.py` | 🟡 |
| Bảo trì 3h sáng hằng ngày + hằng tuần → `data/health/YYYY-MM-DD.json` | `app/maintenance.py` | 🟡 |
| Lưu phiên bản mỗi job (app, hash prompt/channel/config, model, giọng, seed ảnh) | `app/maintenance.py`, `app/worker.py` | 🟡 |
| Golden tests: 5 chủ đề cố định 1 phút, so baseline, giảm > 3 điểm → FAIL | `tests/run_golden.py`, `tests/golden/` | 🟡 (đang chạy lần đầu) |
| Kiểm tra nhanh 18 mục không cần GPU/LLM | `tests/test_units.py` | ✅ 18/18 |
| Tự sinh chủ đề khi hết hàng đợi; tự bắt đầu; mở cùng Windows | `app/worker.py`, `app/gui.py` | ✅ |

## 6. Giao diện & tích hợp
| Thành phần | File | Trạng thái |
|---|---|---|
| GUI: Bắt đầu/Dừng, chế độ, hàng đợi (lên/xuống/duyệt/chạy lại/xoá, điểm), tiến độ, ảnh xem trước, log | `app/gui.py` | ✅ |
| Thẻ GPU/nhiệt/VRAM/RAM/đĩa + banner lỗi nghiêm trọng | `app/gui.py` | 🟡 |
| API nội bộ + Bionic Skill | `app/api.py`, `bionic_skill/SKILL.md` | ✅ |
| Khởi chạy 1 cú nhấp (`AutoVideo.bat`), tự chạy setup nếu thiếu | `AutoVideo.bat`, `setup.bat` | ✅ |

## 7. Giai đoạn tiếp theo – Chất lượng thực tế & độ tin cậy (bổ sung 2026-10-24)
Nguyên tắc: **không thêm module chỉ để kiến trúc đầy đủ**. Xác minh bằng video thật trước, rồi sửa theo thứ tự tác động lên người xem. Giữ cấu trúc `app/` hiện có; module mới chỉ thêm khi không gộp được vào module cũ. Luật đa dạng không được ép đổi hình/camera nếu làm hỏng mục đích giải thích.

### 7.1 Hình ảnh – Visual Director 2.0 (P0)
| Thành phần | File | Trạng thái |
|---|---|---|
| Composition planning: vị trí đối tượng chính, vùng trống an toàn cho phụ đề (dải dưới ~18%), hướng nhìn, cân bằng bố cục – đưa vào storyboard (`composition`, `subtitle_safe`) | `app/director.py` | 🟡 |
| Visual hierarchy: đối tượng chính / phụ / điểm nhấn với kích thước khác nhau | `app/director.py`, `app/image.py` | 🟡 |
| Line-art style lock: độ dày nét, mức chi tiết, không đổ bóng, nền giấy – một khối cố định trong `channel.yaml` dùng cho mọi prompt | `channel.yaml`, `app/image.py` | 🟡 |
| Shot variation thực tế: đo độ giống giữa ảnh liền nhau (histogram + bố cục nét OpenCV), không chỉ đổi nhãn `visual_type` | `app/visual_qc.py` | 🟡 |
| Visual callback: Director chọn 1–2 biểu tượng xuyên suốt, nhắc lại ở cao trào/kết | `app/planner.py`, `app/director.py` | 🟡 |
| **Tách điểm, có ngưỡng cứng từng tiêu chí** (không lấy trung bình bù nhau): `semantic_score` ≥ 0.7, `visual_quality_score` ≥ 0.65, `whiteboard_suitability_score` ≥ 0.6, `continuity_score` ≥ 0.5; ảnh đẹp nhưng sai nghĩa → loại | `app/image.py` | 🟡 |
| Kiểm tra trước render (OpenCV): ảnh gần trắng/quá tối, nét quá mảnh/dày, đối tượng sát mép, thiếu khoảng trắng, quá nhiều chi tiết nhỏ, gần trùng cảnh trước, đè vùng phụ đề | `app/visual_qc.py` | 🟡 |
| Hiệu chỉnh `estimated_draw_time` bằng thời gian vẽ thực tế: lưu (độ phức tạp, thời lượng vẽ thật, thời gian render) mỗi cảnh, fit lại hệ số | `app/complexity.py`, `data/calibration.json` | 🟡 |
| Benchmark ảnh: ~20 prompt đại diện (sơ đồ mũi tên, nhân vật+đồ vật, biểu đồ, timeline, so sánh, ẩn dụ, nhiều đối tượng) – lưu seed, thời gian, VRAM, điểm QC, thời gian render; dùng trước khi đổi model/LoRA | `tests/benchmark/images.yaml`, `tests/run_benchmark.py` | 🟡 |

### 7.2 Nội dung – Narrative Arc Validator & độ chính xác (P0)
| Thành phần | File | Trạng thái |
|---|---|---|
| Mỗi chương trả lời 5 câu: người xem đang hỏi gì, giải đáp thế nào, ví dụ cụ thể, điều bất ngờ/hệ quả, dẫn sang chương sau. Đầu ra là danh sách lỗi `{type, severity, evidence (trích câu), repair}` thay cho một điểm chung | `app/story_quality.py` | 🟡 |
| Kiểm tra toàn video: lời hứa mở đầu được thực hiện, mỗi đoạn có tiến triển, cao trào được chuẩn bị, kết thúc đáng nhớ, không mâu thuẫn giữa các chương | `app/story_quality.py` | 🟡 |
| Repair theo loại lỗi: `add_concrete_example`, `remove_redundant`, `add_transition`, `strengthen_payoff` – chỉ viết lại phần lỗi | `app/director.py` | 🟡 |
| Fact sheet phân loại: sự kiện kiểm chứng được / giải thích / giả thuyết / ý kiến; lưu đoạn trích nguồn khi có (`knowledge/`); không có nguồn → không gắn "đã xác minh", diễn đạt thận trọng; cấm số liệu/nghiên cứu/trích dẫn bịa | `app/research.py` | 🟡 |
| Kiểm tra câu trong kịch bản có số liệu/khẳng định → phải map được về một fact | `app/story_quality.py` | 🟡 |
| **Độ dài theo thời lượng thật**: T = Σ giọng đọc + Σ khoảng nghỉ + Σ phần không chồng lấp (nhạc/hình chạy song song không cộng). Sau TTS đo tổng; thiếu > 10% → Director bổ sung nội dung có giá trị (ví dụ, case) cho các chương mỏng nhất rồi TTS phần mới; không kéo dài bằng lặp ý hay đọc chậm | `app/worker.py`, `app/director.py` | 🟡 |

### 7.3 Âm thanh – Prosody & phát âm (P0)
| Thành phần | File | Trạng thái |
|---|---|---|
| Pronunciation QC: tên riêng, thuật ngữ, từ Anh xen Việt – so Whisper theo từng thuật ngữ; tự đề xuất mục mới cho `pronunciation.yaml` (cần người duyệt) | `app/audio_qc.py` | 🟡 |
| Prosody QC: câu hỏi lên giọng cuối, câu nhấn mạnh/kết luận có điểm dừng (đường F0 + năng lượng bằng librosa) | `app/audio_qc.py` | 🟡 |
| Consistency QC: âm lượng, âm sắc (MFCC trung bình), tốc độ giữa các cảnh – lệch bất thường so với trung vị → đọc lại | `app/audio_qc.py` | 🟡 |
| Pause QC: không ngắt giữa cụm từ, không nghỉ quá dài; Seam QC: chỗ nối WAV không có tiếng bật, không mất âm đầu/cuối, có crossfade ngắn | `app/audio_qc.py`, `app/render.py` | 🟡 |
| Ghi rõ: WER thấp ≠ giọng tự nhiên; điểm audio cuối gồm cả prosody/consistency | `app/video_qc.py` | 🟡 |
| Preset phối âm theo thể loại (giáo dục / kể chuyện / tài chính-khoa học / thư giãn): LUFS, true peak, mức nhạc, ducking, fade – chọn theo `channel.yaml` hoặc Planner | `channel.yaml`, `app/render.py` | 🟡 |
| Đo integrated loudness + true peak trên file thành phẩm (ebur128) | `app/video_qc.py` | 🟡 |
| Benchmark audio: bộ câu tiếng Việt có số, ngày, tên riêng, thuật ngữ – chạy khi đổi giọng/model | `tests/benchmark/audio.yaml` | 🟡 |

### 7.4 Đồng bộ lời – nét vẽ – phụ đề – camera (P0)
| Thành phần | File | Trạng thái |
|---|---|---|
| Timestamp theo từ (Whisper word timestamps) trên audio cuối cùng; đối tượng bắt đầu vẽ đúng lúc được nhắc tên | `app/audio_qc.py`, `app/annotate.py` | 🟡 |
| Phụ đề tạo lại từ audio cuối (sau mọi thay đổi speed/pause); không ngắt giữa tên riêng/cụm từ; không chồng lấn; tốc độ đọc ≤ 17 ký tự/giây; dòng vừa khung; không che đối tượng chính | `app/subtitles.py` | 🟡 |
| Camera chỉ chuyển động khi có mục đích (focus rõ, cảnh quan trọng); cấm move trên cảnh ngắn | `app/director.py`, `app/transition.py` | 🟡 |

### 7.5 Độ tin cậy vận hành (P0)
| Thành phần | File | Trạng thái |
|---|---|---|
| Artifact manifest: mỗi file (wav/png/mp4) có checksum, kích thước, thời lượng, trạng thái hoàn chỉnh; ghi sau khi file đóng; resume kiểm tra hợp lệ (ffprobe/decode) – **không coi "file tồn tại" là thành công** | `app/artifacts.py` | 🟡 |
| Worker lock (file lock + PID) để GUI, API, golden test, service không chạy trùng job | `app/lock.py`, `app/worker.py` | 🟡 |
| Safe shutdown: dừng → chờ subprocess xong/timeout → kill cây tiến trình; lúc khởi động dọn subprocess mồ côi (sd-cli, python render) | `app/worker.py`, `app/resources.py` | 🟡 |
| Preflight khi khởi động: model đủ & đúng kích thước, sd-cli/ffmpeg/lms chạy được, LM Studio có model, repo whiteboard + venv OK, quyền ghi, dung lượng – lỗi → banner, không chạy | `app/preflight.py` | 🟡 |
| Sao lưu SQLite hằng ngày (giữ 7 bản) + thử khôi phục; maintenance chỉ xoá artifact không thuộc job đang chạy/cần resume, và chỉ khi worker rảnh | `app/maintenance.py` | 🟡 |

### 7.6 Quality Gate hai tầng (P0)
| Thành phần | File | Trạng thái |
|---|---|---|
| **Tầng 1 – Hard Gate** (chặn xuất bất kể điểm): video không mở được/stream lỗi, thiếu chương/cảnh, audio mất/hỏng/lệch đồng bộ > ngưỡng, khung đen hoặc im lặng ngoài dự kiến, lỗi nội dung nghiêm trọng chưa xử lý, thiếu artifact/metadata không khớp | `app/video_qc.py` | 🟡 |
| **Tầng 2 – Quality Score** (chỉ sau khi qua tầng 1): content & narrative, retention & repetition, visual & semantic, continuity & variety, audio & subtitle, render & technical; ngưỡng 80, hiệu chỉnh sau bằng đánh giá người xem | `app/video_qc.py` | 🟡 (đã có 8 phần, chưa tách tầng) |
| Auto repair theo loại lỗi: sai nghĩa → sửa concept/prompt + ảnh; quá phức tạp → đơn giản hoá/tách cảnh; sai thuật ngữ → từ điển + TTS lại; lệch timing → cập nhật timing/phụ đề; chuyển cảnh lỗi → render lại đoạn đó; âm lượng → mix lại; lặp nội dung → viết lại chương + kiểm độ dài; lỗi kỹ thuật → chỉ render/assemble artifact bị ảnh hưởng | `app/worker.py` (`repair` map) | 🟡 |

> Trạng thái mục 7 (cập nhật): toàn bộ hạng mục P0 đã code (🟡), 34/34 kiểm tra nhanh đạt (`tests/test_units.py`). Benchmark có sẵn (`tests/run_benchmark.py images|audio`) nhưng chưa chạy lấy baseline. Hiệu chỉnh `estimated_draw_time` đang thu dữ liệu (`data/calibration.jsonl`), tự áp dụng khi đủ 30 mẫu. Phát âm: từ nghi đọc sai ghi vào `data/pronunciation_suggestions.yaml` để người duyệt thêm vào `pronunciation.yaml`. Lỗi nội dung nặng còn lại → Hard Gate chặn, cần duyệt tay.

### 7.7 P1 / P2
- **P1:** Visual callback nâng cao + cache asset/ảnh/audio hợp lệ (`app/cache.py`); dashboard điểm theo model/prompt/phiên bản (`app/metrics.py`); champion/challenger – thay prompt/model chỉ khi qua golden tests, có rollback; giao diện nhiều tab.
- **P2:** thumbnail, tiêu đề/mô tả/tag (`app/publishing.py`); xuất đa định dạng (dọc); research qua web; SFX; đa ngôn ngữ; upload YouTube.

## 7b. Tạo ảnh bằng Auto Flow (Google Flow) – provider mặc định (2026-10-26)
App **không điều khiển extension** (không bấm START, không đụng reCAPTCHA/token). Trao đổi qua file:
1. Bước ảnh xuất `exchange/job_<id>/round_<n>/prompts.txt` (1 dòng/prompt) + `map.json` (row → cảnh).
2. Người dùng: Auto Flow → **Import TXT** → **Add to queue** → **START** (Save folder `Downloads/AutoFlow/Image`).
3. App quét thư mục (đệ quy), đọc số `{row}` đầu tên file, chép vào `exchange/job_<id>/received/`, QC như cũ; ảnh không đạt → `rejected/` và lượt mới chỉ gồm cảnh lỗi; hết ngân sách → ảnh điểm cao nhất.
4. Hết `autoflow.wait_minutes` (60) chưa đủ ảnh → nếu `fallback_local` thì tạo phần thiếu bằng FLUX local.

| Thành phần | File | Trạng thái |
|---|---|---|
| Cấu hình `image.provider`, khối `autoflow` | `config.yaml` | 🟡 |
| Xuất prompt, quét/nhận ảnh, đọc tên file, report | `app/autoflow.py` | 🟡 (đọc tên file ✅ 7/7) |
| Bước ảnh nhánh Auto Flow, lượt, timeout, fallback | `app/worker.py` | 🟡 |
| Prompt 1 dòng cho Nano Banana | `app/image.py` (`target="flow"`) | 🟡 |
| API `/projects`, `/projects/{id}`, `/prompts.txt`, `/import`, `/report`, `/fallback` | `app/api.py` | 🟡 |
| Khung Auto Flow trên GUI (mở file prompt, copy, thư mục, quét ngay, dùng FLUX) | `app/gui.py` | 🟡 |
| Preflight theo provider | `app/preflight.py` | 🟡 |
| Bài giả lập end-to-end | `tests/test_autoflow.py` | 🟡 đang chạy |

## 7e. Kịch bản bằng NotebookLM + Qwen3-VL-30B-A3B (2026-10-26)
- `script.engine: notebooklm`: `app/notebook.py` xếp prompt vào `nb_tasks`; extension `chrome_ext_notebook/` (MV3, click/gõ thật qua DevTools) tạo sổ mới, hỏi dàn ý giữ chân (hook, open loop, twist, CTA) dạng JSON, rồi viết lời từng chương; câu trả lời nằm giữa `===BẮT ĐẦU===`/`===KẾT THÚC===`. API `/nb/next|status|resume|pause|diag`, `/nb/tasks/{id}/result|error`. Kịch bản lưu `exchange/job_<id>/script.md`.
- Extension chưa kết nối / tạm dừng / quá `wait_minutes` → tự quay về `director.build` (Qwen).
- `director.from_narration`: Qwen chia cảnh theo ý hình (nhóm câu liên tiếp, không sửa lời), gộp cảnh < `min_scene_seconds` (trừ câu hook), cảnh > `max_scene_seconds` → 2-3 hình; chỉ soát `unsupported_claim` so với fact sheet (viết lại thận trọng câu đó).
- LLM: `qwen3-vl-30b-a3b-instruct`, ctx 12288, `--gpu max`; TTS unload LLM nếu thiếu VRAM. Đo: ~33 tok/s (không nhanh hơn kỳ vọng – nghi tràn VRAM sang RAM chung).
- Trạng thái: ✅ chạy thật job 7 – 8 chương, ~3.400 chữ (`exchange/job_7/script.md`); prompt chương ngắn (ô hỏi NotebookLM giới hạn độ dài ~5.000 ký tự); kịch bản lưu `notebook_script.json` để chạy lại không phải viết lại.
- **Model theo bước** (`lmstudio.models`, mỗi lúc 1 model trên GPU): research = `qwen/qwen3-vl-8b` (81 tok/s), script = `qwen3-30b-a3b-instruct-2507` IQ4_XS (55 tok/s, 18.7 GB), ảnh/QC = `qwen/qwen3-vl-8b`. Bản VL-30B cũ chỉ 40 tok/s (20.7 GB, tràn VRAM – GPU 29%/315 MHz khi sinh chữ). Mọi mảng JSON tự có `maxItems` (chống lặp tới 9000 token).
- **Research nhiều thực thể**: tìm theo cả cụm (không tách "Nhật Bản" riêng), lọc nguồn phải có đủ tên chính.

## 7f. Giọng đọc – chọn engine (2026-10-26)
`tts.engine`: `omnivoice` (GPU, mặc định, giọng khoá `data/voice.pt`) | `vieneu` (VieNeu-TTS v3 Turbo, CPU ONNX, 48 kHz, Apache-2.0, clone từ `ref_audio` hoặc `data/voice_ref.wav` → giữ giọng cũ) | `zerotts` (CPU, MIT, chỉ giọng có sẵn – chưa clone tại máy). Whisper large-v3-turbo chạy riêng cho Audio QC. Tốc độ engine CPU chỉnh bằng time-stretch.
So sánh 4 câu job 7 (`tests/compare_tts.py`, file nghe `exchange/tts_compare/SO_SANH_*.wav`): OmniVoice RTF 0.33 (GPU) · VieNeu RTF 0.20 (CPU) · ZeroTTS RTF 2.25 (CPU). Whisper lệch ≤ 4% cả 3 → chọn bằng tai. **Người dùng chọn: ZeroTTS giọng `baotrang` (1), VieNeu (2)** → `engine: zerotts`, `fallback_engine: vieneu`. ZeroTTS khi đã nạp: RTF ~0.9 trên CPU. Engine khoá theo job (`versions.tts_engine`) để không lẫn 2 giọng trong một video; job 7 giữ OmniVoice.

## 7g. Ảnh giàu chi tiết – hướng A (2026-10-26)
- Lý do: luật cũ (từ thời FLUX) ép ảnh quá đơn giản: image_prompt 20–45 từ/2–3 vật, khối style "minimalist, lots of empty space, 1–3 objects", ngưỡng dễ vẽ 0.6 + lượt cuối "EXTREMELY SIMPLE".
- Mới: image_prompt 45–90 từ, 3–6 yếu tố lấy từ danh từ/hành động trong lời, nhân vật có biểu cảm, bối cảnh phác nhẹ (tiền/trung/hậu cảnh), ẩn dụ khi lời trừu tượng; cấm tô nền (trời đêm…), cấm số áo/tỉ số; style "New Yorker cartoon spirit" + STYLE LOCK 3–6 yếu tố; ngưỡng dễ vẽ 0.35; lượt cuối chỉ "bớt chi tiết nhỏ".
- Prompt Flow: luật cứng (khổ, style, cấm chữ, tránh) đặt TRƯỚC, mô tả cảnh sau, tối đa 2400 ký tự – bị cắt chỉ mất đuôi mô tả.
- Lượt tạo lại: chỉ dẫn sửa theo tiêu chí bị trượt (`image.fix_instruction`) thay cho nhận xét QC cắt dở ("Fix: The first image clearly shows the main.").
- Thư viện prompt: skill `YouMind-OpenLab/nano-banana-pro-prompts-recommend-skill` (MIT) clone vào `skills/nano-banana-pro-prompts/` (+ cài cho Claude Code ở `.agents/skills/`). `app/prompt_lib.py` lấy 2 mẫu hợp kênh (Clean Whiteboard Animation #17504, New Yorker cartoon #2478) + nguyên tắc + 2 ví dụ cảnh làm few-shot cho Qwen.
- Ảnh Flow chụp vùng hiển thị: tự cắt góc bo đen + phủ biểu tượng ✦ Gemini (`flowbridge.clean_capture`).
- Đổi phong cách giữa chừng: `tests/enrich_job.py <job>` viết lại phần hình (không đổi lời) cho cảnh chưa có ảnh đạt + huỷ task Flow cũ. Job 7: 113 cảnh đã viết lại.

## 7h. Mở app → bấm Bắt đầu → AI tự làm tất cả (2026-10-26)
- `app/topic_scout.py`: hàng đợi còn ≤ `auto_topics.min_queue` (1) chủ đề → giữa hai job tự tìm `batch` (5) chủ đề:
  40 tin nóng Google News RSS + video **vượt trội** (≥ 1.2× trung vị lượt xem) của 13 kênh vẽ tay trong trang Notion
  "SHARE KEY - VẼ TAY VIDEOSCRIBLE" (đọc bằng `yt-dlp`, cache 24h) → Qwen 30B text đề xuất 12 ứng viên (mỗi cái đúng 1
  ngách; cấm chính trị trong nước, phe phái, thuyết âm mưu, giật tít, y tế) → **last30days** (`skills/last30days`, MIT,
  `uv` + Python 3.12, nguồn YouTube, không cần key) đo độ hot 30 ngày → điểm 0.6 hấp dẫn + 0.4 hot → chọn 5 (≤ 2 tin
  nóng, ≤ 2/ngách, không trùng đề tài/chủ đề cũ). Báo cáo `data/topic_scout/*.json`. Thiếu `uv`/`yt-dlp` → bỏ phần đo hot.
- Bấm Bắt đầu: preflight → extension NotebookLM / Flow chưa kết nối thì tự mở tab (`browser.open_on_start`).
- Bỏ cơ chế cũ `auto_topics_from`. Test `tests/test_topic_scout.py` 6/6.
- LLM: luôn gỡ model không dùng nếu 2 model cùng nằm trên GPU (tránh tràn VRAM).
- Chạy thật 18:30: 5 chủ đề (bong bóng AI, Houthi–Saudi, Musk–Ukraine, tên lửa đạn đạo, người giàu & khủng hoảng nhỏ), ~2 phút.

## 7i. Flow Bridge song song nhiều Chrome profile (2026-10-26)
- Extension 1.2.0: mỗi profile tự tạo `workerId` (storage riêng từng profile) + ô "Tên profile"; gửi `w`, `name` với mọi lời gọi.
- `flowbridge.claim_next(w)`: UPDATE nguyên tử → N profile nhận N task khác nhau cùng lúc; ảnh gom về đúng `exchange/job_<id>/received`, cột `flow_tasks.worker` ghi profile đã tạo.
- Captcha/giới hạn/tab chưa mở project ở MỘT profile → chỉ profile đó dừng (`set_worker_paused`), task trả về hàng chờ cho profile khác; "Tiếp tục" trên popup chỉ mở lại profile đó. Extension cũ (không gửi `w`) vẫn dừng chung như trước.
- `/flow/status` có `workers`, `running`; log "🖼 Flow: N profile đang tạo ảnh song song (tên…)". Test 15/15.
- Mỗi tài khoản vẫn theo hạn mức/nhịp riêng – không xoay tài khoản để lách giới hạn.

## 7j. Sửa lỗi chạy thật 26–27/10
- Flow: ảnh gán nhầm cảnh (ảnh trễ của prompt trước – chế độ x2 / quá giờ) → extension 1.2.2 chỉ gửi prompt khi Flow không còn ô "%" đang tạo; chờ mọi ảnh cùng prompt xong.
- Flow: 21 ảnh là ảnh chụp dính giao diện (khung prompt đè lên) → ẩn phần tử đè lên trước khi chụp + `flowbridge.ui_capture` loại ảnh có mảng tối lớn; 1.2.4: luôn hiện lại khung prompt sau khi lấy ảnh.
- Flow: chế độ "Tác nhân" (agent) nuốt prompt → 1.2.3 nhận diện và dừng riêng profile đó.
- Lỗi do tab/profile (không bấm được gửi, gõ không vào ô…) không còn trừ lượt của cảnh (trước đó 25 lỗi làm cảnh bị bỏ qua).
- QC: semantic 0.6, alignment 0.6 (QC 8B loại oan ảnh đúng ý).
- NotebookLM: ô hỏi khoá nút gửi khi câu hỏi > ~3.500 ký tự (3.292 gửi được, 3.924 không) → `notebook.MAX_PROMPT = 3200`, tự rút dữ kiện. `script.fallback_qwen: false` (không quay về luồng Qwen cũ), `new_notebook: false`.
- Chế độ bị đổi sang Quality → job 32 đứng chờ duyệt cả đêm → đặt lại Auto. Điểm review 0–10 của Qwen 30B quy về 0–1. Context 16384. Bảo trì không kiểm model FLUX khi dùng Flow.
- Job 7 xong 21:42 – 91.3 điểm.

## 7k. Phân vai NotebookLM / Qwen 8B (27/10)
- **NotebookLM**: viết kịch bản (dàn ý + từng chương) **và prompt ảnh** (`notebook.image_prompts`, lô ≤ 7 cảnh, mỗi câu hỏi < 3.200 ký tự; trả `image_prompt` + `drawing_order`). Job đã qua bước kịch bản → ở đầu bước ảnh, NotebookLM viết lại prompt cho cảnh chưa có ảnh (`worker._nb_prompts_for_pending`). Cấu hình `script.image_prompts: notebooklm | qwen`.
- **Qwen3-VL 8B (LM Studio)** – mọi vai trò (text/vision/light): chia cảnh theo ý hình, điều phối hàng đợi extension, research, tìm chủ đề, QC nhanh.
- QC nhanh: ảnh thu về JPEG ≤ 768 px trước khi gửi Qwen (`lmstudio.vision_max_side`), chỉ 1 ảnh/lần (`image.qc_fast`; đồng nhất với cảnh trước đo bằng OpenCV), max_tokens 900.
- Cảnh lỗi tạo ảnh ≥ 3 lần không còn bị bỏ (FLUX đã xoá) → gửi lại prompt đơn giản hơn, tối đa 9 lần.
- Rà soát: dọn import/biến thừa (pyflakes sạch), sửa thông báo cũ ("Auto Flow", "quay về Qwen", "30B"). Sự cố: lệnh thay hàng loạt làm hỏng 4 file → khôi phục từ file-history của Claude Code, bản hỏng lưu `../_autovideo_hong_2026-10-27/`.

## 7l. Dashboard web, dừng theo mốc, thư mục riêng, đồng hồ (27/10)
- **Dashboard** `app/web/` (HTML/CSS/JS, gradient + kính mờ + neon, pháo giấy khi xong video) hiển thị trong cửa sổ app qua `QWebEngineView` (`/ui`); Ctrl+K = giao diện Qt cũ, `main.py --classic`. Log trực tiếp qua SSE `/events` (`app/events.py`).
- **Dừng sau…**: xong job đang chạy / N chủ đề nữa / chủ đề #ID (nút 🏁 trên dòng). `kv stop_after`; khi có mốc không tự tìm chủ đề mới. API `POST /stop_after`.
- **Các bước + đồng hồ** `app/pipeline.py`: 8 bước (nghiên cứu → kịch bản → giọng → ảnh+QC → vẽ → ghép → QC cuối → đóng gói), `jobs.steps` lưu trạng thái, thời gian chạy thật cộng dồn (dừng app không cộng), tổng kể cả chờ, ước tính còn lại. Tab "Thống kê thời gian" so sánh các job. API `/projects/{id}/timing`, `/stats/timing`.
- **Thư mục riêng mỗi job** `app/packager.py` → `projects/<id>_<tên>/`: `video/`, `kich_ban/` (script.md, loi_doc.txt, canh.json, sources.md, facts.json), `anh/canh_XXX.png` + `anh/khong_dat/`, `giong/`, `bao_cao.json|html`. Bước mới `package` trong worker. Job 3–7 đã chuyển.
- **Nút trên từng dòng hàng đợi**: 📂 mở thư mục · 👁 xem nhanh (ảnh từng cảnh, video, kịch bản, thời gian) · 🏁 · 🔁 · ⬆⬇ · 🗑 xoá job + thư mục vào Thùng rác (`send2trash`).
- **Trình duyệt đang kết nối** (`/browsers`): mỗi Chrome profile – đèn trạng thái, phiên bản, đang làm cảnh nào, số ảnh, ảnh/giờ, lý do dừng + nút Tiếp tục; dòng NotebookLM.
- Flow extension 1.2.5: chỉ nhận captcha/giới hạn từ hộp thoại/thông báo/khung reCAPTCHA thật, ghi nguyên câu Flow báo; dừng vì giới hạn tự thử lại sau 30 phút (captcha thì không).
- Sửa: manifest báo nhầm ảnh "hỏng" mỗi lần khởi động lại (ảnh nằm ngoài thư mục work).
- Test `tests/test_dashboard.py` 22/22.
- Bổ sung đủ theo plan (rà soát lần 2): 13 bước con trong 8 bước lớn (`pipeline.SUBS`: nguồn web, fact sheet, NotebookLM dàn ý/chương/prompt ảnh, Qwen chia cảnh, giọng, đo thời lượng, ảnh Flow, vẽ, ghép, QC cuối, đóng gói); thời gian CHỜ tách riêng theo bước (`add_wait`: chờ NotebookLM kết nối, không có profile Flow nào chạy); chỉ số phụ (`pipeline.metrics`: giây/cảnh, % ảnh đạt QC lần đầu, từng profile Flow, phút làm / phút video); job cũ ước tính từ nhật ký (`estimate_from_log`); gói thư mục thêm `video/anh_bia.jpg`, `giong/giong_full.wav`, `anh/khong_dat/qc.json`, `clip/` (khi `keep_clips: true`); job mới làm thẳng trong `projects/<id>_<tên>/_lam_viec` (job cũ giữ `exchange/job_N`); kéo thả hàng chờ (`/projects/{id}/move`); `DELETE /stop_after`, `DELETE /projects/{id}`; đèn LM Studio / Flow / NotebookLM; ảnh bìa trong thư viện; xem lại timeline + chỉ số của job đã xong; cảnh báo extension bản cũ; giao diện cũ có nút 📂 / 🗑 thư mục; `setup.ps1` cài `send2trash`, `vieneu`, `zerotts`.
- Giao diện hacker (mặc định): nền đen, chữ xanh neon, mưa Matrix, vạch quét CRT, tiêu đề glitch; nút "Giao diện" đổi sang bản màu.

## Dọn dẹp 2026-10-27
- Xoá model LM Studio không dùng: Qwen3-30B-A3B-Instruct-2507 IQ4_XS (15.3 GB), Qwen3-VL-30B-A3B (18.3 GB) → còn Qwen3-VL 8B. Model OmniVoice (3 GB, HF cache) → Thùng rác (giọng dùng ZeroTTS/VieNeu; Whisper QC dùng model riêng).
- Video final giờ CHUYỂN (không chép) vào `projects/<job>/video`, `jobs.output` trỏ về đó; nút "Thư mục video" mở `projects/`. Đã bỏ bản trùng `output/*.mp4` và `exchange/job_3..7` (ảnh cảnh trỏ về `projects/<job>/anh`), rác test (`tts_compare`, `flowbridge_test`, 2 thư mục lưu trữ) → Thùng rác (~1 GB). Còn sót `output/0001_*.mp4` của job 1 đã xoá khỏi hàng đợi từ trước – giữ lại.
- Test Flow Bridge chạy trong thư mục tạm (trước đó tạo rác `projects/0001_test`, `0002_song_song`).

## Dọn dẹp 2026-10-26
Chuyển sang `D:\AI\truyen\_autovideo_da_don_2026-10-26` (có thể xoá hẳn khi chắc chắn): dữ liệu test `data/autoflow_*`, `flowbridge_test`, `research_test`, `golden_run`, `exchange/job_0`, test chạy tay `test_autoflow_real.py`, `test_flowbridge_real.py`. Xoá `__pycache__`, thư mục `vendor` rỗng, 102 task ảnh đã huỷ/test trong DB. Giữ: nhánh tạo ảnh local (FLUX, `canvas.py`) – cài lại bằng `setup.ps1 -LocalImages`; nhánh Auto Flow (Import TXT) làm dự phòng.

## 7d. Ảnh bằng Google Flow qua extension riêng "AutoVideo Flow Bridge" (2026-10-26)
- Model local (FLUX + sd.cpp, ~10.3 GB) **đã xoá** theo yêu cầu; `image.provider: flowbridge`, `autoflow.fallback_local: false`. Cài lại khi cần: `setup.ps1 -LocalImages`.
- App: hàng đợi `flow_tasks` + API `/flow/next`, `/flow/tasks/{id}/result|error`, `/flow/status|pause|resume|diag` (`app/flowbridge.py`); worker gửi prompt, nhận ảnh, QC, lượt mới cho cảnh lỗi; hết giờ chờ chỉ cảnh báo (không còn FLUX).
- Extension `chrome_ext/` (MV3): thao tác Flow bằng click/gõ phím thật qua DevTools (`debugger`), vòng chờ ở service worker (tab Flow có thể nằm nền), captcha/giới hạn → tự tạm dừng, lấy ảnh: tải trực tiếp → tải trong tab → chụp vùng ảnh. Không đụng captcha/token.
- Trạng thái: 🟡 – API test giả 9/9; với Flow thật đang sửa (1.0.3 không bấm được nút gửi; 1.1.0 nhận task không trả kết quả → 1.1.1 lấy ảnh 3 cách + luôn báo lỗi). Job 6 xong bằng FLUX trước khi xoá (91.7 điểm).

## 7c. Research web thời gian thực (2026-10-26)
Bionic có web search nhưng là tool nội bộ của agent (không có API) → AutoVideo tự tra cứu: Qwen trích thực thể + truy vấn **trung tính** → Google News RSS + DuckDuckGo (news 24h→7d→30d theo đúng tên, web) → Qwen lọc liên quan (giữ chỗ 3 tin mới nhất, fallback theo tên thực thể) → `trafilatura` đọc ≤ 8 trang (2500 ký tự, cache 6h) → fact sheet: mỗi fact có URL + câu trích + ngày; chỉ ✔ khi câu trích nằm trong trang đã đọc. Xuất `exchange/job_<id>/sources.md`, API `/projects/{id}/sources`. `config.research.mode`: web | web_first | offline; lỗi mạng → fallback diễn đạt thận trọng.
| Thành phần | File | Trạng thái |
|---|---|---|
| Tra cứu web, lọc, đọc trang | `app/websearch.py` | ✅ (Cốc Cốc: lấy được tin 20/10/2026) |
| Fact sheet theo nguồn, xác minh câu trích, khử trùng | `app/research.py` | ✅ |
| Director ưu tiên tin mới, nêu mốc thời gian, cấm chi tiết ngoài fact sheet | `app/director.py` | 🟡 |
| Beatmap co giãn đủ thời lượng; ≤ 2× TB cảnh/đoạn | `app/planner.py` | ✅ (kiểm tra số) |
| `unsupported_claim` → `fix_fact`, gộp lỗi trùng; cấm lặp ý đã nói | `app/story_quality.py`, `app/director.py` | 🟡 |
| LLM: giới hạn output theo context còn trống | `app/llm.py` | ✅ |

## 8. Thứ tự triển khai tiếp theo
1. **Hoàn tất golden test 1 phút** và *xem video thành phẩm*: giọng, nét vẽ, phụ đề, camera, chuyển cảnh; ghi lỗi người xem thực sự nhận thấy; lưu cấu hình + artifact để tái lập.
2. **Benchmark** ảnh (20 prompt) và audio (câu tiếng Việt có số/tên riêng/thuật ngữ) → baseline.
3. **Hard Gate + artifact manifest + worker lock + safe shutdown + preflight + backup DB.**
4. **Visual Director 2.0 + visual_qc trước render + tách điểm ngưỡng cứng.**
5. **Narrative Arc Validator + phân loại fact + độ dài theo thời lượng thật.**
6. **Audio prosody/pronunciation/consistency/seam QC + preset phối âm + phụ đề theo word timestamps.**
7. Video 10–20 phút → kiểm độ dài thật, giọng nhất quán, lặp hình, lỗi nối, tài nguyên; rồi mới chạy 24 giờ.
8. So sánh Canvas State với pipeline ảnh độc lập (cùng chủ đề, cùng QC); chỉ bật mặc định nếu tốt hơn.
9. P1, rồi P2.

## Lịch sử lỗi đã sửa khi chạy thật
| Lỗi | Cách sửa |
|---|---|
| Bấm đúp `setup.ps1` không chạy | Thêm `setup.bat`, `AutoVideo.bat` tự chạy setup |
| Job mới bỏ qua research | Job mới bắt đầu từ research |
| Render crash `UnicodeEncodeError` | Tiến trình con chạy UTF-8 |
| Repo whiteboard crash khi vùng không có nét | Vá `_lay_ink` dòng 405 |
| QC ảnh chấm 0.25 cho hầu hết ảnh, ảnh 1.00 vẫn bị loại | Chấm theo ý chính, điểm do code tính, bỏ qua chữ ký nhỏ, giữ ảnh tốt nhất |
| Ảnh có nhãn chữ, quá nhiều chi tiết | Prompt ≤ 3 đối tượng, cấm tên riêng/nhãn |
| Video 20' ra 8.6' (62/80 cảnh, lời ngắn) | Ép số cảnh + số chữ, viết dài thêm |
| Golden 1': lời 70–80 chữ/cảnh (mục tiêu 38) → 79s | Giới hạn 80–130% số chữ, rút gọn cảnh dài |
| Cảnh để giấy trắng tới gần cuối + **clip ngắn hơn lời (cắt mất cuối lời đọc)** | Gốc: grounding trả nhiều khung trùng → vùng rỗng; repo whiteboard bỏ qua ghi khung khi vùng rỗng. Sửa: gộp vùng bị che > 65% (`annotate._merge_hidden`), vá `_wash_contour` ghi đủ khung, render báo lỗi nếu clip ngắn hơn audio |
| Mọi cảnh đều zoom; `draw_over` khi Canvas tắt | Camera mặc định đứng yên, chỉ move khi có mục đích, không move cảnh < 6s; bỏ `draw_over` khi Canvas tắt |
| sd.cpp crash không rõ lý do | Log lọc dòng lỗi + mã thoát; phân loại lỗi để tự thử lại |
| Chế độ Quality làm hàng đợi dừng chờ duyệt (người dùng muốn tự động) | Chuyển Auto; luật: có job chờ duyệt thì dừng hàng đợi, làm xong từng job |
| LLM trả JSON bị cắt (fact sheet/kịch bản dài) | Phát hiện `finish_reason=length`, tự tăng max_tokens ×1.6 + giảm temperature khi thử lại |
| Audio QC báo nhầm "đọc sai" (mảnh chữ Việt `ng.`, `lai.`; `USD` đã đọc "đô la") | Chỉ xét thuật ngữ Latin còn lại sau chuẩn hoá |
| 12/96 ảnh job 3 vẫn có chữ | Prompt cấm chữ trên mọi bảng/biển/màn hình/sách; Director tránh vật dễ sinh chữ |
| Điểm nội dung bị trừ vì đoạn recap/CTA nhắc lại ý | Không phạt lặp ở recap/cta; điểm nội dung gộp thêm mạch truyện toàn video |
| Job 6: đoạn CTA dồn 44 cảnh (beatmap chỉ 0→9' cho video 20') | Co giãn beatmap + giới hạn cảnh/đoạn |
| Job 6: "output bị cắt ở 4000 token"; fact sheet vượt context → 400 | max_tokens theo context còn trống; trích trang 2500 ký tự |
| Job 6: hàng loạt unsupported_claim (model bịa) | Research web + `fix_fact` + cấm chi tiết ngoài fact sheet |
| Research: truy vấn tự giả định sự kiện ("bị thu hồi giấy phép") → 0 nguồn | Truy vấn trung tính theo tên thực thể + fallback lọc theo tên |
| Research: nguồn ngoài danh sách vẫn ✔; lỗi font trang web | Chỉ ✔ khi câu trích nằm trong trang đã đọc; tự nhận bảng mã |

## Kiểm chứng còn lại
1. Golden test 1 phút (`test_02_science`) – đang chạy, kiểm chứng Planner, storyboard, Audio QC, khớp hình–lời, camera. **Phải xem video, không chỉ đọc log/điểm.**
2. Lập baseline 5 chủ đề: `.venv\Scripts\python.exe tests\run_golden.py --update-baseline`.
3. Chạy tiếp job 2 bằng code mới; video 10–20 phút; sau đó 24h với 5 chủ đề, theo dõi nhiệt/VRAM/đĩa/log.
4. Bật thử `canvas.enabled` trên một job ngắn và so điểm.

> Lưu ý: các mục ✅ là kết quả chạy trên máy này (log, kiểm tra nhanh, video job 1), chưa có đánh giá độc lập của người xem về chất lượng nội dung, giọng đọc và hình.

## 7m. Kiểu dựng Nikola – bảng hai đảo (tách riêng kiểu Cổ điển)
- Chọn kiểu theo job (`versions.style`, khoá khi job bắt đầu); mặc định ở `style.default` / dashboard (kv `style_default`).
- Giữ: kịch bản NotebookLM, ảnh Google Flow (Flow Bridge), giọng ZeroTTS. Theo repo hi-nikola: 2 cảnh liên tiếp cùng
  chương = 1 màn = 1 ảnh hai đảo (rãnh giữa, đáy 18% trống, không chữ); QC bố cục; annotation 2 vùng theo giọng thật
  (dừng 0.5 s); `annotation_schema.py` + `check_drawable_regions.py`; engine nét của repo (skeleton, contour-wipe,
  hand-follow/hand-height đọc từ references); không camera; màn thử tự kiểm; chữ khoá ASS `\kf`; `finalize_stroke_video.py`;
  kiểm tra cuối; sản phẩm trong `projects/<id>/nikola/`. Code: `app/styles/nikola.py`, rẽ nhánh trong worker.
- Chưa làm: đường "hoạt hình chương trình" (HyperFrames) cho cảnh sơ đồ/thẻ số liệu – hiện mọi cảnh đi đường vẽ nét.
