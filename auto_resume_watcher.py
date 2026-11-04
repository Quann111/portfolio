import time
import requests

SERVER_URL = "http://127.0.0.1:8765"
INTERVAL_SEC = 20
BACKOFF_SEC = 60

print(f"[*] Automation Auto-Resume đang chạy... (chu kỳ {INTERVAL_SEC}s)")
print("[*] Nhấn Ctrl+C để dừng.")

while True:
    try:
        res = requests.get(f"{SERVER_URL}/flow/status", timeout=4)
        if res.status_code == 200:
            data = res.json()
            workers = data.get("workers", [])
            for w in workers:
                wid = w.get("id")
                wname = w.get("name")
                paused = w.get("paused")
                if paused:
                    p_str = str(paused).lower()
                    # AGENTS.md 1.2: CẤM tự resume profile đang pause vì captcha/robot
                    if "captcha" in p_str or "robot" in p_str or "unusual activity" in p_str:
                        continue
                    r = requests.post(f"{SERVER_URL}/flow/resume", json={"w": wid, "auto": True}, timeout=4)
                    if r.status_code == 200 and wid in (r.json().get("kept_captcha") or []):
                        pass
                    elif r.status_code == 200:
                        print(f"[+] Đã tự động tiếp tục profile: {wname} ({wid}) - Lý do pause trước đó: {paused}")
        time.sleep(INTERVAL_SEC)
    except Exception as e:
        # Lỗi kết nối: in 1 dòng ngắn gọn và lùi nhịp 60s
        err_msg = type(e).__name__
        if "ConnectionRefused" in str(e) or "actively refused" in str(e):
            err_msg = "Server AutoVideo (127.0.0.1:8765) chưa mở hoặc đang khởi động lại"
        elif "Read timed out" in str(e):
            err_msg = "Server 127.0.0.1:8765 phản hồi chậm (Read timed out)"
        else:
            err_msg = f"{type(e).__name__}: {e}"
        print(f"[-] [AutoResume] {err_msg} -> thử lại sau {BACKOFF_SEC}s")
        time.sleep(BACKOFF_SEC)
