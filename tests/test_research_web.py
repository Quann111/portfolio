"""Research web thật: .venv\\Scripts\\python.exe tests\\test_research_web.py "chủ đề" """
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import research, websearch  # noqa: E402
from app.config import load  # noqa: E402
from app.llm import LLM  # noqa: E402

topic = sys.argv[1] if len(sys.argv) > 1 else "Chuyện gì đã thực sự xảy ra với Cốc Cốc?"
cfg = load()
llm = LLM(cfg, print)
t0 = time.time()
pages = websearch.collect(topic, cfg, llm, print)
for p in pages:
    print(f"  [{p['n']}] {(p.get('date') or '?')[:16]} {p['source']}: {p['title'][:70]} ({len(p['text'])} ký tự)")
facts = research.fact_sheet(topic, cfg, llm, print, pages)
for f in facts[:12]:
    print(f"  - {'✔' if f['verified'] else '~'} {f['claim'][:110]} | {f['source'][:60]} | {f.get('published', '')}")
out = ROOT / "data" / "research_test"
out.mkdir(parents=True, exist_ok=True)
websearch.sources_md(topic, pages, facts, out / "sources.md")
(out / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n{len(pages)} nguồn, {len(facts)} fact ({sum(f['verified'] for f in facts)} xác minh) – {time.time() - t0:.0f}s")
print("Xem:", out / "sources.md")
