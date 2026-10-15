#!/usr/bin/env python3
"""Latency of the CPU-synchronous main adapter (rtl/pocket/psx_mister_main_sync.sv).

Reuses run_main_latency.py's bench, MiSTer port oracle and stage accounting,
with the synthetic backend and the monitor's memory clock driven from clk3x
(the synchronous adapter has no memory-clock crossing). Prints the same
summary for the original adapter at 104.17 MHz for comparison. Transport cost
only: real PSRAM bursts add the backend cycles (EXTRA) on top.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_system_sim import prepare_memory_models
from run_main_latency import bench_text, measurements, summarize
from run_mister_main_adapter import oracle_text, vectors
from prepare_resource_targets import UPSTREAM
from tool_paths import ROOT, find_tool

CORE_HALF = 14.76255


def sync_bench():
    text = bench_text()
    for old, new in (('psx_mister_main_adapter adapter (', 'psx_mister_main_sync adapter ('),
                     ('        .clk(clk),.reset(rst),.initialized(initialized),.idle(fabric_idle),',
                      '        .clk(clk3x),.reset(rst),.initialized(initialized),.idle(fabric_idle),'),
                     ('.clk1x(clk1x),.clk3x(clk3x),.mem_clk(clk),.reset(rst),.measure(measure),', '.clk1x(clk1x),.clk3x(clk3x),.mem_clk(clk3x),.reset(rst),.measure(measure),')):
        if text.count(old) != 1: raise ValueError('Bench anchor changed: ' + old)
        text = text.replace(old, new)
    return text


def main():
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=ROOT/'build/sim/main-latency')); print(work, flush=True)
    rows, gold = vectors(work)
    model = prepare_memory_models(work/'upstream')[0]
    oracle = work/'oracle.vhd'; oracle.write_text(oracle_text()); ghdl = find_tool('ghdl')
    with (work/'oracle.log').open('w') as log:
        for command in ([ghdl, '-a', '--std=08', '--work=tb', str(UPSTREAM/'sim/system/src/tb/globals.vhd'), model['path']],
                        [ghdl, '-a', '--std=08', str(oracle)], [ghdl, '-e', '--std=08', 'tb_mister_oracle'],
                        [ghdl, '-r', '--std=08', 'tb_mister_oracle', '--max-stack-alloc=0', '--assert-level=error']):
            subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    actual = sorted(((int(f[0]), f[1], *[int(x, 16) for x in f[2:]]) for f in
                     (l.split() for l in (work/'oracle.txt').read_text().splitlines())), key=lambda e: (e[0], e[1]))
    if actual != gold: raise ValueError('Pinned MiSTer memory oracle differs')
    (work/'expected.txt').write_text(''.join(f'{i:x} {k} {m:x} {a:x} {d:x}\n' for i, k, m, a, d in actual))
    results = {}
    for name, sync, half, extra in [('original-104', False, 4.8, 0), ('original-104-burst12', False, 4.8, 12),
                                    ('sync', True, CORE_HALF/3, 0), ('sync-burst12', True, CORE_HALF/3, 12)]:
        d = work/name; d.mkdir()
        for f in ('vectors.txt', 'expected.txt'): shutil.copyfile(work/f, d/f)
        bench = d/'tb.sv'; bench.write_text(sync_bench() if sync else bench_text())
        adapter = ROOT/('rtl/pocket/psx_mister_main_sync.sv' if sync else 'rtl/pocket/psx_mister_main_adapter.sv')
        sources = [adapter, ROOT/'rtl/memory/psx_mem_cdc.sv', ROOT/'sim/pocket/tb_main_latency.sv', bench]
        binary = d/'sim.vvp'
        subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_psx_mister_main_adapter',
                        f'-Ptb_psx_mister_main_adapter.CORE_HALF={CORE_HALF}', f'-Ptb_psx_mister_main_adapter.MEM_HALF={half}',
                        f'-Ptb_psx_mister_main_adapter.EXTRA={extra}', '-o', str(binary), *map(str, sources)],
                       cwd=d, check=True, timeout=60)
        run = subprocess.run([find_tool('vvp'), str(binary)], cwd=d, capture_output=True, text=True, timeout=300)
        (d/'run.log').write_text(run.stdout + run.stderr)
        if run.returncode or 'PASS MISTER MAIN ADAPTER' not in run.stdout:
            raise SystemExit(f'{name}: functional oracle failed\n{run.stdout[-1500:]}{run.stderr[-500:]}')
        summary = summarize(measurements(d/'latency.csv', rows, half, extra))
        results[name] = summary
        print(name, ' '.join(f"{k}={v['median_ns']:.0f}ns" for k, v in summary.items()
                             if k in ('scalar-read', 'cache-critical-0', 'scalar-write', 'DMA-4')), flush=True)
    (work/'summary.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
