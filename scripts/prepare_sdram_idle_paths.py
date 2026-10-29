#!/usr/bin/env python3
"""SDRAM controller: take the request flags out of two idle-edge cones.

rel057h sign-off (clk3x, Slow 85C) failed inside psx_sdram_ctrl on
pend1 -> a[3] (-0.259) and ref_owed -> st1[1] (-0.085). Two exact rewrites:

- st1: the next ch1 row state was computed from `after_base && ch1_req &&
  !took1 ? ch1_addr : ch1_adr_q`, and took1 is the idle edge's whole choice
  (refresh, precharge, SPU, FIFO, ch1). st1 is only read while pend1 is set
  (next_st, open_hit1 and sel_st all need req1, which is pend1 with
  OPEN_ROW's CL3; without OPEN_ROW st1 is unused). On an edge where took1
  and a new capture coincide, pend1 is cleared and stays clear, so st1 is a
  don't-care there and the select can drop !took1.
- a[]: the idle edge's pin value was `open_hit1 ? column(ch1_a) :
  row_of(idle_addr)`, with pend1, spu_first and fifo_v in both the open_hit1
  AND and the address priority mux. open_hit1 is the ch1 branch of that
  priority mux plus a pend1-free condition (hit1), so the value is written
  as one priority mux whose ch1 input is hit1 ? column : row. pend1 then
  only selects, once, at the mux.
Every command, address and output stays cycle-identical
(scripts/run_sdram_idle_paths.py, alone and stacked on prepare_sdram_ref_owed).
Usage: prepare_sdram_idle_paths.py PROJECT
"""
from pathlib import Path
import re
import sys

MARK = 'sdram-idle-paths'
EDITS = (
    ('            st1 <= row_state(after_base && ch1_req && !took1 ? ch1_addr : ch1_adr_q);\n',
     f'            // {MARK}: st1 only matters while pend1 is set; took1 with a new capture clears pend1\n'
     '            st1 <= row_state(after_base && ch1_req ? ch1_addr : ch1_adr_q);\n', 'st1'),
    ('                     && st1 == ST_HIT && ch1_rnw;\n',
     '                     && st1 == ST_HIT && ch1_rnw;\n'
     f'    // {MARK}: the idle edge\'s a[] value as one priority mux; open_hit1 is its ch1 input\'s\n'
     '    // pend1-free hit1 (no flag reaches a[] through the open_hit1 AND).\n'
     '    wire hit1 = OPEN_ROW != 0 && !need_ref && st1 == ST_HIT && ch1_rnw;\n'
     '    wire [12:0] idle_a = spu_first ? row_of(ch3_a) : fifo_v ? row_of(fifo_adr)\n'
     '                       : (pend1 || (!LATE_START && ch1_req)) ? (hit1 ? column(ch1_a, 9\'d0, 1\'b0) : row_of(ch1_a))\n'
     '                       : row_of((pend2 || (!LATE_START && ch2_req)) ? ch2_a : ch3_a);\n', 'idle_a'),
    ("                a <= open_hit1 ? column(ch1_a, 9'd0, 1'b0) : row_of(idle_addr); ba <= bank_of(idle_addr);",
     "                a <= idle_a; ba <= bank_of(idle_addr);", 'a'),
)


def idle_paths_text(text):
    if MARK in text: raise ValueError(f'{MARK} already applied')
    for old, new, what in EDITS:
        if text.count(old) != 1: raise ValueError(f'{MARK}: {what} anchor found {text.count(old)} times')
        text = text.replace(old, new)
    return text


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/psx_sdram_ctrl\.sv)"', q)
    if len(set(hits)) != 1: raise ValueError(f'{MARK}: expected one psx_sdram_ctrl.sv in the project')
    out = project/MARK/'psx_sdram_ctrl.sv'; out.parent.mkdir(exist_ok=True)
    out.write_text(idle_paths_text(Path(hits[0]).read_text()))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
