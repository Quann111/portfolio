# Plan cho Gemini: NotebookLM chỉ chạy trên profile VR + bỏ profile QUAN khỏi "Bật/Đóng tất cả"

## Bối cảnh
1. **NotebookLM đang chạy ở 2 profile.** `Open_Chrome_Profiles.bat` mở tab NotebookLM ở cả **VR (Profile 3)** và **qqkun2 (Profile 33)**, và extension `chrome_ext_notebook` được nạp vào mọi profile. Extension không gửi danh tính profile (`/nb/next` chỉ gửi `tab, v, en`), nên profile nào có tab NotebookLM cũng có thể nhận task. Kết quả là hai sổ khác nhau trả lời lẫn lộn. Yêu cầu: **chỉ VR được dùng NotebookLM.**
2. **Bỏ profile QUAN (`Default`, profile cá nhân của người dùng) khỏi "Bật tất cả" và "Đóng tất cả".**
   - Bật: `/browsers/open_all` (`app/api.py:671`) chạy `autovideo/Open_Chrome_Profiles.bat`; bước [1/9] mở QUAN.
   - Đóng: `/browsers/close_all` (`app/api.py:680`) chạy `taskkill /F /IM chrome.exe /T`, giết **toàn bộ** Chrome, kể cả QUAN.
   - Lưu ý kỹ thuật: mọi profile dùng chung một user-data-dir, nên Chrome chạy **một tiến trình browser duy nhất** cho tất cả profile. Không thể taskkill riêng từng profile. Muốn đóng chọn lọc thì phải để **extension trong từng profile tự đóng cửa sổ của nó**.
