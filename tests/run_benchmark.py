"""Benchmark ảnh + giọng: chạy trước khi đổi model/LoRA/prompt/giọng và so sánh kết quả.

  .venv\\Scripts\\python.exe tests\\run_benchmark.py images   # 20 prompt ảnh
  .venv\\Scripts\\python.exe tests\\run_benchmark.py audio    # 10 câu tiếng Việt khó
Kết quả: data/benchmark/<loại>_<thời gian>.json (+ ảnh/wav), so với lần chạy trước gần nhất.
"""
import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import audio_qc, image, maintenance, resources  # noqa: E402
from app.config import load  # noqa: E402
from app.llm import LLM  # noqa: E402

OUT = ROOT / "data" / "benchmark"


def _compare(kind, report, key):
    prev = sorted(OUT.glob(f"{kind}_*.json"))
    prev = [p for p in prev if p.name != report["file"]]
    if not prev:
        print("Chưa có lần chạy trước để so sánh.")
        return
    old = json.loads(prev[-1].read_text(encoding="utf-8"))
    print(f"So với {prev[-1].name}: {old['summary'][key]} → {report['summary'][key]}")


def images():
    cfg, spec = load(), yaml.safe_load((ROOT / "tests/benchmark/images.yaml").read_text(encoding="utf-8"))
    llm = LLM(cfg, print)
    stamp = time.strftime("%Y%m%d_%H%M")
    d = OUT / f"images_{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for it in spec["items"]:
        f = d / f"{it['id']}.png"
        t0 = time.time()
        seed = image.generate(image.build_prompt(it, {}, cfg), f, cfg, seed=spec.get("seed"))
        gen_s = time.time() - t0
        vram = resources.gpu().get("vram_used_gb")
        res = image.check(f, it, None, llm, cfg, 12000)
        rows.append({"id": it["id"], "type": it["visual_type"], "seed": seed, "gen_s": round(gen_s, 1),
                     "vram_gb": round(vram or 0, 1), "pass": res["pass"], "failed": res["failed_gates"],
                     **{k: res[k] for k in ("semantic_score", "visual_quality_score",
                                            "whiteboard_suitability_score", "has_text")}})
        print(f"{it['id']:18} {'PASS' if res['pass'] else 'fail'} nghĩa {res['semantic_score']:.2f} "
              f"đẹp {res['visual_quality_score']:.2f} dễ vẽ {res['whiteboard_suitability_score']:.2f} "
              f"chữ {res['has_text']} {gen_s:.0f}s")
    summary = {"pass_rate": round(sum(r["pass"] for r in rows) / len(rows), 2),
               "text_rate": round(sum(r["has_text"] for r in rows) / len(rows), 2),
               "avg_semantic": round(sum(r["semantic_score"] for r in rows) / len(rows), 3),
               "avg_gen_s": round(sum(r["gen_s"] for r in rows) / len(rows), 1)}
    report = {"file": f"images_{stamp}.json", "versions": maintenance.versions(cfg), "summary": summary, "rows": rows}
    (OUT / report["file"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nTỔNG:", summary)
    _compare("images", report, "pass_rate")


def audio():
    from app.tts import TTS
    cfg, spec = load(), yaml.safe_load((ROOT / "tests/benchmark/audio.yaml").read_text(encoding="utf-8"))
    tts = TTS(cfg, print)
    stamp = time.strftime("%Y%m%d_%H%M")
    d = OUT / f"audio_{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, text in enumerate(spec["sentences"]):
        f = d / f"s{i:02}.wav"
        total, speech, _ = tts.speak(text, f)
        r = audio_qc.check(f, text, speech, cfg.language, tts, cfg)
        r.pop("words", None)
        rows.append({"text": text, "asr_error": r["asr_error"], "heard": r["heard"], "issues": r["issues"],
                     "suspects": r.get("pronunciation_suspects", [])})
        print(f"[{r['asr_error']:.2f}] {text}\n       nghe: {r['heard']}\n       {r['issues']}")
    summary = {"avg_asr_error": round(sum(r["asr_error"] for r in rows) / len(rows), 3),
               "pass_rate": round(sum(not r["issues"] for r in rows) / len(rows), 2)}
    report = {"file": f"audio_{stamp}.json", "versions": maintenance.versions(cfg), "summary": summary, "rows": rows}
    (OUT / report["file"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nTỔNG:", summary)
    _compare("audio", report, "avg_asr_error")


if __name__ == "__main__":
    {"images": images, "audio": audio}[sys.argv[1] if len(sys.argv) > 1 else "images"]()
