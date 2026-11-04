# Plan tổng hợp cho Gemini: Làm xong từng job + Flow 2 prompt song song + chất lượng kịch bản/video + sửa ngày trên thumbnail

## Bối cảnh
- NotebookLM đã chạy 5 tab song song. Bước tạo ảnh Flow vẫn tuần tự 1 prompt/profile, nên đang là điểm nghẽn.
- Video ra đời có 5 lỗi người dùng nêu:
  1. hook mở đầu không nêu câu hỏi
  2. tiêu đề/thumbnail không khớp nội dung
  3. chủ đề quá rộng, mỗi video một mảng
  4. cảnh đứng yên lâu, minh hoạ không bám lời
  5. giọng đọc đều
- Người dùng muốn tự nhập/sửa ngày tháng in trên thumbnail.
- Lưu bản này vào `D:\AI\truyen\autovideo\PLAN_flow_quality_thumb.md` để Gemini đọc.

Luật chung:
- Đọc `AGENTS.md`. UTF-8 không BOM, không refactor ngoài phạm vi, không thêm thư viện.
- Chế độ Auto chạy hoàn toàn tự động, không thêm bước chờ duyệt.
- Báo cáo ĐẠT / KHÔNG ĐẠT / CHƯA KIỂM kèm log/ảnh thật.
- Làm theo thứ tự F → F2 → F3 → G → 0 → E → A → B1…B5 → C, báo cáo từng phần.

## F. SỬA LỖI KHÔNG JOB NÀO XONG (làm ĐẦU TIÊN, trước mục 0)
Số liệu thật lúc 20:30 ngày 03/11:
- Cả 5 slot NotebookLM đều `busy`. Job 1032, 1034, 1035, 1038 đã xong kịch bản (stage `tts`) nhưng vẫn giữ 4 slot; chỉ slot 2 còn làm việc.
- 7 job bị `failed` oan với lỗi "Không có slot NotebookLM nào có sổ": 1043–1047, 1049, 1051.

Nguyên nhân:
1. `youtube.py:195` gọi `notebook.ask(jid,"youtube")`. `ask()` tự `bind()` slot nhưng không `release`.
2. `script_ahead: 4` làm nhiều job cùng tranh slot.

ĐÃ SỬA SẴN (rà lại, KHÔNG ghi đè):
- `notebook.ask()`: job chưa có slot thì `bind` → `_ask_in_slot()` → `release(jid)` trong `finally` (khi `jid>0`).
- `notebook.init_slots()`: khi khởi động, nhả slot của job không còn task `queued`/`claimed` (trừ job đang running/preparing ở research/script).
- `config.yaml`: `worker.script_ahead: 0`, `worker.focus_one_job: true`.
- `worker._script_ahead_once` / `_prep_once`: khi `focus_one_job` bật thì `sa_n=0` / `prefetch=0`.

Việc còn lại:
- `py_compile app/worker.py app/notebook.py`. Nếu đang sửa dở `worker.py` thì GỘP, giữ 2 đoạn `focus_one_job`.
- Đưa 7 job nêu trên về `queued`, `attempts=0`, `error=NULL`, giữ stage. Dùng API/hàm "Tạo lại" có sẵn.

Kiểm thử F:
- Khởi động app: `select * from nb_slots` → 5 slot `idle`.
- Bộ YouTube của 1 job xong → slot của job đó về `idle`.
- Log không còn dòng "Không có slot NotebookLM nào có sổ".

## F2. SỬA SAU KIỂM TRA 20:54 (làm ngay sau F, trước E)
Số liệu thật:
- App KHÔNG chạy: cổng 8765 không mở, log dừng lúc 20:38:34, `crash.log` không có lỗi mới.
- Heartbeat extension NotebookLM dừng từ 20:38:49.
- Slot 1 `busy` với job 0 (topic_scout); task 836 `claimed` 16 phút không trả.
- Hàng đợi chỉ còn job 1036 (queued, stage `image`). Kịch bản của job này đã xong, nhưng job không được chạy.
- Lúc 20:34 và 20:38, vừa bấm Bắt đầu là app đi tìm chủ đề NotebookLM (chờ tối đa 15 phút) TRƯỚC khi làm job 1036.

