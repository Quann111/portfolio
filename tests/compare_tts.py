"""So sánh giọng đọc: chạy cùng vài câu kịch bản thật qua từng engine, lưu WAV để nghe, đo tốc độ + Whisper.
Dùng: .venv\\Scripts\\python.exe tests\\compare_tts.py [omnivoice vieneu zerotts]
Kết quả: exchange/tts_compare/<engine>_<n>.wav + report.txt"""
import sys
import time
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.audio_qc import asr_error  # noqa: E402
from app.config import load  # noqa: E402
from app.tts import TTS  # noqa: E402

LINES = [
    "Hàn Quốc sở hữu Son Heung-min đoạt Vua phá lưới Premier League, nhưng tại sao lúc này Nhật Bản mới là cái tên "
    "đang phủ sóng và áp đảo toàn bộ giải Ngoại hạng Anh?",
    "Năm 2023, Kaoru Mitoma ghi 7 bàn cho Brighton, và giá trị chuyển nhượng của anh tăng lên khoảng 45 triệu euro.",
    "Họ không còn là những bản hợp đồng thương mại mua về để bán áo đấu như hai mươi năm trước.",
    "Liệu Hàn Quốc đang thực sự cạn kiệt tài năng, hay Nhật Bản đang nắm giữ một công thức bí mật mà cả thế giới "
    "bóng đá chưa từng chú ý?",
]
out = ROOT / "exchange" / "tts_compare"
out.mkdir(parents=True, exist_ok=True)
engines = sys.argv[1:] or ["vieneu", "zerotts", "omnivoice"]
cfg = load()
report = []
for eng in engines:
    cfg["tts"]["engine"] = eng   # cfg.tts trả bản sao
    t = TTS(cfg, print)
    t0 = time.time()
    t._load()
    load_s = time.time() - t0
    gen = audio_s = 0.0
    errs = []
    for i, line in enumerate(LINES, 1):
        f = out / f"{eng}_{i}.wav"
        t0 = time.time()
        t.speak(line, f, pause=0.2)
        gen += time.time() - t0
        audio_s += sf.info(str(f)).duration
        errs.append(asr_error(line, t.transcribe(f), cfg.language))
    row = (f"{eng:10} load {load_s:5.1f}s · tạo {gen:6.1f}s cho {audio_s:5.1f}s audio (RTF {gen / audio_s:.2f}) · "
           f"Whisper lệch TB {sum(errs) / len(errs):.0%} ({', '.join(f'{e:.0%}' for e in errs)})")
    print(row)
    report.append(row)
    t.release()
(out / "report.txt").write_text("\n".join(report), encoding="utf-8")
