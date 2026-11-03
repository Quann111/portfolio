# Plan cho Gemini: NotebookLM chạy song song nhiều tab (vd 5) + ghi nhớ job/task từng tab

## Bối cảnh
Hiện NotebookLM viết kịch bản **tuần tự từng câu hỏi**:
- `app/notebook.py:10` dùng một `_ASK_LOCK` chung → mọi `ask()` của mọi job xếp hàng một.
- Extension `chrome_ext_notebook/background.js` có một biến `busy` duy nhất, một `nbTab()` lấy tab đầu tiên, và `tick()` chỉ claim 1 task mỗi lượt.
- `nb_tasks` (`app/db.py:45`) không biết task chạy ở tab nào.
- Một kịch bản = dàn ý + N chương + "more", nên mỗi job mất nhiều phút. Hàng đợi bị nghẽn ở bước script.
- Các câu `chapter_i` dựa vào ngữ cảnh chat trước đó trong cùng tab. Vì vậy **một job phải ở nguyên một tab từ đầu đến cuối** (gắn job ↔ tab).
- `script.new_notebook: false` → mọi job đang dùng **chung một sổ**. Nếu chạy song song nhiều job trên cùng một sổ thì nguồn và chat sẽ lẫn nhau. Vì vậy **mỗi tab phải mở một sổ riêng**.

Mục tiêu:
- Profile VR mở **N tab NotebookLM** (mặc định 5, chỉnh trong config), mỗi tab là một "slot" làm kịch bản cho một job, các slot chạy **cùng lúc**.
- Hệ thống **ghi nhớ bền vững** slot nào đang làm job nào, task nào. Tắt/mở lại app hoặc Chrome thì làm tiếp đúng chỗ, không hỏi lại câu đã có kết quả.

## Sửa trước: hoàn tác thay đổi sai của Gemini
Gemini vừa sửa extension để "profile nào cài extension notebook cũng tự là VR". Thay đổi này **phá đúng yêu cầu chỉ VR được nhận task**: qqkun2 cài extension thì sẽ tự xưng là VR. Phải hoàn tác:
- `profileName` rỗng thì gửi rỗng, server từ chối và dashboard nhắc đặt tên.
- Muốn đỡ phải gõ tay thì cho popup gợi ý sẵn chữ "VR", nhưng người dùng phải bấm Lưu.

## Thay đổi

### 1. `config.yaml` → `script:`
```yaml
  nb_slots: 5                      # số tab NotebookLM chạy song song trên profile VR
  nb_notebooks:                    # mỗi slot một sổ riêng (URL sổ đã tạo sẵn, có nguồn chung của kênh)
    - "https://notebooklm.google.com/notebook/<id1>"
    # ... đủ nb_slots dòng; người dùng tự tạo sổ và dán URL. Thiếu thì slot đó báo "chưa có sổ"
  nb_stagger_sec: 20               # các slot gửi câu hỏi cách nhau ít nhất N giây (tránh bị NotebookLM giới hạn)
```
**Không bịa URL**: để placeholder và ghi TODO trong báo cáo.

### 2. DB (`app/db.py`, migrate kiểu `ALTER TABLE … ADD COLUMN` nếu chưa có, theo pattern migrate sẵn trong file)
- `nb_tasks`: thêm `slot INTEGER` và `prompt_hash TEXT`.
- Bảng mới:
  ```sql
  nb_slots(slot INTEGER PRIMARY KEY, notebook_url TEXT, tab_id INTEGER,
           job_id INTEGER, task_id INTEGER, state TEXT, step TEXT, updated_at REAL)
  ```
  `state` là một trong: `idle | busy | paused | offline | no_notebook`. Đây là "trí nhớ" slot ↔ job ↔ task.

### 3. `app/notebook.py`
- Bỏ `_ASK_LOCK` toàn cục; thay bằng **lock theo job** (`dict[jid, Lock]`). Các câu của một job vẫn tuần tự, các job khác nhau chạy song song.
- **Gắn job ↔ slot**:
  - `bind(jid) -> slot`: nếu job đã có slot trong `nb_slots` thì dùng lại; không thì lấy slot `idle` có sổ. Hết slot thì chờ, báo `pipeline.add_wait(jid, "script", …, "chờ slot NotebookLM rảnh")`.
  - `release(jid)`: gọi khi `write_script` xong, khi job lỗi/huỷ (`cancel_job`), và trong `_handle_error` của worker.
- `enqueue(...)` ghi `slot` của job. `claim_next(slot)` chỉ trả task có `slot = ?`; không còn claim chung.
- **Không hỏi lại câu đã có kết quả**: trong `ask()`, trước khi enqueue, tìm `nb_tasks` có cùng `job_id, step, prompt_hash` và `status='done'`. Có thì trả `result` luôn. Cache sẵn `notebook_script.json` vẫn giữ.
- Task `claimed` quá `CLAIM_TIMEOUT` thì trả về `queued` **trong cùng slot** (giữ ngữ cảnh chat).
- `paused()`: captcha/giới hạn ở một tab thì chỉ pause slot đó (`nb_slots.state='paused'`). Pause toàn cục chỉ khi lỗi đăng nhập/tài khoản.
- `connected()` = có ít nhất 1 slot không offline.
- `summary()` trả danh sách slot cho dashboard.