Lỗi:
1. **topic_scout chen trước job dở.** `worker.py:230` chỉ kiểm job `running`/`preparing`. Job 1036 đang `queued` ở stage `image` nên bị coi là "không có job dở". Ngoài ra `topic_scout.due()` luôn đúng khi hàng đợi < `min_queue` (10).
   Sửa:
   - Nếu `db.next_job()` trả về job đã qua stage `research` (đã bắt đầu), hoặc có job nào `stage` ≠ `research`/`done` → KHÔNG scout, chạy job đó trước.
   - Chỉ scout khi job kế tiếp còn ở `research`, hoặc hàng đợi trống.
2. **Scout treo khi extension mất kết nối.** Trong `topic_scout`, nếu `notebook.connected()` sai quá 120 giây thì huỷ task, `release(0)`, ghi `topic_scout_last` rồi quay về làm job. Không chờ đủ 15 phút.
3. **Khi khởi động**, `init_slots` trả task `claimed` của job 0 về `cancelled` và nhả slot 1. Dòng `UPDATE ... job_id<=0` hiện có chỉ xoá `job_id`, chưa đặt `state='idle'` → phải đặt.
4. **Báo cáo trước của Gemini sai 2 điểm:**
   - Ghi "Phần 0 ĐẠT" nhưng chưa chạy kiểm 20 phút → phải ghi CHƯA KIỂM.
   - Ghi "5 slot idle" nhưng slot 1 đang `busy`.
   - Sau mỗi lần sửa phải khởi động lại app (`AutoVideo.bat`), bấm Bắt đầu, rồi kiểm DB/log thật.

Kiểm thử F2:
- Khởi động lại app → trong 60 giây log có `=== Job 1036 … (từ bước image …)`, không có dòng "Tìm chủ đề mới" trước đó.
- 5 slot `idle` (riêng slot dùng cho scout chỉ `busy` khi đang hỏi).
- Tắt extension NotebookLM khi scout đang chạy → sau ≤ 2 phút log báo bỏ lượt tìm chủ đề và job tiếp tục chạy.
- Job 1036 tới `done` (dán mốc giờ).

## F3. SAU KIỂM TRA 21:05 (F2 đã xác nhận: 5 slot idle; job 1036 chạy ngay bước image, không bị scout chen trước)
Số liệu thật:
- Job 1036 `running`, stage `image`: 57/70 cảnh đã có ảnh.
- `flow_tasks`: 98 done, 4 claimed, 9 queued, 4 error.

Lỗi cần xem:
1. **Profile Flow tạm dừng/chạy lại liên tục.** Từ 21:04:32 đến 21:05:18 có 7 lần "Không thấy ô nhập prompt – mở một project trên flow.google.com", sau 10–20 giây profile tự chạy lại.
   - Tìm nguyên nhân thật: tab đang tải lại sau repair? Trang project chưa render xong?
   - Hướng sửa: lần đầu không thấy ô nhập thì chờ, thử `diagnose` lại tối đa 3 lần × 5 giây rồi mới pause, và trả task về hàng đợi (không tính `attempts`).
   - KHÔNG đổi cơ chế captcha / repair / `Open_Chrome_Profiles.bat` (AGENTS.md 1.1–1.2). Nếu phải sửa extension: sửa tối thiểu, tăng version, sao lưu vào `backup_extensions/`.
2. **`/flow/tasks/<id>/result → HTTP 400`** (task 5610, 5618).
   - Đọc handler trong `app/api.py` để biết vì sao trả 400: task đã bị huỷ, hay đã được làm lại?
   - Nếu do kết quả đến muộn cho task đã bị thu hồi: trả 409 hoặc 200 `ignored` và log rõ, không để extension coi là lỗi.
3. **"No tab with id"** (3 lần): đây là tab Flow bị đóng hoặc tải lại. Chỉ cần trả task về `queued` đúng cách. Ghi lại số lần trước/sau khi sửa.

Kiểm thử F3:
- Job 1036 tới `done` (dán mốc giờ, đường dẫn `projects/1036_*`, và đủ file YouTube theo AGENTS.md mục 4).
- 30 phút sau khi sửa: số lần "Không thấy ô nhập prompt" giảm; không có HTTP 400 ở `/result`.
- Sau đó mới chạy kiểm thử mục 0: thêm 3 job, quan sát 20 phút chỉ 1 job `running`.

