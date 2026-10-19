#!/usr/bin/env python3
"""psx_audio_sinc_mac.sv against the original psx_audio_sinc.sv, clock for clock.

sim/pocket/tb_audio_sinc_mac.sv runs both with the generated coefficient
bank on random frames, phases and resets (3 seeds) and compares every output
on every clock; the MAC copy also runs run_pocket_audio_sinc.py's integer
vectors. A planted fault (accumulator not cleared on a result's first tap)
must be caught.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from generate_pocket_audio_coeffs import generate
from tool_paths import ROOT, find_tool

RESULT = re.compile(r'RESULT errors (\d+) clocks (\d+) results (\d+)')


def run(work, name, mac, seed, coefficients):
    ref = work/'ref.sv'
    ref.write_text((ROOT/'rtl/audio/psx_audio_sinc.sv').read_text().replace('module psx_audio_sinc #(', 'module psx_audio_sinc_ref #('))
    out = work/f'{name}.vvp'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_audio_sinc_mac', f'-Ptb_audio_sinc_mac.SEED={seed}', '-o', str(out),
                    f'-DCOEFFICIENT_FILE="{coefficients}"', str(ref), str(mac), str(ROOT/'sim/pocket/tb_audio_sinc_mac.sv')], check=True)
    r = subprocess.run([find_tool('vvp'), '-n', str(out)], capture_output=True, text=True, timeout=3600)
    m = RESULT.search(r.stdout)
    if not m: sys.exit(f'{name}: no result\n{r.stdout[-2000:]}{r.stderr[-2000:]}')
    return tuple(map(int, m.groups()))


def main():
    parent = ROOT/'build/sim/audio-sinc-mac'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    coefficients = generate(work/'coefficients')['compact_path']
    mac = ROOT/'rtl/audio/psx_audio_sinc_mac.sv'
    ok = True
    for seed in (1, 2, 3):
        errors, clocks, results = run(work, f'seed{seed}', mac, seed, coefficients)
        good = errors == 0 and results > 5000
        ok &= good
        print(f'seed {seed}: {clocks} clocks, {results} results: {errors} differences: {"pass" if good else "FAIL"}')
    text = mac.read_text(); old = '(first_3 ? 41\'sd0 : acc_left)'
    if text.count(old) != 1: sys.exit('fault anchor not found')
    fault = work/'fault.sv'; fault.write_text(text.replace(old, 'acc_left'))
    errors, *_ = run(work, 'fault', fault, 1, coefficients)
    ok &= errors > 0
    print(f'fault no-clear: {errors} differences: {"caught" if errors else "MISSED"}')
    print('PASS' if ok else 'FAIL')
    if not ok: sys.exit(1)


if __name__ == '__main__':
    main()
