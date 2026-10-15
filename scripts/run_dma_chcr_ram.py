#!/usr/bin/env python3
"""Lockstep-compare dma-channel-ram + dma-chcr-ram with upstream MiSTer DMA (same bench)."""
import subprocess

from prepare_dma_channel_ram import channel_ram_source
from prepare_dma_chcr_ram import chcr_ram_text
from prepare_resource_targets import UPSTREAM
from tool_paths import ROOT, find_tool


def main(fault=None):
    ghdl = find_tool('ghdl')
    if not ghdl: raise SystemExit('GHDL is required for DMA channel RAM checks')
    directory = ROOT / 'build/sim' / ('dma-chcr-ram' + ('-fault' if fault else '')); directory.mkdir(parents=True, exist_ok=True)
    candidate = channel_ram_source(directory / 'candidate')
    path = directory / 'candidate/dma.vhd'
    text = chcr_ram_text(path.read_text())
    if fault: text = fault(text)
    text = text.replace('entity dma is', 'entity dma_chanram is').replace('architecture arch of dma is', 'architecture arch of dma_chanram is')
    renamed = directory / 'dma_chanram.vhd'; renamed.write_text(text)
    rtl = UPSTREAM / 'rtl'
    steps = [[ghdl, '-a', '--std=08', '-frelaxed', '--work=mem', str(rtl / name)] for name in ('SyncFifo.vhd', 'SyncFifoFallThrough.vhd')]
    steps += [[ghdl, '-a', '--std=08', '-frelaxed', str(p)] for p in (rtl / 'dma.vhd', renamed, ROOT / 'sim/reference/tb_dma_channel_ram.vhd')]
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
    if not main(): raise SystemExit('DMA CHCR RAM comparison failed')
    # Swapping the stored chop-size and chop-wait fields must be caught.
    if main(lambda t: t.replace("v(22 downto 20) := s(10 downto 8); v(18 downto 16) := s(7 downto 5);",
                                "v(22 downto 20) := s(7 downto 5); v(18 downto 16) := s(10 downto 8);")):
        raise SystemExit('Planted CHCR field fault not detected')
    print('Planted CHCR field fault rejected')
