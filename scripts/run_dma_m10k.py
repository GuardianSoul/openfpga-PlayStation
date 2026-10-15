#!/usr/bin/env python3
"""Lockstep-compare dma-m10k with the dma-channel-ram + dma-chcr-ram DMA it
transforms (block RAM against MLAB tables).

The tb_dma_channel_ram bench, except that its stimulus changes 1 ns after
the rising clk1x edge instead of on the falling edge: in psx_top every DMA
input is a clk1x register (bus_addr is memorymux's addressData_buf), and a
falling-edge block RAM read samples bus_addr at that falling edge. Resets
hit only an idle DMA. The check compares against the MLAB variant, the
source this transform starts from.
"""
import subprocess

from prepare_dma_channel_ram import channel_ram_source
from prepare_dma_chcr_ram import chcr_ram_text
from prepare_dma_m10k import dma_m10k_text
from prepare_resource_targets import UPSTREAM
from tool_paths import ROOT, find_tool


def main(fault=None):
    ghdl = find_tool('ghdl')
    if not ghdl: raise SystemExit('GHDL is required for DMA channel RAM checks')
    directory = ROOT / 'build/sim' / ('dma-m10k' + ('-fault' if fault else '')); directory.mkdir(parents=True, exist_ok=True)
    candidate = channel_ram_source(directory / 'candidate')
    path = directory / 'candidate/dma.vhd'
    mlab = chcr_ram_text(path.read_text())
    reference = directory / 'dma_mlab.vhd'; reference.write_text(mlab)
    text = dma_m10k_text(mlab)
    if fault: text = fault(text)
    text = text.replace('entity dma is', 'entity dma_chanram is').replace('architecture arch of dma is', 'architecture arch of dma_chanram is')
    renamed = directory / 'dma_chanram.vhd'; renamed.write_text(text)
    rtl = UPSTREAM / 'rtl'
    steps = [[ghdl, '-a', '--std=08', '-frelaxed', '--work=mem', str(rtl / name)] for name in ('SyncFifo.vhd', 'SyncFifoFallThrough.vhd')]
    bench = (ROOT / 'sim/reference/tb_dma_channel_ram.vhd').read_text()
    old = '         wait until falling_edge(clk1x);\n         reset <= chance(0.00005);\n'
    if bench.count(old) != 1: raise SystemExit('tb_dma_channel_ram stimulus changed')
    tb = directory / 'tb_dma_rising.vhd'
    # A one-cycle reset that lands on a pending request starts a transfer of
    # zeroed registers and trips MiSTer's own "not possible" checks; reset only
    # an idle DMA (psx_top resets the whole system for thousands of cycles).
    tb.write_text(bench.replace(old, "         wait until rising_edge(clk1x); wait for 1 ns;\n"
                                     "         if o.dmaRequest = '0' and o.dmaOn = '0' then reset <= chance(0.00005); else reset <= '0'; end if;\n"))
    steps += [[ghdl, '-a', '--std=08', '-frelaxed', str(p)] for p in (reference, renamed, tb)]
    steps.append([ghdl, '-e', '--std=08', '-frelaxed', 'tb_dma_channel_ram'])
    output = ''
    for seed in (1, 2, 3):
        runs = steps + [[ghdl, '-r', '--std=08', '-frelaxed', 'tb_dma_channel_ram', f'-gSEED={seed}', '--assert-level=error']] if seed == 1 else \
            [[ghdl, '-r', '--std=08', '-frelaxed', 'tb_dma_channel_ram', f'-gSEED={seed}', '--assert-level=error']]
        for command in runs:
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=900)
            output += result.stdout + result.stderr
            if result.returncode: return False
    (directory / 'checks.log').write_text(output)
    passes = [line for line in output.splitlines() if 'PASS DMA channel RAM' in line]
    for line in passes: print(line[line.index('PASS'):])
    return len(passes) == 3


if __name__ == '__main__':
    if not main(): raise SystemExit('DMA block RAM comparison failed')
    # A trigger-copy read on the rising edge (one cycle late) must be caught.
    if main(lambda t: t.replace('         madrMem_trig_q <= madrMem_trig(triggerchannel);\n', '')
                       .replace('   bus_MADR  <=', '   process (clk1x) begin if rising_edge(clk1x) then madrMem_trig_q <= madrMem_trig(triggerchannel); end if; end process;\n   bus_MADR  <=')):
        raise SystemExit('Planted late trigger-read fault not detected')
    print('Planted late trigger-read fault rejected')