## G. VIẾT CHƯƠNG SONG SONG TRÊN 5 SLOT NOTEBOOKLM (người dùng đã chọn)
Hiện trạng (kiểm lúc 22:48):
- Job 1052 chiếm slot 1; slot 2–5 rảnh. Trong 3 giờ: slot 1 nhận 9 task, slot 2 nhận 1, slot 3–5 nhận 0.
- `write_script` (`notebook.py:538-570`) viết lần lượt từng chương trong cùng một sổ. Prompt chương chỉ ghi "dùng các dữ kiện ở lượt đầu", tức là dựa vào ngữ cảnh chat của slot 1.
- Chương 1 lỗi "Gõ câu hỏi vào ô không thành công (ô đang có: "")" và chờ khoảng 9 phút mới gửi lại.

Thay đổi:
1. **Dàn ý giữ ở slot chính** của job (bind như cũ).
2. **Mượn slot phụ:** thêm `notebook.bind_extra(jid, n, deadline)` để lấy tối đa `script.parallel_chapters` (mặc định 4) slot `idle` khác cho cùng job.
   - Bảng `nb_slots` chỉ có 1 `job_id` mỗi slot → slot phụ ghi `job_id=jid` và `step='chapter_k'`.
   - `get_job_slot` phải trả slot CHÍNH. Thêm cột `role` (`main`|`extra`) hoặc lưu slot chính trong `kv` `nb_main_<jid>`.
   - `release(jid)` nhả cả slot chính lẫn slot phụ.
   - Không còn slot rảnh → các chương còn lại xếp hàng ở slot đã có (không chờ, không lỗi).
3. **Prompt chương độc lập** (vì slot phụ không có ngữ cảnh chat của slot chính), ≤ `MAX_PROMPT` 3200 ký tự:
   - tiêu đề video, `core_message`
   - dàn ý rút gọn: tên + summary + `open_loop` của chương trước / chương này / chương sau
   - fact sheet rút gọn (`_facts_text(facts, limit≈1200)`), ưu tiên dữ kiện khớp từ khoá chương
   - hook (chương 1), CTA (chương cuối), câu treo dẫn sang chương sau
   - số chữ mục tiêu `w`; giữ nguyên các yêu cầu văn phong ở dòng 552-556
   - nếu vượt 3200 ký tự thì cắt fact sheet trước
4. **Chạy song song:** mỗi chương 1 thread gọi `ask(jid, f"chapter_{k}", prompt, deadline, check, slot=<slot cụ thể>)`.
   - `ask` và `_ask_in_slot` phải nhận tham số `slot`.
   - `_job_lock(jid)` hiện khoá cả job → đổi khoá theo slot, chỉ khi đang ở chế độ song song.
   - Giữ nguyên logic `chapter_k_more` khi chương quá ngắn (hỏi trên cùng slot đó).
   - Ghép `story` theo đúng thứ tự chương.
5. **Soát mạch nối** (1 lượt, ở slot chính): gửi đoạn 300 ký tự cuối chương k và 300 ký tự đầu chương k+1 cho mọi chỗ nối. Chỉ yêu cầu viết lại CÂU NỐI (≤ 2 câu mỗi chỗ), trả JSON `{"k": "câu thay"}`; app thay đúng câu cuối của chương k.
   - Không viết lại cả chương, prompt ≤ 3200 ký tự.
   - Lỗi thì bỏ qua lượt soát (ghi cảnh báo), không làm hỏng job.
6. **Gõ câu hỏi thất bại** ("Gõ câu hỏi vào ô không thành công"): extension thử gõ lại ngay 2 lần, mỗi lần cách 3 giây, trước khi báo lỗi. Phía app: task bị lỗi kiểu này được trả về `queued` ngay (không chờ `CLAIM_TIMEOUT`).
   - Nếu sửa `chrome_ext_notebook`: sửa tối thiểu, tăng version, sao lưu `backup_extensions/`.
   - Giữ nguyên `NEW_NB`, `initAutoClicker`, `autoRepairNb` (AGENTS.md 1.3).
