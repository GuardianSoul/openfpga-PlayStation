#!/usr/bin/env python3
"""Cycle-lockstep check of gpu-poly-diffmux (prepare_gpu_poly_diffmux.py).

Both sides are the gpu-recip release GPU of run_gpu_recip.py (the F-nopool
base with area-reg, dy-ram, sort-reread and the gpu-recip transforms and
unit); the candidate's gpu_poly also gets diffmux_poly_text. The transform
gives the multipliers the same operands in every clock their products are
latched, so the bench of run_gpu_dy_ram.py
compares every public GPU output on every clk2x edge (pixel throughput
included), every GPUREAD word and both VRAMs after all programs (the oracle
programs plus the dy-ram Gouraud and textured polygon traffic). Unswapped colour
or position differences on the second multiplier and a V gradient built from
U must fail.
"""
import argparse
import concurrent.futures
import json
import time

import run_gpu_dy_ram as dy
import run_gpu_recip as recip
from prepare_gpu_poly_diffmux import diffmux_poly_text
from run_gpu_poly_stepadd import make_bench
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool

FAULTS = {
    'second_unswapped': ("muldiv2In1 <= dmD21 when dmEven = '1' else dmD10;",
                         "muldiv2In1 <= dmD10 when dmEven = '1' else dmD21;"),
    'xy_unswapped': ("muldiv2In2 <= dmE10 when dmEven = '1' else dmE21;",
                     "muldiv2In2 <= dmE21 when dmEven = '1' else dmE10;"),
    'v_from_u': ("vt(1).u when (dmTex = '1' and mulStep < 2) else", "vt(1).u when dmTex = '1' else"),
}


def source_text(name, candidate, fault):
    text, origin = recip.source_text(name, True, None)
    if candidate and name == 'gpu_poly.vhd':
        text = diffmux_poly_text(text)
        if fault: text = recip._swap(text, *FAULTS[fault])
    return text, origin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-faults', action='store_true')
    args = parser.parse_args()
    if ('work', recip.UNIT_NAME) not in dy.SOURCES:
        dy.SOURCES.insert(dy.SOURCES.index(('work', 'gpu.vhd')), ('work', recip.UNIT_NAME))
    dy.source_text = source_text; dy.make_bench = make_bench; dy.FAULTS = FAULTS
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    if not ghdl or not verilator: raise SystemExit('GHDL and Verilator required')
    started = time.time()
    directory = ROOT / 'build/sim/gpu-poly-diffmux'; directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'baseline.log').open('w') as log:
        baseline = dy.transcode(directory / 'original', False, None, ghdl, log)
    runs = [(directory, None)] + ([] if args.no_faults else [(directory / ('fault-' + f), f) for f in FAULTS])
    with concurrent.futures.ThreadPoolExecutor(len(runs)) as pool:
        results = list(pool.map(lambda r: dy.run(r[0], baseline, r[1], ghdl, verilator, max(2, 12 // len(runs))), runs))
    for result in results:
        print(f"{result['fault'] or 'candidate'}: {'PASS' if result['passed'] else 'FAIL'} exit={result['exit_code']} "
              f"ticks={result['cycle_ticks']} programs={result['programs']} memory_checks={result['memory_checks']} "
              f"seconds={result['seconds']}")
        if result['coverage']: print('  coverage', ' '.join(f'{k}={v}' for k, v in result['coverage'].items()))
        if result['first_failure']: print('  ' + '\n  '.join(result['first_failure']))
    main_result, faults = results[0], results[1:]
    rejected = {r['fault']: not r['passed'] for r in faults}
    passed = main_result['passed'] and all(rejected.values())
    report = {'passed': passed, 'seconds': round(time.time() - started, 1), 'candidate': main_result,
              'planted_faults_rejected': rejected, 'baseline_verilog_sha256': digest(baseline[1]),
              'compared': 'every public GPU output on every clk2x edge, every GPUREAD word, both VRAMs after every program',
              'hardware_verified': False}
    (directory / 'report.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    for fault, ok in rejected.items(): print(f'Planted fault {"rejected" if ok else "NOT DETECTED"}: {fault}')
    print(f"{'PASS' if passed else 'FAIL'} GPU poly diffmux lockstep ({report['seconds']} s)")
    if not passed: raise SystemExit(1)


if __name__ == '__main__':
    main()
