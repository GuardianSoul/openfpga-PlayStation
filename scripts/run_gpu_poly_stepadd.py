#!/usr/bin/env python3
"""Cycle-lockstep check of gpu-poly-stepadd (prepare_gpu_poly_stepadd.py).

Both sides are the gpu-recip release GPU of run_gpu_recip.py (the F-nopool
base with area-reg, dy-ram, sort-reread and the gpu-recip transforms and
unit); the candidate's gpu_poly also gets stepadd_poly_text. The transform
keeps every register's value and clock, so the bench of run_gpu_dy_ram.py
compares every public GPU output on every clk2x edge (pixel throughput
included), every GPUREAD word and both VRAMs after all programs (the oracle
programs plus the dy-ram Gouraud and textured polygon traffic). A texture
base built from the work register and a line start that adds dx instead of
the product must fail.
"""
import argparse
import concurrent.futures
import json
import re
import time

import run_gpu_dy_ram as dy
import run_gpu_line_share as share
import run_gpu_recip as recip
from prepare_gpu_poly_stepadd import stepadd_poly_text
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool

FAULTS = {
    'texture_base_from_work': ("stepFromBase <= '1' when (state = CALCCOLOR4 or state = CALCTEXTURE4) else '0';",
                               "stepFromBase <= '1' when state = CALCCOLOR4 else '0';"),
    'line_add_dx': ("stepAddDx    <= '1' when state = PROCPIXELS else '0';",
                    "stepAddDx    <= '1' when (state = PROCPIXELS or state = CALCLINEADD) else '0';"),
}


def source_text(name, candidate, fault):
    text, origin = recip.source_text(name, True, None)
    if candidate and name == 'gpu_poly.vhd':
        text = stepadd_poly_text(text)
        if fault: text = recip._swap(text, *FAULTS[fault])
    return text, origin


def make_bench(directory, oldname, newname):
    top = share.bench(directory, oldname, newname, False, True, True)
    text = top.read_text().replace('PASS GPU combined adaptations', 'PASS GPU dy ram')
    old = 'wait(new_gpu.pool_starts!=0);'
    if text.count(old) != 1: raise ValueError('Bench pool marker changed')
    text = text.replace(old, 'repeat(12) @(negedge clk2x);')
    start, end = text.index('integer old_trace,new_trace;'), text.index('integer checks=0;')
    text = text[:start] + text[end:]
    text, count = re.subn(r'\$fatal\(1,"GPU timeout program=[^;]*\);', '$fatal(1,"GPU timeout program=%d",programs);', text)
    if count != 1: raise ValueError('Bench timeout marker changed')
    text = text.replace('repeat(10000000) @(negedge clk1x)', 'repeat(400000000) @(negedge clk1x)')
    marker = 'initial begin\n repeat(100)'
    if text.count(marker) != 1: raise ValueError('Bench initial marker changed')
    # Both sides carry dy-ram and gpu-recip; keep the CALCCOLOR4/CALCTEXTURE4 counts.
    coverage = dy.COVERAGE.replace('old_gpu.igpu_poly.dyu==0', "1'b0").replace('old_gpu.igpu_poly.dxv==0', "1'b0") \
        .replace('old_gpu.igpu_poly.dxu==4096', "1'b0")
    if 'old_gpu.' in coverage: raise ValueError('coverage still reads the baseline')
    text = text.replace(marker, coverage + marker)
    marker = ' $display("PASS GPU cycle outputs ticks='
    if text.count(marker) != 1: raise ValueError('Bench cycle marker changed')
    top.write_text(text.replace(marker, dy.TRAFFIC + marker))
    return top


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
    directory = ROOT / 'build/sim/gpu-poly-stepadd'; directory.mkdir(parents=True, exist_ok=True)
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
    print(f"{'PASS' if passed else 'FAIL'} GPU poly stepadd lockstep ({report['seconds']} s)")
    if not passed: raise SystemExit(1)


if __name__ == '__main__':
    main()