7. **Tương thích:**
   - `script.parallel_chapters: 0` → hành vi cũ (tuần tự).
   - `focus_one_job` vẫn giữ: chỉ 1 job, nhưng job đó dùng nhiều slot.
   - topic_scout (`jid 0`) không được chiếm slot khi job đang viết chương.
   - Mục E (chia cảnh): mỗi chương chia cảnh trên slot đã viết chương đó; nếu khó thì chia trên slot chính, nêu rõ trong báo cáo.
8. **Dashboard:** mỗi slot hiện "Job #… chương k".

Kiểm thử G:
- 1 job 8 chương:
  - `select slot,step,status from nb_tasks where job_id=<id>` cho thấy chương rải trên ≥ 4 slot
  - các chương chạy cùng lúc: `created_at` chênh nhau < 30 giây
- Ghi thời gian bước script trước (job 1036: outline → chương 8 khoảng 15 phút) và sau.
- Kịch bản đủ số chữ mục tiêu ±15%; log có lượt soát mạch nối; đọc 3 chỗ nối, dán đoạn văn thật.
- Tắt 1 tab slot phụ giữa chừng: chương của nó được làm lại ở slot khác, job không fail.
- `parallel_chapters: 0` chạy y như cũ.
- Sau khi job xong: cả 5 slot về `idle`.

## 0. ƯU TIÊN SỐ 1: Tập trung làm xong 1 job rồi mới sang job 2 (làm TRƯỚC A/B/C)
Hiện trạng: `worker.script_ahead: 4` khiến 4 luồng phụ cùng viết kịch bản cho 4–5 job một lúc (log 18:41: job 1032–1036 cùng làm research). Hệ quả:
- LM Studio, NotebookLM và CPU bị chia nhỏ cho nhiều job, nên job đầu xong chậm.
- Nhìn dashboard thấy rối, giống như đang làm nhiều job cùng lúc.

Người dùng muốn: **dồn hết tài nguyên cho job đang chạy**, xong hẳn job đó (đến `done`) rồi mới làm job kế tiếp.

Thay đổi:
- `config.yaml`:
  - `worker.script_ahead: 0`
  - `worker.prefetch: 0` (giữ nguyên)
  - Thêm `worker.focus_one_job: true`
- `app/worker.py`:
  - khi `focus_one_job` bật, không khởi động luồng script-ahead và luồng prep
  - luồng chính lấy job theo đúng thứ tự hàng đợi và chạy hết các bước của job đó
  - không gọi `topic_scout.scout` trong lúc đang có job dở (chỉ gọi giữa hai job)
- Song song **bên trong một job** vẫn giữ, vì nó giúp job xong nhanh hơn:
  - ảnh Flow của job đang chạy dùng mọi profile; `flowbridge.claim_next` đã ưu tiên job `running`
  - phần A (2 prompt/profile) áp cho các cảnh **của cùng job đó**
  - NotebookLM: một job dùng một slot (các chương nối tiếp nhau). Các slot còn lại để trống hoặc dùng cho Top 10 chủ đề. Không gán slot cho job khác khi đang có job dở.
- Dashboard: dòng trên cùng hiện "Đang làm: Job #… – bước … (x/y)", và hàng đợi ghi "chờ".
- Job lỗi thì xử lý như cũ: tự retry hoặc chuyển sang job sau, không để kẹt cả hàng đợi.

Kiểm thử 0:
- Có 5 job queued, bấm Bắt đầu.
- Trong 20 phút, log và DB chỉ có **1 job** ở trạng thái `running`/`preparing`; các job khác giữ `queued`, stage `research`.
- Job 1 tới `done` thì job 2 mới bắt đầu (dán mốc thời gian).
- Ghi thời gian hoàn thành job 1 trước và sau thay đổi.

## E. Chia cảnh + prompt ảnh bằng NotebookLM (làm sau mục 0, trước A)
Hiện trạng: `worker._st_script` gọi `director.from_narration` (`app/director.py:536`), và hàm này chia cảnh theo thứ tự ưu tiên:
1. Gemini Web (`gemini_bridge.ask(..., "split_scenes")`, gửi cả kịch bản trong 1 prompt)
2. nếu không được thì Qwen trên LM Studio (`_group_all`, `_fix_variety`, `split_long` với `max_scene_seconds: 45`)

