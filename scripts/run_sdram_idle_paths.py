#!/usr/bin/env python3
"""Lockstep prepare_sdram_idle_paths.py against the controller it transforms.

Same benches as scripts/run_sdram_ref_owed.py (two sdram_bench copies on the
same seeded traffic, pins and every channel output compared each clk3x
edge), with the transformed controller alone and stacked on
prepare_sdram_ref_owed (as release recipes use it). Planted faults in the
rewritten logic must be caught.
Usage: run_sdram_idle_paths.py [--ops N]
"""
import argparse

from prepare_sdram_idle_paths import idle_paths_text
from prepare_sdram_ref_owed import ref_owed_text
import run_sdram_ref_owed as r
from tool_paths import ROOT

NZ_CHECK = r.TOP[r.TOP.index('        if (b.new_dut.dut.ref_owed_nz'):r.TOP.index('%CHECKS%')]
FAULTS = {   # (old, new): must make the lockstep fail
    'st1-always-captured': ('st1 <= row_state(after_base && ch1_req ? ch1_addr : ch1_adr_q);',
                            'st1 <= row_state(ch1_adr_q);'),
    'hit1-ignores-refresh': ('wire hit1 = OPEN_ROW != 0 && !need_ref && st1 == ST_HIT',
                             'wire hit1 = OPEN_ROW != 0 && st1 == ST_HIT'),
    'ch1-row-not-column': ("(hit1 ? column(ch1_a, 9'd0, 1'b0) : row_of(ch1_a))", 'row_of(ch1_a)'),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ops', type=int, default=4000)
    args = ap.parse_args()
    root = ROOT/'build/sim/sdram-idle-paths'
    base = r.CTRL.read_text()
    alone, stacked = idle_paths_text(base), idle_paths_text(ref_owed_text(base))
    plain_top = r.TOP
    failures = []

    def run(name, text, seed, rc, window=0):
        r.TOP = plain_top if 'ref_owed_nz' in text else plain_top.replace(NZ_CHECK, '')
        return r.run(root/name, text, args.ops, seed, rc, window)

    for name, (old, bad) in FAULTS.items():
        if alone.count(old) != 1: raise SystemExit(f'fault anchor changed: {name}')
        caught = False
        for seed, rc, window in ((1, 780, 0), (3, 40, 0), (6, 12, r.WINDOW_US)):
            ok, tail = run(f'fault-{name}-{seed}', alone.replace(old, bad), seed, rc, window)
            if not ok: caught = True; break
        print(f'fault {name}:', 'caught' if caught else 'NOT DETECTED', flush=True)
        if not caught: failures.append(name)
    for label, text in (('alone', alone), ('stacked', stacked)):
        for seed, rc, window in ((1, 780, 0), (2, 780, 0), (3, 40, 0), (4, 40, 0), (5, 120, 0), (6, 12, r.WINDOW_US), (7, 12, r.WINDOW_US)):
            ok, tail = run(f'{label}-seed{seed}-rc{rc}', text, seed, rc, window)
            print(f'lockstep {label} seed {seed} REFRESH_CYCLES {rc}:', 'PASS' if ok else 'FAIL', '|', tail, flush=True)
            if not ok: failures.append(f'{label} seed {seed} rc {rc}')
    if failures: raise SystemExit('sdram-idle-paths check failed: ' + ', '.join(failures))
    print('PASS sdram-idle-paths: pins and channel outputs identical every clk3x edge, alone and on sdram-ref-owed; planted faults caught')


if __name__ == '__main__':
    main()