3. Có **2 bản `Open_Chrome_Profiles.bat` khác nhau**: `D:\AI\truyen\` và `D:\AI\truyen\autovideo\`. API dùng bản trong `autovideo\` (`ROOT` = `autovideo`).

## Thay đổi

### A. NotebookLM chỉ dùng profile VR
1. **`config.yaml`**:
   ```yaml
   notebook_profile: "VR"     # chỉ profile có tên này được nhận task NotebookLM
   ```
2. **`chrome_ext_notebook`**:
   - Thêm ô "Tên profile" trong `popup.html`/`popup.js`, lưu `chrome.storage.local.profileName`. Làm giống `chrome_ext` (Flow) đang có `s.profileName`, xem `chrome_ext/background.js:381`.
   - `background.js`: gửi thêm `&name=<profileName>` khi gọi `/nb/next` và `/nb/ping`.
   - Tăng `version` trong `manifest.json`, để dashboard báo "outdated" ở profile chưa reload extension.
3. **`app/api.py` `/nb/next`**:
   - Đọc `name`. Nếu `name.strip().lower() != cfg.notebook_profile.lower()` thì trả `{"task": None, "paused": None, "reject": "not_vr"}`: **không** claim task và **không** ghi `notebook.heartbeat`, để `notebook.connected()` chỉ phản ánh VR.
   - Profile bị từ chối thì ghi một lần vào kv `nb_rejected = {name: ts}` để dashboard hiện cảnh báo "Profile X đang mở NotebookLM nhưng không phải VR".
4. **`.bat` (cả 2 bản)**: bước Profile 33 (qqkun2) bỏ `"%NOTEBOOK_URL%"`. Gemini URL thì giữ hay bỏ: **hỏi người dùng nếu không rõ**, mặc định giữ.
5. **Dashboard** (`_browsers()` ở `api.py:116`): dòng NotebookLM hiện thêm "Profile: VR".

### B. Bỏ QUAN khỏi "Bật tất cả"
1. **Cả 2 bản `.bat`**:
   - Xoá bước [1/9] `--profile-directory="Default"`; đánh lại số thứ tự thành [1/8]…[8/8].
   - Gộp 2 bản về một nội dung: bản ở `D:\AI\truyen\` có thể chỉ gọi `call "%~dp0autovideo\Open_Chrome_Profiles.bat"`, để khỏi lệch nhau lần nữa.
2. **`api.py`**: sửa chữ "9 profile" thành "8 profile" (`:672`, `:677`).
3. Ghi chú trong bat: nếu QUAN đang mở trước, các profile khác sẽ chạy chung tiến trình với QUAN và cờ `--load-extension` có thể bị bỏ qua. Khi đó extension phải được cài sẵn trong từng profile (đã "Load unpacked"). Kiểm tra thực tế và ghi kết quả vào báo cáo.

### C. "Đóng tất cả" trừ QUAN
1. **`config.yaml`**:
   ```yaml
   browsers:
     keep_open: ["QUAN"]     # tên profile KHÔNG bị đóng bởi "Đóng tất cả"
   ```
2. **`api.py` `/browsers/close_all`**:
   - Bỏ `taskkill /F /IM chrome.exe /T`.
   - Ghi lệnh `close` vào kv `action_req` cho **mọi worker** Flow đang biết (`flowbridge._workers()`), trừ worker có `name` thuộc `keep_open`. Cùng ghi cho `notebooklm` và `gemini`. Dùng lại đúng cơ chế `action_req` + `/flow|nb|gemini/ping` sẵn có (`api.py:321-337`).
   - Giữ các lệnh reset pause (`flowbridge.set_paused(None)`…) như cũ.
   - Trả `{"ok": true, "closing": [danh sách tên], "kept": [...]}`.
   - **Tuỳ chọn khẩn cấp**: `{force: true}` vẫn chạy `taskkill` (đóng hết, kể cả QUAN). UI hỏi `confirm` trước khi dùng.
3. **3 extension** (`chrome_ext`, `chrome_ext_notebook`, `chrome_ext_gemini`): trong vòng ping, nếu `r.action === 'close'` thì gọi `chrome.windows.getAll()` → `chrome.windows.remove(id)` cho từng cửa sổ của profile đó. Mỗi profile là một extension instance riêng, nên chỉ đóng cửa sổ của chính profile đó. Thêm quyền nếu manifest thiếu (`windows` không cần quyền riêng).
   - Kiểm tra trong `chrome_ext_notebook`/`gemini`: lệnh `close` gửi cho `notebooklm` chỉ được VR thực hiện. Extension bỏ qua lệnh nếu `profileName` không khớp `name` trong phản hồi.
4. **UI** (`app/web/app.js:400-415`):
   - Nút "Đóng tất cả" hiện toast "Đang đóng N profile (giữ QUAN)".
   - Thêm lựa chọn phụ "Đóng cưỡng bức (cả QUAN)" có `confirm`.
5. **Tên profile trong extension phải đặt đúng**: VR, CANH, QUAN2, pana, trung, tung, QUANGBE, qqkun2. QUAN thì đặt "QUAN" nếu có cài extension. Viết hướng dẫn ngắn trong báo cáo.

## Không làm
- Không đổi sang user-data-dir riêng cho từng profile (sẽ mất đăng nhập).
- Không sửa luồng tạo ảnh Flow / viết kịch bản, ngoài phần ping/claim nêu trên.

## Kiểm thử (thật, ghi ĐẠT / KHÔNG ĐẠT / CHƯA KIỂM)
1. `py_compile app/api.py`. Kiểm tra JSON hợp lệ của 3 `manifest.json`.
2. Mở NotebookLM ở cả VR và qqkun2, xếp 1 task NotebookLM:
   - **chỉ VR** nhận (xem `nb_tasks.claimed` + log extension)
   - qqkun2 bị từ chối, dashboard hiện cảnh báo
3. Tắt VR → `notebook.connected()` = False sau ~90s, dù qqkun2 vẫn mở NotebookLM.
4. Mở QUAN bằng tay, bấm "Bật tất cả" → mở 8 profile, **không** mở thêm cửa sổ QUAN. Các extension vẫn hoạt động: dashboard thấy đủ worker.
5. Bấm "Đóng tất cả" → mọi cửa sổ profile automation đóng, **cửa sổ QUAN còn nguyên**. Chụp màn hình / liệt kê cửa sổ trước/sau.
6. "Đóng cưỡng bức" → đóng hết sau khi confirm.
7. Hai file `.bat` cho kết quả giống nhau.
