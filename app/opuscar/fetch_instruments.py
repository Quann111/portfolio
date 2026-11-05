"""Download the opuscar sample libraries on Windows: Python port of the upstream tools/fetch.sh
("instruments <lib>" part), using only urllib + tarfile (no curl needed).

The upstream script fetches tools/assets.json and unpacks each release pack at the library root,
so that core/audio/instruments/<lib>/ appears where vendor/opuscar/core/audio/sampler.py looks for
it (sampler.ROOT). The manifest snapshot below pins the same data (repo, tag, file, size, sha256)
from commit bebbbe1aa92a3cb4bb6f83500e61b23a17d1aa00 so --list works offline.

CLI: python -m app.opuscar.fetch_instruments freepats|karoryfer|salamander|vcsl|vsco2ce|all [--list]
     --list prints the sources + sizes only; nothing is downloaded.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request

# Snapshot of tools/assets.json @ bebbbe1aa92a3cb4bb6f83500e61b23a17d1aa00 (instruments packs only)
MANIFEST_URL = 'https://raw.githubusercontent.com/lemomo-ai/lemo-opuscar/bebbbe1aa92a3cb4bb6f83500e61b23a17d1aa00/tools/assets.json'
MANIFEST = {
    'repo': 'lemomo-ai/lemo-opuscar',
    'tag': 'assets',
    'packs': {
        'instruments-freepats':   {'file': 'instruments-freepats.tar',   'size': 415959040,  'sha256': 'ae73707a7c88c8b985cedead751ff52ff8bfcaa3a48fa07ca6f45c0c6da00c83'},
        'instruments-karoryfer':  {'file': 'instruments-karoryfer.tar',  'size': 77772800,   'sha256': 'b72822ac2f6569ed33b376448784d542bf039478fbf521db0c4bdc7ee766cac4'},
        'instruments-salamander': {'file': 'instruments-salamander.tar', 'size': 229150720,  'sha256': '7c6a543de04daf6fdd7337ae08bf0484bec2a78b902ca80748bca927f42e189c'},
        'instruments-vcsl':       {'file': 'instruments-vcsl.tar',       'size': 284682240,  'sha256': '3ed8f753500e629485e323fc7f883b35aca6a77a1ab04522d1d47037d503f6e1'},
        'instruments-vsco2ce':    {'file': 'instruments-vsco2ce.tar',    'size': 413911040,  'sha256': '231297103cb4a3123707ddb7b284e5ea3aed0b8d77c0b891360b4d3efe8b586f'},
    },
}
LIBS = ['freepats', 'karoryfer', 'salamander', 'vcsl', 'vsco2ce']

# the vendored library root: this file is <autovideo>/app/opuscar/fetch_instruments.py,
# samples must land in <autovideo>/vendor/opuscar/core/audio/instruments/<lib>/
# (sampler.py: ROOT = .../core/audio/instruments)
_VENDOR_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'vendor', 'opuscar')
DEST_ROOT = os.path.normpath(_VENDOR_ROOT)


def pack_of(lib):
    name = f'instruments-{lib}'
    p = MANIFEST['packs'][name]
    url = f"https://github.com/{MANIFEST['repo']}/releases/download/{MANIFEST['tag']}/{p['file']}"
    return name, url, p


def download(url, tmp, size):
    """Download to tmp with resume (Range) and a few retries, like the upstream script."""
    def have():
        return os.path.getsize(tmp) if os.path.exists(tmp) else 0
    if have() > size:  # not a partial copy of this pack
        os.remove(tmp)
    if have() == size:
        return
    resume = f', resuming at {have() / 1e6:.0f} MB' if have() else ''
    print(f'↓ {os.path.basename(url)} ({size / 1e6:.0f} MB){resume}', flush=True)
    fails = 0
    try:
        while have() < size:
            headers = {'Range': f'bytes={have()}-'} if have() else {}
            try:
                resp = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
            except urllib.error.HTTPError as e:  # GitHub answers 5xx now and then: try again, resuming
                fails += 1
                if e.code < 500 or fails > 3:
                    raise
                time.sleep(2 * fails)
                continue
            with resp:
                append = have() > 0 and resp.status == 206  # a server that ignores Range sends everything again
                with open(tmp, 'ab' if append else 'wb') as f:
                    done = have() if append else 0
                    while chunk := resp.read(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        print(f'\r  {done / size * 100:5.1f} %', end='', flush=True)
            print(flush=True)
    except OSError as e:
        sys.exit(f'✗ download failed ({e}); check the network and run the same command again to resume')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def extract(tmp, dest_root):
    """Unpack a release pack at dest_root (the library root), refusing unsafe entries."""
    with tarfile.open(tmp) as t:
        if hasattr(tarfile, 'data_filter'):  # Python 3.12+ and the patched 3.8–3.11
            t.extractall(dest_root, filter='data')
            return
        root = os.path.realpath(dest_root)  # older Pythons: refuse anything that could leave the folder
        for m in t.getmembers():
            dest = os.path.realpath(os.path.join(root, m.name))
            link = os.path.realpath(os.path.join(os.path.dirname(dest), m.linkname)) if m.issym() \
                else os.path.realpath(os.path.join(root, m.linkname)) if m.islnk() else dest
            if os.path.isabs(m.name) or m.isdev() \
                    or not all(x == root or x.startswith(root + os.sep) for x in (dest, link)):
                sys.exit(f'✗ the pack contains an unsafe entry ({m.name}); nothing was unpacked')
        t.extractall(dest_root)


def install(lib, dest_root=DEST_ROOT, tmp_dir=None):
    """Download + verify + unpack one library; returns its target directory."""
    name, url, p = pack_of(lib)
    tmp_dir = tmp_dir or os.path.join(dest_root, '.release')
    os.makedirs(tmp_dir, exist_ok=True)
    tmp = os.path.join(tmp_dir, p['file'] + '.download')
    need = int(p['size'] * 2.2) - (os.path.getsize(tmp) if os.path.exists(tmp) else 0)
    free = shutil.disk_usage(tmp_dir).free
    if free < need:
        sys.exit(f"✗ not enough disk space: {p['file']} is {p['size'] / 1e9:.2f} GB and needs about "
                 f'{need / 1e9:.1f} GB free here while it downloads and unpacks; {free / 1e9:.1f} GB is available')
    download(url, tmp, p['size'])
    if sha256(tmp) != p['sha256']:
        os.remove(tmp)
        sys.exit('✗ checksum mismatch. The pack on the release was probably updated after this copy of the '
                 'manifest was. Refresh the manifest (app/opuscar/fetch_instruments.py) and run this again. '
                 'If it still fails, just retry.')
    extract(tmp, dest_root)
    os.remove(tmp)
    print(f'✓ {lib} → {os.path.join(dest_root, "core", "audio", "instruments", lib)}')
    return tmp_dir


def listing(dest_root=DEST_ROOT):
    """--list: print sources + sizes only; nothing is downloaded."""
    total = 0
    print(f'thu mục đích (sampler.py đọc từ đây): {os.path.join(dest_root, "core", "audio", "instruments")}')
    print(f'nguồn: GitHub release {MANIFEST["repo"]} / tag {MANIFEST["tag"]} (manifest: {MANIFEST_URL})')
    for lib in LIBS:
        name, url, p = pack_of(lib)
        mb = p['size'] / 1e6
        total += p['size']
        print(f'  {lib:10s} {p["file"]:26s} {mb:7.0f} MB  sha256 {p["sha256"][:12]}…\n'
              f'             {url}')
    print(f'  {"all":10s} {len(LIBS)} gói, tổng ≈ {total / 1e9:.2f} GB (cần ~2.2× dung lượng còn đĩa khi tải + giải nén)')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    lib_arg, list_only = None, False
    for a in argv:
        if a == '--list':
            list_only = True
        elif a.startswith('-'):
            lib_arg = None
            break
        elif lib_arg is None:
            lib_arg = a
        else:
            lib_arg = None
            break
    if lib_arg is None and list_only:
        listing()  # --list works with or without a lib argument: sources + sizes only, nothing is downloaded
        return 0
    if lib_arg not in LIBS + ['all']:
        print(f'usage: python -m app.opuscar.fetch_instruments freepats|karoryfer|salamander|vcsl|vsco2ce|all [--list]\n'
              f'  libs: {", ".join(LIBS)} (all ≈ 1.4 GB)', file=sys.stderr)
        return 1
    if list_only:
        listing()
        return 0
    libs = LIBS if lib_arg == 'all' else [lib_arg]
    for lib in libs:
        install(lib)
    try:
        os.rmdir(os.path.join(DEST_ROOT, '.release'))  # ours only when nothing else is staged there
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