Prompt ảnh bổ sung do `config script.image_prompts: "gemini"` quyết định.

Người dùng muốn chia cảnh **dùng NotebookLM**: chính tab/slot vừa viết kịch bản. Tab đó còn ngữ cảnh chat và nguồn, nên minh hoạ bám nội dung tốt hơn.

Thay đổi:
- `config.yaml` → `script.scene_split: notebooklm` (`notebooklm | gemini | qwen`); `script.image_prompts: notebooklm`.
  - **Không tự động quay về Qwen** khi NotebookLM lỗi (giống `fallback_qwen: false`): báo lỗi, job tự thử lại sau.
  - Gemini chỉ dùng khi đặt `scene_split: gemini`.
- Thêm `notebook.split_scenes(jid, nb, cfg, log, check)` trong `app/notebook.py`:
  - **Dùng đúng slot của job**: gọi trước `release(jid)` trong `write_script`, hoặc giữ slot tới hết bước chia cảnh rồi mới release.
  - Giữ nguyên 100% lời đọc theo cách sau:
    - App tách câu bằng `director.sentences()` và **đánh số câu** cho từng chương.
    - NotebookLM chỉ trả **nhóm câu theo chỉ số**: `{"scenes":[{"from":0,"to":2,"visual_concept":"…","image_prompt":"…"}]}`.
    - App tự ghép `narration` từ các câu gốc. NotebookLM không được viết lại chữ.
  - Mỗi lần hỏi 1 chương; câu hỏi ≤ `MAX_PROMPT` (3200 ký tự). Chương dài thì gửi theo khối câu, mỗi khối ≤ 3000 ký tự.
  - Đặt quy tắc ngay trong prompt:
    - mỗi cảnh 1 ý hình ảnh, **≤ `scene.max_sec`** (7 giây ≈ 18–20 chữ), khớp với mục B4
    - `image_prompt` bằng tiếng Anh, bám đúng câu đang đọc (nhân vật thật, cờ, số liệu theo `WRITER_RULE_EN`/`visual_rules`), kết thúc bằng style whiteboard hiện có (`style_rule` ở `director.py:556`)
    - hook đứng riêng một cảnh
  - Kiểm tra bằng code:
    - các khoảng `from..to` phủ kín, liên tục, không chồng nhau, đủ mọi câu
    - sai thì hỏi lại 1 lần cho chương đó; vẫn sai thì tự chia đều theo số chữ và giữ `image_prompt` theo câu
  - Đặt `prompt_by: "notebooklm"` và các trường mặc định giống nhánh Gemini (dòng 606–620).
  - Lưu kết quả vào `notebook_scenes.json` để chạy lại không phải hỏi lại. Cache `(job, step, prompt_hash)` cũng tránh hỏi lặp.
- `director.from_narration`: thêm nhánh `scene_split == "notebooklm"` gọi hàm trên. Sau đó vẫn chạy `_fix_claims`, kiểm `variety`, và `split_long` với `limit = scene.max_sec`.
- Thanh tiến trình bước "split" hiện "NotebookLM chia cảnh chương k/N".

Kiểm thử E:
- 1 job mới:
  - log cho thấy chia cảnh chạy qua NotebookLM cùng slot với kịch bản, không có request LM Studio/Gemini cho bước split
  - ghép `narration` của mọi cảnh = đúng nguyên văn kịch bản (so chuỗi, dán kết quả `==`)
  - không cảnh nào vượt `max_sec`
  - điểm QC khớp hình–lời trung bình so với 1 job chia bằng Gemini/Qwen trước đó
- Tắt NotebookLM giữa bước chia cảnh: job báo lỗi tạm và tự làm lại, không chuyển sang Qwen.

## A. Mỗi profile Flow chạy 2 prompt cùng lúc trên 1 tab (các cảnh của job đang chạy)
Hiện trạng `chrome_ext/background.js`:
- một biến `busy`; `tick()` nhận 1 task, chờ ra ảnh rồi mới nhận tiếp
- `typeAndSend` (~dòng 125) chờ ảnh dở xong (`prep.busy`)
- `content.js` `check` lấy "ảnh mới nhất", không biết ảnh của prompt nào

