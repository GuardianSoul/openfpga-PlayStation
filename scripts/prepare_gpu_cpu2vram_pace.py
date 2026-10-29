#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""GPU CPU-to-VRAM copies (GP0 A0h) take data as slowly as a PS1.

ps1-tests dma/chopping, GPU block DMA of 8 KiB of cpuToVram data: a PS1
takes about 1.13 cycles per word, the pinned GPU 1.06, because its
gpu_cpu2vram unit asks for the next FIFO word as soon as it has written the
second pixel of the last one. Large GPU uploads finish 6-7% early. (RobertPeip
DMABLOCKGPU, GP0 NOP payload, is exact and stays so: only A0h data is paced.)

This step holds the unit's FIFO request for one GPU clock (clk2x, half a CPU
cycle) after every --every words of copy data (default 8, chosen on the Pocket
build: with PSRAM VRAM, every 4 overshot large blocks, bs64/bs128 at 1.19/1.26x
of PS1 time in ps1-tests chopping; every 8 keeps every block size at or below
PS1 time, 0.87-0.96x, mean |error| 0.093; 12/16/off 0.103/0.108/0.117).

Guest-visible: CPU-to-VRAM uploads (DMA or GP0 writes) take PS1 time.
Usage: prepare_gpu_cpu2vram_pace.py PROJECT [--every N]
(post-prepare step; patches the gpu_cpu2vram.vhd the project's apf_top.qsf compiles)
"""
import argparse
import re
from pathlib import Path

TAG = 'prepare_gpu_cpu2vram_pace'
SIGNAL_OLD = '   signal fifo_Valid_1 : std_logic;\n'
REQUEST_OLD = "                  '1' when (state = WRITING and pixelStall = '0' and fifo_Valid = '0'"
DEFAULT_OLD = "            fifo_Valid_1 <= '0';\n"
WORD_OLD = ('               when WRITING => \n'
            "                  if (fifo_Valid = '1') then\n"
            '                     fifo_Valid_1 <= fifo_Valid;\n')


def transform(text, every=8):
    if TAG in text:
        return text
    for what, old in (('signal', SIGNAL_OLD), ('request', REQUEST_OLD), ('default', DEFAULT_OLD), ('word', WORD_OLD)):
        if text.count(old) != 1:
            raise ValueError(f'{what}: expected exactly one match, found {text.count(old)}')
    text = text.replace(SIGNAL_OLD, SIGNAL_OLD +
        f"   signal paceCount    : integer range 0 to {every - 1} := 0; -- {TAG}: copy words since the last hold\n"
        "   signal paceHold     : std_logic := '0';\n")
    text = text.replace(REQUEST_OLD, REQUEST_OLD.replace("pixelStall = '0'", "pixelStall = '0' and paceHold = '0'"))
    text = text.replace(DEFAULT_OLD, DEFAULT_OLD + "            paceHold     <= '0';\n")
    return text.replace(WORD_OLD, WORD_OLD +
        f'                     if (paceCount = {every - 1}) then -- {TAG}: PS1 intake rate\n'
        '                        paceCount <= 0;\n'
        "                        paceHold  <= '1';\n"
        '                     else\n'
        '                        paceCount <= paceCount + 1;\n'
        '                     end if;\n')


def apply(project, every=8):
    project = Path(project).resolve()
    qsf = (project / 'apf_top.qsf').read_text()
    paths = sorted({Path(p) for p in re.findall(r'VHDL_FILE "([^"]*/gpu_cpu2vram\.vhd)"', qsf)})
    if len(paths) != 1:
        raise SystemExit(f'{TAG}: expected one compiled gpu_cpu2vram.vhd, found {len(paths)}')
    path = paths[0].resolve()
    if project not in path.parents:
        raise SystemExit(f'{TAG}: {path} is outside the project; refusing to patch a shared source')
    try:
        path.write_text(transform(path.read_text(), every))
    except ValueError as error:
        raise SystemExit(f'{TAG}: {path}: {error}')
    return path


if __name__ == '__main__':
    ap = argparse.ArgumentParser(usage=__doc__.split('Usage: ')[1])
    ap.add_argument('project'); ap.add_argument('--every', type=int, default=8)
    a = ap.parse_args()
    if a.every < 1:
        raise SystemExit(f'{TAG}: --every must be 1 or more')
    print('patched', apply(a.project, a.every))
