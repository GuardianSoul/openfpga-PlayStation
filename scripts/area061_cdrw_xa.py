"""cd-rewrite-061 step: the XA resampler ring buffers (2 x 32 x 16, one M10K each) in MLAB, freeing two M10K
for the CD sub-CPU program. Safe because the ring is never read and written on the same clock: writes only in
RESAMPLE_CLEAR / RESAMPLE_WRITERING, the registered read only in RESAMPLE_READRING, branches of one case on
ResampleState (scripts/cd_rewrite_061_xaring_check.py checks this on the source text, with mutants)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cd_rewrite_061_xaring_check as chk
TARGET = 'cd_xa.vhd'


def transform(text):
    chk.check(text)
    out = text
    for side in 'LR':
        old = f'attribute ramstyle of XaRingBuffer{side} : signal is "no_rw_check";'
        assert out.count(old) == 1, side
        out = out.replace(old, f'attribute ramstyle of XaRingBuffer{side} : signal is "MLAB, no_rw_check";   -- cd-rewrite-061')
    return out
