#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""GPU DMA (channel 2) holds the CPU as long per block / list node as a PS1.

ps1-tests dma/chopping, sync mode 1 (block) to the GPU, 8 KiB: a PS1 takes
about 10 cycles per block plus 1.13-1.16 per word; the pinned DMA unit about
7.5 per block (10 at least) plus 1.03-1.10 per word, so every block size
from 1 to 128 finishes 7-18% early. ps1-tests dma/chain-looping:
a self-referencing GPU linked list takes 37% of the CPU's time on a PS1 and
14% here (the CPU work runs 0.59x of PS1 time).

RobertPeip DMALINKEDLIST: a list whose nodes are all empty takes 2 cycles
more than here (each extra empty node 10 on both). RobertPeip
DMATimingToDevice: an SPU block-mode upload from RAM takes about 75 cycles
more per block; the SPU asks for the next block when its transfer FIFO is
empty, and a PS1 answers that request later.

RobertPeip DMABLOCKGPU (small GPU blocks, idle GPU) is already exact
without a per-block add, so the chopping gap comes from how fast the GPU
asks for more data, not from DMA block overhead.

This step adds --list-end CPU-paused cycles (REP_target, the DMA unit's
minimum CPU-pause time) to a list that ends on a node without words, and
makes the SPU's (--spu-req-delay) and the GPU's (--gpu-req-delay) DMA request
start a transfer only after it has been held that many cycles (0 = unchanged).
A request already held when the CPU starts the DMA starts it at once, so
single-block and manual DMA keep their time. --block N (0 = off) restores
the older per-block add on channel 2 (N plus words/8, at most 8). Defaults
are the values the sim matched against the hardware logs (--spu-req-delay 28
on the release Pocket build with PSRAM VRAM: Peip SPU16BLOCK16 16,725 in
16,607-16,770; 78, tuned on the ideal build, gave 17,617); see
docs/ps1-hw-timing.md. An extra CPU stall per empty list node (ps1-tests
dma/chain-looping) breaks the exact Peip node times: chain-looping measures
CPU/bus contention while cached code runs next to the DMA.

Guest-visible: GPU block and linked-list DMA take PS1 time; games run as
on a PS1 where they used to have spare time.
Usage: prepare_dma_ps1_timing.py PROJECT [--block N] [--list-end N] [--spu-req-delay N] [--gpu-req-delay N]
(post-prepare step; patches every dma.vhd the project's apf_top.qsf compiles)
"""
import argparse
import re
from pathlib import Path

TAG = 'prepare_dma_ps1_timing'
BLOCK_OLD = ('                           when "01" => -- request\n'
             '                              dmaSettings.D_BCR(31 downto 16) <= blocksleft;\n')
LIST_END_OLD = ("                     elsif (dma_data(23) = '1' or toDevice = '0') then\n"
                '                        dmaState <= STOPPING;\n')
SIGNAL_OLD = '   signal REP_required        : std_logic;\n'
GPU_TRIGGER_OLD = "            if (dmaArray(2).D_CHCR(28) = '1' or gpu_dmaRequest = '1')        then triggerDMA(2) <= '1'; end if;\n"
SPU_TRIGGER_OLD = "            if (dmaArray(4).D_CHCR(28) = '1' or spu_dmaRequest = '1')        then triggerDMA(4) <= '1'; end if;\n"


def transform(text, block=0, list_end=3, spu_req_delay=28, gpu_req_delay=0):
    if TAG in text:
        return text
    for what, old in (('block end', BLOCK_OLD), ('list end', LIST_END_OLD), ('signals', SIGNAL_OLD),
                      ('SPU trigger', SPU_TRIGGER_OLD), ('GPU trigger', GPU_TRIGGER_OLD)):
        if text.count(old) != 1:
            raise ValueError(f'{what}: expected exactly one match, found {text.count(old)}')
    text = text.replace(SIGNAL_OLD, SIGNAL_OLD + f'   -- {TAG}\n')
    if block:
        text = text.replace(BLOCK_OLD, BLOCK_OLD +
            f'                              if (activeChannel = 2) then -- {TAG}: PS1 per-block time\n'
            f'                                 if (dmaSettings.D_BCR(15 downto 6) /= 0) then -- + words/8, at most 8\n'
            f'                                    rep_add   := rep_add + {block} + 8;\n'
            '                                 else\n'
            f'                                    rep_add   := rep_add + {block} + to_integer(dmaSettings.D_BCR(5 downto 3));\n'
            '                                 end if;\n'
            "                                 REP_required <= '1';\n"
            '                              end if;\n')
    if list_end:
        text = text.replace(LIST_END_OLD, LIST_END_OLD +
            f"                        if (toDevice = '1') then -- {TAG}: PS1 time for a list ending on an empty node\n"
            f'                           REP_target   <= REP_target + {list_end};\n'
            "                           REP_required <= '1';\n"
            '                        end if;\n')
    for name, delay, old in (('gpu', gpu_req_delay, GPU_TRIGGER_OLD), ('spu', spu_req_delay, SPU_TRIGGER_OLD)):
        if not delay:
            continue
        ch = 2 if name == 'gpu' else 4
        held = f'{name}ReqHeld'
        text = text.replace(SIGNAL_OLD, SIGNAL_OLD +
            f'   signal {held:<20}: integer range 0 to {delay} := 0; -- {TAG}: cycles the {name.upper()} DMA request is held\n')
        text = text.replace(old,
            f"            if ({name}_dmaRequest = '0') then -- {TAG}: a PS1 answers the {name.upper()}'s request {delay} cycles late\n"
            f'               {held} <= 0;\n'
            f'            elsif ({held} < {delay}) then\n'
            f'               {held} <= {held} + 1;\n'
            '            end if;\n'
            f"            if (dmaArray({ch}).D_CHCR(28) = '1' or ({name}_dmaRequest = '1' and {held} = {delay})) then triggerDMA({ch}) <= '1'; end if;\n")
    return text


def apply(project, **params):
    project = Path(project)
    qsf = (project / 'apf_top.qsf').read_text()
    dmas = sorted({Path(p) for p in re.findall(r'VHDL_FILE "([^"]*/dma\.vhd)"', qsf)})
    if not dmas:
        raise SystemExit(f'{TAG}: the project compiles no dma.vhd')
    for dma in dmas:
        try:
            dma.write_text(transform(dma.read_text(), **params))
        except ValueError as error:
            raise SystemExit(f'{TAG}: {dma}: {error}')
    return dmas


if __name__ == '__main__':
    ap = argparse.ArgumentParser(usage=__doc__.split('Usage: ')[1])
    ap.add_argument('project')
    for name, default in (('block', 0), ('list-end', 3), ('spu-req-delay', 28), ('gpu-req-delay', 0)):
        ap.add_argument('--' + name, type=int, default=default)
    a = vars(ap.parse_args())
    for p in apply(a.pop('project'), **a):
        print('patched', p)
