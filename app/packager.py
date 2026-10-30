"""Mỗi job một thư mục: projects/<id>_<tên>/ chứa video final, kịch bản, lời đọc, ảnh đã dùng (đúng thứ tự cảnh),
ảnh bị loại, giọng từng cảnh, báo cáo thời gian + điểm. Mở thư mục, xoá job (đưa vào Thùng rác, khôi phục được)."""
import json
import os
import re
import shutil
from pathlib import Path

from . import autoflow, db, pipeline
from .config import Cfg, ROOT, path

PROJECTS = ROOT / "projects"


def slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s or "", flags=re.U).strip()
    return re.sub(r"\s+", "_", s)[:60] or "video"


def folder_of(jid: int) -> Path:
    j = db.q("SELECT id, topic, folder FROM jobs WHERE id=?", (jid,))
    if not j:
        raise KeyError(jid)
    j = j[0]
    if j.get("folder") and Path(j["folder"]).exists():
        return Path(j["folder"])
    return PROJECTS / f"{j['id']:04}_{slug(j['topic'])}"


def glob_escape(s: str) -> str:
    import glob
    return glob.escape(s)


def _copy(src, dst: Path):
    try:
        if src and Path(src).exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.stat().st_size != Path(src).stat().st_size:
                shutil.copy2(src, dst)
            return True
    except OSError:
        pass
    return False


def package(cfg: Cfg, jid: int, work: Path | None = None, log=print) -> Path:
    job = db.q("SELECT * FROM jobs WHERE id=?", (jid,))[0]
    out = folder_of(jid)
    ex = autoflow.job_dir(cfg, jid)
    for sub in ("video", "kich_ban", "anh/khong_dat", "giong"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    # video final + phụ đề cùng tên
    if job.get("output") and Path(job["output"]).exists():
        v = Path(job["output"])
        dst = out / "video" / v.name
        if v.resolve() != dst.resolve():
            # CHUYỂN (không chép) video final + phụ đề vào thư mục job – không để 2 bản trùng ở output/
            for s in list(v.parent.glob(glob_escape(v.stem) + ".*")):
                if s.suffix.lower() in (".mp4", ".srt", ".ass", ".vtt", ".jpg", ".png"):
                    t = out / "video" / s.name
                    if t.exists():
                        t.unlink()
                    shutil.move(str(s), str(t))
            db.update_job(jid, output=str(dst))
            job["output"] = str(dst)
    # kịch bản, nguồn, dữ kiện
    for name in ("script.md", "sources.md", "notebook_script.json", "report.json", "web_pages.json"):
        _copy(ex / name, out / "kich_ban" / name)
    if job.get("facts"):
        (out / "kich_ban" / "facts.json").write_text(job["facts"], encoding="utf-8")
    sc = db.scenes(jid)
    lines, canh = [], []
    for s in sc:
        d = db.scene_data(s)
        n = s["idx"] + 1
        lines.append(f"[Cảnh {n}] {s['narration']}")
        img = f"anh/canh_{n:03}.png" if _copy(s.get("image"), out / "anh" / f"canh_{n:03}.png") else None
        wav = f"giong/canh_{n:03}.wav" if _copy(s.get("audio"), out / "giong" / f"canh_{n:03}.wav") else None
        canh.append({"canh": n, "chuong": s.get("chapter"), "loi_doc": s["narration"],
                     "prompt_anh": d.get("image_prompt"), "prompt_by": d.get("prompt_by", "qwen"),
                     "giay": round((s.get("duration_ms") or 0) / 1000, 2), "anh": img, "giong": wav})
    (out / "kich_ban" / "loi_doc.txt").write_text("\n\n".join(lines), encoding="utf-8")
    (out / "kich_ban" / "canh.json").write_text(json.dumps(canh, ensure_ascii=False, indent=1), encoding="utf-8")
    for rej in ("rejected", "rejected_ui"):
        for f in (ex / rej).glob("*.png") if (ex / rej).exists() else []:
            _copy(f, out / "anh" / "khong_dat" / f.name)
    # lý do QC từng cảnh (ảnh không đạt / số lần tạo lại)
    qc = [{"canh": s["idx"] + 1, "lan_tao": s.get("image_tries") or 0,
           "qc": json.loads(s["qc"]) if s.get("qc") else None} for s in sc]
    (out / "anh" / "khong_dat" / "qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=1), encoding="utf-8")
    if (cfg.get("keep_clips") or False):
        for s in sc:
            _copy(s.get("clip"), out / "clip" / f"canh_{s['idx'] + 1:03}.mp4")
    # kiểu Nikola: đủ bộ sản phẩm theo repo (ảnh màn, annotation, SRT, giọng, timeline, clip màn, manifest, kiểm tra)
    nk = Path(work) / "nikola" if work else None
    if nk and nk.exists():
        for f in nk.iterdir():
            if f.is_file() and f.suffix.lower() in (".png", ".json", ".srt", ".ass", ".txt", ".wav", ".mp4"):
                _copy(f, out / "nikola" / f.name)
    _media_extras(job, out, canh)
    write_report(jid, out)
    db.update_job(jid, folder=str(out))
    n_img = sum(1 for c in canh if c["anh"])
    log(f"📦 Đóng gói job {jid} → {out} ({n_img} ảnh, {sum(1 for c in canh if c['giong'])} file giọng)")
    return out


def _media_extras(job: dict, out: Path, canh: list):
    """Ảnh bìa (khung ở 12% video, không có thì ảnh cảnh đầu) + giọng đọc gộp giong_full.wav (mono 24 kHz)."""
    import subprocess
    from .llm import NO_WINDOW
    v = Path(job["output"]) if job.get("output") else None
    cover = out / "video" / "anh_bia.jpg"
    if v and v.exists():
        try:
            if not cover.exists():
                dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                            "csv=p=0", str(v)], capture_output=True, text=True, timeout=60,
                                           creationflags=NO_WINDOW).stdout.strip() or 0)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{dur * 0.12:.1f}", "-i", str(v),
                                "-frames:v", "1", "-q:v", "3", str(cover)], timeout=120, creationflags=NO_WINDOW)
            full = out / "giong" / "giong_full.wav"
            if not full.exists():
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(v), "-vn", "-ac", "1", "-ar", "24000",
                                str(full)], timeout=600, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if not cover.exists():
        first = next((c["anh"] for c in canh if c["anh"]), None)
        if first:
            _copy(out / first, cover)


