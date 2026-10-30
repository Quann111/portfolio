import random
import re
from pathlib import Path

from . import complexity, procs, visual_qc
from .visual_rules import FIX_TEXT, PROMPT_RULE, QC_TEXT, strip_legacy
from .config import Cfg, path
from .llm import LLM

QC_SCHEMA = {
    "type": "object",
    "properties": {
        "semantic": {"type": "number"}, "style": {"type": "number"}, "composition": {"type": "number"},
        "continuity": {"type": "number"}, "timing_match": {"type": "number"}, "has_text": {"type": "boolean"},
        "overall": {"type": "number"}, "issues": {"type": "string"},
    },
    "required": ["semantic", "style", "composition", "continuity", "timing_match", "has_text", "overall",
                 "issues"],
}


def local_available(cfg: Cfg) -> bool:
    """Còn model + sd.cpp để tạo ảnh local không (đã có thể bị xoá khi dùng Google Flow)."""
    c = cfg.image
    try:
        _exe(c)
    except FileNotFoundError:
        return False
    return all(path(c[k]).exists() for k in ("diffusion_model", "t5xxl", "clip_l", "vae"))


def _exe(c) -> Path:
    p = path(c.sd_exe)
    if p.exists():
        return p
    for name in ("sd-cli.exe", "sd.exe"):
        alt = p.parent / name
        if alt.exists():
            return alt
    raise FileNotFoundError(f"Không thấy stable-diffusion.cpp: {p} (chạy setup.ps1)")


SIMPLIFY = ("Cleaner version: keep the main subject and the key action, drop small background details and "
            "textures, fewer lines overall")


def fix_instruction(qc: dict, scene: dict) -> str:
    """Lượt tạo lại: chỉ dẫn sửa theo TIÊU CHÍ bị trượt (không dán nguyên nhận xét QC – câu đó thường chỉ mô tả
    ảnh, cắt ngang 300 ký tự thành câu vô nghĩa với Flow)."""
    f = set(qc.get("failed_gates") or [])
    out = []
    if f & {"semantic_score", "alignment"}:
        first = (scene.get("drawing_order") or [""])[0]
        out.append(f"Show the idea unmistakably: {scene.get('visual_concept', '')}"
                   + (f"; the {first} must be clearly visible" if first else ""))
    if "has_text" in f:
        out.append(FIX_TEXT)
    if "whiteboard_suitability_score" in f:
        out.append("fewer tiny details and no textures or crowds, keep the main elements")
    if "visual_quality_score" in f:
        out.append("clean confident lines, whole subject inside the frame, plain paper background")
    if "continuity_score" in f:
        out.append("same line weight and beige paper look as the rest of the series")
    return "; ".join(out)


def build_prompt(scene: dict, vb: dict, cfg: Cfg, extra_fix: str = "", simplify: bool = False,
                 target: str = "local") -> str:
    if target == "flow":
        return _build_flow(scene, vb, cfg, extra_fix, simplify)
    return _build(scene, vb, cfg, extra_fix, simplify)


NO_WRITING = PROMPT_RULE            # luật chung app/visual_rules.py (số liệu, cờ, chân dung được; cấm chữ rác)


def _build_flow(scene: dict, vb: dict, cfg: Cfg, extra_fix: str = "", simplify: bool = False,
                limit: int | None = None) -> str:
    """Nano Banana (Google Flow): luật cứng (khổ, phong cách, cấm chữ, tránh) đặt TRƯỚC, mô tả cảnh sau – prompt
    dài bị cắt thì chỉ mất đuôi mô tả, không mất luật."""
    v = cfg.bible["visual"]
    limit = limit or int((cfg.get("autoflow") or {}).get("prompt_max_chars", 2400))
    head = ". ".join(["Wide 16:9 hand-drawn whiteboard explainer illustration, New Yorker cartoon spirit",
                      v["style"], v.get("line_style", ""), NO_WRITING,
                      "Keep the bottom fifth of the picture empty paper", "Avoid: " + v["avoid"]]) + ". SCENE: "
    text = (scene.get("image_prompt", "") + " " + scene.get("visual_concept", "")).lower()
    refs = [f"{x['name']}: {x['look']}" for x in vb.get("characters", []) + vb.get("objects", [])
            if any(w in text for w in re.findall(r"\w{4,}", x["name"].lower()))]
    vt = scene.get("visual_type")
    ip = strip_legacy(scene["image_prompt"])
    body = [f"A {vt.replace('_', ' ')} illustration: {ip}" if vt else ip]
    if simplify:
        body.append(SIMPLIFY)
    elif extra_fix:
        body.append("Fix: " + extra_fix[:300])
    if scene.get("main_object") and not simplify:
        body.append(f"Focal point: {scene['main_object']}")
    if scene.get("composition") and not simplify:
        body.append(f"Layout: {scene['composition']}")
    if refs and not simplify:
        body.append("Consistent designs – " + "; ".join(refs))
    p = head + ". ".join(body)
    if len(p) > limit:
        p = p[:limit].rsplit(" ", 1)[0]
    return p


