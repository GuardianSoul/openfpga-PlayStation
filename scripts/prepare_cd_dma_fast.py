#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CD DMA without the 24-clock-per-word CPU stall (Crash Bandicoot level 1).

The pinned MiSTer DMA unit stalls the CPU for 24 clocks per word of a CD-ROM
DMA, in two places: `REP_target` adds 24 per word (skipped by the
`ignoreCDTiming` input, which the Pocket top ties to '0' through
IGNORECDDMATIMING), and an unconditional SLOWDOWN state loads `slowcnt` with 24
for every CD word read (not gated by `ignoreCDTiming`). A 2 KB sector therefore
halts the CPU for about 0.38 ms, 5.6% of a field at the 2x rate. Crash Bandicoot
level 1 streams 96 sectors after the first crates and drops from 30 to 20 fps for
that long (docs/crash-slowdown.md); real hardware does not.

This step ties IGNORECDDMATIMING to '1' in psx_mister_psx_top_resource.vhd and
gates the SLOWDOWN load with `ignoreCDTiming = '0'` in every dma.vhd the project
compiles. Hardware (perf7, Crash level 1): CPU paused for DMA 5.2% of the burst
(was 9.0-9.6%), heavy draw frames every 2 vsyncs throughout, no 20 fps run.
Guest-visible change: CD DMA transfers finish sooner; nothing else.
The two charges overlap rather than add: SLOWDOWN really holds the transfer 24 clocks per
word, REP_target keeps dmaOn (and so the CPU pause) up until a running total of 24 per word has
elapsed. Hardware (CPU paused for DMA in the Crash level-1 burst): baseline 9.6%, only REP_target
removed (perf6) 9.0%, both removed (perf7) 5.2%. So `--duplicate-only` (keep REP_target, remove
only the SLOWDOWN state) is expected to still stall about 24 clocks per word; use it to check.
Usage: prepare_cd_dma_fast.py PROJECT [--duplicate-only]
"""
import re
import sys
from pathlib import Path

TIE_OLD = "        IGNORECDDMATIMING => '0',"
TIE_NEW = "        IGNORECDDMATIMING => '1',"
SLOW_OLD = ("                        if (activeChannel = 3 and toDevice = '0') then -- CD access\n"
            "                           slowcnt  <= 24;")
SLOW_NEW = ("                        if (activeChannel = 3 and toDevice = '0' and ignoreCDTiming = '0') then -- CD access (prepare_cd_dma_fast: skipped with IGNORECDDMATIMING)\n"
            "                           slowcnt  <= 24;")


def patch(text, old, new, what):
    if new in text:
        return text           # already applied
    if text.count(old) != 1:
        raise SystemExit(f'prepare_cd_dma_fast: {what}: expected exactly one match, found {text.count(old)}')
    return text.replace(old, new)


SLOW_DUP = ("                        if (activeChannel = 3 and toDevice = '0' and false) then -- CD access (prepare_cd_dma_fast --duplicate-only: SLOWDOWN removed, REP_target kept)\n"
            "                           slowcnt  <= 24;")


def apply(project, duplicate_only=False):
    project = Path(project)
    wrapper = project / 'psx_mister_psx_top_resource.vhd'
    if not duplicate_only:
        wrapper.write_text(patch(wrapper.read_text(), TIE_OLD, TIE_NEW, 'IGNORECDDMATIMING tie'))
    qsf = (project / 'apf_top.qsf').read_text()
    dmas = sorted({Path(p) for p in re.findall(r'VHDL_FILE "([^"]*/dma\.vhd)"', qsf)})
    if not dmas:
        raise SystemExit('prepare_cd_dma_fast: the project compiles no dma.vhd')
    for dma in dmas:
        dma.write_text(patch(dma.read_text(), SLOW_OLD, SLOW_DUP if duplicate_only else SLOW_NEW, f'{dma} CD slowdown'))
    return [wrapper, *dmas]


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--duplicate-only']
    if len(args) != 1:
        raise SystemExit(__doc__)
    for p in apply(args[0], duplicate_only='--duplicate-only' in sys.argv[1:]):
        print('patched', p)
