import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
CHANNEL_PATH = ROOT / "channel.yaml"
WPM_PATH = ROOT / "data" / "wpm.json"


class Cfg(dict):
    def __getattr__(self, k):
        v = self[k]
        return Cfg(v) if isinstance(v, dict) else v


def load() -> Cfg:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["bible"] = yaml.safe_load(CHANNEL_PATH.read_text(encoding="utf-8"))
    try:
        cfg["words_per_minute"] = json.loads(WPM_PATH.read_text(encoding="utf-8"))["wpm"]
    except (OSError, ValueError, KeyError):
        pass
    return Cfg(cfg)


def save_wpm(measured: float, old: float) -> float:
    """Tốc độ đọc thật của TTS, làm mượt với giá trị cũ để job sau ước lượng đúng số chữ."""
    wpm = round(0.5 * old + 0.5 * measured)
    WPM_PATH.write_text(json.dumps({"wpm": wpm}), encoding="utf-8")
    return wpm


def path(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else (ROOT / q).resolve()
