#!/usr/bin/env python3
"""Lockstep-compare dma-share with the dma-m10k DMA it transforms.

The run_dma_m10k bench (rising-edge stimulus, idle-only resets): every
output of both DMAs is compared each clock for three seeds. Planted faults
(one wrong timing increment, one wrong RAM address step) must be caught.
"""
import subprocess

from prepare_dma_channel_ram import channel_ram_source
from prepare_dma_chcr_ram import chcr_ram_text
from prepare_dma_m10k import dma_m10k_text
from prepare_dma_share import dma_share_text
from prepare_resource_targets import UPSTREAM
from tool_paths import ROOT, find_tool


def main(fault=None, name='dma-share'):
    ghdl = find_tool('ghdl')
    if not ghdl: raise SystemExit('GHDL is required for DMA checks')
    directory = ROOT / 'build/sim' / name; directory.mkdir(parents=True, exist_ok=True)
    channel_ram_source(directory / 'candidate')
    m10k = dma_m10k_text(chcr_ram_text((directory / 'candidate/dma.vhd').read_text()))
    reference = directory / 'dma_m10k.vhd'; reference.write_text(m10k)
    text = dma_share_text(m10k)
    if fault: text = fault(text)
    text = text.replace('entity dma is', 'entity dma_chanram is').replace('architecture arch of dma is', 'architecture arch of dma_chanram is')
    renamed = directory / 'dma_share.vhd'; renamed.write_text(text)
    rtl = UPSTREAM / 'rtl'
    steps = [[ghdl, '-a', '--std=08', '-frelaxed', '--work=mem', str(rtl / n)] for n in ('SyncFifo.vhd', 'SyncFifoFallThrough.vhd')]
    bench = (ROOT / 'sim/reference/tb_dma_channel_ram.vhd').read_text()
    old = '         wait until falling_edge(clk1x);\n         reset <= chance(0.00005);\n'
    if bench.count(old) != 1: raise SystemExit('tb_dma_channel_ram stimulus changed')
    tb = directory / 'tb_dma_rising.vhd'
    tb.write_text(bench.replace(old, "         wait until rising_edge(clk1x); wait for 1 ns;\n"
                                     "         if o.dmaRequest = '0' and o.dmaOn = '0' then reset <= chance(0.00005); else reset <= '0'; end if;\n"))
    steps += [[ghdl, '-a', '--std=08', '-frelaxed', str(p)] for p in (reference, renamed, tb)]
    steps.append([ghdl, '-e', '--std=08', '-frelaxed', 'tb_dma_channel_ram'])
    output = ''
    for command in steps:
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=900)
        output += result.stdout + result.stderr
        if result.returncode: print(output[-2000:]); return False
    passes = []
    for seed in (1, 2, 3):
        result = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', 'tb_dma_channel_ram', f'-gSEED={seed}', '--assert-level=error'],
                                cwd=directory, capture_output=True, text=True, timeout=1800)
        output += result.stdout + result.stderr
        if result.returncode: break
        passes += [l[l.index('PASS'):] for l in (result.stdout + result.stderr).splitlines() if 'PASS DMA channel RAM' in l]
    (directory / 'checks.log').write_text(output)
    for line in passes: print(line)
    return len(passes) == 3


if __name__ == '__main__':
    if not main(): raise SystemExit('dma-share comparison failed')
    if main(lambda t: t.replace("rep_add := 4 + (to_integer(spu_timing_value) * 2);", "rep_add := 5 + (to_integer(spu_timing_value) * 2);"), 'dma-share-fault1'):
        raise SystemExit('Planted SPU timing fault not detected')
    print('Planted SPU timing fault rejected')
    if main(lambda t: t.replace('ram_step := "1111111111111111111" & not unsigned(ram_cnt) & "00";', 'ram_step := "1111111111111111111" & unsigned(ram_cnt) & "00";'), 'dma-share-fault2'):
        raise SystemExit('Planted RAM address fault not detected')
    print('Planted RAM address fault rejected')
