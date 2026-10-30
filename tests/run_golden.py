"""Golden test: chạy 5 chủ đề cố định (1 phút) trên DB/thư mục riêng, so điểm với baseline.

  .venv\\Scripts\\python.exe tests\\run_golden.py                 # chạy và so sánh
  .venv\\Scripts\\python.exe tests\\run_golden.py --update-baseline # chấp nhận kết quả làm baseline mới
  .venv\\Scripts\\python.exe tests\\run_golden.py --only test_02_science
"""
import argparse
import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, db, maintenance, worker  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
RUN = ROOT / "data" / "golden_run"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    spec = yaml.safe_load((GOLDEN / "topics.yaml").read_text(encoding="utf-8"))
    topics = [t for t in spec["topics"] if not args.only or t["id"] == args.only]
    RUN.mkdir(parents=True, exist_ok=True)
    db.DB_PATH = RUN / "golden.db"
    db.DB_PATH.unlink(missing_ok=True)
    db.init()
    db.kv_set("mode", "auto")

    base_load = config.load

    def load_test():
        c = base_load()
        c["target_minutes"] = spec.get("target_minutes", 1)
        c["work_dir"] = str(RUN / "work")
        c["output_dir"] = str(RUN / "output")
        c["auto_topics"] = {"enabled": False}   # golden test không tự thêm chủ đề
        c["keep_work"] = True
        c["maintenance_hour"] = 99
        return c

    worker.load = load_test
    ids = {db.add_topic(t["topic"]): t["id"] for t in topics}
    t0 = time.time()
    w = worker.Worker(lambda m: print(f"[{time.time() - t0:7.0f}s] {m}", flush=True),
                      lambda *a: None, lambda: None)
    w.start()
    while True:
        time.sleep(10)
        js = db.jobs()
        if all(j["status"] in ("done", "failed") for j in js):
            break
    w.stop()
    w.thread.join(120)

    results = {}
    for j in db.jobs():
        sc = json.loads(j["scores"]) if j.get("scores") else {}
        results[ids[j["id"]]] = {"status": j["status"], "total": sc.get("total"),
                                 **{k: sc.get(k) for k in ("content", "retention", "visual", "alignment",
                                                           "audio", "render", "technical")}}
    report = {"versions": maintenance.versions(load_test()), "results": results,
              "minutes": round((time.time() - t0) / 60, 1)}
    (RUN / "last_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    base_path = GOLDEN / "baseline.json"
    if args.update_baseline or not base_path.exists():
        base_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Đã lưu baseline mới:", base_path)
        return 0
    base = json.loads(base_path.read_text(encoding="utf-8"))["results"]
    fail = False
    print(f"\n{'test':24} {'baseline':>9} {'now':>7} {'Δ':>6}")
    for tid, r in results.items():
        b = (base.get(tid) or {}).get("total")
        now = r["total"]
        delta = (now - b) if (now is not None and b is not None) else None
        bad = r["status"] != "done" or (delta is not None and delta < -spec.get("max_drop", 3))
        fail |= bad
        print(f"{tid:24} {b!s:>9} {now!s:>7} {('' if delta is None else f'{delta:+.1f}'):>6} {'FAIL' if bad else 'ok'}")
    print("\nKẾT QUẢ:", "FAIL – không nên đưa thay đổi này vào sản xuất" if fail else "PASS")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