def _build(scene: dict, vb: dict, cfg: Cfg, extra_fix: str = "", simplify: bool = False) -> str:
    v = cfg.bible["visual"]
    text = (scene.get("image_prompt", "") + " " + scene.get("visual_concept", "")).lower()
    refs = [f"{x['name']}: {x['look']}" for x in vb.get("characters", []) + vb.get("objects", [])
            if any(w in text for w in re.findall(r"\w{4,}", x["name"].lower()))]
    vt = scene.get("visual_type")
    ip = strip_legacy(scene["image_prompt"])
    parts = [f"A {vt.replace('_', ' ')} style doodle: {ip}" if vt else ip]
    main, sec = scene.get("main_object"), scene.get("secondary_objects") or []
    if main and not simplify:
        parts.append(f"Main subject drawn LARGE and prominent: {main}"
                     + (f"; smaller supporting elements: {', '.join(sec[:2])}" if sec else ""))
    if scene.get("composition") and not simplify:
        parts.append(f"Layout: {scene['composition']}")
    parts.append("Keep the bottom fifth of the picture completely empty paper")
    parts.append(PROMPT_RULE)
    if refs and not simplify:
        parts.append("Consistent designs – " + "; ".join(refs))
    if simplify:
        parts.append(SIMPLIFY)
    if v.get("line_style"):
        parts.append(v["line_style"])
    parts.append(v["style"])
    # FLUX-schnell (cfg=1) bỏ qua negative prompt nên đưa yêu cầu "tránh" vào prompt chính
    parts.append("Strictly avoid: " + v["avoid"])
    if extra_fix and not simplify:
        parts.append("Fix: " + extra_fix[:300])
    return ". ".join(parts)


def generate(prompt: str, out: Path, cfg: Cfg, seed: int | None = None, init: Path | None = None,
             mask: Path | None = None, strength: float = 0.95) -> int:
    c = cfg.image
    exe = _exe(c)
    seed = seed if seed is not None else random.randint(1, 2**31 - 1)
    cmd = [str(exe),
           "--diffusion-model", str(path(c.diffusion_model)),
           "--t5xxl", str(path(c.t5xxl)), "--clip_l", str(path(c.clip_l)), "--vae", str(path(c.vae)),
           "-p", prompt, "--cfg-scale", "1.0", "--sampling-method", "euler",
           "--steps", str(c.steps), "-W", str(c.width), "-H", str(c.height),
           "-s", str(seed), "-o", str(out), *c.extra_args]
    if init:
        cmd += ["-i", str(init), "--strength", str(strength)]
        if mask:
            cmd += ["--mask", str(mask)]
    r = procs.run(cmd, timeout=900, cwd=str(exe.parent))
    if r.returncode != 0 or not out.exists():
        log = (r.stderr or "") + (r.stdout or "")
        tail = [ln for ln in log.splitlines() if any(k in ln.lower() for k in ("error", "fail", "out of memory",
                                                                               "abort", "exception"))]
        raise RuntimeError(f"sd.cpp lỗi (mã {r.returncode}): " + (" | ".join(tail[-5:]) or log[-800:]))
    return seed


