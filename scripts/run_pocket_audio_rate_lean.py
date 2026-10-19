#!/usr/bin/env python3
"""psx_audio_rate_lean.sv against the original psx_audio_rate.sv, clock for clock.

sim/pocket/tb_audio_rate_lean.sv runs both on random FIFO levels, advance
pulses and resets (3 seeds) and compares every output on every clock. A
planted fault (the wrap step off by one) must be caught.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from tool_paths import ROOT, find_tool

RESULT = re.compile(r'RESULT errors (\d+) clocks (\d+) wraps (\d+) clamped (\d+) between (\d+)')


def run(work, name, lean, seed):
    ref = work/'ref.sv'
    ref.write_text((ROOT/'rtl/audio/psx_audio_rate.sv').read_text().replace('module psx_audio_rate (', 'module psx_audio_rate_ref ('))
    out = work/f'{name}.vvp'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_audio_rate_lean', f'-Ptb_audio_rate_lean.SEED={seed}', '-o', str(out),
                    str(ref), str(lean), str(ROOT/'sim/pocket/tb_audio_rate_lean.sv')], check=True)
    r = subprocess.run([find_tool('vvp'), '-n', str(out)], capture_output=True, text=True, timeout=1800)
    m = RESULT.search(r.stdout)
    if not m: sys.exit(f'{name}: no result\n{r.stdout[-2000:]}{r.stderr[-2000:]}')
    return tuple(map(int, m.groups()))


def main():
    parent = ROOT/'build/sim/audio-rate-lean'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    lean = ROOT/'rtl/audio/psx_audio_rate_lean.sv'
    ok = True
    for seed in (1, 2, 3):
        errors, clocks, wraps, clamps, mid = run(work, f'seed{seed}', lean, seed)
        good = errors == 0 and wraps > 100000 and clamps > 1000 and mid > 100000
        ok &= good
        print(f'seed {seed}: {clocks} clocks, {wraps} wraps, {clamps} clamped, {mid} other nonzero corrections: {errors} differences: {"pass" if good else "FAIL"}')
    text = lean.read_text(); old = "-10'sd13"
    if text.count(old) != 1: sys.exit('fault anchor not found')
    fault = work/'fault.sv'; fault.write_text(text.replace(old, "-10'sd12"))
    errors, *_ = run(work, 'fault', fault, 1)
    ok &= errors > 0
    print(f'fault wrap-step: {errors} differences: {"caught" if errors else "MISSED"}')
    print('PASS' if ok else 'FAIL')
    if not ok: sys.exit(1)


if __name__ == '__main__':
    main()