Thay đổi:
- **A1. `content.js`: ghép ảnh với task.**
  - Điều tra thật bằng DevTools xem thẻ "%" hoặc thẻ ảnh gắn với prompt bằng cách nào.
  - Sau khi gửi task A, gắn `data-autovideo-task=A` vào thẻ "%" mới xuất hiện và theo dõi đúng thẻ đó.
  - Thêm message `submitted{taskId}` và `checkTask{taskId}`.
  - Nếu không chắc thẻ nào là của task → trả `ambiguous`, không đoán; task đó chạy lại kiểu 1 prompt/lần.
- **A2. `background.js`.**
  - Thay `busy` bằng `inflight` Map cộng khoá `typing`.
  - Nhận thêm task khi `inflight < max_inflight`; hai lần gửi cách nhau 4–8 giây.
  - Một vòng theo dõi riêng gọi `checkTask` mỗi 2,5 giây; task xong thì lấy ảnh và POST result.
  - Lỗi/reload: trả mọi task trong `inflight` về app (`/error`, retry; chỉ pause khi captcha).
  - `TASK_MAX_MS` tính riêng từng task. Popup hiện 2 task đang tạo. Tăng version.
- **A3. `app/flowbridge.py`, `app/api.py`, `config.yaml`.**
  - Thêm `flow.max_inflight: 2`; đặt 1 là hành vi cũ.
  - Một worker được giữ tối đa `max_inflight` task claimed.
  - Dòng ~34 (thu hồi task claimed của worker) chỉ chạy khi extension báo `fresh=1`.
  - Dashboard hiện 1/2, 2/2.
- Ràng buộc:
  - giữ x1 ảnh/prompt, 0 ảnh gán nhầm
  - captcha theo AGENTS.md 1.2
  - nếu Flow khoá nút gửi khi đang tạo → báo lại và giữ `max_inflight: 1`

## B. Chất lượng kịch bản/video
Đọc trước:
- `app/notebook.py` (`write_script`, `_brief`)
- `app/youtube.py`, `app/thumbnail.py`, `app/topic_scout.py`
- phần chia cảnh / vẽ whiteboard / render, `app/tts.py`

Lấy 2 video gần nhất trong `projects/` làm mốc "trước khi sửa".

- **B1. Hook.**
  - Đoạn mở đầu ≤ 75 chữ, theo thứ tự:
    1. câu hỏi/bí ẩn trung tâm, trùng với tiêu đề
    2. một dữ kiện kiểm chứng được, lấy từ fact sheet
    3. lời hứa cụ thể
  - Cấm chào hỏi, giới thiệu kênh, "Bạn có biết…".
  - Outline lưu `central_question`.
  - Hàm `check_hook`: 75 chữ đầu phải có câu hỏi và từ khoá của tiêu đề. Sai thì hỏi lại 1 lần chỉ phần mở đầu; vẫn sai thì ghi cảnh báo.
- **B2. Tiêu đề + thumbnail khớp lời hứa.**
  - Sinh tiêu đề và chữ thumbnail từ `central_question` và lời hứa của hook.
  - Kiểm tra:
    - từ khoá tiêu đề xuất hiện trong 30 giây đầu
    - lời hứa được trả lời trong kịch bản (lưu vị trí chương)
    - chữ thumbnail ≤ 5 từ, không phóng đại
  - Không khớp thì làm lại, tối đa 2 lần. Ghi `promise_check` vào `youtube.json`.
- **B3. Chủ đề hẹp theo series.**
  - Thêm `auto_topics.focus` (danh sách series, người dùng tự sửa, không tự bịa thêm).
  - ≥ 70% chủ đề tự thêm phải thuộc focus. Mỗi chủ đề có một câu hỏi cụ thể và gán `series`.
  - Tiêu đề và tag nhất quán trong series. UI hiện series và lọc được theo series.
