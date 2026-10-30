"""Viết kịch bản bằng NotebookLM (Gemini Notebook) qua extension Chrome riêng 'AutoVideo Notebook Bridge':
app xếp prompt vào hàng đợi nb_tasks → extension (tab notebooklm đã đăng nhập) gõ prompt như người dùng, chờ
trả lời xong và gửi văn bản về. Không gọi API nội bộ, gặp giới hạn/captcha thì tạm dừng; worker chờ rồi thử lại (script.fallback_qwen)."""
import json
import re
import threading
import time
from datetime import datetime

_ASK_LOCK = threading.Lock()

from . import db, pipeline, planner
from .config import Cfg

MAX_PROMPT = 3200            # giới hạn an toàn cho ô hỏi NotebookLM (đo 27/10: 3.292 gửi được, 3.924 bị khoá nút gửi)
CLAIM_TIMEOUT = 900          # câu trả lời dài có thể mất vài phút
MAX_TASK_ATTEMPTS = 3
START = "===BẮT ĐẦU==="
END = "===KẾT THÚC==="


# ------------------------------------------------------------------ hàng đợi
def enqueue(jid: int, step: str, prompt: str, kind: str = "ask") -> int:
    return db.x("INSERT INTO nb_tasks(job_id,step,kind,prompt,created_at) VALUES(?,?,?,?,?)",
                (jid, step, kind, prompt, time.time()))


def claim_next() -> dict | None:
    if paused():
        return None
    db.x("UPDATE nb_tasks SET status='queued' WHERE status='claimed' AND claimed_at < ?",
         (time.time() - CLAIM_TIMEOUT,))
    r = db.q("SELECT * FROM nb_tasks WHERE status='queued' ORDER BY id LIMIT 1")
    if not r:
        return None
    t = r[0]
    db.x("UPDATE nb_tasks SET status='claimed', claimed_at=?, attempts=attempts+1 WHERE id=?", (time.time(), t["id"]))
    return {"id": t["id"], "kind": t["kind"], "prompt": t["prompt"], "job": t["job_id"], "step": t["step"]}


def complete(task_id: int, text: str):
    db.x("UPDATE nb_tasks SET status='done', done_at=?, result=? WHERE id=?", (time.time(), text, task_id))


def fail(task_id: int, msg: str, pause: bool = False):
    t = db.q("SELECT attempts FROM nb_tasks WHERE id=?", (task_id,))
    retry = t and t[0]["attempts"] < MAX_TASK_ATTEMPTS and not pause
    db.x("UPDATE nb_tasks SET status=?, error=? WHERE id=?", ("queued" if retry else "error", msg[:500], task_id))
    if pause:
        set_paused(msg)


def heartbeat(info: dict):
    db.kv_set("notebook_seen", {"at": time.time(), **(info or {})})


def connected(max_age: float = 90) -> bool:
    s = db.kv_get("notebook_seen") or {}
    return time.time() - s.get("at", 0) < max_age and s.get("enabled", True)


def paused() -> str | None:
    return db.kv_get("notebook_paused")


def set_paused(reason: str | None):
    db.kv_set("notebook_paused", reason)


def cancel_job(jid: int):
    db.x("UPDATE nb_tasks SET status='cancelled' WHERE job_id=? AND status IN ('queued','claimed')", (jid,))


def summary() -> dict:
    rows = db.q("SELECT status, COUNT(*) n FROM nb_tasks GROUP BY status")
    seen = db.kv_get("notebook_seen") or {}
    return {"tasks": {r["status"]: r["n"] for r in rows}, "extension_connected": connected(),
            "last_seen": seen.get("at"), "enabled": seen.get("enabled"), "tab": seen.get("tab"),
            "version": seen.get("version"), "paused": paused()}


class Unavailable(Exception):
    pass


def ask(jid: int, step: str, prompt: str, deadline: float, check, kind: str = "ask") -> str:
    if len(prompt) > MAX_PROMPT + 400:
        raise Unavailable(f"câu hỏi bước {step} dài {len(prompt)} ký tự – vượt giới hạn ô hỏi NotebookLM")
    # Khóa luồng hỏi NotebookLM: vì tab trình duyệt chỉ có 1 ô nhập duy nhất, tránh tình trạng
    # luồng chính và luồng chuẩn bị (_prep_loop) tranh chấp gõ đè chữ lên nhau
    with _ASK_LOCK:
        tid = enqueue(jid, step, prompt, kind)
        while time.time() < deadline:
            check()
            t = db.q("SELECT status, result, error FROM nb_tasks WHERE id=?", (tid,))[0]
            if t["status"] == "done":
                return t["result"] or ""
            if t["status"] == "error":
                raise Unavailable(f"NotebookLM lỗi ở bước {step}: {t['error']}")
            if paused():
                cancel_job(jid)
                raise Unavailable(f"Notebook Bridge tạm dừng: {paused()}")
            time.sleep(3)
        cancel_job(jid)
        raise Unavailable(f"NotebookLM quá thời gian chờ ở bước {step}")


