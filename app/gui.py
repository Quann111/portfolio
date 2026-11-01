import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import api, db, events, packager, resources
from .config import CONFIG_PATH, ROOT, load
from .worker import Worker

STAGE_VI = {"research": "Research", "script": "Đạo diễn + kịch bản", "tts": "Giọng đọc", "image": "Tạo ảnh + QC",
            "render": "Dựng whiteboard", "assemble": "Ghép video", "qc": "Kiểm tra cuối", "done": "Xong",
            "idle": "Chờ chủ đề", "stopped": "Đã dừng", "review_wait": "Chờ bạn duyệt kịch bản (bấm Duyệt kịch bản)",
            "image_wait": "Chờ ảnh từ Google Flow"}
STATUS_VI = {"queued": "Chờ", "running": "Đang chạy", "done": "Xong", "failed": "Lỗi", "review": "Chờ duyệt"}


class ReviewDialog(QDialog):
    """Duyệt / sửa kịch bản trước khi sản xuất (chế độ Quality)."""

    def __init__(self, parent, jid):
        super().__init__(parent)
        self.jid = jid
        job = db.q("SELECT * FROM jobs WHERE id=?", (jid,))[0]
        ol = json.loads(job["outline"] or "{}")
        self.setWindowTitle(f"Duyệt kịch bản – {ol.get('title', job['topic'])}")
        self.resize(1300, 800)
        info = QLabel(f"<b>Hook:</b> {ol.get('hook', '')}<br><b>Chương:</b> " +
                      " · ".join(f"[{c['beat']}] {c['title']}" for c in ol.get("chapters", [])))
        info.setWordWrap(True)
        self.sc = db.scenes(jid)
        self.t = QTableWidget(len(self.sc), 4)
        self.t.setHorizontalHeaderLabels(["Mục đích", "Lời đọc (sửa được)", "Prompt ảnh (sửa được)", "Thứ tự vẽ"])
        self.t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.t.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.t.setWordWrap(True)
        for r, s in enumerate(self.sc):
            d = db.scene_data(s)
            for c, v in enumerate([d.get("purpose", ""), s["narration"], s["visual"],
                                   ", ".join(d.get("drawing_order", []))]):
                it = QTableWidgetItem(v)
                if c in (0, 3):
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.t.setItem(r, c, it)
        self.t.resizeRowsToContents()
        ok = QPushButton("✔ Duyệt – sản xuất")
        ok.clicked.connect(self.approve)
        redo = QPushButton("↻ Viết lại toàn bộ")
        redo.clicked.connect(self.rewrite)
        bl = QHBoxLayout()
        bl.addStretch()
        bl.addWidget(redo)
        bl.addWidget(ok)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.t, 1)
        lay.addLayout(bl)

    def approve(self):
        for r, s in enumerate(self.sc):
            nar, vis = self.t.item(r, 1).text().strip(), self.t.item(r, 2).text().strip()
            if nar != s["narration"] or vis != s["visual"]:
                db.update_scene(self.jid, s["idx"], narration=nar, visual=vis, audio=None, image=None, clip=None)
        db.update_job(self.jid, status="queued", stage="tts")
        self.accept()

    def rewrite(self):
        db.x("DELETE FROM scenes WHERE job_id=?", (self.jid,))
        db.update_job(self.jid, status="queued", stage="script", outline=None)
        self.accept()
STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup\AutoVideo.bat"