- **B4. Cảnh bám lời, không đứng yên.**
  - Thêm `scene.max_sec: 7`.
  - Cảnh dài hơn thì tách thành nhiều hình theo ý, hoặc thêm chuyển động: vẽ dần, zoom/pan, highlight.
  - Prompt ảnh lấy từ đúng câu đang đọc.
  - Dùng QC khớp hình–lời sẵn có; điểm thấp thì tạo lại.
  - Không để khung đứng im quá 2 giây. Giữ `visual_rules`.
- **B5. Nhấn nhá giọng đọc.**
  - Đánh dấu các điểm: chuyển chương, câu hỏi, tiết lộ, con số quan trọng.
  - Nghỉ 0,6–0,9 giây ở các điểm đó; tách câu dài hơn 25 chữ.
  - Nếu engine hỗ trợ: đổi tốc độ theo đoạn. Không hỗ trợ thì chèn khoảng lặng khi ghép audio.
  - Giữ giọng ZeroTTS "baotrang". Nhạc nền nhấn nhẹ ở điểm chuyển.

## C. Thumbnail: người dùng nhập/sửa ngày tháng
- **C1. Dữ liệu.**
  - Lưu `thumb_date {day, month, year?, text, show}` theo job, trong `youtube.json` và DB.
  - Chủ đề news: mặc định lấy ngày sự kiện từ nguồn. Evergreen: mặc định `show=false`.
  - Thêm `thumbnail.date_format` và `date_position` vào config.
  - Ngày không hợp lệ → trả 400.
- **C2. Vẽ.** Dùng Pillow vẽ nhãn ngày, không đè chữ tiêu đề hay mặt người. Áp cho `thumbnail.jpg` và các biến thể A1..B3. Giữ 1280x720, dưới 2 MB.
- **C3. API.**
  - `GET /jobs/<id>/thumb`
  - `POST /jobs/<id>/thumb` (ngày, `show`, `title_text`): chỉ vẽ lại thumbnail, trả URL có `?v=` để không dính cache.
- **C4. UI.**
  - Nút "🖼 Sửa thumbnail" mở khung: xem trước, ô Ngày/Tháng/Năm, chữ hiển thị, checkbox "Hiện ngày", chữ tiêu đề trên thumb, nút "Lưu & vẽ lại", ảnh nhỏ các biến thể.
  - Đúng màu theme retro và hacker.
  - Chế độ Auto vẫn tự làm thumbnail, không chờ người dùng nhập.
- **C5.** `/projects/audit_missing` không ghi đè `thumb_date` người dùng đã nhập.

## Kiểm thử (thật)
- **A:**
  1. `node --check`, `py_compile`, manifest hợp lệ.
  2. Một profile chạy job ≥ 6 cảnh: thấy 2 task cùng đang tạo; so thời gian/ảnh với `max_inflight: 1`.
  3. ≥ 10 cặp task: 0 ảnh đổi chỗ (dùng QC khớp hình–lời).
  4. Task B xong trước A vẫn ra đúng cảnh.
  5. F5 giữa chừng: cả 2 task về hàng đợi rồi làm lại đúng.
  6. Captcha thì pause đúng.
  7. 6 profile chạy 30 phút: không task nào treo claimed quá 10 phút; ghi số captcha/giờ trước và sau.
- **B (2 job mới, so với 2 video cũ):**
  1. 75 chữ đầu và `central_question`; `check_hook` ĐẠT.
  2. `promise_check` ĐẠT.
  3. Top 10 có ≥ 7 chủ đề thuộc focus.
  4. Bảng thời lượng cảnh trước/sau, không cảnh nào đứng yên quá 8 giây; điểm QC trung bình trước/sau.
  5. 30 giây audio đầu trước/sau, kèm vị trí các khoảng lặng.
  6. `tests/test_units.py` và `tests/test_youtube.py` chạy.
- **C:**
  1. Job news có ngày đúng nguồn; evergreen không có ngày.
  2. Sửa thành 15/08: `thumbnail.jpg` và A1..B3 đổi, chỉ vẽ lại thumbnail, 1280x720, dưới 2 MB.
  3. Nhập 31/02 bị báo lỗi.
  4. Tắt "Hiện ngày" thì mất nhãn.
  5. Ảnh chụp khung sửa ở theme retro và hacker.
