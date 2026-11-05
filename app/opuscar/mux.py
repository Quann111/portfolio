"""Mux the finished film: video.mp4 + mix.wav -> out.mp4, Python port of vendor/opuscar/core/render/mux.sh.

Video is output at the target frame rate (held segments get duplicated frames automatically);
grain = grain strength (default 2, 0 = none; use 0 for pixel/vector styles).
Audio: two-pass loudnorm -> -14 LUFS / TP -1.2 (a single pass is off by about 0.5 LU); the measured
I / LRA / true peak are printed and a warning is issued if off target (loudness outside ±1 LU or peak
above -1 dB). Silent / extremely quiet audio (below -70 LUFS, which loudnorm can't measure): normalisation
is skipped with a warning; the film is still made. Not audio / no audio stream / damaged file: error,
no film. Audio shorter than the picture: padded with silence; longer: cut (-shortest).

CLI: python -m app.opuscar.mux video.mp4 mix.wav out.mp4 [fps=24] [grain=2]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

TARGET_I = -14.0
TARGET_TP = -1.2
TARGET_LRA = 11.0


class MuxError(RuntimeError):
    """A fatal mux problem (mirrors mux.sh's die)."""


def _run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', **kw)


def _die(msg):
    raise MuxError(f'mux.py: {msg}')


def _note(msg):
    print(f'mux.py: note: {msg}', file=sys.stderr, flush=True)


def _warn(msg):
    print(f'mux.py: warning: {msg}', file=sys.stderr, flush=True)


def _ffprobe(query, path):
    """One scalar from ffprobe (csv output); '' when the value is missing."""
    r = _run(['ffprobe', '-v', 'error', '-show_entries', query, '-of', 'csv=p=0', path])
    if r.returncode != 0:
        return ''
    return (r.stdout or '').strip().splitlines()[0].strip() if (r.stdout or '').strip() else ''


def _has_audio(path):
    r = _run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
              'stream=codec_type', '-of', 'csv=p=0', path])
    return bool((r.stdout or '').strip())


def _wav_truncated(path):
    """True when the WAV header declares more audio than the file holds (interrupted write);
    a placeholder length (0 / 0xFFFFFFFF) counts as 'length unknown', not truncated."""
    with open(path, 'rb') as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b'RIFF' or head[8:12] != b'WAVE':
            return False
        size = os.path.getsize(path)
        off = 12
        while off + 8 <= size:
            f.seek(off)
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, ln = hdr[:4], int.from_bytes(hdr[4:8], 'little')
            if cid == b'data':
                if ln in (0, 0xFFFFFFFF):
                    return False
                return off + 8 + ln > size
            off += 8 + ln + (ln & 1)
    return False


def _out_time(s):
    """'HH:MM:SS.micro' (or seconds) -> float seconds; 0 when unparseable."""
    m = re.fullmatch(r'(?:-)?(\d+):(\d+):([\d.]+)', s or '')
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


# decoder/demuxer log lines ([name @ 0x…]) with error wording, except the fixed
# streamed-WAV notices, mean the file is damaged (loudness would be -inf, but that isn't "silence")
_ERR_WORDS = re.compile(r'error|invalid|corrupt|header missing|overread|truncat|incomplete', re.I)
_BENIGN = re.compile(r'Ignoring maximum wav data size'
                     r'|Packet corrupt \(stream = \d+, dts = NOPTS\)'
                     r'|corrupt input packet in stream \d+')
_LOG_LINE = re.compile(r'^\[[^\]]*\]\s')


def _check_first_pass(m, path):
    """Validate the first loudnorm pass's log for a damaged audio file; returns (errs, dec_s)."""
    errs = [ln for ln in m.splitlines() if _LOG_LINE.match(ln) and _ERR_WORDS.search(ln)]
    dec = 0.0
    for ln in m.splitlines():
        if ln.startswith('out_time='):
            dec = _out_time(ln.split('=', 1)[1])
    cont_s = _ffprobe('format=duration', path)
    try:
        cont = float(cont_s)
    except ValueError:
        cont = 0.0
    if 'Estimating duration from bitrate' not in m and cont > 0 and cont - dec > max(0.05 * cont, 0.15):
        _die(f'only {dec:g} s of the {cont:g} s that {path!r} claims could be decoded: the file is damaged or cut off')
    return errs, dec


def _loudnorm_json(m):
    mm = re.search(r'\{[^{}]*\}', m, re.S)
    return json.loads(mm.group(0)) if mm else {}