# ------------------------------------------------------------------ kịch bản
def _facts_text(facts: list, limit: int = 2600) -> str:
    out = []
    for f in facts:
        when = f.get("published") or ""
        tag = " (thận trọng)" if f.get("phrasing") == "cautious" or f.get("kind") == "hypothesis" else ""
        out.append(f"- {f.get('claim') or f.get('fact') or ''}{tag}" + (f" [{when[:10]}]" if when else ""))
    s = "\n".join(out)
    return s[:limit]


def _brief(topic: str, cfg: Cfg) -> str:
    ch = cfg.bible["channel"]
    return (f"Bạn là biên kịch số 1 của một kênh YouTube video hoạt hình bảng trắng tiếng Việt. Khán giả: "
            f"{ch['audience']}. Giọng: {ch['tone']}. Hôm nay là {datetime.now():%d/%m/%Y}.\n"
            f"Chủ đề: {topic}\n")


def _answer(text: str) -> str:
    """Phần giữa dấu mở/đóng của câu trả lời. Khung chat còn chứa cả câu hỏi (cũng có 2 dấu này) nên lấy dấu
    mở CUỐI CÙNG."""
    if START in text:
        head, tail = text.rsplit(START, 1)
        seg = tail.split(END, 1)[0]
        if "\n" not in seg and len(seg.strip()) < 15 and END in tail:   # "giữa S và E" của câu hỏi
            return tail.split(END, 1)[1]
        return seg
    return text


def _json_block(text: str) -> dict:
    seg = _answer(text)
    m = re.search(r"\{.*\}", seg, re.S)
    if not m:
        raise ValueError("không thấy JSON trong câu trả lời")
    raw = m.group(0).replace("“", '"').replace("”", '"')
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    return json.loads(raw)


def clean_narration(text: str) -> str:
    text = _answer(text)
    lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("#", "```", "---", "===")) or re.match(r"^\(?(cảnh|scene|chương|đoạn)\s*\d", ln, re.I):
            continue
        ln = re.sub(r"^[-*•>]\s+", "", ln)
        ln = re.sub(r"\*\*|__|\*", "", ln)
        ln = re.sub(r"\[[^\]]{0,40}\]|\([^)]*(?:nhạc|âm thanh|hình ảnh|SFX)[^)]*\)", "", ln, flags=re.I)
        lines.append(ln.strip())
    return re.sub(r"\s+", " ", " ".join(lines)).strip().lstrip(".,:;!? ")


def _words(t: str) -> int:
    return len(t.split())


OUTLINE_KEYS = ("title", "core_message", "hook", "chapters")


