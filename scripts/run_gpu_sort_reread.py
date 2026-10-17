#!/usr/bin/env python3
"""Pixel-compare gpu-sort-reread (prepare_gpu_sort_reread.py) on the release GPU.

Same harness as run_gpu_dy_ram.py: both sides are the release GPU sources of
the F-nopool base, gpu_poly.vhd with area_reg_text and dy_ram_text (the
release build), and the candidate also gets sort_reread_text. Triangles start
four gpu clocks later, so outputs are not cycle-aligned: the bench compares
both VRAMs after every program (the oracle programs plus the dy-ram polygon
traffic) and every GPUREAD word. A re-read in load order instead of sorted
order, and a re-read that swaps the last two sorted vertices, must fail.
"""
import argparse
import json
import re
import time

import run_gpu_dy_ram as dy
import run_gpu_line_share as share
from prepare_gpu_dy_ram import dy_ram_text
from prepare_gpu_area_reg import area_reg_text
from prepare_gpu_sort_reread import sort_reread_text
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def unsorted(t):
    for k in range(3):
        old = f'ramRa <= sortIdx({k});'
        if t.count(old) != 1: raise ValueError('unsorted marker missing')
        t = t.replace(old, f'ramRa <= triIdx({k});')
    return t


def swapped(t):
    old, new = 'ramRa <= sortIdx(1);', 'ramRa <= sortIdx(2);'
    if t.count(old) != 1 or t.count(new) != 1: raise ValueError('swapped marker missing')
    return t.replace(old, '@@1').replace(new, old).replace('@@1', new)


FAULTS = {'unsorted': unsorted, 'swapped': swapped}
_original_source_text = dy.source_text


def source_text(name, candidate, fault):
    text, origin = _original_source_text(name, False, None)
    if name == 'gpu_poly.vhd':
        text = dy_ram_text(text)
        declaration = '   attribute ramstyle : string;\n'
        if text.count(declaration) == 2:   # see run_gpu_dy_ram.py
            first = text.index(declaration) + len(declaration)
            text = text[:first] + text[first:].replace(declaration, '', 1)
        if candidate:
            text = sort_reread_text(text)
            if fault: text = FAULTS[fault](text)
    return text, origin


def make_bench(directory, oldname, newname):
    top = share.bench(directory, oldname, newname, False, False, True)
    text = top.read_text().replace('PASS GPU combined adaptations', 'PASS GPU sort reread')
    old = 'wait(new_gpu.pool_starts!=0);'
    if text.count(old) == 1: text = text.replace(old, 'repeat(12) @(negedge clk2x);')
    if 'integer old_trace,new_trace;' in text:
        start, end = text.index('integer old_trace,new_trace;'), text.index('integer checks=0;')
        text = text[:start] + text[end:]
    text = re.sub(r'\$fatal\(1,"GPU timeout program=[^;]*\);', '$fatal(1,"GPU timeout program=%d",programs);', text)
    text = text.replace('repeat(10000000) @(negedge clk1x)', 'repeat(400000000) @(negedge clk1x)')
    marker = 'initial begin\n repeat(100)'
    if text.count(marker) != 1: raise ValueError('Bench initial marker changed')
    # Both sides carry dy-ram, so the baseline gradient registers the dy-ram
    # coverage reads are gone; keep only the CALCCOLOR4/CALCTEXTURE4 counts.
    coverage = dy.COVERAGE.replace('old_gpu.igpu_poly.dyu==0', "1'b0").replace('old_gpu.igpu_poly.dxv==0', "1'b0").replace('old_gpu.igpu_poly.dxu==4096', "1'b0")
    if 'old_gpu.' in coverage: raise ValueError('coverage still reads the baseline')
    text = text.replace(marker, coverage + marker)
    marker = ' $display("PASS GPU readback checks'
    if text.count(marker) != 1: raise ValueError('Bench readback marker changed')
    top.write_text(text.replace(marker, dy.TRAFFIC + marker))
    return top


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-faults', action='store_true')
    args = parser.parse_args()
    dy.source_text = source_text; dy.make_bench = make_bench; dy.FAULTS = FAULTS
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    started = time.time()
    directory = ROOT / 'build/sim/gpu-sort-reread'; directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'baseline.log').open('w') as log:
        baseline = dy.transcode(directory / 'original', False, None, ghdl, log)
    runs = [(directory, None)] + ([] if args.no_faults else [(directory / ('fault-' + f), f) for f in FAULTS])
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(len(runs)) as pool:
        results = list(pool.map(lambda r: dy.run(r[0], baseline, r[1], ghdl, verilator, max(2, 12 // len(runs))), runs))
    for result in results:
        output = (result_dir := (directory if not result['fault'] else directory / ('fault-' + result['fault']))) / 'run.log'
        text = output.read_text()
        final = re.search(r'PASS GPU sort reread oracle=\s*(\d+) programs=\s*(\d+) memory_checks=\s*(\d+)', text)
        readback = re.search(r'PASS GPU readback checks=\s*(\d+)', text)
        result['passed'] = bool(result['exit_code'] == 0 and final and readback)
        result['programs'] = int(final[2]) if final else None
        result['memory_checks'] = int(final[3]) if final else None
        print(f"{result['fault'] or 'candidate'}: {'PASS' if result['passed'] else 'FAIL'} exit={result['exit_code']} "
              f"programs={result['programs']} memory_checks={result['memory_checks']} seconds={result['seconds']}")
        if result['coverage']: print('  coverage', ' '.join(f'{k}={v}' for k, v in result['coverage'].items()))
        if result['first_failure']: print('  ' + '\n  '.join(result['first_failure']))
    main_result, faults = results[0], results[1:]
    rejected = {r['fault']: not r['passed'] for r in faults}
    passed = main_result['passed'] and all(rejected.values())
    report = {'passed': passed, 'seconds': round(time.time() - started, 1), 'candidate': main_result,
              'planted_faults_rejected': rejected, 'baseline_verilog_sha256': digest(baseline[1]),
              'compared': 'both VRAMs after every program and every GPUREAD word (not cycle-aligned)',
              'hardware_verified': False}
    (directory / 'report.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    for fault, ok in rejected.items(): print(f'Planted fault {"rejected" if ok else "NOT DETECTED"}: {fault}')
    print(f"{'PASS' if passed else 'FAIL'} GPU sort re-read ({report['seconds']} s)")
    if not passed: raise SystemExit(1)


if __name__ == '__main__':
    main()
