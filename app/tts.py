from pathlib import Path

import numpy as np
import soundfile as sf

from .config import Cfg, ROOT, path
from .normalizer import normalize
from .voices import VOICES_DIR

SR = 48000                     # ZeroTTS / VieNeu trả 48 kHz
VOICE_PT = ROOT / "data" / "voice.pt"
VOICE_REF = ROOT / "data" / "voice_ref.wav"
ASR_MODEL = "openai/whisper-large-v3-turbo"
ENGINES = ("zerotts", "vieneu")
_BROKEN: dict[str, str] = {}   # engine nạp lỗi trong phiên này → các job sau dùng thẳng fallback (không thử lại mỗi job)
REF_SENTENCES = {
    "vietnamese": "Xin chào các bạn, hôm nay chúng ta sẽ cùng nhau khám phá một câu chuyện thật thú vị.",
    "english": "Hello everyone, today we are going to explore a truly fascinating story together.",
}


class TTS:
    """Giọng đọc theo `tts.engine`:
    - zerotts: ZeroTTS, CPU (ONNX), 48 kHz, siêu nhanh, giọng chuẩn tiếng Việt (mặc định: baotrang)
    - vieneu: VieNeu-TTS v3 Turbo, CPU (ONNX), 48 kHz, clone từ `ref_audio` hoặc data/voice_ref.wav
    ASR cho Audio QC (Whisper) chạy riêng, không phụ thuộc engine."""

    def __init__(self, cfg: Cfg, log, engine: str | None = None, voice: str | None = None):
        self.c = cfg.tts
        self.lang = cfg.language.lower()
        self.log = log
        self.engine = (engine or self.c.get("engine") or "zerotts").lower()
        if self.engine in ("omnivoice", "voicebox"):
            self.engine = "zerotts"
        self.voice_sel = voice if voice is not None else (self.c.get("voice") or "baotrang")
        self.upload = VOICES_DIR / self.voice_sel[2:] if self.voice_sel.startswith("u:") else None
        self.gen_lang = {"vi": "vietnamese", "en": "english"}.get(self.voice_sel, self.lang)
        if self.engine not in ENGINES:
            self.engine = "zerotts"
        self.model = None
        self.prompt = None
        self.voice = None
        self.sr = SR
        self.asr = None

    # ------------------------------------------------------------------ load
    def _load(self):
        if self.model is not None:
            return
        fb = (self.c.get("fallback_engine") or "vieneu").lower()
        can_fb = fb and fb != self.engine and fb in ENGINES
        if self.engine in _BROKEN and can_fb:
            self.engine = fb
            getattr(self, f"_load_{fb}")()
            return
        try:
            getattr(self, f"_load_{self.engine}")()
        except Exception as e:
            if not can_fb:
                raise
            _BROKEN[self.engine] = str(e)
            self.log(f"Không load được giọng {self.engine} ({e}) -> dùng {fb} cho tới khi khởi động lại app")
            self.engine, self.model = fb, None
            getattr(self, f"_load_{fb}")()

    def _ref(self) -> tuple[str, str]:
        if self.upload:
            return str(self.upload), ""
        if self.c.ref_audio:
            return str(path(self.c.ref_audio)), self.c.ref_text or ""
        return str(VOICE_REF), REF_SENTENCES.get(self.lang, REF_SENTENCES["english"])

    def _load_vieneu(self):
        from vieneu import Vieneu
        ref, _ = self._ref()
        if not Path(ref).exists():
            raise FileNotFoundError(f"VieNeu cần file giọng mẫu: đặt tts.ref_audio hoặc {VOICE_REF}")
        self.log(f"Load VieNeu-TTS v3 Turbo (CPU), giọng mẫu {Path(ref).name}...")
        self.model = Vieneu(precision=self.c.get("precision", "fp32"))
        self.model.add_voice("autovideo", ref)
        self.voice = "autovideo"
        self.sr = int(getattr(self.model, "sample_rate", 48000) or 48000)

    def _load_zerotts(self):
        from zerotts import ZeroTTS
        self.log("Load ZeroTTS (CPU, 48 kHz)...")
        self.model = ZeroTTS.from_pretrained(
            "zeroweight-ai/ZeroTTS",
            providers=["CPUExecutionProvider"]
        )
        voices = self.model.list_voices()
        self.voice = self.voice_sel if self.voice_sel in voices else (self.c.get("voice") or "baotrang")
        if self.voice not in voices:
            self.voice = "baotrang"
        self.sr = int(getattr(self.model, "sample_rate", 48000) or 48000)

    # ------------------------------------------------------------------ đọc
    def _generate(self, text: str, speed: float) -> np.ndarray:
        if self.engine == "vieneu":
            audio = self.model.infer(normalize(text, self.lang), voice=self.voice)
        else:
            audio = self.model.synthesize(normalize(text, self.lang), voice=self.voice)
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        rate = self.c.speed * speed
        if abs(rate - 1.0) > 0.02:      # co giãn thời gian giữ cao độ
            import librosa
            audio = librosa.effects.time_stretch(audio, rate=rate)
        return audio

    def speak(self, text: str, out: Path, speed: float = 1.0, pause: float = 0.35,
              pause_before: float = 0.0) -> tuple[int, int, int]:
        """Trả về (tổng ms, ms phần lời nói, ms im lặng đầu)."""
        self._load()
        audio = self._generate(text, speed)
        sr = self.sr
        speech_ms = int(len(audio) * 1000 / sr)
        fade = min(len(audio) // 4, int(sr * 0.01))
        if fade:
            ramp = np.linspace(0.0, 1.0, fade, dtype=audio.dtype)
            audio[:fade] *= ramp
            audio[-fade:] *= ramp[::-1]
        lead = np.zeros(int(sr * max(0.0, pause_before)), dtype=audio.dtype)
        audio = np.concatenate([lead, audio, np.zeros(int(sr * pause), dtype=audio.dtype)])
        tmp = out.with_suffix(".tmp.wav")
        sf.write(tmp, audio, sr)
        tmp.replace(out)
        return int(len(audio) * 1000 / sr), speech_ms, int(len(lead) * 1000 / sr)

    # ------------------------------------------------------------------ nghe lại
    def transcribe(self, wav: Path) -> str:
        return self.transcribe_words(wav)[0]

    def _asr(self):
        if self.asr is not None:
            return self.asr
        import torch
        from transformers import pipeline
        cuda = torch.cuda.is_available()
        self.log(f"Load Whisper (ASR) cho Audio QC ({'GPU' if cuda else 'CPU'})...")
        self.asr = pipeline("automatic-speech-recognition", model=self.c.get("asr_model") or ASR_MODEL,
                            dtype=torch.float16 if cuda else torch.float32, device="cuda:0" if cuda else "cpu")
        return self.asr

    def transcribe_words(self, wav: Path) -> tuple[str, list]:
        """(văn bản, [(từ, giây_bắt_đầu, giây_kết_thúc)]) – timestamp theo từ trên audio cuối cùng."""
        asr = self._asr()
        try:
            r = asr(str(wav), return_timestamps="word")
            words = [(c["text"].strip(), c["timestamp"][0], c["timestamp"][1])
                     for c in r.get("chunks", []) if c.get("timestamp") and c["timestamp"][0] is not None]
            return r["text"].strip(), words
        except Exception:
            return asr(str(wav))["text"].strip(), []

    def release(self):
        if self.model is None and self.asr is None:
            return
        import torch
        self.model = None
        self.prompt = None
        self.asr = None
        torch.cuda.empty_cache()
