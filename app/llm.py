import base64
import json
import re
import subprocess
import threading
import time

import requests

from . import db
from .config import Cfg

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _lms(*args, timeout=600):
    return subprocess.run(["lms", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=NO_WINDOW)


def _bound_arrays(s, cap=60):
    """Mảng không có maxItems → đặt trần, tránh model (MoE) lặp liệt kê tới khi bị cắt output."""
    if isinstance(s, dict):
        if s.get("type") == "array" and "maxItems" not in s:
            s["maxItems"] = cap
        for v in s.values():
            _bound_arrays(v, cap)
    elif isinstance(s, list):
        for v in s:
            _bound_arrays(v, cap)
    return s


# ------------------------------------------------------------------ khe model (bảng điều khiển)
# Tối đa 3 khe chạy song song, mỗi khe 1 model trên GPU hoặc CPU; bật/tắt tuỳ ý, luôn còn ít nhất 1 khe.
MAX_SLOTS = 3
ONLY_MODEL = "qwen/qwen3-vl-4b"    # 28/10: chỉ dùng 4B cho mọi khe, bỏ 8B
_LOCK = threading.RLock()          # nạp/gỡ model: mỗi lúc một luồng gọi lms
_OK = {}                           # {(model, các khe bật): lúc kiểm tra} – khỏi gọi `lms ps` mỗi request


def slots(c) -> list[dict]:
    v = db.kv_get("llm_slots")
    if not v:
        v = [{"on": True, "model": (c.get("models") or {}).get("text") or c.model, "device": "gpu"}]
    v = (list(v) + [{"on": False, "model": "", "device": "gpu"}] * MAX_SLOTS)[:MAX_SLOTS]
    if not any(x.get("on") and x.get("model") for x in v):
        v[0] = {**v[0], "on": True}
    return [{**x, "model": ONLY_MODEL} for x in v]


def active(c) -> list[int]:
    return [i for i, x in enumerate(slots(c)) if x.get("on") and x.get("model")]


def set_slot(c, i: int, **kw) -> str | None:
    v = slots(c)
    new = {**v[i], **{k: kw[k] for k in ("on", "device") if k in kw}, "model": ONLY_MODEL}
    if new.get("device") not in ("gpu", "cpu"):
        new["device"] = "gpu"
    if new["on"] and not new.get("model"):
        return "Chọn model trước khi bật khe"
    v[i] = new
    if not any(x.get("on") and x.get("model") for x in v):
        return "Phải giữ ít nhất 1 khe model đang bật"
    db.kv_set("llm_slots", v)
    return None


def ident(c, i: int) -> str:
    """Tên nạp trong LM Studio: khe 1 GPU giữ đúng tên model (tương thích chỗ khác), khe khác thêm hậu tố."""
    x = slots(c)[i]
    return x["model"] if i == 0 and x["device"] == "gpu" else f"{x['model']}@slot{i + 1}-{x['device']}"


def _is_vl(model: str) -> bool:
    return bool(re.search(r"(^|[-_/])vl|vision|llava|gemma-3", model, re.I))


def loaded() -> list[dict]:
    try:
        return json.loads(_lms("ps", "--json", timeout=30).stdout or "[]")
    except (ValueError, subprocess.SubprocessError):
        return []


class LLM:
    def __init__(self, cfg: Cfg, log):
        self.c = cfg.lmstudio
        self.log = log
        # vai trò quyết định model: text (viết/chia cảnh – model chữ lớn), vision (nhìn ảnh), light (việc vặt)
        self.role = "vision"
        self.slot = None            # khe được giao (None = tự chọn khe đang bật)

    def _pick(self) -> int:
        act = active(self.c)
        if self.slot in act:
            return self.slot
        sl = slots(self.c)
        order = sorted(act, key=lambda i: sl[i]["device"] != "gpu")          # GPU trước, CPU sau
        if self.role == "vision":
            vl = [i for i in order if _is_vl(sl[i]["model"])]
            order = vl or order
        return order[0]

    @property
    def model(self) -> str:
        return ident(self.c, self._pick())

    def server_up(self) -> bool:
        try:
            return requests.get(f"{self.c.base_url}/models", timeout=5).ok
        except requests.RequestException:
            return False

    def ensure(self):
        if not self.server_up():
            self.log("Khởi động LM Studio server...")
            _lms("server", "start")
            for _ in range(60):
                if self.server_up():
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Không bật được LM Studio server (lms server start)")
        with _LOCK:
            i = self._pick()
            m, x = ident(self.c, i), slots(self.c)[i]
            want = {ident(self.c, k) for k in active(self.c)}
            key = (m, tuple(sorted(want)))
            if time.time() - _OK.get(key, 0) < 15:
                return
            info = {p.get("identifier"): p for p in loaded()}
            cur = info.get(m)
            ctx, par = int(self.c.context_length), int(self.c.get("parallel", 1))
            if cur and (cur.get("contextLength"), cur.get("parallel")) != (ctx, par):
                # LM Studio tự nạp (JIT) với context mặc định 262k → KV tràn sang CPU, chỉ ~5 tok/s → nạp lại
                self.log(f"Nạp lại {m}: context {cur.get('contextLength')}/parallel {cur.get('parallel')} "
                         f"≠ cấu hình {ctx}/{par}")
                _lms("unload", m)
                info.pop(m)
            ps = set(info)
            for k in ps - want:
                # model không thuộc khe nào đang bật (khe vừa tắt / model cũ) → gỡ, giải phóng VRAM/RAM
                if k:
                    _lms("unload", k)
            if m in ps:
                _OK[key] = time.time()
                return
            self.log(f"Load model {x['model']} (khe {i + 1}, {x['device'].upper()})...")
            t0 = time.time()
            r = _lms("load", x["model"], "-y", "-c", str(self.c.context_length),
                     "--gpu", "off" if x["device"] == "cpu" else str(self.c.get("gpu", "max")),
                     "--parallel", str(self.c.get("parallel", 1)), "--identifier", m)
            if r.returncode != 0:
                err = (r.stderr or r.stdout)[-300:]
                if len(active(self.c)) > 1:
                    # thiếu VRAM/RAM… → tắt khe này, các khe còn lại chạy tiếp
                    set_slot(self.c, i, on=False)
                    self.log(f"  khe {i + 1} nạp lỗi → tự tắt khe này: {err}")
                    self.slot = None
                    return self.ensure()
                raise RuntimeError(f"lms load lỗi: {err}")
            self.log(f"  đã load sau {time.time() - t0:.0f}s")
            _OK[key] = time.time()

    def unload(self):
        self.log("Unload LLM để giải phóng VRAM")
        with _LOCK:
            for k in active(self.c):
                _lms("unload", ident(self.c, k))
            _OK.clear()

    def chat(self, messages, schema=None, temperature=0.7, max_tokens=8000):
        self.ensure()
        body = {"model": self.model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        if schema:
            schema = _bound_arrays(json.loads(json.dumps(schema)))
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "out", "strict": True, "schema": schema}}
        last = None
        # output tối đa phải chừa chỗ cho prompt trong context (prompt ước ~ 1 token / 3 ký tự)
        # ảnh base64 không tính theo ký tự (làm cap rơi về 2000 → JSON QC ảnh bị cắt): mỗi ảnh ~1000 token
        def _tok(c):
            if isinstance(c, list):
                return sum(1000 if p.get("type") == "image_url" else _tok(p.get("text", "")) for p in c)
            return len(json.dumps(c, ensure_ascii=False)) // 3
        prompt_tok = sum(_tok(m.get("content")) for m in messages)
        cap = max(2000, int(self.c.get("context_length", 32768) * 0.95) - prompt_tok)
        body["max_tokens"] = min(body["max_tokens"], cap)
        for attempt in range(4):
            try:
                self.ensure()
                body["model"] = self.model          # khe có thể vừa bị tắt/đổi → lấy lại tên
                t0 = time.time()
                r = requests.post(f"{self.c.base_url}/chat/completions", json=body, timeout=1800)
                if not r.ok:
                    self.log(f"LLM {r.status_code}: {r.text[:300]}")
                r.raise_for_status()
                dt = time.time() - t0
                if dt > 60:
                    u = r.json().get("usage") or {}
                    out = u.get("completion_tokens") or 0
                    self.log(f"LLM chậm: {dt:.0f}s, prompt {u.get('prompt_tokens')} tok, "
                             f"output {out} tok ({out / dt:.1f} tok/s)")
                choice = r.json()["choices"][0]
                text = choice["message"]["content"]
                text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
                if not schema:
                    return text
                if choice.get("finish_reason") == "length":
                    raise ValueError(f"output bị cắt ở {body['max_tokens']} token")
                m = re.search(r"\{.*\}", text, re.S)
                return json.loads(m.group(0) if m else text)
            except (requests.RequestException, ValueError, KeyError) as e:
                last = e
                self.log(f"LLM lỗi (lần {attempt + 1}): {e}")
                if isinstance(e, ValueError):
                    # JSON bị cắt/hỏng: tăng nhẹ max_tokens nhưng khống chế trần an toàn (không vượt quá 8192 hoặc cap)
                    max_safe_limit = min(cap, 8192)
                    body["max_tokens"] = min(max_safe_limit, max(body["max_tokens"], int(body["max_tokens"] * 1.3)))
                    body["temperature"] = max(0.1, body["temperature"] - 0.2)
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"LLM thất bại: {last}")

    def look(self, images, prompt, schema):
        if isinstance(images, (str, bytes)) or hasattr(images, "suffix"):
            images = [images]
        content = [{"type": "text", "text": prompt}]
        for im in images:
            b64, mime = _small_image(im, int(self.c.get("vision_max_side", 768)))
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        # 900 token có lần bị cắt (nhận xét QC dài) → 1300, vẫn nhanh hơn nhiều so với 2000 cũ
        return self.chat([{"role": "user", "content": content}], schema, temperature=0.1, max_tokens=1300)


def _small_image(im, max_side: int):
    """QC nhanh: ảnh 1376×774 PNG → JPEG cạnh dài ≤ max_side (ít token hình hơn nhiều, đủ để chấm ý chính)."""
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.fromfile(str(im), np.uint8), cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        s = min(1.0, max_side / max(h, w))
        if s < 1.0:
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        return base64.b64encode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()).decode(), \
            "image/jpeg"
    except Exception:
        return base64.b64encode(open(im, "rb").read()).decode(), "image/png"
