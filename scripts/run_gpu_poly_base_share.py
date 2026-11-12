#!/usr/bin/env python3
"""Pixel/VRAM check of gpu-poly-base-share (prepare_gpu_poly_base_share.py) on the release GPU.

Both sides are the gpu-recip release GPU of run_gpu_recip.py (the F-nopool
base with area-reg, dy-ram, sort-reread and the gpu-recip transforms and
unit) plus gpu-poly-diffmux, i.e. the shipped rc1i gpu_poly; the candidate's
gpu_poly also gets the base-share transform. The bench
is not cycle-lockstep (an area candidate may change timing): the run_gpu_line_share bench (timing_prune off)
drives both GPUs with the same commands, waits until both are idle, and
compares every GPUREAD word and both whole VRAMs after every program (the
oracle programs, the bounded random Gouraud/textured triangles and quads, and
the dy-ram Gouraud and textured polygon traffic). Planted faults (G base
copied from R; V base never copied) must fail.
"""
import argparse
import concurrent.futures
import json
import re
import subprocess
import time
from pathlib import Path

import run_gpu_dy_ram as dy
import run_gpu_line_share as share
import run_gpu_recip as recip
import prepare_gpu_poly_base_share as base_share
VARIANT = 1
from prepare_gpu_poly_diffmux import diffmux_poly_text
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool

FAULTS = {'g_from_r': lambda t: t.replace('base_G <= work_G;', 'base_G <= work_R;', 1),
          'v_not_copied': lambda t: re.sub(r'\n +base_V <= work_V;', '', t, count=1)}


def source_text(name, candidate, fault):
    text, origin = recip.source_text(name, True, None)
    if name == 'gpu_poly.vhd':
        text = diffmux_poly_text(text)  # both sides: the shipped rc1i gpu_poly (byte-identical to gpu-recip/gpu_poly.vhd)
    if candidate and name == 'gpu_poly.vhd':
        text = (base_share.transform2 if VARIANT == 2 else base_share.transform)(text)
        if fault: text = FAULTS[fault](text)
    return text, origin


def make_bench(directory, oldname, newname):
    top = share.bench(directory, oldname, newname, False, False, True)
    text = top.read_text()
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
    # Both sides carry dy-ram and gpu-recip; keep the CALCCOLOR4/CALCTEXTURE4 counts (as run_gpu_poly_stepadd).
    coverage = dy.COVERAGE.replace('old_gpu.igpu_poly.dyu==0', "1'b0").replace('old_gpu.igpu_poly.dxv==0', "1'b0") \
        .replace('old_gpu.igpu_poly.dxu==4096', "1'b0")
    if 'old_gpu.' in coverage: raise ValueError('coverage still reads the baseline')
    text = text.replace(marker, coverage + marker)
    marker = ' $display("PASS GPU combined adaptations'
    if text.count(marker) != 1: raise ValueError('Bench final marker changed')
    top.write_text(text.replace(marker, dy.TRAFFIC + marker))
    return top