def write_report(jid: int, out: Path):
    job = db.q("SELECT * FROM jobs WHERE id=?", (jid,))[0]
    t = pipeline.view(jid)
    m = pipeline.metrics(jid, t)
    scores = json.loads(job["scores"]) if job.get("scores") else {}
    rep = {"job": jid, "chu_de": job["topic"], "thoi_gian": t, "chi_so": m, "diem": scores,
           "phien_ban": json.loads(job["versions"]) if job.get("versions") else {}}
    (out / "bao_cao.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = "".join(
        f"<tr><td>{s['icon']} {s['name']}</td><td>{pipeline.fmt(s['active_sec'])}</td>"
        f"<td><div class=b style='width:{(100 * s['active_sec'] / max(1, t['active_sec'])):.1f}%'></div></td>"
        f"<td>{s['state']}</td></tr>" for s in t["steps"])
    html = f"""<!doctype html><meta charset=utf-8><title>Báo cáo job {jid}</title>
<style>body{{font:15px system-ui;background:linear-gradient(135deg,#1e1b4b,#0f766e);color:#f8fafc;padding:32px}}
table{{border-collapse:collapse;width:100%;background:#ffffff14;border-radius:12px;overflow:hidden}}
td,th{{padding:10px 14px;border-bottom:1px solid #ffffff22}}.b{{height:12px;border-radius:6px;
background:linear-gradient(90deg,#f472b6,#facc15,#22d3ee)}}h1{{margin:0 0 6px}}.k{{opacity:.8}}</style>
<h1>🎬 {job['topic']}</h1><p class=k>Thời gian chạy thật: <b>{pipeline.fmt(t['active_sec'])}</b> · tổng (kể cả chờ):
<b>{pipeline.fmt(t['wall_sec'])}</b> · điểm: <b>{scores.get('total', '')}</b></p>
<table><tr><th>Quy trình</th><th>Thời gian</th><th>Tỉ lệ</th><th>Trạng thái</th></tr>{rows}</table>
<h2>Chỉ số</h2><p class=k>{m['so_canh']} cảnh · {m['phut_video']} phút video ·
{m['phut_lam_moi_phut_video'] or '–'} phút làm / 1 phút video · ảnh đạt QC ngay lần đầu: {m['qc_dat_lan_dau_pct'] or '–'}% ·
giây/cảnh: {', '.join(f'{k} {v}s' for k, v in m['giay_moi_canh'].items())}</p>
<p class=k>Profile Flow: {', '.join(f"{p['ten']} {p['anh']} ảnh ({p['giay_moi_anh']}s/ảnh)" for p in m['profile']) or '–'}</p>
{'<p class=k>⚠ Thời gian của job này là ước tính từ nhật ký.</p>' if t.get('estimated') else ''}"""
    yt = out / "youtube"
    if (yt / "youtube.json").exists():
        import html as _h
        y = json.loads((yt / "youtube.json").read_text(encoding="utf-8"))
        thumb = "<img src='youtube/thumbnail.jpg' style='width:480px;border-radius:10px'>" \
            if (yt / "thumbnail.jpg").exists() else ""
        html += (f"<h2>📺 Bộ đăng YouTube</h2>{thumb}"
                 + "".join(f"<p class=k><b>{lang.upper()}:</b> {_h.escape((y[lang]['titles'] or [''])[0])}</p>"
                           for lang in ("vi", "en") if y.get(lang))
                 + "<p class=k>Mô tả đầy đủ: youtube/description_vi.txt · description_en.txt · tags.txt</p>")
    (out / "bao_cao.html").write_text(html, encoding="utf-8")


