import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import mimetypes
import time

from . import llm as llm_mod
from .llm import LLM
from . import autoflow, db, events, flowbridge, gemini_bridge, notebook, packager, pipeline, resources, styles, voices, youtube
from .config import ROOT, load

WEB = ROOT / "app" / "web"


def _project(j):
    return {k: j[k] for k in ("id", "topic", "status", "stage", "output", "error")}


def _project_full(j):
    t = pipeline.view(j["id"])
    sc = json.loads(j["scores"]).get("total") if j.get("scores") else None
    f = packager.folder_of(j["id"])
    cfg = load()
    dur = j.get("target_minutes") or db.kv_get("target_minutes_default") or cfg.target_minutes
    return {**_project(j), "score": sc, "active_sec": t["active_sec"], "wall_sec": t["wall_sec"],
            "folder": str(f), "packaged": f.exists(), "style": styles.of_job(j["id"]),
            "voice": voices.of_job(j["id"], cfg),
            "target_minutes": dur,
            "yt": youtube.status(j["id"]).get("state")}


def _ext_version(folder: str) -> str:
    try:
        return json.loads((ROOT / folder / "manifest.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return ""


def _older(v: str, latest: str) -> bool:
    try:
        return tuple(map(int, (v or "0").split("."))) < tuple(map(int, latest.split(".")))
    except ValueError:
        return False


_lms_cache = {"t": 0, "v": None}


_models_cache = {"t": 0, "v": []}


def _llm_models() -> list[dict]:
    """Model đã tải trong LM Studio (lms ls), cache 8 giây → tải model mới là thấy ngay trên bảng điều khiển."""
    if time.time() - _models_cache["t"] < 8:
        return _models_cache["v"]
    try:
        r = llm_mod._lms("ls", "--json", timeout=30)
        v = [{"id": m["modelKey"], "vl": bool(m.get("vision")), "size": m.get("paramsString") or ""}
             for m in json.loads(r.stdout or "[]")
             if m.get("type") == "llm" and m["modelKey"] == llm_mod.ONLY_MODEL]
    except Exception:
        v = _models_cache["v"]
    _models_cache.update(t=time.time(), v=v)
    return v


def _llm_slots(cfg) -> list[dict]:
    loaded = {p.get("identifier") for p in llm_mod.loaded()}
    return [{**x, "i": i, "loaded": x.get("on") and llm_mod.ident(cfg.lmstudio, i) in loaded}
            for i, x in enumerate(llm_mod.slots(cfg.lmstudio))]


def _lmstudio(cfg) -> dict:
    """LM Studio server có chạy không + model đang nạp (cache 10 giây)."""
    if time.time() - _lms_cache["t"] < 10 and _lms_cache["v"]:
        return _lms_cache["v"]
    import requests
    try:
        r = requests.get(cfg.lmstudio.base_url.replace("/v1", "/api/v0") + "/models", timeout=2).json()
        loaded = [m["id"] for m in r.get("data", []) if m.get("state") == "loaded"]
        v = {"up": True, "loaded": loaded}
    except Exception:
        try:
            requests.get(cfg.lmstudio.base_url + "/models", timeout=2)
            v = {"up": True, "loaded": []}
        except Exception:
            v = {"up": False, "loaded": []}
    _lms_cache.update(t=time.time(), v=v)
    return v


def _browsers() -> dict:
    """Chrome profile đang dùng extension: trạng thái, việc đang làm, số ảnh, tốc độ/giờ, lần gọi gần nhất."""
    now = time.time()
    rows = []
    flow_latest, nb_latest = _ext_version("chrome_ext"), _ext_version("chrome_ext_notebook")
    for k, v in (db.kv_get("flowbridge_workers") or {}).items():
        age = now - v.get("at", 0)
        cur = db.q("SELECT job_id, idx, claimed_at FROM flow_tasks WHERE worker=? AND status='claimed' "
                   "ORDER BY id DESC LIMIT 1", (k,))
        hour = db.q("SELECT COUNT(*) n FROM flow_tasks WHERE worker=? AND status='done' AND done_at>?",
                    (k, now - 3600))[0]["n"]
        paused = flowbridge.worker_paused(k)
        state = ("offline" if age > 360 else "paused" if paused else "busy" if cur else "idle")
        rows.append({"id": k, "name": v.get("name") or k[:6], "kind": "flow", "version": v.get("version"),
                     "state": state, "last_sec": round(age), "done": v.get("done", 0), "errors": v.get("errors", 0),
                     "outdated": _older(v.get("version"), flow_latest) and flow_latest, "latest": flow_latest,
                     "per_hour": hour, "paused": paused,
                     "doing": (f"Cảnh {cur[0]['idx'] + 1} – job {cur[0]['job_id']} "
                               f"({round(now - cur[0]['claimed_at'])}s)") if cur else ""})
    nb = db.kv_get("notebook_seen") or {}
    if nb:
        age = now - nb.get("at", 0)
        cur = db.q("SELECT step, job_id FROM nb_tasks WHERE status='claimed' ORDER BY id DESC LIMIT 1")
        rows.append({"id": "notebooklm", "name": "NotebookLM", "kind": "notebook", "version": nb.get("version"),
                     "state": ("offline" if age > 360 else "paused" if notebook.paused() else
                               "busy" if cur else "idle" if nb.get("enabled", True) else "off"),
                     "last_sec": round(age), "paused": notebook.paused(), "latest": nb_latest,
                     "outdated": _older(nb.get("version"), nb_latest) and nb_latest,
                     "doing": f"{cur[0]['step']} – job {cur[0]['job_id']}" if cur else ""})
    gem = db.kv_get("gemini_seen") or {}
    gem_latest = _ext_version("chrome_ext_gemini")
    if gem:
        age = now - gem.get("at", 0)
        cur = db.q("SELECT step, job_id FROM gemini_tasks WHERE status='claimed' ORDER BY id DESC LIMIT 1")
        rows.append({"id": "gemini", "name": "Gemini Web", "kind": "gemini", "version": gem.get("version"),
                     "state": ("offline" if age > 360 else "paused" if gemini_bridge.paused() else
                               "busy" if cur else "idle" if gem.get("enabled", True) else "off"),
                     "last_sec": round(age), "paused": gemini_bridge.paused(), "latest": gem_latest,
                     "outdated": _older(gem.get("version"), gem_latest) and gem_latest,
                     "doing": f"{cur[0]['step']} – job {cur[0]['job_id']}" if cur else ""})
    rows.sort(key=lambda r: (r["kind"] != "flow", r["state"] == "offline", r["name"]))
    fl = [r for r in rows if r["kind"] == "flow" and r["state"] != "offline"]
    return {"browsers": rows, "flow_online": len(fl), "flow_busy": sum(r["state"] == "busy" for r in fl),
            "per_hour": sum(r.get("per_hour", 0) for r in fl)}


def serve(port: int, worker, changed):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def _text(self, code, text, ctype="text/plain; charset=utf-8"):
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def _job(self, jid):
            r = db.q("SELECT * FROM jobs WHERE id=?", (jid,))
            return r[0] if r else None

        def _file(self, f: Path):
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            size = f.stat().st_size
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            if rng and rng.startswith("bytes="):          # cho phép tua video trong trình xem
                a, _, b = rng[6:].partition("-")
                start = int(a or 0)
                end = int(b) if b else size - 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            with f.open("rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def _sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = int(parse_qs(urlparse(self.path).query).get("last", ["0"])[0] or 0)
            for e in events.recent(n=200):              # gửi lại lịch sử gần đây khi mới mở trang
                if e["id"] > last:
                    self.wfile.write(f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode())
                    last = e["id"]
            try:
                while True:
                    batch = events.since(last, 15)
                    if not batch:
                        self.wfile.write(b": ping\n\n")
                    for e in batch:
                        self.wfile.write(f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode())
                        last = e["id"]
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def do_GET(self):
            u = urlparse(self.path)
            p = u.path.rstrip("/")
            cfg = load()
            # ---- dashboard web
            if p == "/llm":
                c = load()
                return self._send(200, {"models": _llm_models(), "slots": _llm_slots(c)})
            if p == "/voices":
                return self._send(200, {"options": voices.options(), "default": voices.default(cfg)})
            if p in ("", "/ui"):
                return self._file(WEB / "index.html")
            if p.startswith("/ui/"):
                f = (WEB / p[4:]).resolve()
                if WEB.resolve() in f.parents and f.is_file():
                    return self._file(f)
                return self._send(404, {"error": "not found"})
            if p.startswith("/files/"):                 # chỉ phục vụ file trong projects/ hoặc exchange/
                from urllib.parse import unquote
                rel = unquote(p[7:])
                for base in (packager.PROJECTS, ROOT / "exchange"):
                    f = (base / rel).resolve()
                    if base.resolve() in f.parents and f.is_file():
                        return self._file(f)
                return self._send(404, {"error": "không có file"})
            if p == "/test_image/status":
                tid = parse_qs(u.query).get("task_id", [None])[0]
                if not tid:
                    return self._send(400, {"error": "thiếu task_id"})
                row = db.q("SELECT id, status, image, error FROM flow_tasks WHERE id=?", (int(tid),))
                if not row:
                    return self._send(404, {"error": "không tìm thấy task"})
                t = row[0]
                img_url = None
                saved_path = None
                if t["status"] == "done" and t["image"]:
                    img_p = Path(t["image"])
                    if img_p.exists():
                        saved_path = str(img_p)
                        for base in (packager.PROJECTS, ROOT / "exchange"):     # đúng 2 gốc mà /files/ phục vụ
                            try:
                                rel = img_p.resolve().relative_to(base.resolve()).as_posix()
                                img_url = f"/files/{rel}"
                                break
                            except ValueError:
                                continue
                return self._send(200, {
                    "id": t["id"],
                    "status": t["status"],
                    "image_url": img_url,
                    "saved_path": saved_path,
                    "error": t["error"]
                })
            if p == "/events":
                return self._sse()
            if p == "/browsers":
                return self._send(200, _browsers())
            if p == "/status":
                br = _browsers()
                return self._send(200, {"running": worker.running, "mode": db.kv_get("mode") or cfg.get("mode"),
                                        "style": styles.default(cfg), "target_minutes": db.kv_get("target_minutes_default") or cfg.target_minutes,
                                        "lmstudio": _lmstudio(cfg), "flow_online": br["flow_online"],
                                        "flow_busy": br["flow_busy"],
                                        "notebook": next((b["state"] for b in br["browsers"] if b["kind"] == "notebook"),
                                                         "offline"),
                                        "gemini": next((b["state"] for b in br["browsers"] if b["kind"] == "gemini"),
                                                       "offline"),
                                        "stop_after": db.kv_get("stop_after"), "fatal": db.kv_get("fatal"),
                                        "res": resources.snapshot()})
            if p == "/stats/timing":
                done = [j for j in db.jobs() if j["status"] == "done"]
                out = []
                for j in done:
                    t = pipeline.view(j["id"])
                    out.append({"id": j["id"], "topic": j["topic"], "active_sec": t["active_sec"],
                                "wall_sec": t["wall_sec"], "steps": {s["key"]: s["active_sec"] for s in t["steps"]}})
                return self._send(200, out)
            if p == "/jobs":
                return self._send(200, {"running": worker.running, "jobs": [_project_full(j) for j in db.jobs()]})
            if p == "/flow/status":
                return self._send(200, flowbridge.summary())
            if p == "/flow/next":
                q = parse_qs(u.query)
                w = q.get("w", [""])[0]          # mỗi Chrome profile một id riêng
                flowbridge.heartbeat({"tab": q.get("tab", [""])[0], "version": q.get("v", [""])[0],
                                      "name": q.get("name", [""])[0]}, w)
                t = flowbridge.claim_next(w) if q.get("tab", ["1"])[0] != "0" else None
                st = flowbridge.summary()
                return self._send(200, {"task": t, "paused": flowbridge.paused() or flowbridge.worker_paused(w),
                                        "profiles": st["running"]})
            if p in ("/flow/ping", "/nb/ping"):
                # extension hỏi nhanh: dashboard có yêu cầu "đưa tab lên trước" hoặc "tự sửa lỗi/repair" không
                w = parse_qs(u.query).get("w", ["notebooklm"])[0] if p == "/flow/ping" else "notebooklm"
                req = db.kv_get("focus_req") or {}
                act_req = db.kv_get("action_req") or {}
                if p == "/flow/ping" and w in flowbridge._workers():
                    flowbridge._set_worker(w, at=time.time())   # đang chờ ảnh lâu vẫn tính là còn kết nối
                hit = req.pop(w, None)
                if hit:
                    db.kv_set("focus_req", req)
                act = act_req.pop(w, None)
                if act:
                    db.kv_set("action_req", act_req)
                # extension so sánh r.action === 'repair' → trả CHUỖI tên lệnh, không phải cả dict
                return self._send(200, {"focus": bool(hit and time.time() - hit < 60),
                                        "action": act.get("action") if act and time.time() - act.get("at", 0) < 60
                                        else None})
            if p == "/nb/status":
                return self._send(200, notebook.summary())
            if p == "/nb/next":
                q = parse_qs(u.query)
                en = q.get("en", ["1"])[0] != "0"
                notebook.heartbeat({"tab": q.get("tab", [""])[0], "version": q.get("v", [""])[0], "enabled": en})
                return self._send(200, {"task": notebook.claim_next() if en else None, "paused": notebook.paused()})
            if p == "/gemini/next":
                q = parse_qs(u.query)
                en = q.get("en", ["1"])[0] != "0"
                db.kv_set("gemini_seen", {"at": time.time(), "tab": q.get("tab", [""])[0],
                                          "version": q.get("v", [""])[0], "enabled": en})
                return self._send(200, {"task": gemini_bridge.claim_next() if en else None,
                                        "paused": gemini_bridge.paused()})
            if p == "/gemini/ping":
                req = db.kv_get("focus_req") or {}
                hit = req.pop("gemini", None)
                if hit:
                    db.kv_set("focus_req", req)
                return self._send(200, {"focus": bool(hit and time.time() - hit < 60)})
            if p == "/projects":
                return self._send(200, [{**_project(j), "images": autoflow.status(cfg, j["id"])} for j in db.jobs()])
            m = re.fullmatch(r"/projects/(\d+)(/[\w.]+)?", p)
            if not m:
                return self._send(404, {"error": "not found"})
            jid, sub = int(m.group(1)), m.group(2) or ""
            job = self._job(jid)
            if not job:
                return self._send(404, {"error": f"không có job {jid}"})
            if sub == "":
                return self._send(200, {**_project(job), "images": autoflow.status(cfg, jid)})
            if sub == "/youtube":
                v = youtube.view(jid)
                try:
                    rel = Path(v["folder"]).resolve().relative_to(packager.PROJECTS.resolve()).as_posix()
                    v["url"] = "/files/" + rel + "/"
                except ValueError:
                    v["url"] = ""
                return self._send(200, v)
            if sub == "/timing":
                v = pipeline.view(jid)
                v["metrics"] = pipeline.metrics(jid, v)
                return self._send(200, v)
            if sub == "/cover":
                f = packager.folder_of(jid) / "video" / "anh_bia.jpg"
                if f.exists():
                    return self._file(f)
                return self._send(404, {"error": "chưa có ảnh bìa"})
            if sub == "/files":
                fl = packager.list_files(jid)
                if not fl["files"]:                       # chưa đóng gói → ảnh đang nhận ở exchange
                    ex = autoflow.job_dir(cfg, jid)
                    fl["files"] = ["../exchange/" + str(x.relative_to(ROOT / "exchange")).replace("\\", "/")
                                   for x in sorted(ex.rglob("*")) if x.is_file()][:2000]
                return self._send(200, fl)
            if sub == "/scenes":
                rows = []
                for s in db.scenes(jid):
                    img = s.get("image")
                    rel = None
                    for base in (packager.PROJECTS, ROOT / "exchange"):   # /files tìm ở cả hai gốc này
                        try:
                            rel = str(Path(img).resolve().relative_to(base.resolve())).replace("\\", "/") if img \
                                else None
                            break
                        except ValueError:
                            continue
                    rows.append({"idx": s["idx"], "narration": s["narration"], "image": rel,
                                 "prompt": db.scene_data(s).get("image_prompt", "")})
                return self._send(200, rows)
            if sub == "/prompts.txt":
                rnd = parse_qs(u.query).get("round", [None])[0]
                st = autoflow.state(jid)
                txt = (autoflow.job_dir(cfg, jid) / f"round_{rnd}" / "prompts.txt") if rnd else \
                    (Path(st["txt"]) if st.get("txt") else None)
                if not txt or not txt.exists():
                    return self._send(404, {"error": "chưa có file prompt (job chưa tới bước ảnh)"})
                return self._text(200, txt.read_text(encoding="utf-8"))
            if sub == "/sources":
                f = autoflow.job_dir(cfg, jid) / "sources.md"
                if not f.exists():
                    return self._send(404, {"error": "job chưa qua bước research"})
                return self._text(200, f.read_text(encoding="utf-8"), "text/markdown; charset=utf-8")
            if sub == "/report":
                autoflow.write_report(cfg, jid)
                return self._text(200, (autoflow.job_dir(cfg, jid) / "report.json").read_text(encoding="utf-8"),
                                  "application/json; charset=utf-8")
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                return self._send(400, {"error": "body không phải JSON hợp lệ"})
            p = urlparse(self.path).path.rstrip("/")
            if p == "/test_image/open_folder":
                import subprocess, os
                test_dir = ROOT / "exchange" / "test_images"
                test_dir.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    os.startfile(str(test_dir))
                else:
                    subprocess.Popen(["xdg-open", str(test_dir)])
                return self._send(200, {"ok": True, "path": str(test_dir)})

            if p == "/test_image":
                test_topic = (data.get("topic") or "Putin, NATO và chiến tuyến 1.000 km ở Ukraine").strip()
                test_dir = ROOT / "exchange" / "test_images"
                test_dir.mkdir(parents=True, exist_ok=True)
                
                # đi qua đúng đường dựng prompt Flow của job thật (style + avoid của channel.yaml + luật visual_rules)
                from . import image, visual_rules
                scene = {"image_prompt": f"One whiteboard explainer illustration about: {test_topic}. "
                                         + visual_rules.WRITER_RULE_EN}
                full_prompt = image.build_prompt(scene, {}, load(), target="flow")
                now_t = time.time()
                uid = f"test_{int(now_t * 1000)}"
                # Dùng job_id = 0 cho task test
                db.x(
                    "INSERT INTO flow_tasks(job_id, idx, uid, round, prompt, created_at, status) "
                    "VALUES(?, ?, ?, ?, ?, ?, 'queued')",
                    (0, 1, uid, 1, full_prompt, now_t)
                )
                tid = db.q("SELECT id FROM flow_tasks WHERE uid=? ORDER BY id DESC LIMIT 1", (uid,))[0]["id"]
                events.push("log", {"msg": f"🎨 [TEST IMAGE] Đã nạp prompt test tạo ảnh vào Flow Bridge (Task #{tid})"})
                changed()
                return self._send(200, {
                    "ok": True,
                    "task_id": tid,
                    "prompt": full_prompt,
                    "msg": f"Đã xếp Task #{tid} vào Flow Bridge để tạo ảnh!"
                })

            if p == "/topics/scout":
                  # Tự động quét và tìm N chủ đề mới (mặc định n=3)
                  import threading
                  from . import topic_scout
                  n_count = int(data.get("count") or 3)
                  def _bg_scout():
                      try:
                          c = load()
                          l = LLM(c, print)
                          l.role = "text"
                          events.push("log", {"msg": f"🔍 Đang tự động tìm {n_count} chủ đề mới qua AI..."})
                          topics = topic_scout.scout(c, l, print, n=n_count)
                          events.push("log", {"msg": f"✔ Đã tìm thấy và thêm {len(topics)} chủ đề mới vào hàng đợi!"})
                          changed()
                      except Exception as e:
                          events.push("log", {"msg": f"❌ Lỗi tự tìm chủ đề: {e}"})
                  threading.Thread(target=_bg_scout, daemon=True).start()
                  return self._send(200, {"ok": True, "msg": f"Đang tìm {n_count} chủ đề..."})

            if p == "/topics":
                topics = data.get("topics") or [data.get("topic", "")]
                t_min = float(data["target_minutes"]) if data.get("target_minutes") else None
                ids = [db.add_topic(t, target_minutes=t_min) for t in topics if t and t.strip()]
                if data.get("style") in styles.STYLES:
                    for i in ids:
                        styles.lock(i, data["style"], load())
                if data.get("voice") and voices.valid(data["voice"]):
                    for i in ids:
                        voices.change(i, data["voice"])
                changed()
                return self._send(200, {"added": ids})
            m = re.fullmatch(r"/flow/tasks/(\d+)/(result|error)", p)
            if m:
                tid = int(m.group(1))
                if not db.q("SELECT id FROM flow_tasks WHERE id=?", (tid,)):
                    return self._send(404, {"error": "không có task"})
                if m.group(2) == "result":
                    try:
                        f = flowbridge.complete(load(), tid, data.get("data", ""), data.get("mime", "image/png"))
                    except Exception as e:
                        flowbridge.fail(tid, f"ảnh lỗi: {e}", worker=data.get("w", ""))
                        return self._send(400, {"error": str(e)})
                    return self._send(200, {"ok": True, "saved": str(f)})
                flowbridge.fail(tid, data.get("message", "lỗi"), pause=bool(data.get("pause")), worker=data.get("w", ""))
                return self._send(200, {"ok": True})
            m = re.fullmatch(r"/nb/tasks/(\d+)/(result|error)", p)
            if m:
                tid = int(m.group(1))
                if not db.q("SELECT id FROM nb_tasks WHERE id=?", (tid,)):
                    return self._send(404, {"error": "không có task"})
                if m.group(2) == "result":
                    notebook.complete(tid, data.get("text", ""))
                else:
                    notebook.fail(tid, data.get("message", "lỗi"), pause=bool(data.get("pause")))
                return self._send(200, {"ok": True})
            m_gem = re.fullmatch(r"/gemini/tasks/(\d+)/(result|error)", p)
            if m_gem:
                tid = int(m_gem.group(1))
                if not db.q("SELECT id FROM gemini_tasks WHERE id=?", (tid,)):
                    return self._send(404, {"error": "không có task gemini"})
                if m_gem.group(2) == "result":
                    gemini_bridge.complete(tid, data.get("text", ""))
                else:
                    gemini_bridge.fail(tid, data.get("message", "lỗi"), pause=bool(data.get("pause")))
                return self._send(200, {"ok": True})
            if p == "/gemini/resume":
                gemini_bridge.set_paused(None)
                return self._send(200, {"ok": True})
            if p == "/gemini/pause":
                gemini_bridge.set_paused(data.get("reason") or "tạm dừng thủ công")
                return self._send(200, {"ok": True})
            if p == "/nb/diag":
                db.kv_set("notebook_diag", data)
                return self._send(200, {"ok": True})
            if p == "/nb/resume":
                notebook.set_paused(None)
                return self._send(200, {"ok": True})
            if p == "/nb/pause":
                notebook.set_paused(data.get("reason") or "tạm dừng thủ công")
                return self._send(200, {"ok": True})
            if p == "/flow/diag":
                db.kv_set("flowbridge_diag", data)
                return self._send(200, {"ok": True})
            if p == "/flow/resume":
                # có w → chỉ mở lại profile đó; không có → mở lại tất cả (cả dừng chung)
                if data.get("w"):
                    flowbridge.set_worker_paused(data["w"], None)
                else:
                    flowbridge.set_paused(None)
                    flowbridge.set_worker_paused(None, None)
                return self._send(200, {"ok": True})
            if p == "/flow/pause":
                if data.get("w"):
                    flowbridge.set_worker_paused(data["w"], data.get("reason") or "tạm dừng thủ công")
                else:
                    flowbridge.set_paused(data.get("reason") or "tạm dừng thủ công")
                return self._send(200, {"ok": True})
            if p == "/start":
                worker.start()
                return self._send(200, {"running": True})
            if p == "/stop":
                worker.stop(immediate=bool(data.get("immediate")))
                return self._send(200, {"running": False})
            if p == "/stop_after":
                # {"job_id": 34} = dừng sau khi xong chủ đề #34 · {"count": 3} = sau 3 chủ đề nữa · {} = huỷ
                v = {k: data[k] for k in ("job_id", "count") if data.get(k)}
                db.kv_set("stop_after", v or None)
                events.push("log", {"msg": f"🏁 Mốc dừng: {v}" if v else "🏁 Đã huỷ mốc dừng"})
                changed()
                return self._send(200, {"stop_after": v or None})
            if p == "/focus":
                # dashboard bấm tên profile → extension của profile đó đưa tab Flow/NotebookLM lên trước
                req = db.kv_get("focus_req") or {}
                req[data.get("w") or "notebooklm"] = time.time()
                db.kv_set("focus_req", req)
                return self._send(200, {"ok": True})
            if p == "/repair":
                # Tự sửa lỗi cho profile: giải phóng pause + gửi lệnh action=repair cho extension reload/reconnect
                w = data.get("w") or "notebooklm"
                act_req = db.kv_get("action_req") or {}
                act_req[w] = {"action": "repair", "at": time.time()}
                db.kv_set("action_req", act_req)
                if w == "notebooklm":
                    notebook.set_paused(None)
                else:
                    flowbridge.set_worker_paused(w, None)
                events.push("log", {"msg": f"🔧 Đã gửi lệnh tự động sửa lỗi cho profile {w}"})
                changed()
                return self._send(200, {"ok": True, "worker": w})
            if p == "/browsers/open_all":
                # Mở toàn bộ 9 profile Chrome thành viên tự động
                import subprocess
                bat_path = ROOT / "Open_Chrome_Profiles.bat"
                if bat_path.exists():
                    subprocess.Popen(f'cmd /c "{bat_path}"', shell=True)
                    events.push("log", {"msg": "🚀 Đang tự động mở tất cả 9 profile Chrome..."})
                    return self._send(200, {"ok": True, "msg": "Đang mở toàn bộ profile Chrome"})
                return self._send(404, {"error": "Không tìm thấy Open_Chrome_Profiles.bat"})
            if p == "/browsers/close_all":
                # Đóng toàn bộ các tiến trình Chrome
                import subprocess
                subprocess.Popen('taskkill /F /IM chrome.exe /T', shell=True)
                flowbridge.set_paused(None)
                flowbridge.set_worker_paused(None, None)
                notebook.set_paused(None)
                events.push("log", {"msg": "⏹ Đã gửi lệnh đóng toàn bộ profile Chrome"})
                changed()
                return self._send(200, {"ok": True, "msg": "Đã đóng toàn bộ profile Chrome"})
            if p == "/test_all":
                # Tự động quét kiểm tra và tự sửa lỗi toàn bộ profile đang tạm dừng hoặc có lỗi
                repaired = []
                act_req = db.kv_get("action_req") or {}
                now_t = time.time()
                for wid, info in (flowbridge._workers() or {}).items():
                    if info.get("paused") or info.get("errors", 0) > 0:
                        flowbridge.set_worker_paused(wid, None)
                        act_req[wid] = {"action": "repair", "at": now_t}
                        repaired.append(info.get("name") or wid)
                if notebook.paused():
                    notebook.set_paused(None)
                    act_req["notebooklm"] = {"action": "repair", "at": now_t}
                    repaired.append("NotebookLM")
                db.kv_set("action_req", act_req)
                events.push("log", {"msg": f"🩺 Đã kiểm tra & kích hoạt tự sửa lỗi cho {len(repaired)} profile: {', '.join(repaired)}" if repaired else "🩺 Kiểm tra: Tất cả profile đều bình thường"})
                changed()
                return self._send(200, {"ok": True, "repaired": repaired})
            if p == "/llm/slot":
                c = load()
                try:
                    i = int(data.get("i"))
                    assert 0 <= i < llm_mod.MAX_SLOTS
                except (TypeError, ValueError, AssertionError):
                    return self._send(400, {"error": "khe không hợp lệ"})
                err = llm_mod.set_slot(c.lmstudio, i, **{k: data[k] for k in ("on", "model", "device") if k in data})
                if err:
                    return self._send(400, {"error": err, "slots": _llm_slots(c)})
                return self._send(200, {"slots": _llm_slots(c)})
            if p == "/voice":
                if voices.valid(data.get("voice") or ""):
                    db.kv_set("voice_default", data["voice"])
                return self._send(200, {"voice": voices.default(load())})
            if p == "/voices/upload":
                try:
                    name = voices.save_upload(data.get("name") or "giong.wav",
                                              base64.b64decode(data.get("data") or ""))
                except Exception as e:
                    return self._send(400, {"error": f"không đọc được file giọng: {e}"})
                return self._send(200, {"file": name, "options": voices.options()})
            if p == "/voices/delete":
                # Xoá file giọng mẫu tải lên
                file_name = data.get("file") or ""
                if not file_name:
                    return self._send(400, {"error": "Thiếu tên file giọng cần xoá"})
                ok = voices.delete_upload(file_name)
                # Nếu giọng đang là mặc định thì đưa về mặc định hệ thống
                cur_def = db.kv_get("voice_default") or ""
                if file_name in cur_def:
                    db.kv_set("voice_default", None)
                return self._send(200, {"ok": ok, "file": file_name, "options": voices.options(), "default": voices.default(load())})
            if p == "/voices/speak":
                # Thử nghiệm đọc thử câu mẫu bằng giọng được chọn
                vid = data.get("voice") or voices.default(load())
                test_text = (data.get("text") or "Xin chào! Đây là giọng đọc thử nghiệm trên hệ thống AutoVideo.").strip()
                try:
                    eng, v_sel = vid.split("|", 1)
                    from .tts import TTS
                    import tempfile
                    c = load()
                    t = TTS(c, print, engine=eng, voice=v_sel)
                    tmp_wav = Path(tempfile.gettempdir()) / f"voice_preview_{int(time.time()*1000)}.wav"
                    t.speak(test_text, tmp_wav)
                    audio_b64 = base64.b64encode(tmp_wav.read_bytes()).decode("ascii")
                    tmp_wav.unlink(missing_ok=True)
                    return self._send(200, {"audio": f"data:audio/wav;base64,{audio_b64}"})
                except Exception as e:
                    return self._send(500, {"error": f"Đọc thử giọng thất bại: {e}"})
            m = re.fullmatch(r"/projects/(\d+)/voice", p)
            if m:
                if not voices.valid(data.get("voice") or ""):
                    return self._send(400, {"error": "giọng không hợp lệ"})
                why = voices.change(int(m.group(1)), data["voice"])
                if why:
                    return self._send(409, {"error": why})
                changed()
                return self._send(200, {"voice": data["voice"]})
            if p == "/style":
                if data.get("style") in styles.STYLES:
                    db.kv_set("style_default", data["style"])
                return self._send(200, {"style": styles.default(load())})
            if p == "/mode":
                if data.get("mode") in ("auto", "quality"):
                    db.kv_set("mode", data["mode"])
                return self._send(200, {"mode": db.kv_get("mode")})
            if p == "/target_minutes":
                if data.get("minutes"):
                    try:
                        m_val = float(data["minutes"])
                        if 1 <= m_val <= 180:
                            db.kv_set("target_minutes_default", m_val)
                    except ValueError:
                        pass
                cur_m = db.kv_get("target_minutes_default") or load().target_minutes
                return self._send(200, {"target_minutes": cur_m})
            m_dur = re.fullmatch(r"/projects/(\d+)/duration", p)
            if m_dur:
                jid = int(m_dur.group(1))
                if not self._job(jid):
                    return self._send(404, {"error": "không có job"})
                try:
                    m_val = float(data.get("minutes", 20))
                    db.update_job(jid, target_minutes=m_val)
                    changed()
                    return self._send(200, {"ok": True, "job_id": jid, "target_minutes": m_val})
                except ValueError:
                    return self._send(400, {"error": "thời lượng không hợp lệ"})
            m = re.fullmatch(r"/projects/(\d+)/youtube/(regen|open)", p)
            if m and self._job(int(m.group(1))):
                jid = int(m.group(1))
                if m.group(2) == "open":
                    f = youtube.folder(jid)
                    f.mkdir(parents=True, exist_ok=True)
                    import os
                    os.startfile(str(f))
                    return self._send(200, {"opened": str(f)})
                c = load()
                started = youtube.start(jid, c, worker.log if hasattr(worker, "log") else print, LLM(c, print),
                                        force=True)
                return self._send(200, {"started": started})
            m = re.fullmatch(r"/projects/(\d+)/style", p)
            if m:
                if data.get("style") not in styles.STYLES:
                    return self._send(400, {"error": "kiểu không hợp lệ"})
                why = styles.change(int(m.group(1)), data["style"])
                if why:
                    return self._send(409, {"error": why})
                changed()
                return self._send(200, {"style": data["style"]})
            m = re.fullmatch(r"/projects/(\d+)/move", p)
            if m:
                db.move_job_before(int(m.group(1)), data.get("before"))
                changed()
                return self._send(200, {"ok": True})
            m = re.fullmatch(r"/projects/(\d+)/(open|delete|retry|up|down|package)", p)
            if m and self._job(int(m.group(1))):
                jid, act = int(m.group(1)), m.group(2)
                try:
                    if act == "open":
                        return self._send(200, {"opened": str(packager.open_folder(load(), jid))})
                    if act == "delete":
                        r = packager.delete_job(load(), jid)
                        changed()
                        return self._send(200, r)
                    if act == "retry":
                        db.retry_job(jid)
                    elif act in ("up", "down"):
                        db.move_job(jid, -1 if act == "up" else 1)
                    elif act == "package":
                        return self._send(200, {"folder": str(packager.package(load(), jid))})
                    changed()
                    return self._send(200, {"ok": True})
                except Exception as e:
                    return self._send(400, {"error": str(e)})
            m = re.fullmatch(r"/projects/(\d+)/(import|fallback)", p)
            if m and self._job(int(m.group(1))):
                jid = int(m.group(1))
                if m.group(2) == "import":
                    folder = data.get("folder")
                    if folder and not Path(folder).is_dir():
                        return self._send(400, {"error": f"không thấy thư mục {folder}"})
                    db.kv_set(f"autoflow_scan_{jid}", {"folder": folder})
                    return self._send(200, {"ok": True, "note": "worker sẽ quét ở nhịp tới"})
                db.kv_set(f"autoflow_fallback_{jid}", True)
                return self._send(200, {"ok": True, "note": "phần ảnh còn thiếu sẽ tạo bằng FLUX local"})
            return self._send(404, {"error": "not found"})

        def do_DELETE(self):
            p = urlparse(self.path).path.rstrip("/")
            if p == "/stop_after":
                db.kv_set("stop_after", None)
                changed()
                return self._send(200, {"stop_after": None})
            m = re.fullmatch(r"/projects/(\d+)", p)
            if m and self._job(int(m.group(1))):
                try:
                    r = packager.delete_job(load(), int(m.group(1)))
                    changed()
                    return self._send(200, r)
                except Exception as e:
                    return self._send(400, {"error": str(e)})
            return self._send(404, {"error": "not found"})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
