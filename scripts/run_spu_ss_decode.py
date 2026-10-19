#!/usr/bin/env python3
"""spu-ss-decode checks: the save tap bench (run_spu_save_tap.py) on the
decoded readback, after checking that the bench saves exactly the words the
save routine lists (save_handler.S SPU_SS); a swapped decode must fail.
"""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_spu_save_tap
from prepare_save_states import spu_tap_text
from prepare_spu_ss_decode import ss_decode_text
from tool_paths import ROOT


def routine_words():
    text = (ROOT / 'rtl/pocket/save_fw/save_handler.S').read_text()
    pairs = [int(v) for v in re.search(r'SPU_SS:\n\s*\.hword\s+([^\n]*)', text).group(1).split(',')]
    words = set()
    for a, b in zip(pairs[::2], pairs[1::2]):
        if b == 0: break
        words |= set(range(a, b))
    return words


def bench_words():
    tb = (ROOT / 'sim/reference/tb_spu_save_tap.vhd').read_text()
    if 'or address = 37 or address = 40 or address = 43 or' not in tb: raise SystemExit('tb_spu_save_tap ss_saved changed')
    return set(range(2, 7)) | {37, 40, 43} | set(range(256, 304)) | set(range(320, 370)) | set(range(384, 480))


def main():
    if routine_words() != bench_words(): raise SystemExit('save routine SPU_SS and the bench disagree')
    directory = ROOT / 'build/sim/spu-ss-decode'
    run_spu_save_tap.spu_tap_text = lambda t: ss_decode_text(spu_tap_text(t))
    results = run_spu_save_tap.save_tap(directory, (1,), (0,)) if (directory / 'lockstep/spu_guest.vhd').exists() else None
    if results is None:
        for passed, tail in run_spu_save_tap.run_spu_static_readback.run(directory / 'lockstep', lambda t: spu_tap_text(t), (1,), 3000):
            if not passed: raise SystemExit('lockstep base failed')
        results = run_spu_save_tap.save_tap(directory, (1, 2), (0,))
    if not all(ok for ok, _ in results.values()): raise SystemExit('spu-ss-decode save tap failed')
    swap = lambda t: ss_decode_text(spu_tap_text(t)).replace('SS_DataRead <= ss_out(37);', 'SS_DataRead <= not ss_out(37);')
    run_spu_save_tap.spu_tap_text = lambda t: swap(t)
    bad = run_spu_save_tap.save_tap(directory, (1,), (0,))
    if all(ok for ok, _ in bad.values()): raise SystemExit('planted ENDX readback fault not caught')
    print('PASS spu-ss-decode: routine words match the bench, save tap passes, planted fault caught')


if __name__ == '__main__':
    main()