def open_folder(cfg: Cfg, jid: int) -> Path:
    f = folder_of(jid)
    if not f.exists():
        f = autoflow.job_dir(cfg, jid)       # job chưa đóng gói → mở thư mục làm việc (ảnh, kịch bản đang ra)
    os.startfile(str(f))
    return f


def size_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6 if p.exists() else 0.0


def delete_job(cfg: Cfg, jid: int) -> dict:
    """Xoá job khỏi hàng đợi + đưa các thư mục của nó vào Thùng rác (không xoá vĩnh viễn)."""
    from send2trash import send2trash
    job = db.q("SELECT * FROM jobs WHERE id=?", (jid,))
    if not job:
        raise KeyError(jid)
    job = job[0]
    if job["status"] == "running":
        raise RuntimeError("job đang chạy – dừng hoặc chờ xong rồi mới xoá")
    dirs = [folder_of(jid), path(cfg.get("autoflow", {}).get("exchange_dir", "exchange")) / f"job_{jid}",
            path(cfg.work_dir) / f"job_{jid}"]
    files = [Path(job["output"])] if job.get("output") else []
    moved, mb = [], 0.0
    for p in dirs + files:
        if p.exists():
            mb += size_mb(p) if p.is_dir() else p.stat().st_size / 1e6
            send2trash(str(p))
            moved.append(str(p))
    for t in ("scenes", "flow_tasks", "nb_tasks"):
        db.x(f"DELETE FROM {t} WHERE job_id=?", (jid,))
    db.x("DELETE FROM jobs WHERE id=?", (jid,))
    try:
        topic_title = (job.get("topic") or "").strip()
        if topic_title:
            d_list = db.kv_get("deleted_topics") or []
            if topic_title not in d_list:
                d_list.append(topic_title)
                db.kv_set("deleted_topics", d_list[-300:])
    except Exception:
        pass
    return {"deleted": jid, "trash": moved, "mb": round(mb, 1)}


def list_files(jid: int) -> dict:
    f = folder_of(jid)
    base = f if f.exists() else None
    if not base:
        return {"folder": str(f), "files": []}
    return {"folder": str(f), "files": [str(p.relative_to(PROJECTS)).replace("\\", "/")
                                        for p in sorted(base.rglob("*")) if p.is_file()][:2000]}
