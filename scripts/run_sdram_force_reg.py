#!/usr/bin/env python3
"""Lockstep prepare_sdram_force_reg.py against the original controller fed
refreshForce through one clk3x register (scripts/run_sdram_ref_owed.py's
benches: pins and every channel output compared on each clk3x edge), alone
and stacked on sdram-ref-owed + sdram-idle-paths. Without that register on
the original's input the lockstep must fail (the bench does see the delay).
Usage: run_sdram_force_reg.py [--ops N]
"""
import argparse

from prepare_sdram_force_reg import force_reg_text
from prepare_sdram_idle_paths import idle_paths_text
from prepare_sdram_ref_owed import ref_owed_text
import run_sdram_ref_owed as r
from tool_paths import ROOT

NZ_CHECK = r.TOP[r.TOP.index('        if (b.new_dut.dut.ref_owed_nz'):r.TOP.index('%CHECKS%')]
PLAIN_BENCH, PLAIN_TOP = r.bench_text, r.TOP
PORT = '.refreshForce(refresh_force)'


def delayed_bench():
    text = PLAIN_BENCH()
    cut = text.index('module sdram_bench_orig')
    orig = text[cut:]
    if orig.count(PORT) != 2: raise ValueError('sdram_bench refreshForce connection changed')
    old = '    reg init = 1, refresh_force = 0;\n'
    if orig.count(old) != 1: raise ValueError('sdram_bench refresh_force declaration changed')
    orig = orig.replace(old, old + '    reg refresh_force_q = 0;   // sdram-force-reg reference: one clk3x register\n'
                                   '    always @(posedge clk3) refresh_force_q <= refresh_force;\n')
    return text[:cut] + orig.replace(PORT, '.refreshForce(refresh_force_q)')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ops', type=int, default=4000)
    args = ap.parse_args()
    root = ROOT/'build/sim/sdram-force-reg'
    base = r.CTRL.read_text()
    failures = []

    def run(name, text, seed, rc, window=0, delayed=True):
        r.TOP = PLAIN_TOP if 'ref_owed_nz' in text else PLAIN_TOP.replace(NZ_CHECK, '')
        r.bench_text = delayed_bench if delayed else PLAIN_BENCH
        return r.run(root/name, text, args.ops, seed, rc, window)

    ok, _ = run('undelayed-reference', force_reg_text(base), 1, 780, delayed=False)
    print('original without the input register:', 'NOT DETECTED' if ok else 'differs (as it must)', flush=True)
    if ok: failures.append('delay not visible to the bench')
    stacked = force_reg_text(idle_paths_text(ref_owed_text(base)))
    for label, text in (('alone', force_reg_text(base)), ('stacked', stacked)):
        for seed, rc, window in ((1, 780, 0), (2, 780, 0), (3, 40, 0), (4, 40, 0), (5, 120, 0), (6, 12, r.WINDOW_US), (7, 12, r.WINDOW_US)):
            ok, tail = run(f'{label}-seed{seed}-rc{rc}', text, seed, rc, window)
            print(f'lockstep {label} seed {seed} REFRESH_CYCLES {rc}:', 'PASS' if ok else 'FAIL', '|', tail, flush=True)
            if not ok: failures.append(f'{label} seed {seed} rc {rc}')
    if failures: raise SystemExit('sdram-force-reg check failed: ' + ', '.join(failures))
    print('PASS sdram-force-reg: identical every clk3x edge to the original with refreshForce registered once on clk3x')


if __name__ == '__main__':
    main()