def mux(video, audio, out, fps=24, grain=None):
    """Mux video + audio into out with two-pass loudnorm to -14 LUFS (TP -1.2) and optional grain.

    grain=None behaves like mux.sh's default (2); grain=0 disables grain.
    Returns a dict: measured input loudness (first pass), whether linear normalisation was applied,
    the post-mux ebur128 I / LRA / Peak, and the output path. Non-zero exit on failure (MuxError).
    """
    video, audio, out = str(video), str(audio), str(out)
    color = os.environ.get('LEMO_COLOR', '')
    if color not in ('', 'bt709'):
        _die(f'LEMO_COLOR must be bt709 or unset, got {color!r}')
    try:
        fps = 24.0 if fps is None else float(fps)
        grain = 2.0 if grain is None else float(grain)
    except (TypeError, ValueError):
        _die(f'fps and grain must be numbers, got {fps!r} / {grain!r}')
    if fps <= 0:
        _die(f'fps must be a number, got {fps!r}')
    if grain < 0:
        _die(f'grain must be a number (0 = none), got {grain!r}')
    if not os.path.isfile(video):
        _die(f'video not found: {video}')
    if not os.path.isfile(audio):
        _die(f'audio not found: {audio}')

    # first pass: measure loudness
    if not _has_audio(audio):
        _die(f'no audio stream in {audio!r} (is it really the mixed audio file?)')
    r = _run(['ffmpeg', '-hide_banner', '-nostats', '-progress', 'pipe:2', '-i', audio,
              '-af', f'loudnorm=I={TARGET_I:g}:TP={TARGET_TP:g}:LRA={TARGET_LRA:g}:print_format=json',
              '-f', 'null', '-'])
    if r.returncode != 0:
        tail = '\n'.join((r.stderr or '').splitlines()[-2:])
        _die(f'ffmpeg cannot read the audio {audio!r}: {tail}')
    m = r.stderr or ''
    errs, dec = _check_first_pass(m, audio)
    if _wav_truncated(audio):
        _die(f'the WAV file {audio!r} is cut off: its header promises more audio than the file contains '
             f'(was the write interrupted?)')
    j = _loudnorm_json(m)
    norm = 1
    for k in ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset'):
        v = str(j.get(k, ''))
        if not v or 'inf' in v or 'nan' in v:
            norm = 0
    if norm:
        ln = (f'loudnorm=I={TARGET_I:g}:TP={TARGET_TP:g}:LRA={TARGET_LRA:g}'
              f':measured_I={j["input_i"]}:measured_TP={j["input_tp"]}'
              f':measured_LRA={j["input_lra"]}:measured_thresh={j["input_thresh"]}'
              f':offset={j["target_offset"]}:linear=true')
    else:
        ln = 'anull'
        _warn(f'the audio is silent or quieter than -70 LUFS, so loudness normalisation was skipped')
    if errs:
        bad = [ln_ for ln_ in errs if not _BENIGN.search(ln_)][:2]
        if bad:
            _die(f'the audio {audio!r} is damaged; ffmpeg reported while decoding it:\n' + '\n'.join(bad))
        _note(f"ffmpeg complained about the header/ending of {audio!r} (typical of a WAV streamed with "
              f"'ffmpeg … -f wav -', whose length is a placeholder), but all {dec:g} s decoded; continuing")

    # pad with silence if the audio is shorter than the picture
    vd = _ffprobe('format=duration', video)
    ad = _ffprobe('format=duration', audio)
    try:
        a_dur, v_dur = float(ad), float(vd)
    except ValueError:
        a_dur, v_dur = 0.0, 0.0
    pad = ''
    if 0 < a_dur and 0 < v_dur and a_dur < v_dur - 0.02:
        pad = f',apad=whole_dur={v_dur:g}'
        _note(f'the audio ({ad} s) is shorter than the video ({vd} s); padding it with silence')

    vf = f'fps={fps:g}' if grain == 0 else f'fps={fps:g},noise=c0s={grain:g}:allf=t'
    if os.environ.get('LEMO_COLOR') == 'bt709':
        vf += ',scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p,' \
              'setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709'
        cargs = ['-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709']
    else:
        vf += ',format=yuv420p'
        cargs = []
    r = _run(['ffmpeg', '-y', '-loglevel', 'error', '-i', video, '-i', audio,
              '-filter_complex',
              f'[0:v]{vf}[v];[1:a]{ln},aresample=48000{pad}[a]',
              '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-preset', 'slow', '-crf', '19',
              '-r', f'{fps:g}', *cargs, '-c:a', 'aac', '-b:a', '256k',
              '-movflags', '+faststart', '-shortest', out])
    if r.returncode != 0:
        if os.path.exists(out):
            os.remove(out)
        _die(f'ffmpeg failed while writing {out!r} (its message is below)\n{(r.stderr or "").strip()}')
    if not (os.path.exists(out) and os.path.getsize(out) > 0):
        if os.path.exists(out):
            os.remove(out)
        _die(f'no output was written to {out!r}')
    print(out)

    # second pass on the muxed file: report the measured loudness
    r = _run(['ffmpeg', '-hide_banner', '-nostats', '-i', out, '-af', 'ebur128=peak=true', '-f', 'null', '-'])
    meas = dict(i=None, lra=None, peak=None)
    for ln_ in (r.stderr or '').splitlines():
        mo = re.match(r'\s+(I|LRA|Peak):\s+(-?[\d.]+|-inf)', ln_)
        if mo:
            meas[{'I': 'i', 'LRA': 'lra', 'Peak': 'peak'}[mo.group(1)]] = mo.group(2)
            if all(v is not None for v in meas.values()):
                break
    print(f"  I:         {meas['i']} LUFS\n  LRA:       {meas['lra']} LU\n  Peak:      {meas['peak']} dBFS")
    if norm and meas['i'] and meas['i'] != '-inf' and meas['peak']:
        try:
            i, p = float(meas['i']), float(meas['peak'])
            if i < TARGET_I - 1 or i > TARGET_I + 1 or p > -1:
                _warn(f'the film missed the target ({TARGET_I:g} LUFS, true peak <= {TARGET_TP:g} dB): '
                      f'measured {i:g} LUFS, peak {p:g} dB. The mix is probably clipping or has very hot peaks: '
                      f'lower it and tame the peaks, then mux again')
        except ValueError:
            pass
    return dict(out=out, fps=fps, grain=grain, applied_linear_norm=bool(norm), input=j,
                measured=meas)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) not in (3, 4, 5):
        print('usage: python -m app.opuscar.mux video.mp4 mix.wav out.mp4 [fps=24] [grain=2]',
              file=sys.stderr)
        return 1
    try:
        mux(argv[0], argv[1], argv[2], *(argv[3:]))
    except MuxError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
