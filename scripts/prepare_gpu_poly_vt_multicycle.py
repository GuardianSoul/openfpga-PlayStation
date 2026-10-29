#!/usr/bin/env python3
"""gpu_poly: two clk2x cycles from the vertex registers to the gradient products.

gpu_poly writes its vertex registers vt(0..2) only in ISSUE_READ1-3 and
SORT_READ1-3, and loads muldivResult1clk / muldivResult2clk (the registered
products of the two mul9s gradient multipliers, fed combinationally from vt
through dmP*/dmE* and dmV*/dmD*) only in CALCCOLOR2 and CALCTEXTURE2. Every
path from a vt write to a capture runs through SORT_READ3 -> CALCBOUNDARY1 ->
CALCBOUNDARY2 -> CALCCOLOR1/CALCTEXTURE1 (ISSUE_READ3 goes on to SORTVERTICES
and the SORT_READ states), so a capture is at least four clk2x edges after the
last vt write; ce only stretches that. A setup multicycle of 2 (hold 1) from
vt to those two registers is therefore true; every other source of the
products (mulStep, state, the selects) keeps its single-cycle check.

0.5.7-rel057c sign-off (psx-fit #754): the only failing release_timing row was
gpu_poly vt[1].x[7] -> muldivResult1clk[6], -0.024 ns Slow 0C setup.
Check: scripts/run_gpu_poly_vt_multicycle.py (cycle lockstep with a sim-only
guard that corrupts a capture right after a vt write; a guard requiring four
edges must fail).

Usage: prepare_gpu_poly_vt_multicycle.py PROJECT  (post-prepare step; appends to
PROJECT/pocket.sdc; the gpu_poly.vhd the QSF compiles must have the pinned
vertex writes and captures)
"""
import re
import sys
from pathlib import Path

from run_gpu_poly_vt_multicycle import guard_text

TAG = 'gpu-poly-vt-multicycle'
SDC = f'''
# {TAG}: vt is written only in ISSUE_READ1-3 / SORT_READ1-3 and muldivResult1clk/2clk load only in
# CALCCOLOR2 / CALCTEXTURE2, at least four clk2x edges later (scripts/prepare_gpu_poly_vt_multicycle.py).
set_multicycle_path -setup 2 -from [get_registers {{*|gpu_poly:igpu_poly|vt[*]*}}] -to [get_registers {{*|gpu_poly:igpu_poly|muldivResult1clk[*] *|gpu_poly:igpu_poly|muldivResult2clk[*]}}]
set_multicycle_path -hold 1 -from [get_registers {{*|gpu_poly:igpu_poly|vt[*]*}}] -to [get_registers {{*|gpu_poly:igpu_poly|muldivResult1clk[*] *|gpu_poly:igpu_poly|muldivResult2clk[*]}}]
'''


def apply(project):
    project = Path(project).resolve()
    qsf = (project / 'apf_top.qsf').read_text()
    polys = sorted(set(re.findall(r'VHDL_FILE "([^"]*/gpu_poly\.vhd)"', qsf)))
    if len(polys) != 1: raise SystemExit(f'{TAG}: expected one compiled gpu_poly.vhd, found {len(polys)}')
    guard_text(Path(polys[0]).read_text())   # raises if the vertex writes or captures changed
    sdc = project / 'pocket.sdc'
    text = sdc.read_text()
    if TAG in text: raise SystemExit(f'{TAG}: already applied')
    if not re.search(r'^set_global_assignment -name SDC_FILE ("?)(pocket\.sdc|' + re.escape(str(sdc)) + r')\1\s*$', qsf, re.M):
        raise SystemExit(f'{TAG}: the QSF does not read {sdc}')
    sdc.write_text(text.rstrip('\n') + '\n' + SDC)
    return sdc


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print('patched', apply(sys.argv[1]))