class Bridge(QObject):
    log = Signal(str)
    progress = Signal(object, str, int, int, object)
    changed = Signal()


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoVideo – NotebookLM · Qwen3-VL 8B · ZeroTTS · Google Flow · Whiteboard")
        self.resize(1400, 850)
        self.br = Bridge()
        self.br.log.connect(self._log)
        self.br.progress.connect(self._progress)
        self.br.changed.connect(self.refresh)
        self.worker = Worker(self.br.log.emit, self.br.progress.emit, self.br.changed.emit)
        (ROOT / "data").mkdir(exist_ok=True)
        self.logfile = (ROOT / "data" / "autovideo.log").open("a", encoding="utf-8")

        self.btn_start = QPushButton("▶  Bắt đầu")
        self.btn_stop = QPushButton("■  Dừng")
        for b, color in ((self.btn_start, "#1f9d55"), (self.btn_stop, "#c0392b")):
            b.setMinimumHeight(44)
            b.setStyleSheet(f"font-size:16px;font-weight:600;color:white;background:{color};border-radius:6px")
        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_kill = QPushButton("Dừng ngay")
        self.btn_kill.setToolTip("Kết thúc ngay tiến trình đang chạy (bước dở dang sẽ làm lại khi chạy tiếp)")
        self.btn_kill.clicked.connect(lambda: (self.worker.stop(immediate=True), self._log("Dừng ngay…")))
        self.status = QLabel()
        self.status.setStyleSheet("font-size:14px")
        self.chk_auto = QCheckBox("Tự bắt đầu khi mở app")
        self.chk_auto.setChecked(bool(db.kv_get("auto_start", False)))
        self.chk_auto.toggled.connect(lambda v: db.kv_set("auto_start", v))
        self.chk_boot = QCheckBox("Mở app khi Windows khởi động")
        self.chk_boot.setChecked(STARTUP_LNK.exists())
        self.chk_boot.toggled.connect(self._boot)
        btn_cfg = QPushButton("Cấu hình")
        btn_cfg.clicked.connect(lambda: os.startfile(CONFIG_PATH))
        btn_out = QPushButton("Thư mục video")
        btn_out.clicked.connect(self._open_output)

        self.mode = QComboBox()
        self.mode.addItem("🚀 Auto – chạy thẳng", "auto")
        self.mode.addItem("⭐ Quality – duyệt kịch bản", "quality")
        cur = db.kv_get("mode") or load().get("mode", "auto")
        self.mode.setCurrentIndex(1 if cur == "quality" else 0)
        self.mode.currentIndexChanged.connect(lambda _: db.kv_set("mode", self.mode.currentData()))
        btn_bible = QPushButton("Channel Bible")
        btn_bible.clicked.connect(lambda: os.startfile(ROOT / "channel.yaml"))

        top = QHBoxLayout()
        for w in (self.btn_start, self.btn_stop, self.btn_kill):
            top.addWidget(w)
        top.addWidget(self.status, 1)
        for w in (self.mode, self.chk_auto, self.chk_boot, btn_bible, btn_cfg, btn_out):
            top.addWidget(w)

        self.topics = QPlainTextEdit()
        self.topics.setPlaceholderText("Mỗi dòng một chủ đề…")
        self.topics.setMaximumHeight(110)
        btn_add = QPushButton("Thêm vào hàng đợi")
        btn_add.clicked.connect(self.add_topics)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "Chủ đề", "Trạng thái", "Bước", "Điểm", "Kết quả / lỗi"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._open_row)

        row_btns = QHBoxLayout()
        for text, fn in (("↑ Lên", lambda: self._sel(lambda i: db.move_job(i, -1))),
                         ("↓ Xuống", lambda: self._sel(lambda i: db.move_job(i, 1))),
                         ("Duyệt kịch bản", self._review),
                         ("Chạy lại", lambda: self._sel(db.retry_job)),
                         ("📂 Thư mục job", lambda: self._sel(lambda i: packager.open_folder(load(), i))),
                         ("🗑 Xoá job + thư mục", self._delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row_btns.addWidget(b)
        row_btns.addStretch()

        left = QVBoxLayout()
        left.addWidget(QLabel("Chủ đề mới"))
        left.addWidget(self.topics)
        left.addWidget(btn_add)
        left.addWidget(QLabel("Hàng đợi"))
        left.addWidget(self.table, 1)
        left.addLayout(row_btns)
        lw = QWidget()
        lw.setLayout(left)

        self.res_lbl = QLabel("GPU …")
        self.res_lbl.setStyleSheet("font-family:Consolas,monospace;padding:6px;border:1px solid #ccc;"
                                   "border-radius:4px;background:#fafafa")
        self.fatal_lbl = QLabel()
        self.fatal_lbl.setWordWrap(True)
        self.fatal_lbl.setStyleSheet("color:white;background:#c0392b;padding:8px;border-radius:4px;font-weight:600")
        self.fatal_lbl.hide()
        self.af_box = QWidget()
        self.af_box.setStyleSheet("background:#fff7e0;border:1px solid #f0c040;border-radius:6px")
        afl = QVBoxLayout(self.af_box)
        self.af_lbl = QLabel()
        self.af_lbl.setWordWrap(True)
        self.af_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        afl.addWidget(self.af_lbl)
        afb = QHBoxLayout()
        for text, fn in (("Mở file prompt", lambda: self._af_open("txt")),
                         ("Copy đường dẫn", self._af_copy),
                         ("Thư mục ảnh Auto Flow", lambda: self._af_open("inbox")),
                         ("Thư mục kiểm tra", lambda: self._af_open("exchange")),
                         ("Quét ngay", lambda: self._af_flag("scan")),
                         ("Dùng FLUX cho phần thiếu", lambda: self._af_flag("fallback"))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            afb.addWidget(b)
        afl.addLayout(afb)
        self.af_box.hide()
        self.stage_lbl = QLabel("—")
        self.bar = QProgressBar()
        self.preview = QLabel("Ảnh xem trước")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(300)
        self.preview.setStyleSheet("background:#F5EBD7;border:1px solid #ccc")
        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        self.logv.setMaximumBlockCount(3000)
        right = QVBoxLayout()
        right.addWidget(self.fatal_lbl)
        right.addWidget(self.res_lbl)
        right.addWidget(self.af_box)
        right.addWidget(self.stage_lbl)
        right.addWidget(self.bar)
        right.addWidget(self.preview, 2)
        right.addWidget(QLabel("Nhật ký"))
        right.addWidget(self.logv, 3)
        rw = QWidget()
        rw.setLayout(right)

        split = QSplitter()
        split.addWidget(lw)
        split.addWidget(rw)
        split.setSizes([750, 650])
        root = QVBoxLayout()
        root.addLayout(top)
        root.addWidget(split, 1)
        cw = QWidget()
        cw.setLayout(root)
        self.setCentralWidget(cw)

        cfg = load()
        try:
            api.serve(cfg.api_port, self.worker, self.br.changed.emit)
            self._log(f"API cho Bionic: http://127.0.0.1:{cfg.api_port}")
        except OSError as e:
            self._log(f"Không mở được API: {e}")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self.res_timer = QTimer(self)
        self.res_timer.timeout.connect(self._resources)
        self.res_timer.start(5000)
        QTimer.singleShot(200, self._resources)
        self.refresh()
        self._resume_hint()
        if self.chk_auto.isChecked():
            QTimer.singleShot(1500, self.start)

    # ---------- actions ----------
    def start(self):
        self.worker.start()
        self._tick()

    def stop(self):
        self.worker.stop()
        self._log("Đang dừng sau bước hiện tại…")

    def add_topics(self):
        lines = [l for l in self.topics.toPlainText().splitlines() if l.strip()]
        for l in lines:
            db.add_topic(l)
        self.topics.clear()
        self._log(f"Đã thêm {len(lines)} chủ đề")
        self.refresh()

    def _sel(self, fn):
        r = self.table.currentRow()
        if r < 0:
            return
        fn(int(self.table.item(r, 0).text()))
        self.refresh()

    def _delete(self):
        r = self.table.currentRow()
        if r < 0:
            return
        if self.table.item(r, 2).text() == STATUS_VI["running"]:
            QMessageBox.information(self, "AutoVideo", "Dừng app trước khi xoá job đang chạy.")
            return
        jid = int(self.table.item(r, 0).text())
        mb = packager.size_mb(packager.folder_of(jid))
        if QMessageBox.question(self, "AutoVideo", f"Xoá job #{jid} cùng thư mục của nó ({mb:.0f} MB)?\n"
                                "Thư mục được đưa vào Thùng rác, có thể khôi phục.") != QMessageBox.Yes:
            return
        try:
            res = packager.delete_job(load(), jid)
            self._log(f"🗑 Đã xoá job {jid} ({res['mb']} MB vào Thùng rác)")
        except Exception as e:
            QMessageBox.warning(self, "AutoVideo", str(e))
        self.refresh()

    def _review(self):
        r = self.table.currentRow()
        if r < 0:
            return
        jid = int(self.table.item(r, 0).text())
        if not db.scenes(jid):
            QMessageBox.information(self, "AutoVideo", "Job này chưa có kịch bản.")
            return
        if self.table.item(r, 2).text() == STATUS_VI["running"]:
            QMessageBox.information(self, "AutoVideo", "Job đang chạy, dừng app trước khi sửa kịch bản.")
            return
        ReviewDialog(self, jid).exec()
        self.refresh()

    def _open_row(self, r, _c):
        if self.table.item(r, 2).text() == STATUS_VI["review"]:
            self._review()
            return
        out = self.table.item(r, 5).data(Qt.UserRole)
        if out and Path(out).exists():
            os.startfile(out)

    def _open_output(self):
        # mỗi job một thư mục riêng (video, kịch bản, ảnh, giọng) → mở thư mục chứa tất cả các job
        p = packager.PROJECTS
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)

    def _boot(self, on):
        if on:
            pyw = Path(sys.executable).with_name("pythonw.exe")
            # chcp 65001: cmd đọc đúng đường dẫn có dấu tiếng Việt trong file .bat UTF-8
            STARTUP_LNK.write_text(f'@echo off\nchcp 65001 >nul\ncd /d "{ROOT}"\nstart "" "{pyw}" main.py\n',
                                   encoding="utf-8")
        else:
            STARTUP_LNK.unlink(missing_ok=True)

    # ---------- view ----------
    def refresh(self):
        jobs = db.jobs()
        self.table.setRowCount(len(jobs))
        for r, j in enumerate(jobs):
            res = j["output"] or (j["error"] or "")
            sc = json.loads(j["scores"]).get("total", "") if j.get("scores") else ""
            vals = [str(j["id"]), j["topic"], STATUS_VI.get(j["status"], j["status"]),
                    STAGE_VI.get(j["stage"], j["stage"]), str(sc), res]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 5:
                    it.setData(Qt.UserRole, j["output"])
                self.table.setItem(r, c, it)

    def _resume_hint(self):
        for j in db.jobs():
            if j["status"] == "queued" and j["stage"] not in ("research", "script"):
                sc = db.scenes(j["id"])
                self.stage_lbl.setText(f"Session trước: “{j['topic']}” đang ở bước {STAGE_VI[j['stage']]} "
                                       f"({len(sc)} cảnh) – bấm Bắt đầu để làm tiếp.")
                return

    def _tick(self):
        on = self.worker.running
        self.btn_start.setEnabled(not on)
        self.btn_stop.setEnabled(on and not self.worker.stop_evt.is_set())
        self.btn_kill.setEnabled(on)
        self._af_refresh()
        n = sum(1 for j in db.jobs() if j["status"] == "queued")
        state = "ĐANG CHẠY" if on else "ĐÃ DỪNG"
        if on and self.worker.stop_evt.is_set():
            state = "ĐANG DỪNG…"
        self.status.setText(f"  {state}  ·  {n} chủ đề chờ")

    def _af_state(self):
        return db.kv_get("autoflow_ui") or {}

    def _af_open(self, key):
        p = self._af_state().get(key)
        if p and Path(p).exists():
            os.startfile(p)

    def _af_copy(self):
        p = self._af_state().get("txt")
        if p:
            QApplication.clipboard().setText(p)
            self._log(f"Đã copy: {p}")

    def _af_flag(self, kind):
        jid = self._af_state().get("job")
        if jid:
            db.kv_set(f"autoflow_{kind}_{jid}", {"folder": None} if kind == "scan" else True)
            self._log("Đã yêu cầu quét ngay" if kind == "scan" else "Sẽ tạo phần ảnh còn thiếu bằng FLUX local")

    def _af_refresh(self):
        s = self._af_state()
        self.af_box.setVisible(bool(s) and self.worker.running)
        if s:
            mins = (time.time() - s.get("since", time.time())) / 60
            self.af_lbl.setText(
                f"<b>🖼 Auto Flow – job {s['job']}, lượt {s['round']}</b>: đã nhận {s['received']}/{s['rows']} ảnh "
                f"(chờ {mins:.0f} phút)<br>1) Auto Flow → <b>Import TXT</b> → chọn:<br><code>{s['txt']}</code><br>"
                f"2) <b>Add to queue</b> → <b>START</b>. Ảnh lưu vào <code>{s['inbox']}</code> – app tự nhận và kiểm tra.")

    def _resources(self):
        r = resources.snapshot()
        if r.get("temp") is not None:
            color = "#c0392b" if r["temp"] >= 90 else ("#e67e22" if r["temp"] >= 82 else "#1f9d55")
            gpu = (f"<span style='color:{color}'>GPU {r['temp']:.0f}°C</span> · tải {r['util']:.0f}% · "
                   f"VRAM {r['vram_used_gb']:.1f}/{r['vram_total_gb']:.0f} GB")
        else:
            gpu = "GPU: không đọc được"
        disk = r.get("disk_free_gb", 0)
        dcol = "#c0392b" if disk < 10 else ("#e67e22" if disk < 20 else "#333")
        ram = f" · RAM {r['ram_used_pct']:.0f}%" if "ram_used_pct" in r else ""
        self.res_lbl.setText(f"{gpu}{ram} · <span style='color:{dcol}'>Đĩa trống {disk:.0f} GB</span>")
        fatal = db.kv_get("fatal")
        self.fatal_lbl.setVisible(bool(fatal))
        if fatal:
            self.fatal_lbl.setText(f"⛔ Đã dừng cả hàng đợi: {fatal}. Khắc phục rồi bấm Bắt đầu.")

    def _progress(self, jid, stage, done, total, img):
        label = STAGE_VI.get(stage, stage)
        self.stage_lbl.setText(f"Job {jid} · {label} · {done}/{total}" if jid else label)
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(done)
        if img and Path(img).exists():
            pm = QPixmap(img).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.preview.setPixmap(pm)
        self.refresh()

    def _log(self, msg):
        line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
        events.push("log", {"msg": msg, "time": time.strftime('%H:%M:%S')})
        self.logv.appendPlainText(line)
        self.logfile.write(line + "\n")
        self.logfile.flush()

    def closeEvent(self, e):
        if self.worker.running:
            ans = QMessageBox.question(self, "AutoVideo",
                                       "App đang chạy. Dừng và thoát? (tiến độ đã được lưu, lần sau làm tiếp)")
            if ans != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.stop()
            self.worker.thread.join(timeout=15)
            if self.worker.running:     # còn kẹt ở tiến trình con dài → kết thúc cả cây tiến trình
                self.worker.stop(immediate=True)
                self.worker.thread.join(timeout=10)
        e.accept()


def _use_web(w: Main):
    """Dashboard web (HTML/CSS/JS) trong cửa sổ app; giao diện cũ vẫn giữ – Ctrl+K để chuyển qua lại."""
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QKeySequence, QShortcut
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QStackedWidget
    except ImportError:
        return
    classic = w.takeCentralWidget()
    stack = QStackedWidget()
    view = QWebEngineView()
    view.load(QUrl(f"http://127.0.0.1:{load().api_port}/ui"))
    stack.addWidget(view)
    stack.addWidget(classic)
    w.setCentralWidget(stack)
    w.web = view
    QShortcut(QKeySequence("Ctrl+K"), w, activated=lambda: stack.setCurrentIndex(1 - stack.currentIndex()))
    QShortcut(QKeySequence("F5"), w, activated=view.reload)


def run():
    db.init()
    app = QApplication(sys.argv)
    w = Main()
    if "--classic" not in sys.argv:
        _use_web(w)
    w.show()
    sys.exit(app.exec())
