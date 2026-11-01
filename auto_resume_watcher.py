import time
import requests

SERVER_URL = "http://127.0.0.1:8765"
INTERVAL_SEC = 20

print(f"[*] Automation Auto-Resume dang chay... (Lap lai moi {INTERVAL_SEC}s)")
print("[*] Nhan Ctrl+C de dung.")

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
                    # Tu dong go pause cho profile nay
                    # auto=True: captcha duoc server tu go sau browser.captcha_auto_resume_sec (mac dinh 180s)
                    r = requests.post(f"{SERVER_URL}/flow/resume", json={"w": wid, "auto": True}, timeout=4)
                    if r.status_code == 200 and wid in (r.json().get("kept_captcha") or []):
                        print(f"[!] Captcha: cho het thoi gian cho roi tu go: {wname}")
                    elif r.status_code == 200:
                        print(f"[+] Da tu dong tiep tuc profile: {wname} ({wid}) - Ly do bi pause truoc do: {paused}")
    except Exception as e:
        print(f"[-] Loi ket noi server: {e}")
    time.sleep(INTERVAL_SEC)
