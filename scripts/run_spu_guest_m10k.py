#!/usr/bin/env python3
"""Cycle-lockstep spu-guest-m10k against the SPU it transforms.

Both run context RAM + reverb table (the reset-only build's SPU). Random
writes and readbacks of every SPU address, including write-then-read of the
same address, run while voices, reverb and memory traffic proceed; every
output is compared on every clk1x cycle (3 seeds). A planted rising-edge
(one cycle late) readback and a planted wrong guest address must fail.
"""
from pathlib import Path
import re
import subprocess

from prepare_spu_context_ram import context_ram_source
from prepare_spu_reverb_table import reverb_table_source
from prepare_spu_guest_m10k import guest_m10k_text
from run_reference_checks import spu_simulation_source
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'


def run(directory, variant_text, seeds, operations=6000, bench=None, top='tb_spu_guest_m10k'):
    ghdl = find_tool('ghdl'); directory.mkdir(parents=True, exist_ok=True)
    log = (directory / 'build.log').open('w')
    def call(*args): subprocess.run([ghdl, *args], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    mem = UPSTREAM / 'sim/system/src/mem'
    for library in ('mem', 'work'):
        call('-a', '--std=08', '-frelaxed', f'--work={library}', str(mem / 'dpram.vhd'))
        call('-a', '--std=08', '-frelaxed', f'--work={library}', str(mem / 'RamMLAB.vhd'))
    call('-a', '--std=08', '-frelaxed', '--work=mem', str(mem / 'SyncFifo.vhd'))
    for name in ('SyncFifoFallThrough.vhd', 'SyncFifoFallThroughMLAB.vhd'):
        call('-a', '--std=08', '-frelaxed', '--work=mem', str(UPSTREAM / 'rtl' / name))
    call('-a', '--std=08', '-frelaxed', str(UPSTREAM / 'rtl/spu_gauss.vhd'))
    call('-a', '--std=08', '-frelaxed', spu_simulation_source(UPSTREAM, directory))
    base = Path(reverb_table_source(directory / 'table', context_ram_source(directory / 'context', UPSTREAM / 'rtl/spu.vhd')['path'])['path'])
    call('-a', '--std=08', '-frelaxed', str(base))
    variant = directory / 'spu_g.vhd'
    variant.write_text(re.sub(r'\bspu\b', 'spu_g', variant_text(base.read_text())))
    call('-a', '--std=08', '-frelaxed', str(variant))
    call('-a', '--std=08', '-frelaxed', str(bench or ROOT / 'sim/reference/tb_spu_guest_m10k.vhd'))
    call('-e', '--std=08', '-frelaxed', top)
    results = []
    for seed in seeds:
        r = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', top, f'-gSEED={seed}', f'-gOPERATIONS={operations}',
                            '--assert-level=error', '--ieee-asserts=disable'], cwd=directory, capture_output=True, text=True, timeout=3600)
        out = r.stdout + r.stderr
        results.append((r.returncode == 0 and 'PASS SPU guest block RAM lockstep' in out,
                        [l for l in out.splitlines() if 'PASS' in l or 'mismatch' in l][:1]))
    return results


def main():
    directory = ROOT / 'build/sim/spu-guest-m10k'
    failures = []
    for passed, tail in run(directory / 'variant', guest_m10k_text, (1, 2, 3)):
        print('PASS' if passed else 'FAIL', *tail)
        if not passed: failures.append('lockstep')
    faults = {
        'rising-edge': lambda t: guest_m10k_text(t).replace('clock_b => not clk1x,\n            address_b => std_logic_vector(RamVoice_addrB(i))',
                                                            'clock_b => clk1x,\n            address_b => std_logic_vector(RamVoice_addrB(i))', 1),
        'address': lambda t: guest_m10k_text(t).replace('rv_guest_ram_q <= rvTable_guest(rv_guest_addr);',
                                                        'rv_guest_ram_q <= rvTable_guest((rv_guest_addr + 1) mod 32);'),
    }
    for name, fault in faults.items():
        caught = not run(directory / ('fault-' + name), fault, (1,), 2000)[0][0]
        print(f'fault {name}: {"rejected" if caught else "NOT DETECTED"}')
        if not caught: failures.append(name)
    if failures: raise SystemExit('SPU guest block RAM check failed: ' + ', '.join(failures))
    print('SPU guest readback copies in block RAM are cycle-identical; planted faults rejected')


if __name__ == '__main__': main()
