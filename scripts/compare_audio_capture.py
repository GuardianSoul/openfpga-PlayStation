#!/usr/bin/env python3
"""Compare line-in captures of the same Pocket scene on two cores.

Each capture is a WAV recorded from the Pocket's headphone output (for example
arecord -D hw:<UR22C> -f S32_LE -r 48000 -c 4). Per capture it reports
dropouts (near-silent runs inside loud audio), held samples (the output stops
changing) and clicks (second-difference outliers). With two captures it aligns
them in 1 s windows: a jump in the window lag means one core paused or skipped,
and the residual after alignment (dB below the signal) measures how different
the two outputs sound.

  python3 scripts/compare_audio_capture.py a.wav b.wav
"""
import argparse
import subprocess

import numpy as np

RATE = 48000


def load(path, channels):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-af', f'pan=stereo|c0=c{channels[0]}|c1=c{channels[1]}',
                          '-ar', str(RATE), '-f', 'f32le', '-'], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def runs(mask, min_len):
    """(start, length) of True runs at least min_len long."""
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    s, e = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    keep = (e - s) >= min_len
    return list(zip(s[keep], (e - s)[keep]))


def db(x):
    return 20 * np.log10(max(x, 1e-12))


def analyse(name, x):
    mono = x.mean(axis=1)
    n = len(mono)
    win = RATE // 20
    env = np.sqrt(np.convolve(mono ** 2, np.ones(win) / win, mode='same'))
    peak = np.abs(x).max()
    print(f'{name}: {n / RATE:.1f} s, peak {db(peak):.1f} dBFS, RMS {db(np.sqrt((mono ** 2).mean())):.1f} dBFS'
          + ('  CLIPPING' if peak > 0.99 else ''))
    loud = env > 10 ** (-40 / 20)
    # Dropouts: >= 2 ms below -70 dBFS while the 50 ms envelope around it is loud.
    quiet = np.abs(mono) < 10 ** (-70 / 20)
    drops = [(s, l) for s, l in runs(quiet, RATE // 500)
             if loud[max(0, s - win)] and loud[min(n - 1, s + l + win)]]
    # Held output: >= 1 ms of identical samples (a stalled DAC stream).
    held = runs(np.abs(np.diff(mono)) < 1e-7, RATE // 1000)
    held = [(s, l) for s, l in held if loud[s]]
    # Clicks: second difference far above its local level.
    d2 = np.abs(np.diff(mono, 2))
    local = np.convolve(d2, np.ones(win) / win, mode='same') + 1e-9
    clicks = np.flatnonzero((d2 > 12 * local) & loud[1:-1])
    clicks = clicks[np.concatenate(([True], np.diff(clicks) > RATE // 100))] if len(clicks) else clicks
    print(f'  dropouts {len(drops)}, held runs {len(held)}, clicks {len(clicks)}')
    for label, items in (('dropout', drops), ('held', held)):
        for s, l in items[:8]:
            print(f'    {label} at {s / RATE:7.2f} s, {1000 * l / RATE:.1f} ms')
    for c in clicks[:8]:
        print(f'    click at {c / RATE:7.2f} s')
    return mono


def compare(a, b):
    # Coarse offset from the envelopes, then per-window lag and residual.
    def env(m):
        return np.sqrt(np.convolve(m ** 2, np.ones(480) / 480, mode='same'))[::480]
    ea, eb = env(a), env(b)
    k = min(len(ea), len(eb))
    corr = np.correlate(ea[:k] - ea[:k].mean(), eb[:k] - eb[:k].mean(), mode='full')
    offset = (corr.argmax() - (k - 1)) * 480   # b sample = a sample - offset
    print(f'coarse offset {offset / RATE:+.3f} s (B relative to A)')
    lags, res = [], []
    w = RATE
    for s in range(0, len(a) - 2 * w, w):
        sb = s - offset
        if sb < w // 2 or sb + w + w // 2 > len(b):
            continue
        sa = a[s:s + w]
        if np.sqrt((sa ** 2).mean()) < 10 ** (-40 / 20):
            continue
        seg = b[sb - w // 2: sb + w + w // 2]
        c = np.correlate(seg, sa, mode='valid')
        lag = c.argmax() - w // 2
        al = seg[w // 2 + lag: w // 2 + lag + w]
        g = (sa @ al) / max(al @ al, 1e-12)
        r = sa - g * al
        lags.append((s / RATE, lag))
        res.append(db(np.sqrt((r ** 2).mean())) - db(np.sqrt((sa ** 2).mean())))
    if not lags:
        print('no overlapping loud windows')
        return
    lag_v = np.array([l for _, l in lags])
    jumps = [(t, int(l - p)) for (t, l), p in zip(lags[1:], lag_v[:-1]) if abs(l - p) > 48]
    print(f'{len(lags)} windows: residual median {np.median(res):.1f} dB, worst {max(res):.1f} dB; '
          f'lag drift {lag_v.min()}..{lag_v.max()} samples, {len(jumps)} jumps > 1 ms')
    for t, j in jumps[:10]:
        print(f'    lag jump at {t:7.2f} s: {1000 * j / RATE:+.1f} ms')
    worst = np.argsort(res)[-5:][::-1]
    for i in worst:
        print(f'    window at {lags[i][0]:7.2f} s: residual {res[i]:.1f} dB')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('a')
    p.add_argument('b', nargs='?')
    p.add_argument('--channels', default='0,1', help='input channels holding left,right (default 0,1)')
    args = p.parse_args()
    ch = [int(c) for c in args.channels.split(',')]
    a = analyse('A', load(args.a, ch))
    if args.b:
        b = analyse('B', load(args.b, ch))
        compare(a, b)


if __name__ == '__main__':
    main()
