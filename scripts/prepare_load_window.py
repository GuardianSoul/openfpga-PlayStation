#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Consecutive main-RAM loads: hold MiSTer's 7-cycle load window while a read is in flight.

memorymux lets a RAM load skip WAITFORRAMREAD when the previous load started
less than 7 clk1x ago (`ram_load_last`). On the Pocket the SDRAM read itself
takes most of those 7 cycles, so the window has run out before the CPU can
issue the next load, and each load of a run pays the first-load wait again.
Peip TimingLoadRAM LW2X / LW3X1 / LW3X2: PS1 28 / 32 / 34, release rig
31 / 35 / 36 (docs/timing-cpu-mem.md). This step counts the window down only
while `readram` is low (Codex's load-window experiment, docs/pocket-memorymux-
load-window.md on claude/timing-gaps-059). Window length, the first-load wait,
data paths, the write FIFO, DMA and SDRAM timing are unchanged.

Guest-visible on purpose: no equivalence claim; the Peip rows decide.
Usage: prepare_load_window.py PROJECT
(post-prepare step; memorymux moves to PROJECT/load-window/)
"""
import argparse
import re
import shutil
from pathlib import Path

TAG = 'load-window'
OLD = '         if (ram_load_last > 0) then\n            ram_load_last <= ram_load_last - 1;\n'
NEW = (f"         if (ram_load_last > 0 and readram = '0') then -- {TAG}: hold while a read is in flight\n"
       '            ram_load_last <= ram_load_last - 1;\n')


def memorymux_text(text):
    if TAG in text:
        raise ValueError('memorymux already carries ' + TAG)
    if text.count(OLD) != 1:
        raise ValueError(f'load window countdown: expected one match, found {text.count(OLD)}')
    return text.replace(OLD, NEW)


def apply(project):
    project = Path(project).resolve()
    qsf = project / 'apf_top.qsf'
    text = qsf.read_text()
    muxes = re.findall(r'VHDL_FILE "([^"]*/memorymux\.vhd)"', text)
    if len(muxes) != 1:
        raise SystemExit(f'{TAG}: expected one memorymux.vhd, found {muxes}')
    out = project / TAG
    out.mkdir(exist_ok=False)
    source = Path(muxes[0])
    new = out / 'memorymux.vhd'
    new.write_text(memorymux_text(source.read_text()))
    if (source.parent / 'LICENSE').exists():
        shutil.copy2(source.parent / 'LICENSE', out / 'LICENSE')
    qsf.write_text(text.replace(muxes[0], str(new)))
    return new


if __name__ == '__main__':
    ap = argparse.ArgumentParser(usage=__doc__.split('Usage: ')[1])
    ap.add_argument('project')
    print('patched', apply(ap.parse_args().project))
