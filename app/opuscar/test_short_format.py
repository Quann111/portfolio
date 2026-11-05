"""Kiểm thử đơn vị độc lập cho định dạng Short của opuscar:
1. notebook.write_script: short 45s -> prompt yêu cầu ~45*wpm/60 chữ và 1 chương; long -> prompt y như cũ.
2. youtube packaging: short -> tiêu đề <= 60, có #Shorts, không có '00:00'; long không đổi.
3. worker opuscar QC: short 1080x1920 và độ dài <= seconds_of + 3; quá báo lỗi rõ ràng.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import config, db, notebook, packager, styles, worker, youtube


class TestShortFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.orig_db = db.DB_PATH
        self.orig_projects = packager.PROJECTS
        db.DB_PATH = self.tmp / "test.db"
        db.init()
        packager.PROJECTS = self.tmp / "projects"
        self.cfg = config.load()
        self.cfg["words_per_minute"] = 200

    def tearDown(self):
        db.DB_PATH = self.orig_db
        packager.PROJECTS = self.orig_projects
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_notebook_write_script_short_45s(self):
        jid = db.add_topic("Test topic short")
        styles.lock(jid, "opuscar", self.cfg)
        styles.set_format(jid, "short", 45)

        prompts_asked = []

        def mock_ask(jid_arg, step, prompt, deadline, check, slot=None, kind=None):
            prompts_asked.append((step, prompt))
            if step == "new":
                return "ok"
            if step == "outline":
                return (
                    f"{notebook.START}\n"
                    + json.dumps({
                        "title": "Bí mật 45s",
                        "core_message": "Thông điệp ngắn",
                        "hook": "Tại sao bạn chỉ có 45 giây?",
                        "callback_symbol": "Đồng hồ cát",
                        "chapters": [
                            {"title": "Toàn cảnh", "beat": "hook", "summary": "Nội dung tóm tắt",
                             "open_loop": "", "key_takeaway": "Điểm chính", "minutes": 0.75},
                            {"title": "Dư thừa", "beat": "explain", "summary": "Chương thừa",
                             "open_loop": "", "key_takeaway": "Thừa", "minutes": 0.5}
                        ]
                    })
                    + f"\n{notebook.END}"
                )
            if step.startswith("chapter_"):
                # 45s * 200 wpm / 60 = 150 words
                words = ["từ"] * 150
                return f"{notebook.START}\n{' '.join(words)}\n{notebook.END}"
            return ""

        with patch("app.notebook.connected", return_value=True), \
             patch("app.notebook.paused", return_value=None), \
             patch("app.notebook.bind", return_value="s1"), \
             patch("app.notebook.ask", side_effect=mock_ask), \
             patch("app.notebook.check_hook", return_value=True):

            res = notebook.write_script(jid, "Test topic short", [{"claim": "Dữ kiện 1", "evidence": "ev1"}],
                                        self.cfg, log=lambda *a: None)

            # 1. Đúng 1 chương
            self.assertEqual(len(res["chapters"]), 1)
            # 2. Kiểm tra prompt outline: có 45 giây, 1 chương, ~150 chữ (45 * 200 / 60)
            expected_words = round(45 * 200 / 60)
            self.assertEqual(expected_words, 150)
            outline_prompt = next(p for s, p in prompts_asked if s == "outline")
            self.assertIn("45 giây", outline_prompt)
            self.assertIn("~150 chữ", outline_prompt)
            self.assertIn("Đúng 1 chương duy nhất", outline_prompt)
            self.assertIn("nằm ngay 2 câu đầu", outline_prompt)

            # 3. Kiểm tra prompt viết chương: khoảng 150 chữ, kết bằng câu đọng lại
            chap_prompt = next(p for s, p in prompts_asked if s == "chapter_1")
            self.assertIn("khoảng 150 chữ", chap_prompt)
            self.assertIn("45 giây", chap_prompt)
            self.assertIn("Hook phải nằm trong 2 câu đầu", chap_prompt)
            self.assertIn("Kết thúc video bằng một câu đọng lại", chap_prompt)

    def test_notebook_write_script_long_identical_prompt(self):
        jid = db.add_topic("Test topic long")
        styles.lock(jid, "classic", self.cfg)
        styles.set_format(jid, "long", None)

        prompts_asked = []

        def mock_ask(jid_arg, step, prompt, deadline, check, slot=None, kind=None):
            prompts_asked.append((step, prompt))
            if step == "new":
                return "ok"
            if step == "outline":
                return (
                    f"{notebook.START}\n"
                    + json.dumps({
                        "title": "Video Dài",
                        "core_message": "Thông điệp dài",
                        "hook": "Câu hỏi mở đầu dài?",
                        "callback_symbol": "Biểu tượng",
                        "chapters": [
                            {"title": "Phần 1", "beat": "hook", "summary": "Tóm tắt 1",
                             "open_loop": "Câu hỏi treo 1", "key_takeaway": "Key 1", "minutes": 2.5},
                            {"title": "Phần 2", "beat": "explain", "summary": "Tóm tắt 2",
                             "open_loop": "Câu hỏi treo 2", "key_takeaway": "Key 2", "minutes": 2.5},
                            {"title": "Phần 3", "beat": "payoff", "summary": "Tóm tắt 3",
                             "open_loop": "", "key_takeaway": "Key 3", "minutes": 2.5}
                        ]
                    })
                    + f"\n{notebook.END}"
                )
            if step.startswith("chapter_"):
                return f"{notebook.START}\n{' '.join(['lời đọc'] * 200)}\n{notebook.END}"
            return ""

        with patch("app.notebook.connected", return_value=True), \
             patch("app.notebook.paused", return_value=None), \
             patch("app.notebook.bind", return_value="s1"), \
             patch("app.notebook.bind_extra", return_value=[]), \
             patch("app.notebook.ask", side_effect=mock_ask), \
             patch("app.notebook.check_hook", return_value=True):

            res = notebook.write_script(jid, "Test topic long", [{"claim": "Dữ kiện 1", "evidence": "ev1"}],
                                        self.cfg, log=lambda *a: None)

            self.assertEqual(len(res["chapters"]), 3)
            outline_prompt = next(p for s, p in prompts_asked if s == "outline")
            self.assertIn("thiết kế DÀN Ý cho video", outline_prompt)
            self.assertIn("khiến người xem KHÔNG THỂ BỎ DỞ", outline_prompt)
            self.assertIn("vòng lặp tò mò", outline_prompt)

            chap1_prompt = next(p for s, p in prompts_asked if s == "chapter_1")
            self.assertIn("Chương này PHẢI mở đầu bằng hook", chap1_prompt)
            self.assertIn("Kết chương bằng câu treo dẫn sang chương 2", chap1_prompt)

    def test_youtube_short_vs_long(self):
        # 1. Short
        jid_s = db.add_topic("Short topic")
        styles.lock(jid_s, "opuscar", self.cfg)
        styles.set_format(jid_s, "short", 45)

        one_s = {
            "titles": ["Tiêu đề siêu dài của video giải thích công nghệ này sẽ bị cắt bớt nếu vượt 60 ký tự"],
            "thumb_text": ["TIÊU ĐỀ THUMBNAIL"],
            "thumb_scene": "scene", "hook": "Hook ngắn?", "intro": "Intro ngắn",
            "key_points": ["p1", "p2", "p3", "p4", "p5"], "conclusion": "Kết luận",
            "comment_question": "Hỏi gì?", "hashtags": ["#Tech", "#AI"], "tags": ["tech", "ai"]
        }
        data_s = {"vi": youtube._clean(one_s), "en": youtube._clean(one_s)}
        youtube.save(jid_s, data_s, self.cfg, [0, 15000, 30000])

        f_s = packager.folder_of(jid_s) / "youtube"
        title_s = (f_s / "title_vi.txt").read_text(encoding="utf-8")
        desc_s = (f_s / "description_vi.txt").read_text(encoding="utf-8")

        self.assertLessEqual(len(title_s), 60)
        self.assertIn("#Shorts", title_s)
        self.assertIn("#Shorts", desc_s)
        self.assertNotIn("00:00", desc_s)

        # 2. Long
        jid_l = db.add_topic("Long topic")
        styles.lock(jid_l, "classic", self.cfg)
        styles.set_format(jid_l, "long", None)
        db.update_job(jid_l, outline=json.dumps({"title": "Long topic", "chapters": [
            {"title": "Mở đầu"}, {"title": "Nội dung"}, {"title": "Kết luận"}
        ]}))
        db.insert_scenes(jid_l, [{"chapter": c, "narration": "n", "image_prompt": "v"} for c in (0, 1, 2)])

        one_l = {
            "titles": ["Tiêu đề dài của video chuẩn thông thường cho YouTube"],
            "thumb_text": ["TIÊU ĐỀ THUMBNAIL"],
            "thumb_scene": "scene", "hook": "Hook dài?", "intro": "Intro dài",
            "key_points": ["p1", "p2", "p3", "p4", "p5"], "conclusion": "Kết luận",
            "comment_question": "Hỏi gì?", "hashtags": ["#Tech"], "tags": ["tech"]
        }
        data_l = {"vi": youtube._clean(one_l), "en": youtube._clean(one_l)}
        youtube.save(jid_l, data_l, self.cfg, [0, 30000, 60000])

        f_l = packager.folder_of(jid_l) / "youtube"
        desc_l = (f_l / "description_vi.txt").read_text(encoding="utf-8")
        self.assertIn("00:00 Mở đầu", desc_l)

    def test_worker_qc_short_branches(self):
        w = worker.Worker(log=lambda *a: None, progress=lambda *a: None, changed=lambda: None)
        w.cfg = self.cfg
        jid = db.add_topic("Test topic for qc")

        # Mock ffprobe output
        def make_ffprobe_res(width, height, duration):
            return MagicMock(return_value=MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "streams": [{"codec_type": "video", "width": width, "height": height}, {"codec_type": "audio"}],
                    "format": {"duration": str(duration)}
                })
            ))

        with patch.object(styles, "format_of", return_value="short"), \
             patch.object(styles, "seconds_of", return_value=45), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("subprocess.run") as mock_subproc, \
             patch("app.db.update_job") as mock_update:

            # 1. Pass: 1080x1920, 47.5s (<= 45 + 3 = 48s)
            mock_subproc.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps({
                    "streams": [{"codec_type": "video", "width": 1080, "height": 1920}, {"codec_type": "audio"}],
                    "format": {"duration": "47.5"}
                })),
                MagicMock(returncode=0, stderr="")  # blackdetect
            ]
            w._opuscar_qc(jid, self.tmp, {"repairs": 0, "output": "video.mp4"})
            # Check db.update_job was called with pass=True
            args, kw = mock_update.call_args
            scores = json.loads(kw["scores"])
            self.assertTrue(scores["pass"])

            # 2. Fail size: 1920x1080
            mock_subproc.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps({
                    "streams": [{"codec_type": "video", "width": 1920, "height": 1080}, {"codec_type": "audio"}],
                    "format": {"duration": "44.0"}
                })),
                MagicMock(returncode=0, stderr="")
            ]
            w._repair = MagicMock()
            w._opuscar_qc(jid, self.tmp, {"repairs": 0, "output": "video.mp4"})
            args, kw = mock_update.call_args
            scores = json.loads(kw["scores"])
            self.assertFalse(scores["pass"])
            self.assertTrue(any("1080x1920" in f for f in scores["opuscar_qc"]))

            # 3. Fail duration: 49s (> 45 + 3 = 48s)
            mock_subproc.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps({
                    "streams": [{"codec_type": "video", "width": 1080, "height": 1920}, {"codec_type": "audio"}],
                    "format": {"duration": "49.0"}
                })),
                MagicMock(returncode=0, stderr="")
            ]
            w._opuscar_qc(jid, self.tmp, {"repairs": 0, "output": "video.mp4"})
            args, kw = mock_update.call_args
            scores = json.loads(kw["scores"])
            self.assertFalse(scores["pass"])
            self.assertTrue(any("vượt quá" in f for f in scores["opuscar_qc"]))


if __name__ == "__main__":
    unittest.main()
