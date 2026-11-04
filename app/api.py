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
from . import autoflow, db, events, flowbridge, gemini_bridge, notebook, packager, pipeline, resources, styles, topic_scout, voices, youtube
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
    """LM Studio server có chạy không + model đang nạp (cache 20 giây, timeout nhanh 0.3s)."""
    now = time.time()
    if now - _lms_cache["t"] < 20 and _lms_cache["v"] is not None:
        return _lms_cache["v"]
    import requests
    v = {"up": False, "loaded": []}
    base = getattr(cfg.lmstudio, "base_url", "http://localhost:1234/v1") if hasattr(cfg, "lmstudio") else "http://localhost:1234/v1"
    try:
        url = base.replace("/v1", "/api/v0") + "/models"
        r = requests.get(url, timeout=0.3).json()
        loaded = [m["id"] for m in r.get("data", []) if m.get("state") == "loaded"]
        v = {"up": True, "loaded": loaded}
    except Exception:
        try:
            r2 = requests.get(base + "/models", timeout=0.3)
            if r2.status_code == 200:
                v = {"up": True, "loaded": []}
        except Exception:
            v = {"up": False, "loaded": []}
    _lms_cache.update(t=now, v=v)
    return v


def _browsers() -> dict:
    """Chrome profile đang dùng extension: trạng thái, việc đang làm, số ảnh, tốc độ/giờ, lần gọi gần nhất."""
    now = time.time()
    cfg = load()
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
    notebook.init_slots()
    nb_slots_db = db.q("SELECT * FROM nb_slots ORDER BY slot")
    target_prof = cfg.get("notebook_profile", "VR")
    nb_rejs = db.kv_get("nb_rejected") or {}
    active_rejs = [k for k, ts in nb_rejs.items() if now - ts < 120]
    warn_msg = f"Cảnh báo: Profile '{active_rejs[0]}' đang mở NotebookLM nhưng không phải {target_prof}" if active_rejs else ""
    if nb_slots_db:
        age = now - nb.get("at", 0) if nb else 9999
        ext_online = bool(nb and age < 360 and nb.get("enabled", True))
        for srow in nb_slots_db:
            s_id = srow["slot"]
            s_url = srow.get("notebook_url") or ""
            s_job = srow.get("job_id")
            s_step = srow.get("step") or ""
            s_up = srow.get("updated_at") or 0
            s_p = notebook.slot_paused(s_id)
            if not ext_online:
                st = "offline"
            elif srow["state"] == "no_notebook":
                st = "no_notebook"
            elif srow["state"] == "creating":
                st = "creating"
            elif s_p or notebook.paused():
                st = "paused"
            elif s_job:
                st = "busy"
            else:
                st = "idle"
            if st == "creating":
                doing_str = "Đang tạo sổ…"
            elif s_job:
                doing_str = f"Job #{s_job} ({s_step})"
            elif st == "no_notebook":
                doing_str = "Chưa có URL sổ"
            else:
                doing_str = "Chờ job"
            if warn_msg and s_id == 1:
                doing_str = f"{doing_str} · {warn_msg}"
            rows.append({
                "id": f"notebooklm_{s_id}",
                "slot": s_id,
                "name": f"NotebookLM #{s_id} ({target_prof})",
                "kind": "notebook",
                "version": nb.get("version") if nb else "",
                "state": st,
                "last_sec": round(now - s_up) if s_up else round(age),
                "paused": s_p or notebook.paused(),
                "latest": nb_latest,
                "outdated": _older(nb.get("version"), nb_latest) and nb_latest if nb else False,
                "doing": doing_str,
                "job_id": s_job,
                "notebook_url": s_url
            })
    elif nb:
        age = now - nb.get("at", 0)
        rows.append({"id": "notebooklm", "name": f"NotebookLM ({target_prof})", "kind": "notebook", "version": nb.get("version"),
                     "state": ("offline" if age > 360 else "idle"),
                     "last_sec": round(age), "paused": notebook.paused(), "latest": nb_latest,
                     "outdated": _older(nb.get("version"), nb_latest) and nb_latest,
                     "doing": warn_msg})
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
            if p == "/selftest":
                from . import selftest
                return self._send(200, {**selftest.results(), "running": worker.running})
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
                return self._send(200, {"running": getattr(worker, "running", False) if worker else False, "mode": db.kv_get("mode") or cfg.get("mode"),
                                        "style": styles.default(cfg), "target_minutes": db.kv_get("target_minutes_default") or cfg.target_minutes,
                                        "auto_topics": topic_scout.acfg(cfg)["enabled"],
                                        "topic_engine": topic_scout.acfg(cfg).get("engine", "notebooklm"),
                                        "lmstudio": _lmstudio(cfg), "flow_online": br["flow_online"],
                                        "flow_busy": br["flow_busy"],
                                        "notebook": next((b["state"] for b in br["browsers"] if b["kind"] == "notebook"),
                                                         "offline"),
                                        "gemini": next((b["state"] for b in br["browsers"] if b["kind"] == "gemini"),
                                                       "offline"),
                                        "stop_after": db.kv_get("stop_after"), "fatal": db.kv_get("fatal"),
                                        "res": resources.snapshot()})
            if p == "/topics/niches":
                cfg = load()
                return self._send(200, {"niches": topic_scout.acfg(cfg)["niches"]})
            if p == "/topics/suggestions":
                cache = db.kv_get("topic_suggestions") or {}
                items = list(cache.get("items") or [])
                existing_topics = {j["topic"].strip().lower() for j in db.jobs()}
                for it in items:
                    it["added"] = it.get("topic", "").strip().lower() in existing_topics
                return self._send(200, {
                    "ts": cache.get("ts"),
                    "query": cache.get("query"),
                    "items": items
                })
            if p == "/stats/timing":
                done = [j for j in db.jobs() if j["status"] == "done"]
                out = []
                for j in done:
                    t = pipeline.view(j["id"])
                    out.append({"id": j["id"], "topic": j["topic"], "active_sec": t["active_sec"],
                                "wall_sec": t["wall_sec"], "steps": {s["key"]: s["active_sec"] for s in t["steps"]}})
                return self._send(200, out)
            if p == "/jobs":
                return self._send(200, {"running": getattr(worker, "running", False) if worker else False, "jobs": [_project_full(j) for j in db.jobs()]})
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
            if p in ("/flow/ping", "/nb/ping", "/gemini/ping"):
                # extension hỏi nhanh: dashboard có yêu cầu "đưa tab lên trước" hoặc "tự sửa lỗi/repair" hoặc "close" không
                w = parse_qs(u.query).get("w", ["notebooklm"])[0] if p == "/flow/ping" else ("gemini" if p == "/gemini/ping" else "notebooklm")
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
                # extension so sánh r.action === 'repair' | 'close' → trả chuỗi lệnh
                action_name = act.get("action") if act and time.time() - act.get("at", 0) < 60 else None
                return self._send(200, {"focus": bool(hit and time.time() - hit < 60),
                                        "action": action_name,
                                        "name": act.get("name") if act else None})
            if p == "/nb/status":
                return self._send(200, notebook.summary())
            if p == "/nb/config":
                sc = (cfg.get("script") or {})
                notebook.init_slots()
                slots_data = db.q("SELECT slot, notebook_url, state, job_id, step FROM nb_slots ORDER BY slot")
                return self._send(200, {
                    "profile": (cfg.get("notebook_profile") or "VR").strip(),
                    "slots_count": int(sc.get("nb_slots", 5)),
                    "slots": slots_data,
                    "notebooks": sc.get("nb_notebooks") or [],
                    "stagger": int(sc.get("nb_stagger_sec", 20)),
                    "stagger_sec": int(sc.get("nb_stagger_sec", 20)),
                })
            if p == "/nb/next":
                q = parse_qs(u.query)
                en = q.get("en", ["1"])[0] != "0"
                name = (q.get("name", [""])[0] or "").strip()
                target_prof = (cfg.get("notebook_profile") or "VR").strip()
                slot_param = q.get("slot", [None])[0]
                slot = int(slot_param) if slot_param and slot_param.isdigit() else None
                tab_id_param = q.get("tab_id", [None])[0]
                tab_id = int(tab_id_param) if tab_id_param and tab_id_param.isdigit() else None
                if name.lower() != target_prof.lower():
                    rej = db.kv_get("nb_rejected") or {}
                    rej[name or "unknown"] = time.time()
                    db.kv_set("nb_rejected", rej)
                    return self._send(200, {"task": None, "paused": None, "reject": "not_vr"})
                # Đã khớp VR: xoá cảnh báo reject cũ nếu có
                rej = db.kv_get("nb_rejected") or {}
                if rej:
                    db.kv_set("nb_rejected", {})
                ext_ver = q.get("v", [""])[0]
                tab_url_param = q.get("tab_url", [""])[0]
                if ext_ver:
                    if _older(ext_ver, "1.1.2"):
                        now_warn = time.time()
                        last_v_warn = db.kv_get("nb_version_warn_last") or 0
                        if now_warn - last_v_warn > 300:
                            db.kv_set("nb_version_warn_last", now_warn)
                            events.push("log", {"msg": f"⚠️ Extension Notebook Bridge cũ (v{ext_ver} < v1.1.2) – reload ở chrome://extensions profile VR"})
                hb_data = {"tab": q.get("tab", [""])[0], "version": ext_ver, "enabled": en, "name": name, "slot": slot}
                if tab_url_param:
                    hb_data["tab_url"] = tab_url_param
                notebook.heartbeat(hb_data)
                if slot:
                    cur_slot = db.q("SELECT job_id, state, notebook_url FROM nb_slots WHERE slot=?", (slot,))
                    if cur_slot:
                        cs = cur_slot[0]
                        # Giữ nguyên state "creating" khi extension đang tạo sổ
                        if cs["state"] == "creating":
                            st = "creating"
                        elif notebook.slot_paused(slot):
                            st = "paused"
                        elif cs["job_id"] is not None:   # job_id=0 = đang tìm chủ đề, vẫn là bận
                            st = "busy"
                        elif cs.get("notebook_url"):
                            st = "idle"
                        else:
                            st = "no_notebook"
                        db.x("UPDATE nb_slots SET tab_id=COALESCE(?, tab_id), state=?, updated_at=? WHERE slot=?",
                             (tab_id, st, time.time(), slot))
                task = notebook.claim_next(slot=slot) if en else None
                return self._send(200, {"task": task, "paused": notebook.paused() or (notebook.slot_paused(slot) if slot else None)})
            if p == "/gemini/next":
                q = parse_qs(u.query)
                en = q.get("en", ["1"])[0] != "0"
                db.kv_set("gemini_seen", {"at": time.time(), "tab": q.get("tab", [""])[0],
                                          "version": q.get("v", [""])[0], "enabled": en})
                return self._send(200, {"task": gemini_bridge.claim_next() if en else None,
                                        "paused": gemini_bridge.paused()})

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
            if sub == "/thumb":
                return self._send(200, youtube.thumb_date_get(jid, load()))
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
            cfg = load()
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

            if p == "/topics/auto":
                enabled = bool(data.get("enabled", True))
                db.kv_set("auto_topics_enabled", enabled)
                changed()
                return self._send(200, {"ok": True, "enabled": enabled})

            if p == "/topics/suggest":
                cfg = load()
                a_cfg = topic_scout.acfg(cfg)
                engine = (a_cfg.get("engine") or "notebooklm").strip().lower()
                if engine == "notebooklm":
                    notebook.init_slots()
                    idle_slots = db.q("SELECT COUNT(*) n FROM nb_slots WHERE notebook_url != '' AND state != 'offline'")[0]["n"]
                    s_seen = db.kv_get("notebook_seen") or {}
                    has_tab = bool(s_seen.get("tab_url") and notebook.is_real_url(s_seen.get("tab_url")))
                    if idle_slots == 0 and not has_tab and not notebook.connected():
                        return self._send(503, {"ok": False, "error": "Chưa có sổ NotebookLM nào sẵn sàng – mở hoặc tạo sổ trên tab NotebookLM", "msg": "Chưa có sổ NotebookLM nào sẵn sàng"})

                niche = (data.get("niche") or "").strip() or None
                valid_niches = a_cfg["niches"]
                if niche and niche not in valid_niches:
                    return self._send(400, {"error": "Ngách không hợp lệ"})

                q_text = (data.get("query") or "").strip() or None
                if q_text and topic_scout.is_vietnam(q_text):
                    return self._send(400, {"error": "Kênh không làm chủ đề về Việt Nam"})

                if topic_scout._SCOUT_LOCK.locked():
                    return self._send(409, {"error": "Đang tìm chủ đề, đợi xong lượt trước"})

                n_count = int(data.get("count") or 10)
                def _bg_suggest():
                    try:
                        c = load()
                        l = LLM(c, print)
                        l.role = "text"
                        q_desc = f" theo từ khoá \"{q_text}\"" if q_text else ""
                        n_desc = f" ngách [{niche}]" if niche else ""
                        events.push("log", {"msg": f"🔥 Đang tìm {n_count} chủ đề hot{n_desc}{q_desc} qua AI..."})
                        items = topic_scout.suggest(c, l, print, n=n_count, query=q_text, niche=niche)
                        events.push("suggestions", {"query": q_text, "niche": niche, "items": items})
                        events.push("log", {"msg": f"✔ Đã tìm thấy {len(items)} chủ đề hot{n_desc}{q_desc}!"})
                    except Exception as e:
                        events.push("suggestions_error", {"msg": str(e)})
                        events.push("log", {"msg": f"❌ Lỗi tìm chủ đề hot: {e}"})
                threading.Thread(target=_bg_suggest, daemon=True).start()
                return self._send(200, {"ok": True, "msg": f"Đang tìm {n_count} chủ đề..."})

            if p == "/topics/scout":
                  # Tự động quét và tìm N chủ đề mới (mặc định n=3)
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
                r_task = db.q("SELECT * FROM flow_tasks WHERE id=?", (tid,))
                if not r_task:
                    events.push("log", {"msg": f"⚠️ Task #{tid} không tồn tại trong DB, bỏ qua kết quả."})
                    return self._send(200, {"ok": True, "ignored": True, "reason": "task không tồn tại"})
                t = r_task[0]
                if m.group(2) == "result":
                    if t.get("status") != "claimed":
                        events.push("log", {"msg": f"⚠️ Task #{tid} gửi kết quả muộn (status='{t.get('status')}'), bỏ qua."})
                        return self._send(200, {"ok": True, "ignored": True, "reason": f"task status is {t.get('status')}"})
                    try:
                        f = flowbridge.complete(load(), tid, data.get("data", ""), data.get("mime", "image/png"))
                    except Exception as e:
                        flowbridge.fail(tid, f"ảnh lỗi: {e}", worker=data.get("w", ""))
                        return self._send(200, {"ok": False, "ignored": True, "error": str(e)})
                    return self._send(200, {"ok": True, "saved": str(f)})
                flowbridge.fail(tid, data.get("message", "lỗi"), pause=bool(data.get("pause")), worker=data.get("w", ""))
                return self._send(200, {"ok": True})
            m = re.fullmatch(r"/nb/tasks/(\d+)/(result|error)", p)
            if m:
                tid = int(m.group(1))
                if not db.q("SELECT id FROM nb_tasks WHERE id=?", (tid,)):
                    return self._send(404, {"error": "không có task"})
                slot_in = data.get("slot")
                if m.group(2) == "result":
                    notebook.complete(tid, data.get("text", ""))
                else:
                    notebook.fail(tid, data.get("message", "lỗi"), pause=bool(data.get("pause")), slot=slot_in)
                changed()
                return self._send(200, {"ok": True})
            m_rel = re.fullmatch(r"/nb/slots/(\d+)/release", p)
            if m_rel:
                s_idx = int(m_rel.group(1))
                r_slot = db.q("SELECT job_id, notebook_url FROM nb_slots WHERE slot=?", (s_idx,))
                if r_slot:
                    jid = r_slot[0]["job_id"]
                    url = r_slot[0].get("notebook_url") or ""
                    has_url = notebook.is_real_url(url)
                    st = "idle" if has_url else "no_notebook"
                    db.x("UPDATE nb_slots SET job_id=NULL, task_id=NULL, step=NULL, state=?, updated_at=? WHERE slot=?",
                         (st, time.time(), s_idx))
                    notebook.set_slot_paused(s_idx, None)
                    events.push("log", {"msg": f"🔓 Đã giải phóng thủ công NotebookLM slot #{s_idx} (Job #{jid})"})
                    changed()
                    return self._send(200, {"ok": True, "slot": s_idx})
                return self._send(404, {"error": "slot không tồn tại"})
            m_slot_state = re.fullmatch(r"/nb/slot/(\d+)/state", p)
            if m_slot_state:
                k = int(m_slot_state.group(1))
                cfg2 = load()
                target_prof = (cfg2.get("notebook_profile") or "VR").strip()
                req_name = (data.get("name") or "").strip()
                if req_name.lower() != target_prof.lower():
                    return self._send(403, {"error": "chỉ profile VR được cập nhật trạng thái slot"})
                new_state = (data.get("state") or "").strip()
                msg = (data.get("msg") or "").strip()
                if new_state in ("creating", "error", "idle", "no_notebook", "paused", "busy"):
                    db.x("UPDATE nb_slots SET state=?, updated_at=? WHERE slot=?",
                         (new_state, time.time(), k))
                    if msg:
                        events.push("log", {"msg": f"Slot #{k}: {msg}"})
                    changed()
                    return self._send(200, {"ok": True, "slot": k, "state": new_state})
                return self._send(400, {"error": f"trạng thái không hợp lệ: {new_state}"})

            m_slot_url = re.fullmatch(r"/nb/slot/(\d+)/url", p)
            if m_slot_url:
                k = int(m_slot_url.group(1))
                cfg2 = load()
                target_prof = (cfg2.get("notebook_profile") or "VR").strip()
                req_name = (data.get("name") or "").strip()
                if req_name.lower() != target_prof.lower():
                    return self._send(403, {"error": "chỉ profile VR được ghi URL sổ"})
                new_url = (data.get("url") or "").strip()
                if new_url:
                    if not notebook.is_real_url(new_url):
                        return self._send(400, {"error": "URL sổ không hợp lệ (hỗ trợ notebook.google.com & notebooklm.google.com)"})
                    db.x("UPDATE nb_slots SET notebook_url=?, state='idle', updated_at=? WHERE slot=?",
                         (new_url, time.time(), k))
                    events.push("log", {"msg": f"📓 Slot #{k}: đã tạo/lưu sổ {new_url}"})
                else:
                    # BẮT BUỘC kiểm tra confirm: 'recreate'
                    confirm = data.get("confirm")
                    raw_ua = self.headers.get("User-Agent", "unknown")
                    raw_ref = self.headers.get("Referer", "unknown")
                    client_ip = self.client_address[0] if self.client_address else "unknown"
                    if confirm != "recreate":
                        err_log = f"⚠️ Từ chối xóa URL slot #{k}: thiếu confirm='recreate'. Caller: {client_ip}, UA: {raw_ua}, Referer: {raw_ref}, Body: {data}"
                        events.push("log", {"msg": err_log})
                        return self._send(400, {"error": "Thiếu confirm: recreate để xoá URL sổ", "details": err_log})
                    db.x("UPDATE nb_slots SET notebook_url='', state='no_notebook', updated_at=? WHERE slot=?",
                         (time.time(), k))
                    events.push("log", {"msg": f"🗑 Slot #{k}: đã xóa URL sổ (người dùng bấm Tạo lại sổ)"})
                changed()
                return self._send(200, {"ok": True, "slot": k})
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
            if p == "/selftest":
                # test từng bước: chỉ khi app đã dừng (không tranh GPU / tab Flow / khoá TTS với job thật)
                from . import selftest
                if worker.running:
                    return self._send(409, {"error": "App đang chạy – bấm Dừng trước khi test từng bước"})
                why = selftest.start(data.get("step") or "all", data.get("topic") or "")
                if why:
                    return self._send(409, {"error": why})
                return self._send(200, {"ok": True})
            if p == "/start":
                from . import selftest
                if selftest.results()["busy"]:
                    return self._send(409, {"error": "Đang test từng bước – chờ test xong rồi mới Bắt đầu"})
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
                # Tự sửa lỗi cho profile: gửi lệnh action=repair cho extension reload/reconnect
                # Nếu profile đang bị captcha/giới hạn: giữ trạng thái tạm dừng, yêu cầu người dùng xác nhận trên tab và bấm Tiếp tục
                w = data.get("w") or "notebooklm"
                act_req = db.kv_get("action_req") or {}
                act_req[w] = {"action": "repair", "at": time.time()}
                db.kv_set("action_req", act_req)
                if w == "notebooklm":
                    # Chống spam repair: tối đa 1 lần/5 phút
                    _repair_throttle = 300
                    _last_repair_t = db.kv_get("nb_repair_last_at") or 0
                    if time.time() - _last_repair_t < _repair_throttle:
                        return self._send(200, {"ok": True, "throttled": True, "worker": w})
                    db.kv_set("nb_repair_last_at", time.time())
                    nb_paused = notebook.paused()
                    if nb_paused and "captcha" in str(nb_paused).lower():
                        notebook.set_paused("Xử lý captcha/xác minh trên tab rồi bấm 'Tiếp tục'")
                    else:
                        notebook.set_paused(None)
                elif w == "gemini":
                    gem_paused = gemini_bridge.paused()
                    if gem_paused and "captcha" in str(gem_paused).lower():
                        gemini_bridge.set_paused("Xử lý captcha/xác minh trên tab rồi bấm 'Tiếp tục'")
                    else:
                        gemini_bridge.set_paused(None)
                else:
                    cur_info = (flowbridge._workers() or {}).get(w, {})
                    cur_p = str(cur_info.get("paused") or "")
                    if cur_p and "captcha" in cur_p.lower():
                        flowbridge.set_worker_paused(w, "Xử lý captcha trên tab rồi bấm 'Tiếp tục profile này'")
                    else:
                        flowbridge.set_worker_paused(w, None)
                events.push("log", {"msg": f"🔧 Đã gửi lệnh tự động sửa lỗi cho profile {w}"})
                changed()
                return self._send(200, {"ok": True, "worker": w})
            if p == "/browsers/open_all":
                # Mở toàn bộ 7 profile Chrome thành viên tự động (bỏ QUAN và CANH)
                import subprocess
                bat_path = ROOT / "Open_Chrome_Profiles.bat"
                if bat_path.exists():
                    def _bg_open():
                        t_wait_start = time.time()
                        while time.time() - t_wait_start < 15:
                            try:
                                check_cmd = 'Get-CimInstance Win32_Process -Filter "Name = \'chrome.exe\'" | Where-Object { $_.CommandLine -match "Profile (2|3|23|24|27|28|33)" }'
                                out_p = subprocess.run(["powershell", "-NoProfile", "-Command", check_cmd], capture_output=True, text=True, timeout=3)
                                if not out_p.stdout.strip():
                                    break
                            except Exception:
                                break
                            time.sleep(1)
                        subprocess.Popen(f'cmd /c "{bat_path}"', shell=True)
                        events.push("log", {"msg": "🚀 Đang tự động mở 7 profile Chrome..."})
                    threading.Thread(target=_bg_open, daemon=True).start()
                    return self._send(200, {"ok": True, "msg": "Đang mở 7 profile Chrome (tiến trình nền)"})
                return self._send(404, {"error": "Không tìm thấy Open_Chrome_Profiles.bat"})
            if p == "/browsers/close_all":
                # Đóng profile có chọn lọc hoặc cưỡng bức bằng taskkill
                import subprocess
                force = bool(data.get("force")) if isinstance(data, dict) else False
                flowbridge.set_paused(None)
                flowbridge.set_worker_paused(None, None)
                notebook.set_paused(None)
                gemini_bridge.set_paused(None)
                if force:
                    subprocess.Popen('taskkill /F /IM chrome.exe', shell=True)
                    events.push("log", {"msg": "⏹ Đã gửi lệnh đóng cưỡng bức toàn bộ Chrome (kể cả QUAN)"})
                    changed()
                    return self._send(200, {"ok": True, "msg": "Đã đóng cưỡng bức toàn bộ Chrome"})
                
                keep_open = [x.strip().lower() for x in (cfg.get("browsers", {}).get("keep_open") or ["QUAN"])]
                act_req = db.kv_get("action_req") or {}
                now_t = time.time()
                closing = []
                kept = []
                for wid, winfo in (flowbridge._workers() or {}).items():
                    wname = (winfo.get("name") or wid).strip()
                    if wname.lower() in keep_open:
                        kept.append(wname)
                    else:
                        closing.append(wname)
                        act_req[wid] = {"action": "close", "at": now_t}
                # NotebookLM & Gemini
                target_nb = (cfg.get("notebook_profile") or "VR").strip()
                if target_nb.lower() not in keep_open:
                    act_req["notebooklm"] = {"action": "close", "name": target_nb, "at": now_t}
                    closing.append("NotebookLM (" + target_nb + ")")
                else:
                    kept.append("NotebookLM (" + target_nb + ")")
                act_req["gemini"] = {"action": "close", "at": now_t}
                closing.append("Gemini")
                db.kv_set("action_req", act_req)
                all_kept = sorted(list(set(kept + ["QUAN (Default)", "CANH (Profile 1)"])))
                events.push("log", {"msg": f"⏹ Đang đóng {len(closing)} profile Chrome (giữ: {', '.join(all_kept)})"})
                changed()
                return self._send(200, {"ok": True, "closing": closing, "kept": kept, "msg": f"Đang đóng {len(closing)} profile (giữ {', '.join(kept) or 'QUAN'})"})
            if p == "/test_all":
                # Tự động quét kiểm tra và tự sửa lỗi toàn bộ profile đang tạm dừng hoặc có lỗi
                repaired = []
                act_req = db.kv_get("action_req") or {}
                now_t = time.time()
                for wid, info in (flowbridge._workers() or {}).items():
                    if info.get("paused") or info.get("errors", 0) > 0:
                        cur_p = str(info.get("paused") or "")
                        if cur_p and "captcha" in cur_p.lower():
                            flowbridge.set_worker_paused(wid, "Xử lý captcha trên tab rồi bấm 'Tiếp tục profile này'")
                        else:
                            flowbridge.set_worker_paused(wid, None)
                        act_req[wid] = {"action": "repair", "at": now_t}
                        repaired.append(info.get("name") or wid)
                # NotebookLM: chỉ repair khi thực sự paused (không phải no_notebook/creating)
                # Chống spam: tối đa 1 repair/5 phút
                _nb_repair_throttle = 300
                _nb_last_repair = db.kv_get("nb_repair_last_at") or 0
                _nb_slots_all = db.q("SELECT state FROM nb_slots")
                _nb_all_no_url = _nb_slots_all and all(
                    r["state"] in ("no_notebook", "creating", "offline") for r in _nb_slots_all)
                if (notebook.paused() and not _nb_all_no_url
                        and (now_t - _nb_last_repair) >= _nb_repair_throttle):
                    nb_p = str(notebook.paused() or "")
                    if "captcha" in nb_p.lower():
                        notebook.set_paused("Xử lý captcha/xác minh trên tab rồi bấm 'Tiếp tục'")
                    else:
                        notebook.set_paused(None)
                    act_req["notebooklm"] = {"action": "repair", "at": now_t}
                    db.kv_set("nb_repair_last_at", now_t)
                    repaired.append("NotebookLM")
                if gemini_bridge.paused():
                    gem_p = str(gemini_bridge.paused() or "")
                    if "captcha" in gem_p.lower():
                        gemini_bridge.set_paused("Xử lý captcha/xác minh trên tab rồi bấm 'Tiếp tục'")
                    else:
                        gemini_bridge.set_paused(None)
                    act_req["gemini"] = {"action": "repair", "at": now_t}
                    repaired.append("Gemini")
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
            if p == "/projects/delete_batch":
                jids = data.get("ids") or []
                if not isinstance(jids, list) or not jids:
                    return self._send(400, {"error": "Danh sách id không hợp lệ"})
                r = packager.delete_jobs(load(), [int(x) for x in jids if str(x).isdigit()])
                changed()
                return self._send(200, r)
            if p == "/projects/audit_missing" or p == "/youtube/audit_missing":
                c = load()
                res = youtube.audit_and_fix_all(c, worker.log if hasattr(worker, "log") else print, LLM(c, print))
                changed()
                return self._send(200, res)
            m = re.fullmatch(r"/projects/(\d+)/youtube/(regen|open)", p)
            if m and self._job(int(m.group(1))):
                jid = int(m.group(1))
                if m.group(2) == "open":
                    f = youtube.folder(jid)
                    f.mkdir(parents=True, exist_ok=True)
                    import os
                    os.startfile(str(f))
                    return self._send(200, {"opened": str(f)})
                parts = [x for x in (data.get("parts") or ["title", "thumb"]) if x in ("title", "thumb")]
                if not parts:
                    return self._send(400, {"error": "chưa chọn phần nào để tạo lại"})
                if "date" in data or "show" in data:
                    try:
                        youtube.thumb_date_set(jid, (data.get("date") or "").strip() or None,
                                               bool(data.get("show", True)))
                    except ValueError:
                        return self._send(400, {"error": "ngày không hợp lệ (yyyy-mm-dd, vd 2026-08-15)"})
                c = load()
                started = youtube.start(jid, c, worker.log if hasattr(worker, "log") else print, LLM(c, print),
                                        force=True, parts=parts)
                if not started:
                    return self._send(409, {"error": "bộ YouTube của job này đang chạy – đợi xong rồi tạo lại"})
                return self._send(200, {"started": started, "parts": parts})
            m = re.fullmatch(r"/projects/(\d+)/thumb", p)
            if m and self._job(int(m.group(1))):
                jid = int(m.group(1))
                # ngày sai (vd 31/02) → 400; chỉ vẽ lại thumbnail, không gọi Flow
                try:
                    youtube.thumb_date_set(jid, (data.get("date") or "").strip() or None, bool(data.get("show", True)))
                except ValueError:
                    return self._send(400, {"error": "ngày không hợp lệ (yyyy-mm-dd, vd 2026-08-15)"})
                try:
                    youtube.redraw_thumbnails(jid, load())
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                changed()
                return self._send(200, {**youtube.thumb_date_get(jid, load()), "v": int(time.time())})
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