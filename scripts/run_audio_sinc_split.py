#!/usr/bin/env python3
"""prepare_audio_sinc_split.py against the converter it transforms, clock for clock.

sim/pocket/tb_audio_sinc_mac.sv (random frames, phases 0..159 including 79
and 80, resets; every output compared on every clock), 3 seeds, with the
release's selected psx_audio_sinc.sv (the MAC form) as the reference and its
split form as the design. Two planted faults must be caught: port b taking
phase 80 for phase 80 instead of 79, and the phase-80 table read mirrored.
(A port-a-tap-only fault was behaviourally equivalent: for phases 79 and 80
both taps are issued[6:0], so the step reads the table there directly.) Usage: run_audio_sinc_split.py [PROJECT] (default: the
repository's rtl/audio/psx_audio_sinc_mac.sv as the release form)
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from generate_pocket_audio_coeffs import generate
from prepare_audio_sinc_split import phase80_file, sinc_split_text
from tool_paths import ROOT, find_tool

RESULT = re.compile(r'RESULT errors (\d+) clocks (\d+) results (\d+)')
FAULTS = {
    'port-b-select': ("use80_b <= phase_b == 8'd80;", "use80_b <= phase == 8'd80;"),
    'phase80-mirrored': ("phase80_q <= phase80[issued[6:0]];", "phase80_q <= phase80[~issued[6:0]];"),
}


def run(work, name, reference_text, design_text, seed, coefficients):
    ref = work/f'{name}-ref.sv'; ref.write_text(reference_text.replace('module psx_audio_sinc #(', 'module psx_audio_sinc_ref #('))
    dut = work/f'{name}-dut.sv'; dut.write_text(design_text)
    out = work/f'{name}.vvp'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_audio_sinc_mac', f'-Ptb_audio_sinc_mac.SEED={seed}', '-o', str(out),
                    f'-DCOEFFICIENT_FILE="{coefficients}"', str(ref), str(dut), str(ROOT/'sim/pocket/tb_audio_sinc_mac.sv')], check=True)
    r = subprocess.run([find_tool('vvp'), '-n', str(out)], capture_output=True, text=True, timeout=3600)
    m = RESULT.search(r.stdout)
    if not m: sys.exit(f'{name}: no result\n{r.stdout[-2000:]}{r.stderr[-2000:]}')
    return tuple(map(int, m.groups()))


def main():
    if len(sys.argv) > 1:
        from prepare_pocket_config_cdc import assignments
        project = Path(sys.argv[1]).resolve()
        rows = {p for _, p in assignments(project, (project/'apf_top.qsf').read_text()) if p.name == 'psx_audio_sinc.sv'}
        assert len(rows) == 1; reference = rows.pop().read_text()
    else:
        reference = (ROOT/'rtl/audio/psx_audio_sinc_mac.sv').read_text().replace('module psx_audio_sinc_mac', 'module psx_audio_sinc')
    parent = ROOT/'build/sim/audio-sinc-split'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    coefficients = generate(work/'coefficients')['compact_path']
    design = sinc_split_text(reference, phase80_file(coefficients, work/'phase80.hex18'))
    ok = True
    for seed in (1, 2, 3):
        errors, clocks, results = run(work, f'seed{seed}', reference, design, seed, coefficients)
        good = errors == 0 and results > 5000; ok &= good
        print(f'seed {seed}: {clocks} clocks, {results} results: {errors} differences: {"pass" if good else "FAIL"}', flush=True)
    for name, (old, new) in FAULTS.items():
        if design.count(old) != 1: sys.exit('fault anchor not found: ' + name)
        errors, *_ = run(work, name, reference, design.replace(old, new), 1, coefficients)
        ok &= errors > 0
        print(f'fault {name}: {errors} differences: {"caught" if errors else "MISSED"}', flush=True)
    print('PASS audio-sinc-split' if ok else 'FAIL audio-sinc-split')
    if not ok: sys.exit(1)


if __name__ == '__main__':
    main()
