"""Kiểm tra 3 extension AutoVideo (Flow, NotebookLM, Gemini) đã được cài (Load unpacked) trong từng profile Chrome chưa.

Chrome 137+ bỏ qua --load-extension của bản Chrome thường → mỗi profile phải "Load unpacked" một lần; Chrome nhớ
extension unpacked qua các lần mở. Script chỉ ĐỌC file Preferences, không sửa gì.

  python check_extensions.py "Default" "Profile 1" ...   → in JSON {profile: [extension còn thiếu]}
  python check_extensions.py --missing-only ...           → chỉ in tên profile còn thiếu (mỗi dòng 1 profile)
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXTS = ("chrome_ext", "chrome_ext_notebook", "chrome_ext_gemini")
USER_DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p or ""))


def installed_paths(profile: str) -> set[str]:
    paths = set()
    for name in ("Secure Preferences", "Preferences"):
        f = USER_DATA / profile / name
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for info in ((data.get("extensions") or {}).get("settings") or {}).values():
            if isinstance(info, dict) and info.get("path") and info.get("state", 1) != 0:
                paths.add(_norm(info["path"]))
    return paths


def missing(profile: str) -> list[str]:
    have = installed_paths(profile)
    return [e for e in EXTS if _norm(str(HERE / e)) not in have]


def check(profiles) -> dict:
    return {p: missing(p) for p in profiles}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    res = check(args or ["Default"])
    if "--missing-only" in sys.argv:
        for p, m in res.items():
            if m:
                print(p)
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