def run(directory, baseline, fault, ghdl, verilator, jobs):
    started = time.time()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'checks.log').open('w') as log:
        sources, newverilog, newname = dy.transcode(directory / 'adapted', True, fault, ghdl, log)
        oldsources, oldverilog, oldname = baseline
        original = directory / 'original'
        if original.resolve() != Path(oldverilog).parent.resolve():
            if original.is_symlink() or original.exists(): original.unlink()
            original.symlink_to(Path(oldverilog).parent.resolve())
        top = make_bench(directory, oldname, newname)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', str(jobs), '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_share', '--Mdir', str(directory / 'obj'), str(oldverilog), str(newverilog),
                        str(ROOT / 'sim/reference/mister_memory_models.sv'), str(top)],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=1800, check=True)
    result = subprocess.run([str(directory / 'obj/Vtb_gpu_share')], cwd=directory, capture_output=True, text=True, timeout=7200)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    final = re.search(r'PASS GPU combined adaptations oracle=\s*(\d+) programs=\s*(\d+) memory_checks=\s*(\d+)', output)
    cov = re.search(r'^COVERAGE (.*)$', output, re.M)
    mismatch = [l for l in output.splitlines() if 'mismatch' in l.lower() or 'fatal' in l.lower() or 'timeout' in l.lower()
                or 'differs' in l.lower()]
    return {'fault': fault, 'passed': bool(result.returncode == 0 and final), 'exit_code': result.returncode,
            'seconds': round(time.time() - started, 1), 'oracle_checks': int(final[1]) if final else None,
            'programs': int(final[2]) if final else None, 'memory_checks': int(final[3]) if final else None,
            'coverage': dict(kv.split('=') for kv in cov[1].split()) if cov else None, 'first_failure': mismatch[:3], 'candidate_verilog_sha256': digest(newverilog), 'bench_sha256': digest(top)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-faults', action='store_true')
    parser.add_argument('--variant', type=int, choices=(1, 2), default=1)
    parser.add_argument('--lockstep', action='store_true', help='cycle-lockstep bench (every public GPU output on every clk2x edge) instead of pixel/VRAM')
    args = parser.parse_args()
    if ('work', recip.UNIT_NAME) not in dy.SOURCES:
        dy.SOURCES.insert(dy.SOURCES.index(('work', 'gpu.vhd')), ('work', recip.UNIT_NAME))
    global VARIANT; VARIANT = args.variant
    dy.source_text = source_text; dy.FAULTS = FAULTS
    if args.lockstep:
        from run_gpu_poly_stepadd import make_bench as lockstep_bench
        dy.make_bench = lockstep_bench
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    if not ghdl or not verilator: raise SystemExit('GHDL and Verilator required')
    started = time.time()
    directory = ROOT / ('build/sim/gpu-poly-base-share' + ('-v2' if args.variant == 2 else '') + ('-lockstep' if args.lockstep else '')); directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'baseline.log').open('w') as log:
        baseline = dy.transcode(directory / 'original', False, None, ghdl, log)
    runs = [(directory, None)] + ([] if args.no_faults else [(directory / ('fault-' + f), f) for f in FAULTS])
    with concurrent.futures.ThreadPoolExecutor(len(runs)) as pool:
        results = list(pool.map(lambda r: (dy.run if args.lockstep else run)(r[0], baseline, r[1], ghdl, verilator, max(2, 12 // len(runs))), runs))
    for result in results:
        print(f"{result['fault'] or 'candidate'}: {'PASS' if result['passed'] else 'FAIL'} exit={result['exit_code']} "
              f"programs={result['programs']} memory_checks={result['memory_checks']} seconds={result['seconds']}")
        if result['first_failure']: print('  ' + '\n  '.join(result['first_failure']))
    main_result, faults = results[0], results[1:]
    rejected = {r['fault']: not r['passed'] for r in faults}
    passed = main_result['passed'] and all(rejected.values())
    report = {'passed': passed, 'seconds': round(time.time() - started, 1), 'candidate': main_result,
              'planted_faults_rejected': rejected, 'baseline_verilog_sha256': digest(baseline[1]),
              'compared': ('every public GPU output on every clk2x edge, every GPUREAD word, both VRAMs after every program'
                           if args.lockstep else 'every GPUREAD word and both whole VRAMs after every program (not cycle-lockstep)'),
              'hardware_verified': False}
    (directory / 'report.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    for fault, ok in rejected.items(): print(f'Planted fault {"rejected" if ok else "NOT DETECTED"}: {fault}')
    print(f"{'PASS' if passed else 'FAIL'} GPU poly base share {'cycle lockstep' if args.lockstep else 'pixel/VRAM'} ({report['seconds']} s)")
    if not passed: raise SystemExit(1)


if __name__ == '__main__':
    main()
