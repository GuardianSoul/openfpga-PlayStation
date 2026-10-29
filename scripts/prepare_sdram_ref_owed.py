#!/usr/bin/env python3
"""SDRAM controller: "refresh owed" from a register, not a 4-bit compare.

psx_sdram_ctrl's need_ref (ref_owed != 0 || force_pend) feeds open_hit1 and
the idle edge's a[] pin-register mux; the ref_owed compare was the failing
setup path of 0.5.7-test13 (ref_owed -> a[2..4], clk3x). ref_owed_nz is set
beside every ref_owed assignment to the value's (ref_owed != 0), so the
compare comes from one register and every command, address and output stays
cycle-identical (scripts/run_sdram_ref_owed.py).
Usage: prepare_sdram_ref_owed.py PROJECT
"""
from pathlib import Path
import re
import sys

MARK = 'sdram-ref-owed'
EDITS = (
    ('    reg [3:0]  ref_owed = 0;\n',
     '    reg [3:0]  ref_owed = 0;\n'
     f'    reg        ref_owed_nz = 0;   // {MARK}: ref_owed is nonzero, set with every ref_owed write\n', 'declaration'),
    ('    assign need_ref = ref_owed != 0 || force_pend', '    assign need_ref = ref_owed_nz || force_pend', 'need_ref'),
    ("                if (ref_owed != 4'd8) ref_owed <= ref_owed + 1'b1;\n",
     "                if (ref_owed != 4'd8) begin ref_owed <= ref_owed + 1'b1; ref_owed_nz <= 1'b1; end\n", 'interval'),
    ("ref_timer <= REFRESH_CYCLES[10:0]; ref_owed <= 0; end\n",
     "ref_timer <= REFRESH_CYCLES[10:0]; ref_owed <= 0; ref_owed_nz <= 1'b0; end\n", 'init'),
    ("                        else ref_owed <= ref_owed - 1'b1 + (ref_timer == 0);\n",
     "                        else begin ref_owed <= ref_owed - 1'b1 + (ref_timer == 0); ref_owed_nz <= ref_owed != 4'd1 || ref_timer == 0; end\n",
     'forced refresh'),
    ("                    end else ref_owed <= ref_owed - 1'b1 + (ref_timer == 0 && ref_owed != 4'd8);\n",
     "                    end else begin\n"
     "                        ref_owed <= ref_owed - 1'b1 + (ref_timer == 0 && ref_owed != 4'd8);\n"
     "                        ref_owed_nz <= ref_owed != 4'd1 || (ref_timer == 0 && ref_owed != 4'd8);\n"
     "                    end\n", 'owed refresh'),
)


def ref_owed_text(text):
    if MARK in text: raise ValueError(f'{MARK} already applied')
    for old, new, what in EDITS:
        if text.count(old) != 1: raise ValueError(f'{MARK}: {what} anchor found {text.count(old)} times')
        text = text.replace(old, new)
    # the other "nothing owed" tests (DMA chain, forced-refresh timer, ram_idle)
    text, n = re.subn(r'\bref_owed == 0\b', '!ref_owed_nz', text)
    if n != 3: raise ValueError(f'{MARK}: expected 3 ref_owed == 0 tests, found {n}')
    if re.search(r'ref_owed != 0\b', text): raise ValueError(f'{MARK}: a ref_owed != 0 test is left')
    return text


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/psx_sdram_ctrl\.sv)"', q)
    if len(set(hits)) != 1: raise ValueError(f'{MARK}: expected one psx_sdram_ctrl.sv in the project')
    out = project/MARK/'psx_sdram_ctrl.sv'; out.parent.mkdir(exist_ok=True)
    out.write_text(ref_owed_text(Path(hits[0]).read_text()))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