def write_script(jid: int, topic: str, facts: list, cfg: Cfg, log, check=lambda: None,
                 progress=lambda d, t: None) -> dict:
    """Trả về {title, core_message, hook, callback_symbol, chapters:[{title, beat, summary, key_takeaway,
    open_loop, text}]}. Lỗi/không kết nối → raise Unavailable (worker thử lại sau, hoặc dùng Qwen nếu bật fallback_qwen)."""
    sc = cfg.get("script") or {}
    if not connected():
        # chờ một lúc (extension có thể đang được Reload / service worker vừa ngủ)
        log("NotebookLM: chờ extension Notebook Bridge kết nối (tối đa 3 phút)…")
        until = time.time() + float(sc.get("connect_wait_minutes", 3)) * 60
        t_wait = time.time()
        while not connected() and time.time() < until:
            check()
            time.sleep(5)
    if not connected():
        raise Unavailable("extension Notebook Bridge chưa kết nối hoặc đang tắt (mở tab NotebookLM, bật tự động)")
    if paused():
        raise Unavailable(f"Notebook Bridge đang tạm dừng: {paused()}")
    deadline = time.time() + float(sc.get("wait_minutes", 20)) * 60
    # Lấy target_minutes riêng của job nếu có, fallback về cấu hình chung
    j_row = db.q("SELECT target_minutes FROM jobs WHERE id=?", (jid,))
    target_m = (j_row and j_row[0]["target_minutes"]) or cfg.target_minutes
    total_words = round(target_m * cfg.words_per_minute)
    n_ch = max(3, min(12, round(target_m / 2.5)))
    ch = cfg.bible["channel"]
    facts_txt = _facts_text(facts)
    brief = _brief(topic, cfg)
    from . import styles
    if styles.of_job(jid, cfg) == "nikola":
        # semantic-island-storytelling.md §6: móc câu (mâu thuẫn kiểm chứng được) → điểm xuất phát → bước ngoặt → kết quả
        brief += ("Nhịp kể: mở bằng một mâu thuẫn có thật, kiểm chứng được (không giật tít) → quay về điểm xuất phát → "
                  "bước ngoặt → kết quả/giá trị. Mỗi sự kiện gói trong 1–2 câu (4–8 giây đọc), hai sự kiện liền nhau "
                  "phải nối tiếp nhau để vẽ thành hai cảnh cạnh nhau trên cùng một trang.\n")

    if sc.get("new_notebook", True):
        try:
            ask(jid, "new", "", deadline, check, kind="new")
        except Unavailable as e:
            log(f"NotebookLM: không tạo được sổ mới ({e}) → viết tiếp trong sổ hiện tại")
            if paused():
                raise

    if "t_wait" in locals():
        pipeline.add_wait(jid, "script", time.time() - t_wait, "chờ extension NotebookLM kết nối")
    pipeline.sub(jid, "nb_outline")
    log(f"NotebookLM: lập dàn ý giữ chân ({n_ch} chương, ~{total_words} chữ)")
    progress(0, n_ch + 1)
    p_outline = (
        brief + "Dữ kiện đã kiểm chứng từ web (ưu tiên dùng; không bịa số liệu/trích dẫn/sự kiện). Dữ kiện nào lạc đề thì bỏ qua. Nếu dữ kiện thiếu, được dùng kiến thức phổ biến đã được xác nhận rộng rãi về chủ đề, diễn đạt thận trọng, KHÔNG hỏi lại – luôn trả về dàn ý:\n"
        f"{facts_txt}\n\n"
        f"Hãy suy nghĩ kỹ và thiết kế DÀN Ý cho video {target_m} phút (~{total_words} chữ lời đọc) "
        "khiến người xem KHÔNG THỂ BỎ DỞ:\n"
        "- hook: 1-2 câu mở đầu gây tò mò cực mạnh trong 5 giây đầu (sự thật gây sốc, câu hỏi táo bạo, hoặc "
        "nghịch lý), trung thực, không câu view rẻ tiền\n"
        "- mở các 'vòng lặp tò mò' (open loop) sớm và chỉ trả lời ở chương sau; mỗi chương kết thúc bằng một câu "
        "treo khiến người xem muốn nghe tiếp\n"
        "- có cao trào, ít nhất một cú twist/điều bất ngờ, ví dụ cụ thể, so sánh dễ hình dung\n"
        f"- chương cuối gói lại thông điệp và kêu gọi: {ch['cta']}\n"
        f"Đúng {n_ch} chương. beat của mỗi chương chọn trong: {', '.join(planner.BEATS)}.\n"
        f"Chỉ trả về JSON (không dùng khối code) nằm giữa hai dòng {START} và {END}, dạng:\n"
        '{"title": "...", "core_message": "...", "hook": "...", "callback_symbol": "một hình ảnh biểu tượng lặp '
        'lại", "chapters": [{"title": "...", "beat": "...", "summary": "nội dung chính", "open_loop": "câu hỏi '
        'treo cuối chương", "key_takeaway": "...", "minutes": 2.5}]}')
    # ô hỏi NotebookLM khoá nút gửi khi câu hỏi quá dài (3.292 ký tự gửi được, 3.924 thì không) → cắt bớt dữ kiện
    over = len(p_outline) - MAX_PROMPT
    if over > 0:
        short = _facts_text(facts, limit=max(600, len(facts_txt) - over - 20))
        p_outline = p_outline.replace(facts_txt, short)
        log(f"  Câu hỏi dài {len(p_outline) + len(facts_txt) - len(short)} ký tự → rút dữ kiện còn {len(short)} ký tự")
    ol = None
    for attempt in range(2):
        try:
            ol = _json_block(ask(jid, "outline", p_outline if attempt == 0 else
                                 "Bạn chưa trả về dàn ý. Không hỏi lại, không cần tìm thêm nguồn: hãy lập ngay dàn "
                                 "ý theo yêu cầu ở trên (dùng dữ kiện liên quan + kiến thức phổ biến đã xác nhận), "
                                 f"dạng JSON hợp lệ (dấu nháy kép thẳng, không khối code) giữa hai dòng {START} và {END}.",
                                 deadline, check))
            if all(k in ol for k in OUTLINE_KEYS) and ol["chapters"]:
                break
        except ValueError as e:
            log(f"NotebookLM: dàn ý lỗi ({e}) → hỏi lại")
            ol = None
    if not ol:
        raise Unavailable("NotebookLM không trả dàn ý hợp lệ")
    chapters = ol["chapters"]
    for c in chapters:
        if c.get("beat") not in planner.BEATS:
            c["beat"] = "explain"
        c.setdefault("summary", "")
        c.setdefault("key_takeaway", c.get("summary", ""))
    chapters[0]["beat"] = "hook" if chapters[0]["beat"] not in ("hook", "promise") else chapters[0]["beat"]
    mins = [max(0.3, float(c.get("minutes") or 0) or cfg.target_minutes / len(chapters)) for c in chapters]
    scale = cfg.target_minutes / sum(mins)
    log("Dàn ý: " + ol["title"] + " | " + " → ".join(f"[{c['beat']}] {c['title']}" for c in chapters))

    pipeline.sub(jid, "nb_outline", "done", note=f"{len(chapters)} chương")
    story = ""
    for i, c in enumerate(chapters):
        progress(i + 1, len(chapters) + 1)
        pipeline.sub(jid, "nb_chapters", done=i, total=len(chapters))
        w = max(60, round(mins[i] * scale * cfg.words_per_minute))
        first = (f"Chương này PHẢI mở đầu bằng hook: \"{ol['hook']}\"\n" if i == 0 else "")
        last = (f"Kết thúc video bằng lời kêu gọi: {ch['cta']}\n" if i == len(chapters) - 1 else
                f"Kết chương bằng câu treo dẫn sang chương {i + 2}.\n")
        # ô hỏi NotebookLM giới hạn độ dài (prompt ~5200 ký tự không gửi được): dữ kiện đã nằm trong cuộc trò
        # chuyện (lượt dàn ý) nên chỉ nhắc lại, không gửi lại
        prompt = (
            f"Tiếp tục video '{ol['title']}' theo dàn ý bạn vừa lập. Dùng các dữ kiện ở lượt đầu (bỏ qua dữ kiện lạc "
            "đề; thiếu thì dùng kiến thức phổ biến đã xác nhận, diễn đạt thận trọng, không bịa số liệu).\n"
            + (f"Đoạn cuối phần đã viết: \"...{story[-400:]}\"\n" if story else "")
            + f"Viết LỜI ĐỌC đầy đủ cho chương {i + 1}: {c['title']} — khoảng {w} chữ.\n{first}{last}"
            "Yêu cầu: tiếng Việt nói tự nhiên như kể chuyện cho bạn nghe, câu ngắn, nhịp nhanh, nhiều hình ảnh cụ "
            "thể, câu hỏi tu từ, chi tiết bất ngờ; không lặp ý chương trước; không tiêu đề, không gạch đầu dòng, "
            "không ghi chú cảnh/âm thanh, không emoji; con số viết bằng chữ. Trước khi trả lời hãy tự đọc lại, cắt "
            "chỗ nhàm và làm câu mở đầu, câu kết thật cuốn.\n"
            f"Chỉ trả về lời đọc nằm giữa hai dòng {START} và {END}.")
        text = clean_narration(ask(jid, f"chapter_{i + 1}", prompt, deadline, check))
        if _words(text) < w * 0.5:
            log(f"  Chương {i + 1} quá ngắn ({_words(text)}/{w} chữ) → yêu cầu viết đầy đủ hơn")
            more = clean_narration(ask(jid, f"chapter_{i + 1}_more",
                                       f"Lời đọc chương {i + 1} mới có {_words(text)} chữ, cần khoảng {w} chữ. Viết lại "
                                       f"đầy đủ hơn với thêm ví dụ cụ thể từ dữ kiện. Chỉ trả về lời đọc giữa {START} "
                                       f"và {END}.", deadline, check))
            if _words(more) > _words(text):
                text = more
        if not text:
            raise Unavailable(f"NotebookLM trả lời rỗng ở chương {i + 1}")
        c["text"] = text
        story += " " + text
        log(f"  Chương {i + 1}/{len(chapters)}: {_words(text)} chữ")
    pipeline.sub(jid, "nb_chapters", "done", done=len(chapters), total=len(chapters))
    got = _words(story)
    log(f"NotebookLM: kịch bản {got} chữ ≈ {got / cfg.words_per_minute:.1f} phút (mục tiêu {cfg.target_minutes})")
    ol["chapters"] = chapters
    ol["engine"] = "notebooklm"
    return ol


