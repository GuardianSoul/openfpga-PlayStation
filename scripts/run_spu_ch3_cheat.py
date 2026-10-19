#!/usr/bin/env python3
"""Cheat port of the slim SPU cache (sim/memory/tb_spu_ch3_cheat.sv).

Runs psx_spu_ch3_cache_slim.sv with CHEAT=1 on psx_sdram_ctrl and the chip
model: SPU-like traffic and random cheat byte/halfword reads and writes
against a golden copy, with a guest reset in the middle. Passes when every
seed has no errors and no late SPU tick, and two planted faults are caught
(cheat accesses taken over a pending SPU request; the wrong byte lane).
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from run_sdram_ctrl import ALTDDIO
from tool_paths import ROOT, find_tool

RESULT = re.compile(r'RESULT errors (\d+) late_ticks (\d+) cheats (\d+) discard_zeros (\d+)')
CACHE = ROOT/'rtl/pocket/psx_spu_ch3_cache_slim.sv'
FAULTS = {'spu-second': ('end else if (take) begin', 'end else if (take && !(CHEAT!=0 && ch_pend)) begin'),
          'byte-lane': ("ch_addr[0] ? 4'b0010 : 4'b0001", "ch_addr[0] ? 4'b0001 : 4'b0010")}


def run(work, name, cache, seed):
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    out = work/f'{name}.vvp'
    subprocess.run([iverilog, '-g2012', '-o', str(out), '-s', 'tb_spu_ch3_cheat', f'-Ptb_spu_ch3_cheat.SEED={seed}',
                    str(work/'altddio_out.sv'), str(ROOT/'rtl/memory/psx_sdram_ctrl.sv'), str(ROOT/'sim/memory/sdram_chip_model.sv'),
                    str(cache), str(ROOT/'sim/memory/tb_spu_ch3_cheat.sv')], check=True)
    r = subprocess.run([vvp, '-n', str(out)], cwd=work, capture_output=True, text=True, timeout=1800)
    (work/f'{name}.log').write_text(r.stdout + r.stderr)
    m = RESULT.search(r.stdout)
    if not m: sys.exit(f'{name}: no result (see {work}/{name}.log)')
    return tuple(map(int, m.groups()))


def main():
    if not find_tool('iverilog'): sys.exit('Icarus Verilog not found (scripts/setup_iverilog.sh)')
    parent = ROOT/'build/sim/spu-ch3-cheat'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    (work/'altddio_out.sv').write_text(ALTDDIO)
    ok = True
    for seed in (1, 2, 3):
        errors, late, cheats, zeros = run(work, f'seed{seed}', CACHE, seed)
        good = errors == 0 and late == 0 and cheats > 1000 and zeros > 0
        ok &= good
        print(f'seed {seed}: errors {errors} late ticks {late} cheat accesses {cheats} reset answers {zeros}: {"pass" if good else "FAIL"}')
    text = CACHE.read_text()
    for name, (old, new) in FAULTS.items():
        if text.count(old) != 1: sys.exit(f'fault {name}: anchor not found')
        (work/f'{name}.sv').write_text(text.replace(old, new))
        errors, *_ = run(work, name, work/f'{name}.sv', 1)
        ok &= errors > 0
        print(f'fault {name}: {errors} errors: {"caught" if errors else "MISSED"}')
    print('PASS' if ok else 'FAIL')
    if not ok: sys.exit(1)


if __name__ == '__main__':
    main()