### 4. Worker: cho nhiều job viết kịch bản cùng lúc (`app/worker.py`)
- Hiện `_prep_loop` tối đa `min(prefetch, 2)` luồng và chạy tới `render`.
- Thêm `worker.script_ahead: 4`: một nhóm luồng riêng **chỉ chạy research → script** (`stop_at="tts"`) cho các job `queued` kế tiếp, tối đa `nb_slots - 1` job cùng lúc. Một slot để dành cho job chính.
- Các bước sau (TTS, ảnh, render, encode, package) **giữ nguyên tuần tự từng job** như yêu cầu "làm xong từng job rồi mới sang job sau". Chỉ phần viết kịch bản được làm trước.
- Job đang ở trạng thái script-ahead dùng status `preparing` như `_prep_loop`. Xong thì trả về `queued` ở stage `tts`. Lỗi thì xử lý như `_prep_once`.
- Không để job nào rơi vào `review` ngoài cơ chế sẵn có (chế độ Auto).

### 5. Extension `chrome_ext_notebook` (chỉ chạy khi `profileName` là VR)
- `background.js` quản lý **pool slot**:
  - `chrome.storage.local.slots = {slot: {tabId, url}}` để nhớ qua lần restart Chrome.
  - Khi khởi động/tick: lấy `nb_notebooks` + `nb_slots` từ endpoint mới `/nb/config`. Slot nào chưa có tab hoặc tab đã đóng thì `chrome.tabs.create({url, active:false})`.
  - Dùng chung **một cửa sổ riêng** cho các tab slot (`chrome.windows.create` lần đầu, lưu `windowId`).
- `busy` theo slot (`Map`). `tick()` duyệt mọi slot rảnh và gọi `/nb/next?slot=k&tab=…&name=…`, chạy **song song** (không await nối tiếp). Giữa hai lần gửi câu hỏi (kể cả khác slot) cách nhau ít nhất `nb_stagger_sec`.
- CDP (`chrome.debugger`) gắn theo từng `tabId`, đã hỗ trợ (`attached` là Set).
  - **Kiểm tra thật**: tab nền có nhận gõ phím/đọc câu trả lời đúng không (Chrome có thể throttle tab nền).
  - Nếu không ổn thì mỗi slot một cửa sổ nhỏ riêng thay vì tab, và ghi kết quả vào báo cáo.
- Kết quả/lỗi gửi kèm `slot`. Heartbeat gửi trạng thái từng slot.
- Popup hiện bảng slot: slot · sổ · job · bước · thời gian.

### 6. API (`app/api.py`)
- `/nb/next`: thêm `slot`. Kiểm tra `name == notebook_profile` như hiện tại. Cập nhật `nb_slots` (`tab_id`, `state`, `updated_at`). Trả task của slot đó.
- `GET /nb/config` → `{slots, notebooks, stagger}`.
- `/nb/tasks/<id>/result|error`: cập nhật `nb_slots.task_id/state`.
- Dashboard (`_browsers()` / `app.js`):
  - thay dòng "NotebookLM" bằng N dòng "NotebookLM #k", mỗi dòng ghi job, bước, thời gian, trạng thái
  - thêm nút "Giải phóng slot" (release thủ công khi kẹt)

## Không làm
- Không đổi prompt viết kịch bản, không đổi bước sau script.
- Không dùng profile khác ngoài VR. Không tạo sổ mới tự động (`new_notebook` vẫn false); sổ do người dùng tạo sẵn.

## Rủi ro cần báo trong kết quả
- Một tài khoản Google gửi 5 câu cùng lúc có thể bị NotebookLM giới hạn hoặc captcha. Bắt đầu thử với `nb_slots: 2`, rồi 3, rồi 5. Ghi lại ngưỡng thực tế.
- Tab nền bị throttle (xem mục 5).

## Kiểm thử (thật, ghi ĐẠT / KHÔNG ĐẠT / CHƯA KIỂM)
1. `py_compile` các file Python đã sửa; `manifest.json` hợp lệ; migrate DB chạy trên `autovideo.db` thật (backup trước).
2. `nb_slots: 2`, 2 sổ thật, 3 job queued:
   - 2 job viết kịch bản **cùng lúc** ở 2 tab (log có thời gian chồng nhau)
   - job thứ 3 chờ slot
   - mỗi job ở nguyên một slot từ outline đến hết các chương
3. **Ghi nhớ**: đang viết dở chương 3 thì tắt app rồi mở lại → job tiếp tục **cùng slot**, các bước đã `done` không gửi lại (đếm `nb_tasks` theo job/step).
4. Tắt Chrome rồi mở lại → extension mở lại đúng tab/sổ theo slot, làm tiếp.
5. Captcha/lỗi một tab → chỉ slot đó pause, slot khác chạy tiếp.
6. qqkun2 có extension, tên trống hoặc khác VR → bị từ chối.
7. Lên `nb_slots: 5` → ghi lại có bị giới hạn không.
8. Pipeline sau script vẫn tuần tự: chỉ một job ở TTS/ảnh/render tại một thời điểm.
9. Dashboard hiện bảng slot đúng (chụp màn hình).
