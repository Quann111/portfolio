import re
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import ROOT
from .normalizer import normalize

LOG = ROOT / "data" / "pronunciation_log.txt"


def _plain(text: str, language: str) -> str:
    t = normalize(text, language).lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _edit(a, b) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def asr_error(script: str, heard: str, language: str) -> float:
    """Tỉ lệ lỗi ký tự sau khi bỏ dấu cách: 'block chain' == 'blockchain', hợp tiếng Việt tách âm tiết."""
    a = _plain(script, language).replace(" ", "")
    b = _plain(heard, language).replace(" ", "")
    if not a:
        return 0.0
    return _edit(a, b) / len(a)


def missing_words(script: str, heard: str, language: str) -> list[str]:
    """Thuật ngữ/tên nước ngoài (token toàn chữ Latin không dấu, ≥ 2 ký tự) còn lại SAU khi chuẩn hoá
    mà Whisper không nghe thấy. Không xét mảnh chữ tiếng Việt và từ đã có trong từ điển phát âm."""
    h = _plain(heard, language).replace(" ", "")
    spoken = normalize(script, language)
    out = []
    for tok in spoken.split():
        w = tok.strip(".,;:!?()\"'“”‘’…-")
        if len(w) < 2 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9+]*", w):
            continue
        if w.lower() not in h:
            out.append(w)
    return sorted(set(out))


def signal(wav: Path, text: str, speech_ms: int) -> dict:
    audio, sr = sf.read(str(wav), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    speech = audio[: int(sr * speech_ms / 1000)] if speech_ms else audio
    peak = float(np.abs(speech).max()) if speech.size else 0.0
    rms = float(np.sqrt(np.mean(speech ** 2))) if speech.size else 0.0
    frame = int(sr * 0.05)
    frames = speech[: len(speech) // frame * frame].reshape(-1, frame) if len(speech) >= frame else np.zeros((1, 1))
    quiet = np.sqrt((frames ** 2).mean(axis=1)) < 0.005
    longest, run = 0, 0
    for q in quiet:
        run = run + 1 if q else 0
        longest = max(longest, run)
    n_words = len(text.split())
    secs = max(0.1, len(speech) / sr)
    return {
        "peak": round(peak, 3), "clipping": peak >= 0.999,
        "loudness_dbfs": round(20 * np.log10(rms + 1e-9), 1),
        "longest_silence_s": round(longest * 0.05, 2),
        "words_per_sec": round(n_words / secs, 2),
    }


SUGGEST = ROOT / "data" / "pronunciation_suggestions.yaml"


def _question_rise(audio: np.ndarray, sr: int) -> float | None:
    """Tỉ lệ F0 trung bình 0.5s cuối / 1.5s trước đó; > 1 nghĩa là lên giọng."""
    try:
        import librosa
        y = librosa.resample(audio, orig_sr=sr, target_sr=16000)
        f0, voiced, _ = librosa.pyin(y, fmin=70, fmax=400, sr=16000, frame_length=1024)
        f0 = f0[voiced]
        if f0.size < 20:
            return None
        n_end = max(5, f0.size // 6)
        return float(np.nanmean(f0[-n_end:]) / np.nanmean(f0[-4 * n_end:-n_end]))
    except Exception:
        return None


def _mid_pauses(words: list) -> list[float]:
    return [round(b[1] - a[2], 2) for a, b in zip(words, words[1:]) if a[2] is not None and b[1] - a[2] > 0]


def features(wav: Path, speech_ms: int) -> dict:
    """Đặc trưng dùng để so độ đồng nhất giữa các cảnh."""
    audio, sr = sf.read(str(wav), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    y = audio[: int(sr * speech_ms / 1000)] if speech_ms else audio
    rms = float(np.sqrt(np.mean(y ** 2)) + 1e-9)
    try:
        import librosa
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13).mean(axis=1).tolist()
    except Exception:
        mfcc = []
    return {"dbfs": round(float(20 * np.log10(rms)), 2), "mfcc": [round(float(v), 3) for v in mfcc]}


def consistency(stats: list[dict]) -> list[int]:
    """Chỉ số cảnh lệch bất thường (âm lượng ±6dB, tốc độ ±35%, âm sắc) so với trung vị toàn video."""
    if len(stats) < 3:
        return []
    db_ = np.array([s["features"]["dbfs"] for s in stats])
    wps = np.array([s["words_per_sec"] for s in stats])
    bad = set(np.where(np.abs(db_ - np.median(db_)) > 6)[0])
    bad |= set(np.where(np.abs(wps / np.median(wps) - 1) > 0.35)[0])
    mf = [s["features"]["mfcc"] for s in stats]
    if all(mf):
        m = np.array(mf)
        d = np.linalg.norm(m - np.median(m, axis=0), axis=1)
        bad |= set(np.where(d > np.median(d) * 3 + 1e-6)[0])
    return sorted(int(i) for i in bad)


def _suggest(words: list[str], text: str):
    import yaml
    SUGGEST.parent.mkdir(parents=True, exist_ok=True)
    cur = yaml.safe_load(SUGGEST.read_text(encoding="utf-8")) if SUGGEST.exists() else {}
    cur = cur or {}
    for w in words:
        e = cur.setdefault(w, {"count": 0, "example": text[:120], "pronunciation": "?"})
        e["count"] += 1
    SUGGEST.write_text(yaml.safe_dump(cur, allow_unicode=True, sort_keys=True), encoding="utf-8")


def check(wav: Path, text: str, speech_ms: int, language: str, tts, cfg) -> dict:
    s = signal(wav, text, speech_ms)
    issues = []
    if s["clipping"]:
        issues.append("clipping")
    if s["loudness_dbfs"] < -35:
        issues.append("quá nhỏ")
    if s["longest_silence_s"] > 2.0:
        issues.append(f"im lặng {s['longest_silence_s']}s")
    if not 1.2 <= s["words_per_sec"] <= 5.5:
        issues.append(f"tốc độ bất thường {s['words_per_sec']} chữ/s")
    heard, words = tts.transcribe_words(wav)
    err = asr_error(text, heard, language)
    s.update(asr_error=round(err, 3), heard=heard[:500], words=words)
    if err > cfg.bible["quality"].get("wer_max", 0.25):
        issues.append(f"đọc lệch kịch bản {err:.0%}")
    miss = missing_words(text, heard, language)
    if miss:
        s["pronunciation_suspects"] = miss
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{', '.join(miss)}\t| {text[:200]}\n")
        _suggest(miss, text)
        if err > cfg.bible["quality"].get("wer_max", 0.25) / 2:
            issues.append(f"nghi đọc sai thuật ngữ: {', '.join(miss)}")
    pauses = _mid_pauses(words)
    long_mid = [p for p in pauses if p > 1.2]
    if long_mid:
        issues.append(f"ngắt nghỉ giữa câu {max(long_mid)}s")
    s["mid_pauses_max"] = max(pauses) if pauses else 0
    if text.rstrip().endswith("?"):
        audio, sr = sf.read(str(wav), dtype="float32")
        audio = audio[: int(sr * speech_ms / 1000)] if speech_ms else audio
        rise = _question_rise(audio, sr)
        s["question_rise"] = rise
        if rise is not None and rise < 0.97:
            issues.append("câu hỏi không lên giọng")
    s["features"] = features(wav, speech_ms)
    s["issues"] = issues
    s["pass"] = not issues
    return s
