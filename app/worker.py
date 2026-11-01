import copy
import json
import re
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import (annotate, voices, artifacts, audio_qc, autoflow, canvas, complexity, db, director, flowbridge, errors, events, image, maintenance, notebook, packager, pipeline, preflight, procs,
               render, research, resources, styles, subtitles, topic_scout, video_qc, websearch, youtube)
from .config import load, path, save_wpm
from .styles import nikola
from .llm import LLM
from .lock import WorkerLock
from .tts import TTS

STAGES = ["research", "script", "tts", "image", "render", "assemble", "qc", "package", "done"]
CALIB = path("data") / "calibration.jsonl"


class Stopped(Exception):
    pass


class AwaitReview(Exception):
    pass


def slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.U).strip()
    return re.sub(r"\s+", "_", s)[:60] or "video"


class _RedoImage(Exception):
    """Nikola: vùng vẽ của ảnh màn không đạt check_drawable_regions → quay lại bước tạo ảnh."""


class Worker:
    def __init__(self, log, progress, changed):
        self.log = log
        self._ui_progress = progress  # (job_id, stage, done, total, image_path|None)
        self.changed = changed
        self.stop_evt = threading.Event()
        self.thread = None
        self._shared = {"tts": None}        # copy.copy() ở luồng chuẩn bị giữ cùng dict → 1 TTS + 1 Whisper cho cả app
        self._tts_lock = threading.Lock()   # TTS dùng chung giữa luồng chính và luồng chuẩn bị
        self._prep = False                  # bản sao chạy ở luồng chuẩn bị: không dọn tài nguyên toàn cục

    @property
    def tts(self):
        return self._shared["tts"]

    @tts.setter
    def tts(self, v):
        self._shared["tts"] = v

    def progress(self, jid, stage, done, total, img):
        """Tiến độ → giao diện + đồng hồ bước (pipeline) + luồng sự kiện cho dashboard web."""
        if jid:
            try:
                pipeline.tick(jid, stage, done, total)
            except Exception:
                pass
        events.push("progress", {"job": jid, "stage": stage, "done": done, "total": total,
                                 "image": str(img) if img else None})
        self._ui_progress(jid, stage, done, total, img)

    def _stop_after_reached(self, jid) -> bool:
        """Mốc 'dừng sau khi xong…': theo id chủ đề hoặc theo số chủ đề làm xong kể từ lúc đặt."""
        sa = db.kv_get("stop_after") or {}
        if not sa:
            return False
        if sa.get("job_id") == jid:
            return True
        if sa.get("count"):
            sa["done"] = sa.get("done", 0) + 1
            db.kv_set("stop_after", sa)
            return sa["done"] >= int(sa["count"])
        return False

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        if any(t.is_alive() for t in getattr(self, "_prep_threads", [])):
            self.log("⏳ Luồng chuẩn bị của lần chạy trước chưa dừng hẳn – đợi vài giây rồi bấm Bắt đầu lại")
            return
        self.lock = WorkerLock(db.DB_PATH)
        ok, why = self.lock.acquire()
        if not ok:
            self.log(f"⛔ Không chạy: {why}")
            db.kv_set("fatal", why)
            self.changed()
            return
        db.kv_set("fatal", None)
        db.x("UPDATE jobs SET status='queued' WHERE status IN ('preparing','running')")
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self, immediate: bool = False):
        """Dừng an toàn: mặc định làm xong bước nhỏ hiện tại; immediate=True kết thúc ngay tiến trình con."""
        self.stop_evt.set()
        if immediate:
            procs.terminate_all()

    def _preflight(self) -> bool:
        resources.kill_orphans(self.log)
        problems = preflight.check(load())
        if problems:
            for p in problems:
                self.log(f"Preflight: {p}")
            self._fatal("Preflight không đạt: " + "; ".join(problems[:3]))
            return False
        self.log("Preflight OK")
        self._open_browser_tabs()
        return True

    def _open_browser_tabs(self):
        """Bấm Bắt đầu là đủ: extension NotebookLM / Flow chưa kết nối thì mở sẵn tab trên trình duyệt mặc định
        (Chrome đã đăng nhập) để extension tự chạy."""
        cfg = load()
        b = cfg.get("browser") or {}
        if not b.get("open_on_start", True):
            return
        import webbrowser
        want = []
        if (cfg.get("script") or {}).get("engine") == "notebooklm" and not notebook.connected():
            want.append(b.get("notebook_url", "https://notebooklm.google.com/"))
        if cfg.image.get("provider") == "flowbridge" and not flowbridge.connected():
            want.append(b.get("flow_url", "https://labs.google/fx/tools/flow"))
        for u in want:
            try:
                webbrowser.open(u, new=2)
                self.log(f"Mở tab {u} cho extension")
            except Exception as e:
                self.log(f"Không mở được trình duyệt: {e}")

    def _check(self):
        if self.stop_evt.is_set():
            raise Stopped()

    def _budget(self, key, default):
        return int((self.cfg.bible.get("retry") or {}).get(key, default))

    # ------------------------------------------------------------ main loop
    def _loop(self):
        self.log("▶ Bắt đầu chạy")
        try:
            if self._preflight():
                # Chạy 2 luồng chuẩn bị song song (đáp ứng làm kịch bản trước cho 2 dự án sau ở hàng chờ)
                self._prep_threads = [threading.Thread(target=self._prep_loop, name=f"prep-{i}", daemon=True)
                                      for i in (1, 2)]
                for t in self._prep_threads:
                    t.start()
                self._main()
        except Exception as e:
            self.log(f"⛔ Luồng chính dừng vì lỗi không mong đợi: {e!r}")
            db.kv_set("fatal", f"luồng chính lỗi: {e}")
        finally:
            self.stop_evt.set()
            # chờ luồng chuẩn bị thoát trước khi nhả khoá (Bắt đầu lại không chạy chồng 2 bộ luồng)
            for t in getattr(self, "_prep_threads", []):
                t.join(timeout=30)
            with self._tts_lock:
                if self.tts:
                    self.tts.release()
            self.lock.release()
            self.progress(None, "stopped", 0, 0, None)
            self.log("■ Đã dừng")

    def _main(self):
        while not self.stop_evt.is_set():
            try:
                self.cfg = load()
                self.llm = LLM(self.cfg, self.log)
                self.guard = resources.Guard(self.cfg, self.log, self.stop_evt)
                if self.tts is None:
                    self.tts = TTS(self.cfg, self.log)
            except Exception as e:
                self._fatal(f"Không nạp được cấu hình/mô hình (kiểm tra config.yaml): {e}")
                break
            if maintenance.due(self.cfg):
                try:
                    maintenance.daily(self.cfg, self.log)
                except Exception as e:
                    self.log(f"Bảo trì lỗi: {e}")
                maintenance.mark_done()
            waiting = [j for j in db.jobs() if j["status"] == "review"]
            if waiting:
                # làm xong từng job: có job chờ duyệt thì dừng cả hàng đợi tới khi được duyệt
                w = waiting[0]
                if getattr(self, "_waiting_for", None) != w["id"]:
                    self.log(f"✋ Hàng đợi tạm dừng: chờ duyệt kịch bản job {w['id']} – {w['topic']}")
                    self._waiting_for = w["id"]
                self.progress(w["id"], "review_wait", 0, 0, None)
                self.stop_evt.wait(5)
                continue
            self._waiting_for = None
            # hàng đợi gần hết (≤ min_queue chủ đề chờ) → tự nghiên cứu thêm 5 chủ đề, giữa hai job
            # (không tìm thêm khi đang có mốc "dừng sau…")
            # đếm sau khi đã chọn job sắp chạy – job đó không còn là "chờ"
            job = db.next_job() or self._auto_retry_failed()
            if topic_scout.due(self.cfg, pending=1 if job else 0) and not db.kv_get("stop_after") \
                    and not self._notebook_broken():
                try:
                    self.llm.role = "text"      # chọn chủ đề cần suy luận tốt – 8B hay trùng/giật tít
                    topic_scout.scout(self.cfg, self.llm, self.log)
                    self.changed()
                except Exception as e:
                    db.kv_set("topic_scout_last", time.time())
                    self.log(f"Tự tìm chủ đề lỗi: {e}")
            job = db.next_job()
            if job is None:
                self.progress(None, "idle", 0, 0, None)
                self.stop_evt.wait(10)
                continue
            try:
                self.guard.check_disk(new_job=True)
            except errors.FatalError as e:
                self._fatal(str(e))
                break
            if not db.claim_job(job["id"], "running"):
                continue                    # luồng chuẩn bị vừa nhận job này
            self.changed()
            try:
                self._run(job["id"])
                db.update_job(job["id"], status="done", stage="done", error=None)
            except AwaitReview:
                pipeline.pause_all(job["id"])
                db.update_job(job["id"], status="review")
                self.log(f"✋ Job {job['id']} chờ bạn duyệt kịch bản (chế độ Quality)")
            except Stopped:
                pipeline.pause_all(job["id"])
                db.update_job(job["id"], status="queued")
                self.log("⏸ Đã dừng – session đã lưu, bấm Bắt đầu để làm tiếp")
            except Exception as e:
                pipeline.pause_all(job["id"])
                if self.stop_evt.is_set() and not db.kv_get("fatal"):
                    # tiến trình con bị kết thúc do Dừng ngay – không tính là lỗi
                    db.update_job(job["id"], status="queued")
                    self.log("⏸ Đã dừng ngay – session đã lưu")
                else:
                    self._handle_error(job, e)
                    if self.stop_evt.is_set() and db.kv_get("fatal"):
                        break
            else:
                # job đã xong – lỗi ở phần báo cáo phía sau không được kéo job về hàng đợi
                try:
                    t = pipeline.view(job["id"])
                    self.log(f"✔ Xong: {job['topic']} – mất {pipeline.fmt(t['active_sec'])} chạy thật "
                             f"(tổng {pipeline.fmt(t['wall_sec'])})")
                    events.push("done", {"job": job["id"], "topic": job["topic"]})
                except Exception as e:
                    self.log(f"⚠ Job {job['id']} đã xong nhưng lỗi khi báo cáo: {e}")
                if self._stop_after_reached(job["id"]):
                    db.kv_set("stop_after", None)
                    self.log(f"🏁 Đã làm xong tới chủ đề #{job['id']} – dừng hàng đợi theo yêu cầu")
                    self.stop_evt.set()
                    self.changed()
                    break
            self.changed()

    # ------------------------------------------------------------ luồng chuẩn bị
    PREP_STAGES = ("research", "script", "tts", "image")

    def _prep_loop(self):
        """Trong lúc luồng chính làm job hiện tại, chạy trước research → script → tts → ảnh của job kế tiếp
        (Flow luôn có cảnh để tạo). Xong để lại 'queued' ở bước render cho luồng chính làm tiếp."""
        me = copy.copy(self)
        me._prep = True
        while not self.stop_evt.is_set():
            try:
                self._prep_once(me)
            except Exception as e:
                self.log(f"⚠ Luồng chuẩn bị lỗi: {e!r}")
                self.stop_evt.wait(10)

    def _prep_once(self, me):
        if True:
            cfg = load()
            # Prefetch nâng lên 3 (giữ sẵn 2 dự án gối đầu liên tục ở hàng chờ)
            prefetch = int((cfg.get("worker") or {}).get("prefetch", 3))
            rows = db.jobs()
            ready = [j for j in rows if j["status"] == "queued" and j["stage"] not in self.PREP_STAGES]
            busy = any(j["status"] == "running" for j in rows)
            cand = [j for j in rows if j["status"] == "queued" and j["stage"] in self.PREP_STAGES]
            if not busy or len(ready) >= prefetch or not cand or any(j["status"] == "review" for j in rows):
                self.stop_evt.wait(3)
                return
            job = cand[0]
            me.cfg, me.llm = cfg, LLM(cfg, self.log)   # tạo trước khi nhận job: lỗi ở đây không kẹt job 'preparing'
            me.guard = resources.Guard(cfg, self.log, self.stop_evt)
            if not db.claim_job(job["id"], "preparing"):
                self.stop_evt.wait(1)       # luồng khác vừa nhận – không quay vòng liên tục
                return
            self.log(f"⏩ Chuẩn bị trước job {job['id']}: {job['topic']}")
            self.changed()
            try:
                me._run(job["id"], stop_at="render")
                db.update_job(job["id"], status="queued", error=None)
                self.log(f"⏩ Job {job['id']} đã xong ảnh – chờ luồng chính dựng video")
            except AwaitReview:
                pipeline.pause_all(job["id"])
                db.update_job(job["id"], status="review")
            except Stopped:
                pipeline.pause_all(job["id"])
                db.update_job(job["id"], status="queued")
            except Exception as e:
                pipeline.pause_all(job["id"])
                if self.stop_evt.is_set() and not db.kv_get("fatal"):
                    db.update_job(job["id"], status="queued")
                else:
                    me._handle_error(job, e)
            self.changed()

    def _fatal(self, msg):
        db.kv_set("fatal", msg)
        self.log(f"⛔ LỖI NGHIÊM TRỌNG – dừng cả hàng đợi: {msg}")
        self.stop_evt.set()
        self.changed()

    def _handle_error(self, job, e):
        kind = errors.classify(e)
        attempts = job["attempts"] + 1
        self.log(f"✖ Lỗi job {job['id']} [{errors.LABEL[kind]}]: {e}\n{traceback.format_exc(limit=3)}")
        if kind == errors.FATAL:
            db.update_job(job["id"], status="queued", error=str(e)[:2000])
            self._fatal(str(e)[:300])
            return
        failed = attempts >= errors.MAX_ATTEMPTS[kind]
        db.update_job(job["id"], attempts=attempts, error=f"[{kind}] {str(e)[:2000]}",
                      status="failed" if failed else "queued")
        if kind == errors.TRANSIENT and self._prep:
            # luồng chuẩn bị: giải phóng GPU / kill tiến trình sẽ làm hỏng job luồng chính đang chạy → chỉ chờ
            self.stop_evt.wait(60)
        elif kind == errors.TRANSIENT:
            self.log("Phục hồi: giải phóng GPU rồi thử lại sau 60s")
            with self._tts_lock:            # luồng chuẩn bị có thể đang đọc bằng TTS dùng chung
                if self.tts:
                    self.tts.release()
            resources.kill_stale()
            resources.free_torch()
            self.stop_evt.wait(60)
        else:
            self.stop_evt.wait(20)

    def _job(self, jid):
        return db.q("SELECT * FROM jobs WHERE id=?", (jid,))[0]

    def _run(self, jid, stop_at=None):
        work = path(self.cfg.work_dir) / f"job_{jid}"
        work.mkdir(parents=True, exist_ok=True)
        job = self._job(jid)
        stage = job["stage"] if job["stage"] in STAGES else "research"
        if not job["facts"] and stage == "script" and not db.scenes(jid):
            stage = "research"
        v = json.loads(job.get("versions") or "{}")
        if not v.get("built_at") and not v.get("app"):
            db.update_job(jid, versions=json.dumps({**maintenance.versions(self.cfg), **v}, ensure_ascii=False))
        style = styles.lock(jid, None, self.cfg)
        if style == "nikola":
            why = nikola.available(self.cfg)
            if why:
                raise RuntimeError(f"Kiểu dựng Nikola chưa sẵn sàng: {why}")
        self.log(f"=== Job {jid}: {job['topic']} (từ bước {stage}, kiểu {style})")
        self.man = artifacts.Manifest(work)
        complexity.calibrate(CALIB)
        # chỉ báo file còn được cảnh dùng tới (ảnh đã bị loại / bị thay bằng lượt mới thì không quan trọng)
        used = {Path(s[k]).name for s in db.scenes(jid) for k in ("image", "audio", "clip") if s.get(k)}
        bad = [b for b in self.man.check_all() if b in used]
        if bad:
            self.log(f"Artifact hỏng/không khớp checksum sau lần chạy trước: {bad[:8]} → sẽ làm lại")
        if STAGES.index(stage) > STAGES.index("script"):
            self._youtube_start(jid)
        while stage != "done" and stage != stop_at:
            self._check()
            self.guard.check_disk()
            self.guard.check_thermal()
            # model theo bước: research = việc vặt (8B), script = viết/chia cảnh (model chữ lớn), còn lại = nhìn ảnh (VL 8B)
            self.llm.role = {"research": "light", "script": "text"}.get(stage, "vision")
            pipeline.start(jid, stage)
            events.push("step", {"job": jid, "stage": stage})
            if stage == "tts":
                with self._tts_lock:
                    getattr(self, f"_st_{stage}")(jid, work)
            else:
                getattr(self, f"_st_{stage}")(jid, work)
            pipeline.end(jid, stage)
            if stage == "script":
                self._youtube_start(jid)
            job = self._job(jid)
            if job["stage"] == stage:      # bước tự đổi stage khi cần quay lại (repair)
                stage = STAGES[STAGES.index(stage) + 1]
                db.update_job(jid, stage=stage)
            else:
                stage = job["stage"]
        if stage == "done" and not self.cfg.keep_work:
            shutil.rmtree(work, ignore_errors=True)

    # ------------------------------------------------------------ stages
    def _youtube_start(self, jid):
        """Bộ đăng YouTube (title/description VI+EN, thumbnail) – luồng phụ, song song với các bước làm video."""
        if (self.cfg.get("youtube") or {}).get("enabled", True):
            if youtube.start(jid, self.cfg, self.log, LLM(self.cfg, self.log)):
                self.log("📺 Bắt đầu tạo bộ đăng YouTube (chạy song song)")

    def _st_package(self, jid, work):
        """Gom mọi thứ của job vào một thư mục riêng projects/<id>_<tên>/ (video, kịch bản, ảnh, giọng, báo cáo)."""
        mins = float(youtube.conf(self.cfg)["wait_minutes"])
        youtube.wait(jid, mins * 60)
        try:
            sc = db.scenes(jid)
            offs = self._offsets(sc)[0] if sc and all(s.get("clip") for s in sc) else None
            youtube.refresh(jid, self.cfg, offs)      # thêm mốc thời gian chương thật vào description
        except Exception as e:
            self.log(f"📺 Cập nhật mô tả YouTube lỗi: {e}")
        if (self.cfg.get("youtube") or {}).get("enabled", True):
            try:
                youtube.make_subtitles(jid, self.cfg, self.log, self.llm)   # phụ đề VI + EN cho mục "Phụ đề"
            except Exception as e:
                self.log(f"💬 Tạo phụ đề YouTube lỗi: {e}")
        folder = packager.package(self.cfg, jid, work, self.log)
        db.update_job(jid, folder=str(folder))

    def _st_research(self, jid, work):
        job = self._job(jid)
        if job["facts"]:
            return
        self.progress(jid, "research", 0, 1, None)
        self.llm.ensure()
        topic = research.clean_topic(job["topic"], self.cfg, self.llm, self.log)
        pages = []
        pipeline.sub(jid, "web")
        try:
            pages = websearch.collect(topic, self.cfg, self.llm, self.log)
        except Exception as e:
            self.log(f"Web research lỗi ({e}) → dùng tài liệu local + kiến thức model, diễn đạt thận trọng")
        pipeline.sub(jid, "web", "done", note=f"{len(pages)} nguồn")
        if not pages and websearch.rcfg(self.cfg)["mode"] != "offline":
            self.log("⚠ Không lấy được nguồn web (mất mạng hoặc 0 kết quả) – fact sheet sẽ diễn đạt thận trọng")
        pipeline.sub(jid, "factsheet")
        facts = research.fact_sheet(topic, self.cfg, self.llm, self.log, pages)
        pipeline.sub(jid, "factsheet", "done", note=f"{len(facts)} dữ kiện")
        db.update_job(jid, topic=topic, facts=json.dumps(facts, ensure_ascii=False))
        out = autoflow.job_dir(self.cfg, jid)
        (out / "web_pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=1), encoding="utf-8")
        websearch.sources_md(topic, pages, facts, out / "sources.md")

    def _st_script(self, jid, work):
        job = self._job(jid)
        if not db.scenes(jid):
            self.progress(jid, "script", 0, 1, None)
            facts = json.loads(job["facts"] or "[]")
            prog = lambda d, t: (self._check(), self.progress(jid, "script", d, t, None))  # noqa: E731
            nb = None
            out = autoflow.job_dir(self.cfg, jid)
            cache = out / "notebook_script.json"
            if cache.exists():
                # đã viết xong ở lần chạy trước (lỗi ở bước chia cảnh) → không bắt NotebookLM viết lại
                nb = json.loads(cache.read_text(encoding="utf-8"))
                self.log(f"Dùng lại kịch bản NotebookLM đã viết ({len(nb['chapters'])} chương)")
            elif (self.cfg.get("script") or {}).get("engine", "qwen") == "notebooklm":
                try:
                    nb = notebook.write_script(jid, job["topic"], facts, self.cfg, self.log, self._check, prog)
                    nb["topic"] = job["topic"]
                    out.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
                    (out / "script.md").write_text(notebook.script_md(nb), encoding="utf-8")
                except notebook.Unavailable as e:
                    if not (self.cfg.get("script") or {}).get("fallback_qwen", False):
                        # không quay về luồng cũ (Qwen tự viết – chất lượng kém hơn nhiều): báo lỗi tạm thời → job
                        # tự thử lại NotebookLM sau, các bước khác giữ nguyên
                        self.log(f"⚠ {e} → chờ rồi thử lại NotebookLM (không dùng Qwen viết kịch bản)")
                        raise RuntimeError(f"NotebookLM chưa sẵn sàng: {e}") from e
                    self.log(f"⚠ {e} → viết kịch bản bằng Qwen")
            if nb:
                pipeline.sub(jid, "nb_outline", "done")
                pipeline.sub(jid, "nb_chapters", "done", note=f"{len(nb['chapters'])} chương")
                pipeline.sub(jid, "split")
                outline, rows = director.from_narration(nb, facts, self.cfg, self.llm, self.log, prog)
                pipeline.sub(jid, "split", "done", note=f"{len(rows)} cảnh")
                # Kiểm tra nếu các cảnh chưa có image_prompt (ví dụ không dùng Gemini hoặc Gemini lỗi)
                missing_prompts = [i for i, r in enumerate(rows) if not r.get("image_prompt")]
                img_mode = (self.cfg.get("script") or {}).get("image_prompts", "gemini")
                if missing_prompts and img_mode == "notebooklm":
                    self.log(f"NotebookLM: viết bổ sung prompt ảnh cho {len(missing_prompts)} cảnh")
                    pipeline.sub(jid, "nb_img", total=len(rows))
                    n = notebook.image_prompts(jid, outline, rows, self.cfg, self.log, self._check)
                    pipeline.sub(jid, "nb_img", "done", done=n, total=len(rows))
                elif missing_prompts:
                    # Tự động điền prompt nét vẽ whiteboard cơ bản cho các cảnh còn thiếu
                    style_tail = self.cfg.bible.get("image", {}).get("style") or "minimalist hand-drawn sketch, notion-style doodle, dark grey pencil line art on warm beige paper background #F5EBD7"
                    for i in missing_prompts:
                        vc = rows[i].get("visual_concept") or rows[i].get("narration", "")[:60]
                        rows[i]["image_prompt"] = f"{vc}, {style_tail}"
                    pipeline.sub(jid, "nb_img", "done", done=len(rows), total=len(rows))
                else:
                    pipeline.sub(jid, "nb_img", "done", done=len(rows), total=len(rows))
            else:
                outline, rows = director.build(job["topic"], facts, self.cfg, self.llm, self.log, prog)
            db.insert_scenes(jid, rows)
            db.update_job(jid, outline=json.dumps(outline, ensure_ascii=False))
            if (db.kv_get("mode") or self.cfg.get("mode")) == "quality":
                db.update_job(jid, stage="tts")
                raise AwaitReview()

    def _ok(self, p, kind="file") -> bool:
        """Artifact dùng lại được: có trong manifest và khớp checksum; file cũ chưa có manifest thì phải
        decode được mới nhận (và ghi vào manifest)."""
        if not p:
            return False
        if self.man.ok(p):
            return True
        if Path(p).name not in self.man.data and artifacts.valid(p):
            self.man.record(p, kind)
            return True
        return False

    def _speak_scene(self, jid, s, work, budget):
        pros = self.cfg.bible["prosody"]
        d = db.scene_data(s)
        p = pros.get(d.get("emotion", "calm"), pros["calm"])
        pause = p["pause"] + (0.25 if d.get("importance", 0) >= 0.8 else 0)
        lead = min(0.8, max(0.0, float(d.get("pause_before", 0) or 0)))
        out = work / f"scene_{s['idx']:03}_{db.scene_uid(jid, s)}.wav"
        d = db.scene_data(s)
        for attempt in range(budget + 1):
            total, speech, lead_ms = self.tts.speak(s["narration"], out, speed=p["speed"], pause=pause,
                                                    pause_before=lead)
            aq = audio_qc.check(out, s["narration"], speech, self.cfg.language, self.tts, self.cfg)
            if aq["pass"] or attempt == budget:
                break
            self.log(f"Giọng cảnh {s['idx']}: {', '.join(aq['issues'])} → đọc lại")
        if not aq["pass"]:
            self.log(f"Giọng cảnh {s['idx']}: hết lượt, giữ bản cuối ({', '.join(aq['issues'])})")
        d["speech_ms"] = speech
        d["asr_words"] = aq.pop("words", [])
        self.man.record(out, "audio")
        db.update_scene(jid, s["idx"], audio=str(out), duration_ms=total, lead_ms=lead_ms, clip=None,
                        tts_tries=(s.get("tts_tries") or 0) + attempt, audio_qc=json.dumps(aq, ensure_ascii=False),
                        data=json.dumps(d, ensure_ascii=False))

    def _auto_retry_failed(self, max_auto=2, cool=1800):
        """Hàng đợi trống → tự xếp lại tối đa 3 job lỗi "tạo lại được" (lỗi ≥30 phút trước, mỗi job tự xếp lại ≤2 lần)."""
        n = 0
        for j in db.jobs():
            if n >= 3:
                break
            if j["status"] != "failed" or time.time() - (j.get("updated_at") or 0) < cool:
                continue
            if not (j.get("error") or "").startswith(("[regenerable]", "[transient]")):
                continue
            v = json.loads(j.get("versions") or "{}")
            if v.get("auto_retry", 0) >= max_auto:
                continue
            v["auto_retry"] = v.get("auto_retry", 0) + 1
            db.update_job(j["id"], versions=json.dumps(v, ensure_ascii=False))
            db.retry_job(j["id"])
            self.log(f"↻ Tự xếp lại job {j['id']} (lần {v['auto_retry']}/{max_auto}): {j['topic']}")
            n += 1
        if n:
            self.changed()
        return db.next_job()

    def _notebook_broken(self):
        """3 job lỗi gần nhất đều do NotebookLM → ngưng tìm chủ đề mới (chủ đề mới cũng sẽ chết ngay)."""
        failed = sorted((j for j in db.jobs() if j["status"] == "failed"),
                        key=lambda j: j.get("updated_at") or 0, reverse=True)[:3]
        recent = [j for j in failed if time.time() - (j.get("updated_at") or 0) < 3600]
        if len(recent) == 3 and all("NotebookLM" in (j.get("error") or "") for j in recent):
            if not getattr(self, "_nb_broken_logged", False):
                self.log("⏸ Tạm ngưng tìm chủ đề mới: NotebookLM lỗi liên tiếp – kiểm tra tab NotebookLM")
                self._nb_broken_logged = True
            return True
        self._nb_broken_logged = False
        return False

    def _tts_for_job(self, jid):
        """Khoá engine giọng theo job: job đã bắt đầu đọc bằng giọng nào thì đọc hết bằng giọng đó (đổi config giữa
        chừng không làm video lẫn 2 giọng)."""
        job = self._job(jid)
        v = json.loads(job.get("versions") or "{}")
        if not v.get("tts_engine"):
            v["tts_engine"], v["tts_voice"] = voices.default(self.cfg).split("|", 1)
            db.update_job(jid, versions=json.dumps(v, ensure_ascii=False))
        eng, voice = v["tts_engine"], v.get("tts_voice") or ""
        if self.tts is None or (self.tts.engine, self.tts.voice_sel) != (eng, voice):
            if self.tts is not None:
                self.tts.release()
            self.tts = TTS(self.cfg, self.log, engine=eng, voice=voice)
            self.log(f"Job {jid}: giọng đọc {eng} {voice}".rstrip())

    def _st_tts(self, jid, work):
        # Nạp trước prompt ảnh vào Flow Bridge ngay khi có kịch bản (chạy song song 100% cùng TTS)
        try:
            if (self.cfg.image.get("provider") or "local") == "flowbridge":
                sc = db.scenes(jid)
                open_uids = {t["uid"] for t in flowbridge.open_tasks(jid)}
                todo = [s for s in sc if db.scene_uid(jid, s) not in open_uids and not self._ok(s.get("image"), "image")]
                if todo:
                    items = [{"idx": s["idx"], "uid": db.scene_uid(jid, s),
                              "prompt": autoflow.one_line(s.get("visual") or db.scene_data(s).get("image_prompt", s["narration"][:100]), 2400)}
                             for s in todo]
                    flowbridge.enqueue(jid, 1, items)
                    pipeline.sub(jid, "flow_parallel", "running", done=0, total=len(items), note=f"{max(1, len(flowbridge.active_workers()))} profile đang vẽ")
                    self.log(f"🖼 Nạp trước {len(items)} prompt vào Flow – các profile Chrome bắt đầu tạo ảnh song song cùng TTS!")
        except Exception as e:
            self.log(f"⚠ Nạp trước prompt Flow lỗi: {e}")
        self._tts_for_job(jid)
        budget = self._budget("tts", 2)
        # Whisper (Audio QC) chạy GPU: thiếu VRAM thì unload LLM (tự load lại khi cần)
        self.guard.ensure_vram(self.cfg.resources.get("vram_tts_gb", 4), llm=self.llm)
        for _pass in range(3):
            sc = db.scenes(jid)
            for s in sc:
                self._check()
                if self._ok(s["audio"], "audio") and s["duration_ms"]:
                    continue
                # Cập nhật số ảnh Flow đã tạo song song
                try:
                    done_flow = len([x for x in db.scenes(jid) if self._ok(x.get("image"), "image")])
                    pipeline.sub(jid, "flow_parallel", "running", done=done_flow, total=len(sc), note=f"{done_flow}/{len(sc)} ảnh đã vẽ")
                except Exception:
                    pass
                self.progress(jid, "tts", s["idx"], len(sc), None)
                self._speak_scene(jid, s, work, budget)
            # độ đồng nhất giọng giữa các cảnh (âm lượng / tốc độ / âm sắc lệch trung vị)
            sc = db.scenes(jid)
            stats = [json.loads(s["audio_qc"]) for s in sc if s.get("audio_qc")]
            if len(stats) == len(sc):
                odd = [i for i in audio_qc.consistency(stats) if (sc[i].get("tts_tries") or 0) < budget * 2]
                if odd:
                    self.log(f"Giọng lệch tông/âm lượng so với phần còn lại ở cảnh {odd} → đọc lại")
                    for i in odd:
                        self._speak_scene(jid, db.scenes(jid)[i], work, 0)
            if not self._extend_if_short(jid, work):
                break
        try:
            done_flow = len([x for x in db.scenes(jid) if self._ok(x.get("image"), "image")])
            pipeline.sub(jid, "flow_parallel", "done" if done_flow >= len(sc) else "running", done=done_flow, total=len(sc), note=f"{done_flow}/{len(sc)} ảnh")
        except Exception:
            pass
        self.tts.release()

    def _extend_if_short(self, jid, work) -> bool:
        """Đo thời lượng thật sau TTS; thiếu > 10% mục tiêu → thêm cảnh có giá trị (một lần mỗi job)."""
        job = self._job(jid)
        ol = json.loads(job["outline"] or "{}")
        sc = db.scenes(jid)
        total = sum(s["duration_ms"] or 0 for s in sc) / 1000
        target = self.cfg.target_minutes * 60
        self.log(f"Thời lượng thật sau TTS: {total / 60:.1f} phút / mục tiêu {target / 60:.0f} phút")
        words = sum(len((s["narration"] or "").split()) for s in sc)
        if not ol.get("extended") and total > 60 and words:
            wpm = save_wpm(words / (total / 60), self.cfg.words_per_minute)
            self.cfg["words_per_minute"] = wpm
            self.log(f"Tốc độ đọc thật {words / (total / 60):.0f} chữ/phút → job sau ước lượng theo {wpm}")
        pipeline.sub(jid, "voice", "done")
        pipeline.sub(jid, "extend", note=f"{total / 60:.1f}/{target / 60:.0f} phút")
        if ol.get("extended") or total >= target * 0.9 or not ol.get("chapters"):
            return False
        rows = [{**db.scene_data(s), **{k: s[k] for k in s if k != "data"}} for s in sc]
        by_ch = {}
        for r in rows:
            by_ch.setdefault(r["chapter"], []).append(r)
        vb = ol.get("bible", {})
        new = director.extend(ol, by_ch, target - total, json.loads(job["facts"] or "[]"), vb, self.cfg,
                              self.llm, self.log)
        merged = []
        for c in sorted(by_ch):
            merged += by_ch[c]
            for s in new.get(c, []):
                s["image_prompt"] = s.get("image_prompt", "")
                merged.append({"chapter": c, "narration": s["narration"], "visual": s["image_prompt"],
                               "data": json.dumps(s, ensure_ascii=False)})
        for r in merged:
            if "data" not in r or isinstance(r.get("data"), dict):
                r["data"] = json.dumps({k: v for k, v in r.items() if k not in db.SCENE_COLS},
                                       ensure_ascii=False)
        db.replace_scenes(jid, merged)
        ol["extended"] = True
        db.update_job(jid, outline=json.dumps(ol, ensure_ascii=False))
        return True

    def _nb_prompts_for_pending(self, jid):
        """Job đã qua bước kịch bản trước khi có tính năng này: cảnh chưa có ảnh mà prompt chưa do NotebookLM viết
        → nhờ NotebookLM viết lại (một lần), rồi mới xếp hàng tạo ảnh."""
        sc_cfg = self.cfg.get("script") or {}
        if sc_cfg.get("engine") != "notebooklm" or sc_cfg.get("image_prompts", "notebooklm") != "notebooklm":
            return
        ol = json.loads(self._job(jid)["outline"] or "{}")
        sc = db.scenes(jid)
        rows = [{**db.scene_data(s), "narration": s["narration"]} for s in sc]
        idx = [i for i, s in enumerate(sc) if not s.get("image") and rows[i].get("prompt_by") != "notebooklm"
               and not rows[i].get("prompt_by_tried")]
        if not idx:
            return
        self.log(f"NotebookLM: viết prompt ảnh cho {len(idx)} cảnh chưa có ảnh")
        notebook.image_prompts(jid, ol, rows, self.cfg, self.log, self._check, idx=idx)
        for i in idx:
            rows[i]["prompt_by_tried"] = True
            d = {k: v for k, v in rows[i].items() if k not in db.SCENE_COLS}
            db.update_scene(jid, sc[i]["idx"], visual=rows[i].get("image_prompt", ""),
                            data=json.dumps(d, ensure_ascii=False))
        flowbridge.cancel_job(jid)        # task cũ mang prompt Qwen → xếp lại bằng prompt mới

    def _st_image(self, jid, work):
        c = self.cfg.image
        if (c.get("provider") or "local") == "flowbridge":
            self._nb_prompts_for_pending(jid)
        vb = json.loads(self._job(jid)["outline"] or "{}").get("bible", {})
        unload = self.cfg.lmstudio.unload_for_images
        budget = self._budget("image", 3)
        provider = c.get("provider") or "local"
        if provider == "autoflow":
            return self._image_autoflow(jid, work, vb, budget)
        if provider == "flowbridge":
            return self._image_flowbridge(jid, work, vb, budget)
        self.guard.ensure_vram(self.cfg.resources.get("vram_image_gb", 9), llm=self.llm if unload else None,
                               tts=self.tts)
        for _round in range(budget + 2):
            pending = [s for s in db.scenes(jid) if not self._ok(s["image"], "image")]
            if not pending:
                return
            if unload:
                self.llm.unload()
            for s in pending:
                self._check()
                f = self._attempt_path(jid, work, s)
                if not self._ok(f, "image"):
                    self.progress(jid, "image", s["idx"], len(db.scenes(jid)), None)
                    self._generate(jid, s, f, vb, work, budget)
                    self.man.record(f, "image")
                if not c.qc:
                    db.update_scene(jid, s["idx"], image=str(f))
                elif not unload:
                    self._qc(jid, s["idx"], f, budget)
            if unload and c.qc:
                for s in pending:
                    self._check()
                    f = self._attempt_path(jid, work, s)
                    if self._ok(f, "image"):
                        self._qc(jid, s["idx"], f, budget)

    # ------------------------------------------------------------ Auto Flow (Google Flow qua extension)
    def _image_autoflow(self, jid, work, vb, budget):
        af = autoflow.cfg_of(self.cfg)
        while True:
            sc = db.scenes(jid)
            pending = [s for s in sc if not self._ok(s["image"], "image")]
            if not pending:
                autoflow.write_report(self.cfg, jid)
                db.kv_set("autoflow_ui", None)
                return
            st = autoflow.state(jid)
            in_round = {r["uid"] for r in st.get("rows", [])}
            pend_uids = {db.scene_uid(jid, s) for s in pending}
            received_uids = {st["rows"][r - 1]["uid"] for r in st.get("received_rows", [])}
            # cần lượt mới khi chưa có lượt, hoặc mọi cảnh còn thiếu đều đã nhận ảnh ở lượt này (đã QC, bị loại)
            if not st or not (pend_uids & in_round) or (pend_uids & in_round) <= received_uids:
                items = []
                for s in pending:
                    d = db.scene_data(s)
                    qc = json.loads(s["qc"]) if s.get("qc") else {}
                    fix = image.fix_instruction(qc, d) if qc and not qc.get("pass") else ""
                    simplify = s["image_tries"] >= budget - 1
                    items.append({"idx": s["idx"], "uid": db.scene_uid(jid, s),
                                  "prompt": image.build_prompt(d, vb, self.cfg, fix, simplify=simplify,
                                                               target="flow")})
                st = autoflow.export_round(self.cfg, jid, items)
                self.log(f"🖼 Auto Flow lượt {st['round']}: {len(items)} prompt → Import TXT file "
                         f"{st['txt']} vào Auto Flow rồi bấm START (ảnh lưu vào {af['inbox_dir']})")
                self.changed()
            db.kv_set("autoflow_ui", {"job": jid, "round": st["round"], "txt": st["txt"],
                                      "inbox": af["inbox_dir"], "exchange": str(autoflow.job_dir(self.cfg, jid)),
                                      "rows": len(st["rows"]), "received": len(st["received_rows"]),
                                      "since": st["exported_at"]})
            self._autoflow_poll(jid, work, budget, af)

    def _autoflow_poll(self, jid, work, budget, af):
        """Một nhịp chờ: nhận ảnh mới, QC; xử lý quét-ngay / fallback / hết giờ."""
        self._check()
        st = autoflow.state(jid)
        extra = db.kv_get(f"autoflow_scan_{jid}")
        if extra is not None:
            db.kv_set(f"autoflow_scan_{jid}", None)
        items = autoflow.scan(self.cfg, jid, extra.get("folder") if isinstance(extra, dict) else None)
        for it in items:
            self._check()
            row, f = autoflow.receive(self.cfg, jid, it)
            s = next((x for x in db.scenes(jid) if db.scene_uid(jid, x) == row["uid"]), None)
            if s is None or self._ok(s["image"], "image"):
                continue                      # cảnh đã có ảnh đạt (slot thừa) → bỏ qua
            self.man.record(f, "image")
            self.progress(jid, "image_wait", len(autoflow.state(jid)["received_rows"]), len(st["rows"]), str(f))
            self._qc(jid, s["idx"], f, budget)
            if not db.scenes(jid)[s["idx"]].get("image"):
                autoflow.reject(self.cfg, jid, f)
            autoflow.write_report(self.cfg, jid)
        st = autoflow.state(jid)
        waited = time.time() - st["exported_at"]
        missing = [r for r in st["rows"] if r["row"] not in st["received_rows"]]
        force = db.kv_get(f"autoflow_fallback_{jid}")
        if missing and (force or waited > af["wait_minutes"] * 60):
            if (force or af["fallback_local"]) and image.local_available(self.cfg):
                db.kv_set(f"autoflow_fallback_{jid}", None)
                self.log(f"⏱ Auto Flow: {len(missing)} ảnh chưa về sau {waited / 60:.0f} phút → tạo bằng FLUX local")
                self._fallback_local(jid, work, missing, budget)
                return
            if time.time() - st.get("warned_at", 0) > 600:
                self.log(f"⏱ Đang chờ {len(missing)} ảnh Auto Flow ({waited / 60:.0f} phút) – kiểm tra extension "
                         f"đã Import TXT {st['txt']} và bấm START chưa")
                st["warned_at"] = time.time()
                autoflow.save_state(jid, st)
        self.progress(jid, "image_wait", len(st["received_rows"]), len(st["rows"]), None)
        self.stop_evt.wait(af["poll_seconds"])

    # ------------------------------------------------------------ Flow Bridge (extension Chrome riêng)
    def _image_flowbridge(self, jid, work, vb, budget):
        af = autoflow.cfg_of(self.cfg)
        rnd = 0
        waiting_since = time.time()
        warned = 0
        self._flow_warned = {}
        nk = styles.of_job(jid, self.cfg) == "nikola"
        while True:
            self._check()
            af = autoflow.cfg_of(load())       # đọc lại config: đổi wait/fallback có hiệu lực ngay
            if nk:
                self._nikola_sync_partners(jid)
            sc = db.scenes(jid)
            pending = [s for s in sc if not self._ok(s["image"], "image")]
            if not pending:
                autoflow.write_report(self.cfg, jid)
                db.kv_set("flowbridge_ui", None)
                return
            # ảnh extension đã gửi về → QC
            for t in flowbridge.done_unprocessed(jid):
                if str(t["uid"]).startswith("thumb_"):
                    continue                  # ảnh nền thumbnail – luồng YouTube tự nhận
                flowbridge.mark_used(t["id"])
                s = next((x for x in db.scenes(jid) if db.scene_uid(jid, x) == t["uid"]), None)
                f = Path(t["image"])
                if s is None or self._ok(s["image"], "image") or not artifacts.valid(f):
                    continue
                self.man.record(f, "image")
                self._qc(jid, s["idx"], f, budget)
                if not db.scenes(jid)[s["idx"]].get("image"):
                    autoflow.reject(self.cfg, jid, f)
                autoflow.write_report(self.cfg, jid)
                waiting_since = time.time()
            # cảnh còn thiếu mà không còn task đang chờ → xếp lượt mới (prompt sửa theo lỗi QC)
            sc = db.scenes(jid)
            pending = [s for s in sc if not self._ok(s["image"], "image")]
            open_uids = {t["uid"] for t in flowbridge.open_tasks(jid)}
            errs = {}
            for t in db.q("SELECT uid FROM flow_tasks WHERE job_id=? AND status='error'", (jid,)):
                errs[t["uid"]] = errs.get(t["uid"], 0) + 1
            # cảnh có task lỗi ≥ 3 lần (vd Flow từ chối prompt): không bỏ cảnh (không còn FLUX local làm thay) mà gửi
            # lại bằng prompt đơn giản hơn; quá 9 lần thì mới thôi để không lặp vô hạn
            todo = [s for s in pending if db.scene_uid(jid, s) not in open_uids
                    and errs.get(db.scene_uid(jid, s), 0) < 9
                    and not (nk and nikola.is_partner(sc, s["idx"]))]   # Nikola: ảnh của đảo phải = ảnh màn
            if todo:
                rnd += 1
                items = []
                for s in todo:
                    d = db.scene_data(s)
                    qc = json.loads(s["qc"]) if s.get("qc") else {}
                    fix = image.fix_instruction(qc, d) if qc and not qc.get("pass") else ""
                    if qc.get("layout") and not qc["layout"].get("pass"):
                        fix = (fix + " " + "; ".join(qc["layout"]["issues"])).strip()
                    simple = s["image_tries"] >= budget - 1 or errs.get(db.scene_uid(jid, s), 0) >= 3
                    if nk:
                        act = nikola.act_of(sc, s["idx"])
                        partner = db.scene_data(sc[act[1]]) if len(act) == 2 else None
                        prompt = nikola.pair_prompt(d, partner, vb, fix, simple, af["prompt_max_chars"])
                    else:
                        prompt = image.build_prompt(d, vb, self.cfg, fix, target="flow", simplify=simple)
                    items.append({"idx": s["idx"], "uid": db.scene_uid(jid, s),
                                  "prompt": autoflow.one_line(prompt, af["prompt_max_chars"])})
                flowbridge.enqueue(jid, rnd, items)
                self.log(f"🖼 Flow Bridge: xếp {len(items)} prompt vào hàng đợi (lượt {rnd}) – extension trên tab "
                         f"flow.google.com sẽ tự tạo ảnh")
                waiting_since = time.time()
            st = flowbridge.summary(jid)
            db.kv_set("flowbridge_ui", {"job": jid, "pending": len(pending), **st})
            self.progress(jid, "image_wait", len(sc) - len(pending), len(sc), None)
            waited = time.time() - waiting_since
            if not st["extension_connected"] and time.time() - warned > 300:
                self.log("⚠ Chưa thấy extension AutoVideo Flow Bridge kết nối – mở Chrome, tab flow.google.com "
                         "(đã đăng nhập) và bật extension")
                warned = time.time()
            n_run = st.get("running", 0)
            # không còn profile Flow nào chạy (tắt / captcha / giới hạn) → khoảng này là CHỜ, không tính chạy thật
            now_t = time.time()
            if n_run == 0 and getattr(self, "_flow_idle_since", None):
                pipeline.add_wait(jid, "image", now_t - self._flow_idle_since, "không có profile Flow nào đang chạy")
            self._flow_idle_since = now_t if n_run == 0 else None
            if n_run != getattr(self, "_flow_profiles", None):
                self._flow_profiles = n_run
                names = ", ".join(w["name"] for w in st.get("workers", []) if not w["paused"])
                self.log(f"🖼 Flow: {n_run} profile đang tạo ảnh song song" + (f" ({names})" if names else ""))
            for w in st.get("workers", []):
                if w["paused"] and self._flow_warned.get(w["id"]) != w["paused"]:
                    self._flow_warned[w["id"]] = w["paused"]
                    self.log(f"⏸ Profile {w['name']} tạm dừng: {w['paused']} – các profile khác vẫn chạy")
            if st["paused"] and time.time() - warned > 300:
                self.log(f"⏸ Flow Bridge đang tạm dừng: {st['paused']} – xử lý trên tab Flow rồi bấm Tiếp tục")
                warned = time.time()
            force = db.kv_get(f"autoflow_fallback_{jid}")
            if force or waited > af["wait_minutes"] * 60:
                if af["fallback_local"] and image.local_available(self.cfg):
                    db.kv_set(f"autoflow_fallback_{jid}", None)
                    flowbridge.cancel_job(jid)
                    rows = [{"uid": db.scene_uid(jid, s), "row": 0} for s in pending]
                    self.log(f"⏱ Flow Bridge: {len(pending)} ảnh chưa có sau {waited / 60:.0f} phút → FLUX local")
                    self._fallback_local(jid, work, rows, budget)
                    continue
                if force:
                    db.kv_set(f"autoflow_fallback_{jid}", None)
                    self.log("Không có model ảnh local – tiếp tục chờ ảnh từ Google Flow")
                if time.time() - warned > 600:
                    self.log(f"⏱ Đã chờ ảnh Flow {waited / 60:.0f} phút ({len(pending)} cảnh) – kiểm tra extension "
                             "và tab flow.google.com")
                    warned = time.time()
            self.stop_evt.wait(af["poll_seconds"])

    def _fallback_local(self, jid, work, missing_rows, budget):
        vb = json.loads(self._job(jid)["outline"] or "{}").get("bible", {})
        uids = {r["uid"] for r in missing_rows}
        for s in db.scenes(jid):
            if db.scene_uid(jid, s) not in uids:
                continue
            while not self._ok(db.scenes(jid)[s["idx"]]["image"], "image"):
                self._check()
                cur = db.scenes(jid)[s["idx"]]
                f = self._attempt_path(jid, work, cur)
                self._generate(jid, cur, f, vb, work, budget)
                self.man.record(f, "image")
                self._qc(jid, cur["idx"], f, budget)
        st = autoflow.state(jid)
        if st:
            st["received_rows"] = sorted(set(st["received_rows"]) | {r["row"] for r in missing_rows})
            autoflow.save_state(jid, st)

    def _generate(self, jid, s, f, vb, work, budget):
        d = db.scene_data(s)
        tries = s["image_tries"]
        qc = json.loads(s["qc"]) if s.get("qc") else {}
        fix = image.fix_instruction(qc, d) if qc and not qc.get("pass") else ""
        simplify = tries >= budget - 1
        prev = db.scenes(jid)[s["idx"] - 1] if s["idx"] > 0 else None
        cv = self.cfg.get("canvas") or {}
        if (cv.get("enabled") and d.get("transition") == "draw_over" and tries < 2 and prev
                and prev["chapter"] == s["chapter"] and self._ok(prev["image"], "image")):
            c = self.cfg.image
            box = canvas.empty_area(Path(prev["image"]), c.width, c.height)
            mask = canvas.write_mask(box, c.width, c.height, work / f"mask_{db.scene_uid(jid, s)}.png")
            seed = image.generate(canvas.prompt(image.build_prompt(d, vb, self.cfg, fix)), f, self.cfg,
                                  init=Path(prev["image"]), mask=mask, strength=cv.get("strength", 0.95))
            d["new_area"] = box
        else:
            if simplify:
                self.log(f"Ảnh cảnh {s['idx']}: lượt cuối → đơn giản hoá bố cục")
            seed = image.generate(image.build_prompt(d, vb, self.cfg, fix, simplify=simplify), f, self.cfg)
            d.pop("new_area", None)
        d.setdefault("seeds", []).append(seed)
        db.update_scene(jid, s["idx"], data=json.dumps(d, ensure_ascii=False))

    @staticmethod
    def _attempt_path(jid, work, s):
        return work / f"scene_{s['idx']:03}_{db.scene_uid(jid, s)}_t{s['image_tries']}.png"

    def _qc(self, jid, idx, f, budget):
        all_sc = db.scenes(jid)
        s = all_sc[idx]
        prev = next((Path(p["image"]) for p in reversed(all_sc[:idx]) if self._ok(p["image"], "image")), None)
        self.progress(jid, "image", idx, len(all_sc), str(f))
        d = db.scene_data(s)
        res = image.check(f, d, prev, self.llm, self.cfg, d.get("speech_ms") or s["duration_ms"] or 15000)
        if styles.of_job(jid, self.cfg) == "nikola":
            # Nikola: thêm luật bố cục hai đảo (rãnh giữa trắng, đáy 18% trống, không nét nối hai đảo)
            lay = nikola.layout_check(f, two=len(nikola.act_of(all_sc, idx)) == 2)
            res["layout"] = lay
            if not lay["pass"]:
                res["pass"] = False
                res["failed_gates"] = list(res.get("failed_gates") or []) + ["layout"]
                res["issues"] = (str(res.get("issues") or "") + " | bố cục: " + "; ".join(lay["issues"])).strip()
        res["kind"] = image.failure_kind(res) if not res["pass"] else None
        res["path"] = str(f)
        res["new_area"] = d.get("new_area")
        old = json.loads(s["qc"]) if s.get("qc") else {}
        best = old.get("best")
        if not best or image.rank(res) > image.rank(best) or not self._ok(best.get("path"), "image"):
            best = {k: v for k, v in res.items() if k != "best"}
        res["best"] = best
        tries = s["image_tries"]
        if res["pass"]:
            db.update_scene(jid, idx, image=str(f), qc=json.dumps(res, ensure_ascii=False))
        elif tries + 1 >= budget:
            self.log(f"Ảnh cảnh {idx}: hết {budget} lượt, dùng bản tốt nhất ({best['overall']:.2f})")
            if best.get("new_area") != d.get("new_area"):
                d["new_area"] = best.get("new_area")
                db.update_scene(jid, idx, data=json.dumps(d, ensure_ascii=False))
            db.update_scene(jid, idx, image=best["path"], qc=json.dumps(best, ensure_ascii=False))
        else:
            self.log(f"Ảnh cảnh {idx} không đạt {res['failed_gates']} (nghĩa {res['semantic_score']:.2f}, "
                     f"đẹp {res['visual_quality_score']:.2f}, dễ vẽ {res['whiteboard_suitability_score']:.2f}): "
                     f"{res['issues'][:180]} → tạo lại")
            db.update_scene(jid, idx, image_tries=tries + 1, qc=json.dumps(res, ensure_ascii=False))

    # ------------------------------------------------------------ kiểu Nikola (bảng hai đảo)
    def _nikola_sync_partners(self, jid):
        """Đảo phải dùng chung ảnh với đảo trái của màn: ảnh màn đạt → gán cho cảnh đảo phải."""
        sc = db.scenes(jid)
        for act in nikola.acts(sc):
            if len(act) != 2:
                continue
            lead, part = sc[act[0]], sc[act[1]]
            if self._ok(lead["image"], "image") and part["image"] != lead["image"]:
                db.update_scene(jid, part["idx"], image=lead["image"], qc=lead["qc"], clip=None)
            elif not self._ok(lead["image"], "image") and part["image"]:
                db.update_scene(jid, part["idx"], image=None, clip=None)

    def _nikola_render(self, jid, work):
        sc = db.scenes(jid)
        n = nikola.conf(self.cfg)
        out_dir = work / "nikola"
        out_dir.mkdir(exist_ok=True)
        if not (out_dir / "preflight-stroke-story.json").exists():
            pf = nikola.preflight(self.cfg, out_dir / "preflight-stroke-story.json")
            if not pf.get("passed"):
                (out_dir / "preflight-stroke-story.json").unlink(missing_ok=True)
                raise RuntimeError("Nikola preflight không đạt: " + json.dumps(pf.get("checks", pf),
                                                                                 ensure_ascii=False)[:400])
            self.log("✍️ Nikola preflight đạt (engine nét, PyAV/OpenCV, render thử)")
        acts = nikola.acts(sc)
        todo = [a for a in acts if not all(self._ok(sc[i]["clip"], "clip") for i in a)]
        params = dict(db.kv_get(f"nikola_params_{jid}") or {})

        def one(act, check_sample=False):
            self._check()
            ss = [db.scenes(jid)[i] for i in act]
            name = f"act-{act[0] + 1:03}-{db.scene_uid(jid, ss[0])}"
            img = out_dir / f"{name}.png"
            shutil.copyfile(ss[0]["image"], img)
            parts = [{"narration": s["narration"], "audio_ms": render.duration_ms(Path(s["audio"])),
                      "lead_ms": s.get("lead_ms") or 0} for s in ss]
            ann = out_dir / f"{name}.annotation.json"
            ok, why = False, ""
            for shrink in (0.0, 0.02):
                ann_data = nikola.build_annotation(img, ann, name, parts, shrink)
                ok, why = nikola.check_regions(self.cfg, img, ann, out_dir / f"{name}.regions.json")
                if ok:
                    break
            if not ok:
                if len(act) == 2:
                    self.log(f"Màn {act[0] + 1}: vùng vẽ không đạt ({why[:160]}) → tạo lại ảnh màn")
                    for i in act:
                        db.update_scene(jid, i, image=None, clip=None, qc=None)
                    raise _RedoImage()
                self.log(f"Màn {act[0] + 1}: cảnh báo vùng vẽ: {why[:160]}")
            audio = out_dir / f"{name}.wav"
            nikola.concat_audio([Path(s["audio"]) for s in ss], audio)
            clip = out_dir / f"{name}.mp4"
            t0 = time.time()
            nikola.render_act(self.cfg, img, ann, audio, clip, params)
            if check_sample:
                rep = nikola.check_act(clip, img, ann_data)
                (out_dir / "sample-check.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                                                           encoding="utf-8")
                if not rep["pass"]:
                    return rep
            self.man.record(clip, "clip")
            for k, s in enumerate(ss):
                db.update_scene(jid, s["idx"], clip=str(clip))
            self.log(f"✍️ Màn {acts.index(act) + 1}/{len(acts)} ({len(act)} đảo) vẽ xong trong "
                     f"{time.time() - t0:.0f}s")
            return {"pass": True, "image": str(img)}

        # màn thử (representative shot) – chọn màn hai đảo đầu tiên, tự kiểm rồi mới render cả loạt
        if todo and not db.kv_get(f"nikola_sample_ok_{jid}"):
            sample = next((a for a in todo if len(a) == 2), todo[0])
            try:
                rep = one(sample, check_sample=True)
                if not rep["pass"]:
                    self.log(f"Màn thử chưa đạt: {rep['issues']} → đổi tham số (ink grid, tay cao hơn) và thử lại")
                    params = {"ink_path": "grid", "hand_height": int(n["hand_height"]) + 40}
                    db.kv_set(f"nikola_params_{jid}", params)
                    rep = one(sample, check_sample=True)
                    if not rep["pass"]:
                        self.log(f"Màn thử vẫn lỗi {rep['issues']} → vẫn tiếp tục (ghi vào báo cáo)")
                        for i in sample:
                            db.update_scene(jid, i, clip=None)
                        one(sample)
                self.log("✅ Màn thử đạt checklist Nikola → render các màn còn lại")
                db.kv_set(f"nikola_sample_ok_{jid}", True)
            except _RedoImage:
                db.update_job(jid, stage="image")
                return
            todo = [a for a in todo if a != sample]
        done = len(acts) - len(todo)
        redo = False
        workers = 1 if self.guard.throttled else max(1, int(n["parallel_renders"]))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(one, a) for a in todo]
            for fu in as_completed(futs):
                try:
                    r = fu.result()
                except _RedoImage:
                    redo = True
                    continue
                done += 1
                self.progress(jid, "render", done, len(acts), r.get("image"))
        if redo:
            db.update_job(jid, stage="image")

    def _nikola_offsets(self, sc):
        """Offset từng cảnh trên trục thời gian: đảo phải bắt đầu ngay sau giọng của đảo trái (cùng clip màn)."""
        offs, t = [0] * len(sc), 0
        for act in nikola.acts(sc):
            offs[act[0]] = t
            if len(act) == 2:
                offs[act[1]] = t + render.duration_ms(Path(sc[act[0]]["audio"]))
            t += render.duration_ms(Path(sc[act[0]]["clip"]))
        return offs, t

    def _nikola_assemble(self, jid, work):
        self.progress(jid, "assemble", 0, 1, None)
        sc = db.scenes(jid)
        out_dir = work / "nikola"
        out = self._output(jid)
        acts = nikola.acts(sc)
        offs, total = self._nikola_offsets(sc)
        rows = []
        for s in sc:
            d = db.scene_data(s)
            d["duration_ms"] = s["duration_ms"]
            d["lead_ms"] = s.get("lead_ms") or 0
            rows.append(d)
        # Nikola quy tắc 1: bản gốc, bản đưa vào TTS và phụ đề là 3 file riêng
        outline = json.loads(self._job(jid)["outline"] or "{}")
        (out_dir / "script.txt").write_text(notebook.script_md(outline) if outline.get("chapters") else
                                            "\n\n".join(s["narration"] for s in sc), encoding="utf-8")
        (out_dir / "narration.txt").write_text("\n\n".join(s["narration"] for s in sc), encoding="utf-8")
        cues = subtitles.cues_from_words(rows, offs)      # thời gian theo Whisper của giọng thật (quy tắc 3)
        subtitles.write_srt(cues, out_dir / "captions.srt")
        # chữ khoá: hiện ngay sau khi đảo tương ứng vẽ xong, đặt trên đảo (phía trên, ngoài vùng phụ đề)
        kw = {i: r.get("keyword") for i, r in enumerate(rows) if r.get("keyword")}
        if len(kw) < len(rows):
            kw.update({k: v for k, v in nikola.keywords(
                self.llm, [{"i": i, "narration": r["narration"]} for i, r in enumerate(rows) if i not in kw]).items()})
            for i, k in kw.items():
                if k and rows[i].get("keyword") != k:
                    d = json.loads(sc[i]["data"] or "{}")
                    d["keyword"] = k
                    db.update_scene(jid, i, data=json.dumps(d, ensure_ascii=False))
        kws, timeline = [], {"acts": [], "scenes": []}
        act_rows = []
        for ai, act in enumerate(acts):
            name = Path(sc[act[0]]["clip"]).stem
            ann = json.loads((out_dir / f"{name}.annotation.json").read_text(encoding="utf-8"))
            dur = render.duration_ms(Path(sc[act[0]]["clip"]))
            act_rows.append({"video": sc[act[0]]["clip"], "image": str(out_dir / f"{name}.png"), "durationMs": dur})
            timeline["acts"].append({"act": ai + 1, "scenes": act, "startMs": offs[act[0]], "durationMs": dur,
                                     "annotation": f"{name}.annotation.json"})
            W = ann["canvas"]["width"]
            for k, (i, el) in enumerate(zip(act, ann["elements"])):
                r = el["region"]
                done_ms = offs[act[0]] + el["reveal"]["startMs"] + el["reveal"]["durationMs"]
                end_ms = offs[act[0]] + dur
                timeline["scenes"].append({"scene": i, "startMs": offs[i], "island": el["label"],
                                           "drawStartMs": offs[act[0]] + el["reveal"]["startMs"],
                                           "drawEndMs": done_ms, "keyword": kw.get(i)})
                if kw.get(i):
                    # chữ khoá đặt vào dải giấy trống gần đảo, không đè icon/hình (trước đây cố định y=40)
                    cx, cy, fs = nikola.caption_slot(out_dir / f"{name}.png", r, W, kw[i])
                    kws.append({"text": kw[i], "start": done_ms, "end": end_ms, "x": cx, "y": cy, "size": fs})
        (out_dir / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
        ass = out_dir / "captions-and-titles.ass"
        nikola.write_ass(cues if self.cfg.get("burn_subtitles") else [], kws, ass)
        narration = out_dir / "narration.wav"
        nikola.concat_audio([Path(a["video"]) for a in act_rows], narration)
        master = out_dir / "final-stroke.mp4"
        nikola.finalize(self.cfg, out_dir, act_rows, narration, ass, master)
        # nhạc nền + chuẩn hoá loudness như luồng cổ điển
        moods = self.cfg.bible["music"]
        plan = []
        for ci, ch in enumerate(outline.get("chapters", [])):
            ms = sum(render.duration_ms(Path(s["audio"])) for s in sc if s["chapter"] == ci)
            if ms:
                plan.append((moods.get(ch["beat"], "calm"), ms))
        bgm = render.build_bgm(plan, work, self.cfg) if plan else None
        render.assemble([master], out, self.cfg, bgm, None)
        shutil.copyfile(out_dir / "captions.srt", out.with_suffix(".srt"))
        rep = nikola.verify_final(out, act_rows)
        (out_dir / "verification.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(("✅ Kiểm tra cuối Nikola đạt" if rep["pass"] else f"⚠ Kiểm tra cuối Nikola: {rep['issues']}")
                 + f" ({len(acts)} màn)")
        db.update_job(jid, output=str(out))

    def _st_render(self, jid, work):
        if styles.of_job(jid, self.cfg) == "nikola":
            return self._nikola_render(jid, work)
        all_sc = db.scenes(jid)
        sc = [s for s in all_sc if not self._ok(s["clip"], "clip")]
        wb = self.cfg.whiteboard
        budget = self._budget("whiteboard", 2)
        for s in sc:
            db.scene_uid(jid, s)

        def one(s):
            self._check()
            name = f"scene-{s['idx'] + 1:03}-{db.scene_uid(jid, s)}"
            img = work / f"{name}.png"
            shutil.copyfile(s["image"], img)
            d = db.scene_data(s)
            ann = work / f"{name}.annotation.json"
            clip = work / f"{name}-whiteboard.mp4"
            last = None
            for attempt in range(budget):
                grounded = None
                if attempt == 0:
                    try:
                        grounded = annotate.ground(img, d.get("drawing_order", []), self.llm)
                    except Exception as e:
                        self.log(f"Grounding cảnh {s['idx']} lỗi, dùng OpenCV: {e}")
                ann_data = annotate.build(img, ann, name, s["narration"], s["duration_ms"], wb.max_regions,
                                          grounded, new_area=d.get("new_area"), lead_ms=s.get("lead_ms") or 0)
                focus = self._focus(d, grounded, ann_data)
                fx = {"camera": d.get("camera"), "focus": focus, "transition": d.get("transition")}
                try:
                    t0 = time.time()
                    render.scene_clip(img, ann, Path(s["audio"]), clip, self.cfg, fx)
                    self.man.record(clip, "clip")
                    cx = (json.loads(s["qc"]).get("complexity") if s.get("qc") else None) or {}
                    els = ann_data["elements"]
                    with CALIB.open("a", encoding="utf-8") as fcal:
                        fcal.write(json.dumps({
                            "stroke_length_px": cx.get("stroke_length_px"), "objects": cx.get("objects"),
                            "estimated_draw_time": cx.get("estimated_draw_time"), "regions": len(els),
                            "draw_ms": sum(e["reveal"]["durationMs"] for e in els),
                            "scene_ms": ann_data["sceneDurationMs"], "render_s": round(time.time() - t0, 1),
                        }) + "\n")
                    db.update_scene(jid, s["idx"], clip=str(clip), render_tries=attempt)
                    return str(img)
                except Exception as e:
                    last = e
                    self.log(f"Render cảnh {s['idx']} lỗi (lần {attempt + 1}): {str(e)[-200:]} → thử lại bằng OpenCV")
            raise last

        done = len(all_sc) - len(sc)
        workers = 1 if self.guard.throttled else max(1, wb.parallel_renders)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fu in as_completed([ex.submit(one, s) for s in sc]):
                done += 1
                self.progress(jid, "render", done, len(all_sc), fu.result())

    @staticmethod
    def _focus(d, grounded, ann):
        w, h = ann["canvas"]["width"], ann["canvas"]["height"]
        target = (d.get("focus") or "").lower()
        for name, b in grounded or []:
            if target and (target in name.lower() or name.lower() in target):
                return ((b[0] + b[2]) / 2 / w, (b[1] + b[3]) / 2 / h)
        if grounded:
            name, b = grounded[0]
            return ((b[0] + b[2]) / 2 / w, (b[1] + b[3]) / 2 / h)
        return (0.5, 0.5)

    def _output(self, jid):
        out_dir = path(self.cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{jid:04}_{slug(self._job(jid)['topic'])}.mp4"

    def _offsets(self, sc):
        if sc and styles.of_job(sc[0]["job_id"], self.cfg) == "nikola":
            return self._nikola_offsets(sc)
        offs, t = [], 0
        for s in sc:
            offs.append(t)
            t += render.duration_ms(Path(s["clip"]))
        return offs, t

    def _st_assemble(self, jid, work):
        if styles.of_job(jid, self.cfg) == "nikola":
            return self._nikola_assemble(jid, work)
        self.progress(jid, "assemble", 0, 1, None)
        sc = db.scenes(jid)
        outline = json.loads(self._job(jid)["outline"] or "{}")
        offs, total = self._offsets(sc)
        out = self._output(jid)

        rows = []
        for s, off in zip(sc, offs):
            d = db.scene_data(s)
            d["duration_ms"] = s["duration_ms"]
            d["lead_ms"] = s.get("lead_ms") or 0
            rows.append(d)
        # timing phụ đề luôn lấy từ audio cuối cùng (timestamp từng từ), không dùng lại timing cũ
        cues = subtitles.cues_from_words(rows, offs)
        subtitles.write_srt(cues, out.with_suffix(".srt"))
        ass = None
        if self.cfg.get("burn_subtitles"):
            ass = out.with_suffix(".ass")
            subtitles.write_ass(cues, ass)

        moods = self.cfg.bible["music"]
        plan = []
        for ci, ch in enumerate(outline.get("chapters", [])):
            ms = sum(render.duration_ms(Path(s["clip"])) for s in sc if s["chapter"] == ci)
            if ms:
                plan.append((moods.get(ch["beat"], "calm"), ms))
        bgm = render.build_bgm(plan, work, self.cfg) if plan else None
        render.assemble([s["clip"] for s in sc], out, self.cfg, bgm, ass)
        if ass:
            ass.unlink(missing_ok=True)
        db.update_job(jid, output=str(out))

    def _st_qc(self, jid, work):
        self.progress(jid, "qc", 0, 1, None)
        job = self._job(jid)
        sc = db.scenes(jid)
        out = Path(job["output"])
        outline = json.loads(job["outline"] or "{}")
        offs, total = self._offsets(sc)
        tech = video_qc.technical(out, total)
        q = self.cfg.bible["quality"]
        rounds = self._budget("final_scene", q.get("repair_rounds", 1))

        # Tầng 1 – Hard Gate: chặn xuất bất kể điểm
        for s in sc:   # artifact cũ hợp lệ nhưng chưa có trong manifest (resume) → ghi nhận trước khi kiểm
            self._ok(s.get("clip"), "clip")
            self._ok(s.get("audio"), "audio")
        fails = video_qc.hard_gate(out, sc, self.man, total, tech, outline)
        if fails:
            self.log("⛔ Hard Gate: " + "; ".join(f"{f['type']}: {f.get('detail', '')}" for f in fails))
            db.update_job(jid, scores=json.dumps({"hard_gate": fails, "tech": tech}, ensure_ascii=False))
            if job["repairs"] >= rounds + 1:
                raise RuntimeError("Hard Gate vẫn lỗi sau khi sửa: " + "; ".join(f["type"] for f in fails))
            self._repair(jid, work, {video_qc.REPAIR_ACTION.get(f["type"], "reassemble"): f.get("scenes", [])
                                     for f in fails}, job)
            return

        # Tầng 2 – điểm chất lượng
        bad_render = [s["idx"] for s in sc if not video_qc.render_ok(Path(s["clip"]), Path(s["image"]), work)]
        bad_frames = video_qc.visual_spot_check(sc, offs, out, work, self.llm)
        scores = video_qc.score(outline, sc, tech, bad_render, bad_frames)
        scores.update(tech=tech, bad_render=bad_render, bad_frames=bad_frames, repairs=job["repairs"],
                      hard_gate="pass")
        db.update_job(jid, scores=json.dumps(scores, ensure_ascii=False))
        self.log(f"Điểm video: {scores['total']} " + json.dumps({k: scores[k] for k in video_qc.PARTS}))
        if scores["total"] >= q["video_min_score"] or job["repairs"] >= rounds:
            return
        actions = {}
        for s in sc:
            qc = json.loads(s["qc"]) if s.get("qc") else {}
            aq = json.loads(s["audio_qc"]) if s.get("audio_qc") else {}
            if qc and not qc.get("pass"):
                kind = qc.get("kind") or image.failure_kind(qc)
                actions.setdefault(video_qc.REPAIR_ACTION.get(kind, "regen_image"), []).append(s["idx"])
            if aq.get("pronunciation_suspects") or (aq and not aq.get("pass")):
                actions.setdefault("redo_tts", []).append(s["idx"])
        for i in set(bad_render) | set(bad_frames):
            actions.setdefault("rerender", []).append(i)
        if tech.get("lufs") is not None and abs(tech["lufs"] - render.mix_preset(self.cfg)["lufs"]) > 3:
            actions["remix"] = []
        cap = max(1, int(len(sc) * 0.25))
        actions = {a: sorted(set(v))[:cap] for a, v in actions.items()}
        if actions:
            self._repair(jid, work, actions, job)

    def _repair(self, jid, work, actions: dict, job):
        """Chỉ làm lại phần bị ảnh hưởng, theo loại lỗi."""
        self.log("🔧 Auto repair: " + ", ".join(f"{a} {v}" if v else a for a, v in actions.items()))
        stage = "assemble"
        sc = db.scenes(jid)
        budget = self._budget("image", 3)
        if styles.of_job(jid, self.cfg) == "nikola":
            # Nikola: một màn = một ảnh + một clip cho cả hai đảo → sửa cả màn
            actions = {a: sorted({j for i in v for j in nikola.act_of(sc, i)}) for a, v in actions.items()}
            if any(a in actions for a in ("regen_image", "simplify_image", "rerender", "retime", "redo_tts")):
                db.kv_set(f"nikola_sample_ok_{jid}", None)
        for a, idxs in actions.items():
            if a in ("regen_image", "simplify_image"):
                for i in idxs:
                    tries = budget - 1 if a == "simplify_image" else 0   # lượt cuối = đơn giản hoá
                    db.update_scene(jid, i, image=None, image_tries=tries, qc=None, clip=None)
                stage = "image" if stage not in ("tts",) else stage
            elif a == "redo_tts":
                for i in idxs or range(len(sc)):
                    db.update_scene(jid, i, audio=None, duration_ms=None, clip=None)
                stage = "tts"
            elif a in ("rerender", "retime"):
                for i in idxs or range(len(sc)):
                    db.update_scene(jid, i, clip=None)
                stage = stage if stage in ("tts", "image") else "render"
            elif a == "rewrite_chapter":
                raise RuntimeError("Nội dung có lỗi nghiêm trọng cần viết lại chương – hãy Duyệt kịch bản "
                                   "(chế độ Quality) hoặc Chạy lại job")
        # remix / reassemble: chỉ cần ghép lại (stage mặc định = assemble)
        db.update_job(jid, repairs=job["repairs"] + 1, stage=stage)
