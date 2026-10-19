#!/usr/bin/env python3
"""Pixel-compare gpu-recip (prepare_gpu_recip.py) on the release GPU.

Same harness as run_gpu_sort_reread.py: both sides are the release GPU
sources of the F-nopool base with gpu_poly's area-reg, dy-ram and
sort-reread transforms (the release build); the candidate also gets the
gpu-recip transforms of gpu.vhd, gpu_poly.vhd and gpu_line.vhd and
rtl/pocket/psx_gpu_recip_unit.vhd. Gradients finish earlier, so outputs are
not cycle-aligned: the bench compares both VRAMs after every program (the
oracle programs, with Gouraud lines, plus the dy-ram polygon traffic) and
every GPUREAD word. A unit without its correction step, a line that stores
the red step as blue, and a polygon that writes dyB to the dyR entry must
fail. The 20-bit colour and texture arithmetic in gpu_poly must reject an
unsigned gradient product and a dxU = 4096 filter flag that is never set.
"""
import argparse
import concurrent.futures
import json
import re
import time

import run_gpu_dy_ram as dy
import run_gpu_sort_reread as sr
from prepare_gpu_recip import UNIT, recip_gpu_text, recip_line_text, recip_poly_text
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool

UNIT_NAME = 'psx_gpu_recip_unit.vhd'


def _swap(text, old, new):
    if text.count(old) != 1: raise ValueError('fault marker missing: ' + old)
    return text.replace(old, new)


FAULTS = {
    'no_correction': ('unit', "if (s3R >= ('0' & dAbs)) then c := '1'; else c := '0'; end if;", "c := '0';"),
    'line_red_as_blue': ('gpu_line.vhd', 'when 0      => stepDr <= gradOutQ(19 downto 0);',
                         'when 0      => stepDb <= gradOutQ(19 downto 0);'),
    'poly_dy_entry': ('gpu_poly.vhd', 'dyWaddr <= to_integer(gradOutIdx) / 2;',
                      'dyWaddr <= (to_integer(gradOutIdx) / 2) mod 2;'),
    'narrow_unsigned_product': ('gpu_poly.vhd', 'mulProduct     <= signed(mulOp1) * signed(mulOp2);',
                                'mulProduct     <= signed(resize(mulOp1 * mulOp2, 33));'),
    'narrow_dxu_flag': ('gpu_poly.vhd', "if (unsigned(gradOutQ) = 4096) then dxU_4096 <= '1'; else dxU_4096 <= '0'; end if;",
                        "dxU_4096 <= '0';"),
}


def source_text(name, candidate, fault):
    if name == UNIT_NAME:
        text = UNIT.read_text()
        if fault and FAULTS[fault][0] == 'unit': text = _swap(text, *FAULTS[fault][1:])
        return text, str(UNIT)
    text, origin = sr._original_source_text(name, False, None)
    if name == 'gpu_poly.vhd':
        text = sr.dy_ram_text(text)
        declaration = '   attribute ramstyle : string;\n'
        if text.count(declaration) == 2:   # see run_gpu_dy_ram.py
            first = text.index(declaration) + len(declaration)
            text = text[:first] + text[first:].replace(declaration, '', 1)
        text = sr.sort_reread_text(text)
    if candidate:
        transform = {'gpu.vhd': recip_gpu_text, 'gpu_poly.vhd': recip_poly_text, 'gpu_line.vhd': recip_line_text}.get(name)
        if transform: text = transform(text)
        if fault and FAULTS[fault][0] == name: text = _swap(text, *FAULTS[fault][1:])
    return text, origin


def make_bench(directory, oldname, newname):
    top = sr.make_bench(directory, oldname, newname)
    top.write_text(top.read_text().replace('PASS GPU sort reread', 'PASS GPU recip'))
    return top


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-faults', action='store_true')
    args = parser.parse_args()
    if ('work', UNIT_NAME) not in dy.SOURCES:
        dy.SOURCES.insert(dy.SOURCES.index(('work', 'gpu.vhd')), ('work', UNIT_NAME))
    dy.source_text = source_text; dy.make_bench = make_bench; dy.FAULTS = FAULTS
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    if not ghdl or not verilator: raise SystemExit('GHDL and Verilator required')
    started = time.time()
    directory = ROOT / 'build/sim/gpu-recip-gpu'; directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'baseline.log').open('w') as log:
        baseline = dy.transcode(directory / 'original', False, None, ghdl, log)
    runs = [(directory, None)] + ([] if args.no_faults else [(directory / ('fault-' + f), f) for f in FAULTS])
    with concurrent.futures.ThreadPoolExecutor(len(runs)) as pool:
        results = list(pool.map(lambda r: dy.run(r[0], baseline, r[1], ghdl, verilator, max(2, 12 // len(runs))), runs))
    for result in results:
        text = ((directory if not result['fault'] else directory / ('fault-' + result['fault'])) / 'run.log').read_text()
        final = re.search(r'PASS GPU recip oracle=\s*(\d+) programs=\s*(\d+) memory_checks=\s*(\d+)', text)
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
    print(f"{'PASS' if passed else 'FAIL'} GPU recip ({report['seconds']} s)")
    if not passed: raise SystemExit(1)


if __name__ == '__main__':
    main()
