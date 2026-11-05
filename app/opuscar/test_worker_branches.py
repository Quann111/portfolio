"""Kiểm thử đơn vị cho các nhánh rẽ style trong worker.py (classic, nikola, opuscar).
Đảm bảo style classic và nikola giữ 100% nguyên vẹn hành vi, không bị ảnh hưởng bởi opuscar.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app import styles
from app.worker import Worker


class TestWorkerBranches(unittest.TestCase):
    def setUp(self):
        self.cfg = MagicMock()
        self.worker = Worker(log=lambda *a: None, progress=lambda *a: None, changed=lambda: None)
        self.worker.cfg = self.cfg

    def test_offsets_branching(self):
        sc = [{"job_id": 999, "idx": 0, "clip": "clip.mp4", "duration_ms": 3000}]

        # 1. Classic style
        with patch.object(styles, "of_job", return_value="classic"), \
             patch("app.render.duration_ms", return_value=3000):
            offs, total = self.worker._offsets(sc)
            self.assertEqual(offs, [0])
            self.assertEqual(total, 3000)

        # 2. Nikola style
        with patch.object(styles, "of_job", return_value="nikola"), \
             patch.object(self.worker, "_nikola_offsets", return_value=([0], 2500)) as mock_nk:
            offs, total = self.worker._offsets(sc)
            mock_nk.assert_called_once_with(sc)
            self.assertEqual(total, 2500)

        # 3. Opuscar style
        with patch.object(styles, "of_job", return_value="opuscar"), \
             patch.object(self.worker, "_opuscar_offsets", return_value=([0], 2800)) as mock_op:
            offs, total = self.worker._offsets(sc)
            mock_op.assert_called_once_with(sc)
            self.assertEqual(total, 2800)

    def test_render_branching(self):
        jid = 999
        work = MagicMock()

        # 1. Nikola style
        with patch.object(styles, "of_job", return_value="nikola"), \
             patch.object(self.worker, "_nikola_render", return_value="nk_done") as mock_nk:
            res = self.worker._st_render(jid, work)
            mock_nk.assert_called_once_with(jid, work)
            self.assertEqual(res, "nk_done")

        # 2. Opuscar style
        with patch.object(styles, "of_job", return_value="opuscar"), \
             patch.object(self.worker, "_opuscar_render", return_value={"video": "op.mp4"}) as mock_op:
            res = self.worker._st_render(jid, work)
            mock_op.assert_called_once_with(jid, work)
            self.assertEqual(res, {"video": "op.mp4"})

    def test_assemble_branching(self):
        jid = 999
        work = MagicMock()

        # 1. Nikola style
        with patch.object(styles, "of_job", return_value="nikola"), \
             patch.object(self.worker, "_nikola_assemble", return_value="nk_assembled") as mock_nk:
            res = self.worker._st_assemble(jid, work)
            mock_nk.assert_called_once_with(jid, work)
            self.assertEqual(res, "nk_assembled")

        # 2. Opuscar style
        with patch.object(styles, "of_job", return_value="opuscar"), \
             patch.object(self.worker, "_opuscar_assemble", return_value="op_assembled") as mock_op:
            res = self.worker._st_assemble(jid, work)
            mock_op.assert_called_once_with(jid, work)
            self.assertEqual(res, "op_assembled")

    def test_qc_branching(self):
        jid = 999
        work = MagicMock()
        job = {"id": jid, "output": "out.mp4"}

        # Opuscar style calls _opuscar_qc
        with patch.object(self.worker, "_job", return_value=job), \
             patch.object(styles, "of_job", return_value="opuscar"), \
             patch.object(self.worker, "_opuscar_qc", return_value="op_qc_done") as mock_qc:
            res = self.worker._st_qc(jid, work)
            mock_qc.assert_called_once_with(jid, work, job)
            self.assertEqual(res, "op_qc_done")


if __name__ == "__main__":
    unittest.main()
