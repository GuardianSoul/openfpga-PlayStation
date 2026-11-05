#!/usr/bin/env python3
"""SDRAM controller: register refreshForce on clk3x before it is used.

refreshForce is psx_top's ram_refresh = reset_intern, a clk1x register
(savestates reset_out). force_rise = refreshForce && !force_d fed the DMA
write chain decision straight from it, and that decision picks a[10]
(auto-precharge), so a clk1x register reached the pin registers in one
clk3x period: rel058b sign-off, savestates|reset_out -> a[10], -0.185 ns
(Slow 85C). force_in now registers the input once on clk3x and force_d /
force_rise work from it, so the controller behaves exactly like the
original fed refreshForce one clk3x edge later. That delay is harmless:
a forced refresh is a reset-time courtesy (refreshForce rises when the core
enters reset and stays high through it), the interval timer still owes
every refresh, and one edge is far inside any refresh deadline.
Proof: scripts/run_sdram_force_reg.py runs the new controller in lockstep
with the original whose refreshForce input goes through one clk3x register.
Usage: prepare_sdram_force_reg.py PROJECT
"""
from pathlib import Path
import re
import sys

MARK = 'sdram-force-reg'
EDITS = (
    ('    wire force_rise = refreshForce && !force_d;\n',
     f'    reg        force_in = 0;   // {MARK}: refreshForce (clk1x register) registered once on clk3x\n'
     '    wire force_rise = force_in && !force_d;\n', 'force_rise'),
    ('            force_d <= refreshForce;\n',
     '            force_in <= refreshForce;\n'
     '            force_d <= force_in;\n', 'force_d'),
)


def force_reg_text(text):
    if MARK in text: raise ValueError(f'{MARK} already applied')
    for old, new, what in EDITS:
        if text.count(old) != 1: raise ValueError(f'{MARK}: {what} anchor found {text.count(old)} times')
        text = text.replace(old, new)
    code = re.sub(r'//[^\n]*', '', text)
    if len(re.findall(r'\brefreshForce\b', code)) != 2:   # the port and force_in only
        raise ValueError(f'{MARK}: refreshForce used elsewhere')
    return text


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/psx_sdram_ctrl\.sv)"', q)
    if len(set(hits)) != 1: raise ValueError(f'{MARK}: expected one psx_sdram_ctrl.sv in the project')
    out = project/MARK/'psx_sdram_ctrl.sv'; out.parent.mkdir(exist_ok=True)
    out.write_text(force_reg_text(Path(hits[0]).read_text()))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