IMG_RULES = (
    "Mỗi prompt bằng TIẾNG ANH, 45-90 từ, cho MỘT tranh minh hoạ vẽ tay kiểu whiteboard (nét mực xám đậm trên giấy "
    "be, tinh thần tranh biếm hoạ New Yorker): thể hiện ĐÚNG điều lời đọc nói – ai, đang làm gì, ở đâu; 3-6 yếu tố "
    "lấy từ danh từ/hành động trong lời đọc; một điểm nhấn chính; nhân vật có biểu cảm; bối cảnh phác nhẹ; ẩn dụ "
    "hình ảnh khi câu trừu tượng; nói rõ vị trí (trái/giữa/phải, gần/xa). TUYỆT ĐỐI không chữ, không số, không "
    "nhãn, không số áo, không logo, không tên riêng (tả người/vật bằng ngoại hình); không tô nền màu; chừa trống "
    "1/5 phía dưới.")


def image_prompts(jid: int, ol: dict, rows: list, cfg: Cfg, log, check=lambda: None, idx: list | None = None,
                  batch: int = 7) -> int:
    """NotebookLM viết image_prompt cho các cảnh (theo lô, mỗi câu hỏi < MAX_PROMPT ký tự). Qwen chỉ giữ phần
    cấu trúc (visual_type, drawing_order…) và QC. Trả về số cảnh đã viết; lỗi → giữ prompt cũ của Qwen."""
    idx = list(range(len(rows))) if idx is None else idx
    deadline = time.time() + float((cfg.get("script") or {}).get("wait_minutes", 25)) * 60 * 2
    done = 0
    k = 0
    while k < len(idx):
        part = idx[k:k + batch]
        head = (f"Video '{ol.get('title', '')}'. Viết prompt tạo ảnh (Google Flow / Nano Banana) cho từng cảnh dưới "
                f"đây. {IMG_RULES}\n")
        tail = (f"\nChỉ trả về JSON (không khối code) giữa hai dòng {START} và {END}, dạng "
                '{"prompts": [{"i": số cảnh, "image_prompt": "...", "drawing_order": ["2-4 vật tiếng Anh theo thứ tự lời đọc nhắc tới"]}]}')
        body = "\n".join(f"Cảnh {i}: {rows[i]['narration']}" for i in part)
        while len(head + body + tail) > MAX_PROMPT and len(part) > 1:      # lô quá dài → bớt cảnh
            part = part[:-1]
            body = "\n".join(f"Cảnh {i}: {rows[i]['narration']}" for i in part)
        k += len(part)
        try:
            res = _json_block(ask(jid, f"img_{part[0]}", head + body + tail, deadline, check))
        except (Unavailable, ValueError) as e:
            log(f"  NotebookLM prompt ảnh cảnh {part[0]}–{part[-1]} lỗi ({e}) → giữ prompt của Qwen")
            if isinstance(e, Unavailable) and paused():
                break
            continue
        for p in res.get("prompts", []):
            try:
                i = int(p.get("i"))
            except (TypeError, ValueError):
                continue
            txt = re.sub(r"\s+", " ", str(p.get("image_prompt") or "")).strip()
            if i in part and len(txt.split()) >= 15:
                rows[i]["image_prompt"] = txt
                rows[i]["prompt_by"] = "notebooklm"
                # QC (Qwen) chấm theo ý chính + thứ tự vẽ → lấy từ chính prompt của NotebookLM cho khớp
                rows[i]["visual_concept"] = re.split(r"(?<=[.!?])\s", txt, 1)[0][:300]
                order = [str(x).strip() for x in (p.get("drawing_order") or []) if str(x).strip()]
                if order:
                    rows[i]["drawing_order"] = order[:4]
                done += 1
        log(f"  NotebookLM viết prompt ảnh: {min(k, len(idx))}/{len(idx)} cảnh")
    return done


def script_md(ol: dict) -> str:
    out = [f"# {ol.get('title', '')}", "", f"**Hook:** {ol.get('hook', '')}", ""]
    for i, c in enumerate(ol.get("chapters", [])):
        out += [f"## {i + 1}. [{c.get('beat')}] {c.get('title')}", "", c.get("text", ""), ""]
    return "\n".join(out)
