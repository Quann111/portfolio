"""Chuẩn bị một "thư mục phim" (film_dir) cho các tool render của vendor opuscar.

Vì sao cách này đúng (đối chiếu vendor `core/render/serve.mjs` + `page.mjs`):
- `serve.mjs` phục vụ static trên 127.0.0.1 với root = repo vendor; `page.mjs →
  pageURL()` thấy `dir` nằm NGOÀI repo vendor (ví dụ `app/opuscar/engine/_sample`
  hay tương lai `projects/<job>/opuscar/`) thì mount chính dir đó tại `/@film/`
  (biến `filmDir` trong serve.mjs): mọi request `/@film/...` được đọc thẳng từ
  film_dir, còn request khác (`/core/lib.js`, `/styles/...`) đọc từ repo vendor.
- Gốc của film_dir phải có `index.html`; trang nạp engine bằng URL tương đối
  (`engine/index.js`), và `engine/index.js` tự nạp `fonts/…` theo `import.meta.url`
  + ảnh theo đường dẫn tương đối của `content.json`. Như vậy chỉ cần CHÉP CỤC
  THỂ (copy, không link/junction) bộ engine runtime vào `film_dir/engine/` là
  cả cụm nạp hoàn toàn từ film_dir — không phụ thuộc vị trí engine gốc.
- Import tuyệt đối `/core/lib.js` trong JS của engine vẫn được vendor phục vụ từ
  repo gốc nên không cần (và không được) copy vendor. Vendor giữ nguyên vẹn.

CẤM tạo junction/symlink từ film_dir trỏ tới thư mục tổ tiên (điều đó gây vòng
lặp vô hạn cho mọi công cụ quét thư mục — đã từng xảy ra với `_sample/engine`).

Dùng:
    python app/opuscar/film.py <film_dir>     # chuẩn bị lại film_dir/engine
hoặc import `prepare_film(film_dir)` từ nơi khác (ví dụ ứng dụng render).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent / "engine"  # app/opuscar/engine

# bộ runtime tối thiểu để một film_dir tự chạy khi được vendor mount ở /@film/
_TOP_FILES = ("index.js", "core.js", "CREDITS")
_SUBDIRS = ("looks", "shots", "fonts")


def _reset_engine_dir(dst: Path) -> None:
    """Xoá dst cũ an toàn: nếu là junction/symlink thì chỉ gỡ 'mỏ', chấm dứt target.

    Dấu hiệu link/junction(Windows): os.path.realpath() trỏ tới nơi khác khỏi dst.
    So sánh bằng realpath trước tuyệt đối trước khi rmtree — nếu không, một
    junction trỏ lên thư mục tổ tiên có thể xoá nhầm engine thật.
    """
    if not dst.exists() and not dst.is_symlink():
        return
    if os.path.realpath(dst) != os.path.abspath(dst):
        os.rmdir(dst)  # junction/symlink tới thư mục khác: gỡ đúng chính nó
        return
    if dst.is_dir():
        shutil.rmtree(dst)
    else:
        dst.unlink()


def prepare_film(film_dir):
    """Chép engine runtime vào `<film_dir>/engine/` (copy thuần, không link).

    film_dir phải là "thư mục phim": có `index.html` ở gốc nạp `engine/index.js`.
    Trả về Path tới `film_dir/engine` vừa tạo (chép đè toàn bộ mỗi lần chạy, nên
    luôn phản chiếu engine hiện hành).
    """
    film_dir = Path(film_dir).resolve()
    if not (film_dir / "index.html").exists():
        raise SystemExit(
            f"[film.py] {film_dir} chưa có index.html — một thư mục phim opuscar "
            "phải có index.html ở gốc nạp engine/index.js (xem engine/README.md)"
        )
    src_names = [n for n in (*_TOP_FILES, *_SUBDIRS) if (ENGINE / n).exists()]
    if not src_names:
        raise SystemExit(f"[film.py] không tìm thấy engine runtime hợp lệ tại {ENGINE}")

    dst = film_dir / "engine"
    _reset_engine_dir(dst)
    dst.mkdir(parents=True)
    for name in src_names:
        src = ENGINE / name
        if src.is_dir():
            shutil.copytree(src, dst / name)
        else:
            shutil.copy2(src, dst / name)
    return dst


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python app/opuscar/film.py <film_dir>")
    out = prepare_film(sys.argv[1])
    print(f"[film.py] engine copied -> {out}")
