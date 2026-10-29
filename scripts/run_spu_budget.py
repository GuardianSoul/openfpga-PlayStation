#!/usr/bin/env python3
"""SPU per-sample time against the 768-clk1x budget (sim/reference/tb_spu_budget.vhd).

Builds a project's spu.vhd (one with the audio probe's dbg_stash/dbg_busy ports,
e.g. rc057t14's audio-probe/spu.vhd) and runs it with all 24 voices on, reverb and
CD audio on, SPU RAM answering each request after LAT (+ random 0..JIT) clk1x.
Prints the longest busy run, the stash maximum and the dropped ticks per setting.
Usage: run_spu_budget.py PROJECT [--samples N] [--lat 3,8,13,...] [--jit N] [--pitch HEX]
"""
import argparse
import re
import subprocess
from pathlib import Path

from run_reference_checks import spu_simulation_source
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project', type=Path)
    ap.add_argument('--samples', type=int, default=4000)
    ap.add_argument('--lat', default='3,8,13,20,30')
    ap.add_argument('--jit', type=int, default=0)
    ap.add_argument('--pitch', default='3FFF')
    ap.add_argument('--spu', type=Path, help='this spu.vhd instead of the project\'s')
    ap.add_argument('--extra', default='', help='more generics, space separated (e.g. -gCACHE=1 -gVIDX=1)')
    args = ap.parse_args()
    project = args.project.resolve(); q = (project/'apf_top.qsf').read_text()
    pick = lambda n: sorted(set(re.findall(rf'"([^"]*/{re.escape(n)})"', q)))[0]
    spu, sdp = str(args.spu.resolve()) if args.spu else pick('spu.vhd'), pick('pocket_dpram_sdp.vhd')
    if 'dbg_stash' not in Path(spu).read_text(): raise SystemExit('the project spu.vhd has no audio-probe ports')
    ghdl = find_tool('ghdl'); lib = Path(find_tool('quartus_sh')).parents[1]/'eda/sim_lib'
    d = ROOT/'build/sim/spu-budget'; d.mkdir(parents=True, exist_ok=True)
    for f in d.glob('*.cf'): f.unlink()
    opts = ['--std=08', '-frelaxed', '-fsynopsys']
    def call(*a): subprocess.run([ghdl, *a], cwd=d, check=True, capture_output=True, timeout=900)
    mem = UPSTREAM/'sim/system/src/mem'
    call('-a', *opts, '--work=altera_mf', str(lib/'altera_mf_components.vhd'), str(lib/'altera_mf.vhd'))
    for library in ('mem', 'work'):
        call('-a', *opts, f'--work={library}', str(mem/'dpram.vhd'), str(mem/'RamMLAB.vhd'))
    call('-a', *opts, '--work=mem', str(mem/'SyncFifo.vhd'), str(UPSTREAM/'rtl/SyncFifoFallThrough.vhd'),
         str(UPSTREAM/'rtl/SyncFifoFallThroughMLAB.vhd'), sdp)
    call('-a', *opts, str(UPSTREAM/'rtl/spu_gauss.vhd'), spu_simulation_source(UPSTREAM, d), spu,
         str(ROOT/'sim/reference/tb_spu_budget.vhd'))
    call('-e', *opts, 'tb_spu_budget')
    for lat in map(int, args.lat.split(',')):
        r = subprocess.run([ghdl, '-r', *opts, 'tb_spu_budget', f'-gOPERATIONS={args.samples}', f'-gLAT={lat}',
                            f'-gJIT={args.jit}', f'-gPITCH={int(args.pitch, 16)}', *args.extra.split(), '--ieee-asserts=disable'],
                           cwd=d, capture_output=True, text=True, timeout=14400)
        m = re.search(r'BUDGET .*', r.stdout + r.stderr)
        print(m.group(0) if m else f'LAT={lat}: no result\n' + (r.stdout + r.stderr)[-1500:], flush=True)


if __name__ == '__main__':
    main()
