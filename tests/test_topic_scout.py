"""Test topic_scout không cần mạng/LLM: luật trùng, cân bằng tin nóng/ngách, due()."""
import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db
db.DB_PATH = Path(tempfile.mkdtemp()) / "t.db"
db.init()
from app import topic_scout as ts
from app.config import load
ok = fail = 0
def t(n, c):
    global ok, fail
    ok += bool(c); fail += not c
    print(("PASS " if c else "FAIL ") + n)
t("trùng tiêu đề gần giống", ts._similar("Vì sao Bitcoin thủng 80.000 USD?", ["Vì Sao Bitcoin Thủng Mốc 80.000 USD?"]))
t("khác đề tài không trùng", not ts._similar("Vì sao người Nhật sống thọ?", ["Bitcoin thủng 80.000 USD"]))
cfg = load()
t("hàng đợi rỗng → cần tìm", ts.due(cfg))
for i in range(3):
    db.add_topic(f"chủ đề {i}")
t("còn 3 chủ đề chờ → chưa tìm", not ts.due(cfg))
db.x("DELETE FROM jobs WHERE topic='chủ đề 2'")
t("2 chờ, chưa trừ job sắp chạy → chưa tìm", not ts.due(cfg))
t("2 chờ, 1 sắp chạy → tìm thêm", ts.due(cfg, pending=1))
db.x("DELETE FROM jobs")
db.kv_set("topic_scout_last", time.time())
t("vừa tìm xong → không tìm lại ngay", not ts.due(cfg))
a = ts.acfg(cfg)
t("13 kênh tham khảo có URL", len(ts._channels(a)) == 13 and all(c["url"] for c in ts._channels(a)))
print(f"\n{ok} PASS, {fail} FAIL"); sys.exit(1 if fail else 0)
