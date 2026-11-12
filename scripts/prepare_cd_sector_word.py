#!/usr/bin/env python3
"""cd_top: one register for the sector word on its way to the sector buffers, XA and CDDA.

sectorProcessState copies sectorBuffer_DataB into three 32-bit registers:
sectorBuffers_DataA (SPROC_DATA), XA_data (SPROC_XA) and CDDA_data (SPROC_CDDA).
The states exclude each other and each register is read only under the
one-clock strobe set with it: sectorBuffers_wrenA (the packed sector RAM),
XA_write (data_a of cd_xa's input RAM) and CDDA_write (FifoOut_Din in cd_xa),
all on clk1x. So one register, loaded in all three states, gives each consumer
the same word on the same clock; the strobes, addresses and states are not
touched. The save-state load of sectorBuffers_DataA is inside "if (false)"
(cd-sector-pack). Check: scripts/run_cd_sector_word.py, docs/cd-sector-word.md.
Run after the cd_top steps (prepare_cd_buffer_sizes.py among them).
Usage: prepare_cd_sector_word.py PROJECT
"""
from pathlib import Path
import re
import sys

MARK = 'cd-sector-word'


def once(text, old, new, what):
    if text.count(old) != 1: raise ValueError(f'{MARK}: {what} anchor found {text.count(old)} times')
    return text.replace(old, new)


def word_text(t):
    if MARK in t: raise ValueError(f'{MARK} already applied')
    t = once(t, "                  XA_data  <= sectorBuffer_DataB;\n",
             f"                  sectorBuffers_DataA <= sectorBuffer_DataB;   -- {MARK}: XA_data\n", 'XA load')
    t = once(t, "                  CDDA_data  <= sectorBuffer_DataB;\n",
             f"                  sectorBuffers_DataA <= sectorBuffer_DataB;   -- {MARK}: CDDA_data\n", 'CDDA load')
    # the consumers keep their names, as wires
    t = once(t, "   icd_xa : entity work.cd_xa\n",
             f"   XA_data   <= sectorBuffers_DataA;   -- {MARK}\n"
             f"   CDDA_data <= sectorBuffers_DataA;   -- {MARK}\n\n"
             "   icd_xa : entity work.cd_xa\n", 'cd_xa instance')
    loads = re.findall(r'^\s*(XA_data|CDDA_data)\s*<=.*$', t, re.M)
    if loads != ['XA_data', 'CDDA_data']: raise ValueError(f'{MARK}: other drivers left: {loads}')
    return t


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = sorted(set(re.findall(r'"([^"]*/cd_top\.vhd)"', q)))
    if len(hits) != 1: raise ValueError(f'{MARK}: expected one cd_top.vhd in the project, found {hits}')
    out = project/MARK/'cd_top.vhd'; out.parent.mkdir(exist_ok=True)
    out.write_text(word_text(Path(hits[0]).read_text()))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