def check(image: Path, scene: dict, prev: Path | None, llm: LLM, cfg: Cfg, speech_ms: int = 0) -> dict:
    # QC nhanh: chỉ gửi 1 ảnh cho Qwen; độ đồng nhất với cảnh trước đo bằng OpenCV (visual_qc) bên dưới
    fast = (cfg.image.get("qc_fast", True))
    imgs = [image] + ([prev] if prev and not fast else [])
    prev_for_llm = prev if imgs[1:] else None
    res = llm.look(imgs,
        "You review illustrations for a simple whiteboard explainer video. Be fair, not pedantic: these are "
        "quick doodles, so missing small details, exact colours, arrow directions or exact positions are FINE.\n"
        f"Narration: \"{scene['narration']}\"\nMain idea to show: \"{scene.get('visual_concept', '')}\"\n"
        "Score the FIRST image, each 0-1:\n"
        "- semantic: would a viewer hearing the narration see the MAIN idea (right kind of subjects, roughly "
        "right relation)? 1 = clearly yes, 0.6 = partly, 0.2 = unrelated or misleading\n"
        "- style: plain light paper background, dark line-art, few colour accents, not photo/3D\n"
        "- composition: main subjects visible and reasonably sized, not cut off, not cluttered\n"
        + ("- continuity: is the DRAWING STYLE (line weight, colours, background) similar to the SECOND image? "
           "Different characters or scenes are fine.\n" if prev_for_llm else "- continuity: 1.0\n")
        + f"- timing_match: the narration mentions things in this order {scene.get('drawing_order', [])}; are "
          "the things mentioned FIRST clearly present and easy to spot, so the picture can be drawn in the same "
          "order as it is spoken? 1 = yes, 0.5 = some missing, 0.2 = the first-mentioned thing is absent\n"
          f"- {QC_TEXT}\n"
          "- overall: put 0 (computed elsewhere)\nList the most important issue briefly.", QC_SCHEMA)
    q = cfg.bible["quality"]
    cv = visual_qc.check(image, prev)
    cx = complexity.measure(image, speech_ms or 15000)
    res.update(cv, complexity=cx)
    # 4 điểm tách riêng, mỗi điểm có ngưỡng cứng – không cho điểm cao bù điểm thấp
    res["semantic_score"] = round(res["semantic"], 3)
    res["visual_quality_score"] = round((res["style"] + res["composition"] + cv["cv_quality"]) / 3, 3)
    res["whiteboard_suitability_score"] = round(min(cx["suitability"], 1.0), 3)
    res["continuity_score"] = round(res["continuity"] * (0.5 if cv["similarity_prev"] > 0.85 else 1.0), 3)
    res["alignment"] = round(0.65 * res["semantic"] + 0.35 * res["timing_match"], 3)
    res["overall"] = round(0.4 * res["semantic_score"] + 0.25 * res["visual_quality_score"]
                           + 0.2 * res["whiteboard_suitability_score"] + 0.15 * res["continuity_score"], 3)
    th = q.get("image_thresholds", {})
    gates = {
        "semantic_score": th.get("semantic", 0.7),
        "visual_quality_score": th.get("visual_quality", 0.65),
        "whiteboard_suitability_score": th.get("whiteboard_suitability", 0.6),
        "continuity_score": th.get("continuity", 0.5),
        "alignment": q.get("alignment_min", 0.82),
    }
    failed = [k for k, v in gates.items() if res[k] < v]
    if res["has_text"]:
        failed.append("has_text")
    res["failed_gates"] = failed
    res["pass"] = not failed
    if failed:
        extra = list(cv["cv_issues"])
        if "whiteboard_suitability_score" in failed:
            extra.append(f"too complex to draw in {cx['available_time']}s: fewer objects and lines")
        res["issues"] = "; ".join([res.get("issues", "")] + extra).strip("; ")
    return res


def failure_kind(res: dict) -> str:
    f = res.get("failed_gates", [])
    if "semantic_score" in f or "alignment" in f:
        return "semantic"
    if "whiteboard_suitability_score" in f:
        return "too_complex"
    return "visual"


def rank(res: dict) -> float:
    return (res["overall"] + res.get("alignment", res["overall"])) / 2 - (0.3 if res.get("has_text") else 0)
