#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""DMA6 (OTC) keeps only the CHCR bits a PS1 has for it.

On a PS1 channel 6's CHCR stores only bits 24 (start/busy), 28 (start
trigger) and 30; bit 1 (address step backward) reads as 1 and every other
bit as 0, so the order-table clear always runs backward, from RAM-start
direction "to RAM", sync mode 0, without chopping. ps1-tests dma/otc-test
(real PS1 13/13) writes forward step, from-RAM direction, sync modes 1/2/3
and stray bits; the pinned DMA unit stored them all and the clear ran the
wrong way or not at all (15 failed assertions). The dma-chcr-ram write now
stores bit 30 and a forced bit 1 for channel 6; bits 24/28 are unchanged.

Guest-visible: only CHCR6 writes other than the standard 0x11000002 change.
Usage: prepare_dma_otc.py PROJECT  (post-prepare step; patches every dma.vhd
the project's apf_top.qsf compiles; needs dma-chcr-ram)
"""
import re
import sys
from pathlib import Path

TAG = 'prepare_dma_otc'
OLD = "chcr_we := '1'; chcr_wa := channel; chcr_wd := chcrPack(unsigned(bus_dataWrite));\n"
NEW = ("chcr_we := '1'; chcr_wa := channel; chcr_wd := chcrPack(unsigned(bus_dataWrite));\n"
       "                        if (channel = 6) then -- " + TAG + ": OTC stores bit 30 only, bit 1 reads 1\n"
       "                           chcr_wd := chcrPack(unsigned(bus_dataWrite) and x\"40000000\") or to_unsigned(2, 13);\n"
       "                        end if;\n")


def transform(text):
    if TAG in text:
        return text
    if text.count(OLD) != 1:
        raise ValueError(f'CHCR write (dma-chcr-ram): expected exactly one match, found {text.count(OLD)}')
    return text.replace(OLD, NEW)


def apply(project):
    project = Path(project)
    qsf = (project / 'apf_top.qsf').read_text()
    dmas = sorted({Path(p) for p in re.findall(r'VHDL_FILE "([^"]*/dma\.vhd)"', qsf)})
    if not dmas:
        raise SystemExit(f'{TAG}: the project compiles no dma.vhd')
    for dma in dmas:
        try:
            dma.write_text(transform(dma.read_text()))
        except ValueError as error:
            raise SystemExit(f'{TAG}: {dma}: {error}')
    return dmas


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split('Usage: ')[1])
    for p in apply(sys.argv[1]):
        print('patched', p)
