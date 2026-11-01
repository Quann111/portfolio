import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
CHANNEL_PATH = ROOT / "channel.yaml"
WPM_PATH = ROOT / "data" / "wpm.json"


class Cfg(dict):
    def __getattr__(self, k):
        try:
            v = self[k]
        except KeyError:
            raise AttributeError(k) from None     # hasattr()/getattr(cfg, k, mặc định)/copy/pickle hoạt động đúng
        if isinstance(v, dict) and not isinstance(v, Cfg):
            v = self[k] = Cfg(v)                   # giữ cùng 1 đối tượng → cfg.image["x"] = ... không bị mất
        return v


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
    WPM_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = WPM_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"wpm": wpm}), encoding="utf-8")
    tmp.replace(WPM_PATH)                          # ghi nguyên tử – tắt giữa chừng không làm hỏng file
    return wpm


def path(p: str) -> Path:
    q = Path(p).expanduser()          # cho phép "~/Downloads/..." (không cứng tên user)
    return q if q.is_absolute() else (ROOT / q).resolve()
